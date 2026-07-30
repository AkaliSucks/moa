import sqlite3
from datetime import datetime, timezone
from unittest.mock import Mock, call

import pytest

from moa.models.character import (
    BadgeLevel,
    ClaimConfirmation,
    KakeraStateSnapshot,
    KakeralootStateSnapshot,
    KakeralootSettingsSnapshot,
    MudapinSnapshot,
    PlayerBonusMetric,
    PlayerBonusSnapshot,
    ProfileSnapshot,
    RollObservation,
    ServerSettingMetric,
    ServerSettingsSnapshot,
    SphereGain,
    SphereResultSnapshot,
    TowerStateSnapshot,
    TimerStateSnapshot,
)
from moa.models.catalog import AutomaticImportResult
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.parser.mudae import MudaeTextParser
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.services.automatic_import_service import (
    AutomaticImportService,
    DurableClaimImportContext,
    DurableInfoklImportContext,
    DurableKakeraImportContext,
    DurableKakeralootStateImportContext,
    DurableMudapinsImportContext,
    DurablePlayerBonusImportContext,
    DurableProfileImportContext,
    DurableRollImportContext,
    DurableSettingsImportContext,
    DurableSphereResultImportContext,
    DurableTimerImportContext,
    DurableTowerStateImportContext,
)
from moa.services.catalog_service import CatalogService
from moa.services.claim_projection_coordinator import (
    ClaimProjectionCoordinator,
    ClaimProjectionResult,
)
from moa.services.infokl_projection_coordinator import (
    InfoklProjectionCoordinator,
    InfoklProjectionResult,
)
from moa.services.kakera_state_projection_coordinator import (
    KakeraStateProjectionCoordinator,
    KakeraStateProjectionResult,
)
from moa.services.kakeraloot_state_projection_coordinator import (
    KakeralootStateProjectionResult,
)
from moa.services.mudapins_projection_coordinator import (
    MudapinsProjectionCoordinator,
    MudapinsProjectionResult,
)
from moa.services.player_bonus_projection_coordinator import PlayerBonusProjectionResult
from moa.services.profile_projection_coordinator import (
    ProfileProjectionCoordinator,
    ProfileProjectionResult,
)
from moa.services.roll_projection_coordinator import (
    RollProjectionCoordinator,
    RollProjectionResult,
)
from moa.services.settings_projection_coordinator import (
    SettingsProjectionCoordinator,
    SettingsProjectionResult,
)
from moa.services.sphere_result_projection_coordinator import SphereResultProjectionResult
from moa.services.timer_projection_coordinator import (
    TimerProjectionCoordinator,
    TimerProjectionResult,
)
from moa.services.tower_state_projection_coordinator import TowerStateProjectionResult


ROLL_MESSAGE = "Hips\nDekoboko Majo no Oyako Jijou\n30:kakera:"
PROFILE_MESSAGE = "moa\nCollection size: 0 (0%:female: 0% :male:)"
CLAIM_MESSAGE = "ernieuuu and Pakunoda are now married!"
TIMER_MESSAGE = "You have **17** rolls left. Next rolls reset in **49** min."
KAKERA_MESSAGE = (
    "You have 7,673:kakera:!\n"
    ":BronzeIV: Bronze IV · Max reached!\n"
    "Silver III · Max reached!\n"
    "Gold II · 10,000 kakera remaining"
)
MUDAPINS_MESSAGE = ":pin139::pin139::logopin6::pin2157:"
EMPTY_MUDAPINS_MESSAGE = "No mudapins found! Collect them with kakeraloots ($kl)"
TOWER_MESSAGE = "tower payload"
SPHERE_MESSAGE = ":sp: +158\n:spG: +43 (Stock: 3,655)"
SETTINGS_MESSAGE = "settings payload"
BONUS_MESSAGE = "bonus payload"
OBSERVED_AT = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
FINISHED_AT = datetime(2026, 7, 21, 12, 1, tzinfo=timezone.utc)

SPHERE_RESULT = SphereResultSnapshot(
    clicks_available=2,
    click_window_minutes=2,
    purple_target=4,
    purple_total=3,
    gains=(
        SphereGain(sphere_type="purple", amount=158),
        SphereGain(sphere_type="green", amount=43),
    ),
    total_gained=201,
    stock=3655,
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

INFOKL_MESSAGE = "infokl payload"
INFOKL_SETTINGS = KakeralootSettingsSnapshot(
    loot_cost=500,
    quantity_quality_base_cost=1000,
    quantity_quality_level_increment=250,
)

KAKERALOOT_MESSAGE = "kakeraloot state payload"
KAKERALOOT_STATE = KakeralootStateSnapshot(
    has_kakeraloots=True,
    status_note="all fields",
    rolls_stacked=1,
    disable_wa_ha_reduction=102,
    disable_wg_hg_reduction=68,
    protected_wish_level=42,
    protected_wish_denominator=4642,
    mudapins=22,
    rt_cooldown_reduction_hours=2,
    permanent_roll_bonus=1,
    star_branches=3,
    starwish_slots_from_branches=4,
    quantity_level=23,
    quality_level=6,
    usage_count=256,
    kakera_balance=9210,
)

KAKERA_STATE = KakeraStateSnapshot(
    kakera_balance=7_673,
    badges=(
        BadgeLevel(badge_name="bronze", level=4, max_reached=True),
        BadgeLevel(badge_name="silver", level=3, max_reached=True),
        BadgeLevel(badge_name="gold", level=2, max_reached=False),
    ),
)
ZERO_KAKERA_STATE = KakeraStateSnapshot(kakera_balance=0, badges=())
TOWER_STATE = TowerStateSnapshot(
    current_level=2,
    completed_towers=3,
    next_level_cost=75_000,
    kakera_balance=7_673,
    built_perk_ids=(2, 7),
)

TIMER_STATE = TimerStateSnapshot(
    can_claim_now=None,
    claim_reset_minutes=None,
    rolls_left=17,
    rolls_reset_minutes=49,
    rolls_reset_stock=None,
    vote_reset_minutes=None,
    daily_reset_minutes=None,
    daily_kakera_ready=None,
    rt_available=None,
    can_react_kakera_now=None,
    reaction_power_percent=None,
    kakera_button_power_cost_percent=None,
    soulmate_button_power_cost_percent=None,
    kakera_stock=None,
    gold_key_stock_remaining=None,
    gold_key_reset_minutes=None,
    bku_reset_probability_percent=None,
    oh_remaining=None,
    oc_remaining=None,
    oq_remaining=None,
    oq_stored=None,
    ot_remaining=None,
    ouro_refill_minutes=None,
    rolls_reset_status=None,
    rolls_per_hour_limit=None,
    rt_reset_minutes=None,
)

PROFILE = ProfileSnapshot(
    profile_name="profile-account",
    collection_size=0,
    female_percent=0,
    male_percent=0,
    pokedex_count=None,
    pokedex_pokemon=(),
    kakera_reacts={},
    mudapins_collected=None,
    mudapins_total=None,
    kakera_balance=None,
    bronze_keys=0,
    silver_keys=0,
    gold_keys=0,
    sphere_stock=None,
    spheres={},
    displayed_badges=(),
)

MUDAPINS = MudapinSnapshot(
    pin_markers=(":pin139:", ":pin139:", ":logopin6:", ":pin2157:")
)
EMPTY_MUDAPINS = MudapinSnapshot(pin_markers=())
PLAYER_BONUS = PlayerBonusSnapshot(
    metrics=(PlayerBonusMetric(label="Rolls", detail="+2"),),
    rolls_per_hour_bonus=2,
    wishlist_slot_bonus=0,
    wish_spawn_bonus_percent=None,
    starwish_spawn_bonus_percent=3,
    starwish_total_spawn_bonus_percent=4,
    starwish_slot_bonus=5,
    additional_wish_key_chance_percent=6,
    kakera_max_power_percent=7,
    kakera_button_power_cost_percent=8,
    starwish_kakera_button_bonus_percent=9,
    light_kakera_minimum=10,
    light_kakera_maximum=11,
)


def _durable_roll_importer(tmp_path):
    database_path = tmp_path / "durable-roll.db"
    catalog_repository = CatalogRepository(database_path)
    discord_repository = DiscordMessageRepository(database_path)
    coordinator = RollProjectionCoordinator(catalog_repository, discord_repository)
    aggregate_key = MessageAggregateKey(
        SourcePlatform.DISCORD, "guild", "channel", "message"
    )
    received = discord_repository.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(
            aggregate_key, "payload-hash", "revision-1"
        ),
        event_key="event",
        event_kind="message_create",
        raw_text=ROLL_MESSAGE,
        payload_json='{"content":"roll"}',
        payload_capture_version="capture-1",
        source_observed_at=OBSERVED_AT,
        received_at=OBSERVED_AT,
    )
    discord_repository.record_server_attribution(
        received.source_event_id,
        status="resolved",
        server_name="Lake",
        recorded_at=OBSERVED_AT,
    )
    discord_repository.record_account_attribution(
        received.source_event_id,
        status="resolved",
        server_name="Lake",
        account_name="ernieuuu",
        recorded_at=OBSERVED_AT,
    )
    attempt = discord_repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OBSERVED_AT,
    )
    service = AutomaticImportService(
        CatalogService(catalog_repository),
        roll_projection_coordinator=coordinator,
    )
    return service, received.source_event_id, attempt.attempt_id


def _durable_profile_importer(tmp_path):
    database_path = tmp_path / "durable-profile.db"
    catalog_repository = CatalogRepository(database_path)
    discord_repository = DiscordMessageRepository(database_path)
    coordinator = ProfileProjectionCoordinator(catalog_repository, discord_repository)
    aggregate_key = MessageAggregateKey(
        SourcePlatform.DISCORD, "guild", "channel", "profile-message"
    )
    received = discord_repository.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(
            aggregate_key, "profile-payload-hash", "revision-1"
        ),
        event_key="profile-event",
        event_kind="message_create",
        raw_text=PROFILE_MESSAGE,
        payload_json='{"content":"profile"}',
        payload_capture_version="capture-1",
        source_observed_at=OBSERVED_AT,
        received_at=OBSERVED_AT,
    )
    discord_repository.record_server_attribution(
        received.source_event_id,
        status="resolved",
        server_name="Lake",
        recorded_at=OBSERVED_AT,
    )
    discord_repository.record_account_attribution(
        received.source_event_id,
        status="resolved",
        server_name="Lake",
        account_name="moa",
        recorded_at=OBSERVED_AT,
    )
    attempt = discord_repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OBSERVED_AT,
    )
    service = AutomaticImportService(
        CatalogService(catalog_repository),
        profile_projection_coordinator=coordinator,
    )
    return service, received.source_event_id, attempt.attempt_id


def _durable_claim_importer(tmp_path):
    database_path = tmp_path / "durable-claim.db"
    catalog_repository = CatalogRepository(database_path)
    discord_repository = DiscordMessageRepository(database_path)
    coordinator = ClaimProjectionCoordinator(catalog_repository, discord_repository)
    aggregate_key = MessageAggregateKey(
        SourcePlatform.DISCORD, "guild", "channel", "claim-message"
    )
    received = discord_repository.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(
            aggregate_key, "claim-payload-hash", "revision-1"
        ),
        event_key="claim-event",
        event_kind="message_create",
        raw_text=CLAIM_MESSAGE,
        payload_json='{"content":"claim"}',
        payload_capture_version="capture-1",
        source_observed_at=OBSERVED_AT,
        received_at=OBSERVED_AT,
    )
    discord_repository.record_server_attribution(
        received.source_event_id,
        status="resolved",
        server_name="Lake",
        recorded_at=OBSERVED_AT,
    )
    discord_repository.record_account_attribution(
        received.source_event_id,
        status="resolved",
        server_name="Lake",
        account_name="ernieuuu",
        recorded_at=OBSERVED_AT,
    )
    attempt = discord_repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OBSERVED_AT,
    )
    service = AutomaticImportService(
        CatalogService(catalog_repository),
        claim_projection_coordinator=coordinator,
    )
    return service, received.source_event_id, attempt.attempt_id


def _durable_settings_importer(tmp_path):
    database_path = tmp_path / "durable-settings.db"
    catalog_repository = CatalogRepository(database_path)
    discord_repository = DiscordMessageRepository(database_path)
    coordinator = SettingsProjectionCoordinator(catalog_repository, discord_repository)
    aggregate_key = MessageAggregateKey(
        SourcePlatform.DISCORD, "guild", "channel", "settings-message"
    )
    received = discord_repository.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(
            aggregate_key, "settings-payload-hash", "revision-1"
        ),
        event_key="settings-event",
        event_kind="message_create",
        raw_text=SETTINGS_MESSAGE,
        payload_json='{"content":"settings payload"}',
        payload_capture_version="capture-1",
        source_observed_at=OBSERVED_AT,
        received_at=OBSERVED_AT,
    )
    discord_repository.record_server_attribution(
        received.source_event_id,
        status="resolved",
        server_name="Lake",
        recorded_at=OBSERVED_AT,
    )
    attempt = discord_repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OBSERVED_AT,
    )
    parser = Mock()
    parser.parse_server_settings.return_value = SETTINGS
    service = AutomaticImportService(
        CatalogService(catalog_repository),
        parser=parser,
        settings_projection_coordinator=coordinator,
    )
    return service, parser, received.source_event_id, attempt.attempt_id


def _durable_infokl_importer(tmp_path):
    database_path = tmp_path / "durable-infokl.db"
    catalog_repository = CatalogRepository(database_path)
    discord_repository = DiscordMessageRepository(database_path)
    coordinator = InfoklProjectionCoordinator(catalog_repository, discord_repository)
    aggregate_key = MessageAggregateKey(
        SourcePlatform.DISCORD, "guild", "channel", "infokl-message"
    )
    received = discord_repository.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(
            aggregate_key, "infokl-payload-hash", "revision-1"
        ),
        event_key="infokl-event",
        event_kind="message_create",
        raw_text=INFOKL_MESSAGE,
        payload_json='{"content":"infokl payload"}',
        payload_capture_version="capture-1",
        source_observed_at=OBSERVED_AT,
        received_at=OBSERVED_AT,
    )
    discord_repository.record_server_attribution(
        received.source_event_id,
        status="resolved",
        server_name="Lake",
        recorded_at=OBSERVED_AT,
    )
    attempt = discord_repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OBSERVED_AT,
    )
    parser = Mock()
    parser.parse_kakeraloot_settings.return_value = INFOKL_SETTINGS
    service = AutomaticImportService(
        CatalogService(catalog_repository),
        parser=parser,
        infokl_projection_coordinator=coordinator,
    )
    return service, parser, received.source_event_id, attempt.attempt_id


