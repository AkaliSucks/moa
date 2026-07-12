"""Compare two imported Mudae account contexts without estimating missing state."""

from moa.models.catalog import AccountComparison, AccountComparisonRow, AccountOverview
from moa.services.account_overview_service import AccountOverviewService


class AccountComparisonService:
    """Create a factual progress comparison for two server/account pairs."""

    def __init__(self, overview_service: AccountOverviewService | None = None) -> None:
        self._overview = overview_service or AccountOverviewService()

    def compare(
        self,
        left_server_name: str,
        left_account_name: str,
        right_server_name: str,
        right_account_name: str,
    ) -> AccountComparison:
        """Compare the newest imported state for two account contexts."""
        left = self._overview.overview(left_server_name, left_account_name)
        right = self._overview.overview(right_server_name, right_account_name)
        return AccountComparison(
            left_server_name=left.server_name,
            left_account_name=left.account_name,
            right_server_name=right.server_name,
            right_account_name=right.account_name,
            rows=tuple(
                AccountComparisonRow(label=label, left_value=self._value(left, field), right_value=self._value(right, field))
                for label, field in (
                    ("Kakera balance", "kakera"),
                    ("Claimed-roll rarity", "rare"),
                    ("Badges", "badges"),
                    ("Tower", "tower"),
                    ("Kakeraloots", "loots"),
                    ("Wishlist", "wishlist"),
                    ("Disablelist", "disablelist"),
                    ("Keyed harem", "harem"),
                )
            ),
        )

    @staticmethod
    def _value(overview: AccountOverview, field: str) -> str:
        if field == "kakera":
            return f"{overview.kakera_balance:,} ($k)" if overview.kakera_balance is not None else "Not imported"
        if field == "rare":
            if overview.personal_rare_multiplier is None:
                return "Not imported"
            if overview.personal_rare_multiplier == 0:
                return (
                    f"0 (server {overview.server_rare_multiplier})"
                    if overview.server_rare_multiplier is not None
                    else "0 (server setting not imported)"
                )
            return f"{overview.personal_rare_multiplier} (personal override)"
        if field == "badges":
            return f"{overview.max_badge_count}/{overview.badge_count} maxed" if overview.badge_count else "Not imported"
        if field == "tower":
            return (
                f"Level {overview.tower_level}; {overview.completed_towers} completed"
                if overview.tower_level is not None
                else "Not imported"
            )
        if field == "loots":
            if overview.kakeraloots_unlocked is False:
                return "Locked: " + ", ".join(overview.missing_kakeraloot_prerequisites)
            if overview.has_kakeraloots is False:
                return overview.kakeraloot_status_note or "No Kakeraloots bought"
            return (
                f"Quantity {overview.quantity_level}; Quality {overview.quality_level}; "
                f"{overview.loot_usage_count:,} uses"
                if overview.quantity_level is not None and overview.loot_usage_count is not None
                else "Not imported"
            )
        if field == "wishlist":
            return (
                f"{overview.wishlist_count}/{overview.wishlist_capacity} wishes; "
                f"{overview.starwish_count}/{overview.starwish_capacity} Starwishes"
                if overview.wishlist_count is not None
                else "Not imported"
            )
        if field == "disablelist":
            return (
                f"{overview.disable_slots_used}/{overview.disable_slots_capacity} slots used"
                if overview.disable_slots_used is not None
                else "Not imported"
            )
        if field == "harem":
            return f"{overview.keyed_harem_count} imported characters"
        raise ValueError(f"Unknown comparison field: {field}")
