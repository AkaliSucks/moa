from datetime import datetime, timezone

import pytest

from moa.models.catalog import (
    CatalogCharacter,
    HaremKeyObservation,
    OwnedCharacterObservation,
    RankedCatalogCharacter,
    TopOwnerObservation,
    UnavailableCharacterObservation,
    WishlistObservation,
)
from moa.models.character import WishlistEntry
from moa.services.top_search_service import TopSearchService


class InMemoryTopCatalog:
    def __init__(self, owned_scan_complete: bool = True) -> None:
        observed_at = datetime(2026, 7, 12, tzinfo=timezone.utc)
        self._top = (
            RankedCatalogCharacter(
                character=CatalogCharacter(id=1, name="Zero Two", series="Darling in the Franxx", gender=None, roulette=None),
                claim_rank=2,
                like_rank=4,
                observed_at=observed_at,
            ),
            RankedCatalogCharacter(
                character=CatalogCharacter(id=2, name="Rem", series="Re:Zero", gender=None, roulette=None),
                claim_rank=3,
                like_rank=2,
                observed_at=observed_at,
            ),
            RankedCatalogCharacter(
                character=CatalogCharacter(id=3, name="Albedo", series="Overlord", gender=None, roulette=None),
                claim_rank=4,
                like_rank=3,
                observed_at=observed_at,
            ),
        )
        self._harem = (
            HaremKeyObservation(
                character_name="Zero Two",
                character=self._top[0].character,
                key_type="gold",
                key_count=7,
                kakera_value=1440,
                observed_at=observed_at,
            ),
        )
        self._unavailable = (
            UnavailableCharacterObservation(
                character=self._top[1].character,
                claim_rank=3,
                reason="$togglewestern",
                observed_at=observed_at,
            ),
        )
        self._owned = (
            OwnedCharacterObservation(
                character_name="Rem",
                character=self._top[1].character,
                claim_rank=3,
                kakera_value=1426,
                observed_at=observed_at,
            ),
        )
        self._owned_scan_complete = owned_scan_complete
        self._wishlist = None

    def top(self, limit: int | None):
        return self._top if limit is None else self._top[:limit]

    def harem_keys(self, server_name: str, account_name: str):
        return self._harem

    def unavailable_characters(self, server_name: str, account_name: str):
        return self._unavailable

    def owned_characters(self, server_name: str, account_name: str):
        return self._owned

    def has_complete_harem_scan(self, server_name: str, account_name: str, scan_kind: str = "keys"):
        return scan_kind == "owned" and self._owned_scan_complete

    def wishlist(self, server_name: str, account_name: str):
        return self._wishlist

    def top_owner_observations(self, server_name: str):
        return tuple(
            TopOwnerObservation(
                character=entry.character,
                owner_name=entry.owner_name,
                observed_at=entry.observed_at,
            )
            for entry in self._top
            if entry.owner_name
        )


def test_top_search_cross_references_keyed_and_unavailable_evidence() -> None:
    service = TopSearchService(InMemoryTopCatalog())

    keyed = service.search(server_name="Lake", account_name="ernieuuu", keyed_only=True)
    unavailable = service.search(server_name="Lake", account_name="ernieuuu", unavailable_only=True)

    assert [entry.character.name for entry in keyed] == ["Zero Two"]
    assert [entry.character.name for entry in unavailable] == ["Rem"]
    assert keyed[0].keyed is True
    assert keyed[0].unavailable is False
    assert keyed[0].unavailable_reason is None
    assert unavailable[0].unavailable_reason == "$togglewestern"


def test_top_search_uses_topo_owner_as_unavailable_reason() -> None:
    catalog = InMemoryTopCatalog()
    claimed = catalog._top[2]
    catalog._top = catalog._top[:2] + (
        RankedCatalogCharacter(
            character=claimed.character,
            claim_rank=claimed.claim_rank,
            like_rank=claimed.like_rank,
            observed_at=claimed.observed_at,
            owner_name="xuppii",
        ),
    )

    unavailable = TopSearchService(catalog).search(
        server_name="Lake", account_name="ernieuuu", unavailable_only=True
    )

    assert [entry.character.name for entry in unavailable] == ["Rem", "Albedo"]
    assert unavailable[1].unavailable_reason == "claimed by xuppii"