def _durable_timer_importer(tmp_path):
    database_path = tmp_path / "durable-timer.db"
    catalog_repository = CatalogRepository(database_path)
    discord_repository = DiscordMessageRepository(database_path)
    coordinator = TimerProjectionCoordinator(catalog_repository, discord_repository)
    aggregate_key = MessageAggregateKey(
        SourcePlatform.DISCORD, "guild", "channel", "timer-message"
    )
    received = discord_repository.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(
            aggregate_key, "timer-payload-hash", "revision-1"
        ),
        event_key="timer-event",
        event_kind="message_create",
        raw_text=TIMER_MESSAGE,
        payload_json='{"content":"timer payload"}',
        payload_capture_version="capture-1",
        source_observed_at=OBSERVED_AT,
        received_at=OBSERVED_AT,
    )
    discord_repository.record_server_attribution(
        received.source_event_id,
        status="resolved",
        server_name="Lake",
        recorded_at=OBSERVED_AT,
    )
    discord_repository.record_account_attribution(
        received.source_event_id,
        status="resolved",
        server_name="Lake",
        account_name="ernieuuu",
        recorded_at=OBSERVED_AT,
    )
    attempt = discord_repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OBSERVED_AT,
    )
    service = AutomaticImportService(
        CatalogService(catalog_repository),
        timer_projection_coordinator=coordinator,
    )
    return database_path, service, received.source_event_id, attempt.attempt_id


def _durable_kakera_importer(tmp_path):
    database_path = tmp_path / "durable-kakera.db"
    catalog_repository = CatalogRepository(database_path)
    discord_repository = DiscordMessageRepository(database_path)
    coordinator = KakeraStateProjectionCoordinator(catalog_repository, discord_repository)
    aggregate_key = MessageAggregateKey(
        SourcePlatform.DISCORD, "guild", "channel", "kakera-message"
    )
    received = discord_repository.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(
            aggregate_key, "kakera-payload-hash", "revision-1"
        ),
        event_key="kakera-event",
        event_kind="message_create",
        raw_text=KAKERA_MESSAGE,
        payload_json='{"content":"kakera payload"}',
        payload_capture_version="capture-1",
        source_observed_at=OBSERVED_AT,
        received_at=OBSERVED_AT,
    )
    discord_repository.record_server_attribution(
        received.source_event_id,
        status="resolved",
        server_name="Lake",
        recorded_at=OBSERVED_AT,
    )
    discord_repository.record_account_attribution(
        received.source_event_id,
        status="resolved",
        server_name="Lake",
        account_name="ernieuuu",
        recorded_at=OBSERVED_AT,
    )
    attempt = discord_repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OBSERVED_AT,
    )
    service = AutomaticImportService(
        CatalogService(catalog_repository),
        kakera_state_projection_coordinator=coordinator,
    )
    return database_path, service, received.source_event_id, attempt.attempt_id


def _durable_mudapins_importer(tmp_path):
    database_path = tmp_path / "durable-mudapins.db"
    catalog_repository = CatalogRepository(database_path)
    discord_repository = DiscordMessageRepository(database_path)
    coordinator = MudapinsProjectionCoordinator(catalog_repository, discord_repository)
    aggregate_key = MessageAggregateKey(
        SourcePlatform.DISCORD, "guild", "channel", "mudapins-message"
    )
    received = discord_repository.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(
            aggregate_key, "mudapins-payload-hash", "revision-1"
        ),
        event_key="mudapins-event",
        event_kind="message_create",
        raw_text=MUDAPINS_MESSAGE,
        payload_json='{"content":"mudapins payload"}',
        payload_capture_version="capture-1",
        source_observed_at=OBSERVED_AT,
        received_at=OBSERVED_AT,
    )
    discord_repository.record_server_attribution(
        received.source_event_id,
        status="resolved",
        server_name="Lake",
        recorded_at=OBSERVED_AT,
    )
    discord_repository.record_account_attribution(
        received.source_event_id,
        status="resolved",
        server_name="Lake",
        account_name="ernieuuu",
        recorded_at=OBSERVED_AT,
    )
    attempt = discord_repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OBSERVED_AT,
    )
    service = AutomaticImportService(
        CatalogService(catalog_repository),
        mudapins_projection_coordinator=coordinator,
    )
    return database_path, service, received.source_event_id, attempt.attempt_id


def test_automatic_import_routes_top_pages_without_server_context(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = "TOP 1000\n#1 - Hatsune Miku - VOCALOID\nPage 1 / 67"

    result = service.import_message(message, "test")

    assert result.kind == "top"
    assert result.imported_count == 1
    assert catalog.character_count() == 1


def test_automatic_import_requires_context_only_for_account_scoped_messages(tmp_path) -> None:
    service = AutomaticImportService(CatalogService(CatalogRepository(tmp_path / "catalog.db")))
    message = "ernieuuu, you can claim right now! The next claim reset is in 2h 32 min."

    with pytest.raises(ValueError, match="--server"):
        service.import_message(message, "test")


def test_automatic_import_observes_help_and_tutorial_without_creating_characters(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)

    help_result = service.import_message("Looking for a specific command? Try $search", "test")
    tutorial_result = service.import_message("2/17 - Tutorial\nReward: +200:kakera:", "test")

    assert help_result.kind == "help"
    assert tutorial_result.kind == "tutorial"
    assert help_result.imported_count == tutorial_result.imported_count == 0
    assert catalog.character_count() == 0


def test_automatic_import_persists_kakeraloot_purchase_guard_as_empty_state(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = (
        "You need to buy kakeraloots before using this command ($kl)\n"
        "Type $infokl to get more infos about kakeraloots."
    )

    result = service.import_message(message, "test", "Lake", "cute_beagle_91130")
    state = catalog.kakeraloot_state("Lake", "cute_beagle_91130")

    assert result.kind == "lootstate"
    assert result.imported_count == 1
    assert state is not None
    assert not state.has_kakeraloots


def test_automatic_import_persists_kakeraloot_prerequisite_guard_as_empty_state(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = "Prerequisites: Sapphire I + Ruby I + Emerald I ($infokl)"

    result = service.import_message(message, "test", "Lake", "cute_beagle_91130")
    state = catalog.kakeraloot_state("Lake", "cute_beagle_91130")

    assert result.kind == "lootstate"
    assert result.imported_count == 1
    assert state is not None
    assert not state.has_kakeraloots


def test_automatic_import_persists_rankless_rolls_for_future_history(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = "Hips\nDekoboko Majo no Oyako Jijou\n30:kakera:"

    result = service.import_message(message, "test", "Lake", "ernieuuu")
    rolls = catalog.recent_rolls("Lake", "ernieuuu")

    assert result.kind == "roll"
    assert result.imported_count == 1
    assert rolls[0].character.name == "Hips"
    assert rolls[0].claim_rank is None


def test_automatic_import_non_durable_kakera_keeps_catalog_path_and_neutral_result() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_kakera_state.return_value = KAKERA_STATE
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        kakera_state_projection_coordinator=coordinator,
    )

    result = service.import_message(
        KAKERA_MESSAGE,
        "clipboard",
        "Lake",
        "ernieuuu",
        detected_kind="kakera",
    )

    parser.parse_kakera_state.assert_called_once_with(KAKERA_MESSAGE)
    catalog.import_kakera_state.assert_called_once_with(
        KAKERA_STATE,
        "Lake",
        "ernieuuu",
        KAKERA_MESSAGE,
        "clipboard",
    )
    coordinator.coordinate_kakera_state.assert_not_called()
    assert result.kind == "kakera"
    assert result.imported_count == len(KAKERA_STATE.badges)
    assert result.import_event_id is None
    assert result.replay_skipped is False
    assert result.durable_success_recorded is False


def test_automatic_import_durable_kakera_delegates_once_with_all_context() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_kakera_state.return_value = KAKERA_STATE
    coordinator = Mock()
    coordinator.coordinate_kakera_state.return_value = KakeraStateProjectionResult(
        imported_count=1,
        import_event_id=92,
        kakera_state_observation_id=93,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("kakera_state_observations", 93),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        kakera_state_projection_coordinator=coordinator,
    )
    context = DurableKakeraImportContext(
        source_event_id=81,
        attempt_id=83,
        server="Lake",
        account="ernieuuu",
        raw="persisted kakera payload",
        source="discord:message",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )

    result = service.import_message(
        KAKERA_MESSAGE,
        "caller-source",
        detected_kind="kakera",
        durable_kakera_context=context,
    )

    parser.parse_kakera_state.assert_called_once_with(KAKERA_MESSAGE)
    coordinator.coordinate_kakera_state.assert_called_once_with(
        source_event_id=81,
        attempt_id=83,
        state=KAKERA_STATE,
        server="Lake",
        account="ernieuuu",
        raw="persisted kakera payload",
        source="discord:message",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )
    catalog.import_kakera_state.assert_not_called()
    assert result.kind == "kakera"
    assert result.imported_count == 1
    assert result.import_event_id == 92
    assert result.replay_skipped is False
    assert result.durable_success_recorded is True


@pytest.mark.parametrize("state", (KAKERA_STATE, ZERO_KAKERA_STATE))
def test_automatic_import_durable_kakera_preserves_snapshot_values(state) -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_kakera_state.return_value = state
    coordinator = Mock()
    coordinator.coordinate_kakera_state.return_value = KakeraStateProjectionResult(
        imported_count=1,
        import_event_id=92,
        kakera_state_observation_id=93,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("kakera_state_observations", 93),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        kakera_state_projection_coordinator=coordinator,
    )

    service.import_message(
        KAKERA_MESSAGE,
        "discord",
        detected_kind="kakera",
        durable_kakera_context=DurableKakeraImportContext(
            source_event_id=81,
            attempt_id=83,
            server="Lake",
            account="ernieuuu",
            raw=KAKERA_MESSAGE,
            source="discord",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        ),
    )

    assert coordinator.coordinate_kakera_state.call_args.kwargs["state"] is state


def test_automatic_import_durable_kakera_requires_coordinator_before_catalog_write() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_kakera_state.return_value = KAKERA_STATE
    service = AutomaticImportService(catalog, parser=parser, router=Mock())

    with pytest.raises(RuntimeError, match="KakeraStateProjectionCoordinator"):
        service.import_message(
            KAKERA_MESSAGE,
            "discord",
            detected_kind="kakera",
            durable_kakera_context=DurableKakeraImportContext(
                source_event_id=81,
                attempt_id=83,
                server="Lake",
                account="ernieuuu",
                raw=KAKERA_MESSAGE,
                source="discord",
                observed_at=OBSERVED_AT,
                finished_at=FINISHED_AT,
            ),
        )

    parser.parse_kakera_state.assert_called_once_with(KAKERA_MESSAGE)
    catalog.import_kakera_state.assert_not_called()


def test_automatic_import_durable_kakera_propagates_coordinator_error_without_fallback() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_kakera_state.return_value = KAKERA_STATE
    coordinator = Mock()
    coordinator.coordinate_kakera_state.side_effect = RuntimeError("coordinator failed")
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        kakera_state_projection_coordinator=coordinator,
    )

    with pytest.raises(RuntimeError, match="coordinator failed"):
        service.import_message(
            KAKERA_MESSAGE,
            "discord",
            detected_kind="kakera",
            durable_kakera_context=DurableKakeraImportContext(
                source_event_id=81,
                attempt_id=83,
                server="Lake",
                account="ernieuuu",
                raw=KAKERA_MESSAGE,
                source="discord",
                observed_at=OBSERVED_AT,
                finished_at=FINISHED_AT,
            ),
        )

    parser.parse_kakera_state.assert_called_once_with(KAKERA_MESSAGE)
    catalog.import_kakera_state.assert_not_called()


def test_automatic_import_durable_kakera_parse_failure_precedes_any_write() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_kakera_state.side_effect = ValueError("invalid Kakera response")
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        kakera_state_projection_coordinator=coordinator,
    )

    with pytest.raises(ValueError, match="invalid Kakera response"):
        service.import_message(
            KAKERA_MESSAGE,
            "discord",
            detected_kind="kakera",
            durable_kakera_context=DurableKakeraImportContext(
                source_event_id=81,
                attempt_id=83,
                server="Lake",
                account="ernieuuu",
                raw=KAKERA_MESSAGE,
                source="discord",
                observed_at=OBSERVED_AT,
                finished_at=FINISHED_AT,
            ),
        )

    coordinator.coordinate_kakera_state.assert_not_called()
    catalog.import_kakera_state.assert_not_called()


def test_automatic_import_real_durable_kakera_first_processing_and_replay_are_atomic(tmp_path) -> None:
    database_path, service, source_event_id, attempt_id = _durable_kakera_importer(tmp_path)
    context = DurableKakeraImportContext(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        server="Lake",
        account="ernieuuu",
        raw=KAKERA_MESSAGE,
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )

    first = service.import_message(
        KAKERA_MESSAGE,
        "caller-source",
        detected_kind="kakera",
        durable_kakera_context=context,
    )

    assert first.kind == "kakera"
    assert first.imported_count == 1
    assert first.import_event_id is not None
    assert first.replay_skipped is False
    assert first.durable_success_recorded is True
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM kakera_state_observations").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_source_event_server_attributions"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_source_event_account_attributions"
        ).fetchone()[0] == 1
        link = connection.execute(
            "SELECT projection_table, projection_row_id, state "
            "FROM discord_projection_links"
        ).fetchone()
        assert tuple(link) == (
            "kakera_state_observations",
            connection.execute("SELECT id FROM kakera_state_observations").fetchone()[0],
            "completed",
        )
        assert tuple(
            connection.execute(
                "SELECT status, legacy_import_event_id FROM discord_source_events"
            ).fetchone()
        ) == ("succeeded", first.import_event_id)
        assert connection.execute(
            "SELECT status FROM discord_processing_attempts"
        ).fetchone()[0] == "succeeded"
        before_replay = tuple(
            connection.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM import_events), "
                "(SELECT COUNT(*) FROM kakera_state_observations), "
                "(SELECT COUNT(*) FROM discord_projection_links), "
                "(SELECT COUNT(*) FROM server_contexts), "
                "(SELECT COUNT(*) FROM account_contexts), "
                "(SELECT COUNT(*) FROM discord_processing_attempts), "
                "(SELECT COUNT(*) FROM discord_source_event_server_attributions), "
                "(SELECT COUNT(*) FROM discord_source_event_account_attributions)"
            ).fetchone()
        )

    replay = service.import_message(
        KAKERA_MESSAGE,
        "caller-source",
        detected_kind="kakera",
        durable_kakera_context=DurableKakeraImportContext(
            source_event_id=source_event_id,
            attempt_id=None,
            server="Lake",
            account="ernieuuu",
            raw=KAKERA_MESSAGE,
            source="discord",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        ),
    )

    assert replay.kind == "kakera"
    assert replay.imported_count == 0
    assert replay.import_event_id == first.import_event_id
    assert replay.replay_skipped is True
    assert replay.durable_success_recorded is True
    with sqlite3.connect(database_path) as connection:
        after_replay = tuple(
            connection.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM import_events), "
                "(SELECT COUNT(*) FROM kakera_state_observations), "
                "(SELECT COUNT(*) FROM discord_projection_links), "
                "(SELECT COUNT(*) FROM server_contexts), "
                "(SELECT COUNT(*) FROM account_contexts), "
                "(SELECT COUNT(*) FROM discord_processing_attempts), "
                "(SELECT COUNT(*) FROM discord_source_event_server_attributions), "
                "(SELECT COUNT(*) FROM discord_source_event_account_attributions)"
            ).fetchone()
        )
    assert after_replay == before_replay


