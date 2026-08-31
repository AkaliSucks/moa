import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from moa.database.sqlite import connect
from moa.models.character import KakeralootSettingsSnapshot
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.repositories.projection_link_repository import ProjectionLinkIntegrityError
from moa.services.infokl_projection_coordinator import (
    InfoklProjectionCoordinator,
    InfoklProjectionDatabasePathError,
    InfoklProjectionIntegrityError,
    InfoklProjectionResult,
    InfoklProjectionStateError,
    InfoklProjectionTargetError,
)
from moa.services.projection_expectations import server_projection_slot
from moa.services.projection_expectations import DurableProjectionExpectationFactsError


OBSERVED_AT = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
FINISHED_AT = datetime(2026, 7, 22, 12, 1, tzinfo=timezone.utc)
SETTINGS = KakeralootSettingsSnapshot(
    loot_cost=500,
    quantity_quality_base_cost=2000,
    quantity_quality_level_increment=200,
)


def _repositories(tmp_path):
    database_path = tmp_path / "infokl-coordinator.db"
    catalog = CatalogRepository(database_path)
    discord = DiscordMessageRepository(database_path)
    return database_path, catalog, discord, InfoklProjectionCoordinator(catalog, discord)


def _receive_and_begin(discord, *, suffix="one", message_id="message"):
    aggregate_key = MessageAggregateKey(SourcePlatform.DISCORD, "guild", "channel", message_id)
    received = discord.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(
            aggregate_key, f"payload-{suffix}", f"revision-{suffix}"
        ),
        event_key=f"event-{suffix}",
        event_kind="message_create" if suffix == "one" else "message_update",
        raw_text="infokl payload",
        payload_json='{"content":"infokl payload"}',
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


def _record_attribution(discord, source_event_id, *, status="resolved", server_name="Server A"):
    return discord.record_server_attribution(
        source_event_id,
        status=status,
        server_name=server_name if status == "resolved" else None,
        recorded_at=OBSERVED_AT,
    )


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


def _insert_historical_completed_link(
    connection: sqlite3.Connection, source_event_id: int
) -> None:
    value = OBSERVED_AT.isoformat()
    connection.execute(
        """
        INSERT INTO discord_projection_links (
            source_event_id, generation_id, projection_kind, projection_slot,
            projection_table, projection_row_id, state,
            claimed_at, completed_at, created_at, updated_at
        ) VALUES (?, 1, 'catalog.kakeraloot_settings', ?,
                  'kakeraloot_settings_observations', 987, 'completed', ?, ?, ?, ?)
        """,
        (
            source_event_id,
            server_projection_slot(CatalogRepository._normalize("Server A")),
            value,
            value,
            value,
            value,
        ),
    )


def _coordinate(coordinator, source_event_id, attempt_id, *, server="Server A"):
    return coordinator.coordinate_infokl(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        settings=SETTINGS,
        server=server,
        raw="infokl payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "kakeraloot_settings_observations",
        "discord_projection_links",
        "discord_source_event_server_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _snapshot(database_path) -> dict[str, tuple[tuple[object, ...], ...]]:
    tables = (
        "import_events",
        "server_contexts",
        "kakeraloot_settings_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_processing_attempts",
        "discord_source_event_server_attributions",
    )
    with connect(database_path) as connection:
        return {
            table: tuple(
                tuple(row)
                for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            )
            for table in tables
        }


def test_generation_one_processing_coordinates_infokl_and_success(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    attribution = _record_attribution(discord, source_event_id)

    result = _coordinate(coordinator, source_event_id, attempt_id)

    assert result == InfoklProjectionResult(
        imported_count=1,
        import_event_id=result.import_event_id,
        kakeraloot_settings_observation_id=result.kakeraloot_settings_observation_id,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=(
            "kakeraloot_settings_observations",
            result.kakeraloot_settings_observation_id,
        ),
    )
    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "kakeraloot_settings_observations": 1,
            "discord_projection_links": 1,
            "discord_source_event_server_attributions": 1,
            "discord_processing_attempts": 1,
        }
        link = connection.execute(
            "SELECT generation_id, projection_kind, projection_slot, projection_table, "
            "projection_row_id, state "
            "FROM discord_projection_links"
        ).fetchone()
        assert tuple(link) == (
            1,
            "catalog.kakeraloot_settings",
            '{"server":"server a"}',
            "kakeraloot_settings_observations",
            result.kakeraloot_settings_observation_id,
            "completed",
        )
        observation = connection.execute(
            """
            SELECT loot_cost, quantity_quality_base_cost,
                   quantity_quality_level_increment, import_event_id
            FROM kakeraloot_settings_observations
            """
        ).fetchone()
        assert tuple(observation) == (
            500,
            2000,
            200,
            result.import_event_id,
        )
        event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events"
        ).fetchone()
        attempt = connection.execute("SELECT status FROM discord_processing_attempts").fetchone()
        assert tuple(event) == ("succeeded", result.import_event_id)
        assert tuple(attempt) == ("succeeded",)
    assert discord.get_server_attribution(source_event_id) == attribution


