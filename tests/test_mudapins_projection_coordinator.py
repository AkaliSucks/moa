import json
import sqlite3
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import MudapinSnapshot
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.services.mudapins_projection_coordinator import (
    MudapinsProjectionCoordinator,
    MudapinsProjectionDatabasePathError,
    MudapinsProjectionIntegrityError,
    MudapinsProjectionResult,
    MudapinsProjectionStateError,
    MudapinsProjectionTargetError,
)


OBSERVED_AT = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
FINISHED_AT = datetime(2026, 7, 28, 12, 1, tzinfo=timezone.utc)
SNAPSHOT = MudapinSnapshot(
    pin_markers=(":pinA:", ":logopinB:", ":pinA:", ":logopinC:")
)


def _repositories(tmp_path):
    database_path = tmp_path / "mudapins-coordinator.db"
    catalog = CatalogRepository(database_path)
    discord = DiscordMessageRepository(database_path)
    return database_path, catalog, discord, MudapinsProjectionCoordinator(catalog, discord)


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
        raw_text="mudapins payload",
        payload_json='{"content":"mudapins payload"}',
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


def _coordinate(
    coordinator,
    source_event_id,
    attempt_id,
    *,
    snapshot=SNAPSHOT,
    server=" Server ",
    account=" Account ",
):
    return coordinator.coordinate_mudapins(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        snapshot=snapshot,
        server=server,
        account=account,
        raw="mudapins payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "mudapin_observations",
        "profile_observations",
        "roll_observations",
        "discord_projection_links",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


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


def _durable_state(connection: sqlite3.Connection):
    return (
        tuple(connection.execute("SELECT * FROM discord_source_events").fetchone()),
        tuple(connection.execute("SELECT * FROM discord_processing_attempts").fetchone()),
        _counts(connection),
        _attribution_rows(connection),
    )


def _set_attribution_failure(database_path, failure: str) -> None:
    statements = {
        "missing_server": "DELETE FROM discord_source_event_server_attributions",
        "unresolved_server": (
            "UPDATE discord_source_event_server_attributions "
            "SET status = 'unresolved', server_name = NULL"
        ),
        "ambiguous_server": (
            "UPDATE discord_source_event_server_attributions "
            "SET status = 'ambiguous', server_name = NULL"
        ),
        "server_mismatch": (
            "UPDATE discord_source_event_server_attributions SET server_name = 'Server B'"
        ),
        "missing_account": "DELETE FROM discord_source_event_account_attributions",
        "unresolved_account": (
            "UPDATE discord_source_event_account_attributions "
            "SET status = 'unresolved', server_name = NULL, account_name = NULL"
        ),
        "ambiguous_account": (
            "UPDATE discord_source_event_account_attributions "
            "SET status = 'ambiguous', server_name = NULL, account_name = NULL"
        ),
        "account_server_mismatch": (
            "UPDATE discord_source_event_account_attributions SET server_name = 'Server B'"
        ),
        "account_mismatch": (
            "UPDATE discord_source_event_account_attributions SET account_name = 'Account B'"
        ),
    }
    with connect(database_path) as connection:
        connection.execute(statements[failure])


def test_first_processing_coordinates_mudapins_and_preserves_snapshot(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    result = _coordinate(coordinator, source_event_id, attempt_id)

    assert result == MudapinsProjectionResult(
        imported_count=1,
        import_event_id=result.import_event_id,
        mudapin_observation_id=result.mudapin_observation_id,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("mudapin_observations", result.mudapin_observation_id),
    )
    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "mudapin_observations": 1,
            "profile_observations": 0,
            "roll_observations": 0,
            "discord_projection_links": 1,
        }
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events"
        ).fetchone()
        assert tuple(event) == ("mudapins", "discord", OBSERVED_AT.isoformat(), "mudapins payload")
        observation = connection.execute(
            "SELECT pin_markers_json, pin_count, observed_at, import_event_id "
            "FROM mudapin_observations"
        ).fetchone()
        assert tuple(observation) == (
            json.dumps(list(SNAPSHOT.pin_markers)),
            4,
            OBSERVED_AT.isoformat(),
            result.import_event_id,
        )
        link = connection.execute(
            "SELECT projection_kind, projection_slot, projection_table, projection_row_id, state "
            "FROM discord_projection_links"
        ).fetchone()
        assert tuple(link) == (
            "catalog.mudapins",
            '{"account":"account","server":"server"}',
            "mudapin_observations",
            result.mudapin_observation_id,
            "completed",
        )
        assert tuple(connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events"
        ).fetchone()) == ("succeeded", result.import_event_id)
        assert tuple(connection.execute(
            "SELECT status, finished_at FROM discord_processing_attempts"
        ).fetchone()) == ("succeeded", FINISHED_AT.isoformat())
        assert _attribution_rows(connection)[0][0][1:3] == ("resolved", "Server")
        assert _attribution_rows(connection)[1][0][1:4] == ("resolved", "Server", "Account")


