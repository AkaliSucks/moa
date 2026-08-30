import json
import sqlite3
from datetime import datetime, timezone

import pytest

import moa.services.roll_projection_coordinator as roll_projection_coordinator_module
from moa.database.sqlite import connect
from moa.models.character import RollObservation
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import (
    DiscordMessageProcessingConflictError,
    DiscordMessageRepository,
)
from moa.services.roll_projection_coordinator import (
    RollProjectionCoordinator,
    RollProjectionIntegrityError,
)
from moa.services.projection_expectations import (
    ExpectedProjectionAssessment,
    ExpectedProjectionSet,
    Expectedness,
    build_projection_expectation_facts,
    resolve_expected_projections,
)


OBSERVED_AT = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
FINISHED_AT = datetime(2026, 7, 21, 12, 1, tzinfo=timezone.utc)
ROLL_ALL = RollObservation(
    name="  Coordinator Character ",
    series="Coordinator Series",
    claim_rank=7,
    kakera_value=12,
    displayed_key_type=" GOLD ",
    displayed_key_count=3,
)
ROLL_NONE = RollObservation(
    name="Coordinator Character",
    series="Coordinator Series",
    claim_rank=None,
    kakera_value=None,
)
ROLL_KEY_ONLY = RollObservation(
    name="Coordinator Character",
    series="Coordinator Series",
    claim_rank=None,
    kakera_value=None,
    displayed_key_type="GOLD",
    displayed_key_count=3,
)


def _repositories(tmp_path):
    database_path = tmp_path / "coordinator.db"
    catalog = CatalogRepository(database_path)
    discord = DiscordMessageRepository(database_path)
    return database_path, catalog, discord, RollProjectionCoordinator(catalog, discord)


def _receive_and_begin(discord, *, suffix="one"):
    aggregate_key = MessageAggregateKey(
        SourcePlatform.DISCORD, "guild", "channel", f"message-{suffix}"
    )
    received = discord.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(
            aggregate_key, f"payload-{suffix}", "revision-1"
        ),
        event_key=f"event-{suffix}",
        event_kind="message_create",
        raw_text="roll payload",
        payload_json='{"content":"roll payload"}',
        payload_capture_version="capture-1",
        source_observed_at=OBSERVED_AT,
        received_at=OBSERVED_AT,
    )
    attempt = discord.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OBSERVED_AT,
    )
    discord.record_server_attribution(
        received.source_event_id,
        status="resolved",
        server_name="Server",
        recorded_at=OBSERVED_AT,
    )
    discord.record_account_attribution(
        received.source_event_id,
        status="resolved",
        server_name="Server",
        account_name="Account",
        recorded_at=OBSERVED_AT,
    )
    return received.source_event_id, attempt.attempt_id


def _set_attribution_failure(database_path, failure: str) -> None:
    statements = {
        "missing_server": (
            "DELETE FROM discord_source_event_server_attributions",
            (),
        ),
        "unresolved_server": (
            "UPDATE discord_source_event_server_attributions SET status = 'unresolved', server_name = NULL",
            (),
        ),
        "ambiguous_server": (
            "UPDATE discord_source_event_server_attributions SET status = 'ambiguous', server_name = NULL",
            (),
        ),
        "server_mismatch": (
            "UPDATE discord_source_event_server_attributions SET server_name = 'Server B'",
            (),
        ),
        "missing_account": (
            "DELETE FROM discord_source_event_account_attributions",
            (),
        ),
        "unresolved_account": (
            "UPDATE discord_source_event_account_attributions SET status = 'unresolved', server_name = NULL, account_name = NULL",
            (),
        ),
        "ambiguous_account": (
            "UPDATE discord_source_event_account_attributions SET status = 'ambiguous', server_name = NULL, account_name = NULL",
            (),
        ),
        "account_server_mismatch": (
            "UPDATE discord_source_event_account_attributions SET server_name = 'Server B'",
            (),
        ),
        "account_mismatch": (
            "UPDATE discord_source_event_account_attributions SET account_name = 'Account B'",
            (),
        ),
    }
    statement, parameters = statements[failure]
    with connect(database_path) as connection:
        connection.execute(statement, parameters)


