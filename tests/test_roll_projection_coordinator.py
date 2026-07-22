import json
import sqlite3
from datetime import datetime, timezone

import pytest

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
    return received.source_event_id, attempt.attempt_id


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
            SELECT projection_kind, projection_table, projection_row_id, state, completed_at
            FROM discord_projection_links ORDER BY id
            """
        ).fetchall()
        assert [(row[0], row[1], row[3]) for row in links] == [
            ("catalog.roll", "roll_observations", "completed"),
            ("catalog.roll_key", "harem_key_observations", "completed"),
            ("catalog.roll_rank", "rank_snapshots", "completed"),
            ("catalog.roll_server_character", "server_character_observations", "completed"),
        ]
        assert all(row[2] > 0 and row[4] == FINISHED_AT.isoformat() for row in links)
        event, attempt = _event_and_attempt(connection)
        assert event[0:2] == ("succeeded", result.import_event_id)
        assert attempt[0:2] == ("succeeded", FINISHED_AT.isoformat())


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


def test_projection_slots_are_compact_deterministic_normalized_json(tmp_path) -> None:
    _database_path, _catalog, _discord, coordinator = _repositories(tmp_path)

    specs = coordinator._expected_projections(ROLL_ALL, "  SERVER  ", " ACCOUNT ")

    assert json.loads(specs[0].slot) == {
        "account": "account",
        "character": "coordinator character",
        "series": "coordinator series",
        "server": "server",
    }
    assert specs[0].slot == (
        '{"account":"account","character":"coordinator character",'
        '"series":"coordinator series","server":"server"}'
    )
    assert json.loads(specs[1].slot)["key_type"] == "gold"


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
    spec = coordinator._expected_projections(ROLL_NONE, "Server", "Account")[0]
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
                spec.kind,
                spec.slot,
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

    with pytest.raises(RollProjectionIntegrityError, match="unexpected projection links"):
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
