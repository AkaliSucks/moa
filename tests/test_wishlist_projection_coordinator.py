import json
import sqlite3
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import WishlistEntry, WishlistSnapshot
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.services.wishlist_projection_coordinator import (
    WishlistProjectionCoordinator,
    WishlistProjectionCoordinatorError,
    WishlistProjectionDatabasePathError,
    WishlistProjectionIntegrityError,
    WishlistProjectionResult,
    WishlistProjectionStateError,
    WishlistProjectionTargetError,
)


OBSERVED_AT = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
FINISHED_AT = datetime(2026, 7, 30, 12, 1, tzinfo=timezone.utc)
WISHLIST = WishlistSnapshot(
    wishlist_count=3,
    wishlist_capacity=13,
    starwish_count=2,
    starwish_capacity=2,
    entries=(
        WishlistEntry(
            name="Saber",
            is_starwish=False,
            is_owned_marker_present=True,
            kakera_marker_present=True,
        ),
        WishlistEntry(
            name="Emilia",
            is_starwish=True,
            is_owned_marker_present=False,
            kakera_marker_present=False,
        ),
        WishlistEntry(
            name="Saber",
            is_starwish=False,
            is_owned_marker_present=True,
            kakera_marker_present=True,
        ),
    ),
)
EMPTY_WISHLIST = WishlistSnapshot(
    wishlist_count=0,
    wishlist_capacity=0,
    starwish_count=0,
    starwish_capacity=0,
    entries=(),
)
ZERO_WISHLIST = WishlistSnapshot(
    wishlist_count=0,
    wishlist_capacity=13,
    starwish_count=0,
    starwish_capacity=2,
    entries=(
        WishlistEntry(
            name="Zero Boundary",
            is_starwish=False,
            is_owned_marker_present=False,
            kakera_marker_present=False,
        ),
    ),
)


def _repositories(tmp_path):
    database_path = tmp_path / "wishlist-coordinator.db"
    catalog = CatalogRepository(database_path)
    discord = DiscordMessageRepository(database_path)
    return database_path, catalog, discord, WishlistProjectionCoordinator(catalog, discord)


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
        raw_text="wishlist payload",
        payload_json='{"content":"wishlist payload"}',
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
    state=WISHLIST,
):
    return coordinator.coordinate_wishlist(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        state=state,
        server=server,
        account=account,
        raw="wishlist payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "wishlist_observations",
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
                    FROM discord_projection_links ORDER BY id
                    """
                ).fetchall()
            ],
        }


def test_first_processing_writes_one_wishlist_projection_and_preserves_values(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)

    result = _coordinate(coordinator, source_event_id, attempt_id)

    assert result == WishlistProjectionResult(
        imported_count=1,
        import_event_id=result.import_event_id,
        wishlist_observation_id=result.wishlist_observation_id,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("wishlist_observations", result.wishlist_observation_id),
    )
    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "wishlist_observations": 1,
            "discord_projection_links": 1,
            "discord_source_events": 1,
            "discord_processing_attempts": 1,
            "discord_source_event_server_attributions": 1,
            "discord_source_event_account_attributions": 1,
        }
        event = connection.execute(
            "SELECT id, kind, source, raw_message, observed_at FROM import_events"
        ).fetchone()
        assert tuple(event) == (
            result.import_event_id,
            "wishlist",
            "discord",
            "wishlist payload",
            OBSERVED_AT.isoformat(),
        )
        observation = connection.execute(
            """
            SELECT id, account_context_id, wishlist_count, wishlist_capacity,
                   starwish_count, starwish_capacity, entries_json, observed_at,
                   import_event_id
            FROM wishlist_observations
            """
        ).fetchone()
        assert observation["id"] == result.wishlist_observation_id
        assert observation["wishlist_count"] == WISHLIST.wishlist_count
        assert observation["wishlist_capacity"] == WISHLIST.wishlist_capacity
        assert observation["starwish_count"] == WISHLIST.starwish_count
        assert observation["starwish_capacity"] == WISHLIST.starwish_capacity
        assert observation["entries_json"] == json.dumps(
            [entry.model_dump() for entry in WISHLIST.entries]
        )
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
            "catalog.wishlist",
            '{"account":"account","server":"server"}',
            "wishlist_observations",
            result.wishlist_observation_id,
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

    assert coordinator._wishlist_slot("  Server   A ", " Account   A ") == (
        '{"account":"account a","server":"server a"}'
    )
    assert coordinator._wishlist_slot("Server A", "Account A") == coordinator._wishlist_slot(
        " server a ", " account a "
    )


@pytest.mark.parametrize(
    ("state", "expected_entries"),
    ((WISHLIST, WISHLIST.entries), (EMPTY_WISHLIST, ()), (ZERO_WISHLIST, ZERO_WISHLIST.entries)),
)
def test_boundary_states_preserve_counts_order_markers_duplicates_and_empty_lists(
    tmp_path, state, expected_entries
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)

    result = _coordinate(coordinator, source_event_id, attempt_id, state=state)

    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT wishlist_count, wishlist_capacity, starwish_count, starwish_capacity,
                   entries_json FROM wishlist_observations WHERE id = ?
            """,
            (result.wishlist_observation_id,),
        ).fetchone()
        assert tuple(row[:4]) == (
            state.wishlist_count,
            state.wishlist_capacity,
            state.starwish_count,
            state.starwish_capacity,
        )
        assert row["entries_json"] == json.dumps(
            [entry.model_dump() for entry in expected_entries]
        )
        assert json.loads(row["entries_json"]) == [
            entry.model_dump() for entry in expected_entries
        ]