def test_generation_two_ignores_generation_one_history_and_replay_is_no_write(
    tmp_path,
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    with connect(database_path) as connection:
        _activate_test_projection_generation(connection, 2)
        _insert_historical_completed_link(connection, source_event_id)
        historical_before = tuple(
            connection.execute(
                "SELECT * FROM discord_projection_links WHERE generation_id = 1"
            ).fetchone()
        )

    first = _coordinate(coordinator, source_event_id, attempt_id)
    before_replay = _snapshot(database_path)
    replay = _coordinate(coordinator, source_event_id, None)

    assert replay.replay_skipped is True
    assert replay.projection_target == first.projection_target
    assert _snapshot(database_path) == before_replay
    with connect(database_path) as connection:
        assert tuple(
            connection.execute(
                "SELECT * FROM discord_projection_links WHERE generation_id = 1"
            ).fetchone()
        ) == historical_before
        assert [
            tuple(row)
            for row in connection.execute(
                """
                SELECT generation_id, projection_table, projection_row_id, state
                FROM discord_projection_links
                WHERE source_event_id = ? AND generation_id = 2
                """,
                (source_event_id,),
            ).fetchall()
        ] == [
            (
                2,
                "kakeraloot_settings_observations",
                first.kakeraloot_settings_observation_id,
                "completed",
            )
        ]


def test_generation_two_replay_fails_when_only_historical_link_exists(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        _activate_test_projection_generation(connection, 2)
    before = _snapshot(database_path)

    with pytest.raises(
        InfoklProjectionIntegrityError,
        match="inconsistent Infokl projection link",
    ):
        _coordinate(coordinator, source_event_id, None)

    assert _snapshot(database_path) == before


def test_generation_two_target_validation_failure_preserves_history_and_processing(
    tmp_path, monkeypatch
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    with connect(database_path) as connection:
        _activate_test_projection_generation(connection, 2)
        _insert_historical_completed_link(connection, source_event_id)
        historical_before = tuple(
            connection.execute(
                "SELECT * FROM discord_projection_links WHERE generation_id = 1"
            ).fetchone()
        )
    monkeypatch.setattr(
        coordinator,
        "_validate_infokl_target",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            InfoklProjectionTargetError("forced target-validation failure")
        ),
    )

    with pytest.raises(InfoklProjectionTargetError, match="target-validation"):
        _coordinate(coordinator, source_event_id, attempt_id)

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
        assert connection.execute(
            "SELECT COUNT(*) FROM kakeraloot_settings_observations"
        ).fetchone()[0] == 0
        assert tuple(
            connection.execute(
                "SELECT status, legacy_import_event_id FROM discord_source_events"
            ).fetchone()
        ) == ("processing", None)
        assert connection.execute(
            "SELECT status FROM discord_processing_attempts"
        ).fetchone()[0] == "processing"


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("missing_target", "missing"),
        ("wrong_import_event", "another import event"),
        ("wrong_import_kind", "missing or wrong"),
        ("wrong_server_scope", "mismatched server scope"),
    ),
)
def test_generation_two_replay_validates_durable_target_ownership(
    tmp_path, mutation: str, message: str
) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    with connect(database_path) as connection:
        _activate_test_projection_generation(connection, 2)
    first = _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        if mutation == "missing_target":
            connection.execute(
                "DELETE FROM kakeraloot_settings_observations WHERE id = ?",
                (first.kakeraloot_settings_observation_id,),
            )
        elif mutation == "wrong_import_event":
            second = catalog.import_kakeraloot_settings(
                SETTINGS, "Server A", "second payload", "discord"
            )
            second_observation_id = connection.execute(
                "SELECT id FROM kakeraloot_settings_observations WHERE import_event_id = ?",
                (second.import_event_id,),
            ).fetchone()[0]
            connection.execute(
                """
                UPDATE discord_projection_links
                SET projection_row_id = ?
                WHERE source_event_id = ? AND generation_id = 2
                """,
                (second_observation_id, source_event_id),
            )
        elif mutation == "wrong_import_kind":
            connection.execute(
                "UPDATE import_events SET kind = 'profile' WHERE id = ?",
                (first.import_event_id,),
            )
        elif mutation == "wrong_server_scope":
            connection.execute(
                "UPDATE server_contexts SET normalized_name = 'other server'"
            )

    with pytest.raises(InfoklProjectionTargetError, match=message):
        _coordinate(coordinator, source_event_id, None)


