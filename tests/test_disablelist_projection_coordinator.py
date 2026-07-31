import json
import sqlite3
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import DisableListEntry, DisableListSnapshot
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.services.disablelist_projection_coordinator import (
    DisableListProjectionCoordinator,
    DisableListProjectionCoordinatorError,
    DisableListProjectionDatabasePathError,
    DisableListProjectionIntegrityError,
    DisableListProjectionStateError,
    DisableListProjectionTargetError,
)


OBSERVED_AT = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
FINISHED_AT = datetime(2026, 7, 30, 12, 1, tzinfo=timezone.utc)
STATE = DisableListSnapshot(
    slots_used=13, slots_capacity=16, total_disabled=107_529,
    disabled_wa=41_247, disabled_ha=42_438, disabled_wg=20_996, disabled_hg=14_789,
    wa_pool_limit=40_861, ha_pool_limit=42_213, western_disabled=True, irl_disabled=False,
    entries=(
        DisableListEntry(name="Kadokawa Corporation", disabled_count=13_207),
        DisableListEntry(name="Webcomics", disabled_count=11_073),
        DisableListEntry(name="Kadokawa Corporation", disabled_count=13_207),
    ),
)
BOUNDARY = DisableListSnapshot(
    slots_used=0, slots_capacity=0, total_disabled=0, disabled_wa=0, disabled_ha=0,
    disabled_wg=0, disabled_hg=0, wa_pool_limit=0, ha_pool_limit=None,
    western_disabled=False, irl_disabled=False, entries=(),
)


def _repositories(tmp_path):
    path = tmp_path / "disablelist-coordinator.db"
    catalog = CatalogRepository(path)
    discord = DiscordMessageRepository(path)
    return path, catalog, discord, DisableListProjectionCoordinator(catalog, discord)


def _receive_and_begin(discord, suffix="one"):
    key = MessageAggregateKey(SourcePlatform.DISCORD, "guild", "channel", f"message-{suffix}")
    received = discord.receive_message(
        aggregate_key=key,
        revision_key=MessageRevisionKey.versioned(key, f"payload-{suffix}", "revision-1"),
        event_key=f"event-{suffix}", event_kind="message_create", raw_text="disablelist payload",
        payload_json='{"content":"disablelist payload"}', payload_capture_version="capture-1",
        source_observed_at=OBSERVED_AT, received_at=OBSERVED_AT,
    )
    attempt = discord.begin_processing_attempt(source_event_id=received.source_event_id, parser_version="parser-1", router_version="router-1", started_at=OBSERVED_AT)
    return received.source_event_id, attempt.attempt_id


def _attribute(discord, source_event_id, server="Server", account="Account"):
    discord.record_server_attribution(source_event_id, status="resolved", server_name=server, recorded_at=OBSERVED_AT)
    discord.record_account_attribution(source_event_id, status="resolved", server_name=server, account_name=account, recorded_at=OBSERVED_AT)


def _coordinate(coordinator, source_event_id, attempt_id, state=STATE, server=" Server ", account=" Account "):
    return coordinator.coordinate_disablelist(
        source_event_id=source_event_id, attempt_id=attempt_id, state=state, server=server, account=account,
        raw="disablelist payload", source="discord", observed_at=OBSERVED_AT, finished_at=FINISHED_AT,
    )


def _counts(connection: sqlite3.Connection):
    tables = ("import_events", "server_contexts", "account_contexts", "disablelist_observations", "discord_projection_links", "discord_source_events", "discord_processing_attempts", "discord_source_event_server_attributions", "discord_source_event_account_attributions")
    return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}


