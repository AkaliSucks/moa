"""Typed observations parsed from Mudae character output."""

from moa.models.base import MOAModel


class RankedCharacter(MOAModel):
    """One character entry from a global Mudae ranking page."""

    name: str
    series: str
    claim_rank: int
    owner_name: str | None = None


class TopPage(MOAModel):
    """A single parsed page from Mudae's `$top` output."""

    limit: int | None
    page_number: int | None
    page_count: int | None
    characters: tuple[RankedCharacter, ...]


class CharacterDetails(MOAModel):
    """The currently observed fields from a Mudae `$im` response."""

    name: str
    series: str
    gender: str | None
    roulette: str | None
    kakera_value: int | None
    claim_rank: int | None
    like_rank: int | None


class RollObservation(MOAModel):
    """The currently observed fields from one Mudae roll card."""

    name: str
    series: str
    claim_rank: int | None
    kakera_value: int | None
    displayed_key_type: str | None = None
    displayed_key_count: int | None = None


class KakeraReactionReceipt(MOAModel):
    """One standalone Kakera amount reported after a player reacts."""

    reaction_label: str
    account_name: str
    kakera_earned: int


class MudaeMessageDetection(MOAModel):
    """A conservative classification for one raw Mudae message."""

    kind: str
    reason: str


class HaremKeyEntry(MOAModel):
    """One keyed character displayed by a Mudae keyed-harem view."""

    name: str
    key_type: str
    key_count: int
    kakera_value: int | None = None


class HaremKeyPage(MOAModel):
    """A single parsed page from a Mudae keyed-harem view."""

    page_number: int | None
    page_count: int | None
    entries: tuple[HaremKeyEntry, ...]
    total_harem_value: int | None = None


class RankedHaremEntry(MOAModel):
    """One character directly shown in a ranked `$mm` harem page."""

    name: str
    claim_rank: int
    kakera_value: int | None = None
    roulette_types: tuple[str, ...] = ()
    key_type: str | None = None
    key_count: int | None = None


class RankedHaremPage(MOAModel):
    """A page of direct owned-character evidence from `$mmr`/`$mmrk`."""

    page_number: int | None
    page_count: int | None
    entries: tuple[RankedHaremEntry, ...]


class PlayerBonusMetric(MOAModel):
    """One labelled modifier reported by Mudae's `$bonus` command."""

    label: str
    detail: str


class PlayerBonusSnapshot(MOAModel):
    """Typed, account-scoped values extracted from one `$bonus` response."""

    metrics: tuple[PlayerBonusMetric, ...]
    rolls_per_hour_bonus: int | None = None
    wishlist_slot_bonus: int | None = None
    wish_spawn_bonus_percent: int | None = None
    starwish_spawn_bonus_percent: int | None = None
    starwish_total_spawn_bonus_percent: int | None = None
    starwish_slot_bonus: int | None = None
    additional_wish_key_chance_percent: int | None = None
    kakera_max_power_percent: int | None = None
    kakera_button_power_cost_percent: int | None = None
    starwish_kakera_button_bonus_percent: int | None = None
    light_kakera_minimum: int | None = None
    light_kakera_maximum: int | None = None


class WishlistEntry(MOAModel):
    """One character in a Mudae `$wl` response."""

    name: str
    is_starwish: bool
    is_owned_marker_present: bool
    kakera_marker_present: bool


class WishlistSnapshot(MOAModel):
    """Account-scoped wishlist and Starwish state parsed from `$wl`."""

    wishlist_count: int
    wishlist_capacity: int
    starwish_count: int
    starwish_capacity: int
    entries: tuple[WishlistEntry, ...]


class AntidisablePage(MOAModel):
    """One page from the account-scoped `$adl` series list."""

    page_number: int | None
    page_count: int | None
    slots_used: int
    slots_capacity: int
    antidisabled_character_count: int | None
    series_names: tuple[str, ...]


class DisableListEntry(MOAModel):
    """One disabled Mudae bundle shown by `$dl`."""

    name: str
    disabled_count: int


