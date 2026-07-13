from datetime import datetime, timezone

from moa.models.catalog import CatalogCharacter, HaremKeyObservation, WishlistObservation
from moa.models.character import RollObservation, WishlistEntry
from moa.services.roll_analysis_service import RollAnalysisService


class InMemoryRollAnalysisCatalog:
    def wishlist(self, server_name: str, account_name: str) -> WishlistObservation:
        return WishlistObservation(
            server_name="Lake",
            account_name="ernieuuu",
            wishlist_count=13,
            wishlist_capacity=13,
            starwish_count=2,
            starwish_capacity=2,
            entries=(
                WishlistEntry(
                    name="Power",
                    is_starwish=True,
                    is_owned_marker_present=True,
                    kakera_marker_present=False,
                ),
            ),
            observed_at=datetime.now(timezone.utc),
        )

    def harem_keys(self, server_name: str, account_name: str) -> tuple[HaremKeyObservation, ...]:
        return (
            HaremKeyObservation(
                character_name="Power",
                character=CatalogCharacter(
                    id=1,
                    name="Power",
                    series="Chainsaw Man",
                    gender=None,
                    roulette=None,
                ),
                key_type="silver",
                key_count=5,
                kakera_value=1448,
                observed_at=datetime.now(timezone.utc),
            ),
        )

    def timer_state(self, server_name: str, account_name: str) -> None:
        return None


def test_roll_analysis_combines_direct_roll_with_imported_wishlist_and_key_context() -> None:
    analysis = RollAnalysisService(InMemoryRollAnalysisCatalog()).analyze(
        RollObservation(name="Power", series="Chainsaw Man", claim_rank=7, kakera_value=1448),
        "Lake",
        "ernieuuu",
    )

    assert analysis.wishlist_state == "Starwish"
    assert analysis.keyed_harem_state == ":silverkey: (5)"
    assert analysis.rollability_state == "Observed rolling now (available at import time)"
    assert analysis.claim_window_state == "No imported claim-window state"
    assert analysis.kakera_value == 1448