def test_first_processing_is_atomic_and_preserves_disablelist_snapshot(tmp_path):
    path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _attribute(discord, source_event_id)
    result = _coordinate(coordinator, source_event_id, attempt_id)
    assert result.imported_count == 1
    assert result.replay_skipped is False
    assert result.durable_success_recorded is True
    assert result.projection_target == ("disablelist_observations", result.disablelist_observation_id)
    with connect(path) as connection:
        assert _counts(connection) == {key: 1 for key in _counts(connection)}
        event = connection.execute("SELECT kind, source, raw_message, observed_at FROM import_events WHERE id = ?", (result.import_event_id,)).fetchone()
        row = connection.execute("SELECT slots_used, slots_capacity, total_disabled, disabled_wa, disabled_ha, disabled_wg, disabled_hg, wa_pool_limit, ha_pool_limit, western_disabled, irl_disabled, entries_json, import_event_id FROM disablelist_observations WHERE id = ?", (result.disablelist_observation_id,)).fetchone()
        link = connection.execute("SELECT projection_kind, projection_slot, projection_table, projection_row_id, state FROM discord_projection_links").fetchone()
        assert tuple(event) == ("disablelist", "discord", "disablelist payload", OBSERVED_AT.isoformat())
        assert tuple(row[:11]) == (13, 16, 107_529, 41_247, 42_438, 20_996, 14_789, 40_861, 42_213, 1, 0)
        assert row["entries_json"] == json.dumps([entry.model_dump() for entry in STATE.entries])
        assert row["import_event_id"] == result.import_event_id
        assert tuple(link) == ("catalog.disablelist", '{"account":"account","server":"server"}', "disablelist_observations", result.disablelist_observation_id, "completed")
        assert tuple(connection.execute("SELECT status, legacy_import_event_id FROM discord_source_events").fetchone()) == ("succeeded", result.import_event_id)
        assert tuple(connection.execute("SELECT status, finished_at FROM discord_processing_attempts").fetchone()) == ("succeeded", FINISHED_AT.isoformat())


def test_boundary_state_and_normalized_slot_preserve_zero_null_false_and_empty_entries(tmp_path):
    path, _catalog, discord, coordinator = _repositories(tmp_path)
    assert coordinator._disablelist_slot(" Server A ", " Account A ") == '{"account":"account a","server":"server a"}'
    source_event_id, attempt_id = _receive_and_begin(discord)
    _attribute(discord, source_event_id)
    result = _coordinate(coordinator, source_event_id, attempt_id, BOUNDARY)
    with connect(path) as connection:
        row = connection.execute("SELECT slots_used, slots_capacity, total_disabled, disabled_wa, disabled_ha, disabled_wg, disabled_hg, wa_pool_limit, ha_pool_limit, western_disabled, irl_disabled, entries_json FROM disablelist_observations WHERE id = ?", (result.disablelist_observation_id,)).fetchone()
        assert tuple(row) == (0, 0, 0, 0, 0, 0, 0, 0, None, 0, 0, "[]")


def test_failure_rolls_back_and_retry_creates_one_projection(tmp_path, monkeypatch):
    path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _attribute(discord, source_event_id)
    monkeypatch.setattr(coordinator, "_complete_projection_link", lambda *_args: (_ for _ in ()).throw(RuntimeError("forced failure")))
    with pytest.raises(RuntimeError, match="forced failure"):
        _coordinate(coordinator, source_event_id, attempt_id)
    with connect(path) as connection:
        counts = _counts(connection)
        assert counts["import_events"] == counts["disablelist_observations"] == counts["discord_projection_links"] == 0
        assert counts["server_contexts"] == counts["account_contexts"] == 0
    monkeypatch.undo()
    result = _coordinate(coordinator, source_event_id, attempt_id)
    assert result.imported_count == 1


