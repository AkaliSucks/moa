"""Cross-reference imported global ranks with account-scoped evidence."""

from moa.models.catalog import CatalogTopSearchEntry
from moa.services.catalog_service import CatalogService


class TopSearchService:
    """Search imported `$top` rows without inferring ownership or rollability."""

    _SORTS = {"rank", "name"}

    def __init__(self, catalog_service: CatalogService | None = None) -> None:
        self._catalog = catalog_service or CatalogService()

    def search(
        self,
        *,
        server_name: str | None = None,
        account_name: str | None = None,
        series: str | None = None,
        exact_series: bool = False,
        keyed_only: bool = False,
        unavailable_only: bool = False,
        sort_by: str = "rank",
        limit: int | None = 15,
    ) -> tuple[CatalogTopSearchEntry, ...]:
        """Return ranked characters with optional direct account evidence."""
        normalized_sort = sort_by.strip().casefold()
        if normalized_sort not in self._SORTS:
            raise ValueError("Unknown top sort. Choose from: name, rank.")
        if limit is not None and limit <= 0:
            raise ValueError("Top result limit must be positive.")

        scoped = bool(server_name and account_name)
        if bool(server_name) != bool(account_name):
            raise ValueError("--server and --account must be supplied together.")
        if (keyed_only or unavailable_only) and not scoped:
            raise ValueError("--server and --account are required for account evidence filters.")

        keyed_names: set[str] | None = None
        unavailable_names: set[str] | None = None
        if scoped:
            keyed_names = {
                entry.character_name.casefold()
                for entry in self._catalog.harem_keys(server_name, account_name)
            }
            unavailable_names = {
                entry.character.name.casefold()
                for entry in self._catalog.unavailable_characters(server_name, account_name)
            }

        normalized_series = series.strip().casefold() if series else None
        ranked = self._catalog.top(None)
        results: list[CatalogTopSearchEntry] = []
        for entry in ranked:
            if normalized_series is not None:
                candidate_series = entry.character.series.casefold()
                if exact_series:
                    if candidate_series != normalized_series:
                        continue
                elif normalized_series not in candidate_series:
                    continue

            name = entry.character.name.casefold()
            keyed = name in keyed_names if keyed_names is not None else None
            unavailable = name in unavailable_names if unavailable_names is not None else None
            if keyed_only and not keyed:
                continue
            if unavailable_only and not unavailable:
                continue
            results.append(
                CatalogTopSearchEntry(
                    character=entry.character,
                    claim_rank=entry.claim_rank,
                    like_rank=entry.like_rank,
                    observed_at=entry.observed_at,
                    keyed=keyed,
                    unavailable=unavailable,
                )
            )

        if normalized_sort == "name":
            results.sort(key=lambda entry: entry.character.name.casefold())
        else:
            results.sort(key=lambda entry: (entry.claim_rank, entry.character.name.casefold()))
        return tuple(results[:limit] if limit is not None else results)
