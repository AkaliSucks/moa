import json
import sqlite3
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import BadgeLevel, KakeraStateSnapshot
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.services.kakera_state_projection_coordinator import (
    KakeraStateProjectionCoordinator,
    KakeraStateProjectionDatabasePathError,
    KakeraStateProjectionIntegrityError,
    KakeraStateProjectionResult,
    KakeraStateProjectionStateError,
    KakeraStateProjectionTargetError,
)


OBSERVED_AT = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
FINISHED_AT = datetime(2026, 7, 27, 12, 1, tzinfo=timezone.utc)
KAKERA_STATE = KakeraStateSnapshot(
    kakera_balance=7_673,
    badges=(
        BadgeLevel(badge_name="bronze", level=4, max_reached=True),
        BadgeLevel(badge_name="silver", level=3, max_reached=False),
        BadgeLevel(badge_name="gold", level=2, max_reached=True),
    ),
)
ZERO_KAKERA_STATE = KakeraStateSnapshot(kakera_balance=0, badges=())


def _repositories(tmp_path):
    database_path = tmp_path / "kakera-coordinator.db"
    catalog = CatalogRepository(database_path)
    discord = DiscordMessageRepository(database_path)
    return database_path, catalog, discord, KakeraStateProjectionCoordinator(catalog, discord)


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
        raw_text="kakera payload",
        payload_json='{"content":"kakera payload"}',
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
    state=KAKERA_STATE,
):
    return coordinator.coordinate_kakera_state(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        state=state,
        server=server,
        account=account,
        raw="kakera payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "kakera_state_observations",
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


def test_successful_first_processing_persists_atomic_kakera_projection(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)

    result = _coordinate(coordinator, source_event_id, attempt_id)

    assert result == KakeraStateProjectionResult(
        imported_count=1,
        import_event_id=result.import_event_id,
        kakera_state_observation_id=result.kakera_state_observation_id,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("kakera_state_observations", result.kakera_state_observation_id),
    )
    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "kakera_state_observations": 1,
            "discord_projection_links": 1,
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
            "catalog.kakera_state",
            '{"account":"account","server":"server"}',
            "kakera_state_observations",
            result.kakera_state_observation_id,
            "completed",
            FINISHED_AT.isoformat(),
        )
        observation = connection.execute(
            "SELECT kakera_balance, badges_json, observed_at, import_event_id "
            "FROM kakera_state_observations"
        ).fetchone()
        assert observation["kakera_balance"] == KAKERA_STATE.kakera_balance
        assert json.loads(observation["badges_json"]) == [
            badge.model_dump() for badge in KAKERA_STATE.badges
        ]
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
        assert _snapshot(database_path)["server_attribution"] == ("resolved", "Server")
        assert _snapshot(database_path)["account_attribution"] == (
            "resolved",
            "Server",
            "Account",
        )


def test_zero_balance_and_boolean_badges_are_preserved(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)

    _coordinate(coordinator, source_event_id, attempt_id, state=ZERO_KAKERA_STATE)

    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT kakera_balance, badges_json FROM kakera_state_observations"
        ).fetchone()
        assert row["kakera_balance"] == 0
        assert json.loads(row["badges_json"]) == []