def _attribution_rows(connection: sqlite3.Connection):
    return (
        tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM discord_source_event_server_attributions ORDER BY source_event_id"
            )
        ),
        tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM discord_source_event_account_attributions ORDER BY source_event_id"
            )
        ),
    )


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "characters",
        "server_contexts",
        "account_contexts",
        "roll_observations",
        "harem_key_observations",
        "rank_snapshots",
        "server_character_observations",
        "discord_projection_links",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _event_and_attempt(connection: sqlite3.Connection):
    event = connection.execute(
        "SELECT status, legacy_import_event_id, updated_at FROM discord_source_events"
    ).fetchone()
    attempt = connection.execute(
        "SELECT status, finished_at FROM discord_processing_attempts"
    ).fetchone()
    return event, attempt


def _activate_test_projection_generation(
    connection: sqlite3.Connection, generation_id: int
) -> None:
    """Move a temporary test database to a later projection generation."""
    connection.execute("DROP TRIGGER projection_generations_keep_current_on_update")
    connection.execute(
        "INSERT INTO projection_generations (id, is_current) VALUES (?, 0)",
        (generation_id,),
    )
    connection.execute("UPDATE projection_generations SET is_current = 0 WHERE id = 1")
    connection.execute(
        "UPDATE projection_generations SET is_current = 1 WHERE id = ?",
        (generation_id,),
    )


def _insert_historical_completed_roll_link(
    connection: sqlite3.Connection, source_event_id: int
) -> None:
    value = OBSERVED_AT.isoformat()
    connection.execute(
        """
        INSERT INTO discord_projection_links (
            source_event_id, generation_id, projection_kind, projection_slot,
            projection_table, projection_row_id, state,
            claimed_at, completed_at, created_at, updated_at
        ) VALUES (?, 1, 'catalog.roll', '{"historical":true}',
                  'roll_observations', 987, 'completed', ?, ?, ?, ?)
        """,
        (source_event_id, value, value, value, value),
    )


