import json
import sqlite3
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import TowerStateSnapshot
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.services.tower_state_projection_coordinator import (
    TowerStateProjectionCoordinator,
    TowerStateProjectionDatabasePathError,
    TowerStateProjectionIntegrityError,
    TowerStateProjectionResult,
    TowerStateProjectionStateError,
    TowerStateProjectionTargetError,
)


OBSERVED_AT = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
FINISHED_AT = datetime(2026, 7, 29, 12, 1, tzinfo=timezone.utc)
TOWER_STATE = TowerStateSnapshot(
    current_level=2,
    completed_towers=3,
    next_level_cost=75_000,
    kakera_balance=7_673,
    built_perk_ids=(2, 7),
)
ZERO_TOWER_STATE = TOWER_STATE.model_copy(update={"completed_towers": 0})
NO_COMPLETED_TOWER_STATE = TOWER_STATE.model_copy(update={"completed_towers": None})


def _repositories(tmp_path):
    database_path = tmp_path / "tower-coordinator.db"
    catalog = CatalogRepository(database_path)
    discord = DiscordMessageRepository(database_path)
    return database_path, catalog, discord, TowerStateProjectionCoordinator(catalog, discord)


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
        raw_text="tower payload",
        payload_json='{"content":"tower payload"}',
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


def _record_attribution(discord, source_event_id, *, server="Server", account="Account"):
    discord.record_server_attribution(
        source_event_id,
        status="resolved",
        server_name=server,
        recorded_at=OBSERVED_AT,
    )
    discord.record_account_attribution(
        source_event_id,
        status="resolved",
        server_name=server,
        account_name=account,
        recorded_at=OBSERVED_AT,
    )


def _coordinate(
    coordinator,
    source_event_id,
    attempt_id,
    *,
    server=" Server ",
    account=" Account ",
    state=TOWER_STATE,
):
    return coordinator.coordinate_tower_state(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        state=state,
        server=server,
        account=account,
        raw="tower payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "tower_state_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
        "roll_observations",
        "profile_observations",
        "claim_observations",
        "server_settings_observations",
        "kakeraloot_settings_observations",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _snapshot(database_path):
    with connect(database_path) as connection:
        server_attribution = connection.execute(
            "SELECT status, server_name FROM discord_source_event_server_attributions"
        ).fetchone()
        account_attribution = connection.execute(
            "SELECT status, server_name, account_name "
            "FROM discord_source_event_account_attributions"
        ).fetchone()
        return {
            "counts": _counts(connection),
            "event": tuple(
                connection.execute(
                    "SELECT status, legacy_import_event_id FROM discord_source_events"
                ).fetchone()
            ),
            "attempt": tuple(
                connection.execute(
                    "SELECT status, finished_at FROM discord_processing_attempts"
                ).fetchone()
            ),
            "server_attribution": tuple(server_attribution) if server_attribution else None,
            "account_attribution": tuple(account_attribution) if account_attribution else None,
        }


def test_first_processing_persists_atomic_tower_projection(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)

    result = _coordinate(coordinator, source_event_id, attempt_id)

    assert result == TowerStateProjectionResult(
        imported_count=1,
        import_event_id=result.import_event_id,
        tower_state_observation_id=result.tower_state_observation_id,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("tower_state_observations", result.tower_state_observation_id),
    )
    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "tower_state_observations": 1,
            "discord_projection_links": 1,
            "discord_source_events": 1,
            "discord_source_event_server_attributions": 1,
            "discord_source_event_account_attributions": 1,
            "discord_processing_attempts": 1,
            "roll_observations": 0,
            "profile_observations": 0,
            "claim_observations": 0,
            "server_settings_observations": 0,
            "kakeraloot_settings_observations": 0,
        }
        link = connection.execute(
            "SELECT projection_kind, projection_slot, projection_table, projection_row_id, "
            "state, completed_at FROM discord_projection_links"
        ).fetchone()
        assert tuple(link) == (
            "catalog.tower_state",
            '{"account":"account","server":"server"}',
            "tower_state_observations",
            result.tower_state_observation_id,
            "completed",
            FINISHED_AT.isoformat(),
        )
        observation = connection.execute(
            "SELECT current_level, completed_towers, next_level_cost, kakera_balance, "
            "built_perk_ids_json, observed_at, import_event_id "
            "FROM tower_state_observations"
        ).fetchone()
        assert tuple(observation[:4]) == (
            TOWER_STATE.current_level,
            TOWER_STATE.completed_towers,
            TOWER_STATE.next_level_cost,
            TOWER_STATE.kakera_balance,
        )
        assert json.loads(observation["built_perk_ids_json"]) == list(TOWER_STATE.built_perk_ids)
        assert observation["observed_at"] == OBSERVED_AT.isoformat()
        assert observation["import_event_id"] == result.import_event_id
        assert tuple(
            connection.execute(
                "SELECT status, legacy_import_event_id FROM discord_source_events"
            ).fetchone()
        ) == ("succeeded", result.import_event_id)
        assert tuple(
            connection.execute(
                "SELECT status, finished_at FROM discord_processing_attempts"
            ).fetchone()
        ) == ("succeeded", FINISHED_AT.isoformat())