def test_automatic_import_durable_roll_delegates_once_with_all_context(tmp_path) -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_roll.return_value = RollObservation(
        name="Hips",
        series="Dekoboko Majo no Oyako Jijou",
        claim_rank=None,
        kakera_value=30,
    )
    router = Mock()
    coordinator = Mock()
    coordinator.coordinate_roll.return_value = RollProjectionResult(
        imported_count=1,
        import_event_id=42,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_targets=(),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=router,
        roll_projection_coordinator=coordinator,
    )
    context = DurableRollImportContext(
        source_event_id=17,
        attempt_id=19,
        finished_at=FINISHED_AT,
    )

    result = service.import_message(
        ROLL_MESSAGE,
        "discord:message",
        " Lake ",
        " ernieuuu ",
        detected_kind="roll",
        observed_at=OBSERVED_AT,
        durable_roll_context=context,
    )

    parser.parse_roll.assert_called_once_with(ROLL_MESSAGE)
    coordinator.coordinate_roll.assert_called_once_with(
        source_event_id=17,
        attempt_id=19,
        roll=parser.parse_roll.return_value,
        server="Lake",
        account="ernieuuu",
        raw=ROLL_MESSAGE,
        source="discord:message",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )
    catalog.import_roll.assert_not_called()
    assert result.imported_count == 1
    assert result.import_event_id == 42
    assert result.replay_skipped is False
    assert result.durable_success_recorded is True


def test_automatic_import_durable_roll_maps_completed_replay(tmp_path) -> None:
    service, source_event_id, attempt_id = _durable_roll_importer(tmp_path)
    first = service.import_message(
        ROLL_MESSAGE,
        "discord",
        "Lake",
        "ernieuuu",
        detected_kind="roll",
        observed_at=OBSERVED_AT,
        durable_roll_context=DurableRollImportContext(
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            finished_at=FINISHED_AT,
        ),
    )

    replay = service.import_message(
        ROLL_MESSAGE,
        "discord",
        "Lake",
        "ernieuuu",
        detected_kind="roll",
        observed_at=OBSERVED_AT,
        durable_roll_context=DurableRollImportContext(
            source_event_id=source_event_id,
            attempt_id=None,
            finished_at=FINISHED_AT,
        ),
    )

    assert first.imported_count == 1
    assert first.import_event_id is not None
    assert replay.imported_count == 0
    assert replay.import_event_id == first.import_event_id
    assert replay.replay_skipped is True
    assert replay.durable_success_recorded is True


def test_automatic_import_durable_roll_propagates_coordinator_error_without_fallback() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_roll.return_value = RollObservation(
        name="Hips", series="Series", claim_rank=None, kakera_value=30
    )
    coordinator = Mock()
    coordinator.coordinate_roll.side_effect = RuntimeError("coordinator failed")
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        roll_projection_coordinator=coordinator,
    )

    with pytest.raises(RuntimeError, match="coordinator failed"):
        service.import_message(
            ROLL_MESSAGE,
            "discord",
            "Lake",
            "ernieuuu",
            detected_kind="roll",
            durable_roll_context=DurableRollImportContext(
                source_event_id=17,
                attempt_id=19,
                finished_at=FINISHED_AT,
            ),
        )

    catalog.import_roll.assert_not_called()


def test_automatic_import_durable_context_requires_coordinator_before_catalog_write(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)

    with pytest.raises(RuntimeError, match="RollProjectionCoordinator"):
        service.import_message(
            ROLL_MESSAGE,
            "discord",
            "Lake",
            "ernieuuu",
            detected_kind="roll",
            durable_roll_context=DurableRollImportContext(
                source_event_id=17,
                attempt_id=19,
                finished_at=FINISHED_AT,
            ),
        )

    assert catalog.character_count() == 0
    with sqlite3.connect(tmp_path / "catalog.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0


def test_automatic_import_non_durable_roll_keeps_catalog_path_and_neutral_result() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_roll.return_value = RollObservation(
        name="Hips", series="Series", claim_rank=None, kakera_value=30
    )
    service = AutomaticImportService(catalog, parser=parser, router=Mock())

    result = service.import_message(
        ROLL_MESSAGE,
        "clipboard",
        "Lake",
        "ernieuuu",
        detected_kind="roll",
    )

    catalog.import_roll.assert_called_once_with(
        parser.parse_roll.return_value,
        "Lake",
        "ernieuuu",
        ROLL_MESSAGE,
        "clipboard",
    )
    assert result.imported_count == 1
    assert result.import_event_id is None
    assert result.replay_skipped is False
    assert result.durable_success_recorded is False


def test_automatic_import_non_durable_timer_keeps_catalog_path_and_neutral_result() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_timer_state.return_value = TIMER_STATE
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        timer_projection_coordinator=coordinator,
    )

    result = service.import_message(
        TIMER_MESSAGE,
        "clipboard",
        "Lake",
        "ernieuuu",
        detected_kind="timers",
    )

    parser.parse_timer_state.assert_called_once_with(TIMER_MESSAGE)
    catalog.import_timer_state.assert_called_once_with(
        TIMER_STATE,
        "Lake",
        "ernieuuu",
        TIMER_MESSAGE,
        "clipboard",
    )
    coordinator.coordinate_timer_state.assert_not_called()
    assert result.kind == "timers"
    assert result.imported_count == 1
    assert result.import_event_id is None
    assert result.replay_skipped is False
    assert result.durable_success_recorded is False


def test_automatic_import_durable_timer_delegates_once_with_all_context() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_timer_state.return_value = TIMER_STATE
    coordinator = Mock()
    coordinator.coordinate_timer_state.return_value = TimerProjectionResult(
        imported_count=1,
        import_event_id=82,
        timer_state_observation_id=83,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("timer_state_observations", 83),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        timer_projection_coordinator=coordinator,
    )
    context = DurableTimerImportContext(
        source_event_id=71,
        attempt_id=73,
        server="Lake",
        account="ernieuuu",
        raw="persisted timer payload",
        source="discord:message",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )

    result = service.import_message(
        TIMER_MESSAGE,
        "caller-source",
        detected_kind="timers",
        durable_timer_context=context,
    )

    parser.parse_timer_state.assert_called_once_with(TIMER_MESSAGE)
    coordinator.coordinate_timer_state.assert_called_once_with(
        source_event_id=71,
        attempt_id=73,
        state=TIMER_STATE,
        server="Lake",
        account="ernieuuu",
        raw="persisted timer payload",
        source="discord:message",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )
    catalog.import_timer_state.assert_not_called()
    assert result.kind == "timers"
    assert result.imported_count == 1
    assert result.import_event_id == 82
    assert result.replay_skipped is False
    assert result.durable_success_recorded is True


def test_automatic_import_durable_timer_requires_coordinator_before_catalog_write() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_timer_state.return_value = TIMER_STATE
    service = AutomaticImportService(catalog, parser=parser, router=Mock())
    context = DurableTimerImportContext(
        source_event_id=71,
        attempt_id=73,
        server="Lake",
        account="ernieuuu",
        raw=TIMER_MESSAGE,
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )

    with pytest.raises(RuntimeError, match="TimerProjectionCoordinator"):
        service.import_message(
            TIMER_MESSAGE,
            "caller-source",
            detected_kind="timers",
            durable_timer_context=context,
        )

    parser.parse_timer_state.assert_called_once_with(TIMER_MESSAGE)
    catalog.import_timer_state.assert_not_called()


def test_automatic_import_durable_timer_propagates_coordinator_error_without_fallback() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_timer_state.return_value = TIMER_STATE
    coordinator = Mock()
    coordinator.coordinate_timer_state.side_effect = RuntimeError("coordinator failed")
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        timer_projection_coordinator=coordinator,
    )

    with pytest.raises(RuntimeError, match="coordinator failed"):
        service.import_message(
            TIMER_MESSAGE,
            "caller-source",
            detected_kind="timers",
            durable_timer_context=DurableTimerImportContext(
                source_event_id=71,
                attempt_id=73,
                server="Lake",
                account="ernieuuu",
                raw=TIMER_MESSAGE,
                source="discord",
                observed_at=OBSERVED_AT,
                finished_at=FINISHED_AT,
            ),
        )

    parser.parse_timer_state.assert_called_once_with(TIMER_MESSAGE)
    catalog.import_timer_state.assert_not_called()


def test_automatic_import_durable_timer_parse_failure_precedes_any_write() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_timer_state.side_effect = ValueError("invalid timer response")
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        timer_projection_coordinator=coordinator,
    )

    with pytest.raises(ValueError, match="invalid timer response"):
        service.import_message(
            TIMER_MESSAGE,
            "caller-source",
            detected_kind="timers",
            durable_timer_context=DurableTimerImportContext(
                source_event_id=71,
                attempt_id=73,
                server="Lake",
                account="ernieuuu",
                raw=TIMER_MESSAGE,
                source="discord",
                observed_at=OBSERVED_AT,
                finished_at=FINISHED_AT,
            ),
        )

    coordinator.coordinate_timer_state.assert_not_called()
    catalog.import_timer_state.assert_not_called()


def test_automatic_import_real_durable_timer_first_processing_and_replay_are_atomic(tmp_path) -> None:
    database_path, service, source_event_id, attempt_id = _durable_timer_importer(tmp_path)
    context = DurableTimerImportContext(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        server="Lake",
        account="ernieuuu",
        raw=TIMER_MESSAGE,
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )

    first = service.import_message(
        TIMER_MESSAGE,
        "caller-source",
        detected_kind="timers",
        durable_timer_context=context,
    )

    assert first.kind == "timers"
    assert first.imported_count == 1
    assert first.import_event_id is not None
    assert first.replay_skipped is False
    assert first.durable_success_recorded is True
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM timer_state_observations").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 1
        link = connection.execute(
            "SELECT projection_table, projection_row_id, state "
            "FROM discord_projection_links"
        ).fetchone()
        assert tuple(link) == (
            "timer_state_observations",
            connection.execute("SELECT id FROM timer_state_observations").fetchone()[0],
            "completed",
        )
        assert tuple(
            connection.execute(
                "SELECT status, legacy_import_event_id FROM discord_source_events"
            ).fetchone()
        ) == ("succeeded", first.import_event_id)
        assert connection.execute(
            "SELECT status FROM discord_processing_attempts"
        ).fetchone()[0] == "succeeded"
        before_replay = tuple(
            connection.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM import_events), "
                "(SELECT COUNT(*) FROM timer_state_observations), "
                "(SELECT COUNT(*) FROM discord_projection_links), "
                "(SELECT COUNT(*) FROM server_contexts), "
                "(SELECT COUNT(*) FROM account_contexts), "
                "(SELECT COUNT(*) FROM discord_processing_attempts)"
            ).fetchone()
        )

    replay = service.import_message(
        TIMER_MESSAGE,
        "caller-source",
        detected_kind="timers",
        durable_timer_context=DurableTimerImportContext(
            source_event_id=source_event_id,
            attempt_id=None,
            server="Lake",
            account="ernieuuu",
            raw=TIMER_MESSAGE,
            source="discord",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        ),
    )

    assert replay.kind == "timers"
    assert replay.imported_count == 0
    assert replay.import_event_id == first.import_event_id
    assert replay.replay_skipped is True
    assert replay.durable_success_recorded is True
    with sqlite3.connect(database_path) as connection:
        after_replay = tuple(
            connection.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM import_events), "
                "(SELECT COUNT(*) FROM timer_state_observations), "
                "(SELECT COUNT(*) FROM discord_projection_links), "
                "(SELECT COUNT(*) FROM server_contexts), "
                "(SELECT COUNT(*) FROM account_contexts), "
                "(SELECT COUNT(*) FROM discord_processing_attempts)"
            ).fetchone()
        )
    assert after_replay == before_replay


def test_automatic_import_durable_settings_delegates_once_with_all_context() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_server_settings.return_value = SETTINGS
    coordinator = Mock()
    coordinator.coordinate_settings.return_value = SettingsProjectionResult(
        imported_count=2,
        import_event_id=62,
        server_settings_observation_id=63,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("server_settings_observations", 63),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        settings_projection_coordinator=coordinator,
    )
    context = DurableSettingsImportContext(
        source_event_id=41,
        attempt_id=43,
        finished_at=FINISHED_AT,
    )

    result = service.import_message(
        SETTINGS_MESSAGE,
        "discord:message",
        " Lake ",
        detected_kind="settings",
        observed_at=OBSERVED_AT,
        durable_settings_context=context,
    )

    parser.parse_server_settings.assert_called_once_with(SETTINGS_MESSAGE)
    assert coordinator.method_calls == [
        call.coordinate_settings(
            source_event_id=41,
            attempt_id=43,
            settings=SETTINGS,
            server="Lake",
            raw=SETTINGS_MESSAGE,
            source="discord:message",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        )
    ]
    catalog.import_server_settings.assert_not_called()
    assert result.imported_count == 2
    assert result.import_event_id == 62
    assert result.replay_skipped is False
    assert result.durable_success_recorded is True


def test_automatic_import_durable_settings_maps_completed_replay(tmp_path) -> None:
    service, parser, source_event_id, attempt_id = _durable_settings_importer(tmp_path)
    first = service.import_message(
        SETTINGS_MESSAGE,
        "discord",
        "Lake",
        detected_kind="settings",
        observed_at=OBSERVED_AT,
        durable_settings_context=DurableSettingsImportContext(
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            finished_at=FINISHED_AT,
        ),
    )

    replay = service.import_message(
        SETTINGS_MESSAGE,
        "discord",
        "Lake",
        detected_kind="settings",
        observed_at=OBSERVED_AT,
        durable_settings_context=DurableSettingsImportContext(
            source_event_id=source_event_id,
            attempt_id=None,
            finished_at=FINISHED_AT,
        ),
    )

    parser.parse_server_settings.assert_has_calls([call(SETTINGS_MESSAGE), call(SETTINGS_MESSAGE)])
    assert first.imported_count == 1
    assert first.import_event_id is not None
    assert first.replay_skipped is False
    assert first.durable_success_recorded is True
    assert replay.imported_count == 0
    assert replay.import_event_id == first.import_event_id
    assert replay.replay_skipped is True
    assert replay.durable_success_recorded is True


