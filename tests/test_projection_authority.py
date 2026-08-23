from dataclasses import FrozenInstanceError

import pytest

from moa.services.projection_authority import (
    CLAIM_PROJECTION,
    PROJECTION_AUTHORITIES,
    PROJECTION_AUTHORITY_BY_KIND,
    get_projection_authority,
)


EXPECTED_KIND_TARGETS = {
    "catalog.antidisable_page": "import_events",
    "catalog.claim": "claim_observations",
    "catalog.disablelist": "disablelist_observations",
    "catalog.kakeraloot_settings": "kakeraloot_settings_observations",
    "catalog.kakera_state": "kakera_state_observations",
    "catalog.kakeraloot_state": "kakeraloot_state_observations",
    "catalog.mudapins": "mudapin_observations",
    "catalog.player_bonus": "player_bonus_observations",
    "catalog.profile": "profile_observations",
    "catalog.roll": "roll_observations",
    "catalog.roll_key": "harem_key_observations",
    "catalog.roll_rank": "rank_snapshots",
    "catalog.roll_server_character": "server_character_observations",
    "catalog.server_settings": "server_settings_observations",
    "catalog.sphere_result": "sphere_result_observations",
    "catalog.timer_state": "timer_state_observations",
    "catalog.tower_state": "tower_state_observations",
    "catalog.wishlist": "wishlist_observations",
}


def test_projection_authority_has_exact_eighteen_kind_target_pairs() -> None:
    assert len(PROJECTION_AUTHORITIES) == 18
    assert len({authority.projection_kind for authority in PROJECTION_AUTHORITIES}) == 18
    assert {
        authority.projection_kind: authority.target_table for authority in PROJECTION_AUTHORITIES
    } == EXPECTED_KIND_TARGETS


def test_projection_authority_lookup_is_exact_and_read_only() -> None:
    for projection_kind, target_table in EXPECTED_KIND_TARGETS.items():
        authority = get_projection_authority(projection_kind)
        assert authority.target_table == target_table
        assert PROJECTION_AUTHORITY_BY_KIND[projection_kind] is authority

    with pytest.raises(KeyError):
        get_projection_authority("catalog.unknown")
    with pytest.raises(TypeError):
        PROJECTION_AUTHORITY_BY_KIND["catalog.unknown"] = CLAIM_PROJECTION


def test_projection_authority_descriptors_are_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        CLAIM_PROJECTION.target_table = "other"
