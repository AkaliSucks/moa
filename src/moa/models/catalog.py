"""Models for MOA's local character catalog and rank history."""

from datetime import datetime

from moa.models.base import MOAModel
from moa.models.character import (
    BadgeLevel,
    DisableListEntry,
    KakeralootStateSnapshot,
    PlayerBonusMetric,
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