def test_projection_slot_is_deterministic_and_normalized(tmp_path) -> None:
    _database_path, _catalog, _discord, coordinator = _repositories(tmp_path)
    assert coordinator._mudapins_slot("  SeRver  ", "  AcCount  ") == (
        '{"account":"account","server":"server"}'
    )


@pytest.mark.parametrize(
    ("failure", "message"),
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
def test_attribution_failures_happen_before_writes(
    tmp_path, failure, message
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _set_attribution_failure(database_path, failure)
    with connect(database_path) as connection:
        before = _durable_state(connection)

    with pytest.raises(MudapinsProjectionIntegrityError, match=message):
        _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        assert _durable_state(connection) == before
        assert connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()[0] == "processing"


def test_failure_after_catalog_writes_rolls_back_every_coordinator_write(
    tmp_path, monkeypatch
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    def fail_after_catalog_writes(*_args, **_kwargs):
        raise RuntimeError("forced failure after Mudapins catalog writes")

    monkeypatch.setattr(coordinator, "_complete_projection_link", fail_after_catalog_writes)
    with pytest.raises(RuntimeError, match="forced failure after Mudapins catalog writes"):
        _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "mudapin_observations": 0,
            "profile_observations": 0,
            "roll_observations": 0,
            "discord_projection_links": 0,
        }
        assert tuple(connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events"
        ).fetchone()) == ("processing", None)
        assert connection.execute("SELECT status FROM discord_processing_attempts").fetchone()[0] == (
            "processing"
        )


def test_retry_after_rollback_succeeds_exactly_once(tmp_path, monkeypatch) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
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
            "account_contexts": 1,
            "mudapin_observations": 1,
            "profile_observations": 0,
            "roll_observations": 0,
            "discord_projection_links": 1,
        }


def test_matching_succeeded_replay_returns_existing_ids_and_inserts_nothing(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        before = _durable_state(connection)

    replay = _coordinate(coordinator, source_event_id, None)

    assert replay == MudapinsProjectionResult(
        imported_count=0,
        import_event_id=first.import_event_id,
        mudapin_observation_id=first.mudapin_observation_id,
        replay_skipped=True,
        durable_success_recorded=True,
        projection_target=first.projection_target,
    )
    with connect(database_path) as connection:
        assert _durable_state(connection) == before


def test_edited_discord_revision_gets_independent_projection(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    first_event, first_attempt = _receive_and_begin(discord, suffix="one")
    second_event, second_attempt = _receive_and_begin(discord, suffix="edited")

    first = _coordinate(coordinator, first_event, first_attempt)
    second = _coordinate(coordinator, second_event, second_attempt)

    assert first.mudapin_observation_id != second.mudapin_observation_id
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM mudapin_observations").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM discord_projection_links").fetchone()[0] == 2


def test_persisted_claimed_link_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    slot = coordinator._mudapins_slot("Server", "Account")
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO discord_projection_links (
                source_event_id, projection_kind, projection_slot, state,
                claimed_at, created_at, updated_at
            ) VALUES (?, 'catalog.mudapins', ?, 'claimed', ?, ?, ?)
            """,
            (source_event_id, slot, OBSERVED_AT.isoformat(), OBSERVED_AT.isoformat(), OBSERVED_AT.isoformat()),
        )

    with pytest.raises(MudapinsProjectionIntegrityError, match="still claimed"):
        _coordinate(coordinator, source_event_id, attempt_id)


def test_unrelated_projection_link_fails_closed_and_no_mudapins_link_is_created(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO discord_projection_links (
                source_event_id, projection_kind, projection_slot, projection_table,
                projection_row_id, state, claimed_at, completed_at, created_at, updated_at
            ) VALUES (?, 'catalog.roll', '{}', 'roll_observations', 1, 'completed', ?, ?, ?, ?)
            """,
            (source_event_id,) + (OBSERVED_AT.isoformat(),) * 4,
        )

    with pytest.raises(MudapinsProjectionIntegrityError, match="unexpected projection links"):
        _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM mudapin_observations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0


