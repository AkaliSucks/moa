import json
import sqlite3
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import (
    AntidisablePage,
    BadgeLevel,
    ClaimConfirmation,
    DisableListEntry,
    DisableListSnapshot,
    KakeraStateSnapshot,
    KakeralootStateSnapshot,
    PlayerBonusMetric,
    PlayerBonusSnapshot,
    ProfileSnapshot,
    RollObservation,
    ServerSettingMetric,
    ServerSettingsSnapshot,
    KakeralootSettingsSnapshot,
    MudapinSnapshot,
    SphereGain,
    SphereResultSnapshot,
    TowerStateSnapshot,
    TimerStateSnapshot,
    WishlistEntry,
    WishlistSnapshot,
)
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.catalog_repository import (
    CatalogRepository,
    _AntidisablePageImportConnectionResult,
    _KakeralootStateImportConnectionResult,
    _DisableListImportConnectionResult,
    _PlayerBonusImportConnectionResult,
    _WishlistImportConnectionResult,
)
from moa.repositories.discord_message_repository import DiscordMessageRepository


ROLL = RollObservation(
    name="Transaction Character",
    series="Transaction Series",
    claim_rank=7,
    kakera_value=12,
    displayed_key_type="gold",
    displayed_key_count=3,
)
CLAIM = ClaimConfirmation(account_name="Account", character_name="Transaction Character")
PROFILE = ProfileSnapshot(
    profile_name="profile-account",
    collection_size=35,
    female_percent=100,
    male_percent=0,
    pokedex_count=2,
    pokedex_pokemon=("gulpin", "piloswine"),
    kakera_reacts={":kakeraY:": 497},
    mudapins_collected=None,
    mudapins_total=None,
    kakera_balance=812,
    bronze_keys=3,
    silver_keys=0,
    gold_keys=0,
    sphere_stock=None,
    spheres={":spP:": 2},
    displayed_badges=(":silvmudae:", ":DiamondI:"),
)
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
KAKERALOOT_SETTINGS = KakeralootSettingsSnapshot(
    loot_cost=500,
    quantity_quality_base_cost=2000,
    quantity_quality_level_increment=200,
)
TIMER_STATE = TimerStateSnapshot(
    can_claim_now=True,
    claim_reset_minutes=0,
    rolls_left=17,
    rolls_reset_minutes=42,
    rolls_reset_stock=0,
    vote_reset_minutes=None,
    daily_reset_minutes=613,
    daily_kakera_ready=False,
    rt_available=None,
    can_react_kakera_now=True,
    reaction_power_percent=72,
    kakera_button_power_cost_percent=36,
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
KAKERA_STATE = KakeraStateSnapshot(
    kakera_balance=7_673,
    badges=(
        BadgeLevel(badge_name="bronze", level=4, max_reached=True),
        BadgeLevel(badge_name="silver", level=3, max_reached=False),
        BadgeLevel(badge_name="gold", level=2, max_reached=True),
    ),
)
ZERO_KAKERA_STATE = KakeraStateSnapshot(kakera_balance=0, badges=())
PLAYER_BONUS = PlayerBonusSnapshot(
    metrics=(
        PlayerBonusMetric(label="Rolls per hour", detail="+9"),
        PlayerBonusMetric(label="Spawn bonus", detail="+210% ($k + $bw + slash)"),
    ),
    rolls_per_hour_bonus=9,
    wishlist_slot_bonus=8,
    wish_spawn_bonus_percent=210,
    starwish_spawn_bonus_percent=180,
    starwish_total_spawn_bonus_percent=390,
    starwish_slot_bonus=1,
    additional_wish_key_chance_percent=10,
    kakera_max_power_percent=25,
    kakera_button_power_cost_percent=12,
    starwish_kakera_button_bonus_percent=20,
    light_kakera_minimum=4,
    light_kakera_maximum=5,
)
BOUNDARY_PLAYER_BONUS = PlayerBonusSnapshot(
    metrics=(PlayerBonusMetric(label="", detail=""),),
    rolls_per_hour_bonus=0,
    wishlist_slot_bonus=None,
    wish_spawn_bonus_percent=-1,
    starwish_spawn_bonus_percent=0,
    starwish_total_spawn_bonus_percent=None,
    starwish_slot_bonus=0,
    additional_wish_key_chance_percent=None,
    kakera_max_power_percent=0,
    kakera_button_power_cost_percent=-2,
    starwish_kakera_button_bonus_percent=None,
    light_kakera_minimum=0,
    light_kakera_maximum=None,
)
TOWER_STATE = TowerStateSnapshot(
    current_level=2,
    completed_towers=3,
    next_level_cost=75_000,
    kakera_balance=7_673,
    built_perk_ids=(2, 7),
)
TOWER_STATE_WITHOUT_COMPLETED_TOWERS = TOWER_STATE.model_copy(update={"completed_towers": None})
MUDAPINS = MudapinSnapshot(pin_markers=(":pin139:", ":pin182:", ":logopin6:"))
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
ANTIDISABLE_PAGE = AntidisablePage(
    page_number=1,
    page_count=2,
    slots_used=0,
    slots_capacity=0,
    antidisabled_character_count=2_614,
    series_names=("Series B", "Series A", "Series B"),
)
ANTIDISABLE_CONTINUATION_PAGE = AntidisablePage(
    page_number=2,
    page_count=2,
    slots_used=7,
    slots_capacity=9,
    antidisabled_character_count=None,
    series_names=("Series C", "Series A"),
)
EMPTY_WISHLIST = WishlistSnapshot(
    wishlist_count=0,
    wishlist_capacity=0,
    starwish_count=0,
    starwish_capacity=0,
    entries=(),
)
DISABLELIST = DisableListSnapshot(
    slots_used=13,
    slots_capacity=16,
    total_disabled=107_529,
    disabled_wa=41_247,
    disabled_ha=42_438,
    disabled_wg=20_996,
    disabled_hg=14_789,
    wa_pool_limit=40_861,
    ha_pool_limit=42_213,
    western_disabled=True,
    irl_disabled=False,
    entries=(
        DisableListEntry(name="Kadokawa Corporation", disabled_count=13_207),
        DisableListEntry(name="Webcomics", disabled_count=11_073),
        DisableListEntry(name="Kadokawa Corporation", disabled_count=13_207),
    ),
)
BOUNDARY_DISABLELIST = DisableListSnapshot(
    slots_used=0,
    slots_capacity=0,
    total_disabled=0,
    disabled_wa=0,
    disabled_ha=0,
    disabled_wg=0,
    disabled_hg=0,
    wa_pool_limit=0,
    ha_pool_limit=None,
    western_disabled=False,
    irl_disabled=False,
    entries=(),
)
OBSERVED_AT = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
FINISHED_AT = datetime(2026, 7, 21, 12, 1, tzinfo=timezone.utc)


def _repositories(tmp_path):
    database_path = tmp_path / "transaction-seams.db"
    return database_path, CatalogRepository(database_path), DiscordMessageRepository(database_path)


def _receive_and_begin(repository: DiscordMessageRepository):
    aggregate_key = MessageAggregateKey(SourcePlatform.DISCORD, "guild", "channel", "message")
    received = repository.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(aggregate_key, "payload-hash", "revision"),
        event_key="event",
        event_kind="message_create",
        raw_text="raw",
        payload_json='{"content":"raw"}',
        payload_capture_version="capture-1",
        source_observed_at=OBSERVED_AT,
        received_at=OBSERVED_AT,
    )
    attempt = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OBSERVED_AT,
    )
    return received.source_event_id, attempt.attempt_id


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "characters",
        "server_contexts",
        "account_contexts",
        "claim_observations",
        "profile_observations",
        "roll_observations",
        "harem_key_observations",
        "rank_snapshots",
        "server_character_observations",
        "discord_projection_links",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _settings_counts(connection: sqlite3.Connection) -> dict[str, int]:
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