def test_replay_reconstructs_without_inserts(tmp_path):
    path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _attribute(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(path) as connection:
        before = _counts(connection)
    replay = DisableListProjectionCoordinator(catalog, discord)
    result = _coordinate(replay, source_event_id, None)
    assert (result.imported_count, result.replay_skipped, result.import_event_id, result.disablelist_observation_id) == (0, True, first.import_event_id, first.disablelist_observation_id)
    with connect(path) as connection:
        assert _counts(connection) == before


@pytest.mark.parametrize("mutation", ("scalar", "limit", "boolean", "entries", "target_table", "target_row", "legacy_kind"))
def test_replay_integrity_failures_do_not_create_replacement_rows(tmp_path, mutation):
    path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _attribute(discord, source_event_id)
    result = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(path) as connection:
        if mutation == "scalar":
            connection.execute("UPDATE disablelist_observations SET slots_used = 99")
        elif mutation == "limit":
            connection.execute("UPDATE disablelist_observations SET ha_pool_limit = 0")
        elif mutation == "boolean":
            connection.execute("UPDATE disablelist_observations SET western_disabled = 0")
        elif mutation == "entries":
            connection.execute("UPDATE disablelist_observations SET entries_json = '[]'")
        elif mutation == "target_table":
            connection.execute("UPDATE discord_projection_links SET projection_table = 'wishlist_observations'")
        elif mutation == "target_row":
            connection.execute("UPDATE discord_projection_links SET projection_row_id = 999999")
        else:
            connection.execute("UPDATE import_events SET kind = 'wishlist' WHERE id = ?", (result.import_event_id,))
        connection.commit()
    with connect(path) as connection:
        before = _counts(connection)
    with pytest.raises(DisableListProjectionCoordinatorError):
        _coordinate(coordinator, source_event_id, None)
    with connect(path) as connection:
        assert _counts(connection) == before


def test_missing_attribution_attempt_and_database_mismatch_fail_closed(tmp_path):
    path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    with pytest.raises(DisableListProjectionIntegrityError):
        _coordinate(coordinator, source_event_id, attempt_id)
    _attribute(discord, source_event_id)
    with pytest.raises(DisableListProjectionStateError):
        _coordinate(coordinator, source_event_id, attempt_id + 99)
    with connect(path) as connection:
        assert _counts(connection)["import_events"] == 0
    with pytest.raises(DisableListProjectionDatabasePathError):
        DisableListProjectionCoordinator(catalog, DiscordMessageRepository(tmp_path / "other.db"))


def test_route_specific_error_hierarchy():
    assert issubclass(DisableListProjectionStateError, DisableListProjectionCoordinatorError)
    assert issubclass(DisableListProjectionIntegrityError, DisableListProjectionCoordinatorError)
    assert issubclass(DisableListProjectionTargetError, DisableListProjectionIntegrityError)


def test_rollback_preserves_preexisting_contexts(tmp_path, monkeypatch):
    path, catalog, discord, coordinator = _repositories(tmp_path)
    catalog.import_disablelist(STATE, "Server", "Account", "existing", "discord")
    source_event_id, attempt_id = _receive_and_begin(discord, "preexisting")
    _attribute(discord, source_event_id)
    monkeypatch.setattr(coordinator, "_complete_projection_link", lambda *_args: (_ for _ in ()).throw(RuntimeError("rollback")))
    with pytest.raises(RuntimeError, match="rollback"):
        _coordinate(coordinator, source_event_id, attempt_id)
    with connect(path) as connection:
        assert _counts(connection) == {
            "import_events": 1, "server_contexts": 1, "account_contexts": 1,
            "disablelist_observations": 1, "discord_projection_links": 0,
            "discord_source_events": 1, "discord_processing_attempts": 1,
            "discord_source_event_server_attributions": 1,
            "discord_source_event_account_attributions": 1,
        }


@pytest.mark.parametrize("attempt_status", ("succeeded", "failed"))
def test_finished_attempts_fail_closed_without_import(tmp_path, attempt_status):
    path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord, attempt_status)
    _attribute(discord, source_event_id)
    with connect(path) as connection:
        connection.execute("UPDATE discord_processing_attempts SET status = ?, finished_at = ? WHERE id = ?", (attempt_status, FINISHED_AT.isoformat(), attempt_id))
        connection.commit()
    with pytest.raises(DisableListProjectionStateError):
        _coordinate(coordinator, source_event_id, attempt_id)
    with connect(path) as connection:
        assert _counts(connection)["import_events"] == 0
        assert _counts(connection)["disablelist_observations"] == 0
        assert _counts(connection)["discord_projection_links"] == 0


