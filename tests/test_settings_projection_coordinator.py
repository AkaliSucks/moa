import sqlite3
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import ServerSettingMetric, ServerSettingsSnapshot
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.services.settings_projection_coordinator import (
    SettingsProjectionCoordinator,
    SettingsProjectionDatabasePathError,
    SettingsProjectionIntegrityError,
    SettingsProjectionResult,
    SettingsProjectionStateError,
    SettingsProjectionTargetError,
)


OBSERVED_AT = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
FINISHED_AT = datetime(2026, 7, 22, 12, 1, tzinfo=timezone.utc)
SETTINGS = ServerSettingsSnapshot(
    server_premium=False,
    prefix="$",
    language="English",
    claim_reset_minutes=60,
    reset_minute="00",
    reset_shift_minutes=0,
    rolls_per_hour=10,
    claim_reaction_expiry_seconds=30,
    claimed_character_rarity_multiplier=2,
    kakera_bonus_percent=15,
    sphere_bonus_percent=5,
    game_mode=1,
    channel_instance=2,
    metrics=(
        ServerSettingMetric(label="Prefix", value="$"),
        ServerSettingMetric(label="Lang", value="English"),
    ),
)


def _repositories(tmp_path):
    database_path = tmp_path / "settings-coordinator.db"
    catalog = CatalogRepository(database_path)
    discord = DiscordMessageRepository(database_path)
    return database_path, catalog, discord, SettingsProjectionCoordinator(catalog, discord)


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
        raw_text="settings payload",
        payload_json='{"content":"settings payload"}',
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


def _coordinate(coordinator, source_event_id, attempt_id, *, server="Server A"):
    return coordinator.coordinate_settings(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        settings=SETTINGS,
        server=server,
        raw="settings payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "server_settings_observations",
        "discord_projection_links",
        "discord_source_event_server_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def test_first_processing_coordinates_settings_and_success(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    attribution = _record_attribution(discord, source_event_id)

    result = _coordinate(coordinator, source_event_id, attempt_id)

    assert result == SettingsProjectionResult(
        imported_count=1,
        import_event_id=result.import_event_id,
        server_settings_observation_id=result.server_settings_observation_id,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("server_settings_observations", result.server_settings_observation_id),
    )
    assert result.import_event_id > 0
    assert result.server_settings_observation_id > 0
    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "server_settings_observations": 1,
            "discord_projection_links": 1,
            "discord_source_event_server_attributions": 1,
            "discord_processing_attempts": 1,
        }
        link = connection.execute(
            "SELECT projection_kind, projection_slot, projection_table, projection_row_id, state "
            "FROM discord_projection_links"
        ).fetchone()
        assert tuple(link) == (
            "catalog.server_settings",
            '{"server":"server a"}',
            "server_settings_observations",
            result.server_settings_observation_id,
            "completed",
        )
        observation = connection.execute(
            "SELECT import_event_id, metrics_json FROM server_settings_observations"
        ).fetchone()
        assert tuple(observation) == (result.import_event_id, '[{"label": "Prefix", "value": "$"}, {"label": "Lang", "value": "English"}]')
        event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events"
        ).fetchone()
        attempt = connection.execute(
            "SELECT status FROM discord_processing_attempts"
        ).fetchone()
        assert tuple(event) == ("succeeded", result.import_event_id)
        assert tuple(attempt) == ("succeeded",)
    assert discord.get_server_attribution(source_event_id) == attribution


def test_projection_slot_is_deterministic_and_uses_catalog_normalization(tmp_path) -> None:
    _database_path, _catalog, _discord, coordinator = _repositories(tmp_path)

    assert coordinator._settings_slot("  Server   A ") == '{"server":"server a"}'
    assert coordinator._settings_slot("Server A") == coordinator._settings_slot(" server a ")


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

    with pytest.raises(SettingsProjectionStateError):
        _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "server_settings_observations": 0,
            "discord_projection_links": 0,
            "discord_source_event_server_attributions": 0 if status is None else 1,
            "discord_processing_attempts": 1,
        }
        assert connection.execute("SELECT status FROM discord_source_events").fetchone()[0] == "processing"
        assert connection.execute("SELECT status FROM discord_processing_attempts").fetchone()[0] == "processing"