def _kakeraloot_settings_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "kakeraloot_settings_observations",
        "discord_projection_links",
        "discord_source_event_server_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _timer_state_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "timer_state_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _kakeraloot_state_counts(connection: sqlite3.Connection) -> dict[str, int]:
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
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _mudapins_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "mudapin_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _sphere_result_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "sphere_result_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _player_bonus_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "player_bonus_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _wishlist_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "wishlist_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _disablelist_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "disablelist_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _antidisable_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "harem_scans",
        "harem_scan_pages",
        "antidisable_series_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _kakera_state_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "kakera_state_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _tower_state_counts(connection: sqlite3.Connection) -> dict[str, int]:
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
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def test_public_kakera_import_preserves_compatibility_and_stored_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_kakera_state(
        KAKERA_STATE,
        "  Server  ",
        "  Account  ",
        "kakera payload",
        "discord",
    )

    assert set(result.model_dump()) == {"import_event_id", "server_name", "account_name", "observed_at"}
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.observed_at.tzinfo is not None
    assert result.observed_at.utcoffset().total_seconds() == 0
    with connect(database_path) as connection:
        assert _kakera_state_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "kakera_state_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT id, account_context_id, kakera_balance, badges_json, observed_at, import_event_id
            FROM kakera_state_observations
            WHERE import_event_id = ?
            """,
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == (
            "kakera_state",
            "discord",
            result.observed_at.isoformat(),
            "kakera payload",
        )
        assert observation["id"] > 0
        assert observation["account_context_id"] > 0
        assert observation["kakera_balance"] == 7_673
        assert json.loads(observation["badges_json"]) == [badge.model_dump() for badge in KAKERA_STATE.badges]
        assert observation["observed_at"] == result.observed_at.isoformat()
        assert observation["import_event_id"] == result.import_event_id


def test_kakera_helper_uses_supplied_connection_and_returns_actual_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_kakera_state_with_connection(
            connection,
            state=KAKERA_STATE,
            server=" Server ",
            account=" Account ",
            raw="raw kakera",
            source="clipboard",
            observed_at=OBSERVED_AT,
        )
        assert is_dataclass(imported)
        assert [field.name for field in fields(imported)] == [
            "import_event_id",
            "kakera_state_observation_id",
        ]
        assert imported.import_event_id > 0
        assert imported.kakera_state_observation_id > 0
        assert connection.in_transaction is True
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM kakera_state_observations").fetchone()[0] == 1
        observation = connection.execute(
            """
            SELECT kakera_state_observations.*, account_contexts.normalized_name AS account_name
            FROM kakera_state_observations
            JOIN account_contexts ON account_contexts.id = kakera_state_observations.account_context_id
            WHERE kakera_state_observations.id = ?
            """,
            (imported.kakera_state_observation_id,),
        ).fetchone()
        assert observation["account_name"] == "account"
        assert observation["kakera_balance"] == 7_673
        assert json.loads(observation["badges_json"]) == [badge.model_dump() for badge in KAKERA_STATE.badges]
        assert observation["observed_at"] == OBSERVED_AT.isoformat()
        assert observation["import_event_id"] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM kakera_state_observations").fetchone()[0] == 0
        connection.commit()

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT id FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()[0] == imported.import_event_id
        assert connection.execute(
            "SELECT id FROM kakera_state_observations WHERE id = ?",
            (imported.kakera_state_observation_id,),
        ).fetchone()[0] == imported.kakera_state_observation_id


def test_kakera_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_kakera_state_with_connection(
            connection,
            state=ZERO_KAKERA_STATE,
            server="Server",
            account="Account",
            raw="rollback kakera",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        connection.rollback()

    with connect(database_path) as connection:
        assert _kakera_state_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "kakera_state_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_kakera_helper_reuses_existing_contexts_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_kakera_state(ZERO_KAKERA_STATE, "Server", "Account", "existing", "discord")

    with connect(database_path) as connection:
        before = connection.execute(
            """
            SELECT server_contexts.id AS server_id, server_contexts.name AS server_name,
                   account_contexts.id AS account_id, account_contexts.name AS account_name
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        before_counts = _kakera_state_counts(connection)

    with connect(database_path) as connection:
        imported = catalog._import_kakera_state_with_connection(
            connection,
            state=KAKERA_STATE,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="reused contexts",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, server_contexts.name AS server_name,
                   account_contexts.id AS account_id, account_contexts.name AS account_name
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert (current["server_id"], current["account_id"]) == (before["server_id"], before["account_id"])
        assert (current["server_name"], current["account_name"]) == ("SERVER", "ACCOUNT")
        assert _kakera_state_counts(connection)["server_contexts"] == 1
        assert _kakera_state_counts(connection)["account_contexts"] == 1
        assert imported.import_event_id > 0
        assert imported.kakera_state_observation_id > 0
        connection.rollback()

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, server_contexts.name AS server_name,
                   account_contexts.id AS account_id, account_contexts.name AS account_name
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(before)
        assert _kakera_state_counts(connection) == before_counts


def test_public_tower_import_preserves_compatibility_and_stored_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_tower_state(
        TOWER_STATE,
        "  Server  ",
        "  Account  ",
        "tower payload",
        "discord",
    )

    assert set(result.model_dump()) == {"import_event_id", "server_name", "account_name", "observed_at"}
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    with connect(database_path) as connection:
        assert _tower_state_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "tower_state_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT id, account_context_id, current_level, completed_towers, next_level_cost,
                   kakera_balance, built_perk_ids_json, observed_at, import_event_id
            FROM tower_state_observations
            WHERE import_event_id = ?
            """,
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("tower_state", "discord", result.observed_at.isoformat(), "tower payload")
        assert observation["id"] > 0
        assert observation["account_context_id"] > 0
        assert observation["current_level"] == TOWER_STATE.current_level
        assert observation["completed_towers"] == TOWER_STATE.completed_towers
        assert observation["next_level_cost"] == TOWER_STATE.next_level_cost
        assert observation["kakera_balance"] == TOWER_STATE.kakera_balance
        assert json.loads(observation["built_perk_ids_json"]) == list(TOWER_STATE.built_perk_ids)
        assert observation["observed_at"] == result.observed_at.isoformat()
        assert observation["import_event_id"] == result.import_event_id


def test_tower_helper_uses_supplied_connection_returns_actual_ids_and_commits(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_tower_state_with_connection(
            connection,
            state=TOWER_STATE,
            server=" Server ",
            account=" Account ",
            raw="raw tower",
            source="clipboard",
            observed_at=OBSERVED_AT,
        )
        assert is_dataclass(imported)
        assert [field.name for field in fields(imported)] == [
            "import_event_id",
            "tower_state_observation_id",
        ]
        assert imported.import_event_id > 0
        assert imported.tower_state_observation_id > 0
        assert connection.in_transaction is True
        assert connection.execute(
            "SELECT id FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()[0] == imported.import_event_id
        assert connection.execute(
            "SELECT id FROM tower_state_observations WHERE id = ?",
            (imported.tower_state_observation_id,),
        ).fetchone()[0] == imported.tower_state_observation_id
        assert _tower_state_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "tower_state_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        observation = connection.execute(
            "SELECT observed_at, import_event_id FROM tower_state_observations WHERE id = ?",
            (imported.tower_state_observation_id,),
        ).fetchone()
        assert observation["observed_at"] == OBSERVED_AT.isoformat()
        assert observation["import_event_id"] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM tower_state_observations").fetchone()[0] == 0
        connection.commit()

    with connect(database_path) as connection:
        assert _tower_state_counts(connection)["tower_state_observations"] == 1


def test_tower_helper_rollback_removes_new_rows_and_contexts(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_tower_state_with_connection(
            connection,
            state=TOWER_STATE,
            server="Server",
            account="Account",
            raw="rollback tower",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.rollback()

    with connect(database_path) as connection:
        assert _tower_state_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "tower_state_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_tower_helper_reuses_existing_contexts_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_tower_state(TOWER_STATE, "Server", "Account", "existing", "discord")

    with connect(database_path) as connection:
        before = connection.execute(
            """
            SELECT server_contexts.id AS server_id, server_contexts.name AS server_name,
                   account_contexts.id AS account_id, account_contexts.name AS account_name
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        before_counts = _tower_state_counts(connection)

    with connect(database_path) as connection:
        catalog._import_tower_state_with_connection(
            connection,
            state=TOWER_STATE,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="reused contexts",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, server_contexts.name AS server_name,
                   account_contexts.id AS account_id, account_contexts.name AS account_name
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert (current["server_id"], current["account_id"]) == (before["server_id"], before["account_id"])
        assert (current["server_name"], current["account_name"]) == ("SERVER", "ACCOUNT")
        assert _tower_state_counts(connection)["server_contexts"] == 1
        assert _tower_state_counts(connection)["account_contexts"] == 1
        connection.rollback()

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, server_contexts.name AS server_name,
                   account_contexts.id AS account_id, account_contexts.name AS account_name
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(before)
        assert _tower_state_counts(connection) == before_counts


@pytest.mark.parametrize(
    ("state", "expected_completed_towers"),
    [
        (TOWER_STATE, 3),
        (TOWER_STATE_WITHOUT_COMPLETED_TOWERS, 0),
    ],
)
def test_tower_helper_preserves_completed_tower_values(tmp_path, state, expected_completed_towers) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_tower_state_with_connection(
            connection,
            state=state,
            server="Server",
            account=f"Account {expected_completed_towers}",
            raw="completed towers",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.execute(
            "SELECT completed_towers FROM tower_state_observations WHERE id = ?",
            (imported.tower_state_observation_id,),
        ).fetchone()[0] == expected_completed_towers


def test_roll_helper_writes_all_projections_on_supplied_connection(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        result = catalog._import_roll_with_connection(
            connection,
            roll=ROLL,
            server="Server",
            account="Account",
            raw="roll payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert result.import_event_id > 0
        assert result.character_id > 0
        assert result.roll_observation_id > 0
        assert result.harem_key_observation_id > 0
        assert result.rank_snapshot_id > 0
        assert result.server_character_observation_id > 0

    with connect(database_path) as connection:
        counts = _counts(connection)
        assert counts == {
            "import_events": 1,
            "characters": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "claim_observations": 0,
            "profile_observations": 0,
            "roll_observations": 1,
            "harem_key_observations": 1,
            "rank_snapshots": 1,
            "server_character_observations": 1,
            "discord_projection_links": 0,
        }


def test_public_roll_wrapper_keeps_public_result_and_transaction_behavior(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_roll(ROLL, " Server ", " Account ", "roll payload", "discord")

    assert result.import_event_id > 0
    assert result.character_id > 0
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 1


def test_public_claim_wrapper_keeps_result_and_writes_expected_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_claim(CLAIM, " Server ", " Account ", "claim payload", "discord")

    assert result.import_event_id > 0
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.character_name == "Transaction Character"
    assert result.character_id is None
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 1
        assert _counts(connection)["server_contexts"] == 1
        assert _counts(connection)["account_contexts"] == 1
        assert _counts(connection)["claim_observations"] == 1
        assert _counts(connection)["discord_projection_links"] == 0
        event = connection.execute(
            "SELECT kind, source, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("claim", "discord", "claim payload")
        observation = connection.execute(
            """
            SELECT id, character_id, character_name, normalized_character_name, import_event_id
            FROM claim_observations
            """
        ).fetchone()
        assert tuple(observation) == (
            observation["id"],
            None,
            "Transaction Character",
            "transaction character",
            result.import_event_id,
        )


def test_claim_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_claim_with_connection(
            connection,
            claim=CLAIM,
            server="Server",
            account="Account",
            raw="claim payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM claim_observations").fetchone()[0] == 1
        assert connection.execute(
            "SELECT import_event_id FROM claim_observations WHERE id = ?",
            (imported.claim_observation_id,),
        ).fetchone()[0] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM claim_observations").fetchone()[0] == 0


def test_claim_helper_commit_persists_all_rows_and_result_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_claim_with_connection(
            connection,
            claim=CLAIM,
            server="Server",
            account="Account",
            raw="claim payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT id FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()[0] == imported.import_event_id
        assert connection.execute(
            "SELECT id FROM claim_observations WHERE id = ?", (imported.claim_observation_id,)
        ).fetchone()[0] == imported.claim_observation_id
        assert connection.execute(
            "SELECT import_event_id FROM claim_observations WHERE id = ?",
            (imported.claim_observation_id,),
        ).fetchone()[0] == imported.import_event_id
        assert _counts(connection)["claim_observations"] == 1
        assert _counts(connection)["discord_projection_links"] == 0


def test_claim_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_claim_with_connection(
                connection,
                claim=CLAIM,
                server="Server",
                account="Account",
                raw="claim payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 0
        assert _counts(connection)["claim_observations"] == 0
        assert _counts(connection)["server_contexts"] == 0
        assert _counts(connection)["account_contexts"] == 0
        assert _counts(connection)["characters"] == 0
        assert _counts(connection)["discord_projection_links"] == 0


def test_claim_helper_reuses_canonical_rows_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    roll_result = catalog.import_roll(ROLL, "Server", "Account", "roll payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id,
                   characters.id AS character_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            JOIN characters ON characters.normalized_name = ?
            """,
            ("transaction character",),
        ).fetchone()
        before_counts = _counts(connection)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            imported = catalog._import_claim_with_connection(
                connection,
                claim=CLAIM,
                server=" SERVER ",
                account=" ACCOUNT ",
                raw="claim payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            assert imported.character_id == roll_result.character_id
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id,
                   characters.id AS character_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            JOIN characters ON characters.normalized_name = ?
            """,
            ("transaction character",),
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _counts(connection) == before_counts


def test_public_profile_wrapper_keeps_result_and_writes_expected_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_profile(PROFILE, " Server ", " Account ", "profile payload", "discord")

    assert result.import_event_id > 0
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 1,
            "characters": 0,
            "server_contexts": 1,
            "account_contexts": 1,
            "claim_observations": 0,
            "profile_observations": 1,
            "roll_observations": 0,
            "harem_key_observations": 0,
            "rank_snapshots": 0,
            "server_character_observations": 0,
            "discord_projection_links": 0,
        }
        event = connection.execute(
            "SELECT kind, source, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("profile", "discord", "profile payload")
        row = connection.execute(
            """
            SELECT id, profile_name, collection_size, pokedex_json, kakera_reacts_json,
                   displayed_badges_json, import_event_id
            FROM profile_observations
            """
        ).fetchone()
        assert row["id"] > 0
        assert (row["profile_name"], row["collection_size"], row["import_event_id"]) == (
            "profile-account",
            35,
            result.import_event_id,
        )
        assert row["pokedex_json"] == '["gulpin", "piloswine"]'
        assert row["kakera_reacts_json"] == '{":kakeraY:": 497}'
        assert row["displayed_badges_json"] == '[":silvmudae:", ":DiamondI:"]'


def test_public_server_settings_wrapper_preserves_result_rows_and_metrics(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_server_settings(SETTINGS, " Server ", "settings payload", "discord")

    assert result.import_event_id > 0
    assert result.server_name == "Server"
    with connect(database_path) as connection:
        assert _settings_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "server_settings_observations": 1,
            "discord_projection_links": 0,
            "discord_source_event_server_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("server_settings", "discord", "settings payload")
        observation = connection.execute(
            """
            SELECT id, server_premium, prefix, language, claim_reset_minutes, reset_minute,
                   reset_shift_minutes, rolls_per_hour, claim_reaction_expiry_seconds,
                   claimed_character_rarity_multiplier, kakera_bonus_percent, sphere_bonus_percent,
                   game_mode, channel_instance, metrics_json, import_event_id
            FROM server_settings_observations
            """
        ).fetchone()
        assert observation["id"] > 0
        assert tuple(observation)[1:14] == (
            0,
            "$",
            "English",
            60,
            "00",
            0,
            10,
            30,
            2,
            15,
            5,
            1,
            2,
        )
        assert observation["metrics_json"] == '[{"label": "Prefix", "value": "$"}, {"label": "Lang", "value": "English"}]'
        assert observation["import_event_id"] == result.import_event_id


def test_server_settings_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_server_settings_with_connection(
            connection,
            settings=SETTINGS,
            server="Server",
            raw="settings payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM server_settings_observations").fetchone()[0] == 1
        assert connection.execute(
            "SELECT import_event_id FROM server_settings_observations WHERE id = ?",
            (imported.server_settings_observation_id,),
        ).fetchone()[0] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM server_settings_observations").fetchone()[0] == 0


def test_server_settings_helper_commit_persists_rows_and_result_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_server_settings_with_connection(
            connection,
            settings=SETTINGS,
            server="Server",
            raw="settings payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT id FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()[0] == imported.import_event_id
        assert connection.execute(
            "SELECT id FROM server_settings_observations WHERE id = ?",
            (imported.server_settings_observation_id,),
        ).fetchone()[0] == imported.server_settings_observation_id
        assert _settings_counts(connection)["server_settings_observations"] == 1


def test_server_settings_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_server_settings_with_connection(
                connection,
                settings=SETTINGS,
                server="Server",
                raw="settings payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        assert _settings_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "server_settings_observations": 0,
            "discord_projection_links": 0,
            "discord_source_event_server_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_server_settings_helper_reuses_server_and_rollback_preserves_it(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_server_settings(SETTINGS, "Server", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            "SELECT id, name, normalized_name FROM server_contexts"
        ).fetchone()
        before_counts = _settings_counts(connection)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_server_settings_with_connection(
                connection,
                settings=SETTINGS,
                server=" SERVER ",
                raw="second payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        current = connection.execute(
            "SELECT id, name, normalized_name FROM server_contexts"
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _settings_counts(connection) == before_counts


def test_server_settings_helper_does_not_write_projection_or_attribution_state(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        before = _settings_counts(connection)
        catalog._import_server_settings_with_connection(
            connection,
            settings=SETTINGS,
            server="Server",
            raw="settings payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        after = _settings_counts(connection)

    assert after["import_events"] == before["import_events"] + 1
    assert after["server_contexts"] == before["server_contexts"] + 1
    assert after["server_settings_observations"] == before["server_settings_observations"] + 1
    assert after["discord_projection_links"] == before["discord_projection_links"]
    assert after["discord_source_event_server_attributions"] == before["discord_source_event_server_attributions"]
    assert after["discord_processing_attempts"] == before["discord_processing_attempts"]


def test_public_kakeraloot_settings_wrapper_preserves_result_rows_and_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_kakeraloot_settings(
        KAKERALOOT_SETTINGS, " Server ", "infokl payload", "discord"
    )

    assert result.import_event_id > 0
    assert result.server_name == "Server"
    settings = catalog.kakeraloot_settings("Server")
    assert settings is not None
    assert (settings.loot_cost, settings.quantity_quality_base_cost, settings.quantity_quality_level_increment) == (
        500,
        2000,
        200,
    )
    with connect(database_path) as connection:
        assert _kakeraloot_settings_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "kakeraloot_settings_observations": 1,
            "discord_projection_links": 0,
            "discord_source_event_server_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("kakeraloot_settings", "discord", "infokl payload")
        observation = connection.execute(
            """
            SELECT id, server_context_id, loot_cost, quantity_quality_base_cost,
                   quantity_quality_level_increment, observed_at, import_event_id
            FROM kakeraloot_settings_observations
            """
        ).fetchone()
        assert observation["id"] > 0
        assert observation["server_context_id"] == connection.execute(
            "SELECT id FROM server_contexts WHERE normalized_name = ?",
            ("server",),
        ).fetchone()[0]
        assert tuple(observation)[2:] == (
            500,
            2000,
            200,
            result.observed_at.isoformat(),
            result.import_event_id,
        )


def test_kakeraloot_settings_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_kakeraloot_settings_with_connection(
            connection,
            settings=KAKERALOOT_SETTINGS,
            server="Server",
            raw="infokl payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM kakeraloot_settings_observations").fetchone()[0] == 1
        assert connection.execute(
            "SELECT import_event_id FROM kakeraloot_settings_observations WHERE id = ?",
            (imported.kakeraloot_settings_observation_id,),
        ).fetchone()[0] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM kakeraloot_settings_observations").fetchone()[0] == 0


def test_kakeraloot_settings_helper_commit_persists_rows_and_result_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_kakeraloot_settings_with_connection(
            connection,
            settings=KAKERALOOT_SETTINGS,
            server="Server",
            raw="infokl payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT id FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()[0] == imported.import_event_id
        assert connection.execute(
            "SELECT id FROM kakeraloot_settings_observations WHERE id = ?",
            (imported.kakeraloot_settings_observation_id,),
        ).fetchone()[0] == imported.kakeraloot_settings_observation_id
        row = connection.execute(
            """
            SELECT loot_cost, quantity_quality_base_cost, quantity_quality_level_increment,
                   import_event_id
            FROM kakeraloot_settings_observations
            WHERE id = ?
            """,
            (imported.kakeraloot_settings_observation_id,),
        ).fetchone()
        assert tuple(row) == (500, 2000, 200, imported.import_event_id)


def test_kakeraloot_settings_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_kakeraloot_settings_with_connection(
                connection,
                settings=KAKERALOOT_SETTINGS,
                server="Server",
                raw="infokl payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        assert _kakeraloot_settings_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "kakeraloot_settings_observations": 0,
            "discord_projection_links": 0,
            "discord_source_event_server_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_kakeraloot_settings_helper_reuses_server_and_rollback_preserves_it(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_kakeraloot_settings(KAKERALOOT_SETTINGS, "Server", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            "SELECT id, name, normalized_name FROM server_contexts"
        ).fetchone()
        before_counts = _kakeraloot_settings_counts(connection)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_kakeraloot_settings_with_connection(
                connection,
                settings=KAKERALOOT_SETTINGS,
                server=" SERVER ",
                raw="second payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        current = connection.execute(
            "SELECT id, name, normalized_name FROM server_contexts"
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _kakeraloot_settings_counts(connection) == before_counts


def test_kakeraloot_settings_helper_does_not_write_projection_or_discord_state(tmp_path) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    with connect(database_path) as connection:
        before_event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()
        before_attempt = connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()[0]
        before_counts = _kakeraloot_settings_counts(connection)
        catalog._import_kakeraloot_settings_with_connection(
            connection,
            settings=KAKERALOOT_SETTINGS,
            server="Server",
            raw="infokl payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        after_counts = _kakeraloot_settings_counts(connection)
        after_event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()
        after_attempt = connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()[0]

    assert after_counts["import_events"] == before_counts["import_events"] + 1
    assert after_counts["server_contexts"] == before_counts["server_contexts"] + 1
    assert after_counts["kakeraloot_settings_observations"] == before_counts["kakeraloot_settings_observations"] + 1
    assert after_counts["discord_projection_links"] == before_counts["discord_projection_links"]
    assert after_counts["discord_source_event_server_attributions"] == before_counts[
        "discord_source_event_server_attributions"
    ]
    assert after_counts["discord_processing_attempts"] == before_counts["discord_processing_attempts"]
    assert tuple(after_event) == tuple(before_event)
    assert after_attempt == before_attempt


def test_profile_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_profile_with_connection(
            connection,
            profile=PROFILE,
            server="Server",
            account="Account",
            raw="profile payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM profile_observations").fetchone()[0] == 1
        assert connection.execute(
            "SELECT import_event_id FROM profile_observations WHERE id = ?",
            (imported.profile_observation_id,),
        ).fetchone()[0] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM profile_observations").fetchone()[0] == 0


def test_profile_helper_commit_persists_all_rows_and_result_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_profile_with_connection(
            connection,
            profile=PROFILE,
            server="Server",
            account="Account",
            raw="profile payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT id FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()[0] == imported.import_event_id
        assert connection.execute(
            "SELECT id FROM profile_observations WHERE id = ?", (imported.profile_observation_id,)
        ).fetchone()[0] == imported.profile_observation_id
        assert _counts(connection)["profile_observations"] == 1
        assert _counts(connection)["discord_projection_links"] == 0


def test_profile_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_profile_with_connection(
                connection,
                profile=PROFILE,
                server="Server",
                account="Account",
                raw="profile payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 0,
            "characters": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "claim_observations": 0,
            "profile_observations": 0,
            "roll_observations": 0,
            "harem_key_observations": 0,
            "rank_snapshots": 0,
            "server_character_observations": 0,
            "discord_projection_links": 0,
        }


def test_profile_helper_reuses_canonical_rows_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_profile(PROFILE, "Server", "Account", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_profile_with_connection(
                connection,
                profile=PROFILE,
                server=" SERVER ",
                account=" ACCOUNT ",
                raw="second payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _counts(connection)["import_events"] == 1
        assert _counts(connection)["profile_observations"] == 1


def test_processing_success_helper_updates_supplied_connection(tmp_path) -> None:
    database_path, _catalog, discord = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    with connect(database_path) as connection:
        import_event_id = int(
            connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("roll", "discord", OBSERVED_AT.isoformat(), "roll payload"),
            ).lastrowid
        )
        result = discord._mark_processing_success_with_connection(
            connection,
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            finished_at=FINISHED_AT,
            legacy_import_event_id=import_event_id,
        )
        assert connection.in_transaction is True
        assert result.attempt_status == "succeeded"
        assert result.source_event_status == "succeeded"
        assert result.legacy_import_event_id == import_event_id
        assert connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()[0] == "succeeded"


def test_one_caller_transaction_commits_roll_and_success_atomically(tmp_path) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    with connect(database_path) as connection:
        roll_result = catalog._import_roll_with_connection(
            connection,
            roll=ROLL,
            server="Server",
            account="Account",
            raw="roll payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        success = discord._mark_processing_success_with_connection(
            connection,
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            finished_at=FINISHED_AT,
            legacy_import_event_id=roll_result.import_event_id,
        )
        assert success.attempt_status == "succeeded"

    with connect(database_path) as connection:
        counts = _counts(connection)
        assert counts["import_events"] == 1
        assert counts["roll_observations"] == 1
        assert counts["discord_projection_links"] == 0
        event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events"
        ).fetchone()
        assert (event["status"], event["legacy_import_event_id"]) == (
            "succeeded",
            roll_result.import_event_id,
        )
        assert connection.execute("SELECT status FROM discord_processing_attempts").fetchone()[0] == "succeeded"


def test_failure_after_roll_helper_rolls_back_catalog_and_canonical_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_roll_with_connection(
                connection,
                roll=ROLL,
                server="Server",
                account="Account",
                raw="roll payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 0,
            "characters": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "claim_observations": 0,
            "profile_observations": 0,
            "roll_observations": 0,
            "harem_key_observations": 0,
            "rank_snapshots": 0,
            "server_character_observations": 0,
            "discord_projection_links": 0,
        }


def test_failure_after_both_helpers_rolls_back_catalog_and_processing_success(tmp_path) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            roll_result = catalog._import_roll_with_connection(
                connection,
                roll=ROLL,
                server="Server",
                account="Account",
                raw="roll payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            discord._mark_processing_success_with_connection(
                connection,
                source_event_id=source_event_id,
                attempt_id=attempt_id,
                finished_at=FINISHED_AT,
                legacy_import_event_id=roll_result.import_event_id,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        counts = _counts(connection)
        assert counts["import_events"] == 0
        assert counts["characters"] == 0
        assert counts["roll_observations"] == 0
        assert counts["discord_projection_links"] == 0
        event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events"
        ).fetchone()
        assert (event["status"], event["legacy_import_event_id"]) == ("processing", None)
        assert connection.execute("SELECT status FROM discord_processing_attempts").fetchone()[0] == "processing"


def test_public_timer_state_wrapper_preserves_result_rows_and_snapshot(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    raw_message = "timer payload with exact source text"

    result = catalog.import_timer_state(
        TIMER_STATE,
        " Server ",
        " Account ",
        raw_message,
        "discord",
    )

    assert set(result.model_dump()) == {"import_event_id", "server_name", "account_name", "observed_at"}
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.observed_at.tzinfo is not None
    with connect(database_path) as connection:
        assert _timer_state_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "timer_state_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("timer_state", "discord", raw_message, result.observed_at.isoformat())
        observation = connection.execute(
            """
            SELECT id, account_context_id, snapshot_json, observed_at, import_event_id
            FROM timer_state_observations
            WHERE id = (SELECT MAX(id) FROM timer_state_observations)
            """
        ).fetchone()
        account = connection.execute(
            "SELECT id, name, normalized_name FROM account_contexts"
        ).fetchone()
        server = connection.execute(
            "SELECT name, normalized_name FROM server_contexts"
        ).fetchone()
        assert tuple(account) == (account["id"], "Account", "account")
        assert tuple(server) == ("Server", "server")
        assert observation["id"] > 0
        assert observation["account_context_id"] == account["id"]
        assert json.loads(observation["snapshot_json"]) == TIMER_STATE.model_dump()
        assert observation["observed_at"] == result.observed_at.isoformat()
        assert observation["import_event_id"] == result.import_event_id


def test_timer_state_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        imported = catalog._import_timer_state_with_connection(
            connection,
            state=TIMER_STATE,
            server="Server",
            account="Account",
            raw="timer payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert _timer_state_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "timer_state_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        observation = connection.execute(
            "SELECT import_event_id FROM timer_state_observations WHERE id = ?",
            (imported.timer_state_observation_id,),
        ).fetchone()
        assert observation["import_event_id"] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM timer_state_observations").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0


def test_timer_state_helper_commit_persists_rows_and_returned_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_timer_state_with_connection(
            connection,
            state=TIMER_STATE,
            server="Server",
            account="Account",
            raw="timer payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT id, kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (imported.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            "SELECT id, observed_at, import_event_id, account_context_id FROM timer_state_observations WHERE id = ?",
            (imported.timer_state_observation_id,),
        ).fetchone()
        account = connection.execute(
            "SELECT id FROM account_contexts WHERE normalized_name = ?",
            ("account",),
        ).fetchone()
        assert event["id"] == imported.import_event_id
        assert tuple(event)[1:] == ("timer_state", "discord", "timer payload", OBSERVED_AT.isoformat())
        assert observation["id"] == imported.timer_state_observation_id
        assert observation["observed_at"] == OBSERVED_AT.isoformat()
        assert observation["import_event_id"] == imported.import_event_id
        assert observation["account_context_id"] == account["id"]


def test_timer_state_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_timer_state_with_connection(
            connection,
            state=TIMER_STATE,
            server="Server",
            account="Account",
            raw="timer payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.rollback()

    with connect(database_path) as connection:
        assert _timer_state_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "timer_state_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_timer_state_helper_reuses_contexts_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_timer_state(TIMER_STATE, "Server", "Account", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        before_counts = _timer_state_counts(connection)

    with connect(database_path) as connection:
        imported = catalog._import_timer_state_with_connection(
            connection,
            state=TIMER_STATE,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="second payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert imported.import_event_id > 0
        assert imported.timer_state_observation_id > 0
        connection.rollback()

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _timer_state_counts(connection) == before_counts


def test_kakeraloot_state_connection_result_is_frozen_slotted_with_exact_fields() -> None:
    assert is_dataclass(_KakeralootStateImportConnectionResult)
    assert _KakeralootStateImportConnectionResult.__dataclass_params__.frozen is True
    assert [field.name for field in fields(_KakeralootStateImportConnectionResult)] == [
        "import_event_id",
        "kakeraloot_state_observation_id",
    ]
    assert not hasattr(_KakeralootStateImportConnectionResult(1, 2), "__dict__")


def test_public_kakeraloot_state_wrapper_preserves_result_and_stored_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    raw_message = "kakeraloot state payload with exact source text"

    result = catalog.import_kakeraloot_state(
        KAKERALOOT_STATE,
        "  Server  ",
        "  Account  ",
        raw_message,
        "discord",
    )

    assert result.import_event_id > 0
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.observed_at.tzinfo is not None
    observation = catalog.kakeraloot_state("Server", "Account")
    assert observation is not None
    assert observation.server_name == "Server"
    assert observation.account_name == "Account"
    assert observation.has_kakeraloots is True
    assert observation.status_note == KAKERALOOT_STATE.status_note
    for field in (
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
    ):
        assert getattr(observation, field) == getattr(KAKERALOOT_STATE, field)
    assert observation.observed_at == result.observed_at

    with connect(database_path) as connection:
        assert _kakeraloot_state_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "kakeraloot_state_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == (
            "kakeraloot_state",
            "discord",
            raw_message,
            result.observed_at.isoformat(),
        )
        stored = connection.execute(
            """
            SELECT has_kakeraloots, status_note, rolls_stacked, disable_wa_ha_reduction,
                   disable_wg_hg_reduction, protected_wish_level, protected_wish_denominator,
                   mudapins, rt_cooldown_reduction_hours, permanent_roll_bonus,
                   star_branches, starwish_slots_from_branches, quantity_level, quality_level,
                   usage_count, kakera_balance, observed_at, import_event_id
            FROM kakeraloot_state_observations
            """
        ).fetchone()
        assert tuple(stored) == (
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
            result.observed_at.isoformat(),
            result.import_event_id,
        )


def test_kakeraloot_state_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        imported = catalog._import_kakeraloot_state_with_connection(
            connection,
            state=KAKERALOOT_STATE,
            server="Server",
            account="Account",
            raw="kakeraloot payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert imported.import_event_id > 0
        assert imported.kakeraloot_state_observation_id > 0
        assert _kakeraloot_state_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "kakeraloot_state_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        observation = connection.execute(
            "SELECT import_event_id FROM kakeraloot_state_observations WHERE id = ?",
            (imported.kakeraloot_state_observation_id,),
        ).fetchone()
        assert observation["import_event_id"] == imported.import_event_id
        with connect(database_path) as observer:
            assert _kakeraloot_state_counts(observer) == {
                "import_events": 0,
                "server_contexts": 0,
                "account_contexts": 0,
                "kakeraloot_state_observations": 0,
                "discord_projection_links": 0,
                "discord_source_events": 0,
                "discord_source_event_server_attributions": 0,
                "discord_source_event_account_attributions": 0,
                "discord_processing_attempts": 0,
            }


def test_kakeraloot_state_helper_commit_persists_rows_and_returned_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_kakeraloot_state_with_connection(
            connection,
            state=KAKERALOOT_STATE,
            server="Server",
            account="Account",
            raw="kakeraloot payload",
            source="clipboard",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT id, kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (imported.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT id, account_context_id, observed_at, import_event_id
            FROM kakeraloot_state_observations
            WHERE id = ?
            """,
            (imported.kakeraloot_state_observation_id,),
        ).fetchone()
        assert event["id"] == imported.import_event_id
        assert tuple(event)[1:] == (
            "kakeraloot_state",
            "clipboard",
            "kakeraloot payload",
            OBSERVED_AT.isoformat(),
        )
        assert observation["id"] == imported.kakeraloot_state_observation_id
        assert observation["account_context_id"] == connection.execute(
            "SELECT id FROM account_contexts WHERE normalized_name = ?",
            ("account",),
        ).fetchone()[0]
        assert observation["observed_at"] == OBSERVED_AT.isoformat()
        assert observation["import_event_id"] == imported.import_event_id


def test_kakeraloot_state_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_kakeraloot_state_with_connection(
            connection,
            state=KAKERALOOT_STATE,
            server="Server",
            account="Account",
            raw="kakeraloot payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.rollback()

    with connect(database_path) as connection:
        assert _kakeraloot_state_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "kakeraloot_state_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_kakeraloot_state_helper_reuses_contexts_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_kakeraloot_state(KAKERALOOT_STATE, "Server", "Account", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        before_counts = _kakeraloot_state_counts(connection)

    with connect(database_path) as connection:
        imported = catalog._import_kakeraloot_state_with_connection(
            connection,
            state=KAKERALOOT_STATE,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="second payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert imported.import_event_id > 0
        assert imported.kakeraloot_state_observation_id > 0
        connection.rollback()

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _kakeraloot_state_counts(connection) == before_counts


@pytest.mark.parametrize(
    ("state", "expected_has_kakeraloots", "expected_status_note"),
    [
        (ZERO_KAKERALOOT_STATE, 1, ""),
        (NULL_KAKERALOOT_STATE, 1, None),
        (NO_KAKERALOOT_STATE, 0, "No Kakeraloots bought; Mudae did not report loot statistics."),
    ],
)
def test_kakeraloot_state_helper_preserves_boundary_values(tmp_path, state, expected_has_kakeraloots, expected_status_note) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_kakeraloot_state_with_connection(
            connection,
            state=state,
            server="Server",
            account="Account",
            raw="boundary kakeraloot payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
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
            (imported.kakeraloot_state_observation_id,),
        ).fetchone()
        assert tuple(row) == (
            expected_has_kakeraloots,
            expected_status_note,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            OBSERVED_AT.isoformat(),
            imported.import_event_id,
        )
        observation = catalog.kakeraloot_state("Server", "Account")
        assert observation is not None
        assert observation.has_kakeraloots is bool(expected_has_kakeraloots)
        assert observation.status_note == expected_status_note
        if expected_has_kakeraloots:
            assert observation.rolls_stacked == 0
            assert observation.kakera_balance == 0
        else:
            assert observation.rolls_stacked is None
            assert observation.kakera_balance is None


def test_kakeraloot_state_helper_does_not_write_projection_or_discord_state(tmp_path) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    with connect(database_path) as connection:
        before_event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()
        before_attempt = connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()[0]
        before_counts = _kakeraloot_state_counts(connection)
        catalog._import_kakeraloot_state_with_connection(
            connection,
            state=KAKERALOOT_STATE,
            server="Server",
            account="Account",
            raw="kakeraloot payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        after_counts = _kakeraloot_state_counts(connection)
        after_event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()
        after_attempt = connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()[0]

    assert after_counts["import_events"] == before_counts["import_events"] + 1
    assert after_counts["server_contexts"] == before_counts["server_contexts"] + 1
    assert after_counts["account_contexts"] == before_counts["account_contexts"] + 1
    assert after_counts["kakeraloot_state_observations"] == before_counts["kakeraloot_state_observations"] + 1
    assert after_counts["discord_projection_links"] == before_counts["discord_projection_links"]
    assert after_counts["discord_source_events"] == before_counts["discord_source_events"]
    assert after_counts["discord_source_event_server_attributions"] == before_counts[
        "discord_source_event_server_attributions"
    ]
    assert after_counts["discord_source_event_account_attributions"] == before_counts[
        "discord_source_event_account_attributions"
    ]
    assert after_counts["discord_processing_attempts"] == before_counts["discord_processing_attempts"]
    assert tuple(after_event) == tuple(before_event)
    assert after_attempt == before_attempt


def test_player_bonus_connection_result_is_frozen_slotted_with_exact_fields() -> None:
    assert is_dataclass(_PlayerBonusImportConnectionResult)
    assert _PlayerBonusImportConnectionResult.__dataclass_params__.frozen is True
    assert [field.name for field in fields(_PlayerBonusImportConnectionResult)] == [
        "import_event_id",
        "player_bonus_observation_id",
    ]
    assert not hasattr(_PlayerBonusImportConnectionResult(1, 2), "__dict__")


def test_public_player_bonus_wrapper_preserves_result_and_stored_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    raw_message = "player bonus payload with exact source text"

    result = catalog.import_player_bonus(
        PLAYER_BONUS,
        "  Server  ",
        "  Account  ",
        raw_message,
        "discord",
    )

    assert set(result.model_dump()) == {"import_event_id", "server_name", "account_name", "observed_at"}
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.observed_at.tzinfo is not None
    assert result.observed_at.utcoffset().total_seconds() == 0
    with connect(database_path) as connection:
        assert _player_bonus_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "player_bonus_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT account_context_id, metrics_json, rolls_per_hour_bonus, wishlist_slot_bonus,
                   wish_spawn_bonus_percent, starwish_spawn_bonus_percent,
                   starwish_total_spawn_bonus_percent, starwish_slot_bonus,
                   additional_wish_key_chance_percent, kakera_max_power_percent,
                   kakera_button_power_cost_percent, starwish_kakera_button_bonus_percent,
                   light_kakera_minimum, light_kakera_maximum, observed_at, import_event_id
            FROM player_bonus_observations WHERE import_event_id = ?
            """,
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("player_bonus", "discord", result.observed_at.isoformat(), raw_message)
        assert observation["metrics_json"] == json.dumps(
            [metric.model_dump() for metric in PLAYER_BONUS.metrics]
        )
        assert tuple(observation)[2:] == (
            PLAYER_BONUS.rolls_per_hour_bonus,
            PLAYER_BONUS.wishlist_slot_bonus,
            PLAYER_BONUS.wish_spawn_bonus_percent,
            PLAYER_BONUS.starwish_spawn_bonus_percent,
            PLAYER_BONUS.starwish_total_spawn_bonus_percent,
            PLAYER_BONUS.starwish_slot_bonus,
            PLAYER_BONUS.additional_wish_key_chance_percent,
            PLAYER_BONUS.kakera_max_power_percent,
            PLAYER_BONUS.kakera_button_power_cost_percent,
            PLAYER_BONUS.starwish_kakera_button_bonus_percent,
            PLAYER_BONUS.light_kakera_minimum,
            PLAYER_BONUS.light_kakera_maximum,
            result.observed_at.isoformat(),
            result.import_event_id,
        )


def test_player_bonus_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        imported = catalog._import_player_bonus_with_connection(
            connection,
            state=PLAYER_BONUS,
            server="Server",
            account="Account",
            raw="player bonus payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert _player_bonus_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "player_bonus_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        observation = connection.execute(
            "SELECT id, import_event_id FROM player_bonus_observations WHERE id = ?",
            (imported.player_bonus_observation_id,),
        ).fetchone()
        assert observation["id"] == imported.player_bonus_observation_id
        assert observation["import_event_id"] == imported.import_event_id
        with connect(database_path) as observer:
            assert _player_bonus_counts(observer) == {
                "import_events": 0,
                "server_contexts": 0,
                "account_contexts": 0,
                "player_bonus_observations": 0,
                "discord_projection_links": 0,
                "discord_source_events": 0,
                "discord_source_event_server_attributions": 0,
                "discord_source_event_account_attributions": 0,
                "discord_processing_attempts": 0,
            }
        connection.rollback()


def test_player_bonus_helper_commit_persists_values_and_returned_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_player_bonus_with_connection(
            connection,
            state=PLAYER_BONUS,
            server="Server",
            account="Account",
            raw="player bonus payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT id, kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (imported.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT id, account_context_id, metrics_json, rolls_per_hour_bonus, wishlist_slot_bonus,
                   wish_spawn_bonus_percent, starwish_spawn_bonus_percent,
                   starwish_total_spawn_bonus_percent, starwish_slot_bonus,
                   additional_wish_key_chance_percent, kakera_max_power_percent,
                   kakera_button_power_cost_percent, starwish_kakera_button_bonus_percent,
                   light_kakera_minimum, light_kakera_maximum, observed_at, import_event_id
            FROM player_bonus_observations WHERE id = ?
            """,
            (imported.player_bonus_observation_id,),
        ).fetchone()
        account = connection.execute(
            "SELECT id, name, normalized_name, server_context_id FROM account_contexts"
        ).fetchone()
        server = connection.execute(
            "SELECT id, name, normalized_name FROM server_contexts"
        ).fetchone()
        assert event["id"] == imported.import_event_id
        assert tuple(event)[1:] == ("player_bonus", "discord", "player bonus payload", OBSERVED_AT.isoformat())
        assert observation["id"] == imported.player_bonus_observation_id
        assert observation["account_context_id"] == account["id"]
        assert observation["metrics_json"] == json.dumps(
            [metric.model_dump() for metric in PLAYER_BONUS.metrics]
        )
        assert tuple(observation)[3:] == (
            PLAYER_BONUS.rolls_per_hour_bonus,
            PLAYER_BONUS.wishlist_slot_bonus,
            PLAYER_BONUS.wish_spawn_bonus_percent,
            PLAYER_BONUS.starwish_spawn_bonus_percent,
            PLAYER_BONUS.starwish_total_spawn_bonus_percent,
            PLAYER_BONUS.starwish_slot_bonus,
            PLAYER_BONUS.additional_wish_key_chance_percent,
            PLAYER_BONUS.kakera_max_power_percent,
            PLAYER_BONUS.kakera_button_power_cost_percent,
            PLAYER_BONUS.starwish_kakera_button_bonus_percent,
            PLAYER_BONUS.light_kakera_minimum,
            PLAYER_BONUS.light_kakera_maximum,
            OBSERVED_AT.isoformat(),
            imported.import_event_id,
        )
        assert tuple(server) == (server["id"], "Server", "server")
        assert tuple(account) == (account["id"], "Account", "account", server["id"])
        assert _player_bonus_counts(connection)["player_bonus_observations"] == 1


def test_player_bonus_helper_rollback_removes_new_rows_and_contexts(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_player_bonus_with_connection(
            connection,
            state=PLAYER_BONUS,
            server="Server",
            account="Account",
            raw="player bonus payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.rollback()

    with connect(database_path) as connection:
        assert _player_bonus_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "player_bonus_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_player_bonus_helper_reuses_contexts_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_player_bonus(PLAYER_BONUS, "Server", "Account", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        before_counts = _player_bonus_counts(connection)

    with connect(database_path) as connection:
        imported = catalog._import_player_bonus_with_connection(
            connection,
            state=PLAYER_BONUS,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="second payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert imported.import_event_id > 0
        assert imported.player_bonus_observation_id > 0
        connection.rollback()

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _player_bonus_counts(connection) == before_counts


def test_player_bonus_helper_preserves_null_zero_negative_and_empty_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_player_bonus_with_connection(
            connection,
            state=BOUNDARY_PLAYER_BONUS,
            server="Server",
            account="Account",
            raw="boundary player bonus payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        observation = connection.execute(
            """
            SELECT metrics_json, rolls_per_hour_bonus, wishlist_slot_bonus,
                   wish_spawn_bonus_percent, starwish_spawn_bonus_percent,
                   starwish_total_spawn_bonus_percent, starwish_slot_bonus,
                   additional_wish_key_chance_percent, kakera_max_power_percent,
                   kakera_button_power_cost_percent, starwish_kakera_button_bonus_percent,
                   light_kakera_minimum, light_kakera_maximum, import_event_id
            FROM player_bonus_observations WHERE id = ?
            """,
            (imported.player_bonus_observation_id,),
        ).fetchone()
        assert json.loads(observation["metrics_json"]) == [
            {"label": "", "detail": ""}
        ]
        assert tuple(observation)[1:] == (
            0,
            None,
            -1,
            0,
            None,
            0,
            None,
            0,
            -2,
            None,
            0,
            None,
            imported.import_event_id,
        )


def test_public_sphere_result_wrapper_preserves_compatibility_and_stored_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    raw_message = "sphere result payload with exact source text"

    result = catalog.import_sphere_result(
        SPHERE_RESULT,
        "  Server  ",
        "  Account  ",
        raw_message,
        "discord",
    )

    assert set(result.model_dump()) == {"import_event_id", "server_name", "account_name", "observed_at"}
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.observed_at.tzinfo is not None
    assert result.observed_at.utcoffset().total_seconds() == 0
    with connect(database_path) as connection:
        assert _sphere_result_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "sphere_result_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT account_context_id, snapshot_json, total_gained, stock, observed_at, import_event_id
            FROM sphere_result_observations WHERE import_event_id = ?
            """,
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("sphere_result", "discord", result.observed_at.isoformat(), raw_message)
        assert json.loads(observation["snapshot_json"]) == SPHERE_RESULT.model_dump(mode="json")
        assert observation["total_gained"] == SPHERE_RESULT.total_gained
        assert observation["stock"] is None
        assert observation["observed_at"] == result.observed_at.isoformat()
        assert observation["import_event_id"] == result.import_event_id


def test_sphere_result_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        imported = catalog._import_sphere_result_with_connection(
            connection,
            state=SPHERE_RESULT,
            server="Server",
            account="Account",
            raw="sphere payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert _sphere_result_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "sphere_result_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        observation = connection.execute(
            "SELECT id, import_event_id FROM sphere_result_observations WHERE id = ?",
            (imported.sphere_result_observation_id,),
        ).fetchone()
        assert observation["id"] == imported.sphere_result_observation_id
        assert observation["import_event_id"] == imported.import_event_id
        with connect(database_path) as observer:
            assert _sphere_result_counts(observer) == {
                "import_events": 0,
                "server_contexts": 0,
                "account_contexts": 0,
                "sphere_result_observations": 0,
                "discord_projection_links": 0,
                "discord_source_events": 0,
                "discord_source_event_server_attributions": 0,
                "discord_source_event_account_attributions": 0,
                "discord_processing_attempts": 0,
            }


@pytest.mark.parametrize("stock", [None, 0, 123])
def test_sphere_result_helper_commit_persists_values_and_returned_ids(tmp_path, stock) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    state = SPHERE_RESULT.model_copy(update={"stock": stock})

    with connect(database_path) as connection:
        imported = catalog._import_sphere_result_with_connection(
            connection,
            state=state,
            server="Server",
            account="Account",
            raw="sphere payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT id, kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (imported.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT id, account_context_id, snapshot_json, total_gained, stock, observed_at, import_event_id
            FROM sphere_result_observations WHERE id = ?
            """,
            (imported.sphere_result_observation_id,),
        ).fetchone()
        account = connection.execute(
            "SELECT id, name, normalized_name FROM account_contexts"
        ).fetchone()
        server = connection.execute(
            "SELECT id, name, normalized_name FROM server_contexts"
        ).fetchone()
        assert event["id"] == imported.import_event_id
        assert tuple(event)[1:] == ("sphere_result", "discord", "sphere payload", OBSERVED_AT.isoformat())
        assert observation["id"] == imported.sphere_result_observation_id
        assert observation["account_context_id"] == account["id"]
        assert observation["snapshot_json"] == json.dumps(state.model_dump())
        assert observation["total_gained"] == SPHERE_RESULT.total_gained
        assert observation["stock"] == stock
        assert observation["observed_at"] == OBSERVED_AT.isoformat()
        assert observation["import_event_id"] == imported.import_event_id
        assert tuple(server) == (server["id"], "Server", "server")
        assert tuple(account) == (account["id"], "Account", "account")
        assert _sphere_result_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "sphere_result_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_sphere_result_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_sphere_result_with_connection(
            connection,
            state=SPHERE_RESULT,
            server="Server",
            account="Account",
            raw="sphere payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.rollback()

    with connect(database_path) as connection:
        assert _sphere_result_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "sphere_result_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_sphere_result_helper_reuses_contexts_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_sphere_result(SPHERE_RESULT, "Server", "Account", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        before_counts = _sphere_result_counts(connection)

    with connect(database_path) as connection:
        imported = catalog._import_sphere_result_with_connection(
            connection,
            state=SPHERE_RESULT,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="second payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert imported.import_event_id > 0
        assert imported.sphere_result_observation_id > 0
        connection.rollback()

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _sphere_result_counts(connection) == before_counts


def test_wishlist_connection_result_is_frozen_slotted_with_exact_fields() -> None:
    assert is_dataclass(_WishlistImportConnectionResult)
    assert _WishlistImportConnectionResult.__dataclass_params__.frozen is True
    assert [field.name for field in fields(_WishlistImportConnectionResult)] == [
        "import_event_id",
        "wishlist_observation_id",
    ]
    assert not hasattr(_WishlistImportConnectionResult(1, 2), "__dict__")


def test_public_wishlist_wrapper_preserves_result_and_stored_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    raw_message = "wishlist payload with exact source text"

    result = catalog.import_wishlist(
        WISHLIST,
        "  Server  ",
        "  Account  ",
        raw_message,
        "discord",
    )

    assert set(result.model_dump()) == {"import_event_id", "server_name", "account_name", "observed_at"}
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.observed_at.tzinfo is not None
    assert result.observed_at.utcoffset().total_seconds() == 0
    with connect(database_path) as connection:
        assert _wishlist_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "wishlist_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT account_context_id, wishlist_count, wishlist_capacity, starwish_count,
                   starwish_capacity, entries_json, observed_at, import_event_id
            FROM wishlist_observations WHERE import_event_id = ?
            """,
            (result.import_event_id,),
        ).fetchone()
        account = connection.execute(
            "SELECT id, name, normalized_name FROM account_contexts"
        ).fetchone()
        server = connection.execute(
            "SELECT id, name, normalized_name FROM server_contexts"
        ).fetchone()
        assert tuple(event) == ("wishlist", "discord", result.observed_at.isoformat(), raw_message)
        assert observation["account_context_id"] == account["id"]
        assert tuple(observation)[1:5] == (
            WISHLIST.wishlist_count,
            WISHLIST.wishlist_capacity,
            WISHLIST.starwish_count,
            WISHLIST.starwish_capacity,
        )
        assert observation["entries_json"] == json.dumps(
            [entry.model_dump() for entry in WISHLIST.entries]
        )
        assert observation["observed_at"] == result.observed_at.isoformat()
        assert observation["import_event_id"] == result.import_event_id
        assert tuple(server) == (server["id"], "Server", "server")
        assert tuple(account) == (account["id"], "Account", "account")


def test_wishlist_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        imported = catalog._import_wishlist_with_connection(
            connection,
            state=WISHLIST,
            server="Server",
            account="Account",
            raw="wishlist payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert isinstance(imported, _WishlistImportConnectionResult)
        assert connection.in_transaction is True
        assert _wishlist_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "wishlist_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT id, kind FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()
        observation = connection.execute(
            "SELECT id, import_event_id FROM wishlist_observations WHERE id = ?",
            (imported.wishlist_observation_id,),
        ).fetchone()
        assert tuple(event) == (imported.import_event_id, "wishlist")
        assert tuple(observation) == (
            imported.wishlist_observation_id,
            imported.import_event_id,
        )
        with connect(database_path) as observer:
            assert _wishlist_counts(observer) == {
                "import_events": 0,
                "server_contexts": 0,
                "account_contexts": 0,
                "wishlist_observations": 0,
                "discord_projection_links": 0,
                "discord_source_events": 0,
                "discord_source_event_server_attributions": 0,
                "discord_source_event_account_attributions": 0,
                "discord_processing_attempts": 0,
            }
        connection.rollback()


def test_wishlist_helper_commit_persists_values_and_returned_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_wishlist_with_connection(
            connection,
            state=WISHLIST,
            server="Server",
            account="Account",
            raw="wishlist payload",
            source="clipboard",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT id, kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (imported.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT id, account_context_id, wishlist_count, wishlist_capacity, starwish_count,
                   starwish_capacity, entries_json, observed_at, import_event_id
            FROM wishlist_observations WHERE id = ?
            """,
            (imported.wishlist_observation_id,),
        ).fetchone()
        assert event["id"] == imported.import_event_id
        assert tuple(event)[1:] == ("wishlist", "clipboard", "wishlist payload", OBSERVED_AT.isoformat())
        assert observation["id"] == imported.wishlist_observation_id
        assert observation["wishlist_count"] == WISHLIST.wishlist_count
        assert observation["wishlist_capacity"] == WISHLIST.wishlist_capacity
        assert observation["starwish_count"] == WISHLIST.starwish_count
        assert observation["starwish_capacity"] == WISHLIST.starwish_capacity
        assert observation["entries_json"] == json.dumps(
            [entry.model_dump() for entry in WISHLIST.entries]
        )
        assert observation["observed_at"] == OBSERVED_AT.isoformat()
        assert observation["import_event_id"] == imported.import_event_id
        wishlist = catalog.wishlist("Server", "Account")
        assert wishlist is not None
        assert wishlist.entries == WISHLIST.entries


def test_wishlist_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_wishlist_with_connection(
            connection,
            state=WISHLIST,
            server="Server",
            account="Account",
            raw="wishlist payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.rollback()

    with connect(database_path) as connection:
        assert _wishlist_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "wishlist_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_wishlist_helper_reuses_contexts_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_wishlist(WISHLIST, "Server", "Account", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        before_counts = _wishlist_counts(connection)

    with connect(database_path) as connection:
        imported = catalog._import_wishlist_with_connection(
            connection,
            state=EMPTY_WISHLIST,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="second payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert imported.import_event_id > 0
        assert imported.wishlist_observation_id > 0
        connection.rollback()

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _wishlist_counts(connection) == before_counts


@pytest.mark.parametrize("state", [EMPTY_WISHLIST])
def test_wishlist_helper_preserves_empty_zero_and_duplicate_boundaries(tmp_path, state) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_wishlist_with_connection(
            connection,
            state=state,
            server="Server",
            account="Account",
            raw="boundary wishlist payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT wishlist_count, wishlist_capacity, starwish_count, starwish_capacity, entries_json
            FROM wishlist_observations WHERE id = ?
            """,
            (imported.wishlist_observation_id,),
        ).fetchone()
        assert tuple(row) == (0, 0, 0, 0, "[]")
        assert json.loads(row["entries_json"]) == []

        duplicate_row = connection.execute(
            "SELECT entries_json FROM wishlist_observations WHERE import_event_id = ?",
            (catalog.import_wishlist(WISHLIST, "Server", "Account", "duplicate payload", "discord").import_event_id,),
        ).fetchone()
        assert json.loads(duplicate_row["entries_json"]) == [entry.model_dump() for entry in WISHLIST.entries]


def test_disablelist_connection_result_is_frozen_slotted_with_exact_fields() -> None:
    assert is_dataclass(_DisableListImportConnectionResult)
    assert _DisableListImportConnectionResult.__dataclass_params__.frozen is True
    assert [field.name for field in fields(_DisableListImportConnectionResult)] == [
        "import_event_id",
        "disablelist_observation_id",
    ]
    assert not hasattr(_DisableListImportConnectionResult(1, 2), "__dict__")


def test_public_disablelist_wrapper_preserves_result_and_stored_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    raw_message = "disablelist payload with exact source text"

    result = catalog.import_disablelist(
        DISABLELIST,
        "  Server  ",
        "  Account  ",
        raw_message,
        "discord",
    )

    assert set(result.model_dump()) == {"import_event_id", "server_name", "account_name", "observed_at"}
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.observed_at.tzinfo is not None
    assert result.observed_at.utcoffset().total_seconds() == 0
    with connect(database_path) as connection:
        assert _disablelist_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "disablelist_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT account_context_id, slots_used, slots_capacity, total_disabled, disabled_wa,
                   disabled_ha, disabled_wg, disabled_hg, wa_pool_limit, ha_pool_limit,
                   western_disabled, irl_disabled, entries_json, observed_at, import_event_id
            FROM disablelist_observations WHERE import_event_id = ?
            """,
            (result.import_event_id,),
        ).fetchone()
        account = connection.execute("SELECT id, name, normalized_name FROM account_contexts").fetchone()
        server = connection.execute("SELECT id, name, normalized_name FROM server_contexts").fetchone()
        assert tuple(event) == ("disablelist", "discord", result.observed_at.isoformat(), raw_message)
        assert observation["account_context_id"] == account["id"]
        assert tuple(observation)[1:12] == (
            DISABLELIST.slots_used,
            DISABLELIST.slots_capacity,
            DISABLELIST.total_disabled,
            DISABLELIST.disabled_wa,
            DISABLELIST.disabled_ha,
            DISABLELIST.disabled_wg,
            DISABLELIST.disabled_hg,
            DISABLELIST.wa_pool_limit,
            DISABLELIST.ha_pool_limit,
            DISABLELIST.western_disabled,
            DISABLELIST.irl_disabled,
        )
        assert observation["entries_json"] == json.dumps(
            [entry.model_dump() for entry in DISABLELIST.entries]
        )
        assert observation["observed_at"] == result.observed_at.isoformat()
        assert observation["import_event_id"] == result.import_event_id
        assert tuple(server) == (server["id"], "Server", "server")
        assert tuple(account) == (account["id"], "Account", "account")


def test_disablelist_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        imported = catalog._import_disablelist_with_connection(
            connection,
            state=DISABLELIST,
            server="Server",
            account="Account",
            raw="disablelist payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert isinstance(imported, _DisableListImportConnectionResult)
        assert connection.in_transaction is True
        assert _disablelist_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "disablelist_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT id, kind FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()
        observation = connection.execute(
            "SELECT id, import_event_id FROM disablelist_observations WHERE id = ?",
            (imported.disablelist_observation_id,),
        ).fetchone()
        assert tuple(event) == (imported.import_event_id, "disablelist")
        assert tuple(observation) == (
            imported.disablelist_observation_id,
            imported.import_event_id,
        )
        with connect(database_path) as observer:
            assert _disablelist_counts(observer) == {
                "import_events": 0,
                "server_contexts": 0,
                "account_contexts": 0,
                "disablelist_observations": 0,
                "discord_projection_links": 0,
                "discord_source_events": 0,
                "discord_source_event_server_attributions": 0,
                "discord_source_event_account_attributions": 0,
                "discord_processing_attempts": 0,
            }
        connection.rollback()


def test_disablelist_helper_commit_preserves_all_values_and_returned_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_disablelist_with_connection(
            connection,
            state=DISABLELIST,
            server="Server",
            account="Account",
            raw="disablelist payload",
            source="clipboard",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT id, kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (imported.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT id, slots_used, slots_capacity, total_disabled, disabled_wa, disabled_ha,
                   disabled_wg, disabled_hg, wa_pool_limit, ha_pool_limit, western_disabled,
                   irl_disabled, entries_json, observed_at, import_event_id
            FROM disablelist_observations WHERE id = ?
            """,
            (imported.disablelist_observation_id,),
        ).fetchone()
        assert event["id"] == imported.import_event_id
        assert tuple(event)[1:] == (
            "disablelist",
            "clipboard",
            "disablelist payload",
            OBSERVED_AT.isoformat(),
        )
        assert observation["id"] == imported.disablelist_observation_id
        assert tuple(observation)[1:12] == (
            DISABLELIST.slots_used,
            DISABLELIST.slots_capacity,
            DISABLELIST.total_disabled,
            DISABLELIST.disabled_wa,
            DISABLELIST.disabled_ha,
            DISABLELIST.disabled_wg,
            DISABLELIST.disabled_hg,
            DISABLELIST.wa_pool_limit,
            DISABLELIST.ha_pool_limit,
            DISABLELIST.western_disabled,
            DISABLELIST.irl_disabled,
        )
        assert observation["entries_json"] == json.dumps(
            [entry.model_dump() for entry in DISABLELIST.entries]
        )
        assert observation["observed_at"] == OBSERVED_AT.isoformat()
        assert observation["import_event_id"] == imported.import_event_id
        disablelist = catalog.disablelist("Server", "Account")
        assert disablelist is not None
        assert disablelist.entries == DISABLELIST.entries


def test_disablelist_helper_rollback_removes_new_rows_and_contexts(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_disablelist_with_connection(
            connection,
            state=DISABLELIST,
            server="Server",
            account="Account",
            raw="disablelist payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.rollback()

    with connect(database_path) as connection:
        assert _disablelist_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "disablelist_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_disablelist_helper_reuses_contexts_and_preserves_boundary_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_disablelist(DISABLELIST, "Server", "Account", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        before_counts = _disablelist_counts(connection)

    with connect(database_path) as connection:
        imported = catalog._import_disablelist_with_connection(
            connection,
            state=BOUNDARY_DISABLELIST,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="boundary disablelist payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        row = connection.execute(
            """
            SELECT slots_used, slots_capacity, total_disabled, disabled_wa, disabled_ha, disabled_wg,
                   disabled_hg, wa_pool_limit, ha_pool_limit, western_disabled, irl_disabled, entries_json
            FROM disablelist_observations WHERE id = ?
            """,
            (imported.disablelist_observation_id,),
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert tuple(row) == (0, 0, 0, 0, 0, 0, 0, 0, None, 0, 0, "[]")
        connection.rollback()

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _disablelist_counts(connection) == before_counts


def test_public_mudapins_wrapper_preserves_compatibility_and_stored_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    raw_message = "mudapins payload with exact source text"

    result = catalog.import_mudapins(
        MUDAPINS,
        "  Server  ",
        "  Account  ",
        raw_message,
        "discord",
    )

    assert set(result.model_dump()) == {"import_event_id", "server_name", "account_name", "observed_at"}
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.observed_at.tzinfo is not None
    assert result.observed_at.utcoffset().total_seconds() == 0
    with connect(database_path) as connection:
        assert _mudapins_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "mudapin_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT id, account_context_id, pin_markers_json, pin_count, observed_at, import_event_id
            FROM mudapin_observations WHERE import_event_id = ?
            """,
            (result.import_event_id,),
        ).fetchone()
        account = connection.execute("SELECT id, name, normalized_name FROM account_contexts").fetchone()
        server = connection.execute("SELECT name, normalized_name FROM server_contexts").fetchone()
        assert tuple(event) == ("mudapins", "discord", result.observed_at.isoformat(), raw_message)
        assert observation["id"] > 0
        assert observation["account_context_id"] == account["id"]
        assert json.loads(observation["pin_markers_json"]) == list(MUDAPINS.pin_markers)
        assert observation["pin_count"] == len(MUDAPINS.pin_markers)
        assert observation["observed_at"] == result.observed_at.isoformat()
        assert observation["import_event_id"] == result.import_event_id
        assert tuple(account) == (account["id"], "Account", "account")
        assert tuple(server) == ("Server", "server")


def test_mudapins_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        imported = catalog._import_mudapins_with_connection(
            connection,
            snapshot=MUDAPINS,
            server="Server",
            account="Account",
            raw="mudapins payload",
            source="clipboard",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert _mudapins_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "mudapin_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        assert connection.execute(
            "SELECT import_event_id FROM mudapin_observations WHERE id = ?",
            (imported.mudapin_observation_id,),
        ).fetchone()[0] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM mudapin_observations").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0


def test_mudapins_helper_commit_persists_rows_and_returned_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_mudapins_with_connection(
            connection,
            snapshot=MUDAPINS,
            server="Server",
            account="Account",
            raw="mudapins payload",
            source="clipboard",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT id, kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (imported.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT id, observed_at, import_event_id, account_context_id
            FROM mudapin_observations WHERE id = ?
            """,
            (imported.mudapin_observation_id,),
        ).fetchone()
        account = connection.execute(
            "SELECT id FROM account_contexts WHERE normalized_name = ?", ("account",)
        ).fetchone()
        assert event["id"] == imported.import_event_id
        assert tuple(event)[1:] == (
            "mudapins",
            "clipboard",
            "mudapins payload",
            OBSERVED_AT.isoformat(),
        )
        assert observation["id"] == imported.mudapin_observation_id
        assert observation["observed_at"] == OBSERVED_AT.isoformat()
        assert observation["import_event_id"] == imported.import_event_id
        assert observation["account_context_id"] == account["id"]


def test_mudapins_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_mudapins_with_connection(
            connection,
            snapshot=MUDAPINS,
            server="Server",
            account="Account",
            raw="mudapins payload",
            source="clipboard",
            observed_at=OBSERVED_AT,
        )
        connection.rollback()

    with connect(database_path) as connection:
        assert _mudapins_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "mudapin_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_mudapins_helper_reuses_contexts_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_mudapins(MUDAPINS, "Server", "Account", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        before_counts = _mudapins_counts(connection)

    with connect(database_path) as connection:
        imported = catalog._import_mudapins_with_connection(
            connection,
            snapshot=MUDAPINS,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="second payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert imported.import_event_id > 0
        assert imported.mudapin_observation_id > 0
        connection.rollback()

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _mudapins_counts(connection) == before_counts


def test_mudapins_marker_storage_preserves_order_duplicates_and_empty_inventory(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    snapshots = (
        (MudapinSnapshot(pin_markers=(":pin1:", ":pin2:")), (":pin1:", ":pin2:")),
        (MudapinSnapshot(pin_markers=(":pin3:", ":logopin4:")), (":pin3:", ":logopin4:")),
        (
            MudapinSnapshot(pin_markers=(":pin5:", ":pin5:", ":logopin6:")),
            (":pin5:", ":pin5:", ":logopin6:"),
        ),
        (MudapinSnapshot(pin_markers=()), ()),
    )

    results = [
        catalog.import_mudapins(snapshot, "Server", f"Account {index}", "payload", "discord")
        for index, (snapshot, _expected) in enumerate(snapshots)
    ]

    with connect(database_path) as connection:
        for result, (_snapshot, expected) in zip(results, snapshots):
            observation = connection.execute(
                "SELECT pin_markers_json, pin_count FROM mudapin_observations WHERE import_event_id = ?",
                (result.import_event_id,),
            ).fetchone()
            assert json.loads(observation["pin_markers_json"]) == list(expected)
            assert observation["pin_count"] == len(expected)


def test_mudapins_helper_does_not_write_discord_or_legacy_state(tmp_path) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    with connect(database_path) as connection:
        before_event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()
        before_attempt = connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()[0]
        before_counts = _mudapins_counts(connection)
        imported = catalog._import_mudapins_with_connection(
            connection,
            snapshot=MUDAPINS,
            server="Server",
            account="Account",
            raw="mudapins payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        after_counts = _mudapins_counts(connection)
        after_event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()
        after_attempt = connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()[0]

    assert after_counts["import_events"] == before_counts["import_events"] + 1
    assert after_counts["server_contexts"] == before_counts["server_contexts"] + 1
    assert after_counts["account_contexts"] == before_counts["account_contexts"] + 1
    assert after_counts["mudapin_observations"] == before_counts["mudapin_observations"] + 1
    assert after_counts["discord_projection_links"] == before_counts["discord_projection_links"]
    assert after_counts["discord_source_event_server_attributions"] == before_counts[
        "discord_source_event_server_attributions"
    ]
    assert after_counts["discord_source_event_account_attributions"] == before_counts[
        "discord_source_event_account_attributions"
    ]
    assert after_counts["discord_processing_attempts"] == before_counts["discord_processing_attempts"]
    assert tuple(after_event) == tuple(before_event)
    assert after_attempt == before_attempt
    assert imported.import_event_id > 0
    assert imported.mudapin_observation_id > 0


def test_public_antidisable_import_preserves_result_and_stored_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_antidisable_page(
        ANTIDISABLE_PAGE,
        "  Server  ",
        "  Account  ",
        "antidisable payload",
        "clipboard",
    )

    assert set(result.model_dump()) == {
        "import_event_id",
        "server_name",
        "account_name",
        "series_imported",
        "observed_at",
        "scan_id",
        "page_number",
        "page_count",
    }
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.series_imported == 3
    assert result.scan_id is None
    assert result.page_number == 1
    assert result.page_count == 2
    assert result.observed_at == datetime.fromisoformat(result.observed_at.isoformat())

    with connect(database_path) as connection:
        assert _antidisable_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "harem_scans": 0,
            "harem_scan_pages": 0,
            "antidisable_series_observations": 3,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        rows = connection.execute(
            """
            SELECT series_name, normalized_series_name, antidisabled_character_count,
                   observed_at, import_event_id, harem_scan_id
            FROM antidisable_series_observations
            WHERE import_event_id = ?
            ORDER BY id
            """,
            (result.import_event_id,),
        ).fetchall()
        assert tuple(event) == (
            "antidisable",
            "clipboard",
            result.observed_at.isoformat(),
            "antidisable payload",
        )
        assert [tuple(row) for row in rows] == [
            ("Series B", "series b", 2_614, result.observed_at.isoformat(), result.import_event_id, None),
            ("Series A", "series a", 2_614, result.observed_at.isoformat(), result.import_event_id, None),
            ("Series B", "series b", 2_614, result.observed_at.isoformat(), result.import_event_id, None),
        ]


def test_antidisable_helper_uses_supplied_connection_and_returns_actual_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_antidisable_page_with_connection(
            connection,
            page=ANTIDISABLE_PAGE,
            scan_id=None,
            server=" Server ",
            account=" Account ",
            raw="raw antidisable",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert isinstance(imported, _AntidisablePageImportConnectionResult)
        assert [field.name for field in fields(imported)] == ["import_event_id", "scan_id"]
        assert imported.import_event_id > 0
        assert imported.scan_id is None
        assert connection.in_transaction is True
        assert _antidisable_counts(connection)["antidisable_series_observations"] == 3
        with connect(database_path) as observer:
            assert _antidisable_counts(observer) == {
                "import_events": 0,
                "server_contexts": 0,
                "account_contexts": 0,
                "harem_scans": 0,
                "harem_scan_pages": 0,
                "antidisable_series_observations": 0,
                "discord_projection_links": 0,
                "discord_source_events": 0,
                "discord_source_event_server_attributions": 0,
                "discord_source_event_account_attributions": 0,
                "discord_processing_attempts": 0,
            }
        connection.commit()

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT kind FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()[0] == "antidisable"
        assert connection.execute(
            "SELECT COUNT(*) FROM antidisable_series_observations WHERE import_event_id = ?",
            (imported.import_event_id,),
        ).fetchone()[0] == 3


def test_antidisable_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_antidisable_page_with_connection(
            connection,
            page=ANTIDISABLE_PAGE,
            scan_id=None,
            server="Server",
            account="Account",
            raw="rollback antidisable",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.rollback()

    with connect(database_path) as connection:
        assert _antidisable_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "harem_scans": 0,
            "harem_scan_pages": 0,
            "antidisable_series_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_antidisable_helper_rollback_preserves_existing_scan_and_contexts(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    scan = catalog.begin_antidisable_scan("Server", "Account")

    with connect(database_path) as connection:
        before = _antidisable_counts(connection)
        existing = connection.execute(
            "SELECT account_context_id, expected_page_count FROM harem_scans WHERE id = ?",
            (scan.id,),
        ).fetchone()

    with connect(database_path) as connection:
        imported = catalog._import_antidisable_page_with_connection(
            connection,
            page=ANTIDISABLE_PAGE,
            scan_id=scan.id,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="rolled back page",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert imported.scan_id == scan.id
        connection.rollback()

    with connect(database_path) as connection:
        after = _antidisable_counts(connection)
        current = connection.execute(
            "SELECT account_context_id, expected_page_count FROM harem_scans WHERE id = ?",
            (scan.id,),
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert after == before


def test_antidisable_scanned_pages_preserve_order_nulls_and_scan_state(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    scan = catalog.begin_antidisable_scan("Server", "Account")

    with connect(database_path) as connection:
        second = catalog._import_antidisable_page_with_connection(
            connection,
            page=ANTIDISABLE_CONTINUATION_PAGE,
            scan_id=scan.id,
            server="Server",
            account="Account",
            raw="page two",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert second.scan_id == scan.id
        assert connection.execute(
            "SELECT expected_page_count FROM harem_scans WHERE id = ?", (scan.id,)
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT page_number FROM harem_scan_pages WHERE harem_scan_id = ?", (scan.id,)
        ).fetchone()[0] == 2
        continuation_rows = connection.execute(
            """
            SELECT series_name, antidisabled_character_count
            FROM antidisable_series_observations
            WHERE import_event_id = ?
            ORDER BY id
            """,
            (second.import_event_id,),
        ).fetchall()
        assert [tuple(row) for row in continuation_rows] == [
            ("Series C", None),
            ("Series A", None),
        ]
        with connect(database_path) as observer:
            assert observer.execute(
                "SELECT COUNT(*) FROM harem_scan_pages WHERE harem_scan_id = ?", (scan.id,)
            ).fetchone()[0] == 0
        connection.commit()

    first = catalog.import_antidisable_page(
        ANTIDISABLE_PAGE,
        "Server",
        "Account",
        "page one",
        "discord",
        scan.id,
    )
    assert first.scan_id == scan.id
    progress = catalog.harem_scan_progress(scan.id)
    assert progress is not None
    assert progress.imported_pages == (1, 2)
    assert progress.is_complete is True
    with connect(database_path) as connection:
        pages = connection.execute(
            "SELECT page_number, import_event_id FROM harem_scan_pages WHERE harem_scan_id = ? "
            "ORDER BY page_number",
            (scan.id,),
        ).fetchall()
        assert [tuple(row) for row in pages] == [(1, first.import_event_id), (2, second.import_event_id)]
        rows = connection.execute(
            """
            SELECT series_name, antidisabled_character_count
            FROM antidisable_series_observations
            WHERE harem_scan_id = ?
            ORDER BY id
            """,
            (scan.id,),
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("Series C", None),
            ("Series A", None),
            ("Series B", 2_614),
            ("Series A", 2_614),
            ("Series B", 2_614),
        ]

    catalog.complete_antidisable_scan(scan.id)
    assert catalog.antidisable_series("Server", "Account") == ("Series A", "Series B", "Series C")


def test_antidisable_duplicate_and_invalid_pages_keep_existing_rejections(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    scan = catalog.begin_antidisable_scan("Server", "Account")
    catalog.import_antidisable_page(
        ANTIDISABLE_PAGE, "Server", "Account", "first", "discord", scan.id
    )
    with connect(database_path) as connection:
        before = _antidisable_counts(connection)

    with pytest.raises(ValueError, match="already contains that page"):
        catalog.import_antidisable_page(
            ANTIDISABLE_PAGE, "Server", "Account", "duplicate", "discord", scan.id
        )
    with pytest.raises(ValueError, match="include its Page X / Y"):
        catalog.import_antidisable_page(
            ANTIDISABLE_PAGE.model_copy(update={"page_number": None}),
            "Server",
            "Account",
            "invalid",
            "discord",
            scan.id,
        )
    with pytest.raises(ValueError, match="page count does not match"):
        catalog.import_antidisable_page(
            ANTIDISABLE_CONTINUATION_PAGE.model_copy(update={"page_count": 3}),
            "Server",
            "Account",
            "mismatch",
            "discord",
            scan.id,
        )

    with connect(database_path) as connection:
        assert _antidisable_counts(connection) == before

    other_scan = catalog.begin_antidisable_scan("Server", "Account")
    other = catalog.import_antidisable_page(
        ANTIDISABLE_PAGE, "Server", "Account", "other scan", "discord", other_scan.id
    )
    assert other.scan_id == other_scan.id
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM harem_scan_pages WHERE page_number = 1"
        ).fetchone()[0] == 2