@pytest.mark.parametrize(
    ("state", "expected_completed_towers"),
    ((NO_COMPLETED_TOWER_STATE, 0), (ZERO_TOWER_STATE, 0), (TOWER_STATE, 3)),
)
def test_completed_tower_values_preserve_repository_semantics(
    tmp_path, state: TowerStateSnapshot, expected_completed_towers: int
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)

    result = _coordinate(coordinator, source_event_id, attempt_id, state=state)

    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT completed_towers FROM tower_state_observations WHERE id = ?",
            (result.tower_state_observation_id,),
        ).fetchone()
        assert row["completed_towers"] == expected_completed_towers


def test_tower_projection_slot_is_deterministic_and_normalized(tmp_path) -> None:
    _database_path, _catalog, _discord, coordinator = _repositories(tmp_path)

    assert coordinator._tower_state_slot("  Server   A ", " Account   A ") == (
        '{"account":"account a","server":"server a"}'
    )
    assert coordinator._tower_state_slot("Server A", "Account A") == coordinator._tower_state_slot(
        " server a ", " account a "
    )


@pytest.mark.parametrize(
    ("category", "message"),
    (
        ("missing_server", "no persisted server attribution"),
        ("unresolved_server", "non-resolved server attribution"),
        ("ambiguous_server", "non-resolved server attribution"),
        ("server_mismatch", "another server"),
        ("missing_account", "no persisted account attribution"),
        ("unresolved_account", "non-resolved account attribution"),
        ("ambiguous_account", "non-resolved account attribution"),
        ("account_server_mismatch", "mismatched account attribution server"),
        ("account_mismatch", "another account"),
    ),
)
def test_attribution_failures_leave_processing_and_projection_state_unchanged(
    tmp_path, category: str, message: str
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    with connect(database_path) as connection:
        if category == "missing_server":
            connection.execute(
                "DELETE FROM discord_source_event_server_attributions WHERE source_event_id = ?",
                (source_event_id,),
            )
        elif category in {"unresolved_server", "ambiguous_server"}:
            connection.execute(
                "UPDATE discord_source_event_server_attributions SET status = ?, server_name = NULL "
                "WHERE source_event_id = ?",
                (category.removesuffix("_server"), source_event_id),
            )
        elif category == "server_mismatch":
            connection.execute(
                "UPDATE discord_source_event_server_attributions SET server_name = 'Other Server' "
                "WHERE source_event_id = ?",
                (source_event_id,),
            )
        elif category == "missing_account":
            connection.execute(
                "DELETE FROM discord_source_event_account_attributions WHERE source_event_id = ?",
                (source_event_id,),
            )
        elif category in {"unresolved_account", "ambiguous_account"}:
            connection.execute(
                "UPDATE discord_source_event_account_attributions SET status = ?, server_name = NULL, "
                "account_name = NULL WHERE source_event_id = ?",
                (category.removesuffix("_account"), source_event_id),
            )
        elif category == "account_server_mismatch":
            connection.execute(
                "UPDATE discord_source_event_account_attributions SET server_name = 'Other Server' "
                "WHERE source_event_id = ?",
                (source_event_id,),
            )
        elif category == "account_mismatch":
            connection.execute(
                "UPDATE discord_source_event_account_attributions SET account_name = 'Other Account' "
                "WHERE source_event_id = ?",
                (source_event_id,),
            )
    before = _snapshot(database_path)

    with pytest.raises(TowerStateProjectionIntegrityError, match=message):
        _coordinate(coordinator, source_event_id, attempt_id)

    assert _snapshot(database_path) == before
    assert before["event"] == ("processing", None)
    assert before["attempt"][0] == "processing"
    assert before["counts"]["discord_projection_links"] == 0
    assert before["counts"]["import_events"] == 0
    assert before["counts"]["tower_state_observations"] == 0


def test_failure_after_catalog_writes_rolls_back_and_retry_succeeds_once(tmp_path, monkeypatch) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    monkeypatch.setattr(
        coordinator,
        "_complete_projection_link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("forced Tower failure")),
    )

    with pytest.raises(RuntimeError, match="forced Tower failure"):
        _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 0
        assert _counts(connection)["tower_state_observations"] == 0
        assert _counts(connection)["server_contexts"] == 0
        assert _counts(connection)["account_contexts"] == 0
        assert _counts(connection)["discord_projection_links"] == 0
        assert tuple(
            connection.execute(
                "SELECT status, legacy_import_event_id FROM discord_source_events"
            ).fetchone()
        ) == ("processing", None)
    monkeypatch.undo()

    result = _coordinate(coordinator, source_event_id, attempt_id)
    assert result.imported_count == 1
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 1
        assert _counts(connection)["tower_state_observations"] == 1
        assert _counts(connection)["discord_projection_links"] == 1


def test_rollback_preserves_preexisting_contexts(tmp_path, monkeypatch) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    with connect(database_path) as connection:
        server_id = catalog._upsert_server(connection, "Existing Server", OBSERVED_AT)
        catalog._upsert_account(connection, server_id, "Existing Account", OBSERVED_AT)
        connection.commit()
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id, server="Existing Server", account="Existing Account")
    monkeypatch.setattr(
        coordinator,
        "_complete_projection_link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("forced rollback")),
    )

    with pytest.raises(RuntimeError, match="forced rollback"):
        _coordinate(
            coordinator,
            source_event_id,
            attempt_id,
            server=" Existing Server ",
            account=" Existing Account ",
        )

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM tower_state_observations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 1