def test_failure_after_catalog_insertion_rolls_back_and_retry_creates_one_projection(
    tmp_path, monkeypatch
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    original = coordinator._complete_projection_link
    monkeypatch.setattr(
        coordinator,
        "_complete_projection_link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("forced wishlist failure")),
    )

    with pytest.raises(RuntimeError, match="forced wishlist failure"):
        _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 0
        assert _counts(connection)["wishlist_observations"] == 0
        assert _counts(connection)["server_contexts"] == 0
        assert _counts(connection)["account_contexts"] == 0
        assert _counts(connection)["discord_projection_links"] == 0
        assert tuple(
            connection.execute(
                "SELECT status, legacy_import_event_id FROM discord_source_events"
            ).fetchone()
        ) == ("processing", None)
    monkeypatch.setattr(coordinator, "_complete_projection_link", original)

    result = _coordinate(coordinator, source_event_id, attempt_id)
    assert result.imported_count == 1
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 1
        assert _counts(connection)["wishlist_observations"] == 1
        assert _counts(connection)["discord_projection_links"] == 1


def test_failure_rollback_preserves_pre_existing_contexts(tmp_path, monkeypatch) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    catalog.import_wishlist(WISHLIST, "Server", "Account", "existing", "discord")
    with connect(database_path) as connection:
        existing = tuple(
            connection.execute(
                """
                SELECT server_contexts.id, account_contexts.id
                FROM server_contexts
                JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
                """
            ).fetchone()
        )
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    monkeypatch.setattr(
        coordinator,
        "_complete_projection_link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("rollback")),
    )

    with pytest.raises(RuntimeError, match="rollback"):
        _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        assert tuple(
            connection.execute(
                """
                SELECT server_contexts.id, account_contexts.id
                FROM server_contexts
                JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
                """
            ).fetchone()
        ) == existing
        assert _counts(connection)["server_contexts"] == 1
        assert _counts(connection)["account_contexts"] == 1
        assert _counts(connection)["import_events"] == 1
        assert _counts(connection)["wishlist_observations"] == 1


def test_succeeded_replay_after_reconstructing_coordinator_inserts_nothing(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    before = _snapshot(database_path)

    reconstructed = WishlistProjectionCoordinator(
        CatalogRepository(database_path), DiscordMessageRepository(database_path)
    )
    replay = _coordinate(reconstructed, source_event_id, None)

    assert replay == WishlistProjectionResult(
        imported_count=0,
        import_event_id=first.import_event_id,
        wishlist_observation_id=first.wishlist_observation_id,
        replay_skipped=True,
        durable_success_recorded=True,
        projection_target=first.projection_target,
    )
    assert _snapshot(database_path) == before


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_target",
        "null_target",
        "incomplete_link",
        "wrong_table",
        "wrong_import",
        "wrong_kind",
        "wrong_scope",
        "wrong_account_context",
        "slot",
        "snapshot",
    ),
)
def test_succeeded_replay_validates_completed_target_integrity(tmp_path, mutation) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    if mutation == "wrong_import":
        second = catalog.import_wishlist(WISHLIST, "Server", "Account", "second", "discord")
        with connect(database_path) as connection:
            second_id = connection.execute(
                "SELECT id FROM wishlist_observations WHERE import_event_id = ?",
                (second.import_event_id,),
            ).fetchone()[0]
            connection.execute(
                "UPDATE discord_projection_links SET projection_row_id = ? WHERE source_event_id = ?",
                (second_id, source_event_id),
            )
    elif mutation == "wrong_account_context":
        second = catalog.import_wishlist(WISHLIST, "Server", "Other Account", "other", "discord")
        with connect(database_path) as connection:
            second_id = connection.execute(
                "SELECT id FROM wishlist_observations WHERE import_event_id = ?",
                (second.import_event_id,),
            ).fetchone()[0]
            connection.execute(
                "UPDATE discord_projection_links SET projection_row_id = ? WHERE source_event_id = ?",
                (second_id, source_event_id),
            )
    else:
        with connect(database_path) as connection:
            if mutation == "missing_target":
                connection.execute(
                    "DELETE FROM wishlist_observations WHERE id = ?",
                    (first.wishlist_observation_id,),
                )
            elif mutation == "null_target":
                connection.execute("PRAGMA ignore_check_constraints = ON")
                connection.execute(
                    "UPDATE discord_projection_links SET projection_table = NULL, projection_row_id = NULL WHERE source_event_id = ?",
                    (source_event_id,),
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
            elif mutation == "wrong_kind":
                connection.execute(
                    "UPDATE import_events SET kind = 'profile' WHERE id = ?",
                    (first.import_event_id,),
                )
            elif mutation == "wrong_scope":
                connection.execute(
                    "UPDATE account_contexts SET normalized_name = 'other account' WHERE normalized_name = 'account'"
                )
            elif mutation == "slot":
                connection.execute(
                    "UPDATE discord_projection_links SET projection_slot = ? WHERE source_event_id = ?",
                    ('{"account":"other","server":"server"}', source_event_id),
                )
            elif mutation == "snapshot":
                connection.execute(
                    "UPDATE wishlist_observations SET entries_json = ? WHERE id = ?",
                    ('[{"name":"changed"}]', first.wishlist_observation_id),
                )

    before = _snapshot(database_path)
    with pytest.raises((WishlistProjectionIntegrityError, WishlistProjectionTargetError)):
        _coordinate(coordinator, source_event_id, None)
    assert _snapshot(database_path) == before


@pytest.mark.parametrize("mutation", ("null_legacy", "missing_legacy", "wrong_kind"))
def test_succeeded_replay_requires_valid_legacy_import_event(tmp_path, mutation) -> None:
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
        else:
            connection.execute(
                "UPDATE import_events SET kind = 'profile' WHERE id = ?",
                (first.import_event_id,),
            )

    with pytest.raises((WishlistProjectionIntegrityError, WishlistProjectionTargetError)):
        _coordinate(coordinator, source_event_id, None)


def test_claimed_or_conflicting_first_link_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
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
                coordinator._PROJECTION_KIND,
                coordinator._wishlist_slot("Server", "Account"),
                OBSERVED_AT.isoformat(),
                OBSERVED_AT.isoformat(),
                OBSERVED_AT.isoformat(),
            ),
        )

    with pytest.raises(WishlistProjectionIntegrityError):
        _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 0
        assert _counts(connection)["wishlist_observations"] == 0