def test_top_search_retains_unclaimed_topo_state() -> None:
    catalog = InMemoryTopCatalog()
    catalog._topo = (
        TopOwnerObservation(
            character=catalog._top[2].character,
            owner_name=None,
            observed_at=catalog._top[2].observed_at,
        ),
    )
    catalog.top_owner_observations = lambda server_name: catalog._topo

    albedo = next(
        entry
        for entry in TopSearchService(catalog).search(
            server_name="Lake", account_name="ernieuuu"
        )
        if entry.character.name == "Albedo"
    )

    assert albedo.topo_observed is True
    assert albedo.owner_name is None
    assert albedo.unavailable is False
    assert albedo.rollability_status == "Enabled"


def test_top_search_marks_wishlist_as_rollability_status_and_overrides_disabled() -> None:
    catalog = InMemoryTopCatalog()
    catalog._wishlist = WishlistObservation(
        server_name="Lake",
        account_name="ernieuuu",
        wishlist_count=1,
        wishlist_capacity=15,
        starwish_count=0,
        starwish_capacity=1,
        entries=(
            WishlistEntry(
                name="Albedo",
                is_starwish=False,
                is_owned_marker_present=False,
                kakera_marker_present=False,
            ),
        ),
        observed_at=catalog._top[2].observed_at,
    )
    catalog._unavailable = catalog._unavailable + (
        UnavailableCharacterObservation(
            character=catalog._top[2].character,
            claim_rank=4,
            reason="$togglewestern",
            observed_at=catalog._top[2].observed_at,
        ),
    )

    albedo = next(
        entry
        for entry in TopSearchService(catalog).search(
            server_name="Lake", account_name="ernieuuu"
        )
        if entry.character.name == "Albedo"
    )

    assert albedo.rollability_status == "Wishlist"
    assert albedo.unavailable is False


def test_top_search_treats_selected_account_and_alt_accounts_as_owned() -> None:
    catalog = InMemoryTopCatalog()
    claimed = catalog._top[2]
    catalog._top = catalog._top[:2] + (
        RankedCatalogCharacter(
            character=claimed.character,
            claim_rank=claimed.claim_rank,
            like_rank=claimed.like_rank,
            observed_at=claimed.observed_at,
            owner_name="ernie_alt",
        ),
    )

    entries = TopSearchService(catalog).search(
        server_name="Lake",
        account_name="ernieuuu",
        owned_account_names=("ernieuuu", "ernie_alt"),
    )
    albedo = next(entry for entry in entries if entry.character.name == "Albedo")

    assert albedo.owner_is_self is True
    assert albedo.unavailable is False
    assert albedo.unavailable_reason is None


def test_top_search_filters_to_directly_observed_owned_characters() -> None:
    service = TopSearchService(InMemoryTopCatalog())

    owned = service.search(server_name="Lake", account_name="ernieuuu", owned_only=True)

    assert [entry.character.name for entry in owned] == ["Rem"]
    assert owned[0].owned is True
    assert owned[0].keyed is False


def test_top_search_filters_to_unowned_characters_only_after_complete_scan() -> None:
    service = TopSearchService(InMemoryTopCatalog())

    unowned = service.search(server_name="Lake", account_name="ernieuuu", unowned_only=True)

    assert [entry.character.name for entry in unowned] == ["Zero Two", "Albedo"]
    assert all(entry.owned is False for entry in unowned)


def test_top_search_rejects_unowned_filter_without_complete_scan() -> None:
    with pytest.raises(ValueError, match="complete owned harem scan"):
        TopSearchService(InMemoryTopCatalog(owned_scan_complete=False)).search(
            server_name="Lake", account_name="ernieuuu", unowned_only=True
        )


def test_top_search_filters_series_and_keeps_unknown_account_state_explicit() -> None:
    service = TopSearchService(InMemoryTopCatalog())

    series = service.search(series="re:zero")

    assert [entry.character.name for entry in series] == ["Rem"]
    assert series[0].keyed is None
    assert series[0].owned is None
    assert series[0].unavailable is None
    assert series[0].unavailable_reason is None


def test_top_search_requires_account_context_for_evidence_filters() -> None:
    with pytest.raises(ValueError, match="--server and --account"):
        TopSearchService(InMemoryTopCatalog()).search(keyed_only=True)
    with pytest.raises(ValueError, match="--server and --account"):
        TopSearchService(InMemoryTopCatalog()).search(owned_only=True)
    with pytest.raises(ValueError, match="--server and --account"):
        TopSearchService(InMemoryTopCatalog()).search(unowned_only=True)
