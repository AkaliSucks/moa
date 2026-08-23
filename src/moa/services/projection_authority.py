"""Shared durable projection kind-to-target authority."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping


@dataclass(frozen=True, slots=True)
class ProjectionKindAuthority:
    """The persisted identity fields shared by a durable projection kind."""

    projection_kind: str
    target_table: str


ANTIDISABLE_PAGE_PROJECTION: Final = ProjectionKindAuthority(
    "catalog.antidisable_page", "import_events"
)
CLAIM_PROJECTION: Final = ProjectionKindAuthority("catalog.claim", "claim_observations")
DISABLELIST_PROJECTION: Final = ProjectionKindAuthority(
    "catalog.disablelist", "disablelist_observations"
)
KAKERALOOT_SETTINGS_PROJECTION: Final = ProjectionKindAuthority(
    "catalog.kakeraloot_settings", "kakeraloot_settings_observations"
)
KAKERA_STATE_PROJECTION: Final = ProjectionKindAuthority(
    "catalog.kakera_state", "kakera_state_observations"
)
KAKERALOOT_STATE_PROJECTION: Final = ProjectionKindAuthority(
    "catalog.kakeraloot_state", "kakeraloot_state_observations"
)
MUDAPINS_PROJECTION: Final = ProjectionKindAuthority("catalog.mudapins", "mudapin_observations")
PLAYER_BONUS_PROJECTION: Final = ProjectionKindAuthority(
    "catalog.player_bonus", "player_bonus_observations"
)
PROFILE_PROJECTION: Final = ProjectionKindAuthority("catalog.profile", "profile_observations")
ROLL_PROJECTION: Final = ProjectionKindAuthority("catalog.roll", "roll_observations")
ROLL_KEY_PROJECTION: Final = ProjectionKindAuthority(
    "catalog.roll_key", "harem_key_observations"
)
ROLL_RANK_PROJECTION: Final = ProjectionKindAuthority("catalog.roll_rank", "rank_snapshots")
ROLL_SERVER_CHARACTER_PROJECTION: Final = ProjectionKindAuthority(
    "catalog.roll_server_character", "server_character_observations"
)
SERVER_SETTINGS_PROJECTION: Final = ProjectionKindAuthority(
    "catalog.server_settings", "server_settings_observations"
)
SPHERE_RESULT_PROJECTION: Final = ProjectionKindAuthority(
    "catalog.sphere_result", "sphere_result_observations"
)
TIMER_STATE_PROJECTION: Final = ProjectionKindAuthority(
    "catalog.timer_state", "timer_state_observations"
)
TOWER_STATE_PROJECTION: Final = ProjectionKindAuthority(
    "catalog.tower_state", "tower_state_observations"
)
WISHLIST_PROJECTION: Final = ProjectionKindAuthority("catalog.wishlist", "wishlist_observations")

PROJECTION_AUTHORITIES: Final[tuple[ProjectionKindAuthority, ...]] = (
    ANTIDISABLE_PAGE_PROJECTION,
    CLAIM_PROJECTION,
    DISABLELIST_PROJECTION,
    KAKERALOOT_SETTINGS_PROJECTION,
    KAKERA_STATE_PROJECTION,
    KAKERALOOT_STATE_PROJECTION,
    MUDAPINS_PROJECTION,
    PLAYER_BONUS_PROJECTION,
    PROFILE_PROJECTION,
    ROLL_PROJECTION,
    ROLL_KEY_PROJECTION,
    ROLL_RANK_PROJECTION,
    ROLL_SERVER_CHARACTER_PROJECTION,
    SERVER_SETTINGS_PROJECTION,
    SPHERE_RESULT_PROJECTION,
    TIMER_STATE_PROJECTION,
    TOWER_STATE_PROJECTION,
    WISHLIST_PROJECTION,
)

PROJECTION_AUTHORITY_BY_KIND: Final[Mapping[str, ProjectionKindAuthority]] = MappingProxyType(
    {authority.projection_kind: authority for authority in PROJECTION_AUTHORITIES}
)


def get_projection_authority(projection_kind: str) -> ProjectionKindAuthority:
    """Return the exact authority for a persisted projection kind."""

    return PROJECTION_AUTHORITY_BY_KIND[projection_kind]