@pytest.mark.parametrize("status", ("unresolved", "ambiguous"))
def test_unresolved_or_ambiguous_attribution_fails_closed(tmp_path, status):
    path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord, status)
    discord.record_server_attribution(source_event_id, status=status, server_name=None, recorded_at=OBSERVED_AT)
    discord.record_account_attribution(source_event_id, status=status, server_name=None, account_name=None, recorded_at=OBSERVED_AT)
    with pytest.raises(DisableListProjectionIntegrityError):
        _coordinate(coordinator, source_event_id, attempt_id)
    with connect(path) as connection:
        assert _counts(connection)["import_events"] == 0
        assert _counts(connection)["discord_projection_links"] == 0


@pytest.mark.parametrize("kind", ("server", "account", "account_server"))
def test_conflicting_or_mismatched_attribution_fails_closed(tmp_path, kind):
    path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord, kind)
    _attribute(discord, source_event_id)
    with connect(path) as connection:
        if kind == "server":
            connection.execute("UPDATE discord_source_event_server_attributions SET server_name = 'Other' WHERE source_event_id = ?", (source_event_id,))
        elif kind == "account":
            connection.execute("UPDATE discord_source_event_account_attributions SET account_name = 'Other' WHERE source_event_id = ?", (source_event_id,))
        else:
            connection.execute("UPDATE discord_source_event_account_attributions SET server_name = 'Other' WHERE source_event_id = ?", (source_event_id,))
        connection.commit()
    with pytest.raises(DisableListProjectionIntegrityError):
        _coordinate(coordinator, source_event_id, attempt_id)
    with connect(path) as connection:
        assert _counts(connection)["import_events"] == 0
        assert _counts(connection)["discord_projection_links"] == 0


def _insert_link(connection, source_event_id, kind, slot, state="claimed"):
    value = OBSERVED_AT.isoformat()
    projection_table = "disablelist_observations" if state == "completed" else None
    projection_row_id = 1 if state == "completed" else None
    completed_at = value if state == "completed" else None
    connection.execute(
        "INSERT INTO discord_projection_links (source_event_id, projection_kind, projection_slot, projection_table, projection_row_id, state, claimed_at, completed_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (source_event_id, kind, slot, projection_table, projection_row_id, state, value, completed_at, value, value),
    )


@pytest.mark.parametrize("link_case", ("claim", "completed", "wrong_kind", "wrong_slot"))
def test_existing_projection_links_fail_closed_without_replacement(tmp_path, link_case):
    path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord, link_case)
    _attribute(discord, source_event_id)
    slot = coordinator._disablelist_slot("Server", "Account")
    with connect(path) as connection:
        if link_case == "wrong_kind":
            _insert_link(connection, source_event_id, "catalog.wishlist", slot)
        elif link_case == "wrong_slot":
            _insert_link(connection, source_event_id, "catalog.disablelist", '{"account":"other","server":"server"}')
        else:
            _insert_link(connection, source_event_id, "catalog.disablelist", slot, "claimed" if link_case == "claim" else "completed")
        connection.commit()
    with pytest.raises(DisableListProjectionIntegrityError):
        _coordinate(coordinator, source_event_id, attempt_id)
    with connect(path) as connection:
        assert _counts(connection)["disablelist_observations"] == 0
        assert connection.execute("SELECT COUNT(*) FROM discord_projection_links").fetchone()[0] == 1


@pytest.mark.parametrize("replay_case", ("null_target", "missing_legacy", "missing_import"))
def test_replay_missing_target_or_legacy_event_fails_without_repair(tmp_path, replay_case):
    path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord, replay_case)
    _attribute(discord, source_event_id)
    result = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(path) as connection:
        if replay_case == "null_target":
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute("UPDATE discord_projection_links SET projection_row_id = NULL")
        elif replay_case == "missing_legacy":
            connection.execute("UPDATE discord_source_events SET legacy_import_event_id = NULL")
        else:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("UPDATE discord_source_events SET legacy_import_event_id = 999999")
        connection.commit()
        before = _counts(connection)
    with pytest.raises(DisableListProjectionCoordinatorError):
        _coordinate(coordinator, source_event_id, None)
    with connect(path) as connection:
        assert _counts(connection) == before
        assert connection.execute("SELECT COUNT(*) FROM disablelist_observations WHERE id = ?", (result.disablelist_observation_id,)).fetchone()[0] == 1