def test_projection_slot_is_deterministic_and_normalized(tmp_path) -> None:
    _database_path, _catalog, _discord, coordinator = _repositories(tmp_path)

    assert coordinator._kakera_state_slot("  Server   A ", " Account   A ") == (
        '{"account":"account a","server":"server a"}'
    )
    assert coordinator._kakera_state_slot("Server A", "Account A") == coordinator._kakera_state_slot(
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
def test_attribution_failures_leave_all_processing_and_projection_state_unchanged(
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

    with pytest.raises(KakeraStateProjectionIntegrityError, match=message):
        _coordinate(coordinator, source_event_id, attempt_id)

    assert _snapshot(database_path) == before
    assert before["event"] == ("processing", None)
    assert before["attempt"][0] == "processing"
    assert before["counts"]["discord_projection_links"] == 0
    assert before["counts"]["import_events"] == 0
    assert before["counts"]["kakera_state_observations"] == 0
    assert before["counts"]["server_contexts"] == 0
    assert before["counts"]["account_contexts"] == 0


def test_failure_after_catalog_writes_rolls_back_and_retry_succeeds_once(tmp_path, monkeypatch) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    monkeypatch.setattr(
        coordinator,
        "_complete_projection_link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("forced Kakera failure")),
    )

    with pytest.raises(RuntimeError, match="forced Kakera failure"):
        _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 0
        assert _counts(connection)["kakera_state_observations"] == 0
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
        assert _counts(connection)["kakera_state_observations"] == 1
        assert _counts(connection)["discord_projection_links"] == 1


def test_succeeded_replay_returns_existing_ids_and_inserts_nothing(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    before = _snapshot(database_path)

    replay = _coordinate(coordinator, source_event_id, None)

    assert replay == KakeraStateProjectionResult(
        imported_count=0,
        import_event_id=first.import_event_id,
        kakera_state_observation_id=first.kakera_state_observation_id,
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

    with pytest.raises(KakeraStateProjectionIntegrityError):
        _coordinate(coordinator, source_event_id, None)


def test_historical_succeeded_replay_without_account_attribution_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        connection.execute("DELETE FROM discord_source_event_account_attributions WHERE source_event_id = ?", (source_event_id,))

    with pytest.raises(KakeraStateProjectionIntegrityError, match="no persisted account attribution"):
        _coordinate(coordinator, source_event_id, None)


def test_edited_revision_gets_independent_kakera_projection(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    first_event, first_attempt = _receive_and_begin(discord, suffix="first")
    second_event, second_attempt = _receive_and_begin(discord, suffix="edited")
    _record_attribution(discord, first_event)
    _record_attribution(discord, second_event)

    first = _coordinate(coordinator, first_event, first_attempt)
    second = _coordinate(coordinator, second_event, second_attempt)

    assert first.kakera_state_observation_id != second.kakera_state_observation_id
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM kakera_state_observations").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM discord_projection_links").fetchone()[0] == 2


def test_claimed_link_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    with connect(database_path) as connection:
        connection.execute(
            "INSERT INTO discord_projection_links (source_event_id, projection_kind, projection_slot, state, claimed_at, created_at, updated_at) "
            "VALUES (?, ?, ?, 'claimed', ?, ?, ?)",
            (source_event_id, coordinator._PROJECTION_KIND, coordinator._kakera_state_slot("Server", "Account"), OBSERVED_AT.isoformat(), OBSERVED_AT.isoformat(), OBSERVED_AT.isoformat()),
        )

    with pytest.raises(KakeraStateProjectionIntegrityError, match="still claimed"):
        _coordinate(coordinator, source_event_id, attempt_id)


@pytest.mark.parametrize("mutation", ("missing_target", "wrong_table", "wrong_import", "wrong_kind", "wrong_scope", "slot"))
def test_completed_link_integrity_fails_closed(tmp_path, mutation: str) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        if mutation == "missing_target":
            connection.execute("DELETE FROM kakera_state_observations WHERE id = ?", (first.kakera_state_observation_id,))
        elif mutation == "wrong_table":
            connection.execute("UPDATE discord_projection_links SET projection_table = 'profile_observations' WHERE source_event_id = ?", (source_event_id,))
        elif mutation == "wrong_import":
            second = catalog.import_kakera_state(KAKERA_STATE, "Server", "Account", "second", "test")
            second_id = connection.execute("SELECT id FROM kakera_state_observations WHERE import_event_id = ?", (second.import_event_id,)).fetchone()[0]
            connection.execute("UPDATE discord_projection_links SET projection_row_id = ? WHERE source_event_id = ?", (second_id, source_event_id))
        elif mutation == "wrong_kind":
            connection.execute("UPDATE import_events SET kind = 'profile' WHERE id = ?", (first.import_event_id,))
        elif mutation == "wrong_scope":
            connection.execute("UPDATE account_contexts SET normalized_name = 'other account' WHERE normalized_name = 'account'")
        elif mutation == "slot":
            connection.execute("UPDATE discord_projection_links SET projection_slot = ? WHERE source_event_id = ?", ('{"account":"other","server":"server"}', source_event_id))

    with pytest.raises((KakeraStateProjectionIntegrityError, KakeraStateProjectionTargetError)):
        _coordinate(coordinator, source_event_id, None)


def test_attempt_ownership_and_lifecycle_are_validated(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord, suffix="one")
    other_event_id, other_attempt_id = _receive_and_begin(discord, suffix="two")
    _record_attribution(discord, source_event_id)
    _record_attribution(discord, other_event_id)

    with pytest.raises(KakeraStateProjectionStateError, match="another source event"):
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
    with pytest.raises(KakeraStateProjectionStateError, match="not processing"):
        _coordinate(coordinator, other_event_id, other_attempt_id)
    with pytest.raises(KakeraStateProjectionStateError, match="active processing attempt"):
        _coordinate(coordinator, source_event_id, None)
    assert attempt_id > 0 and database_path.exists()


def test_received_without_attempt_and_succeeded_with_attempt_are_rejected(tmp_path) -> None:
    _database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, _attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    with connect(coordinator._database_path) as connection:
        connection.execute("DELETE FROM discord_processing_attempts WHERE source_event_id = ?", (source_event_id,))
        connection.execute("UPDATE discord_source_events SET status = 'received' WHERE id = ?", (source_event_id,))
    with pytest.raises(KakeraStateProjectionStateError, match="not processing"):
        _coordinate(coordinator, source_event_id, None)

    source_event_id, attempt_id = _receive_and_begin(discord, suffix="succeeded")
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)
    with pytest.raises(KakeraStateProjectionStateError, match="already succeeded"):
        _coordinate(coordinator, source_event_id, attempt_id)


def test_database_path_mismatch_is_rejected_before_writes(tmp_path) -> None:
    catalog_path = tmp_path / "catalog.db"
    discord_path = tmp_path / "discord.db"
    catalog = CatalogRepository(catalog_path)
    discord = DiscordMessageRepository(discord_path)

    with pytest.raises(KakeraStateProjectionDatabasePathError):
        KakeraStateProjectionCoordinator(catalog, discord)

    with connect(catalog_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
    with connect(discord_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM discord_source_events").fetchone()[0] == 0


def test_only_kakera_projection_link_is_created(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        assert [tuple(row) for row in connection.execute(
            "SELECT projection_kind, projection_table FROM discord_projection_links"
        ).fetchall()] == [("catalog.kakera_state", "kakera_state_observations")]


@pytest.mark.parametrize("field", ("observed_at", "finished_at"))
def test_timestamps_must_be_timezone_aware(tmp_path, field: str) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    kwargs = {
        "source_event_id": source_event_id,
        "attempt_id": attempt_id,
        "state": KAKERA_STATE,
        "server": "Server",
        "account": "Account",
        "raw": "raw",
        "source": "test",
        "observed_at": OBSERVED_AT,
        "finished_at": FINISHED_AT,
    }
    kwargs[field] = datetime(2026, 7, 27, 12, 0)

    with pytest.raises(ValueError, match=f"{field} must be timezone-aware"):
        coordinator.coordinate_kakera_state(**kwargs)

    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 0
