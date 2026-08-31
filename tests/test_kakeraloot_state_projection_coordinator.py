import sqlite3
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import KakeralootStateSnapshot
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.repositories.projection_link_repository import ProjectionLinkRepository
from moa.services.kakeraloot_state_projection_coordinator import (
    KakeralootStateProjectionCoordinator,
    KakeralootStateProjectionDatabasePathError,
    KakeralootStateProjectionIntegrityError,
    KakeralootStateProjectionResult,
    KakeralootStateProjectionStateError,
    KakeralootStateProjectionTargetError,
)
from moa.services.projection_expectations import server_account_projection_slot


def _slot(server: str, account: str) -> str:
    return server_account_projection_slot(
        CatalogRepository._normalize(server), CatalogRepository._normalize(account)
    )


OBSERVED_AT = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
FINISHED_AT = datetime(2026, 7, 30, 12, 1, tzinfo=timezone.utc)
KAKERALOOT_VALUE_FIELDS = (
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
)
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


def _activate_test_projection_generation(
    connection: sqlite3.Connection, generation_id: int
) -> None:
    """Move a temporary test database to a later projection generation."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        assert ProjectionLinkRepository(connection).switch_current_generation() == generation_id
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()


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
        ) VALUES (?, 1, 'catalog.kakeraloot_state', ?,
                  'kakeraloot_state_observations', 987, 'completed', ?, ?, ?, ?)
        """,
        (source_event_id, _slot("Server", "Account"), value, value, value, value),
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


def _durable_snapshot(database_path) -> dict[str, tuple[tuple[object, ...], ...]]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "kakeraloot_state_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_processing_attempts",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
    )
    with connect(database_path) as connection:
        return {
            table: tuple(
                tuple(row)
                for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            )
            for table in tables
        }


def test_generation_one_processing_persists_atomic_kakeraloot_state_projection(
    tmp_path,
) -> None:
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
            "SELECT generation_id, projection_kind, projection_slot, projection_table, "
            "projection_row_id, "
            "state, completed_at FROM discord_projection_links"
        ).fetchone()
        assert tuple(link) == (
            1,
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


def test_generation_two_ignores_history_and_replay_is_durable_no_write(tmp_path) -> None:
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
    before_replay = _durable_snapshot(database_path)
    replay = _coordinate(coordinator, source_event_id, None)

    assert replay.replay_skipped is True
    assert replay.projection_target == first.projection_target
    assert _durable_snapshot(database_path) == before_replay
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
                "kakeraloot_state_observations",
                first.kakeraloot_state_observation_id,
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
    before = _durable_snapshot(database_path)

    with pytest.raises(
        KakeralootStateProjectionIntegrityError,
        match="inconsistent Kakeraloot-state projection link",
    ):
        _coordinate(coordinator, source_event_id, None)

    assert _durable_snapshot(database_path) == before


@pytest.mark.parametrize("mutation", ("wrong_import", "wrong_kind"))
def test_current_generation_replay_rejects_target_import_ownership_corruption(
    tmp_path, mutation: str
) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    with connect(database_path) as connection:
        _activate_test_projection_generation(connection, 2)
    first = _coordinate(coordinator, source_event_id, attempt_id)

    if mutation == "wrong_import":
        second = catalog.import_kakeraloot_state(
            KAKERALOOT_STATE, "Server", "Account", "second", "test"
        )
        with connect(database_path) as connection:
            second_id = connection.execute(
                "SELECT id FROM kakeraloot_state_observations WHERE import_event_id = ?",
                (second.import_event_id,),
            ).fetchone()[0]
            connection.execute(
                """
                UPDATE discord_projection_links SET projection_row_id = ?
                WHERE source_event_id = ? AND generation_id = 2
                """,
                (second_id, source_event_id),
            )
    else:
        with connect(database_path) as connection:
            connection.execute(
                "UPDATE import_events SET kind = 'profile' WHERE id = ?",
                (first.import_event_id,),
            )
    before = _durable_snapshot(database_path)

    with pytest.raises(KakeralootStateProjectionTargetError):
        _coordinate(coordinator, source_event_id, None)

    assert _durable_snapshot(database_path) == before


def test_generation_two_failure_rolls_back_new_writes_and_retains_history(
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
        "_validate_kakeraloot_state_target",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("forced rollback")),
    )

    with pytest.raises(RuntimeError, match="forced rollback"):
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
            "SELECT COUNT(*) FROM kakeraloot_state_observations"
        ).fetchone()[0] == 0
        assert tuple(
            connection.execute(
                "SELECT status, legacy_import_event_id FROM discord_source_events"
            ).fetchone()
        ) == ("processing", None)
        assert connection.execute(
            "SELECT status FROM discord_processing_attempts"
        ).fetchone()[0] == "processing"


def test_coordinator_uses_supplied_helper_without_public_wrapper_nesting(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    monkeypatch.setattr(
        catalog,
        "import_kakeraloot_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("coordinator called public Kakeraloot State wrapper")
        ),
    )

    result = _coordinate(coordinator, source_event_id, attempt_id)

    assert result.imported_count == 1
    assert result.replay_skipped is False
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM kakeraloot_state_observations"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT state FROM discord_projection_links WHERE source_event_id = ?",
            (source_event_id,),
        ).fetchone()[0] == "completed"


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
                   usage_count, kakera_balance, observed_at, import_event_id,
                   rolls_stacked_observed, disable_wa_ha_reduction_observed,
                   disable_wg_hg_reduction_observed, protected_wish_level_observed,
                   protected_wish_denominator_observed, mudapins_observed,
                   rt_cooldown_reduction_hours_observed, permanent_roll_bonus_observed,
                   star_branches_observed, starwish_slots_from_branches_observed,
                   quantity_level_observed, quality_level_observed,
                   usage_count_observed, kakera_balance_observed
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
            *(1 for _ in KAKERALOOT_VALUE_FIELDS),
        )