def test_succeeded_replay_rejects_missing_target(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        connection.execute("DELETE FROM mudapin_observations WHERE id = ?", (first.mudapin_observation_id,))

    with pytest.raises(MudapinsProjectionTargetError, match="missing"):
        _coordinate(coordinator, source_event_id, None)


def test_succeeded_replay_rejects_wrong_target_table(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE discord_projection_links SET projection_table = 'roll_observations'"
        )

    with pytest.raises(MudapinsProjectionIntegrityError, match="inconsistent Mudapins projection link"):
        _coordinate(coordinator, source_event_id, None)


def test_succeeded_replay_rejects_wrong_import_event(tmp_path) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    second = catalog.import_mudapins(SNAPSHOT, "Server", "Account", "second", "discord")
    with connect(database_path) as connection:
        second_observation_id = connection.execute(
            "SELECT id FROM mudapin_observations WHERE import_event_id = ?", (second.import_event_id,)
        ).fetchone()[0]
        connection.execute(
            "UPDATE discord_projection_links SET projection_row_id = ?", (second_observation_id,)
        )

    with pytest.raises(MudapinsProjectionTargetError, match="another import event"):
        _coordinate(coordinator, source_event_id, None)
    assert first.import_event_id != second.import_event_id


def test_succeeded_replay_rejects_wrong_import_event_kind(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE import_events SET kind = 'profile' WHERE id = ?", (first.import_event_id,)
        )

    with pytest.raises(MudapinsProjectionTargetError, match="missing or wrong"):
        _coordinate(coordinator, source_event_id, None)


def test_succeeded_replay_rejects_mismatched_context(tmp_path) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    second = catalog.import_mudapins(SNAPSHOT, "Server B", "Account B", "second", "discord")
    with connect(database_path) as connection:
        second_context_id = connection.execute(
            "SELECT account_context_id FROM mudapin_observations WHERE import_event_id = ?",
            (second.import_event_id,),
        ).fetchone()[0]
        connection.execute(
            "UPDATE mudapin_observations SET account_context_id = ? WHERE id = ?",
            (second_context_id, first.mudapin_observation_id),
        )

    with pytest.raises(MudapinsProjectionTargetError, match="mismatched account scope"):
        _coordinate(coordinator, source_event_id, None)


def test_succeeded_replay_rejects_semantic_slot_mismatch(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE discord_projection_links SET projection_slot = ?",
            ('{"account":"other","server":"server"}',),
        )

    with pytest.raises(MudapinsProjectionIntegrityError, match="inconsistent Mudapins projection link"):
        _coordinate(coordinator, source_event_id, None)


@pytest.mark.parametrize(
    ("failure", "message"),
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
def test_succeeded_replay_requires_matching_attribution(
    tmp_path, failure, message
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(coordinator, source_event_id, attempt_id)
    _set_attribution_failure(database_path, failure)
    with connect(database_path) as connection:
        before = _durable_state(connection)

    with pytest.raises(MudapinsProjectionIntegrityError, match=message):
        _coordinate(coordinator, source_event_id, None)

    with connect(database_path) as connection:
        assert _durable_state(connection) == before


def test_historical_succeeded_replay_without_account_attribution_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(coordinator, source_event_id, attempt_id)
    _set_attribution_failure(database_path, "missing_account")

    with pytest.raises(MudapinsProjectionIntegrityError, match="no persisted account attribution"):
        _coordinate(coordinator, source_event_id, None)


def test_attempt_ownership_and_lifecycle_state_are_validated(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, _attempt_id = _receive_and_begin(discord, suffix="one")
    other_event_id, other_attempt_id = _receive_and_begin(discord, suffix="two")

    with pytest.raises(MudapinsProjectionStateError, match="another source event"):
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
    with pytest.raises(MudapinsProjectionStateError, match="not processing"):
        _coordinate(coordinator, other_event_id, other_attempt_id)
    with pytest.raises(MudapinsProjectionStateError, match="active processing attempt"):
        _coordinate(coordinator, source_event_id, None)

    with connect(database_path) as connection:
        connection.execute(
            "DELETE FROM discord_processing_attempts WHERE source_event_id = ?", (source_event_id,)
        )
        connection.execute(
            "UPDATE discord_source_events SET status = 'received' WHERE id = ?", (source_event_id,)
        )
    with pytest.raises(MudapinsProjectionStateError, match="active processing attempt"):
        _coordinate(coordinator, source_event_id, None)


def test_retryable_failed_and_unresolved_terminal_events_fail_closed(tmp_path) -> None:
    _database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    retryable_event, retryable_attempt = _receive_and_begin(discord, suffix="retryable")
    discord.mark_processing_failure(
        source_event_id=retryable_event,
        attempt_id=retryable_attempt,
        status="failed",
        retryable=True,
        failure_code="test",
        failure_detail="retry",
        finished_at=FINISHED_AT,
    )
    with pytest.raises(MudapinsProjectionStateError, match="active processing attempt"):
        _coordinate(coordinator, retryable_event, None)

    terminal_event, terminal_attempt = _receive_and_begin(discord, suffix="terminal")
    discord.mark_processing_failure(
        source_event_id=terminal_event,
        attempt_id=terminal_attempt,
        status="unresolved_attribution",
        retryable=False,
        failure_code="attribution",
        failure_detail="unresolved",
        finished_at=FINISHED_AT,
    )
    with pytest.raises(MudapinsProjectionStateError, match="not processing"):
        _coordinate(coordinator, terminal_event, terminal_attempt)


def test_succeeded_event_rejects_non_null_attempt_id(tmp_path) -> None:
    _database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(coordinator, source_event_id, attempt_id)

    with pytest.raises(MudapinsProjectionStateError, match="already succeeded"):
        _coordinate(coordinator, source_event_id, attempt_id)


def test_repository_database_path_mismatch_is_rejected_before_coordination(tmp_path) -> None:
    catalog = CatalogRepository(tmp_path / "catalog.db")
    discord = DiscordMessageRepository(tmp_path / "discord.db")

    with pytest.raises(MudapinsProjectionDatabasePathError, match="same database path"):
        MudapinsProjectionCoordinator(catalog, discord)


def test_only_mudapins_projection_links_are_created(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT projection_kind, projection_table FROM discord_projection_links"
        ).fetchall()
    assert [tuple(row) for row in rows] == [("catalog.mudapins", "mudapin_observations")]


def test_empty_inventory_is_json_empty_list_with_zero_count(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _coordinate(coordinator, source_event_id, attempt_id, snapshot=MudapinSnapshot(pin_markers=()))

    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT pin_markers_json, pin_count FROM mudapin_observations"
        ).fetchone()
    assert tuple(row) == ("[]", 0)


@pytest.mark.parametrize("field", ["observed_at", "finished_at"])
def test_timestamps_must_be_timezone_aware(tmp_path, field) -> None:
    _database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    values = {
        "source_event_id": source_event_id,
        "attempt_id": attempt_id,
        "snapshot": SNAPSHOT,
        "server": "Server",
        "account": "Account",
        "raw": "mudapins payload",
        "source": "discord",
        "observed_at": OBSERVED_AT,
        "finished_at": FINISHED_AT,
    }
    values[field] = datetime(2026, 7, 28, 12, 0)

    with pytest.raises(ValueError, match="timezone-aware"):
        coordinator.coordinate_mudapins(**values)