def _coordinate_roll(coordinator, source_event_id: int, attempt_id: int | None, roll=ROLL_ALL):
    return coordinator.coordinate_roll(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        roll=roll,
        server="Server",
        account="Account",
        raw="roll payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )


def _corrupt_server_character_value(catalog, monkeypatch, value: int = 999) -> None:
    original = catalog._import_roll_with_connection

    def import_and_corrupt(connection, **kwargs):
        imported = original(connection, **kwargs)
        connection.execute(
            "UPDATE server_character_observations SET kakera_value = ? WHERE id = ?",
            (value, imported.server_character_observation_id),
        )
        return imported

    monkeypatch.setattr(catalog, "_import_roll_with_connection", import_and_corrupt)


def test_first_processing_coordinates_all_roll_projections_and_success(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    result = coordinator.coordinate_roll(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        roll=ROLL_ALL,
        server=" Server ",
        account=" Account ",
        raw="roll payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )

    assert result.imported_count == 1
    assert result.replay_skipped is False
    assert result.durable_success_recorded is True
    assert [table for table, _row_id in result.projection_targets] == [
        "roll_observations",
        "harem_key_observations",
        "rank_snapshots",
        "server_character_observations",
    ]
    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 1,
            "characters": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "roll_observations": 1,
            "harem_key_observations": 1,
            "rank_snapshots": 1,
            "server_character_observations": 1,
            "discord_projection_links": 4,
        }
        links = connection.execute(
            """
            SELECT generation_id, projection_kind, projection_table,
                   projection_row_id, state, completed_at
            FROM discord_projection_links ORDER BY id
            """
        ).fetchall()
        assert [(row[0], row[1], row[2], row[4]) for row in links] == [
            (1, "catalog.roll", "roll_observations", "completed"),
            (1, "catalog.roll_key", "harem_key_observations", "completed"),
            (1, "catalog.roll_rank", "rank_snapshots", "completed"),
            (1, "catalog.roll_server_character", "server_character_observations", "completed"),
        ]
        assert all(row[3] > 0 and row[5] == FINISHED_AT.isoformat() for row in links)
        event, attempt = _event_and_attempt(connection)
        assert event[0:2] == ("succeeded", result.import_event_id)
        assert attempt[0:2] == ("succeeded", FINISHED_AT.isoformat())


def test_historical_roll_links_do_not_satisfy_or_suppress_current_generation(
    tmp_path,
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    with connect(database_path) as connection:
        _activate_test_projection_generation(connection, 2)
        _insert_historical_completed_roll_link(connection, source_event_id)
        historical_before = tuple(
            connection.execute(
                "SELECT * FROM discord_projection_links WHERE generation_id = 1"
            ).fetchone()
        )

    first = _coordinate_roll(coordinator, source_event_id, attempt_id)
    replay = _coordinate_roll(coordinator, source_event_id, None)

    assert first.imported_count == 1
    assert replay.imported_count == 0
    assert replay.replay_skipped is True
    assert replay.projection_targets == first.projection_targets
    with connect(database_path) as connection:
        historical_after = tuple(
            connection.execute(
                "SELECT * FROM discord_projection_links WHERE generation_id = 1"
            ).fetchone()
        )
        current_links = connection.execute(
            """
            SELECT generation_id, state, projection_row_id
            FROM discord_projection_links
            WHERE source_event_id = ? AND generation_id = 2
            ORDER BY id
            """,
            (source_event_id,),
        ).fetchall()
        assert historical_after == historical_before
        assert len(current_links) == 4
        assert all(
            row[0] == 2 and row[1] == "completed" and row[2] > 0
            for row in current_links
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM import_events WHERE kind = 'roll'"
        ).fetchone()[0] == 1


def test_first_processing_key_persistence_is_governed_by_shared_authority(
    tmp_path, monkeypatch
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    original_resolver = roll_projection_coordinator_module.resolve_expected_projections

    def suppress_key_expectation(facts):
        resolved = original_resolver(facts)
        return ExpectedProjectionSet(
            tuple(
                ExpectedProjectionAssessment(
                    assessment.projection_kind,
                    Expectedness.NOT_EXPECTED,
                )
                if assessment.projection_kind == "catalog.roll_key"
                else assessment
                for assessment in resolved.assessments
            )
        )

    monkeypatch.setattr(
        roll_projection_coordinator_module,
        "resolve_expected_projections",
        suppress_key_expectation,
    )

    result = _coordinate_roll(coordinator, source_event_id, attempt_id, ROLL_ALL)

    assert "harem_key_observations" not in dict(result.projection_targets)
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM harem_key_observations"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_projection_links "
            "WHERE projection_kind = 'catalog.roll_key'"
        ).fetchone()[0] == 0


def test_coordinator_calls_supplied_roll_helper_without_public_wrapper(
    tmp_path, monkeypatch
) -> None:
    _database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    original = catalog._import_roll_with_connection
    calls = 0

    def supplied_helper(connection, **kwargs):
        nonlocal calls
        calls += 1
        assert connection.in_transaction is True
        return original(connection, **kwargs)

    def forbidden_public_wrapper(*_args, **_kwargs):
        raise AssertionError("coordinator called public roll wrapper")

    monkeypatch.setattr(catalog, "_import_roll_with_connection", supplied_helper)
    monkeypatch.setattr(catalog, "import_roll", forbidden_public_wrapper)

    result = _coordinate_roll(coordinator, source_event_id, attempt_id)

    assert result.imported_count == 1
    assert calls == 1


def test_roll_without_optional_projections_only_creates_roll_projection(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    result = coordinator.coordinate_roll(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        roll=ROLL_NONE,
        server="Server",
        account="Account",
        raw="roll payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )

    assert len(result.projection_targets) == 1
    with connect(database_path) as connection:
        counts = _counts(connection)
        assert counts["import_events"] == 1
        assert counts["roll_observations"] == 1
        assert counts["harem_key_observations"] == 0
        assert counts["rank_snapshots"] == 0
        assert counts["server_character_observations"] == 0
        assert counts["discord_projection_links"] == 1


@pytest.mark.parametrize(
    "failure, message",
    [
        ("missing_server", "no persisted server attribution"),
        ("unresolved_server", "non-resolved server attribution"),
        ("ambiguous_server", "non-resolved server attribution"),
        ("server_mismatch", "another server"),
        ("missing_account", "no persisted account attribution"),
        ("unresolved_account", "non-resolved account attribution"),
        ("ambiguous_account", "non-resolved account attribution"),
        ("account_server_mismatch", "mismatched account attribution server"),
        ("account_mismatch", "another account"),
    ],
)
def test_first_processing_attribution_failure_is_before_all_projection_writes(
    tmp_path, failure: str, message: str
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _set_attribution_failure(database_path, failure)

    with connect(database_path) as connection:
        before_counts = _counts(connection)
        before_event, before_attempt = _event_and_attempt(connection)
        before_attributions = _attribution_rows(connection)

    with pytest.raises(RollProjectionIntegrityError, match=message):
        coordinator.coordinate_roll(
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            roll=ROLL_ALL,
            server="Server",
            account="Account",
            raw="roll payload",
            source="discord",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        )

    with connect(database_path) as connection:
        assert _counts(connection) == before_counts
        assert _event_and_attempt(connection) == (before_event, before_attempt)
        assert _attribution_rows(connection) == before_attributions


@pytest.mark.parametrize(
    "failure, message",
    [
        ("missing_server", "no persisted server attribution"),
        ("unresolved_server", "non-resolved server attribution"),
        ("ambiguous_server", "non-resolved server attribution"),
        ("server_mismatch", "another server"),
        ("missing_account", "no persisted account attribution"),
        ("unresolved_account", "non-resolved account attribution"),
        ("ambiguous_account", "non-resolved account attribution"),
        ("account_server_mismatch", "mismatched account attribution server"),
        ("account_mismatch", "another account"),
    ],
)
def test_succeeded_replay_validates_attribution_before_trusting_links(
    tmp_path, failure: str, message: str
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = coordinator.coordinate_roll(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        roll=ROLL_ALL,
        server="Server",
        account="Account",
        raw="roll payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )
    _set_attribution_failure(database_path, failure)

    with connect(database_path) as connection:
        before_counts = _counts(connection)
        before_event, before_attempt = _event_and_attempt(connection)
        before_attributions = _attribution_rows(connection)

    with pytest.raises(RollProjectionIntegrityError, match=message):
        coordinator.coordinate_roll(
            source_event_id=source_event_id,
            attempt_id=None,
            roll=ROLL_ALL,
            server="Server",
            account="Account",
            raw="replayed roll payload",
            source="discord",
            observed_at=FINISHED_AT,
            finished_at=FINISHED_AT,
        )

    with connect(database_path) as connection:
        assert _counts(connection) == before_counts
        assert _event_and_attempt(connection) == (before_event, before_attempt)
        assert _attribution_rows(connection) == before_attributions
    assert first.replay_skipped is False


def test_failure_after_catalog_writes_rolls_back_every_coordinator_write(tmp_path, monkeypatch) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    def fail_after_catalog_writes(*_args, **_kwargs):
        raise RuntimeError("forced failure after catalog writes")

    monkeypatch.setattr(coordinator, "_complete_projection_links", fail_after_catalog_writes)
    with pytest.raises(RuntimeError, match="forced failure after catalog writes"):
        coordinator.coordinate_roll(
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            roll=ROLL_ALL,
            server="Server",
            account="Account",
            raw="roll payload",
            source="discord",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        )

    with connect(database_path) as connection:
        counts = _counts(connection)
        assert counts == {
            "import_events": 0,
            "characters": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "roll_observations": 0,
            "harem_key_observations": 0,
            "rank_snapshots": 0,
            "server_character_observations": 0,
            "discord_projection_links": 0,
        }
        event, attempt = _event_and_attempt(connection)
        assert event[0:2] == ("processing", None)
        assert attempt[0] == "processing"


def test_first_processing_rejects_target_from_another_import_and_rolls_back(
    tmp_path, monkeypatch
) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    seeded = catalog.import_roll(
        ROLL_NONE,
        server_name="Server",
        account_name="Account",
        raw_message="seed",
        source="test",
    )
    with connect(database_path) as connection:
        seeded_roll_id = int(
            connection.execute(
                "SELECT id FROM roll_observations WHERE import_event_id = ?",
                (seeded.import_event_id,),
            ).fetchone()[0]
        )
    source_event_id, attempt_id = _receive_and_begin(discord)
    before_counts = None
    with connect(database_path) as connection:
        before_counts = _counts(connection)

    original = coordinator._targets_from_import

    def return_foreign_target(expected, imported):
        targets = original(expected, imported)
        return ((targets[0][0], seeded_roll_id), *targets[1:])

    monkeypatch.setattr(coordinator, "_targets_from_import", return_foreign_target)
    with pytest.raises(RollProjectionIntegrityError, match="another import event"):
        _coordinate_roll(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        assert _counts(connection) == before_counts
        assert _event_and_attempt(connection)[0][0:2] == ("processing", None)
        assert _event_and_attempt(connection)[1][0] == "processing"


def test_first_processing_validates_optional_target_before_completion(
    tmp_path, monkeypatch
) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _corrupt_server_character_value(catalog, monkeypatch)

    with pytest.raises(RollProjectionIntegrityError, match="mismatched Kakera value"):
        _coordinate_roll(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        counts = _counts(connection)
        assert counts["import_events"] == 0
        assert counts["characters"] == 0
        assert counts["roll_observations"] == 0
        assert counts["harem_key_observations"] == 0
        assert counts["rank_snapshots"] == 0
        assert counts["server_character_observations"] == 0
        assert counts["discord_projection_links"] == 0
        assert _event_and_attempt(connection)[0][0:2] == ("processing", None)
        assert _event_and_attempt(connection)[1][0] == "processing"


def test_current_generation_roll_target_validation_failure_rolls_back(
    tmp_path, monkeypatch
) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    with connect(database_path) as connection:
        _activate_test_projection_generation(connection, 2)
        _insert_historical_completed_roll_link(connection, source_event_id)
        historical_before = tuple(
            connection.execute(
                "SELECT * FROM discord_projection_links WHERE generation_id = 1"
            ).fetchone()
        )
    _corrupt_server_character_value(catalog, monkeypatch)

    with pytest.raises(RollProjectionIntegrityError, match="mismatched Kakera value"):
        _coordinate_roll(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        assert tuple(
            connection.execute(
                "SELECT * FROM discord_projection_links WHERE generation_id = 1"
            ).fetchone()
        ) == historical_before
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_projection_links WHERE generation_id = 2"
        ).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM roll_observations").fetchone()[0] == 0
        assert _event_and_attempt(connection)[0][0:2] == ("processing", None)
        assert _event_and_attempt(connection)[1][0] == "processing"


def test_retry_after_rolled_back_failure_succeeds_without_duplicates(tmp_path, monkeypatch) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    monkeypatch.setattr(
        coordinator,
        "_complete_projection_links",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("rollback")),
    )
    with pytest.raises(RuntimeError, match="rollback"):
        coordinator.coordinate_roll(
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            roll=ROLL_ALL,
            server="Server",
            account="Account",
            raw="roll payload",
            source="discord",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        )
    discord.mark_processing_failure(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        status="failed",
        retryable=True,
        failure_code="test",
        failure_detail="retry",
        finished_at=FINISHED_AT,
    )
    retry = discord.begin_processing_attempt(
        source_event_id=source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=FINISHED_AT,
    )
    monkeypatch.undo()

    result = coordinator.coordinate_roll(
        source_event_id=source_event_id,
        attempt_id=retry.attempt_id,
        roll=ROLL_ALL,
        server="Server",
        account="Account",
        raw="roll payload",
        source="discord",
        observed_at=FINISHED_AT,
        finished_at=datetime(2026, 7, 21, 12, 2, tzinfo=timezone.utc),
    )

    assert result.imported_count == 1
    with connect(database_path) as connection:
        counts = _counts(connection)
        assert counts["import_events"] == 1
        assert counts["roll_observations"] == 1
        assert counts["discord_projection_links"] == 4


def test_successful_replay_validates_links_and_inserts_nothing(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = coordinator.coordinate_roll(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        roll=ROLL_ALL,
        server="Server",
        account="Account",
        raw="roll payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )
    with connect(database_path) as connection:
        before_counts = _counts(connection)
        before_event, before_attempt = _event_and_attempt(connection)

    with pytest.raises(DiscordMessageProcessingConflictError, match="already succeeded"):
        coordinator.coordinate_roll(
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            roll=ROLL_ALL,
            server="Server",
            account="Account",
            raw="replay with stale attempt",
            source="discord",
            observed_at=FINISHED_AT,
            finished_at=FINISHED_AT,
        )

    replay = coordinator.coordinate_roll(
        source_event_id=source_event_id,
        attempt_id=None,
        roll=ROLL_ALL,
        server=" Server ",
        account=" Account ",
        raw="replayed raw payload",
        source="discord",
        observed_at=FINISHED_AT,
        finished_at=datetime(2026, 7, 21, 12, 2, tzinfo=timezone.utc),
    )

    assert replay.imported_count == 0
    assert replay.replay_skipped is True
    assert replay.durable_success_recorded is True
    assert replay.import_event_id == first.import_event_id
    assert replay.projection_targets == first.projection_targets
    with connect(database_path) as connection:
        assert _counts(connection) == before_counts
        assert _event_and_attempt(connection) == (before_event, before_attempt)


def test_replay_preserves_durable_unknown_key_expectedness(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate_roll(coordinator, source_event_id, attempt_id, ROLL_NONE)
    with connect(database_path) as connection:
        before_counts = _counts(connection)

    replay = _coordinate_roll(coordinator, source_event_id, None, ROLL_KEY_ONLY)

    assert replay.replay_skipped is True
    assert replay.projection_targets == first.projection_targets
    with connect(database_path) as connection:
        assert _counts(connection) == before_counts


def test_replay_uses_exact_durable_expected_key_identity(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate_roll(coordinator, source_event_id, attempt_id, ROLL_ALL)
    parsed_with_different_key_type = RollObservation(
        name=ROLL_ALL.name,
        series=ROLL_ALL.series,
        claim_rank=ROLL_ALL.claim_rank,
        kakera_value=ROLL_ALL.kakera_value,
        displayed_key_type="SILVER",
        displayed_key_count=ROLL_ALL.displayed_key_count,
    )

    replay = _coordinate_roll(
        coordinator,
        source_event_id,
        None,
        parsed_with_different_key_type,
    )

    assert replay.replay_skipped is True
    assert replay.projection_targets == first.projection_targets
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 1


def test_succeeded_replay_rejects_non_roll_import_kind_without_mutation(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate_roll(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE import_events SET kind = 'claim' WHERE id = ?",
            (first.import_event_id,),
        )
        before_counts = _counts(connection)
        before_event, before_attempt = _event_and_attempt(connection)
        before_links = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT projection_kind, projection_table, projection_row_id, state "
                "FROM discord_projection_links ORDER BY id"
            )
        )

    with pytest.raises(RollProjectionIntegrityError, match="not a roll import"):
        _coordinate_roll(coordinator, source_event_id, None)

    with connect(database_path) as connection:
        assert _counts(connection) == before_counts
        assert _event_and_attempt(connection) == (before_event, before_attempt)
        assert tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT projection_kind, projection_table, projection_row_id, state "
                "FROM discord_projection_links ORDER BY id"
            )
        ) == before_links


@pytest.mark.parametrize(
    "table, column, value, message",
    [
        ("roll_observations", "claim_rank", 99, "mismatched claim rank"),
        ("roll_observations", "kakera_value", 99, "mismatched Kakera value"),
    ],
)
def test_succeeded_replay_rejects_roll_scalar_mismatch(
    tmp_path, table: str, column: str, value: int, message: str
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate_roll(coordinator, source_event_id, attempt_id)
    target_id = dict(first.projection_targets)[table]
    with connect(database_path) as connection:
        connection.execute(
            f"UPDATE {table} SET {column} = ? WHERE id = ?", (value, target_id)
        )
        before_counts = _counts(connection)
        before_event, before_attempt = _event_and_attempt(connection)

    with pytest.raises(RollProjectionIntegrityError, match=message):
        _coordinate_roll(coordinator, source_event_id, None)

    with connect(database_path) as connection:
        assert _counts(connection) == before_counts
        assert _event_and_attempt(connection) == (before_event, before_attempt)


@pytest.mark.parametrize(
    "column, value, message",
    [
        ("key_count", 99, "mismatched key count"),
        ("kakera_value", 99, "mismatched Kakera value"),
    ],
)
def test_succeeded_replay_rejects_key_scalar_mismatch(
    tmp_path, column: str, value: int, message: str
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate_roll(coordinator, source_event_id, attempt_id)
    target_id = dict(first.projection_targets)["harem_key_observations"]
    with connect(database_path) as connection:
        connection.execute(
            f"UPDATE harem_key_observations SET {column} = ? WHERE id = ?",
            (value, target_id),
        )

    with pytest.raises(RollProjectionIntegrityError, match=message):
        _coordinate_roll(coordinator, source_event_id, None)


def test_succeeded_replay_rejects_rank_scalar_mismatch(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate_roll(coordinator, source_event_id, attempt_id)
    target_id = dict(first.projection_targets)["rank_snapshots"]
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE rank_snapshots SET claim_rank = ? WHERE id = ?",
            (99, target_id),
        )

    with pytest.raises(RollProjectionIntegrityError, match="mismatched claim rank"):
        _coordinate_roll(coordinator, source_event_id, None)


def test_succeeded_replay_rejects_server_character_scalar_mismatch(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate_roll(coordinator, source_event_id, attempt_id)
    target_id = dict(first.projection_targets)["server_character_observations"]
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE server_character_observations SET kakera_value = ? WHERE id = ?",
            (99, target_id),
        )

    with pytest.raises(RollProjectionIntegrityError, match="mismatched Kakera value"):
        _coordinate_roll(coordinator, source_event_id, None)


def test_preexisting_character_update_rolls_back_after_target_integrity_failure(
    tmp_path, monkeypatch
) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    seed = RollObservation(
        name="Coordinator Character",
        series="Coordinator Series",
        claim_rank=None,
        kakera_value=None,
    )
    seeded = catalog.import_roll(
        seed,
        server_name="Server",
        account_name="Account",
        raw_message="seed",
        source="test",
    )
    with connect(database_path) as connection:
        before_character = tuple(
            connection.execute(
                "SELECT name, series, normalized_name, normalized_series, updated_at "
                "FROM characters WHERE normalized_name = 'coordinator character'"
            ).fetchone()
        )
        assert seeded.import_event_id > 0
    source_event_id, attempt_id = _receive_and_begin(discord)
    _corrupt_server_character_value(catalog, monkeypatch)

    with pytest.raises(RollProjectionIntegrityError, match="mismatched Kakera value"):
        _coordinate_roll(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        assert tuple(
            connection.execute(
                "SELECT name, series, normalized_name, normalized_series, updated_at "
                "FROM characters WHERE normalized_name = 'coordinator character'"
            ).fetchone()
        ) == before_character
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM roll_observations").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_projection_links WHERE source_event_id = ?",
            (source_event_id,),
        ).fetchone()[0] == 0
        assert _event_and_attempt(connection)[0][0:2] == ("processing", None)
        assert _event_and_attempt(connection)[1][0] == "processing"


def test_projection_slots_are_compact_deterministic_normalized_json(tmp_path) -> None:
    _database_path, _catalog, _discord, coordinator = _repositories(tmp_path)

    identities = resolve_expected_projections(
        build_projection_expectation_facts(
            "roll",
            server="  SERVER  ",
            account=" ACCOUNT ",
            character=ROLL_ALL.name,
            series=ROLL_ALL.series,
            roll_key_present=True,
            roll_key_type=ROLL_ALL.displayed_key_type,
            roll_rank_present=True,
            roll_kakera_value_present=True,
        )
    ).known_expected_identities

    assert json.loads(identities[0].projection_slot) == {
        "account": "account",
        "character": "coordinator character",
        "series": "coordinator series",
        "server": "server",
    }
    assert identities[0].projection_slot == (
        '{"account":"account","character":"coordinator character",'
        '"series":"coordinator series","server":"server"}'
    )
    assert json.loads(identities[1].projection_slot)["key_type"] == "gold"


def test_edited_revision_has_an_independent_projection_set(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    first_event, first_attempt = _receive_and_begin(discord, suffix="first")
    second_event, second_attempt = _receive_and_begin(discord, suffix="edited")
    coordinator.coordinate_roll(
        source_event_id=first_event,
        attempt_id=first_attempt,
        roll=ROLL_NONE,
        server="Server",
        account="Account",
        raw="first roll",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )
    coordinator.coordinate_roll(
        source_event_id=second_event,
        attempt_id=second_attempt,
        roll=ROLL_ALL,
        server="Server",
        account="Account",
        raw="edited roll",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM roll_observations").fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_projection_links"
        ).fetchone()[0] == 5
        assert {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT source_event_id, COUNT(*) FROM discord_projection_links GROUP BY source_event_id"
            )
        } == {first_event: 1, second_event: 4}


def test_completed_link_with_missing_target_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    coordinator.coordinate_roll(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        roll=ROLL_NONE,
        server="Server",
        account="Account",
        raw="roll payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )
    with connect(database_path) as connection:
        connection.execute("DELETE FROM roll_observations")

    with pytest.raises(RollProjectionIntegrityError, match="target .* is missing"):
        coordinator.coordinate_roll(
            source_event_id=source_event_id,
            attempt_id=None,
            roll=ROLL_NONE,
            server="Server",
            account="Account",
            raw="replay",
            source="discord",
            observed_at=FINISHED_AT,
            finished_at=FINISHED_AT,
        )
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 1
        assert _counts(connection)["discord_projection_links"] == 1


def test_completed_link_with_wrong_target_table_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    coordinator.coordinate_roll(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        roll=ROLL_NONE,
        server="Server",
        account="Account",
        raw="roll payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE discord_projection_links SET projection_table = 'characters'"
        )

    with pytest.raises(RollProjectionIntegrityError, match="disallowed table"):
        coordinator.coordinate_roll(
            source_event_id=source_event_id,
            attempt_id=None,
            roll=ROLL_NONE,
            server="Server",
            account="Account",
            raw="replay",
            source="discord",
            observed_at=FINISHED_AT,
            finished_at=FINISHED_AT,
        )


def test_unexpected_claimed_link_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    identity = resolve_expected_projections(
        build_projection_expectation_facts(
            "roll",
            server="Server",
            account="Account",
            character=ROLL_NONE.name,
            series=ROLL_NONE.series,
        )
    ).known_expected_identities[0]
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO discord_projection_links (
                source_event_id, projection_kind, projection_slot, state,
                claimed_at, created_at, updated_at
            ) VALUES (?, ?, ?, 'claimed', ?, ?, ?)
            """,
            (
                source_event_id,
                identity.projection_kind,
                identity.projection_slot,
                OBSERVED_AT.isoformat(),
                OBSERVED_AT.isoformat(),
                OBSERVED_AT.isoformat(),
            ),
        )

    with pytest.raises(RollProjectionIntegrityError, match="still claimed"):
        coordinator.coordinate_roll(
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            roll=ROLL_NONE,
            server="Server",
            account="Account",
            raw="roll payload",
            source="discord",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        )
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 0
        assert connection.execute(
            "SELECT status FROM discord_source_events"
        ).fetchone()[0] == "processing"


def test_completed_replay_optional_projection_mismatch_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    coordinator.coordinate_roll(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        roll=ROLL_ALL,
        server="Server",
        account="Account",
        raw="roll payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )

    with pytest.raises(RollProjectionIntegrityError, match="mismatched claim rank"):
        coordinator.coordinate_roll(
            source_event_id=source_event_id,
            attempt_id=None,
            roll=ROLL_NONE,
            server="Server",
            account="Account",
            raw="replay with changed optional fields",
            source="discord",
            observed_at=FINISHED_AT,
            finished_at=FINISHED_AT,
        )
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 1


def test_attempt_ownership_and_state_are_validated(tmp_path) -> None:
    _database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord, suffix="one")
    other_event_id, other_attempt_id = _receive_and_begin(discord, suffix="two")

    with pytest.raises(DiscordMessageProcessingConflictError, match="another source event"):
        coordinator.coordinate_roll(
            source_event_id=source_event_id,
            attempt_id=other_attempt_id,
            roll=ROLL_NONE,
            server="Server",
            account="Account",
            raw="roll payload",
            source="discord",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        )

    discord.mark_processing_failure(
        source_event_id=other_event_id,
        attempt_id=other_attempt_id,
        status="failed",
        retryable=False,
        failure_code="test",
        failure_detail="done",
        finished_at=FINISHED_AT,
    )
    with pytest.raises(DiscordMessageProcessingConflictError, match="not processing"):
        coordinator.coordinate_roll(
            source_event_id=other_event_id,
            attempt_id=other_attempt_id,
            roll=ROLL_NONE,
            server="Server",
            account="Account",
            raw="roll payload",
            source="discord",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        )
    with pytest.raises(DiscordMessageProcessingConflictError, match="active processing attempt"):
        coordinator.coordinate_roll(
            source_event_id=source_event_id,
            attempt_id=None,
            roll=ROLL_NONE,
            server="Server",
            account="Account",
            raw="roll payload",
            source="discord",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        )
    assert attempt_id > 0


def test_mismatched_repository_paths_are_rejected_before_coordination(tmp_path) -> None:
    catalog = CatalogRepository(tmp_path / "catalog.db")
    discord = DiscordMessageRepository(tmp_path / "discord.db")

    with pytest.raises(ValueError, match="same database path"):
        RollProjectionCoordinator(catalog, discord)