def test_automatic_import_durable_settings_propagates_coordinator_error_without_fallback() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_server_settings.return_value = SETTINGS
    coordinator = Mock()
    coordinator.coordinate_settings.side_effect = RuntimeError("coordinator failed")
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        settings_projection_coordinator=coordinator,
    )

    with pytest.raises(RuntimeError, match="coordinator failed"):
        service.import_message(
            SETTINGS_MESSAGE,
            "discord",
            "Lake",
            detected_kind="settings",
            durable_settings_context=DurableSettingsImportContext(
                source_event_id=41,
                attempt_id=43,
                finished_at=FINISHED_AT,
            ),
        )

    parser.parse_server_settings.assert_called_once_with(SETTINGS_MESSAGE)
    catalog.import_server_settings.assert_not_called()


def test_automatic_import_durable_settings_requires_coordinator_before_catalog_write(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    parser = Mock()
    parser.parse_server_settings.return_value = SETTINGS
    service = AutomaticImportService(catalog, parser=parser, router=Mock())

    with pytest.raises(RuntimeError, match="SettingsProjectionCoordinator"):
        service.import_message(
            SETTINGS_MESSAGE,
            "discord",
            "Lake",
            detected_kind="settings",
            durable_settings_context=DurableSettingsImportContext(
                source_event_id=41,
                attempt_id=43,
                finished_at=FINISHED_AT,
            ),
        )

    parser.parse_server_settings.assert_called_once_with(SETTINGS_MESSAGE)
    with sqlite3.connect(tmp_path / "catalog.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0


def test_automatic_import_non_durable_settings_keeps_catalog_path_and_neutral_result() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_server_settings.return_value = SETTINGS
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        settings_projection_coordinator=coordinator,
    )

    result = service.import_message(
        SETTINGS_MESSAGE,
        "clipboard",
        "Lake",
        detected_kind="settings",
    )

    parser.parse_server_settings.assert_called_once_with(SETTINGS_MESSAGE)
    catalog.import_server_settings.assert_called_once_with(
        SETTINGS,
        "Lake",
        SETTINGS_MESSAGE,
        "clipboard",
    )
    coordinator.coordinate_settings.assert_not_called()
    assert result.imported_count == len(SETTINGS.metrics)
    assert result.import_event_id is None
    assert result.replay_skipped is False
    assert result.durable_success_recorded is False


def test_automatic_import_settings_context_does_not_affect_non_settings_routes() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_roll.return_value = RollObservation(
        name="Hips", series="Series", claim_rank=None, kakera_value=30
    )
    parser.parse_profile.return_value = PROFILE
    parser.parse_claim_confirmation.return_value = ClaimConfirmation(
        account_name="ernieuuu", character_name="Parsed Character"
    )
    settings_coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        settings_projection_coordinator=settings_coordinator,
    )
    context = DurableSettingsImportContext(
        source_event_id=41,
        attempt_id=43,
        finished_at=FINISHED_AT,
    )

    service.import_message(
        ROLL_MESSAGE,
        "clipboard",
        "Lake",
        "ernieuuu",
        detected_kind="roll",
        durable_settings_context=context,
    )
    service.import_message(
        PROFILE_MESSAGE,
        "clipboard",
        "Lake",
        "ernieuuu",
        detected_kind="profile",
        durable_settings_context=context,
    )
    service.import_message(
        CLAIM_MESSAGE,
        "clipboard",
        "Lake",
        "ernieuuu",
        detected_kind="claim",
        durable_settings_context=context,
    )
    result = service.import_message(
        "Looking for a specific command? Try $search",
        "clipboard",
        detected_kind="help",
        durable_settings_context=context,
    )

    catalog.import_roll.assert_called_once()
    catalog.import_profile.assert_called_once()
    catalog.import_claim.assert_called_once()
    settings_coordinator.coordinate_settings.assert_not_called()
    assert result.kind == "help"


def test_automatic_import_durable_infokl_delegates_once_with_all_context() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_kakeraloot_settings.return_value = INFOKL_SETTINGS
    coordinator = Mock()
    coordinator.coordinate_infokl.return_value = InfoklProjectionResult(
        imported_count=1,
        import_event_id=72,
        kakeraloot_settings_observation_id=73,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("kakeraloot_settings_observations", 73),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        infokl_projection_coordinator=coordinator,
    )
    context = DurableInfoklImportContext(
        source_event_id=51,
        attempt_id=53,
        finished_at=FINISHED_AT,
    )

    result = service.import_message(
        INFOKL_MESSAGE,
        "discord:message",
        " Lake ",
        detected_kind="infokl",
        observed_at=OBSERVED_AT,
        durable_infokl_context=context,
    )

    parser.parse_kakeraloot_settings.assert_called_once_with(INFOKL_MESSAGE)
    coordinator.coordinate_infokl.assert_called_once_with(
        source_event_id=51,
        attempt_id=53,
        settings=INFOKL_SETTINGS,
        server="Lake",
        raw=INFOKL_MESSAGE,
        source="discord:message",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )
    catalog.import_kakeraloot_settings.assert_not_called()
    assert result.imported_count == 1
    assert result.import_event_id == 72
    assert result.replay_skipped is False
    assert result.durable_success_recorded is True


def test_automatic_import_durable_infokl_maps_completed_replay(tmp_path) -> None:
    service, parser, source_event_id, attempt_id = _durable_infokl_importer(tmp_path)
    first = service.import_message(
        INFOKL_MESSAGE,
        "discord",
        "Lake",
        detected_kind="infokl",
        observed_at=OBSERVED_AT,
        durable_infokl_context=DurableInfoklImportContext(
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            finished_at=FINISHED_AT,
        ),
    )

    replay = service.import_message(
        INFOKL_MESSAGE,
        "discord",
        "Lake",
        detected_kind="infokl",
        observed_at=OBSERVED_AT,
        durable_infokl_context=DurableInfoklImportContext(
            source_event_id=source_event_id,
            attempt_id=None,
            finished_at=FINISHED_AT,
        ),
    )

    assert parser.parse_kakeraloot_settings.call_count == 2
    assert first.imported_count == 1
    assert first.import_event_id is not None
    assert first.replay_skipped is False
    assert first.durable_success_recorded is True
    assert replay.imported_count == 0
    assert replay.import_event_id == first.import_event_id
    assert replay.replay_skipped is True
    assert replay.durable_success_recorded is True


def test_automatic_import_durable_infokl_propagates_coordinator_error_without_fallback() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_kakeraloot_settings.return_value = INFOKL_SETTINGS
    coordinator = Mock()
    coordinator.coordinate_infokl.side_effect = RuntimeError("coordinator failed")
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        infokl_projection_coordinator=coordinator,
    )

    with pytest.raises(RuntimeError, match="coordinator failed"):
        service.import_message(
            INFOKL_MESSAGE,
            "discord",
            "Lake",
            detected_kind="infokl",
            durable_infokl_context=DurableInfoklImportContext(
                source_event_id=51,
                attempt_id=53,
                finished_at=FINISHED_AT,
            ),
        )

    parser.parse_kakeraloot_settings.assert_called_once_with(INFOKL_MESSAGE)
    catalog.import_kakeraloot_settings.assert_not_called()


def test_automatic_import_durable_infokl_requires_coordinator_before_catalog_write() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_kakeraloot_settings.return_value = INFOKL_SETTINGS
    service = AutomaticImportService(catalog, parser=parser, router=Mock())

    with pytest.raises(RuntimeError, match="InfoklProjectionCoordinator"):
        service.import_message(
            INFOKL_MESSAGE,
            "discord",
            "Lake",
            detected_kind="infokl",
            durable_infokl_context=DurableInfoklImportContext(
                source_event_id=51,
                attempt_id=53,
                finished_at=FINISHED_AT,
            ),
        )

    parser.parse_kakeraloot_settings.assert_called_once_with(INFOKL_MESSAGE)
    catalog.import_kakeraloot_settings.assert_not_called()


def test_automatic_import_non_durable_infokl_keeps_catalog_path_and_neutral_result() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_kakeraloot_settings.return_value = INFOKL_SETTINGS
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        infokl_projection_coordinator=coordinator,
    )

    result = service.import_message(
        INFOKL_MESSAGE,
        "clipboard",
        "Lake",
        detected_kind="infokl",
    )

    parser.parse_kakeraloot_settings.assert_called_once_with(INFOKL_MESSAGE)
    catalog.import_kakeraloot_settings.assert_called_once_with(
        INFOKL_SETTINGS,
        "Lake",
        INFOKL_MESSAGE,
        "clipboard",
    )
    coordinator.coordinate_infokl.assert_not_called()
    assert result.imported_count == 1
    assert result.import_event_id is None
    assert result.replay_skipped is False
    assert result.durable_success_recorded is False


def test_automatic_import_infokl_context_does_not_affect_other_routes() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_roll.return_value = RollObservation(
        name="Hips", series="Series", claim_rank=None, kakera_value=30
    )
    parser.parse_server_settings.return_value = SETTINGS
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        infokl_projection_coordinator=coordinator,
    )
    context = DurableInfoklImportContext(
        source_event_id=51,
        attempt_id=53,
        finished_at=FINISHED_AT,
    )

    roll_result = service.import_message(
        ROLL_MESSAGE,
        "clipboard",
        "Lake",
        "ernieuuu",
        detected_kind="roll",
        durable_infokl_context=context,
    )
    settings_result = service.import_message(
        SETTINGS_MESSAGE,
        "clipboard",
        "Lake",
        detected_kind="settings",
        durable_infokl_context=context,
    )

    catalog.import_roll.assert_called_once()
    catalog.import_server_settings.assert_called_once()
    coordinator.coordinate_infokl.assert_not_called()
    assert roll_result.imported_count == 1
    assert settings_result.imported_count == len(SETTINGS.metrics)


def test_automatic_import_non_infokl_routes_never_call_infokl_coordinator(tmp_path) -> None:
    coordinator = Mock()
    service = AutomaticImportService(
        CatalogService(CatalogRepository(tmp_path / "catalog.db")),
        infokl_projection_coordinator=coordinator,
    )

    result = service.import_message(
        "Looking for a specific command? Try $search", "clipboard"
    )

    assert result.kind == "help"
    coordinator.coordinate_infokl.assert_not_called()


def test_automatic_import_durable_claim_delegates_once_with_parsed_claim_and_context() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_claim_confirmation.return_value = ClaimConfirmation(
        account_name="parsed-account", character_name="Parsed Character"
    )
    coordinator = Mock()
    coordinator.coordinate_claim.return_value = ClaimProjectionResult(
        imported_count=1,
        import_event_id=52,
        claim_observation_id=53,
        character_id=54,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("claim_observations", 53),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        claim_projection_coordinator=coordinator,
    )
    context = DurableClaimImportContext(
        source_event_id=31,
        attempt_id=37,
        finished_at=FINISHED_AT,
    )

    result = service.import_message(
        CLAIM_MESSAGE,
        "discord:message",
        " Lake ",
        " parsed-account ",
        detected_kind="claim",
        observed_at=OBSERVED_AT,
        durable_claim_context=context,
    )

    parser.parse_claim_confirmation.assert_called_once_with(CLAIM_MESSAGE)
    coordinator.coordinate_claim.assert_called_once_with(
        source_event_id=31,
        attempt_id=37,
        claim=parser.parse_claim_confirmation.return_value,
        server="Lake",
        account="parsed-account",
        raw=CLAIM_MESSAGE,
        source="discord:message",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )
    catalog.import_claim.assert_not_called()
    assert result.imported_count == 1
    assert result.import_event_id == 52
    assert result.replay_skipped is False
    assert result.durable_success_recorded is True
    assert "Parsed Character" in result.message
    assert "parsed-account" in result.message


def test_automatic_import_durable_claim_maps_completed_replay(tmp_path) -> None:
    service, source_event_id, attempt_id = _durable_claim_importer(tmp_path)
    first = service.import_message(
        CLAIM_MESSAGE,
        "discord",
        "Lake",
        "ernieuuu",
        detected_kind="claim",
        observed_at=OBSERVED_AT,
        durable_claim_context=DurableClaimImportContext(
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            finished_at=FINISHED_AT,
        ),
    )

    replay = service.import_message(
        CLAIM_MESSAGE,
        "discord",
        "Lake",
        "ernieuuu",
        detected_kind="claim",
        observed_at=OBSERVED_AT,
        durable_claim_context=DurableClaimImportContext(
            source_event_id=source_event_id,
            attempt_id=None,
            finished_at=FINISHED_AT,
        ),
    )

    assert first.imported_count == 1
    assert first.import_event_id is not None
    assert replay.imported_count == 0
    assert replay.import_event_id == first.import_event_id
    assert replay.replay_skipped is True
    assert replay.durable_success_recorded is True


def test_automatic_import_durable_claim_propagates_coordinator_error_without_fallback() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_claim_confirmation.return_value = ClaimConfirmation(
        account_name="ernieuuu", character_name="Pakunoda"
    )
    coordinator = Mock()
    coordinator.coordinate_claim.side_effect = RuntimeError("coordinator failed")
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        claim_projection_coordinator=coordinator,
    )

    with pytest.raises(RuntimeError, match="coordinator failed"):
        service.import_message(
            CLAIM_MESSAGE,
            "discord",
            "Lake",
            "ernieuuu",
            detected_kind="claim",
            durable_claim_context=DurableClaimImportContext(
                source_event_id=31,
                attempt_id=37,
                finished_at=FINISHED_AT,
            ),
        )

    catalog.import_claim.assert_not_called()


def test_automatic_import_durable_claim_requires_coordinator_before_catalog_write() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_claim_confirmation.return_value = ClaimConfirmation(
        account_name="ernieuuu", character_name="Pakunoda"
    )
    service = AutomaticImportService(catalog, parser=parser, router=Mock())

    with pytest.raises(RuntimeError, match="ClaimProjectionCoordinator"):
        service.import_message(
            CLAIM_MESSAGE,
            "discord",
            "Lake",
            "ernieuuu",
            detected_kind="claim",
            durable_claim_context=DurableClaimImportContext(
                source_event_id=31,
                attempt_id=37,
                finished_at=FINISHED_AT,
            ),
        )

    parser.parse_claim_confirmation.assert_called_once_with(CLAIM_MESSAGE)
    catalog.import_claim.assert_not_called()


def test_automatic_import_non_durable_claim_keeps_catalog_path_and_neutral_result() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_claim_confirmation.return_value = ClaimConfirmation(
        account_name="ernieuuu", character_name="Pakunoda"
    )
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        claim_projection_coordinator=coordinator,
    )

    result = service.import_message(
        CLAIM_MESSAGE,
        "clipboard",
        "Lake",
        "ernieuuu",
        detected_kind="claim",
    )

    parser.parse_claim_confirmation.assert_called_once_with(CLAIM_MESSAGE)
    catalog.import_claim.assert_called_once_with(
        parser.parse_claim_confirmation.return_value,
        "Lake",
        "ernieuuu",
        CLAIM_MESSAGE,
        "clipboard",
    )
    coordinator.coordinate_claim.assert_not_called()
    assert result.imported_count == 1
    assert result.import_event_id is None
    assert result.replay_skipped is False
    assert result.durable_success_recorded is False


