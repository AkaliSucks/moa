"""Typed observations parsed from Mudae character output."""

from moa.models.base import MOAModel


class RankedCharacter(MOAModel):
    """One character entry from a global Mudae ranking page."""

    name: str
    series: str
    claim_rank: int


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