@pytest.mark.parametrize(
    ("state", "expected_has_kakeraloots", "expected_status_note", "expected_observed"),
    (
        (KAKERALOOT_STATE, 1, "guarded state", 1),
        (ZERO_KAKERALOOT_STATE, 1, "", 1),
        (NULL_KAKERALOOT_STATE, 1, None, 0),
        (
            KakeralootStateSnapshot(rolls_stacked=-1),
            1,
            None,
            None,
        ),
        (
            NO_KAKERALOOT_STATE,
            0,
            "No Kakeraloots bought; Mudae did not report loot statistics.",
            0,
        ),
    ),
)
def test_supported_kakeraloot_states_preserve_repository_semantics(
    tmp_path,
    state: KakeralootStateSnapshot,
    expected_has_kakeraloots: int,
    expected_status_note: str | None,
    expected_observed: int | None,
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
                   usage_count, kakera_balance,
                   rolls_stacked_observed, disable_wa_ha_reduction_observed,
                   disable_wg_hg_reduction_observed, protected_wish_level_observed,
                   protected_wish_denominator_observed, mudapins_observed,
                   rt_cooldown_reduction_hours_observed, permanent_roll_bonus_observed,
                   star_branches_observed, starwish_slots_from_branches_observed,
                   quantity_level_observed, quality_level_observed,
                   usage_count_observed, kakera_balance_observed
            FROM kakeraloot_state_observations
            WHERE id = ?
            """,
            (result.kakeraloot_state_observation_id,),
        ).fetchone()
        assert tuple(row) == (
            expected_has_kakeraloots,
            expected_status_note,
            *(
                0 if getattr(state, field_name) is None else getattr(state, field_name)
                for field_name in KAKERALOOT_VALUE_FIELDS
            ),
            *(
                expected_observed
                if expected_observed is not None
                else (0 if getattr(state, field_name) is None else 1)
                for field_name in KAKERALOOT_VALUE_FIELDS
            ),
        )


def test_kakeraloot_state_slot_is_deterministic_and_normalized(tmp_path) -> None:
    _database_path, _catalog, _discord, coordinator = _repositories(tmp_path)

    assert _slot("  Server   A ", " Account   A ") == (
        '{"account":"account a","server":"server a"}'
    )
    assert _slot("Server A", "Account A") == _slot(
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


@pytest.mark.parametrize("incoming", (None, 0))
def test_legacy_zero_replay_accepts_ambiguous_inputs_without_mutation(tmp_path, incoming) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id, state=ZERO_KAKERALOOT_STATE)
    with connect(database_path) as connection:
        connection.execute("UPDATE kakeraloot_state_observations SET rolls_stacked_observed = NULL")
    before = _snapshot(database_path)

    replay = _coordinate(
        coordinator,
        source_event_id,
        None,
        state=ZERO_KAKERALOOT_STATE.model_copy(update={"rolls_stacked": incoming}),
    )

    assert replay.replay_skipped is True
    assert _snapshot(database_path) == before


def test_legacy_zero_replay_rejects_nonzero_without_mutation(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id, state=ZERO_KAKERALOOT_STATE)
    with connect(database_path) as connection:
        connection.execute("UPDATE kakeraloot_state_observations SET rolls_stacked_observed = NULL")
    before = _snapshot(database_path)

    with pytest.raises(KakeralootStateProjectionTargetError, match="rolls_stacked"):
        _coordinate(
            coordinator,
            source_event_id,
            None,
            state=ZERO_KAKERALOOT_STATE.model_copy(update={"rolls_stacked": 7}),
        )
    assert _snapshot(database_path) == before


@pytest.mark.parametrize(
    ("stored_state", "incoming"),
    (
        (NULL_KAKERALOOT_STATE, 0),
        (ZERO_KAKERALOOT_STATE, None),
    ),
)
def test_new_row_replay_rejects_presence_mismatch(tmp_path, stored_state, incoming) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    _coordinate(coordinator, source_event_id, attempt_id, state=stored_state)
    before = _snapshot(database_path)

    with pytest.raises(KakeralootStateProjectionTargetError, match="rolls_stacked"):
        _coordinate(
            coordinator,
            source_event_id,
            None,
            state=stored_state.model_copy(update={"rolls_stacked": incoming}),
        )
    assert _snapshot(database_path) == before


def test_has_false_replay_still_validates_masked_field_presence(tmp_path) -> None:
    database_path, _catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_attribution(discord, source_event_id)
    state = KakeralootStateSnapshot(has_kakeraloots=False, rolls_stacked=0)
    _coordinate(coordinator, source_event_id, attempt_id, state=state)
    before = _snapshot(database_path)

    replay = _coordinate(coordinator, source_event_id, None, state=state)
    assert replay.replay_skipped is True
    assert _snapshot(database_path) == before
    with pytest.raises(KakeralootStateProjectionTargetError, match="rolls_stacked"):
        _coordinate(
            coordinator,
            source_event_id,
            None,
            state=state.model_copy(update={"rolls_stacked": None}),
        )
    assert _snapshot(database_path) == before
    with connect(database_path) as connection:
        connection.execute("UPDATE kakeraloot_state_observations SET rolls_stacked_observed = NULL")
    legacy_before = _snapshot(database_path)
    for incoming in (None, 0):
        replay = _coordinate(
            coordinator,
            source_event_id,
            None,
            state=state.model_copy(update={"rolls_stacked": incoming}),
        )
        assert replay.replay_skipped is True
        assert _snapshot(database_path) == legacy_before


@pytest.mark.parametrize(
    "field",
    (
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
    ),
)
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
                _slot("Server", "Account"),
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