def test_automatic_import_claim_context_does_not_affect_roll_or_profile_routes() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_roll.return_value = RollObservation(
        name="Hips", series="Series", claim_rank=None, kakera_value=30
    )
    parser.parse_profile.return_value = PROFILE
    claim_coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        claim_projection_coordinator=claim_coordinator,
    )
    context = DurableClaimImportContext(
        source_event_id=31,
        attempt_id=37,
        finished_at=FINISHED_AT,
    )

    roll_result = service.import_message(
        ROLL_MESSAGE,
        "clipboard",
        "Lake",
        "ernieuuu",
        detected_kind="roll",
        durable_claim_context=context,
    )
    profile_result = service.import_message(
        PROFILE_MESSAGE,
        "clipboard",
        "Lake",
        "ernieuuu",
        detected_kind="profile",
        durable_claim_context=context,
    )

    catalog.import_roll.assert_called_once_with(
        parser.parse_roll.return_value,
        "Lake",
        "ernieuuu",
        ROLL_MESSAGE,
        "clipboard",
    )
    catalog.import_profile.assert_called_once_with(
        PROFILE,
        "Lake",
        "ernieuuu",
        PROFILE_MESSAGE,
        "clipboard",
    )
    claim_coordinator.coordinate_claim.assert_not_called()
    assert roll_result.imported_count == profile_result.imported_count == 1


def test_automatic_import_non_claim_routes_never_call_claim_coordinator(tmp_path) -> None:
    coordinator = Mock()
    service = AutomaticImportService(
        CatalogService(CatalogRepository(tmp_path / "catalog.db")),
        claim_projection_coordinator=coordinator,
    )

    result = service.import_message(
        "Looking for a specific command? Try $search",
        "clipboard",
        durable_claim_context=DurableClaimImportContext(
            source_event_id=31,
            attempt_id=37,
            finished_at=FINISHED_AT,
        ),
    )

    assert result.kind == "help"
    coordinator.coordinate_claim.assert_not_called()


def test_automatic_import_durable_profile_delegates_once_with_all_context() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_profile.return_value = PROFILE
    coordinator = Mock()
    coordinator.coordinate_profile.return_value = ProfileProjectionResult(
        imported_count=1,
        import_event_id=43,
        profile_observation_id=44,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("profile_observations", 44),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        profile_projection_coordinator=coordinator,
    )
    context = DurableProfileImportContext(
        source_event_id=21,
        attempt_id=23,
        finished_at=FINISHED_AT,
    )

    result = service.import_message(
        PROFILE_MESSAGE,
        "discord:message",
        " Lake ",
        " ernieuuu ",
        detected_kind="profile",
        observed_at=OBSERVED_AT,
        durable_profile_context=context,
    )

    parser.parse_profile.assert_called_once_with(PROFILE_MESSAGE)
    coordinator.coordinate_profile.assert_called_once_with(
        source_event_id=21,
        attempt_id=23,
        profile=PROFILE,
        server="Lake",
        account="ernieuuu",
        raw=PROFILE_MESSAGE,
        source="discord:message",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )
    catalog.import_profile.assert_not_called()
    assert result.imported_count == 1
    assert result.import_event_id == 43
    assert result.replay_skipped is False
    assert result.durable_success_recorded is True


def test_automatic_import_durable_profile_maps_completed_replay(tmp_path) -> None:
    service, source_event_id, attempt_id = _durable_profile_importer(tmp_path)
    first = service.import_message(
        PROFILE_MESSAGE,
        "discord",
        "Lake",
        "moa",
        detected_kind="profile",
        observed_at=OBSERVED_AT,
        durable_profile_context=DurableProfileImportContext(
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            finished_at=FINISHED_AT,
        ),
    )

    replay = service.import_message(
        PROFILE_MESSAGE,
        "discord",
        "Lake",
        "moa",
        detected_kind="profile",
        observed_at=OBSERVED_AT,
        durable_profile_context=DurableProfileImportContext(
            source_event_id=source_event_id,
            attempt_id=None,
            finished_at=FINISHED_AT,
        ),
    )

    assert first.imported_count == 1
    assert first.import_event_id is not None
    assert replay.imported_count == 0
    assert replay.import_event_id == first.import_event_id
    assert replay.replay_skipped is True
    assert replay.durable_success_recorded is True


def test_automatic_import_durable_profile_propagates_coordinator_error_without_fallback() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_profile.return_value = PROFILE
    coordinator = Mock()
    coordinator.coordinate_profile.side_effect = RuntimeError("coordinator failed")
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        profile_projection_coordinator=coordinator,
    )

    with pytest.raises(RuntimeError, match="coordinator failed"):
        service.import_message(
            PROFILE_MESSAGE,
            "discord",
            "Lake",
            "moa",
            detected_kind="profile",
            durable_profile_context=DurableProfileImportContext(
                source_event_id=21,
                attempt_id=23,
                finished_at=FINISHED_AT,
            ),
        )

    catalog.import_profile.assert_not_called()


def test_automatic_import_durable_profile_requires_coordinator_before_catalog_write() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_profile.return_value = PROFILE
    service = AutomaticImportService(catalog, parser=parser, router=Mock())

    with pytest.raises(RuntimeError, match="ProfileProjectionCoordinator"):
        service.import_message(
            PROFILE_MESSAGE,
            "discord",
            "Lake",
            "moa",
            detected_kind="profile",
            durable_profile_context=DurableProfileImportContext(
                source_event_id=21,
                attempt_id=23,
                finished_at=FINISHED_AT,
            ),
        )

    parser.parse_profile.assert_called_once_with(PROFILE_MESSAGE)
    catalog.import_profile.assert_not_called()


def test_automatic_import_non_durable_profile_keeps_catalog_path_and_parses_once() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_profile.return_value = PROFILE
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        profile_projection_coordinator=coordinator,
    )

    result = service.import_message(
        PROFILE_MESSAGE,
        "clipboard",
        "Lake",
        "moa",
        detected_kind="profile",
    )

    parser.parse_profile.assert_called_once_with(PROFILE_MESSAGE)
    catalog.import_profile.assert_called_once_with(
        PROFILE,
        "Lake",
        "moa",
        PROFILE_MESSAGE,
        "clipboard",
    )
    coordinator.coordinate_profile.assert_not_called()
    assert result.imported_count == 1
    assert result.import_event_id is None
    assert result.replay_skipped is False
    assert result.durable_success_recorded is False


def test_automatic_import_profile_context_does_not_affect_roll_route() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_roll.return_value = RollObservation(
        name="Hips", series="Series", claim_rank=None, kakera_value=30
    )
    profile_coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        profile_projection_coordinator=profile_coordinator,
    )

    result = service.import_message(
        ROLL_MESSAGE,
        "clipboard",
        "Lake",
        "moa",
        detected_kind="roll",
        durable_profile_context=DurableProfileImportContext(
            source_event_id=21,
            attempt_id=23,
            finished_at=FINISHED_AT,
        ),
    )

    catalog.import_roll.assert_called_once_with(
        parser.parse_roll.return_value,
        "Lake",
        "moa",
        ROLL_MESSAGE,
        "clipboard",
    )
    profile_coordinator.coordinate_profile.assert_not_called()
    assert result.imported_count == 1


def test_automatic_import_non_roll_routes_never_call_roll_coordinator(tmp_path) -> None:
    coordinator = Mock()
    service = AutomaticImportService(
        CatalogService(CatalogRepository(tmp_path / "catalog.db")),
        roll_projection_coordinator=coordinator,
    )

    result = service.import_message(
        "Looking for a specific command? Try $search", "clipboard"
    )

    assert result.kind == "help"
    coordinator.coordinate_roll.assert_not_called()


def test_automatic_import_result_remains_compatible_for_existing_callers() -> None:
    result = AutomaticImportResult(kind="help", imported_count=0, message="message")

    assert result.model_dump() == {
        "kind": "help",
        "imported_count": 0,
        "message": "message",
        "import_event_id": None,
        "replay_skipped": False,
        "durable_success_recorded": False,
    }


def test_automatic_import_persists_account_scoped_claim_evidence(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    roll = "Pakunoda\nHunter × Hunter\n116:kakera:"
    claim = "💖 **ernieuuu** and **Pakunoda** are now married! 💖\n+128:kakera:"

    service.import_message(roll, "discord", "Lake", "ernieuuu")
    result = service.import_message(claim, "discord", "Lake", "ernieuuu")

    observations = catalog.claim_observations("Lake", "ernieuuu")
    assert result.kind == "claim"
    assert result.imported_count == 1
    assert observations[0].character_name == "Pakunoda"
    assert observations[0].character is not None
    assert observations[0].character.series == "Hunter × Hunter"

    with sqlite3.connect(tmp_path / "catalog.db") as connection:
        event = connection.execute(
            "SELECT kind FROM import_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert event[0] == "claim"


def test_automatic_import_observes_divorce_prompt_without_mutating_catalog(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    prompt = (
        "Professor Layton: Do you confirm the divorce? (y/n/yes/no)\n"
        "Characters divorced by $divorce are also removed from the $restorelist "
        "(+54:kakera:if you confirm)"
    )

    result = service.import_message(prompt, "discord", "Lake", "cute_beagle_91130")

    assert result.kind == "divorce_prompt"
    assert result.imported_count == 0
    assert "Professor Layton" in result.message
    assert catalog.character_count() == 0


def test_automatic_import_observes_declined_divorce_without_mutating_catalog(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)

    result = service.import_message("Divorce declined.", "discord", "Lake", "cute_beagle_91130")

    assert result.kind == "divorce_declined"
    assert result.imported_count == 0
    assert catalog.character_count() == 0


def test_automatic_import_persists_completed_divorce_and_hides_old_claim(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)

    service.import_message(
        "Professor Layton\nProfessor Layton\n24:kakera:",
        "discord:roll",
        "Lake",
        "ernieuuu",
    )
    service.import_message(
        "ernieuuu and Professor Layton are now married!",
        "discord:claim",
        "Lake",
        "ernieuuu",
    )

    result = service.import_message(
        "💔 Professor Layton and ernieuuu are now divorced. 💔 (+54:kakera:)",
        "discord:divorce",
        "Lake",
        "ernieuuu",
    )

    assert result.kind == "divorce_complete"
    assert result.imported_count == 1
    assert catalog.claim_observations("Lake", "ernieuuu") == ()
    with sqlite3.connect(tmp_path / "catalog.db") as connection:
        event = connection.execute(
            "SELECT kind FROM import_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        observation = connection.execute(
            "SELECT character_name, kakera_refund FROM divorce_observations"
        ).fetchone()
    assert event[0] == "divorce"
    assert observation == ("Professor Layton", 54)


def test_automatic_import_persists_sphere_result(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = ":sp: +158\n:spG: +43 (Stock: 3,655)"

    result = service.import_message(message, "test", "Lake", "ernieuuu")
    observation = catalog.sphere_result("Lake", "ernieuuu")

    assert result.kind == "sphere_result"
    assert result.imported_count == 1
    assert observation is not None
    assert observation.snapshot.total_gained == 158
    assert observation.snapshot.stock == 3655


def test_automatic_import_non_durable_sphere_keeps_catalog_path_and_result() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_sphere_result.return_value = SPHERE_RESULT
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        sphere_result_projection_coordinator=coordinator,
    )

    result = service.import_message(
        SPHERE_MESSAGE,
        "clipboard",
        "Lake",
        "ernieuuu",
        detected_kind="sphere_result",
    )

    parser.parse_sphere_result.assert_called_once_with(SPHERE_MESSAGE)
    catalog.import_sphere_result.assert_called_once_with(
        SPHERE_RESULT,
        "Lake",
        "ernieuuu",
        SPHERE_MESSAGE,
        "clipboard",
    )
    coordinator.coordinate_sphere_result.assert_not_called()
    assert result.kind == "sphere_result"
    assert result.imported_count == 1
    assert result.import_event_id is None
    assert result.replay_skipped is False
    assert result.durable_success_recorded is False


def test_automatic_import_durable_sphere_first_processing_delegates_once_with_all_context() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_sphere_result.return_value = SPHERE_RESULT
    coordinator = Mock()
    coordinator.coordinate_sphere_result.return_value = SphereResultProjectionResult(
        imported_count=1,
        import_event_id=92,
        sphere_result_observation_id=93,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("sphere_result_observations", 93),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        sphere_result_projection_coordinator=coordinator,
    )
    context = DurableSphereResultImportContext(
        source_event_id=81,
        attempt_id=83,
        server="Persisted Lake",
        account="persisted-account",
        raw="persisted sphere payload",
        source="discord:message",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )

    result = service.import_message(
        SPHERE_MESSAGE,
        "caller-source",
        "Caller Lake",
        "caller-account",
        detected_kind="sphere_result",
        observed_at=datetime(2026, 7, 21, 11, 0, tzinfo=timezone.utc),
        durable_sphere_result_context=context,
    )

    parser.parse_sphere_result.assert_called_once_with(SPHERE_MESSAGE)
    coordinator.coordinate_sphere_result.assert_called_once_with(
        source_event_id=81,
        attempt_id=83,
        state=SPHERE_RESULT,
        server="Persisted Lake",
        account="persisted-account",
        raw="persisted sphere payload",
        source="discord:message",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )
    catalog.import_sphere_result.assert_not_called()
    assert result.kind == "sphere_result"
    assert result.imported_count == 1
    assert result.import_event_id == 92
    assert result.replay_skipped is False
    assert result.durable_success_recorded is True


def test_automatic_import_durable_sphere_maps_succeeded_replay() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_sphere_result.return_value = SPHERE_RESULT
    coordinator = Mock()
    coordinator.coordinate_sphere_result.return_value = SphereResultProjectionResult(
        imported_count=0,
        import_event_id=92,
        sphere_result_observation_id=93,
        replay_skipped=True,
        durable_success_recorded=True,
        projection_target=("sphere_result_observations", 93),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        sphere_result_projection_coordinator=coordinator,
    )

    result = service.import_message(
        SPHERE_MESSAGE,
        "caller-source",
        detected_kind="sphere_result",
        durable_sphere_result_context=DurableSphereResultImportContext(
            source_event_id=81,
            attempt_id=None,
            server="Lake",
            account="ernieuuu",
            raw=SPHERE_MESSAGE,
            source="discord",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        ),
    )

    parser.parse_sphere_result.assert_called_once_with(SPHERE_MESSAGE)
    coordinator.coordinate_sphere_result.assert_called_once_with(
        source_event_id=81,
        attempt_id=None,
        state=SPHERE_RESULT,
        server="Lake",
        account="ernieuuu",
        raw=SPHERE_MESSAGE,
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )
    catalog.import_sphere_result.assert_not_called()
    assert result.kind == "sphere_result"
    assert result.imported_count == 0
    assert result.import_event_id == 92
    assert result.replay_skipped is True
    assert result.durable_success_recorded is True


def test_automatic_import_durable_sphere_requires_coordinator_without_catalog_fallback() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_sphere_result.return_value = SPHERE_RESULT
    service = AutomaticImportService(catalog, parser=parser, router=Mock())

    with pytest.raises(RuntimeError, match="SphereResultProjectionCoordinator"):
        service.import_message(
            SPHERE_MESSAGE,
            "discord",
            detected_kind="sphere_result",
            durable_sphere_result_context=DurableSphereResultImportContext(
                source_event_id=81,
                attempt_id=83,
                server="Lake",
                account="ernieuuu",
                raw=SPHERE_MESSAGE,
                source="discord",
                observed_at=OBSERVED_AT,
                finished_at=FINISHED_AT,
            ),
        )

    parser.parse_sphere_result.assert_called_once_with(SPHERE_MESSAGE)
    catalog.import_sphere_result.assert_not_called()


def test_automatic_import_durable_sphere_propagates_coordinator_error_without_fallback() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_sphere_result.return_value = SPHERE_RESULT
    coordinator = Mock()
    coordinator.coordinate_sphere_result.side_effect = RuntimeError("coordinator failed")
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        sphere_result_projection_coordinator=coordinator,
    )

    with pytest.raises(RuntimeError, match="coordinator failed"):
        service.import_message(
            SPHERE_MESSAGE,
            "discord",
            detected_kind="sphere_result",
            durable_sphere_result_context=DurableSphereResultImportContext(
                source_event_id=81,
                attempt_id=83,
                server="Lake",
                account="ernieuuu",
                raw=SPHERE_MESSAGE,
                source="discord",
                observed_at=OBSERVED_AT,
                finished_at=FINISHED_AT,
            ),
        )

    catalog.import_sphere_result.assert_not_called()


def test_automatic_import_durable_sphere_parse_failure_precedes_coordinator_or_catalog() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_sphere_result.side_effect = ValueError("invalid sphere response")
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        sphere_result_projection_coordinator=coordinator,
    )

    with pytest.raises(ValueError, match="invalid sphere response"):
        service.import_message(
            SPHERE_MESSAGE,
            "discord",
            detected_kind="sphere_result",
            durable_sphere_result_context=DurableSphereResultImportContext(
                source_event_id=81,
                attempt_id=83,
                server="Lake",
                account="ernieuuu",
                raw=SPHERE_MESSAGE,
                source="discord",
                observed_at=OBSERVED_AT,
                finished_at=FINISHED_AT,
            ),
        )

    coordinator.coordinate_sphere_result.assert_not_called()
    catalog.import_sphere_result.assert_not_called()


def test_automatic_import_non_sphere_kind_does_not_call_sphere_coordinator() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_kakera_state.return_value = KAKERA_STATE
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        sphere_result_projection_coordinator=coordinator,
    )

    result = service.import_message(
        KAKERA_MESSAGE,
        "discord",
        "Lake",
        "ernieuuu",
        detected_kind="kakera",
    )

    assert result.kind == "kakera"
    coordinator.coordinate_sphere_result.assert_not_called()
    parser.parse_sphere_result.assert_not_called()