def test_failure_after_catalog_writes_rolls_back_every_coordinator_write(tmp_path, monkeypatch) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    attribution = _record_attribution(discord, source_event_id)

    def fail_after_catalog_writes(*_args, **_kwargs):
        raise RuntimeError("forced failure after settings catalog writes")

    monkeypatch.setattr(coordinator, "_complete_projection_link", fail_after_catalog_writes)
    with pytest.raises(RuntimeError, match="forced failure after settings catalog writes"):
        _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "server_settings_observations": 0,
            "discord_projection_links": 0,
            "discord_source_event_server_attributions": 1,
            "discord_processing_attempts": 1,
        }
        assert connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events"
        ).fetchone()[:2] == ("processing", None)
        assert connection.execute("SELECT status FROM discord_processing_attempts").fetchone()[0] == "processing"
    assert discord.get_server_attribution(source_event_id) == attribution


def test_retry_after_rollback_succeeds_exactly_once(tmp_path, monkeypatch) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    original = coordinator._complete_projection_link
    monkeypatch.setattr(
        coordinator,
        "_complete_projection_link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("forced retry failure")),
    )
    with pytest.raises(RuntimeError, match="forced retry failure"):
        _coordinate(coordinator, source_event_id, attempt_id)
    monkeypatch.setattr(coordinator, "_complete_projection_link", original)

    result = _coordinate(coordinator, source_event_id, attempt_id)

    assert result.imported_count == 1
    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "server_settings_observations": 1,
            "discord_projection_links": 1,
            "discord_source_event_server_attributions": 1,
            "discord_processing_attempts": 1,
        }