class DisableListSnapshot(MOAModel):
    """Account-scoped roll-pool settings parsed from one `$dl` response."""

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


class UnavailableCharacter(MOAModel):
    """One character Mudae marks unavailable in a `$topx` response."""

    name: str
    series: str
    claim_rank: int
    reason: str | None


class UnavailableCharacterPage(MOAModel):
    """A single parsed page from Mudae's unavailable-character `$topx` output."""

    limit: int | None
    page_number: int | None
    page_count: int | None
    characters: tuple[UnavailableCharacter, ...]


class BadgeLevel(MOAModel):
    """One displayed Kakera badge level from `$k`."""

    badge_name: str
    level: int
    max_reached: bool


class KakeraStateSnapshot(MOAModel):
    """Account-scoped Kakera balance and badge state parsed from `$k`."""

    kakera_balance: int
    badges: tuple[BadgeLevel, ...]


class PersonalRareSnapshot(MOAModel):
    """Account-specific claimed-character rarity setting parsed from `$persr`."""

    personal_rare_multiplier: int


class TowerStateSnapshot(MOAModel):
    """Account-scoped Kakera Tower state parsed from `$kt`."""

    current_level: int
    next_level_cost: int
    kakera_balance: int
    built_perk_ids: tuple[int, ...]
    completed_towers: int | None = None


class SphereGain(MOAModel):
    """One color-coded sphere payout reported by `$oq`."""

    sphere_type: str
    amount: int
    is_free: bool = False


class SphereResultSnapshot(MOAModel):
    """Account-scoped sphere gains and stock from one `$oq` result."""

    clicks_available: int | None
    click_window_minutes: int | None
    purple_target: int | None
    purple_total: int | None
    gains: tuple[SphereGain, ...]
    total_gained: int
    stock: int | None


class KakeralootStateSnapshot(MOAModel):
    """Account-scoped Kakeraloot progress parsed from `$lk`."""

    has_kakeraloots: bool = True
    status_note: str | None = None
    rolls_stacked: int | None = None
    disable_wa_ha_reduction: int | None = None
    disable_wg_hg_reduction: int | None = None
    protected_wish_level: int | None = None
    protected_wish_denominator: int | None = None
    mudapins: int | None = None
    rt_cooldown_reduction_hours: int | None = None
    permanent_roll_bonus: int | None = None
    star_branches: int | None = None
    starwish_slots_from_branches: int | None = None
    quantity_level: int | None = None
    quality_level: int | None = None
    usage_count: int | None = None
    kakera_balance: int | None = None


class KakeralootSettingsSnapshot(MOAModel):
    """Server-scoped Kakeraloot costs parsed from `$infokl`."""

    loot_cost: int
    quantity_quality_base_cost: int
    quantity_quality_level_increment: int


class TimerStateSnapshot(MOAModel):
    """Account-scoped action timers parsed from `$tu`."""

    can_claim_now: bool | None
    claim_reset_minutes: int | None
    rolls_left: int | None
    rolls_reset_minutes: int | None
    rolls_reset_stock: int | None
    vote_reset_minutes: int | None
    daily_reset_minutes: int | None
    daily_kakera_ready: bool | None
    rt_available: bool | None
    can_react_kakera_now: bool | None
    reaction_power_percent: int | None
    kakera_button_power_cost_percent: int | None
    soulmate_button_power_cost_percent: int | None
    kakera_stock: int | None
    gold_key_stock_remaining: int | None
    gold_key_reset_minutes: int | None
    bku_reset_probability_percent: int | None
    oh_remaining: int | None
    oc_remaining: int | None
    oq_remaining: int | None
    oq_stored: int | None
    ot_remaining: int | None
    ouro_refill_minutes: int | None
    rolls_reset_status: str | None = None
    rolls_per_hour_limit: int | None = None


class ServerSettingMetric(MOAModel):
    """One labelled option reported by Mudae's `$settings` command."""

    label: str
    value: str


class ServerSettingsSnapshot(MOAModel):
    """Server-scoped settings parsed from one `$settings` response."""

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