@pytest.mark.parametrize("stock", (None, 0, 321))
def test_automatic_import_durable_sphere_preserves_total_and_stock(stock) -> None:
    state = SPHERE_RESULT.model_copy(update={"stock": stock})
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_sphere_result.return_value = state
    coordinator = Mock()
    coordinator.coordinate_sphere_result.return_value = SphereResultProjectionResult(
        imported_count=1,
        import_event_id=92,
        sphere_result_observation_id=93,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("sphere_result_observations", 93),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        sphere_result_projection_coordinator=coordinator,
    )

    result = service.import_message(
        SPHERE_MESSAGE,
        "discord",
        detected_kind="sphere_result",
        durable_sphere_result_context=DurableSphereResultImportContext(
            source_event_id=81,
            attempt_id=83,
            server="Lake",
            account="ernieuuu",
            raw=SPHERE_MESSAGE,
            source="discord",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        ),
    )

    assert coordinator.coordinate_sphere_result.call_args.kwargs["state"] is state
    stock_note = f" Stock: {stock:,}." if stock is not None else ""
    assert result.message == f"Imported +{state.total_gained:,} spheres.{stock_note}"


def test_automatic_import_audits_transaction_steps_without_creating_characters(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)

    result = service.import_message(
        "ernieuuu, do you really want to give 1:kakera: ? (y/n/yes/no)",
        "discord:givek",
        "Lake",
        "ernieuuu",
    )

    assert result.kind == "gift_kakera"
    assert result.imported_count == 0
    assert catalog.character_count() == 0
    with sqlite3.connect(tmp_path / "catalog.db") as connection:
        event = connection.execute(
            "SELECT kind, source, raw_message FROM import_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert event == ("command_observation", "discord:givek:command=$givek", "ernieuuu, do you really want to give 1:kakera: ? (y/n/yes/no)")


def test_automatic_import_persists_profile_snapshot_without_character_rows(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = (
        "cute_beagle_91130\n"
        "Collection size: 35 (100%:female: 0% :male:)\n"
        "Pokédex: 2 Pokémon :gulpin: :piloswine:\n"
        "Reacts:\n"
        "1x:kakeraP: 7x:kakera: 1x:kakeraT:\n"
        "812:kakera:\n"
        "Keys: 3:bronzekey:\n"
        "110 :sp:\n"
        "2x:spP: 12x:spB: 7x:spT: 4x:spG: 1x:spY: 1x:sp: 4x:spL:\n"
        ":silvmudae::MudaeBirthday7::MudaeBirthday8::DiamondI:"
    )

    result = service.import_message(message, "discord", "Lake", "cute_beagle_91130")
    observation = catalog.profile("Lake", "cute_beagle_91130")

    assert result.kind == "profile"
    assert result.imported_count == 1
    assert observation is not None
    assert observation.snapshot.collection_size == 35
    assert observation.snapshot.kakera_balance == 812
    assert observation.snapshot.mudapins_collected is None
    assert catalog.character_count() == 0


def test_automatic_import_persists_empty_profile_without_optional_sections(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)

    result = service.import_message(
        "moa\nCollection size: 0 (0%:female: 0% :male:)",
        "discord",
        "League of Draven",
        "moa",
    )
    observation = catalog.profile("League of Draven", "moa")

    assert result.kind == "profile"
    assert result.imported_count == 1
    assert observation is not None
    assert observation.snapshot.collection_size == 0
    assert observation.snapshot.kakera_balance is None
    assert observation.snapshot.pokedex_count is None


def test_automatic_import_non_durable_mudapins_keeps_catalog_path_and_result() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_mudapins.return_value = MUDAPINS
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        mudapins_projection_coordinator=coordinator,
    )

    result = service.import_message(
        MUDAPINS_MESSAGE,
        "clipboard",
        "Lake",
        "ernieuuu",
        detected_kind="mudapins",
    )

    parser.parse_mudapins.assert_called_once_with(MUDAPINS_MESSAGE)
    catalog.import_mudapins.assert_called_once_with(
        MUDAPINS,
        "Lake",
        "ernieuuu",
        MUDAPINS_MESSAGE,
        "clipboard",
    )
    coordinator.coordinate_mudapins.assert_not_called()
    assert result.kind == "mudapins"
    assert result.imported_count == 4
    assert result.message == "Imported 4 Mudapin markers."
    assert result.import_event_id is None
    assert result.replay_skipped is False
    assert result.durable_success_recorded is False


def test_automatic_import_durable_mudapins_delegates_once_with_all_context() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_mudapins.return_value = MUDAPINS
    coordinator = Mock()
    coordinator.coordinate_mudapins.return_value = MudapinsProjectionResult(
        imported_count=1,
        import_event_id=92,
        mudapin_observation_id=93,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("mudapin_observations", 93),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        mudapins_projection_coordinator=coordinator,
    )
    context = DurableMudapinsImportContext(
        source_event_id=81,
        attempt_id=83,
        server="Persisted Lake",
        account="persisted-account",
        raw="persisted mudapins payload",
        source="discord:message",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )

    result = service.import_message(
        MUDAPINS_MESSAGE,
        "caller-source",
        detected_kind="mudapins",
        durable_mudapins_context=context,
    )

    parser.parse_mudapins.assert_called_once_with(MUDAPINS_MESSAGE)
    coordinator.coordinate_mudapins.assert_called_once_with(
        source_event_id=81,
        attempt_id=83,
        snapshot=MUDAPINS,
        server="Persisted Lake",
        account="persisted-account",
        raw="persisted mudapins payload",
        source="discord:message",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )
    catalog.import_mudapins.assert_not_called()
    assert result.kind == "mudapins"
    assert result.imported_count == 1
    assert result.import_event_id == 92
    assert result.replay_skipped is False
    assert result.durable_success_recorded is True


def test_automatic_import_durable_mudapins_maps_completed_replay() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_mudapins.return_value = MUDAPINS
    coordinator = Mock()
    coordinator.coordinate_mudapins.return_value = MudapinsProjectionResult(
        imported_count=0,
        import_event_id=92,
        mudapin_observation_id=93,
        replay_skipped=True,
        durable_success_recorded=True,
        projection_target=("mudapin_observations", 93),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        mudapins_projection_coordinator=coordinator,
    )

    result = service.import_message(
        MUDAPINS_MESSAGE,
        "caller-source",
        detected_kind="mudapins",
        durable_mudapins_context=DurableMudapinsImportContext(
            source_event_id=81,
            attempt_id=None,
            server="Lake",
            account="ernieuuu",
            raw=MUDAPINS_MESSAGE,
            source="discord",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        ),
    )

    assert result.kind == "mudapins"
    assert result.imported_count == 0
    assert result.import_event_id == 92
    assert result.replay_skipped is True
    assert result.durable_success_recorded is True


@pytest.mark.parametrize(
    ("message", "snapshot", "expected_message"),
    (
        (MUDAPINS_MESSAGE, MUDAPINS, "Imported 4 Mudapin markers."),
        (EMPTY_MUDAPINS_MESSAGE, EMPTY_MUDAPINS, "Imported no Mudapins."),
    ),
)
def test_automatic_import_durable_mudapins_preserves_snapshot_values(
    message, snapshot, expected_message
) -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_mudapins.return_value = snapshot
    coordinator = Mock()
    coordinator.coordinate_mudapins.return_value = MudapinsProjectionResult(
        imported_count=1,
        import_event_id=92,
        mudapin_observation_id=93,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("mudapin_observations", 93),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        mudapins_projection_coordinator=coordinator,
    )

    result = service.import_message(
        message,
        "caller-source",
        detected_kind="mudapins",
        durable_mudapins_context=DurableMudapinsImportContext(
            source_event_id=81,
            attempt_id=83,
            server="Lake",
            account="ernieuuu",
            raw=message,
            source="discord",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        ),
    )

    assert coordinator.coordinate_mudapins.call_args.kwargs["snapshot"] is snapshot
    assert result.message == expected_message


def test_automatic_import_durable_mudapins_requires_coordinator_before_catalog_write() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_mudapins.return_value = MUDAPINS
    service = AutomaticImportService(catalog, parser=parser, router=Mock())

    with pytest.raises(RuntimeError, match="MudapinsProjectionCoordinator"):
        service.import_message(
            MUDAPINS_MESSAGE,
            "discord",
            detected_kind="mudapins",
            durable_mudapins_context=DurableMudapinsImportContext(
                source_event_id=81,
                attempt_id=83,
                server="Lake",
                account="ernieuuu",
                raw=MUDAPINS_MESSAGE,
                source="discord",
                observed_at=OBSERVED_AT,
                finished_at=FINISHED_AT,
            ),
        )

    parser.parse_mudapins.assert_called_once_with(MUDAPINS_MESSAGE)
    catalog.import_mudapins.assert_not_called()


def test_automatic_import_durable_mudapins_propagates_coordinator_error_without_fallback() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_mudapins.return_value = MUDAPINS
    coordinator = Mock()
    coordinator.coordinate_mudapins.side_effect = RuntimeError("coordinator failed")
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        mudapins_projection_coordinator=coordinator,
    )

    with pytest.raises(RuntimeError, match="coordinator failed"):
        service.import_message(
            MUDAPINS_MESSAGE,
            "discord",
            detected_kind="mudapins",
            durable_mudapins_context=DurableMudapinsImportContext(
                source_event_id=81,
                attempt_id=83,
                server="Lake",
                account="ernieuuu",
                raw=MUDAPINS_MESSAGE,
                source="discord",
                observed_at=OBSERVED_AT,
                finished_at=FINISHED_AT,
            ),
        )

    catalog.import_mudapins.assert_not_called()


def test_automatic_import_durable_mudapins_parse_failure_precedes_any_write() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_mudapins.side_effect = ValueError("invalid Mudapin response")
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        mudapins_projection_coordinator=coordinator,
    )

    with pytest.raises(ValueError, match="invalid Mudapin response"):
        service.import_message(
            MUDAPINS_MESSAGE,
            "discord",
            detected_kind="mudapins",
            durable_mudapins_context=DurableMudapinsImportContext(
                source_event_id=81,
                attempt_id=83,
                server="Lake",
                account="ernieuuu",
                raw=MUDAPINS_MESSAGE,
                source="discord",
                observed_at=OBSERVED_AT,
                finished_at=FINISHED_AT,
            ),
        )

    parser.parse_mudapins.assert_called_once_with(MUDAPINS_MESSAGE)
    coordinator.coordinate_mudapins.assert_not_called()
    catalog.import_mudapins.assert_not_called()