def test_succeeded_replay_validates_attribution_and_inserts_nothing(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    attribution = _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        before = _counts(connection)

    replay = _coordinate(coordinator, source_event_id, None)

    assert replay == SettingsProjectionResult(
        imported_count=0,
        import_event_id=first.import_event_id,
        server_settings_observation_id=first.server_settings_observation_id,
        replay_skipped=True,
        durable_success_recorded=True,
        projection_target=first.projection_target,
    )
    with connect(database_path) as connection:
        assert _counts(connection) == before
        assert connection.execute("SELECT COUNT(*) FROM discord_processing_attempts").fetchone()[0] == 1
    assert discord.get_server_attribution(source_event_id) == attribution


def test_edited_discord_revision_gets_independent_projection(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    first_event, first_attempt = _receive_and_begin(discord)
    second_event, second_attempt = _receive_and_begin(discord, suffix="edited")
    _record_attribution(discord, first_event)
    _record_attribution(discord, second_event)

    first = _coordinate(coordinator, first_event, first_attempt)
    second = _coordinate(coordinator, second_event, second_attempt)

    assert first.server_settings_observation_id != second.server_settings_observation_id
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM server_settings_observations").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM discord_projection_links").fetchone()[0] == 2


def test_persisted_claimed_link_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    slot = coordinator._settings_slot("Server A")
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO discord_projection_links (
                source_event_id, projection_kind, projection_slot, state,
                claimed_at, created_at, updated_at
            ) VALUES (?, 'catalog.server_settings', ?, 'claimed', ?, ?, ?)
            """,
            (source_event_id, slot, OBSERVED_AT.isoformat(), OBSERVED_AT.isoformat(), OBSERVED_AT.isoformat()),
        )

    with pytest.raises(SettingsProjectionIntegrityError, match="still claimed"):
        _coordinate(coordinator, source_event_id, attempt_id)


def test_completed_link_with_missing_target_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        connection.execute(
            "DELETE FROM server_settings_observations WHERE id = ?",
            (first.server_settings_observation_id,),
        )

    with pytest.raises(SettingsProjectionTargetError, match="missing"):
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

    with pytest.raises(SettingsProjectionIntegrityError, match="inconsistent settings projection link"):
        _coordinate(coordinator, source_event_id, None)


def test_completed_link_with_wrong_import_event_fails_closed(tmp_path) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    second = catalog.import_server_settings(SETTINGS, "Server A", "second payload", "discord")
    with connect(database_path) as connection:
        second_observation_id = connection.execute(
            "SELECT id FROM server_settings_observations WHERE import_event_id = ?",
            (second.import_event_id,),
        ).fetchone()[0]
        connection.execute(
            "UPDATE discord_projection_links SET projection_row_id = ?",
            (second_observation_id,),
        )

    with pytest.raises(SettingsProjectionTargetError, match="another import event"):
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

    with pytest.raises(SettingsProjectionTargetError, match="missing or wrong"):
        _coordinate(coordinator, source_event_id, None)


def test_completed_link_with_mismatched_server_context_fails_closed(tmp_path) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    other = catalog.import_server_settings(SETTINGS, "Other Server", "other payload", "discord")
    with connect(database_path) as connection:
        other_context_id = connection.execute(
            "SELECT server_context_id FROM server_settings_observations WHERE import_event_id = ?",
            (other.import_event_id,),
        ).fetchone()[0]
        connection.execute(
            "UPDATE server_settings_observations SET server_context_id = ? WHERE id = ?",
            (other_context_id, first.server_settings_observation_id),
        )

    with pytest.raises(SettingsProjectionTargetError, match="mismatched server scope"):
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

    with pytest.raises(SettingsProjectionIntegrityError, match="inconsistent settings projection link"):
        _coordinate(coordinator, source_event_id, None)


def test_attempt_ownership_and_lifecycle_state_are_validated(tmp_path) -> None:
    _database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord, suffix="one")
    other_event_id, other_attempt_id = _receive_and_begin(discord, suffix="two")
    _record_attribution(discord, source_event_id)
    _record_attribution(discord, other_event_id)

    with pytest.raises(SettingsProjectionStateError, match="another source event"):
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
    with pytest.raises(SettingsProjectionStateError, match="not processing"):
        _coordinate(coordinator, other_event_id, other_attempt_id)
    with pytest.raises(SettingsProjectionStateError, match="active processing attempt"):
        _coordinate(coordinator, source_event_id, None)
    assert attempt_id > 0


def test_received_event_without_active_attempt_is_rejected(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, _attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    with connect(database_path) as connection:
        connection.execute(
            "DELETE FROM discord_processing_attempts WHERE source_event_id = ?",
            (source_event_id,),
        )
        connection.execute(
            "UPDATE discord_source_events SET status = 'received' WHERE id = ?",
            (source_event_id,),
        )

    with pytest.raises(SettingsProjectionStateError, match="active processing attempt"):
        _coordinate(coordinator, source_event_id, None)


def test_succeeded_replay_rejects_non_null_attempt_id(tmp_path) -> None:
    _database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)

    with pytest.raises(SettingsProjectionStateError, match="already succeeded"):
        _coordinate(coordinator, source_event_id, attempt_id)


def test_repository_database_path_mismatch_is_rejected_before_writes(tmp_path) -> None:
    catalog = CatalogRepository(tmp_path / "catalog.db")
    discord = DiscordMessageRepository(tmp_path / "discord.db")

    with pytest.raises(SettingsProjectionDatabasePathError, match="same database path"):
        SettingsProjectionCoordinator(catalog, discord)


def test_only_settings_projection_link_is_created(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT projection_kind, projection_table FROM discord_projection_links"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("catalog.server_settings", "server_settings_observations")
        ]
        assert connection.execute("SELECT COUNT(*) FROM roll_observations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM profile_observations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM claim_observations").fetchone()[0] == 0