def test_succeeded_replay_returns_existing_ids_and_reconstructs_from_same_database(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    before = _snapshot(database_path)

    replay = TowerStateProjectionCoordinator(
        CatalogRepository(database_path), DiscordMessageRepository(database_path)
    ).coordinate_tower_state(
        source_event_id=source_event_id,
        attempt_id=None,
        state=NO_COMPLETED_TOWER_STATE,
        server=" Server ",
        account=" Account ",
        raw="replayed payload",
        source="replay",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )

    assert replay == TowerStateProjectionResult(
        imported_count=0,
        import_event_id=first.import_event_id,
        tower_state_observation_id=first.tower_state_observation_id,
        replay_skipped=True,
        durable_success_recorded=True,
        projection_target=first.projection_target,
    )
    assert _snapshot(database_path) == before


@pytest.mark.parametrize(
    "category",
    (
        "missing_server",
        "unresolved_server",
        "ambiguous_server",
        "server_mismatch",
        "missing_account",
        "unresolved_account",
        "ambiguous_account",
        "account_server_mismatch",
        "account_mismatch",
    ),
)
def test_succeeded_replay_revalidates_every_attribution_category(tmp_path, category: str) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        if category == "missing_server":
            connection.execute("DELETE FROM discord_source_event_server_attributions WHERE source_event_id = ?", (source_event_id,))
        elif category in {"unresolved_server", "ambiguous_server"}:
            connection.execute("UPDATE discord_source_event_server_attributions SET status = ?, server_name = NULL WHERE source_event_id = ?", (category.removesuffix("_server"), source_event_id))
        elif category == "server_mismatch":
            connection.execute("UPDATE discord_source_event_server_attributions SET server_name = 'Other Server' WHERE source_event_id = ?", (source_event_id,))
        elif category == "missing_account":
            connection.execute("DELETE FROM discord_source_event_account_attributions WHERE source_event_id = ?", (source_event_id,))
        elif category in {"unresolved_account", "ambiguous_account"}:
            connection.execute("UPDATE discord_source_event_account_attributions SET status = ?, server_name = NULL, account_name = NULL WHERE source_event_id = ?", (category.removesuffix("_account"), source_event_id))
        elif category == "account_server_mismatch":
            connection.execute("UPDATE discord_source_event_account_attributions SET server_name = 'Other Server' WHERE source_event_id = ?", (source_event_id,))
        elif category == "account_mismatch":
            connection.execute("UPDATE discord_source_event_account_attributions SET account_name = 'Other Account' WHERE source_event_id = ?", (source_event_id,))

    with pytest.raises(TowerStateProjectionIntegrityError):
        _coordinate(coordinator, source_event_id, None)


def test_historical_succeeded_replay_without_account_attribution_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        connection.execute(
            "DELETE FROM discord_source_event_account_attributions WHERE source_event_id = ?",
            (source_event_id,),
        )

    with pytest.raises(TowerStateProjectionIntegrityError, match="no persisted account attribution"):
        _coordinate(coordinator, source_event_id, None)


def test_edited_revision_gets_independent_tower_projection(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    first_event, first_attempt = _receive_and_begin(discord, suffix="first")
    second_event, second_attempt = _receive_and_begin(discord, suffix="edited")
    _record_attribution(discord, first_event)
    _record_attribution(discord, second_event)

    first = _coordinate(coordinator, first_event, first_attempt)
    second = _coordinate(coordinator, second_event, second_attempt)

    assert first.tower_state_observation_id != second.tower_state_observation_id
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM tower_state_observations").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM discord_projection_links").fetchone()[0] == 2


def test_claimed_tower_link_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    with connect(database_path) as connection:
        connection.execute(
            "INSERT INTO discord_projection_links (source_event_id, projection_kind, projection_slot, state, claimed_at, created_at, updated_at) "
            "VALUES (?, ?, ?, 'claimed', ?, ?, ?)",
            (
                source_event_id,
                coordinator._PROJECTION_KIND,
                coordinator._tower_state_slot("Server", "Account"),
                OBSERVED_AT.isoformat(),
                OBSERVED_AT.isoformat(),
                OBSERVED_AT.isoformat(),
            ),
        )

    with pytest.raises(TowerStateProjectionIntegrityError, match="still claimed"):
        _coordinate(coordinator, source_event_id, attempt_id)


@pytest.mark.parametrize("link_kind, slot", (("catalog.kakera_state", '{"account":"account","server":"server"}'), ("catalog.tower_state", '{"account":"other","server":"server"}')))
def test_completed_link_with_wrong_kind_or_slot_fails_closed(tmp_path, link_kind: str, slot: str) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    with connect(database_path) as connection:
        connection.execute(
            "INSERT INTO discord_projection_links (source_event_id, projection_kind, projection_slot, projection_table, projection_row_id, state, claimed_at, completed_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?)",
            (source_event_id, link_kind, slot, "tower_state_observations", 1, OBSERVED_AT.isoformat(), FINISHED_AT.isoformat(), OBSERVED_AT.isoformat(), FINISHED_AT.isoformat()),
        )

    with pytest.raises(TowerStateProjectionIntegrityError, match="unexpected projection links"):
        _coordinate(coordinator, source_event_id, attempt_id)


@pytest.mark.parametrize("mutation", ("missing_target", "wrong_table", "null_target", "wrong_import", "wrong_kind", "wrong_scope", "slot"))
def test_succeeded_replay_target_integrity_fails_closed(tmp_path, mutation: str) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        if mutation == "missing_target":
            connection.execute("DELETE FROM tower_state_observations WHERE id = ?", (first.tower_state_observation_id,))
        elif mutation == "wrong_table":
            connection.execute("UPDATE discord_projection_links SET projection_table = 'profile_observations' WHERE source_event_id = ?", (source_event_id,))
        elif mutation == "null_target":
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute("UPDATE discord_projection_links SET projection_table = NULL, projection_row_id = NULL WHERE source_event_id = ?", (source_event_id,))
        elif mutation == "wrong_import":
            second = catalog.import_tower_state(TOWER_STATE, "Server", "Account", "second", "test")
            second_id = connection.execute("SELECT id FROM tower_state_observations WHERE import_event_id = ?", (second.import_event_id,)).fetchone()[0]
            connection.execute("UPDATE discord_projection_links SET projection_row_id = ? WHERE source_event_id = ?", (second_id, source_event_id))
        elif mutation == "wrong_kind":
            connection.execute("UPDATE import_events SET kind = 'profile' WHERE id = ?", (first.import_event_id,))
        elif mutation == "wrong_scope":
            connection.execute("UPDATE account_contexts SET normalized_name = 'other account' WHERE normalized_name = 'account'")
        elif mutation == "slot":
            connection.execute("UPDATE discord_projection_links SET projection_slot = ? WHERE source_event_id = ?", ('{"account":"other","server":"server"}', source_event_id))

    with pytest.raises((TowerStateProjectionIntegrityError, TowerStateProjectionTargetError)):
        _coordinate(coordinator, source_event_id, None)


def test_succeeded_replay_rejects_missing_legacy_import_event(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        connection.execute("UPDATE discord_source_events SET legacy_import_event_id = NULL WHERE id = ?", (source_event_id,))

    with pytest.raises(TowerStateProjectionIntegrityError, match="no legacy import event"):
        _coordinate(coordinator, source_event_id, None)


def test_succeeded_replay_rejects_missing_legacy_import_row(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DELETE FROM import_events WHERE id = ?", (first.import_event_id,))

    with pytest.raises(TowerStateProjectionTargetError, match="legacy Tower import event"):
        _coordinate(coordinator, source_event_id, None)


def test_succeeded_replay_rejects_observation_from_another_context(tmp_path) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    other = catalog.import_tower_state(TOWER_STATE, "Other Server", "Other Account", "other", "test")
    with connect(database_path) as connection:
        other_id = connection.execute("SELECT id FROM tower_state_observations WHERE import_event_id = ?", (other.import_event_id,)).fetchone()[0]
        connection.execute("UPDATE discord_projection_links SET projection_row_id = ? WHERE source_event_id = ?", (other_id, source_event_id))

    with pytest.raises(TowerStateProjectionTargetError, match="another import event"):
        _coordinate(coordinator, source_event_id, None)
    assert first.tower_state_observation_id != other_id


def test_attempt_ownership_and_lifecycle_are_validated(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord, suffix="one")
    other_event_id, other_attempt_id = _receive_and_begin(discord, suffix="two")
    _record_attribution(discord, source_event_id)
    _record_attribution(discord, other_event_id)

    with pytest.raises(TowerStateProjectionStateError, match="another source event"):
        _coordinate(coordinator, source_event_id, other_attempt_id)
    discord.mark_processing_failure(
        source_event_id=other_event_id,
        attempt_id=other_attempt_id,
        status="failed",
        retryable=False,
        failure_code="done",
        failure_detail="done",
        finished_at=FINISHED_AT,
    )
    with pytest.raises(TowerStateProjectionStateError, match="not processing"):
        _coordinate(coordinator, other_event_id, other_attempt_id)
    with pytest.raises(TowerStateProjectionStateError, match="active processing attempt"):
        _coordinate(coordinator, source_event_id, None)
    assert attempt_id > 0 and database_path.exists()


def test_missing_source_event_and_replay_attempt_id_fail_closed(tmp_path) -> None:
    _database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    with pytest.raises(TowerStateProjectionStateError, match="was not found"):
        _coordinate(coordinator, 999, None)

    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)
    with pytest.raises(TowerStateProjectionStateError, match="already succeeded"):
        _coordinate(coordinator, source_event_id, attempt_id)


def test_database_path_mismatch_is_rejected_before_writes(tmp_path) -> None:
    catalog_path = tmp_path / "catalog.db"
    discord_path = tmp_path / "discord.db"
    catalog = CatalogRepository(catalog_path)
    discord = DiscordMessageRepository(discord_path)

    with pytest.raises(TowerStateProjectionDatabasePathError):
        TowerStateProjectionCoordinator(catalog, discord)

    with connect(catalog_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
    with connect(discord_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM discord_source_events").fetchone()[0] == 0


def test_only_tower_projection_link_is_created(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT projection_kind, projection_table FROM discord_projection_links"
            ).fetchall()
        ] == [("catalog.tower_state", "tower_state_observations")]


@pytest.mark.parametrize("field", ("observed_at", "finished_at"))
def test_timestamps_must_be_timezone_aware(tmp_path, field: str) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    kwargs = {
        "source_event_id": source_event_id,
        "attempt_id": attempt_id,
        "state": TOWER_STATE,
        "server": "Server",
        "account": "Account",
        "raw": "raw",
        "source": "test",
        "observed_at": OBSERVED_AT,
        "finished_at": FINISHED_AT,
    }
    kwargs[field] = datetime(2026, 7, 29, 12, 0)

    with pytest.raises(ValueError, match=f"{field} must be timezone-aware"):
        coordinator.coordinate_tower_state(**kwargs)

    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 0