@pytest.mark.parametrize(
    ("message", "expected_markers"),
    (
        (MUDAPINS_MESSAGE, MUDAPINS.pin_markers),
        (EMPTY_MUDAPINS_MESSAGE, ()),
    ),
)
def test_automatic_import_real_durable_mudapins_first_processing_and_replay_are_atomic(
    tmp_path, message, expected_markers
) -> None:
    database_path, service, source_event_id, attempt_id = _durable_mudapins_importer(tmp_path)
    context = DurableMudapinsImportContext(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        server="Lake",
        account="ernieuuu",
        raw=message,
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )

    first = service.import_message(
        message,
        "caller-source",
        detected_kind="mudapins",
        durable_mudapins_context=context,
    )

    assert first.kind == "mudapins"
    assert first.imported_count == 1
    assert first.import_event_id is not None
    assert first.replay_skipped is False
    assert first.durable_success_recorded is True
    observation = service._catalog.mudapins("Lake", "ernieuuu")
    assert observation is not None
    assert observation.snapshot.pin_markers == expected_markers
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM import_events WHERE kind = 'mudapins'"
        ).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM mudapin_observations").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_projection_links "
            "WHERE projection_kind = 'catalog.mudapins' AND state = 'completed'"
        ).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM discord_processing_attempts").fetchone()[0] == 1
        assert tuple(
            connection.execute(
                "SELECT status, legacy_import_event_id FROM discord_source_events"
            ).fetchone()
        ) == ("succeeded", first.import_event_id)
        assert connection.execute(
            "SELECT status FROM discord_processing_attempts"
        ).fetchone()[0] == "succeeded"
        before_replay = tuple(
            connection.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM import_events), "
                "(SELECT COUNT(*) FROM mudapin_observations), "
                "(SELECT COUNT(*) FROM discord_projection_links), "
                "(SELECT COUNT(*) FROM server_contexts), "
                "(SELECT COUNT(*) FROM account_contexts), "
                "(SELECT COUNT(*) FROM discord_source_event_server_attributions), "
                "(SELECT COUNT(*) FROM discord_source_event_account_attributions), "
                "(SELECT COUNT(*) FROM discord_processing_attempts)"
            ).fetchone()
        )

    replay = service.import_message(
        message,
        "caller-source",
        detected_kind="mudapins",
        durable_mudapins_context=DurableMudapinsImportContext(
            source_event_id=source_event_id,
            attempt_id=None,
            server="Lake",
            account="ernieuuu",
            raw=message,
            source="discord",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        ),
    )

    assert replay.kind == "mudapins"
    assert replay.imported_count == 0
    assert replay.import_event_id == first.import_event_id
    assert replay.replay_skipped is True
    assert replay.durable_success_recorded is True
    with sqlite3.connect(database_path) as connection:
        after_replay = tuple(
            connection.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM import_events), "
                "(SELECT COUNT(*) FROM mudapin_observations), "
                "(SELECT COUNT(*) FROM discord_projection_links), "
                "(SELECT COUNT(*) FROM server_contexts), "
                "(SELECT COUNT(*) FROM account_contexts), "
                "(SELECT COUNT(*) FROM discord_source_event_server_attributions), "
                "(SELECT COUNT(*) FROM discord_source_event_account_attributions), "
                "(SELECT COUNT(*) FROM discord_processing_attempts)"
            ).fetchone()
        )
    assert after_replay == before_replay


def test_automatic_import_persists_mudapin_inventory(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)

    result = service.import_message(
        ":pin139::pin182::pin2157::logopin6::logopin141:",
        "discord",
        "Lake Arrowhead 2025",
        "ernieuuu",
    )
    observation = catalog.mudapins("Lake Arrowhead 2025", "ernieuuu")

    assert result.kind == "mudapins"
    assert result.imported_count == 5
    assert result.message == "Imported 5 Mudapin markers."
    assert observation is not None
    assert observation.snapshot.pin_markers == (
        ":pin139:",
        ":pin182:",
        ":pin2157:",
        ":logopin6:",
        ":logopin141:",
    )


def test_automatic_import_persists_empty_mudapin_inventory(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)

    result = service.import_message(
        "No mudapins found! Collect them with kakeraloots ($kl)",
        "discord",
        "League of Draven",
        "cute_beagle_91130",
    )
    observation = catalog.mudapins("League of Draven", "cute_beagle_91130")

    assert result.kind == "mudapins"
    assert result.imported_count == 0
    assert result.message == "Imported no Mudapins."
    assert observation is not None
    assert observation.snapshot.pin_markers == ()


def test_automatic_import_routes_bold_kakera_receipt(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = ":kakeraY: **ernieuuu +524** ($k)"

    result = service.import_message(message, "test", "Lake")
    receipts = catalog.kakera_reactions("Lake", "ernieuuu", 1)

    assert result.kind == "reaction_receipt"
    assert result.imported_count == 1
    assert [(receipt.reaction_label, receipt.kakera_earned) for receipt in receipts] == [
        (":kakeraY:", 524)
    ]


def test_automatic_import_routes_blocked_kakera_reaction_without_writing_timer_state(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = "**cute_beagle_91130**, You can't react to kakera for **34** min. ($ku)"

    result = service.import_message(message, "test", "Lake", "cute_beagle_91130")

    assert result.kind == "reaction_blocked"
    assert result.imported_count == 0
    assert "no $ku snapshot imported" in result.message
    assert catalog.timer_state("Lake", "cute_beagle_91130") is None


def test_automatic_import_routes_keyed_harem_pages(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = "ernieuuu's harem\nAlbedo \u00b7 :goldkey:  (7) 1,453 ka\nPage 1 / 6"

    result = service.import_message(message, "test", "Lake", "ernieuuu")

    assert result.kind == "harem"
    assert result.imported_count == 1
    assert "page 1/6" in result.message
    entries = catalog.harem_keys("Lake", "ernieuuu")
    assert [(entry.character_name, entry.key_count, entry.kakera_value) for entry in entries] == [
        ("Albedo", 7, 1453)
    ]


def test_automatic_import_messages_show_character_and_series(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)

    result = service.import_message(
        "Hips\nDekoboko Majo no Oyako Jijou\n30:kakera:",
        "test",
        "Lake",
        "ernieuuu",
    )

    assert result.message == (
        "Imported roll observation: Hips / Dekoboko Majo no Oyako Jijou."
    )


def test_automatic_import_routes_antidisable_pages(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = (
        "ernieuuu's Antidisablelist (1/500)\n"
        "10 antidisabled characters\n"
        "Chainsaw Man\n"
        "Page 1 / 1"
    )

    result = service.import_message(message, "test", "Lake", "ernieuuu")

    assert result.kind == "antidisable"
    assert result.imported_count == 1
    assert "page 1/1" in result.message


def test_automatic_import_routes_antidisable_continuation_page_without_count(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = (
        "ernieuuu's Antidisablelist (1/500)\n"
        "Chainsaw Man\n"
        "Page 2 / 2"
    )

    result = service.import_message(message, "test", "Lake", "ernieuuu")

    assert result.kind == "antidisable"
    assert result.imported_count == 1
    assert "page 2/2" in result.message


def test_automatic_import_routes_ranked_harem_pages(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    catalog.import_top_page(
        MudaeTextParser().parse_top_page("#2 - Zero Two - DARLING in the FRANXX"),
        "#2 - Zero Two - DARLING in the FRANXX",
        "clipboard",
    )
    service = AutomaticImportService(catalog)
    message = "ernieuuu's harem\n#2 - Zero Two 1,440 ka\nPage 1 / 38"

    result = service.import_message(message, "test", "Lake", "ernieuuu")

    assert result.kind == "ranked_harem"
    assert result.imported_count == 1
    assert "page 1/38" in result.message
    entries = catalog.owned_characters("Lake", "ernieuuu")
    assert [(entry.character_name, entry.claim_rank, entry.kakera_value) for entry in entries] == [
        ("Zero Two", 2, 1440)
    ]


def test_automatic_import_non_durable_tower_keeps_catalog_path_and_result() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_tower_state.return_value = TOWER_STATE
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        tower_state_projection_coordinator=coordinator,
    )

    result = service.import_message(
        TOWER_MESSAGE,
        "clipboard",
        "Lake",
        "ernieuuu",
        detected_kind="towerstate",
    )

    parser.parse_tower_state.assert_called_once_with(TOWER_MESSAGE)
    catalog.import_tower_state.assert_called_once_with(
        TOWER_STATE,
        "Lake",
        "ernieuuu",
        TOWER_MESSAGE,
        "clipboard",
    )
    coordinator.coordinate_tower_state.assert_not_called()
    assert result.kind == "towerstate"
    assert result.imported_count == 1
    assert result.message == "Imported Kakera Tower state."
    assert result.import_event_id is None
    assert result.replay_skipped is False
    assert result.durable_success_recorded is False


def test_automatic_import_durable_tower_delegates_once_with_all_context() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_tower_state.return_value = TOWER_STATE
    coordinator = Mock()
    coordinator.coordinate_tower_state.return_value = TowerStateProjectionResult(
        imported_count=1,
        import_event_id=92,
        tower_state_observation_id=93,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("tower_state_observations", 93),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        tower_state_projection_coordinator=coordinator,
    )
    context = DurableTowerStateImportContext(
        source_event_id=81,
        attempt_id=83,
        server="Persisted Lake",
        account="persisted-account",
        raw="persisted tower payload",
        source="discord:message",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )

    result = service.import_message(
        TOWER_MESSAGE,
        "caller-source",
        detected_kind="towerstate",
        durable_tower_state_context=context,
    )

    parser.parse_tower_state.assert_called_once_with(TOWER_MESSAGE)
    coordinator.coordinate_tower_state.assert_called_once_with(
        source_event_id=81,
        attempt_id=83,
        state=TOWER_STATE,
        server="Persisted Lake",
        account="persisted-account",
        raw="persisted tower payload",
        source="discord:message",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )
    catalog.import_tower_state.assert_not_called()
    assert result.kind == "towerstate"
    assert result.imported_count == 1
    assert result.import_event_id == 92
    assert result.replay_skipped is False
    assert result.durable_success_recorded is True


def test_automatic_import_durable_tower_maps_succeeded_replay() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_tower_state.return_value = TOWER_STATE
    coordinator = Mock()
    coordinator.coordinate_tower_state.return_value = TowerStateProjectionResult(
        imported_count=0,
        import_event_id=92,
        tower_state_observation_id=93,
        replay_skipped=True,
        durable_success_recorded=True,
        projection_target=("tower_state_observations", 93),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        tower_state_projection_coordinator=coordinator,
    )

    result = service.import_message(
        TOWER_MESSAGE,
        "caller-source",
        detected_kind="towerstate",
        durable_tower_state_context=DurableTowerStateImportContext(
            source_event_id=81,
            attempt_id=None,
            server="Lake",
            account="ernieuuu",
            raw=TOWER_MESSAGE,
            source="discord",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        ),
    )

    coordinator.coordinate_tower_state.assert_called_once()
    assert coordinator.coordinate_tower_state.call_args.kwargs["attempt_id"] is None
    catalog.import_tower_state.assert_not_called()
    assert result.kind == "towerstate"
    assert result.imported_count == 0
    assert result.import_event_id == 92
    assert result.replay_skipped is True
    assert result.durable_success_recorded is True


def test_automatic_import_durable_tower_requires_coordinator_before_catalog_write() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_tower_state.return_value = TOWER_STATE
    service = AutomaticImportService(catalog, parser=parser, router=Mock())

    with pytest.raises(RuntimeError, match="TowerStateProjectionCoordinator"):
        service.import_message(
            TOWER_MESSAGE,
            "discord",
            detected_kind="towerstate",
            durable_tower_state_context=DurableTowerStateImportContext(
                source_event_id=81,
                attempt_id=83,
                server="Lake",
                account="ernieuuu",
                raw=TOWER_MESSAGE,
                source="discord",
                observed_at=OBSERVED_AT,
                finished_at=FINISHED_AT,
            ),
        )

    parser.parse_tower_state.assert_called_once_with(TOWER_MESSAGE)
    catalog.import_tower_state.assert_not_called()


def test_automatic_import_durable_tower_propagates_coordinator_error_without_fallback() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_tower_state.return_value = TOWER_STATE
    coordinator = Mock()
    coordinator.coordinate_tower_state.side_effect = RuntimeError("coordinator failed")
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        tower_state_projection_coordinator=coordinator,
    )

    with pytest.raises(RuntimeError, match="coordinator failed"):
        service.import_message(
            TOWER_MESSAGE,
            "discord",
            detected_kind="towerstate",
            durable_tower_state_context=DurableTowerStateImportContext(
                source_event_id=81,
                attempt_id=83,
                server="Lake",
                account="ernieuuu",
                raw=TOWER_MESSAGE,
                source="discord",
                observed_at=OBSERVED_AT,
                finished_at=FINISHED_AT,
            ),
        )

    catalog.import_tower_state.assert_not_called()


def test_automatic_import_tower_parse_failure_precedes_coordinator_or_catalog() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_tower_state.side_effect = ValueError("invalid Tower response")
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        tower_state_projection_coordinator=coordinator,
    )

    with pytest.raises(ValueError, match="invalid Tower response"):
        service.import_message(
            TOWER_MESSAGE,
            "discord",
            detected_kind="towerstate",
            durable_tower_state_context=DurableTowerStateImportContext(
                source_event_id=81,
                attempt_id=83,
                server="Lake",
                account="ernieuuu",
                raw=TOWER_MESSAGE,
                source="discord",
                observed_at=OBSERVED_AT,
                finished_at=FINISHED_AT,
            ),
        )

    parser.parse_tower_state.assert_called_once_with(TOWER_MESSAGE)
    coordinator.coordinate_tower_state.assert_not_called()
    catalog.import_tower_state.assert_not_called()


def test_automatic_import_non_tower_kind_does_not_call_tower_coordinator() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_kakera_state.return_value = KAKERA_STATE
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        tower_state_projection_coordinator=coordinator,
    )

    result = service.import_message(
        KAKERA_MESSAGE,
        "clipboard",
        "Lake",
        "ernieuuu",
        detected_kind="kakera",
    )

    assert result.kind == "kakera"
    coordinator.coordinate_tower_state.assert_not_called()
    parser.parse_tower_state.assert_not_called()


def test_automatic_import_non_durable_tower_does_not_require_context_metadata() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_tower_state.return_value = TOWER_STATE
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        tower_state_projection_coordinator=coordinator,
    )

    result = service.import_message(
        TOWER_MESSAGE,
        "clipboard",
        "Lake",
        "ernieuuu",
        detected_kind="towerstate",
    )

    assert result.imported_count == 1
    coordinator.coordinate_tower_state.assert_not_called()
    catalog.import_tower_state.assert_called_once()


def _kakeraloot_context(attempt_id: int | None = 83) -> DurableKakeralootStateImportContext:
    return DurableKakeralootStateImportContext(
        source_event_id=81,
        attempt_id=attempt_id,
        server="Persisted Lake",
        account="persisted-account",
        raw="persisted kakeraloot payload",
        source="discord:message",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )


def test_automatic_import_non_durable_kakeraloot_keeps_catalog_path_and_result() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_kakeraloot_state.return_value = KAKERALOOT_STATE
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        kakeraloot_state_projection_coordinator=coordinator,
    )

    result = service.import_message(
        KAKERALOOT_MESSAGE,
        "clipboard",
        "Lake",
        "ernieuuu",
        detected_kind="lootstate",
    )

    parser.parse_kakeraloot_state.assert_called_once_with(KAKERALOOT_MESSAGE)
    catalog.import_kakeraloot_state.assert_called_once_with(
        KAKERALOOT_STATE,
        "Lake",
        "ernieuuu",
        KAKERALOOT_MESSAGE,
        "clipboard",
    )
    coordinator.coordinate_kakeraloot_state.assert_not_called()
    assert result.kind == "lootstate"
    assert result.imported_count == 1
    assert result.message == "Imported Kakeraloot state."
    assert result.import_event_id is None
    assert result.replay_skipped is False
    assert result.durable_success_recorded is False


