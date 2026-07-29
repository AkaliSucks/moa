import json
import sqlite3
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import SphereGain, SphereResultSnapshot
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.services.sphere_result_projection_coordinator import (
    SphereResultProjectionCoordinator,
    SphereResultProjectionDatabasePathError,
    SphereResultProjectionIntegrityError,
    SphereResultProjectionResult,
    SphereResultProjectionStateError,
    SphereResultProjectionTargetError,
)


OBSERVED_AT = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
FINISHED_AT = datetime(2026, 7, 29, 12, 1, tzinfo=timezone.utc)
SPHERE_RESULT = SphereResultSnapshot(
    clicks_available=2,
    click_window_minutes=60,
    purple_target=10,
    purple_total=8,
    gains=(
        SphereGain(sphere_type="purple", amount=3),
        SphereGain(sphere_type="blue", amount=4, is_free=True),
    ),
    total_gained=7,
    stock=None,
)


def _repositories(tmp_path):
    database_path = tmp_path / "sphere-result-coordinator.db"
    catalog = CatalogRepository(database_path)
    discord = DiscordMessageRepository(database_path)
    return database_path, catalog, discord, SphereResultProjectionCoordinator(catalog, discord)


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
        raw_text="sphere payload",
        payload_json='{"content":"sphere payload"}',
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
    state=SPHERE_RESULT,
):
    return coordinator.coordinate_sphere_result(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        state=state,
        server=server,
        account=account,
        raw="sphere payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "sphere_result_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_processing_attempts",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _snapshot(database_path):
    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT status, legacy_import_event_id, updated_at FROM discord_source_events"
        ).fetchone()
        attempt = connection.execute(
            "SELECT status, finished_at, failure_code FROM discord_processing_attempts"
        ).fetchone()
        return {
            "counts": _counts(connection),
            "event": tuple(event) if event is not None else None,
            "attempt": tuple(attempt) if attempt is not None else None,
            "links": [
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT projection_kind, projection_slot, projection_table,
                           projection_row_id, state, completed_at
                    FROM discord_projection_links
                    ORDER BY id
                    """
                ).fetchall()
            ],
        }


@pytest.mark.parametrize("stock", [None, 0, 321])
def test_first_processing_writes_one_sphere_projection_and_preserves_values(tmp_path, stock) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    state = SPHERE_RESULT.model_copy(update={"stock": stock})

    result = _coordinate(coordinator, source_event_id, attempt_id, state=state)

    assert result == SphereResultProjectionResult(
        imported_count=1,
        import_event_id=result.import_event_id,
        sphere_result_observation_id=result.sphere_result_observation_id,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("sphere_result_observations", result.sphere_result_observation_id),
    )
    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "sphere_result_observations": 1,
            "discord_projection_links": 1,
            "discord_source_events": 1,
            "discord_processing_attempts": 1,
            "discord_source_event_server_attributions": 1,
            "discord_source_event_account_attributions": 1,
        }
        event = connection.execute(
            "SELECT kind, source, raw_message, observed_at FROM import_events"
        ).fetchone()
        assert tuple(event) == (
            "sphere_result",
            "discord",
            "sphere payload",
            OBSERVED_AT.isoformat(),
        )
        observation = connection.execute(
            """
            SELECT id, account_context_id, snapshot_json, total_gained, stock,
                   observed_at, import_event_id
            FROM sphere_result_observations
            """
        ).fetchone()
        assert observation["id"] == result.sphere_result_observation_id
        assert observation["snapshot_json"] == json.dumps(state.model_dump())
        assert observation["total_gained"] == state.total_gained
        assert observation["stock"] == stock
        assert observation["observed_at"] == OBSERVED_AT.isoformat()
        assert observation["import_event_id"] == result.import_event_id
        assert tuple(
            connection.execute(
                "SELECT name, normalized_name FROM server_contexts"
            ).fetchone()
        ) == ("Server", "server")
        assert tuple(
            connection.execute(
                "SELECT name, normalized_name FROM account_contexts"
            ).fetchone()
        ) == ("Account", "account")
        link = connection.execute(
            """
            SELECT projection_kind, projection_slot, projection_table,
                   projection_row_id, state, completed_at
            FROM discord_projection_links
            """
        ).fetchone()
        assert tuple(link) == (
            "catalog.sphere_result",
            '{"account":"account","server":"server"}',
            "sphere_result_observations",
            result.sphere_result_observation_id,
            "completed",
            FINISHED_AT.isoformat(),
        )
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


def test_projection_slot_is_deterministic_and_normalized(tmp_path) -> None:
    _database_path, _catalog, _discord, coordinator = _repositories(tmp_path)

    assert coordinator._sphere_result_slot("  Server   A ", " Account   A ") == (
        '{"account":"account a","server":"server a"}'
    )
    assert coordinator._sphere_result_slot("Server A", "Account A") == coordinator._sphere_result_slot(
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
                "UPDATE discord_source_event_server_attributions SET status = 'unresolved', server_name = NULL WHERE source_event_id = ?",
                (source_event_id,),
            )
        elif category == "ambiguous_server":
            connection.execute(
                "UPDATE discord_source_event_server_attributions SET status = 'ambiguous', server_name = NULL WHERE source_event_id = ?",
                (source_event_id,),
            )
        elif category == "server_mismatch":
            connection.execute(
                "UPDATE discord_source_event_server_attributions SET server_name = 'Other Server' WHERE source_event_id = ?",
                (source_event_id,),
            )
        elif category == "missing_account":
            connection.execute(
                "DELETE FROM discord_source_event_account_attributions WHERE source_event_id = ?",
                (source_event_id,),
            )
        elif category == "unresolved_account":
            connection.execute(
                "UPDATE discord_source_event_account_attributions SET status = 'unresolved', server_name = NULL, account_name = NULL WHERE source_event_id = ?",
                (source_event_id,),
            )
        elif category == "ambiguous_account":
            connection.execute(
                "UPDATE discord_source_event_account_attributions SET status = 'ambiguous', server_name = NULL, account_name = NULL WHERE source_event_id = ?",
                (source_event_id,),
            )
        elif category == "account_server_mismatch":
            connection.execute(
                "UPDATE discord_source_event_account_attributions SET server_name = 'Other Server' WHERE source_event_id = ?",
                (source_event_id,),
            )
        elif category == "account_mismatch":
            connection.execute(
                "UPDATE discord_source_event_account_attributions SET account_name = 'Other Account' WHERE source_event_id = ?",
                (source_event_id,),
            )
    before = _snapshot(database_path)

    with pytest.raises(SphereResultProjectionIntegrityError, match=message):
        _coordinate(coordinator, source_event_id, attempt_id)

    assert _snapshot(database_path) == before
    assert before["event"][:2] == ("processing", None)
    assert before["attempt"][:1] == ("processing",)
    assert before["counts"]["discord_projection_links"] == 0
    assert before["counts"]["import_events"] == 0
    assert before["counts"]["sphere_result_observations"] == 0


def test_failure_after_catalog_writes_rolls_back_and_retry_succeeds_once(tmp_path, monkeypatch) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    original = coordinator._complete_projection_link
    monkeypatch.setattr(
        coordinator,
        "_complete_projection_link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("forced sphere failure")),
    )

    with pytest.raises(RuntimeError, match="forced sphere failure"):
        _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 0
        assert _counts(connection)["sphere_result_observations"] == 0
        assert _counts(connection)["server_contexts"] == 0
        assert _counts(connection)["account_contexts"] == 0
        assert _counts(connection)["discord_projection_links"] == 0
    monkeypatch.setattr(coordinator, "_complete_projection_link", original)

    result = _coordinate(coordinator, source_event_id, attempt_id)
    assert result.imported_count == 1
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 1
        assert _counts(connection)["sphere_result_observations"] == 1
        assert _counts(connection)["discord_projection_links"] == 1


def test_pre_existing_contexts_are_reused_and_preserved(tmp_path) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    catalog.import_sphere_result(SPHERE_RESULT, "Server", "Account", "existing", "discord")
    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id, account_contexts.id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()

    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    result = _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id, account_contexts.id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert result.import_event_id > 0
        assert _counts(connection)["server_contexts"] == 1
        assert _counts(connection)["account_contexts"] == 1
        assert _counts(connection)["import_events"] == 2
        assert _counts(connection)["sphere_result_observations"] == 2


def test_succeeded_replay_after_reconstructing_coordinator_returns_existing_ids_and_inserts_nothing(
    tmp_path,
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    before = _snapshot(database_path)

    reconstructed_catalog = CatalogRepository(database_path)
    reconstructed_discord = DiscordMessageRepository(database_path)
    reconstructed = SphereResultProjectionCoordinator(
        reconstructed_catalog, reconstructed_discord
    )
    replay = _coordinate(reconstructed, source_event_id, None)

    assert replay == SphereResultProjectionResult(
        imported_count=0,
        import_event_id=first.import_event_id,
        sphere_result_observation_id=first.sphere_result_observation_id,
        replay_skipped=True,
        durable_success_recorded=True,
        projection_target=first.projection_target,
    )
    assert _snapshot(database_path) == before


@pytest.mark.parametrize("mutation", ("missing_target", "incomplete_link", "wrong_table", "wrong_import", "wrong_kind", "wrong_scope", "wrong_account_context", "slot", "snapshot"))
def test_succeeded_replay_validates_completed_target_integrity(tmp_path, mutation: str) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        if mutation == "missing_target":
            connection.execute(
                "DELETE FROM sphere_result_observations WHERE id = ?",
                (first.sphere_result_observation_id,),
            )
        elif mutation == "incomplete_link":
            connection.execute(
                "UPDATE discord_projection_links SET state = 'claimed', completed_at = NULL WHERE source_event_id = ?",
                (source_event_id,),
            )
        elif mutation == "wrong_table":
            connection.execute(
                "UPDATE discord_projection_links SET projection_table = 'profile_observations' WHERE source_event_id = ?",
                (source_event_id,),
            )
        elif mutation == "wrong_import":
            second = catalog.import_sphere_result(
                SPHERE_RESULT, "Server", "Account", "second", "discord"
            )
            second_id = connection.execute(
                "SELECT id FROM sphere_result_observations WHERE import_event_id = ?",
                (second.import_event_id,),
            ).fetchone()[0]
            connection.execute(
                "UPDATE discord_projection_links SET projection_row_id = ? WHERE source_event_id = ?",
                (second_id, source_event_id),
            )
        elif mutation == "wrong_kind":
            connection.execute(
                "UPDATE import_events SET kind = 'profile' WHERE id = ?",
                (first.import_event_id,),
            )
        elif mutation == "wrong_scope":
            connection.execute(
                "UPDATE account_contexts SET normalized_name = 'other account' WHERE normalized_name = 'account'"
            )
        elif mutation == "wrong_account_context":
            second = catalog.import_sphere_result(
                SPHERE_RESULT, "Server", "Other Account", "other", "discord"
            )
            second_id = connection.execute(
                "SELECT id FROM sphere_result_observations WHERE import_event_id = ?",
                (second.import_event_id,),
            ).fetchone()[0]
            connection.execute(
                "UPDATE discord_projection_links SET projection_row_id = ? WHERE source_event_id = ?",
                (second_id, source_event_id),
            )
        elif mutation == "slot":
            connection.execute(
                "UPDATE discord_projection_links SET projection_slot = ? WHERE source_event_id = ?",
                ('{"account":"other","server":"server"}', source_event_id),
            )
        elif mutation == "snapshot":
            connection.execute(
                "UPDATE sphere_result_observations SET total_gained = 99 WHERE id = ?",
                (first.sphere_result_observation_id,),
            )

    with pytest.raises((SphereResultProjectionIntegrityError, SphereResultProjectionTargetError)):
        _coordinate(coordinator, source_event_id, None)


@pytest.mark.parametrize("mutation", ("null_legacy", "missing_legacy", "wrong_kind"))
def test_succeeded_replay_requires_valid_legacy_import_event(tmp_path, mutation: str) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        if mutation == "null_legacy":
            connection.execute(
                "UPDATE discord_source_events SET legacy_import_event_id = NULL WHERE id = ?",
                (source_event_id,),
            )
        elif mutation == "missing_legacy":
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(
                "UPDATE discord_source_events SET legacy_import_event_id = 999999 WHERE id = ?",
                (source_event_id,),
            )
        elif mutation == "wrong_kind":
            connection.execute(
                "UPDATE import_events SET kind = 'timer_state' WHERE id = ?",
                (first.import_event_id,),
            )

    with pytest.raises((SphereResultProjectionIntegrityError, SphereResultProjectionTargetError)):
        _coordinate(coordinator, source_event_id, None)


def test_claimed_or_conflicting_first_link_fails_closed(tmp_path, mutation: str = "claimed") -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    with connect(database_path) as connection:
        if mutation == "claimed":
            connection.execute(
                """
                INSERT INTO discord_projection_links (
                    source_event_id, projection_kind, projection_slot, state,
                    claimed_at, created_at, updated_at
                ) VALUES (?, ?, ?, 'claimed', ?, ?, ?)
                """,
                (
                    source_event_id,
                    coordinator._PROJECTION_KIND,
                    coordinator._sphere_result_slot("Server", "Account"),
                    OBSERVED_AT.isoformat(),
                    OBSERVED_AT.isoformat(),
                    OBSERVED_AT.isoformat(),
                ),
            )
        else:
            connection.execute(
                """
                INSERT INTO discord_projection_links (
                    source_event_id, projection_kind, projection_slot, state,
                    claimed_at, created_at, updated_at
                ) VALUES (?, 'catalog.other', '{}', 'claimed', ?, ?, ?)
                """,
                (source_event_id, OBSERVED_AT.isoformat(), OBSERVED_AT.isoformat(), OBSERVED_AT.isoformat()),
            )

    with pytest.raises(SphereResultProjectionIntegrityError):
        _coordinate(coordinator, source_event_id, attempt_id)


def test_unexpected_first_link_fails_closed(tmp_path) -> None:
    test_claimed_or_conflicting_first_link_fails_closed(tmp_path, mutation="unexpected")


def test_attempt_ownership_and_lifecycle_are_validated(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord, suffix="one")
    other_event_id, other_attempt_id = _receive_and_begin(discord, suffix="two")
    _record_attribution(discord, source_event_id)
    _record_attribution(discord, other_event_id)

    with pytest.raises(SphereResultProjectionStateError, match="another source event"):
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
    with pytest.raises(SphereResultProjectionStateError, match="not processing"):
        _coordinate(coordinator, other_event_id, other_attempt_id)
    with pytest.raises(SphereResultProjectionStateError, match="active processing attempt"):
        _coordinate(coordinator, source_event_id, None)
    assert attempt_id > 0 and database_path.exists()


def test_succeeded_source_event_rejects_supplied_attempt(tmp_path) -> None:
    _database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)

    with pytest.raises(SphereResultProjectionStateError, match="already succeeded"):
        _coordinate(coordinator, source_event_id, attempt_id)


def test_database_path_mismatch_is_rejected_before_writes(tmp_path) -> None:
    catalog_path = tmp_path / "catalog.db"
    discord_path = tmp_path / "discord.db"
    catalog = CatalogRepository(catalog_path)
    discord = DiscordMessageRepository(discord_path)

    with pytest.raises(SphereResultProjectionDatabasePathError):
        SphereResultProjectionCoordinator(catalog, discord)

    with connect(catalog_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
    with connect(discord_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM discord_source_events").fetchone()[0] == 0
