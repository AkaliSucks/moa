import json
import sqlite3
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import TimerStateSnapshot
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.services.timer_projection_coordinator import (
    TimerProjectionCoordinator,
    TimerProjectionDatabasePathError,
    TimerProjectionIntegrityError,
    TimerProjectionResult,
    TimerProjectionStateError,
    TimerProjectionTargetError,
)


OBSERVED_AT = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
FINISHED_AT = datetime(2026, 7, 23, 12, 1, tzinfo=timezone.utc)
TIMER_STATE = TimerStateSnapshot(
    can_claim_now=False,
    claim_reset_minutes=0,
    rolls_left=17,
    rolls_reset_minutes=42,
    rolls_reset_stock=0,
    vote_reset_minutes=None,
    daily_reset_minutes=613,
    daily_kakera_ready=True,
    rt_available=None,
    can_react_kakera_now=True,
    reaction_power_percent=72,
    kakera_button_power_cost_percent=0,
    soulmate_button_power_cost_percent=18,
    kakera_stock=12114,
    gold_key_stock_remaining=0,
    gold_key_reset_minutes=None,
    bku_reset_probability_percent=10,
    oh_remaining=3,
    oc_remaining=None,
    oq_remaining=1,
    oq_stored=0,
    ot_remaining=8,
    ouro_refill_minutes=918,
    rolls_reset_status="limited_timer",
    rolls_per_hour_limit=17,
    rt_reset_minutes=612,
)


def _repositories(tmp_path):
    database_path = tmp_path / "timer-coordinator.db"
    catalog = CatalogRepository(database_path)
    discord = DiscordMessageRepository(database_path)
    return database_path, catalog, discord, TimerProjectionCoordinator(catalog, discord)


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
        raw_text="timer payload",
        payload_json='{"content":"timer payload"}',
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


def _coordinate(coordinator, source_event_id, attempt_id, *, server=" Server ", account=" Account ", state=TIMER_STATE):
    return coordinator.coordinate_timer_state(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        state=state,
        server=server,
        account=account,
        raw="timer payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "timer_state_observations",
        "discord_projection_links",
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
        return {
            "counts": _counts(connection),
            "event": tuple(
                connection.execute(
                    "SELECT status, legacy_import_event_id, updated_at FROM discord_source_events"
                ).fetchone()
            ),
            "attempt": tuple(
                connection.execute(
                    "SELECT status, finished_at, failure_code FROM discord_processing_attempts"
                ).fetchone()
            ),
            "server_attribution": tuple(
                connection.execute(
                    "SELECT status, server_name, created_at, updated_at "
                    "FROM discord_source_event_server_attributions"
                ).fetchone()
            ) if connection.execute("SELECT 1 FROM discord_source_event_server_attributions").fetchone() else None,
            "account_attribution": tuple(
                connection.execute(
                    "SELECT status, server_name, account_name, created_at, updated_at "
                    "FROM discord_source_event_account_attributions"
                ).fetchone()
            ) if connection.execute("SELECT 1 FROM discord_source_event_account_attributions").fetchone() else None,
        }


def test_first_processing_writes_one_timer_projection_and_preserves_snapshot(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id, server="Server", account="Account")

    result = _coordinate(coordinator, source_event_id, attempt_id)

    assert result == TimerProjectionResult(
        imported_count=1,
        import_event_id=result.import_event_id,
        timer_state_observation_id=result.timer_state_observation_id,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("timer_state_observations", result.timer_state_observation_id),
    )
    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "timer_state_observations": 1,
            "discord_projection_links": 1,
            "roll_observations": 0,
            "profile_observations": 0,
            "claim_observations": 0,
            "server_settings_observations": 0,
            "kakeraloot_settings_observations": 0,
        }
        link = connection.execute(
            "SELECT projection_kind, projection_slot, projection_table, projection_row_id, state, completed_at "
            "FROM discord_projection_links"
        ).fetchone()
        assert tuple(link) == (
            "catalog.timer_state",
            '{"account":"account","server":"server"}',
            "timer_state_observations",
            result.timer_state_observation_id,
            "completed",
            FINISHED_AT.isoformat(),
        )
        observation = connection.execute(
            "SELECT import_event_id, snapshot_json, observed_at FROM timer_state_observations"
        ).fetchone()
        assert tuple(observation) == (
            result.import_event_id,
            json.dumps(TIMER_STATE.model_dump()),
            OBSERVED_AT.isoformat(),
        )
        assert tuple(
            connection.execute(
                "SELECT status, legacy_import_event_id FROM discord_source_events"
            ).fetchone()
        ) == ("succeeded", result.import_event_id)
        assert tuple(
            connection.execute("SELECT status, finished_at FROM discord_processing_attempts").fetchone()
        ) == ("succeeded", FINISHED_AT.isoformat())
    assert discord.get_server_attribution(source_event_id).server_name == "Server"
    assert discord.get_account_attribution(source_event_id).account_name == "Account"


