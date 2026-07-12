"""Models for MOA's local character catalog and rank history."""

from datetime import datetime

from moa.models.base import MOAModel
from moa.models.character import (
    BadgeLevel,
    DisableListEntry,
    KakeralootStateSnapshot,
    PlayerBonusMetric,
    ServerSettingMetric,
    TimerStateSnapshot,
    WishlistEntry,
)


class CatalogCharacter(MOAModel):
    """A canonical character record stored by MOA."""

    id: int
    name: str
    series: str
    gender: str | None
    roulette: str | None


class CatalogRankSnapshot(MOAModel):
    """A timestamped rank observation attached to a catalog character."""

    character_id: int
    claim_rank: int | None
    like_rank: int | None
    observed_at: datetime
    import_event_id: int


class RankedCatalogCharacter(MOAModel):
    """A character and its most recently imported ranking information."""

    character: CatalogCharacter
    claim_rank: int
    like_rank: int | None
    observed_at: datetime


class TopImportResult(MOAModel):
    """Summary of a persisted `$top` import."""

    import_event_id: int
    characters_imported: int
    observed_at: datetime


class CharacterDetailsImportResult(MOAModel):
    """Summary of one persisted `$im` import."""

    import_event_id: int
    character_id: int
    server_name: str
    observed_at: datetime


class ServerKakeraObservation(MOAModel):
    """The most recently observed Kakera value for one character on one server."""

    server_name: str
    kakera_value: int | None
    observed_at: datetime


class CharacterProfile(MOAModel):
    """A catalog character with its latest global and server-specific observations."""

    character: CatalogCharacter
    claim_rank: int | None
    like_rank: int | None
    rank_observed_at: datetime | None
    server_observations: tuple[ServerKakeraObservation, ...]


class ImportEventSummary(MOAModel):
    """A compact view of one raw Mudae import event."""

    id: int
    kind: str
    source: str
    server_name: str | None
    observed_at: datetime


class HaremKeyImportResult(MOAModel):
    """Summary of one persisted `$mmy=` keyed-harem page."""

    import_event_id: int
    server_name: str
    account_name: str
    entries_imported: int
    entries_linked: int
    observed_at: datetime
    scan_id: int | None = None
    page_number: int | None = None
    page_count: int | None = None


class HaremKeyObservation(MOAModel):
    """The latest imported key state for one harem entry."""

    character_name: str
    character: CatalogCharacter | None
    key_type: str
    key_count: int
    kakera_value: int | None
    observed_at: datetime


class HaremScanProgress(MOAModel):
    """The completeness state of one multi-page keyed-harem import."""

    id: int
    server_name: str
    account_name: str
    expected_page_count: int | None
    imported_pages: tuple[int, ...]
    completed_at: datetime | None

    @property
    def is_complete(self) -> bool:
        if self.expected_page_count is None:
            return False
        return self.imported_pages == tuple(range(1, self.expected_page_count + 1))


class PlayerBonusImportResult(MOAModel):
    """Summary of one persisted `$bonus` player-state snapshot."""

    import_event_id: int
    server_name: str
    account_name: str
    observed_at: datetime


class PlayerBonusObservation(MOAModel):
    """The latest imported `$bonus` snapshot for one account on one server."""

    server_name: str
    account_name: str
    metrics: tuple[PlayerBonusMetric, ...]
    rolls_per_hour_bonus: int | None
    wishlist_slot_bonus: int | None
    wish_spawn_bonus_percent: int | None
    starwish_spawn_bonus_percent: int | None
    starwish_total_spawn_bonus_percent: int | None
    starwish_slot_bonus: int | None
    additional_wish_key_chance_percent: int | None
    kakera_max_power_percent: int | None
    kakera_button_power_cost_percent: int | None
    starwish_kakera_button_bonus_percent: int | None
    light_kakera_minimum: int | None
    light_kakera_maximum: int | None
    observed_at: datetime


class WishlistImportResult(MOAModel):
    """Summary of one persisted `$wl` account-state snapshot."""

    import_event_id: int
    server_name: str
    account_name: str
    observed_at: datetime


class WishlistObservation(MOAModel):
    """The latest imported wishlist snapshot for one account on one server."""

    server_name: str
    account_name: str
    wishlist_count: int
    wishlist_capacity: int
    starwish_count: int
    starwish_capacity: int
    entries: tuple[WishlistEntry, ...]
    observed_at: datetime


class DisableListImportResult(MOAModel):
    """Summary of one persisted `$dl` account-state snapshot."""

    import_event_id: int
    server_name: str
    account_name: str
    observed_at: datetime