def test_unexpected_first_link_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO discord_projection_links (
                source_event_id, projection_kind, projection_slot, state,
                claimed_at, created_at, updated_at
            ) VALUES (?, 'catalog.other', '{}', 'claimed', ?, ?, ?)
            """,
            (
                source_event_id,
                OBSERVED_AT.isoformat(),
                OBSERVED_AT.isoformat(),
                OBSERVED_AT.isoformat(),
            ),
        )

    with pytest.raises(WishlistProjectionIntegrityError):
        _coordinate(coordinator, source_event_id, attempt_id)


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
        else:
            connection.execute(
                "UPDATE discord_source_event_account_attributions SET account_name = 'Other Account' WHERE source_event_id = ?",
                (source_event_id,),
            )
    before = _snapshot(database_path)

    with pytest.raises(WishlistProjectionIntegrityError, match=message):
        _coordinate(coordinator, source_event_id, attempt_id)

    assert _snapshot(database_path) == before
    assert before["event"][:2] == ("processing", None)
    assert before["attempt"][:1] == ("processing",)
    assert before["counts"]["discord_projection_links"] == 0
    assert before["counts"]["import_events"] == 0
    assert before["counts"]["wishlist_observations"] == 0


def test_attempt_ownership_and_lifecycle_are_validated(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord, suffix="one")
    other_event_id, other_attempt_id = _receive_and_begin(discord, suffix="two")
    _record_attribution(discord, source_event_id)
    _record_attribution(discord, other_event_id)

    with pytest.raises(WishlistProjectionStateError, match="another source event"):
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
    with pytest.raises(WishlistProjectionStateError, match="not processing"):
        _coordinate(coordinator, other_event_id, other_attempt_id)
    with pytest.raises(WishlistProjectionStateError, match="active processing attempt"):
        _coordinate(coordinator, source_event_id, None)
    assert attempt_id > 0 and database_path.exists()


def test_succeeded_source_event_rejects_supplied_attempt(tmp_path) -> None:
    _database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)

    with pytest.raises(WishlistProjectionStateError, match="already succeeded"):
        _coordinate(coordinator, source_event_id, attempt_id)


def test_database_path_mismatch_is_rejected_before_writes(tmp_path) -> None:
    catalog_path = tmp_path / "catalog.db"
    discord_path = tmp_path / "discord.db"
    catalog = CatalogRepository(catalog_path)
    discord = DiscordMessageRepository(discord_path)

    with pytest.raises(WishlistProjectionDatabasePathError):
        WishlistProjectionCoordinator(catalog, discord)

    with connect(catalog_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
    with connect(discord_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM discord_source_events").fetchone()[0] == 0


def test_missing_source_event_fails_closed_without_writes(tmp_path) -> None:
    database_path, _catalog, _discord, coordinator = _repositories(tmp_path)

    with pytest.raises(WishlistProjectionStateError, match="was not found"):
        _coordinate(coordinator, 999999, 1)

    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 0
        assert _counts(connection)["wishlist_observations"] == 0


def test_coordinator_error_hierarchy_is_route_specific() -> None:
    assert issubclass(WishlistProjectionStateError, WishlistProjectionCoordinatorError)
    assert issubclass(WishlistProjectionIntegrityError, WishlistProjectionCoordinatorError)
    assert issubclass(WishlistProjectionTargetError, WishlistProjectionIntegrityError)