def test_automatic_import_durable_kakeraloot_first_processing_delegates_once_with_all_context() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_kakeraloot_state.return_value = KAKERALOOT_STATE
    coordinator = Mock()
    coordinator.coordinate_kakeraloot_state.return_value = KakeralootStateProjectionResult(
        imported_count=1,
        import_event_id=92,
        kakeraloot_state_observation_id=93,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("kakeraloot_state_observations", 93),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        kakeraloot_state_projection_coordinator=coordinator,
    )
    context = _kakeraloot_context()

    result = service.import_message(
        KAKERALOOT_MESSAGE,
        "caller-source",
        detected_kind="lootstate",
        durable_kakeraloot_state_context=context,
    )

    parser.parse_kakeraloot_state.assert_called_once_with(KAKERALOOT_MESSAGE)
    coordinator.coordinate_kakeraloot_state.assert_called_once_with(
        source_event_id=81,
        attempt_id=83,
        state=KAKERALOOT_STATE,
        server="Persisted Lake",
        account="persisted-account",
        raw="persisted kakeraloot payload",
        source="discord:message",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )
    catalog.import_kakeraloot_state.assert_not_called()
    assert result.kind == "lootstate"
    assert result.imported_count == 1
    assert result.import_event_id == 92
    assert result.replay_skipped is False
    assert result.durable_success_recorded is True

    forwarded = coordinator.coordinate_kakeraloot_state.call_args.kwargs["state"]
    assert forwarded.has_kakeraloots is True
    assert forwarded.status_note == "all fields"
    assert forwarded.rolls_stacked == 1
    assert forwarded.disable_wa_ha_reduction == 102
    assert forwarded.disable_wg_hg_reduction == 68
    assert forwarded.protected_wish_level == 42
    assert forwarded.protected_wish_denominator == 4642
    assert forwarded.mudapins == 22
    assert forwarded.rt_cooldown_reduction_hours == 2
    assert forwarded.permanent_roll_bonus == 1
    assert forwarded.star_branches == 3
    assert forwarded.starwish_slots_from_branches == 4
    assert forwarded.quantity_level == 23
    assert forwarded.quality_level == 6
    assert forwarded.usage_count == 256
    assert forwarded.kakera_balance == 9210


def test_automatic_import_durable_kakeraloot_maps_succeeded_replay() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_kakeraloot_state.return_value = KAKERALOOT_STATE
    coordinator = Mock()
    coordinator.coordinate_kakeraloot_state.return_value = KakeralootStateProjectionResult(
        imported_count=0,
        import_event_id=92,
        kakeraloot_state_observation_id=93,
        replay_skipped=True,
        durable_success_recorded=True,
        projection_target=("kakeraloot_state_observations", 93),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        kakeraloot_state_projection_coordinator=coordinator,
    )

    result = service.import_message(
        KAKERALOOT_MESSAGE,
        "caller-source",
        detected_kind="lootstate",
        durable_kakeraloot_state_context=_kakeraloot_context(attempt_id=None),
    )

    parser.parse_kakeraloot_state.assert_called_once_with(KAKERALOOT_MESSAGE)
    coordinator.coordinate_kakeraloot_state.assert_called_once()
    assert coordinator.coordinate_kakeraloot_state.call_args.kwargs["attempt_id"] is None
    catalog.import_kakeraloot_state.assert_not_called()
    assert result.kind == "lootstate"
    assert result.imported_count == 0
    assert result.import_event_id == 92
    assert result.replay_skipped is True
    assert result.durable_success_recorded is True


def test_automatic_import_durable_kakeraloot_requires_coordinator_without_catalog_fallback() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_kakeraloot_state.return_value = KAKERALOOT_STATE
    service = AutomaticImportService(catalog, parser=parser, router=Mock())

    with pytest.raises(RuntimeError, match="KakeralootStateProjectionCoordinator"):
        service.import_message(
            KAKERALOOT_MESSAGE,
            "discord",
            detected_kind="lootstate",
            durable_kakeraloot_state_context=_kakeraloot_context(),
        )

    parser.parse_kakeraloot_state.assert_called_once_with(KAKERALOOT_MESSAGE)
    catalog.import_kakeraloot_state.assert_not_called()


def test_automatic_import_durable_kakeraloot_propagates_coordinator_error_without_fallback() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_kakeraloot_state.return_value = KAKERALOOT_STATE
    coordinator = Mock()
    coordinator.coordinate_kakeraloot_state.side_effect = RuntimeError("coordinator failed")
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        kakeraloot_state_projection_coordinator=coordinator,
    )

    with pytest.raises(RuntimeError, match="coordinator failed"):
        service.import_message(
            KAKERALOOT_MESSAGE,
            "discord",
            detected_kind="lootstate",
            durable_kakeraloot_state_context=_kakeraloot_context(),
        )

    coordinator.coordinate_kakeraloot_state.assert_called_once()
    catalog.import_kakeraloot_state.assert_not_called()


def test_automatic_import_malformed_kakeraloot_does_not_invoke_coordinator() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_kakeraloot_state.side_effect = ValueError("invalid Kakeraloot response")
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        kakeraloot_state_projection_coordinator=coordinator,
    )

    with pytest.raises(ValueError, match="invalid Kakeraloot response"):
        service.import_message(
            KAKERALOOT_MESSAGE,
            "discord",
            detected_kind="lootstate",
            durable_kakeraloot_state_context=_kakeraloot_context(),
        )

    parser.parse_kakeraloot_state.assert_called_once_with(KAKERALOOT_MESSAGE)
    coordinator.coordinate_kakeraloot_state.assert_not_called()
    catalog.import_kakeraloot_state.assert_not_called()


@pytest.mark.parametrize(
    "state",
    (
        KAKERALOOT_STATE,
        KakeralootStateSnapshot(),
        KakeralootStateSnapshot(
            has_kakeraloots=False,
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
        ),
    ),
)
def test_automatic_import_durable_kakeraloot_preserves_boundary_states(state) -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_kakeraloot_state.return_value = state
    coordinator = Mock()
    coordinator.coordinate_kakeraloot_state.return_value = KakeralootStateProjectionResult(
        imported_count=1,
        import_event_id=92,
        kakeraloot_state_observation_id=93,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("kakeraloot_state_observations", 93),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        kakeraloot_state_projection_coordinator=coordinator,
    )

    service.import_message(
        KAKERALOOT_MESSAGE,
        "caller-source",
        detected_kind="lootstate",
        durable_kakeraloot_state_context=_kakeraloot_context(),
    )

    assert coordinator.coordinate_kakeraloot_state.call_args.kwargs["state"] is state
    catalog.import_kakeraloot_state.assert_not_called()


def test_automatic_import_unrelated_route_does_not_invoke_kakeraloot_coordinator() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_kakera_state.return_value = KAKERA_STATE
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        kakeraloot_state_projection_coordinator=coordinator,
    )

    result = service.import_message(
        KAKERA_MESSAGE,
        "clipboard",
        "Lake",
        "ernieuuu",
        detected_kind="kakera",
    )

    assert result.kind == "kakera"
    coordinator.coordinate_kakeraloot_state.assert_not_called()
    parser.parse_kakeraloot_state.assert_not_called()


def _player_bonus_context(attempt_id: int | None = 83) -> DurablePlayerBonusImportContext:
    return DurablePlayerBonusImportContext(
        source_event_id=81,
        attempt_id=attempt_id,
        server="Persisted Lake",
        account="persisted-account",
        raw="persisted bonus payload",
        source="discord:message",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )


def test_automatic_import_non_durable_bonus_keeps_catalog_path_with_configured_coordinator() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_player_bonus.return_value = PLAYER_BONUS
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        player_bonus_projection_coordinator=coordinator,
    )

    result = service.import_message(
        BONUS_MESSAGE,
        "clipboard",
        "Lake",
        "ernieuuu",
        detected_kind="bonus",
    )

    parser.parse_player_bonus.assert_called_once_with(BONUS_MESSAGE)
    catalog.import_player_bonus.assert_called_once_with(
        PLAYER_BONUS, "Lake", "ernieuuu", BONUS_MESSAGE, "clipboard"
    )
    coordinator.coordinate_player_bonus.assert_not_called()
    assert result == AutomaticImportResult(
        kind="bonus",
        imported_count=len(PLAYER_BONUS.metrics),
        message="Imported player bonuses.",
    )


def test_automatic_import_durable_bonus_delegates_once_with_all_context() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_player_bonus.return_value = PLAYER_BONUS
    coordinator = Mock()
    coordinator.coordinate_player_bonus.return_value = PlayerBonusProjectionResult(
        imported_count=1,
        import_event_id=92,
        player_bonus_observation_id=93,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("player_bonus_observations", 93),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        player_bonus_projection_coordinator=coordinator,
    )
    context = _player_bonus_context()

    result = service.import_message(
        BONUS_MESSAGE,
        "caller-source",
        "caller-server",
        "caller-account",
        detected_kind="bonus",
        observed_at=datetime(2026, 7, 21, 12, 2, tzinfo=timezone.utc),
        durable_player_bonus_context=context,
    )

    parser.parse_player_bonus.assert_called_once_with(BONUS_MESSAGE)
    coordinator.coordinate_player_bonus.assert_called_once_with(
        source_event_id=context.source_event_id,
        attempt_id=context.attempt_id,
        state=PLAYER_BONUS,
        server=context.server,
        account=context.account,
        raw=context.raw,
        source=context.source,
        observed_at=context.observed_at,
        finished_at=context.finished_at,
    )
    catalog.import_player_bonus.assert_not_called()
    assert result.kind == "bonus"
    assert result.imported_count == 1
    assert result.import_event_id == 92
    assert result.replay_skipped is False
    assert result.durable_success_recorded is True


def test_automatic_import_durable_bonus_maps_succeeded_replay_without_catalog_fallback() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_player_bonus.return_value = PLAYER_BONUS
    coordinator = Mock()
    coordinator.coordinate_player_bonus.return_value = PlayerBonusProjectionResult(
        imported_count=0,
        import_event_id=92,
        player_bonus_observation_id=93,
        replay_skipped=True,
        durable_success_recorded=True,
        projection_target=("player_bonus_observations", 93),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        player_bonus_projection_coordinator=coordinator,
    )

    result = service.import_message(
        BONUS_MESSAGE,
        "caller-source",
        detected_kind="bonus",
        durable_player_bonus_context=_player_bonus_context(attempt_id=None),
    )

    coordinator.coordinate_player_bonus.assert_called_once()
    assert coordinator.coordinate_player_bonus.call_args.kwargs["attempt_id"] is None
    catalog.import_player_bonus.assert_not_called()
    assert result.imported_count == 0
    assert result.import_event_id == 92
    assert result.replay_skipped is True
    assert result.durable_success_recorded is True


def test_automatic_import_durable_bonus_requires_coordinator_before_catalog_write() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_player_bonus.return_value = PLAYER_BONUS
    service = AutomaticImportService(catalog, parser=parser, router=Mock())

    with pytest.raises(RuntimeError, match="PlayerBonusProjectionCoordinator"):
        service.import_message(
            BONUS_MESSAGE,
            "discord",
            detected_kind="bonus",
            durable_player_bonus_context=_player_bonus_context(),
        )

    parser.parse_player_bonus.assert_called_once_with(BONUS_MESSAGE)
    catalog.import_player_bonus.assert_not_called()


def test_automatic_import_durable_bonus_coordinator_failure_does_not_fallback() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_player_bonus.return_value = PLAYER_BONUS
    coordinator = Mock()
    coordinator.coordinate_player_bonus.side_effect = RuntimeError("coordinator failed")
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        player_bonus_projection_coordinator=coordinator,
    )

    with pytest.raises(RuntimeError, match="coordinator failed"):
        service.import_message(
            BONUS_MESSAGE,
            "discord",
            detected_kind="bonus",
            durable_player_bonus_context=_player_bonus_context(),
        )

    catalog.import_player_bonus.assert_not_called()


def test_automatic_import_malformed_bonus_does_not_invoke_coordinator() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_player_bonus.side_effect = ValueError("invalid bonus response")
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        player_bonus_projection_coordinator=coordinator,
    )

    with pytest.raises(ValueError, match="invalid bonus response"):
        service.import_message(
            BONUS_MESSAGE,
            "discord",
            detected_kind="bonus",
            durable_player_bonus_context=_player_bonus_context(),
        )

    coordinator.coordinate_player_bonus.assert_not_called()
    catalog.import_player_bonus.assert_not_called()


@pytest.mark.parametrize(
    "state",
    (
        PlayerBonusSnapshot(metrics=()),
        PlayerBonusSnapshot(
            metrics=(PlayerBonusMetric(label="Zero", detail="0"),),
            rolls_per_hour_bonus=0,
            wishlist_slot_bonus=0,
            wish_spawn_bonus_percent=0,
            starwish_spawn_bonus_percent=0,
            starwish_total_spawn_bonus_percent=0,
            starwish_slot_bonus=0,
            additional_wish_key_chance_percent=0,
            kakera_max_power_percent=0,
            kakera_button_power_cost_percent=0,
            starwish_kakera_button_bonus_percent=0,
            light_kakera_minimum=0,
            light_kakera_maximum=0,
        ),
        PLAYER_BONUS,
    ),
)
def test_automatic_import_durable_bonus_forwards_every_snapshot_value(state) -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_player_bonus.return_value = state
    coordinator = Mock()
    coordinator.coordinate_player_bonus.return_value = PlayerBonusProjectionResult(
        imported_count=1,
        import_event_id=92,
        player_bonus_observation_id=93,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("player_bonus_observations", 93),
    )
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        player_bonus_projection_coordinator=coordinator,
    )

    service.import_message(
        BONUS_MESSAGE,
        "discord",
        detected_kind="bonus",
        durable_player_bonus_context=_player_bonus_context(),
    )

    assert coordinator.coordinate_player_bonus.call_args.kwargs["state"] is state
    catalog.import_player_bonus.assert_not_called()


def test_automatic_import_unrelated_route_does_not_invoke_bonus_coordinator() -> None:
    catalog = Mock(spec=CatalogService)
    parser = Mock()
    parser.parse_wishlist.return_value = Mock(entries=(object(),))
    coordinator = Mock()
    service = AutomaticImportService(
        catalog,
        parser=parser,
        router=Mock(),
        player_bonus_projection_coordinator=coordinator,
    )

    result = service.import_message(
        "wishlist payload",
        "clipboard",
        "Lake",
        "ernieuuu",
        detected_kind="wishlist",
    )

    assert result.kind == "wishlist"
    coordinator.coordinate_player_bonus.assert_not_called()
    parser.parse_player_bonus.assert_not_called()