class DisableListObservation(MOAModel):
    """The latest imported disable-list snapshot for one server/account pair."""

    server_name: str
    account_name: str
    slots_used: int
    slots_capacity: int
    total_disabled: int
    disabled_wa: int
    disabled_ha: int
    disabled_wg: int
    disabled_hg: int
    wa_pool_limit: int | None
    ha_pool_limit: int | None
    western_disabled: bool
    irl_disabled: bool
    entries: tuple[DisableListEntry, ...]
    observed_at: datetime


class RollabilityImportResult(MOAModel):
    """Summary of a persisted unavailable-character `$topx` page."""

    import_event_id: int
    server_name: str
    account_name: str
    characters_imported: int
    observed_at: datetime


class UnavailableCharacterObservation(MOAModel):
    """A direct Mudae observation that a character cannot currently roll."""

    character: CatalogCharacter
    claim_rank: int
    reason: str | None
    observed_at: datetime


class KeyFarmRecommendation(MOAModel):
    """A transparent, relative key-farm priority based on imported player state."""

    character_name: str
    kakera_value: int
    key_type: str
    key_count: int
    wishlist_status: str
    rollability: str
    spawn_bonus_percent: int
    relative_spawn_multiplier: float
    additional_key_chance_percent: int
    value_weighted_opportunity_index: float


class KeyProgressObservation(MOAModel):
    """An imported harem key count interpreted through universal key rules."""

    character_name: str
    key_count: int
    current_tier: str
    next_milestone_key_count: int | None
    keys_until_next_milestone: int | None
    next_effects: tuple[str, ...]
    kakera_value: int | None


class KakeraStateImportResult(MOAModel):
    """Summary of one persisted `$k` account-state snapshot."""

    import_event_id: int
    server_name: str
    account_name: str
    observed_at: datetime


class KakeraStateObservation(MOAModel):
    """The latest imported Kakera balance and badge state for one account."""

    server_name: str
    account_name: str
    kakera_balance: int
    badges: tuple[BadgeLevel, ...]
    observed_at: datetime


class KakeraProgressPoint(MOAModel):
    """One historical `$k` observation used for progression measurement."""

    kakera_balance: int
    max_badge_count: int
    observed_at: datetime


class KakeraProgressSummary(MOAModel):
    """Measured Kakera progression from imported `$k` snapshots."""

    server_name: str
    account_name: str
    observations: tuple[KakeraProgressPoint, ...]
    kakera_change: int | None
    elapsed_seconds: int | None
    kakera_per_day: float | None


class PersonalRareImportResult(MOAModel):
    """Summary of one persisted `$persr` import."""

    import_event_id: int
    server_name: str
    account_name: str
    observed_at: datetime


class PersonalRareObservation(MOAModel):
    """The latest account-specific claimed-character rarity setting."""

    server_name: str
    account_name: str
    personal_rare_multiplier: int
    observed_at: datetime


class TowerStateImportResult(MOAModel):
    """Summary of one persisted `$kt` account-state snapshot."""

    import_event_id: int
    server_name: str
    account_name: str
    observed_at: datetime


class TowerStateObservation(MOAModel):
    """The latest imported Kakera Tower state for one account."""

    server_name: str
    account_name: str
    current_level: int
    completed_towers: int
    next_level_cost: int
    kakera_balance: int
    built_perk_ids: tuple[int, ...]
    observed_at: datetime


class KakeralootStateImportResult(MOAModel):
    """Summary of one persisted `$lk` account-state snapshot."""

    import_event_id: int
    server_name: str
    account_name: str
    observed_at: datetime


class KakeralootStateObservation(KakeralootStateSnapshot):
    """The latest imported `$lk` snapshot for one account."""

    server_name: str
    account_name: str
    observed_at: datetime


class KakeralootSettingsImportResult(MOAModel):
    """Summary of one persisted `$infokl` server-configuration import."""

    import_event_id: int
    server_name: str
    observed_at: datetime


class KakeralootSettingsObservation(MOAModel):
    """The latest imported Kakeraloot pricing configuration for a server."""

    server_name: str
    loot_cost: int
    quantity_quality_base_cost: int
    quantity_quality_level_increment: int
    observed_at: datetime


class TimerStateImportResult(MOAModel):
    """Summary of one persisted `$tu` account-timer import."""

    import_event_id: int
    server_name: str
    account_name: str
    observed_at: datetime


class TimerStateObservation(MOAModel):
    """The newest imported `$tu` action snapshot for one account."""

    server_name: str
    account_name: str
    snapshot: TimerStateSnapshot
    observed_at: datetime


