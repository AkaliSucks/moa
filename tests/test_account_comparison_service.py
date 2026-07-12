from moa.models.catalog import AccountOverview
from moa.services.account_comparison_service import AccountComparisonService


class InMemoryOverviewService:
    def __init__(self) -> None:
        self._overviews = {
            ("Lake", "main"): AccountOverview(
                server_name="Lake",
                account_name="main",
                kakera_balance=9283,
                kakera_balance_source="$k",
                personal_rare_multiplier=1,
                server_rare_multiplier=4,
                effective_rare_multiplier=1,
                rare_multiplier_source="$personalrare",
                badge_count=7,
                max_badge_count=7,
                tower_level=2,
                completed_towers=1,
                next_tower_cost=75000,
                tower_shortfall=65717,
                kakeraloots_unlocked=True,
                missing_kakeraloot_prerequisites=(),
                has_kakeraloots=True,
                kakeraloot_status_note=None,
                quantity_level=23,
                quality_level=6,
                loot_usage_count=256,
                wishlist_count=13,
                wishlist_capacity=13,
                starwish_count=2,
                starwish_capacity=2,
                disable_slots_used=13,
                disable_slots_capacity=16,
                keyed_harem_count=87,
            ),
            ("Fresh", "alt"): AccountOverview(
                server_name="Fresh",
                account_name="alt",
                kakera_balance=354,
                kakera_balance_source="$k",
                personal_rare_multiplier=None,
                server_rare_multiplier=None,
                effective_rare_multiplier=None,
                rare_multiplier_source=None,
                badge_count=7,
                max_badge_count=0,
                tower_level=None,
                completed_towers=None,
                next_tower_cost=None,
                tower_shortfall=None,
                kakeraloots_unlocked=False,
                missing_kakeraloot_prerequisites=("Sapphire I", "Ruby I", "Emerald I"),
                has_kakeraloots=None,
                kakeraloot_status_note=None,
                quantity_level=None,
                quality_level=None,
                loot_usage_count=None,
                wishlist_count=None,
                wishlist_capacity=None,
                starwish_count=None,
                starwish_capacity=None,
                disable_slots_used=None,
                disable_slots_capacity=None,
                keyed_harem_count=0,
            ),
        }

    def overview(self, server_name: str, account_name: str) -> AccountOverview:
        return self._overviews[(server_name, account_name)]


def test_account_comparison_keeps_missing_state_distinct_from_zero() -> None:
    comparison = AccountComparisonService(InMemoryOverviewService()).compare("Lake", "main", "Fresh", "alt")

    rows = {row.label: row for row in comparison.rows}
    assert rows["Kakera balance"].left_value == "9,283 ($k)"
    assert rows["Kakera balance"].right_value == "354 ($k)"
    assert rows["Claimed-roll rarity"].right_value == "Not imported"
    assert rows["Tower"].right_value == "Not imported"
    assert rows["Kakeraloots"].right_value == "Locked: Sapphire I, Ruby I, Emerald I"