@pytest.mark.parametrize("mutation", ("malformed", "conflicting", "unexpected"))
def test_first_processing_rejects_malformed_conflicting_or_unexpected_links(
    tmp_path, mutation: str
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    value = OBSERVED_AT.isoformat()
    with connect(database_path) as connection:
        if mutation == "malformed":
            projection_kind = coordinator._PROJECTION_KIND
            projection_slot = "not-json"
            state = "claimed"
        elif mutation == "conflicting":
            projection_kind = coordinator._PROJECTION_KIND
            projection_slot = server_projection_slot("server a")
            state = "completed"
        else:
            projection_kind = "catalog.other"
            projection_slot = "{}"
            state = "claimed"
        connection.execute(
            """
            INSERT INTO discord_projection_links (
                source_event_id, generation_id, projection_kind, projection_slot,
                projection_table, projection_row_id, state,
                claimed_at, completed_at, created_at, updated_at
            ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_event_id,
                projection_kind,
                projection_slot,
                "kakeraloot_settings_observations" if state == "completed" else None,
                999 if state == "completed" else None,
                state,
                value,
                value if state == "completed" else None,
                value,
                value,
            ),
        )

    with pytest.raises(InfoklProjectionIntegrityError):
        _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 0
        assert _counts(connection)["kakeraloot_settings_observations"] == 0


@pytest.mark.parametrize("fallback", ("missing_facts", "other_source_family"))
def test_succeeded_replay_durable_facts_fallbacks_are_no_write(
    tmp_path, monkeypatch: pytest.MonkeyPatch, fallback: str
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    with connect(database_path) as connection:
        _activate_test_projection_generation(connection, 2)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    before = _snapshot(database_path)

    if fallback == "missing_facts":
        monkeypatch.setattr(
            "moa.services.infokl_projection_coordinator.load_durable_projection_expectation_facts",
            lambda *_args: (_ for _ in ()).throw(
                DurableProjectionExpectationFactsError("test missing facts")
            ),
        )
    else:
        monkeypatch.setattr(
            "moa.services.infokl_projection_coordinator.load_durable_projection_expectation_facts",
            lambda *_args: SimpleNamespace(source_family="profile"),
        )

    replay = _coordinate(coordinator, source_event_id, None)

    assert replay.replay_skipped is True
    assert replay.projection_target == first.projection_target
    assert _snapshot(database_path) == before


def test_projection_link_integrity_failure_is_propagated_fail_closed(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    failure = ProjectionLinkIntegrityError("forced generation integrity failure")
    monkeypatch.setattr(
        "moa.repositories.projection_link_repository.ProjectionLinkRepository.resolve_current_generation_id",
        lambda *_args: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(ProjectionLinkIntegrityError, match="forced generation integrity failure"):
        _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 0
        assert connection.execute(
            "SELECT status FROM discord_source_events"
        ).fetchone()[0] == "processing"


def test_coordinator_uses_supplied_helper_without_public_wrapper_nesting(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    monkeypatch.setattr(
        catalog,
        "import_kakeraloot_settings",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("coordinator called public Kakeraloot settings wrapper")
        ),
    )

    result = _coordinate(coordinator, source_event_id, attempt_id)

    assert result.imported_count == 1
    assert result.replay_skipped is False
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM kakeraloot_settings_observations"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT state FROM discord_projection_links WHERE source_event_id = ?",
            (source_event_id,),
        ).fetchone()[0] == "completed"


def test_projection_slot_is_deterministic_and_normalized(tmp_path) -> None:
    _database_path, _catalog, _discord, coordinator = _repositories(tmp_path)

    assert server_projection_slot(CatalogRepository._normalize("  Server   A ")) == '{"server":"server a"}'
    assert server_projection_slot(CatalogRepository._normalize("Server A")) == (
        server_projection_slot(CatalogRepository._normalize(" server a "))
    )


@pytest.mark.parametrize(
    ("status", "server_name"),
    [(None, None), ("unresolved", None), ("ambiguous", None), ("resolved", "Other Server")],
)
def test_invalid_or_missing_attribution_fails_before_writes(
    tmp_path, status: str | None, server_name: str | None
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    if status is not None:
        _record_attribution(discord, source_event_id, status=status, server_name=server_name)

    with pytest.raises(InfoklProjectionStateError):
        _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "kakeraloot_settings_observations": 0,
            "discord_projection_links": 0,
            "discord_source_event_server_attributions": 0 if status is None else 1,
            "discord_processing_attempts": 1,
        }
        assert connection.execute("SELECT status FROM discord_source_events").fetchone()[0] == "processing"
        assert connection.execute("SELECT status FROM discord_processing_attempts").fetchone()[0] == "processing"


def test_failure_after_catalog_writes_rolls_back_and_retry_succeeds(tmp_path, monkeypatch) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    attribution = _record_attribution(discord, source_event_id)

    monkeypatch.setattr(
        coordinator,
        "_complete_projection_link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("forced retry failure")),
    )
    with pytest.raises(RuntimeError, match="forced retry failure"):
        _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "kakeraloot_settings_observations": 0,
            "discord_projection_links": 0,
            "discord_source_event_server_attributions": 1,
            "discord_processing_attempts": 1,
        }
        assert connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events"
        ).fetchone()[:2] == ("processing", None)
        assert connection.execute("SELECT status FROM discord_processing_attempts").fetchone()[0] == "processing"
    assert discord.get_server_attribution(source_event_id) == attribution

    monkeypatch.undo()
    result = _coordinate(coordinator, source_event_id, attempt_id)
    assert result.imported_count == 1
    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "kakeraloot_settings_observations": 1,
            "discord_projection_links": 1,
            "discord_source_event_server_attributions": 1,
            "discord_processing_attempts": 1,
        }


def test_first_processing_settings_mismatch_rolls_back(tmp_path, monkeypatch) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    original = catalog._import_kakeraloot_settings_with_connection

    def import_mismatched(connection, **kwargs):
        imported = original(connection, **kwargs)
        connection.execute(
            "UPDATE kakeraloot_settings_observations SET loot_cost = ? WHERE id = ?",
            (SETTINGS.loot_cost + 1, imported.kakeraloot_settings_observation_id),
        )
        return imported

    monkeypatch.setattr(catalog, "_import_kakeraloot_settings_with_connection", import_mismatched)

    with pytest.raises(InfoklProjectionTargetError, match="mismatched loot_cost"):
        _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "kakeraloot_settings_observations": 0,
            "discord_projection_links": 0,
            "discord_source_event_server_attributions": 1,
            "discord_processing_attempts": 1,
        }
        assert tuple(
            connection.execute(
                "SELECT status, legacy_import_event_id FROM discord_source_events"
            ).fetchone()
        ) == ("processing", None)
        assert connection.execute("SELECT status FROM discord_processing_attempts").fetchone()[0] == "processing"


def test_succeeded_replay_returns_existing_ids_and_inserts_nothing(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    attribution = _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        before = _counts(connection)

    replay = _coordinate(coordinator, source_event_id, None)

    assert replay == InfoklProjectionResult(
        imported_count=0,
        import_event_id=first.import_event_id,
        kakeraloot_settings_observation_id=first.kakeraloot_settings_observation_id,
        replay_skipped=True,
        durable_success_recorded=True,
        projection_target=first.projection_target,
    )
    with connect(database_path) as connection:
        assert _counts(connection) == before
    assert discord.get_server_attribution(source_event_id) == attribution


@pytest.mark.parametrize(
    "field",
    [
        "loot_cost",
        "quantity_quality_base_cost",
        "quantity_quality_level_increment",
    ],
)
def test_succeeded_replay_rejects_mismatched_settings_value(tmp_path, field) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        before = _counts(connection)
        connection.execute(
            f"UPDATE kakeraloot_settings_observations SET {field} = ? WHERE id = ?",
            (getattr(SETTINGS, field) + 1, first.kakeraloot_settings_observation_id),
        )

    with pytest.raises(InfoklProjectionTargetError, match=f"mismatched {field}"):
        _coordinate(coordinator, source_event_id, None)

    with connect(database_path) as connection:
        assert _counts(connection) == before
        assert connection.execute(
            f"SELECT {field} FROM kakeraloot_settings_observations WHERE id = ?",
            (first.kakeraloot_settings_observation_id,),
        ).fetchone()[0] == getattr(SETTINGS, field) + 1
        assert tuple(
            connection.execute(
                "SELECT status, legacy_import_event_id FROM discord_source_events"
            ).fetchone()
        ) == ("succeeded", first.import_event_id)
        assert connection.execute("SELECT status FROM discord_processing_attempts").fetchone()[0] == "succeeded"


def test_edited_discord_revision_gets_independent_projection(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    first_event, first_attempt = _receive_and_begin(discord, message_id="edited-message")
    second_event, second_attempt = _receive_and_begin(
        discord, suffix="edited", message_id="edited-message"
    )
    _record_attribution(discord, first_event)
    _record_attribution(discord, second_event)

    first = _coordinate(coordinator, first_event, first_attempt)
    second = _coordinate(coordinator, second_event, second_attempt)

    assert first.kakeraloot_settings_observation_id != second.kakeraloot_settings_observation_id
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM kakeraloot_settings_observations"
        ).fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM discord_projection_links").fetchone()[0] == 2


def test_persisted_claimed_link_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    with connect(database_path) as connection:
        slot = server_projection_slot(CatalogRepository._normalize("Server A"))
        value = OBSERVED_AT.isoformat()
        connection.execute(
            """
            INSERT INTO discord_projection_links (
                source_event_id, projection_kind, projection_slot, state,
                claimed_at, created_at, updated_at
            ) VALUES (?, 'catalog.kakeraloot_settings', ?, 'claimed', ?, ?, ?)
            """,
            (source_event_id, slot, value, value, value),
        )

    with pytest.raises(InfoklProjectionIntegrityError, match="still claimed"):
        _coordinate(coordinator, source_event_id, attempt_id)


def test_completed_link_with_missing_target_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        connection.execute(
            "DELETE FROM kakeraloot_settings_observations WHERE id = ?",
            (first.kakeraloot_settings_observation_id,),
        )

    with pytest.raises(InfoklProjectionTargetError, match="missing"):
        _coordinate(coordinator, source_event_id, None)


def test_completed_link_with_wrong_target_table_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE discord_projection_links SET projection_table = 'profile_observations'"
        )

    with pytest.raises(InfoklProjectionIntegrityError, match="inconsistent"):
        _coordinate(coordinator, source_event_id, None)


def test_completed_link_with_wrong_import_event_fails_closed(tmp_path) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    second = catalog.import_kakeraloot_settings(SETTINGS, "Server A", "second payload", "discord")
    with connect(database_path) as connection:
        second_observation_id = connection.execute(
            "SELECT id FROM kakeraloot_settings_observations WHERE import_event_id = ?",
            (second.import_event_id,),
        ).fetchone()[0]
        connection.execute(
            "UPDATE discord_projection_links SET projection_row_id = ?",
            (second_observation_id,),
        )

    with pytest.raises(InfoklProjectionTargetError, match="another import event"):
        _coordinate(coordinator, source_event_id, None)
    assert first.import_event_id != second.import_event_id


def test_completed_link_with_wrong_import_event_kind_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE import_events SET kind = 'profile' WHERE id = ?",
            (first.import_event_id,),
        )

    with pytest.raises(InfoklProjectionTargetError, match="missing or wrong"):
        _coordinate(coordinator, source_event_id, None)


def test_completed_link_with_mismatched_server_context_fails_closed(tmp_path) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    other = catalog.import_kakeraloot_settings(SETTINGS, "Other Server", "other payload", "discord")
    with connect(database_path) as connection:
        other_context_id = connection.execute(
            "SELECT server_context_id FROM kakeraloot_settings_observations WHERE import_event_id = ?",
            (other.import_event_id,),
        ).fetchone()[0]
        connection.execute(
            "UPDATE kakeraloot_settings_observations SET server_context_id = ? WHERE id = ?",
            (other_context_id, first.kakeraloot_settings_observation_id),
        )

    with pytest.raises(InfoklProjectionTargetError, match="mismatched server scope"):
        _coordinate(coordinator, source_event_id, None)


def test_succeeded_replay_with_semantic_slot_mismatch_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE discord_projection_links SET projection_slot = ?",
            ('{"server":"other server"}',),
        )

    with pytest.raises(InfoklProjectionIntegrityError, match="inconsistent"):
        _coordinate(coordinator, source_event_id, None)


def test_attempt_ownership_and_lifecycle_state_are_validated(tmp_path) -> None:
    _database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, _attempt_id = _receive_and_begin(discord)
    other_event_id, other_attempt_id = _receive_and_begin(discord, suffix="two", message_id="two")
    _record_attribution(discord, source_event_id)
    _record_attribution(discord, other_event_id)

    with pytest.raises(InfoklProjectionStateError, match="another source event"):
        _coordinate(coordinator, source_event_id, other_attempt_id)
    discord.mark_processing_failure(
        source_event_id=other_event_id,
        attempt_id=other_attempt_id,
        status="failed",
        retryable=False,
        failure_code="test",
        failure_detail="done",
        finished_at=FINISHED_AT,
    )
    with pytest.raises(InfoklProjectionStateError, match="not processing"):
        _coordinate(coordinator, other_event_id, other_attempt_id)
    with pytest.raises(InfoklProjectionStateError, match="active processing attempt"):
        _coordinate(coordinator, source_event_id, None)


def test_received_event_without_active_attempt_is_rejected(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, _attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    with connect(database_path) as connection:
        connection.execute(
            "DELETE FROM discord_processing_attempts WHERE source_event_id = ?", (source_event_id,)
        )
        connection.execute(
            "UPDATE discord_source_events SET status = 'received' WHERE id = ?", (source_event_id,)
        )

    with pytest.raises(InfoklProjectionStateError, match="active processing attempt"):
        _coordinate(coordinator, source_event_id, None)


def test_succeeded_replay_rejects_non_null_attempt_id(tmp_path) -> None:
    _database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)

    with pytest.raises(InfoklProjectionStateError, match="already succeeded"):
        _coordinate(coordinator, source_event_id, attempt_id)


def test_repository_database_path_mismatch_is_rejected_before_writes(tmp_path) -> None:
    catalog = CatalogRepository(tmp_path / "catalog.db")
    discord = DiscordMessageRepository(tmp_path / "discord.db")

    with pytest.raises(InfoklProjectionDatabasePathError, match="same database path"):
        InfoklProjectionCoordinator(catalog, discord)


def test_only_infokl_projection_link_is_created(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT projection_kind, projection_table FROM discord_projection_links"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("catalog.kakeraloot_settings", "kakeraloot_settings_observations")
        ]
        for table in (
            "roll_observations",
            "profile_observations",
            "claim_observations",
            "server_settings_observations",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