@pytest.mark.parametrize("ownership", ("import", "server", "account"))
def test_replay_rejects_wrong_observation_ownership(tmp_path, ownership):
    path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord, ownership)
    _attribute(discord, source_event_id)
    result = _coordinate(coordinator, source_event_id, attempt_id)
    other = catalog.import_disablelist(STATE, "Other Server" if ownership == "server" else "Server", "Other Account" if ownership == "account" else "Account", "other", "discord")
    with connect(path) as connection:
        if ownership == "import":
            target = connection.execute("SELECT id FROM disablelist_observations WHERE import_event_id = ? AND id != ?", (other.import_event_id, result.disablelist_observation_id)).fetchone()[0]
        else:
            target = result.disablelist_observation_id
            other_account = connection.execute(
                "SELECT ac.id FROM account_contexts AS ac JOIN server_contexts AS sc ON sc.id = ac.server_context_id WHERE sc.normalized_name = ? AND ac.normalized_name = ?",
                ("other server" if ownership == "server" else "server", "other account" if ownership == "account" else "account"),
            ).fetchone()[0]
            connection.execute("UPDATE disablelist_observations SET account_context_id = ? WHERE id = ?", (other_account, target))
        if ownership == "import":
            connection.execute("UPDATE discord_projection_links SET projection_row_id = ?", (target,))
        connection.commit()
    with pytest.raises(DisableListProjectionTargetError):
        _coordinate(coordinator, source_event_id, None)
    with connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM disablelist_observations").fetchone()[0] == 2


@pytest.mark.parametrize("state,field", ((BOUNDARY, "ha_pool_limit"), (STATE.model_copy(update={"wa_pool_limit": 0}), "wa_pool_limit")))
def test_replay_rejects_null_zero_limit_tampering(tmp_path, state, field):
    path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord, field)
    _attribute(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id, state)
    with connect(path) as connection:
        connection.execute(f"UPDATE disablelist_observations SET {field} = ?", (0 if getattr(state, field) is None else None,))
        connection.commit()
    with pytest.raises(DisableListProjectionTargetError):
        _coordinate(coordinator, source_event_id, None, state)
    with connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM disablelist_observations").fetchone()[0] == 1


@pytest.mark.parametrize("tamper", ("order", "duplicate", "property"))
def test_replay_rejects_serialized_entry_tampering(tmp_path, tamper):
    path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord, tamper)
    _attribute(discord, source_event_id)
    result = _coordinate(coordinator, source_event_id, attempt_id)
    entries = [entry.model_dump() for entry in STATE.entries]
    if tamper == "order":
        entries = [entries[1], entries[0], entries[2]]
    elif tamper == "duplicate":
        entries = entries[:2]
    else:
        entries[0]["name"] = "Changed bundle"
    with connect(path) as connection:
        connection.execute("UPDATE disablelist_observations SET entries_json = ? WHERE id = ?", (json.dumps(entries), result.disablelist_observation_id))
        connection.commit()
    with pytest.raises(DisableListProjectionTargetError):
        _coordinate(coordinator, source_event_id, None)


def test_retry_has_exactly_one_final_projection_and_excludes_adl(tmp_path, monkeypatch):
    path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord, "retry-cardinality")
    _attribute(discord, source_event_id)
    monkeypatch.setattr(coordinator, "_complete_projection_link", lambda *_args: (_ for _ in ()).throw(RuntimeError("retry")))
    with pytest.raises(RuntimeError):
        _coordinate(coordinator, source_event_id, attempt_id)
    monkeypatch.undo()
    result = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events WHERE kind = 'disablelist'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM disablelist_observations").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM discord_projection_links WHERE projection_kind = 'catalog.disablelist' AND state = 'completed'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM discord_projection_links WHERE projection_kind LIKE '%antidisable%'").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM harem_scans").fetchone()[0] == 0
        assert connection.execute("SELECT status FROM discord_source_events WHERE id = ?", (source_event_id,)).fetchone()[0] == "succeeded"
        assert result.imported_count == 1