def test_projection_slot_is_deterministic_and_normalized(tmp_path) -> None:
    _database_path, _catalog, _discord, coordinator = _repositories(tmp_path)

    assert coordinator._timer_slot("  Server   A ", " Account   A ") == (
        '{"account":"account a","server":"server a"}'
    )
    assert coordinator._timer_slot("Server A", "Account A") == coordinator._timer_slot(
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
def test_attribution_failures_leave_processing_state_and_rows_unchanged(
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
        elif category == "unresolved_server":
            connection.execute(
                "UPDATE discord_source_event_server_attributions SET status = 'unresolved', server_name = NULL "
                "WHERE source_event_id = ?",
                (source_event_id,),
            )
        elif category == "ambiguous_server":
            connection.execute(
                "UPDATE discord_source_event_server_attributions SET status = 'ambiguous', server_name = NULL "
                "WHERE source_event_id = ?",
                (source_event_id,),
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
        elif category == "unresolved_account":
            connection.execute(
                "UPDATE discord_source_event_account_attributions SET status = 'unresolved', server_name = NULL, account_name = NULL "
                "WHERE source_event_id = ?",
                (source_event_id,),
            )
        elif category == "ambiguous_account":
            connection.execute(
                "UPDATE discord_source_event_account_attributions SET status = 'ambiguous', server_name = NULL, account_name = NULL "
                "WHERE source_event_id = ?",
                (source_event_id,),
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

    with pytest.raises(TimerProjectionIntegrityError, match=message):
        _coordinate(coordinator, source_event_id, attempt_id)

    assert _snapshot(database_path) == before
    assert before["event"][:2] == ("processing", None)
    assert before["attempt"][:1] == ("processing",)
    assert before["counts"]["discord_projection_links"] == 0
    assert before["counts"]["import_events"] == 0
    assert before["counts"]["timer_state_observations"] == 0


def test_failure_after_catalog_writes_rolls_back_and_retry_succeeds_once(tmp_path, monkeypatch) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    original = coordinator._complete_projection_link
    monkeypatch.setattr(
        coordinator,
        "_complete_projection_link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("forced timer failure")),
    )

    with pytest.raises(RuntimeError, match="forced timer failure"):
        _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 0
        assert _counts(connection)["timer_state_observations"] == 0
        assert _counts(connection)["server_contexts"] == 0
        assert _counts(connection)["account_contexts"] == 0
        assert _counts(connection)["discord_projection_links"] == 0
    monkeypatch.setattr(coordinator, "_complete_projection_link", original)

    result = _coordinate(coordinator, source_event_id, attempt_id)
    assert result.imported_count == 1
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 1
        assert _counts(connection)["timer_state_observations"] == 1
        assert _counts(connection)["discord_projection_links"] == 1


def test_succeeded_replay_returns_existing_ids_and_inserts_nothing(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    before = _snapshot(database_path)

    replay = _coordinate(coordinator, source_event_id, None)

    assert replay == TimerProjectionResult(
        imported_count=0,
        import_event_id=first.import_event_id,
        timer_state_observation_id=first.timer_state_observation_id,
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
        elif category == "unresolved_server":
            connection.execute("UPDATE discord_source_event_server_attributions SET status = 'unresolved', server_name = NULL WHERE source_event_id = ?", (source_event_id,))
        elif category == "ambiguous_server":
            connection.execute("UPDATE discord_source_event_server_attributions SET status = 'ambiguous', server_name = NULL WHERE source_event_id = ?", (source_event_id,))
        elif category == "server_mismatch":
            connection.execute("UPDATE discord_source_event_server_attributions SET server_name = 'Other Server' WHERE source_event_id = ?", (source_event_id,))
        elif category == "missing_account":
            connection.execute("DELETE FROM discord_source_event_account_attributions WHERE source_event_id = ?", (source_event_id,))
        elif category == "unresolved_account":
            connection.execute("UPDATE discord_source_event_account_attributions SET status = 'unresolved', server_name = NULL, account_name = NULL WHERE source_event_id = ?", (source_event_id,))
        elif category == "ambiguous_account":
            connection.execute("UPDATE discord_source_event_account_attributions SET status = 'ambiguous', server_name = NULL, account_name = NULL WHERE source_event_id = ?", (source_event_id,))
        elif category == "account_server_mismatch":
            connection.execute("UPDATE discord_source_event_account_attributions SET server_name = 'Other Server' WHERE source_event_id = ?", (source_event_id,))
        elif category == "account_mismatch":
            connection.execute("UPDATE discord_source_event_account_attributions SET account_name = 'Other Account' WHERE source_event_id = ?", (source_event_id,))

    with pytest.raises(TimerProjectionIntegrityError):
        _coordinate(coordinator, source_event_id, None)


def test_historical_succeeded_replay_without_account_attribution_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        connection.execute("DELETE FROM discord_source_event_account_attributions WHERE source_event_id = ?", (source_event_id,))

    with pytest.raises(TimerProjectionIntegrityError, match="no persisted account attribution"):
        _coordinate(coordinator, source_event_id, None)


def test_edited_revision_gets_independent_timer_projection(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    first_event, first_attempt = _receive_and_begin(discord, suffix="first")
    second_event, second_attempt = _receive_and_begin(discord, suffix="edited")
    _record_attribution(discord, first_event)
    _record_attribution(discord, second_event)

    first = _coordinate(coordinator, first_event, first_attempt)
    second = _coordinate(coordinator, second_event, second_attempt)

    assert first.timer_state_observation_id != second.timer_state_observation_id
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM timer_state_observations").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM discord_projection_links").fetchone()[0] == 2


def test_claimed_link_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    with connect(database_path) as connection:
        connection.execute(
            "INSERT INTO discord_projection_links (source_event_id, projection_kind, projection_slot, state, claimed_at, created_at, updated_at) "
            "VALUES (?, ?, ?, 'claimed', ?, ?, ?)",
            (source_event_id, coordinator._PROJECTION_KIND, coordinator._timer_slot("Server", "Account"), OBSERVED_AT.isoformat(), OBSERVED_AT.isoformat(), OBSERVED_AT.isoformat()),
        )

    with pytest.raises(TimerProjectionIntegrityError, match="still claimed"):
        _coordinate(coordinator, source_event_id, attempt_id)


@pytest.mark.parametrize("mutation", ("missing_target", "wrong_table", "wrong_import", "wrong_kind", "wrong_scope", "slot"))
def test_completed_link_integrity_fails_closed(tmp_path, mutation: str) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        if mutation == "missing_target":
            connection.execute("DELETE FROM timer_state_observations WHERE id = ?", (first.timer_state_observation_id,))
        elif mutation == "wrong_table":
            connection.execute("UPDATE discord_projection_links SET projection_table = 'profile_observations' WHERE source_event_id = ?", (source_event_id,))
        elif mutation == "wrong_import":
            second = catalog.import_timer_state(TIMER_STATE, "Server", "Account", "second", "test")
            second_id = connection.execute("SELECT id FROM timer_state_observations WHERE import_event_id = ?", (second.import_event_id,)).fetchone()[0]
            connection.execute("UPDATE discord_projection_links SET projection_row_id = ? WHERE source_event_id = ?", (second_id, source_event_id))
        elif mutation == "wrong_kind":
            connection.execute("UPDATE import_events SET kind = 'profile' WHERE id = ?", (first.import_event_id,))
        elif mutation == "wrong_scope":
            connection.execute("UPDATE account_contexts SET normalized_name = 'other account' WHERE normalized_name = 'account'")
        elif mutation == "slot":
            connection.execute("UPDATE discord_projection_links SET projection_slot = ? WHERE source_event_id = ?", ('{"account":"other","server":"server"}', source_event_id))

    with pytest.raises((TimerProjectionIntegrityError, TimerProjectionTargetError)):
        _coordinate(coordinator, source_event_id, None)


def test_attempt_ownership_and_lifecycle_are_validated(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord, suffix="one")
    other_event_id, other_attempt_id = _receive_and_begin(discord, suffix="two")
    _record_attribution(discord, source_event_id)
    _record_attribution(discord, other_event_id)

    with pytest.raises(TimerProjectionStateError, match="another source event"):
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
    with pytest.raises(TimerProjectionStateError, match="not processing"):
        _coordinate(coordinator, other_event_id, other_attempt_id)
    with pytest.raises(TimerProjectionStateError, match="active processing attempt"):
        _coordinate(coordinator, source_event_id, None)
    assert attempt_id > 0 and database_path.exists()


def test_received_without_attempt_and_succeeded_with_attempt_are_rejected(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    with connect(database_path) as connection:
        connection.execute("DELETE FROM discord_processing_attempts WHERE source_event_id = ?", (source_event_id,))
        connection.execute("UPDATE discord_source_events SET status = 'received' WHERE id = ?", (source_event_id,))
    with pytest.raises(TimerProjectionStateError, match="not processing"):
        _coordinate(coordinator, source_event_id, None)

    source_event_id, attempt_id = _receive_and_begin(discord, suffix="succeeded")
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)
    with pytest.raises(TimerProjectionStateError, match="already succeeded"):
        _coordinate(coordinator, source_event_id, attempt_id)


def test_database_path_mismatch_is_rejected_before_writes(tmp_path) -> None:
    catalog_path = tmp_path / "catalog.db"
    discord_path = tmp_path / "discord.db"
    catalog = CatalogRepository(catalog_path)
    discord = DiscordMessageRepository(discord_path)

    with pytest.raises(TimerProjectionDatabasePathError):
        TimerProjectionCoordinator(catalog, discord)

    with connect(catalog_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
    with connect(discord_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM discord_source_events").fetchone()[0] == 0


def test_only_timer_projection_link_is_created(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        assert [tuple(row) for row in connection.execute(
            "SELECT projection_kind, projection_table FROM discord_projection_links"
        ).fetchall()] == [("catalog.timer_state", "timer_state_observations")]
