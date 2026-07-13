"""Search imported keyed-harem observations without inferring missing ownership."""

from moa.models.catalog import HaremKeyObservation
from moa.services.catalog_service import CatalogService


class HaremSearchService:
    """Apply explicit filters to one account's imported keyed-harem evidence."""

    _SORTS = {"kakera", "keys", "name", "observed"}

    def __init__(self, catalog_service: CatalogService | None = None) -> None:
        self._catalog = catalog_service or CatalogService()

    def search(
        self,
        server_name: str,
        account_name: str,
        *,
        series: str | None = None,
        exact_series: bool = False,
        key_type: str | None = None,
        min_keys: int | None = None,
        max_keys: int | None = None,
        min_kakera: int | None = None,
        unresolved_only: bool = False,
        sort_by: str = "kakera",
        limit: int | None = None,
    ) -> tuple[HaremKeyObservation, ...]:
        """Return filtered harem entries, with no fallback from unresolved data."""
        normalized_sort = sort_by.strip().casefold()
        if normalized_sort not in self._SORTS:
            choices = ", ".join(sorted(self._SORTS))
            raise ValueError(f"Unknown harem sort `{sort_by}`. Choose from: {choices}.")
        if min_keys is not None and min_keys < 0:
            raise ValueError("Minimum keys cannot be negative.")
        if max_keys is not None and max_keys < 0:
            raise ValueError("Maximum keys cannot be negative.")
        if min_keys is not None and max_keys is not None and min_keys > max_keys:
            raise ValueError("Minimum keys cannot exceed maximum keys.")
        if min_kakera is not None and min_kakera < 0:
            raise ValueError("Minimum Kakera cannot be negative.")
        if limit is not None and limit <= 0:
            raise ValueError("Harem result limit must be positive.")

        entries = self._catalog.harem_keys(server_name, account_name)
        normalized_series = series.strip().casefold() if series else None
        normalized_key_type = key_type.strip().casefold() if key_type else None

        filtered = [
            entry
            for entry in entries
            if self._matches(
                entry,
                normalized_series=normalized_series,
                exact_series=exact_series,
                key_type=normalized_key_type,
                min_keys=min_keys,
                max_keys=max_keys,
                min_kakera=min_kakera,
                unresolved_only=unresolved_only,
            )
        ]
        filtered.sort(key=self._sort_key(normalized_sort))
        return tuple(filtered[:limit] if limit is not None else filtered)

    @staticmethod
    def _matches(
        entry: HaremKeyObservation,
        *,
        normalized_series: str | None,
        exact_series: bool,
        key_type: str | None,
        min_keys: int | None,
        max_keys: int | None,
        min_kakera: int | None,
        unresolved_only: bool,
    ) -> bool:
        if unresolved_only and entry.character is not None:
            return False
        if key_type is not None and entry.key_type.casefold() != key_type:
            return False
        if min_keys is not None and entry.key_count < min_keys:
            return False
        if max_keys is not None and entry.key_count > max_keys:
            return False
        if min_kakera is not None and (
            entry.kakera_value is None or entry.kakera_value < min_kakera
        ):
            return False
        if normalized_series is None:
            return True
        if entry.character is None:
            return False
        candidate = entry.character.series.casefold()
        return candidate == normalized_series if exact_series else normalized_series in candidate

    @staticmethod
    def _sort_key(sort_by: str):
        if sort_by == "keys":
            return lambda entry: (-entry.key_count, entry.character_name.casefold())
        if sort_by == "name":
            return lambda entry: entry.character_name.casefold()
        if sort_by == "observed":
            return lambda entry: (-entry.observed_at.timestamp(), entry.character_name.casefold())
        return lambda entry: (
            entry.kakera_value is None,
            -(entry.kakera_value or 0),
            -entry.key_count,
            entry.character_name.casefold(),
        )
