from datetime import datetime, timezone

import pytest

from moa.models.catalog import CatalogCharacter, HaremKeyObservation
from moa.services.harem_search_service import HaremSearchService


class InMemoryHaremCatalog:
    def __init__(self, entries: tuple[HaremKeyObservation, ...]) -> None:
        self._entries = entries

    def harem_keys(self, server_name: str, account_name: str) -> tuple[HaremKeyObservation, ...]:
        return self._entries


def _entry(
    name: str,
    series: str | None,
    keys: int,
    kakera: int | None,
    observed_at: datetime,
    key_type: str = "gold",
) -> HaremKeyObservation:
    return HaremKeyObservation(
        character_name=name,
        character=(
            CatalogCharacter(id=1, name=name, series=series, gender=None, roulette=None)
            if series is not None
            else None
        ),
        key_type=key_type,
        key_count=keys,
        kakera_value=kakera,
        observed_at=observed_at,
    )


def test_harem_search_filters_series_and_sorts_by_keys() -> None:
    observed_at = datetime(2026, 7, 12, tzinfo=timezone.utc)
    service = HaremSearchService(
        InMemoryHaremCatalog(
            (
                _entry("Rem", "Re:Zero", 5, 1426, observed_at),
                _entry("Miku Nakano", "Re:Zero Side Story", 2, 1296, observed_at),
                _entry("Albedo", "Overlord", 7, 1453, observed_at),
            )
        )
    )

    results = service.search("Lake", "ernieuuu", series="re:zero", sort_by="keys")

    assert [entry.character_name for entry in results] == ["Rem", "Miku Nakano"]


def test_harem_search_supports_thresholds_and_unresolved_entries() -> None:
    observed_at = datetime(2026, 7, 12, tzinfo=timezone.utc)
    service = HaremSearchService(
        InMemoryHaremCatalog(
            (
                _entry("Known", "Series", 4, 1200, observed_at),
                _entry("Needs details", None, 9, 1500, observed_at),
            )
        )
    )

    results = service.search(
        "Lake",
        "ernieuuu",
        min_keys=5,
        min_kakera=1400,
        unresolved_only=True,
    )

    assert [entry.character_name for entry in results] == ["Needs details"]


def test_harem_search_rejects_invalid_sort_and_ranges() -> None:
    service = HaremSearchService(InMemoryHaremCatalog(()))

    with pytest.raises(ValueError, match="Unknown harem sort"):
        service.search("Lake", "ernieuuu", sort_by="rank")
    with pytest.raises(ValueError, match="cannot exceed"):
        service.search("Lake", "ernieuuu", min_keys=5, max_keys=2)
