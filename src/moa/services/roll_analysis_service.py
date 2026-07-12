"""Explain a copied roll using only imported account state and direct roll facts."""

from moa.models.catalog import RollAnalysis
from moa.models.character import RollObservation
from moa.services.catalog_service import CatalogService


class RollAnalysisService:
    """Add factual account context to a Mudae roll without recommending a claim yet."""

    def __init__(self, catalog_service: CatalogService | None = None) -> None:
        self._catalog = catalog_service or CatalogService()

    def analyze(self, roll: RollObservation, server_name: str, account_name: str) -> RollAnalysis:
        """Match one roll against the latest imported wishlist and keyed-harem state."""
        normalized_name = roll.name.casefold().strip()
        wishlist = self._catalog.wishlist(server_name, account_name)
        wish_entry = (
            next(
                (entry for entry in wishlist.entries if entry.name.casefold().strip() == normalized_name),
                None,
            )
            if wishlist is not None
            else None
        )
        if wish_entry is None:
            wishlist_state = "Not wished"
        elif wish_entry.is_starwish:
            wishlist_state = "Starwish"
        else:
            wishlist_state = "Wish"

        keyed_entry = next(
            (
                entry
                for entry in self._catalog.harem_keys(server_name, account_name)
                if entry.character_name.casefold().strip() == normalized_name
            ),
            None,
        )
        keyed_harem_state = (
            f"{keyed_entry.key_type.title()} key ({keyed_entry.key_count})"
            if keyed_entry is not None
            else "No keyed-harem entry imported"
        )

        rollability_state = "Observed rolling now (available at import time)"

        timer_state = self._catalog.timer_state(server_name, account_name)
        if timer_state is None or timer_state.snapshot.can_claim_now is None:
            claim_window_state = "No imported claim-window state"
        elif timer_state.snapshot.can_claim_now:
            claim_window_state = "Claim was ready in the latest $tu snapshot"
        else:
            minutes = timer_state.snapshot.claim_reset_minutes
            claim_window_state = (
                "Claim window unavailable in latest $tu snapshot"
                if minutes is None
                else f"Claim window was {minutes} min away in the latest $tu snapshot"
            )

        return RollAnalysis(
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            character_name=roll.name,
            series=roll.series,
            claim_rank=roll.claim_rank,
            kakera_value=roll.kakera_value,
            wishlist_state=wishlist_state,
            keyed_harem_state=keyed_harem_state,
            rollability_state=rollability_state,
            claim_window_state=claim_window_state,
        )
