import sqlite3
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import KakeralootStateSnapshot
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.services.kakeraloot_state_projection_coordinator import (
    KakeralootStateProjectionCoordinator,
    KakeralootStateProjectionDatabasePathError,
    KakeralootStateProjectionIntegrityError,
    KakeralootStateProjectionResult,
    KakeralootStateProjectionStateError,
    KakeralootStateProjectionTargetError,
)


OBSERVED_AT = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
FINISHED_AT = datetime(2026, 7, 30, 12, 1, tzinfo=timezone.utc)
KAKERALOOT_STATE = KakeralootStateSnapshot(
    status_note="guarded state",
    rolls_stacked=17,
    disable_wa_ha_reduction=102,
    disable_wg_hg_reduction=68,
    protected_wish_level=42,
    protected_wish_denominator=4_642,
    mudapins=22,
    rt_cooldown_reduction_hours=2,
    permanent_roll_bonus=1,
    star_branches=3,
    starwish_slots_from_branches=4,
    quantity_level=5,
    quality_level=6,
    usage_count=1_234,
    kakera_balance=7_673,
)
ZERO_KAKERALOOT_STATE = KakeralootStateSnapshot(
    status_note="",
    rolls_stacked=0,
    disable_wa_ha_reduction=0,
    disable_wg_hg_reduction=0,
    protected_wish_level=0,
    protected_wish_denominator=0,
    mudapins=0,
    rt_cooldown_reduction_hours=0,
    permanent_roll_bonus=0,
    star_branches=0,
    starwish_slots_from_branches=0,
    quantity_level=0,
    quality_level=0,
    usage_count=0,
    kakera_balance=0,
)
NULL_KAKERALOOT_STATE = KakeralootStateSnapshot(
    status_note=None,
    rolls_stacked=None,
    disable_wa_ha_reduction=None,
    disable_wg_hg_reduction=None,
    protected_wish_level=None,
    protected_wish_denominator=None,
    mudapins=None,
    rt_cooldown_reduction_hours=None,
    permanent_roll_bonus=None,
    star_branches=None,
    starwish_slots_from_branches=None,
    quantity_level=None,
    quality_level=None,
    usage_count=None,
    kakera_balance=None,
)
NO_KAKERALOOT_STATE = KakeralootStateSnapshot(
    has_kakeraloots=False,
    status_note="No Kakeraloots bought; Mudae did not report loot statistics.",
)


def _repositories(tmp_path):
    database_path = tmp_path / "kakeraloot-state-coordinator.db"
    catalog = CatalogRepository(database_path)
    discord = DiscordMessageRepository(database_path)
    return database_path, catalog, discord, KakeralootStateProjectionCoordinator(catalog, discord)


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
        raw_text="kakeraloot payload",
        payload_json='{"content":"kakeraloot payload"}',
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
    state=KAKERALOOT_STATE,
):
    return coordinator.coordinate_kakeraloot_state(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        state=state,
        server=server,
        account=account,
        raw="kakeraloot payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "kakeraloot_state_observations",
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
            "links": [
                tuple(row)
                for row in connection.execute(
                    "SELECT projection_kind, projection_slot, projection_table, "
                    "projection_row_id, state FROM discord_projection_links"
                ).fetchall()
            ],
        }


def test_first_processing_persists_atomic_kakeraloot_state_projection(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)

    result = _coordinate(coordinator, source_event_id, attempt_id)

    assert result == KakeralootStateProjectionResult(
        imported_count=1,
        import_event_id=result.import_event_id,
        kakeraloot_state_observation_id=result.kakeraloot_state_observation_id,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("kakeraloot_state_observations", result.kakeraloot_state_observation_id),
    )
    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "kakeraloot_state_observations": 1,
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
            "catalog.kakeraloot_state",
            '{"account":"account","server":"server"}',
            "kakeraloot_state_observations",
            result.kakeraloot_state_observation_id,
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


def test_first_processing_links_and_preserves_every_stored_field(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)

    result = _coordinate(coordinator, source_event_id, attempt_id)

    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == (
            "kakeraloot_state",
            "discord",
            "kakeraloot payload",
            OBSERVED_AT.isoformat(),
        )
        row = connection.execute(
            """
            SELECT has_kakeraloots, status_note, rolls_stacked, disable_wa_ha_reduction,
                   disable_wg_hg_reduction, protected_wish_level, protected_wish_denominator,
                   mudapins, rt_cooldown_reduction_hours, permanent_roll_bonus,
                   star_branches, starwish_slots_from_branches, quantity_level, quality_level,
                   usage_count, kakera_balance, observed_at, import_event_id
            FROM kakeraloot_state_observations
            WHERE id = ?
            """,
            (result.kakeraloot_state_observation_id,),
        ).fetchone()
        assert tuple(row) == (
            1,
            "guarded state",
            17,
            102,
            68,
            42,
            4_642,
            22,
            2,
            1,
            3,
            4,
            5,
            6,
            1_234,
            7_673,
            OBSERVED_AT.isoformat(),
            result.import_event_id,
        )