class ActionReadiness(MOAModel):
    """Actions supported by a sufficiently recent imported `$tu` snapshot."""

    server_name: str
    account_name: str
    observed_at: datetime | None
    snapshot_age_seconds: int | None
    is_stale: bool
    status: str
    available_actions: tuple[str, ...]
    upcoming_events: tuple[tuple[str, int], ...]


class RollAnalysis(MOAModel):
    """Imported-account context for one freshly copied Mudae roll."""

    server_name: str
    account_name: str
    character_name: str
    series: str
    claim_rank: int | None
    kakera_value: int | None
    displayed_key_type: str | None
    displayed_key_count: int | None
    wishlist_state: str
    keyed_harem_state: str
    rollability_state: str
    claim_window_state: str


class AutomaticImportResult(MOAModel):
    """Result of routing one recognized raw Mudae message into MOA."""

    kind: str
    imported_count: int
    message: str


class RollImportResult(MOAModel):
    """Summary of one persisted raw Mudae roll."""

    import_event_id: int
    server_name: str
    account_name: str
    character_id: int
    observed_at: datetime


class KakeraReactionImportResult(MOAModel):
    """Summary of one persisted standalone Kakera-reaction receipt."""

    import_event_id: int
    server_name: str
    account_name: str
    observed_at: datetime


class KakeraReactionObservation(MOAModel):
    """One Kakera payout directly reported by Mudae after a reaction."""

    reaction_label: str
    kakera_earned: int
    observed_at: datetime


class KakeraReactionSummary(MOAModel):
    """Descriptive totals from standalone Kakera-reaction receipts."""

    receipt_count: int
    total_kakera_earned: int
    average_kakera_earned: float | None
    highest_kakera_earned: int | None
    by_reaction: tuple[tuple[str, int, int], ...]


class StoredRollObservation(MOAModel):
    """One timestamped Mudae roll stored for an account context."""

    character: CatalogCharacter
    claim_rank: int | None
    kakera_value: int | None
    observed_at: datetime


class RollStatistics(MOAModel):
    """Simple descriptive statistics from stored roll observations."""

    server_name: str
    account_name: str
    roll_count: int
    best_claim_rank: int | None
    average_claim_rank: float | None
    average_kakera_value: float | None
    highest_kakera_value: int | None


class AccountOverview(MOAModel):
    """A non-destructive summary of the latest imported account state."""

    server_name: str
    account_name: str
    kakera_balance: int | None
    kakera_balance_source: str | None
    personal_rare_multiplier: int | None
    server_rare_multiplier: int | None
    effective_rare_multiplier: int | None
    rare_multiplier_source: str | None
    badge_count: int
    max_badge_count: int
    tower_level: int | None
    completed_towers: int | None
    next_tower_cost: int | None
    tower_shortfall: int | None
    kakeraloots_unlocked: bool | None
    missing_kakeraloot_prerequisites: tuple[str, ...]
    has_kakeraloots: bool | None
    kakeraloot_status_note: str | None
    quantity_level: int | None
    quality_level: int | None
    loot_usage_count: int | None
    wishlist_count: int | None
    wishlist_capacity: int | None
    starwish_count: int | None
    starwish_capacity: int | None
    disable_slots_used: int | None
    disable_slots_capacity: int | None
    keyed_harem_count: int


class AccountComparisonRow(MOAModel):
    """One factual account-state comparison row."""

    label: str
    left_value: str
    right_value: str


class AccountComparison(MOAModel):
    """A read-only comparison of two imported account contexts."""

    left_server_name: str
    left_account_name: str
    right_server_name: str
    right_account_name: str
    rows: tuple[AccountComparisonRow, ...]


class ServerSettingsImportResult(MOAModel):
    """Summary of one persisted `$settings` snapshot."""

    import_event_id: int
    server_name: str
    observed_at: datetime


class ServerSettingsObservation(MOAModel):
    """The latest imported `$settings` snapshot for one server."""

    server_name: str
    server_premium: bool
    prefix: str
    language: str
    claim_reset_minutes: int
    reset_minute: str
    reset_shift_minutes: int
    rolls_per_hour: int
    claim_reaction_expiry_seconds: int
    claimed_character_rarity_multiplier: int
    kakera_bonus_percent: int
    sphere_bonus_percent: int
    game_mode: int
    channel_instance: int
    metrics: tuple[ServerSettingMetric, ...]
    observed_at: datetime


class ServerSettingComparison(MOAModel):
    """One directly comparable setting from two imported server snapshots."""

    label: str
    left_value: str
    right_value: str
    matches: bool


class ServerSettingsComparison(MOAModel):
    """A factual comparison of two latest `$settings` snapshots."""

    left_server_name: str
    right_server_name: str
    entries: tuple[ServerSettingComparison, ...]
