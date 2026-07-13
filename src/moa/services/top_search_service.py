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
        owned_only: bool = False,
        unowned_only: bool = False,
        owned_account_names: tuple[str, ...] | None = None,
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
        if owned_only and unowned_only:
            raise ValueError("--owned-only and --unowned-only cannot be combined.")

        scoped = bool(server_name and account_name)
        if bool(server_name) != bool(account_name):
            raise ValueError("--server and --account must be supplied together.")
        if (owned_only or unowned_only or keyed_only or unavailable_only) and not scoped:
            raise ValueError("--server and --account are required for account evidence filters.")
        if unowned_only and not self._catalog.has_complete_harem_scan(
            server_name, account_name, "owned"
        ):
            raise ValueError(
                "--unowned-only requires a complete owned harem scan; "
                "run `moa harem begin --kind owned` and import every `$mmr`/`$mmrk` page."
            )

        owned_names: set[str] | None = None
        keyed_names: set[str] | None = None
        unavailable_reasons: dict[str, str | None] | None = None
        self_account_names: set[str] | None = None
        owner_names: dict[str, str] | None = None
        if scoped:
            self_account_names = {
                account.casefold()
                for account in (owned_account_names or (account_name,))
            }
            owned_names = {
                entry.character_name.casefold()
                for entry in self._catalog.owned_characters(server_name, account_name)
            }
            keyed_names = {
                entry.character_name.casefold()
                for entry in self._catalog.harem_keys(server_name, account_name)
            }
            unavailable_reasons = {
                entry.character.name.casefold(): entry.reason
                for entry in self._catalog.unavailable_characters(server_name, account_name)
            }
            owner_names = {
                entry.character.name.casefold(): entry.owner_name
                for entry in self._catalog.top_owner_observations(server_name)
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
            owned = name in owned_names if owned_names is not None else None
            keyed = name in keyed_names if keyed_names is not None else None
            owner_name = owner_names.get(name) if owner_names is not None else None
            topx_unavailable = (
                name in unavailable_reasons if unavailable_reasons is not None else None
            )
            owner_is_self = (
                owner_name.casefold() in self_account_names
                if owner_name is not None and self_account_names is not None
                else None
            )
            unavailable = (
                False
                if owner_is_self is True
                else True
                if owner_name
                else topx_unavailable
            )
            unavailable_reason = (
                None
                if owner_is_self is True
                else f"claimed by {owner_name}"
                if owner_name
                else unavailable_reasons[name]
                if topx_unavailable and unavailable_reasons is not None
                else None
            )
            if owned_only and not owned:
                continue
            if unowned_only and owned:
                continue
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
                    owned=owned,
                    keyed=keyed,
                    unavailable=unavailable,
                    unavailable_reason=unavailable_reason,
                    owner_name=owner_name,
                    owner_is_self=owner_is_self,
                )
            )

        if normalized_sort == "name":
            results.sort(key=lambda entry: entry.character.name.casefold())
        else:
            results.sort(key=lambda entry: (entry.claim_rank, entry.character.name.casefold()))
        return tuple(results[:limit] if limit is not None else results)