@pytest.mark.parametrize(
    ("state", "expected_has_kakeraloots", "expected_status_note"),
    (
        (KAKERALOOT_STATE, 1, "guarded state"),
        (ZERO_KAKERALOOT_STATE, 1, ""),
        (NULL_KAKERALOOT_STATE, 1, None),
        (
            NO_KAKERALOOT_STATE,
            0,
            "No Kakeraloots bought; Mudae did not report loot statistics.",
        ),
    ),
)
def test_supported_kakeraloot_states_preserve_repository_semantics(
    tmp_path,
    state: KakeralootStateSnapshot,
    expected_has_kakeraloots: int,
    expected_status_note: str | None,
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)

    result = _coordinate(coordinator, source_event_id, attempt_id, state=state)

    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT has_kakeraloots, status_note, rolls_stacked, disable_wa_ha_reduction,
                   disable_wg_hg_reduction, protected_wish_level, protected_wish_denominator,
                   mudapins, rt_cooldown_reduction_hours, permanent_roll_bonus,
                   star_branches, starwish_slots_from_branches, quantity_level, quality_level,
                   usage_count, kakera_balance
            FROM kakeraloot_state_observations
            WHERE id = ?
            """,
            (result.kakeraloot_state_observation_id,),
        ).fetchone()
        assert tuple(row) == (
            expected_has_kakeraloots,
            expected_status_note,
            state.rolls_stacked or 0,
            state.disable_wa_ha_reduction or 0,
            state.disable_wg_hg_reduction or 0,
            state.protected_wish_level or 0,
            state.protected_wish_denominator or 0,
            state.mudapins or 0,
            state.rt_cooldown_reduction_hours or 0,
            state.permanent_roll_bonus or 0,
            state.star_branches or 0,
            state.starwish_slots_from_branches or 0,
            state.quantity_level or 0,
            state.quality_level or 0,
            state.usage_count or 0,
            state.kakera_balance or 0,
        )


def test_kakeraloot_state_slot_is_deterministic_and_normalized(tmp_path) -> None:
    _database_path, _catalog, _discord, coordinator = _repositories(tmp_path)

    assert coordinator._kakeraloot_state_slot("  Server   A ", " Account   A ") == (
        '{"account":"account a","server":"server a"}'
    )
    assert coordinator._kakeraloot_state_slot("Server A", "Account A") == coordinator._kakeraloot_state_slot(
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
def test_attribution_failures_leave_state_unchanged(
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

    with pytest.raises(KakeralootStateProjectionIntegrityError, match=message):
        _coordinate(coordinator, source_event_id, attempt_id)

    assert _snapshot(database_path) == before
    assert before["event"] == ("processing", None)
    assert before["attempt"][0] == "processing"
    assert before["counts"]["discord_projection_links"] == 0
    assert before["counts"]["import_events"] == 0


def test_failure_after_catalog_writes_rolls_back_and_retry_succeeds_once(tmp_path, monkeypatch) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    monkeypatch.setattr(
        coordinator,
        "_complete_projection_link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("forced Kakeraloot failure")),
    )

    with pytest.raises(RuntimeError, match="forced Kakeraloot failure"):
        _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 0
        assert _counts(connection)["kakeraloot_state_observations"] == 0
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
        assert _counts(connection)["kakeraloot_state_observations"] == 1
        assert _counts(connection)["discord_projection_links"] == 1


def test_rollback_preserves_preexisting_contexts(tmp_path, monkeypatch) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    with connect(database_path) as connection:
        server_id = catalog._upsert_server(connection, "Existing Server", OBSERVED_AT)
        catalog._upsert_account(connection, server_id, "Existing Account", OBSERVED_AT)
        connection.commit()
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(
        discord,
        source_event_id,
        server="Existing Server",
        account="Existing Account",
    )
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
        assert connection.execute(
            "SELECT COUNT(*) FROM kakeraloot_state_observations"
        ).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 1


def test_succeeded_replay_returns_existing_ids_and_reconstructs_from_same_database(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    before = _snapshot(database_path)

    replay = KakeralootStateProjectionCoordinator(
        CatalogRepository(database_path), DiscordMessageRepository(database_path)
    ).coordinate_kakeraloot_state(
        source_event_id=source_event_id,
        attempt_id=None,
        state=KAKERALOOT_STATE,
        server=" Server ",
        account=" Account ",
        raw="replayed payload",
        source="replay",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )

    assert replay == KakeralootStateProjectionResult(
        imported_count=0,
        import_event_id=first.import_event_id,
        kakeraloot_state_observation_id=first.kakeraloot_state_observation_id,
        replay_skipped=True,
        durable_success_recorded=True,
        projection_target=first.projection_target,
    )
    assert _snapshot(database_path) == before


@pytest.mark.parametrize("field", (
    "has_kakeraloots",
    "status_note",
    "rolls_stacked",
    "disable_wa_ha_reduction",
    "disable_wg_hg_reduction",
    "protected_wish_level",
    "protected_wish_denominator",
    "mudapins",
    "rt_cooldown_reduction_hours",
    "permanent_roll_bonus",
    "star_branches",
    "starwish_slots_from_branches",
    "quantity_level",
    "quality_level",
    "usage_count",
    "kakera_balance",
))
def test_replay_validates_every_kakeraloot_field(tmp_path, field: str) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)
    changed = {field: False if field == "has_kakeraloots" else "changed"}
    if field not in {"has_kakeraloots", "status_note"}:
        changed[field] = 999_999

    with pytest.raises(KakeralootStateProjectionTargetError, match=field):
        _coordinate(coordinator, source_event_id, None, state=KAKERALOOT_STATE.model_copy(update=changed))
    assert _snapshot(database_path)["counts"]["import_events"] == 1


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
def test_succeeded_replay_revalidates_every_attribution_category(
    tmp_path, category: str, message: str
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)
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
    with pytest.raises(KakeralootStateProjectionIntegrityError, match=message):
        _coordinate(coordinator, source_event_id, None)
    assert _snapshot(database_path) == before


def test_attempt_ownership_and_lifecycle_are_validated(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord, suffix="one")
    other_event_id, other_attempt_id = _receive_and_begin(discord, suffix="two")
    _record_attribution(discord, source_event_id)
    _record_attribution(discord, other_event_id)

    with pytest.raises(KakeralootStateProjectionStateError, match="another source event"):
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
    with pytest.raises(KakeralootStateProjectionStateError, match="not processing"):
        _coordinate(coordinator, other_event_id, other_attempt_id)
    with pytest.raises(KakeralootStateProjectionStateError, match="active processing attempt"):
        _coordinate(coordinator, source_event_id, None)
    assert attempt_id > 0 and database_path.exists()


def test_missing_source_event_and_replay_attempt_id_fail_closed(tmp_path) -> None:
    _database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    with pytest.raises(KakeralootStateProjectionStateError, match="was not found"):
        _coordinate(coordinator, 999, None)

    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)
    with pytest.raises(KakeralootStateProjectionStateError, match="already succeeded"):
        _coordinate(coordinator, source_event_id, attempt_id)


def test_database_path_mismatch_is_rejected_before_writes(tmp_path) -> None:
    catalog_path = tmp_path / "catalog.db"
    discord_path = tmp_path / "discord.db"
    catalog = CatalogRepository(catalog_path)
    discord = DiscordMessageRepository(discord_path)

    with pytest.raises(KakeralootStateProjectionDatabasePathError):
        KakeralootStateProjectionCoordinator(catalog, discord)

    with connect(catalog_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
    with connect(discord_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM discord_source_events").fetchone()[0] == 0


def test_only_kakeraloot_projection_link_is_created(tmp_path) -> None:
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
        ] == [("catalog.kakeraloot_state", "kakeraloot_state_observations")]


@pytest.mark.parametrize(
    ("link_kind", "slot"),
    (
        ("catalog.kakera_state", '{"account":"account","server":"server"}'),
        ("catalog.kakeraloot_state", '{"account":"other","server":"server"}'),
    ),
)
def test_completed_link_with_wrong_kind_or_slot_fails_closed(
    tmp_path, link_kind: str, slot: str
) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    with connect(database_path) as connection:
        connection.execute(
            "INSERT INTO discord_projection_links "
            "(source_event_id, projection_kind, projection_slot, projection_table, "
            "projection_row_id, state, claimed_at, completed_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?)",
            (
                source_event_id,
                link_kind,
                slot,
                "kakeraloot_state_observations",
                1,
                OBSERVED_AT.isoformat(),
                FINISHED_AT.isoformat(),
                OBSERVED_AT.isoformat(),
                FINISHED_AT.isoformat(),
            ),
        )

    with pytest.raises(KakeralootStateProjectionIntegrityError, match="unexpected projection links"):
        _coordinate(coordinator, source_event_id, attempt_id)


def test_claimed_kakeraloot_link_fails_closed(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    with connect(database_path) as connection:
        connection.execute(
            "INSERT INTO discord_projection_links "
            "(source_event_id, projection_kind, projection_slot, state, claimed_at, created_at, updated_at) "
            "VALUES (?, ?, ?, 'claimed', ?, ?, ?)",
            (
                source_event_id,
                coordinator._PROJECTION_KIND,
                coordinator._kakeraloot_state_slot("Server", "Account"),
                OBSERVED_AT.isoformat(),
                OBSERVED_AT.isoformat(),
                OBSERVED_AT.isoformat(),
            ),
        )

    with pytest.raises(KakeralootStateProjectionIntegrityError, match="still claimed"):
        _coordinate(coordinator, source_event_id, attempt_id)


@pytest.mark.parametrize(
    "mutation",
    ("missing_target", "wrong_table", "null_target", "wrong_import", "wrong_kind", "wrong_scope", "slot"),
)
def test_succeeded_replay_target_integrity_fails_closed(tmp_path, mutation: str) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        if mutation == "missing_target":
            connection.execute(
                "DELETE FROM kakeraloot_state_observations WHERE id = ?",
                (first.kakeraloot_state_observation_id,),
            )
        elif mutation == "wrong_table":
            connection.execute(
                "UPDATE discord_projection_links SET projection_table = 'profile_observations' "
                "WHERE source_event_id = ?",
                (source_event_id,),
            )
        elif mutation == "null_target":
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute(
                "UPDATE discord_projection_links SET projection_table = NULL, projection_row_id = NULL "
                "WHERE source_event_id = ?",
                (source_event_id,),
            )
        elif mutation == "wrong_import":
            second = catalog.import_kakeraloot_state(
                KAKERALOOT_STATE, "Server", "Account", "second", "test"
            )
            second_id = connection.execute(
                "SELECT id FROM kakeraloot_state_observations WHERE import_event_id = ?",
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
                "UPDATE account_contexts SET normalized_name = 'other account' "
                "WHERE normalized_name = 'account'"
            )
        elif mutation == "slot":
            connection.execute(
                "UPDATE discord_projection_links SET projection_slot = ? WHERE source_event_id = ?",
                ('{"account":"other","server":"server"}', source_event_id),
            )

    before = _snapshot(database_path)
    with pytest.raises(
        (KakeralootStateProjectionIntegrityError, KakeralootStateProjectionTargetError)
    ):
        _coordinate(coordinator, source_event_id, None)
    assert _snapshot(database_path) == before


def test_succeeded_replay_rejects_missing_legacy_import_event(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE discord_source_events SET legacy_import_event_id = NULL WHERE id = ?",
            (source_event_id,),
        )

    with pytest.raises(KakeralootStateProjectionIntegrityError, match="no legacy import event"):
        _coordinate(coordinator, source_event_id, None)


def test_succeeded_replay_rejects_missing_legacy_import_row(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    with connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DELETE FROM import_events WHERE id = ?", (first.import_event_id,))

    with pytest.raises(KakeralootStateProjectionTargetError, match="legacy Kakeraloot-state import event"):
        _coordinate(coordinator, source_event_id, None)


def test_succeeded_replay_rejects_observation_from_another_context(tmp_path) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    first = _coordinate(coordinator, source_event_id, attempt_id)
    other = catalog.import_kakeraloot_state(
        KAKERALOOT_STATE, "Other Server", "Other Account", "other", "test"
    )
    with connect(database_path) as connection:
        other_id = connection.execute(
            "SELECT id FROM kakeraloot_state_observations WHERE import_event_id = ?",
            (other.import_event_id,),
        ).fetchone()[0]
        connection.execute(
            "UPDATE discord_projection_links SET projection_row_id = ? WHERE source_event_id = ?",
            (other_id, source_event_id),
        )

    with pytest.raises(KakeralootStateProjectionTargetError, match="another import event"):
        _coordinate(coordinator, source_event_id, None)
    assert first.kakeraloot_state_observation_id != other_id


@pytest.mark.parametrize("field", ("observed_at", "finished_at"))
def test_timestamps_must_be_timezone_aware(tmp_path, field: str) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    kwargs = {
        "source_event_id": source_event_id,
        "attempt_id": attempt_id,
        "state": KAKERALOOT_STATE,
        "server": "Server",
        "account": "Account",
        "raw": "raw",
        "source": "test",
        "observed_at": OBSERVED_AT,
        "finished_at": FINISHED_AT,
    }
    kwargs[field] = datetime(2026, 7, 30, 12, 0)

    with pytest.raises(ValueError, match=f"{field} must be timezone-aware"):
        coordinator.coordinate_kakeraloot_state(**kwargs)

    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 0
