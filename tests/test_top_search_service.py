from datetime import datetime, timezone

import pytest

from moa.models.catalog import (
    CatalogCharacter,
    HaremKeyObservation,
    RankedCatalogCharacter,
    UnavailableCharacterObservation,
)
from moa.services.top_search_service import TopSearchService


class InMemoryTopCatalog:
    def __init__(self) -> None:
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

    def top(self, limit: int | None):
        return self._top if limit is None else self._top[:limit]

    def harem_keys(self, server_name: str, account_name: str):
        return self._harem

    def unavailable_characters(self, server_name: str, account_name: str):
        return self._unavailable


def test_top_search_cross_references_keyed_and_unavailable_evidence() -> None:
    service = TopSearchService(InMemoryTopCatalog())

    keyed = service.search(server_name="Lake", account_name="ernieuuu", keyed_only=True)
    unavailable = service.search(server_name="Lake", account_name="ernieuuu", unavailable_only=True)

    assert [entry.character.name for entry in keyed] == ["Zero Two"]
    assert [entry.character.name for entry in unavailable] == ["Rem"]
    assert keyed[0].keyed is True
    assert keyed[0].unavailable is False


def test_top_search_filters_series_and_keeps_unknown_account_state_explicit() -> None:
    service = TopSearchService(InMemoryTopCatalog())

    series = service.search(series="re:zero")

    assert [entry.character.name for entry in series] == ["Rem"]
    assert series[0].keyed is None
    assert series[0].unavailable is None


def test_top_search_requires_account_context_for_evidence_filters() -> None:
    with pytest.raises(ValueError, match="--server and --account"):
        TopSearchService(InMemoryTopCatalog()).search(keyed_only=True)
