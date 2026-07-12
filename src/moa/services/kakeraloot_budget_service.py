"""Calculate Kakeraloot spending affordability from imported Mudae state."""

from moa.models.loot import KakeralootBudgetPlan, KakeralootUpgradeOption
from moa.services.catalog_service import CatalogService


class KakeralootBudgetService:
    """Plan the next Quantity and Quality costs without guessing reward EV."""

    _REQUIRED_BADGES = (
        ("sapphire", "Sapphire I"),
        ("ruby", "Ruby I"),
        ("emerald", "Emerald I"),
    )

    def __init__(self, catalog_service: CatalogService | None = None) -> None:
        self._catalog = catalog_service or CatalogService()

    def plan(self, server_name: str, account_name: str) -> KakeralootBudgetPlan:
        """Return only costs and affordability supported by imported evidence."""
        kakera = self._catalog.kakera_state(server_name, account_name)
        settings = self._catalog.kakeraloot_settings(server_name)
        if kakera is None:
            return KakeralootBudgetPlan(
                server_name=server_name.strip(),
                account_name=account_name.strip(),
                kakera_balance=None,
                loot_cost=None,
                affordable_loot_count=None,
                status="Import $k before planning Kakeraloot spending.",
                missing_prerequisites=(),
                upgrades=(),
            )
        badge_levels = {badge.badge_name.casefold(): badge.level for badge in kakera.badges}
        missing = tuple(label for name, label in self._REQUIRED_BADGES if badge_levels.get(name, 0) < 1)
        if missing:
            return KakeralootBudgetPlan(
                server_name=server_name.strip(),
                account_name=account_name.strip(),
                kakera_balance=kakera.kakera_balance,
                loot_cost=None,
                affordable_loot_count=None,
                status="Kakeraloots are locked.",
                missing_prerequisites=missing,
                upgrades=(),
            )
        if settings is None:
            return KakeralootBudgetPlan(
                server_name=server_name.strip(),
                account_name=account_name.strip(),
                kakera_balance=kakera.kakera_balance,
                loot_cost=None,
                affordable_loot_count=None,
                status="Import $infokl to use this server's Kakeraloot prices.",
                missing_prerequisites=(),
                upgrades=(),
            )
        state = self._catalog.kakeraloot_state(server_name, account_name)
        if state is None:
            return KakeralootBudgetPlan(
                server_name=server_name.strip(),
                account_name=account_name.strip(),
                kakera_balance=kakera.kakera_balance,
                loot_cost=settings.loot_cost,
                affordable_loot_count=kakera.kakera_balance // settings.loot_cost,
                status="Import $lk to determine the current Quantity and Quality levels.",
                missing_prerequisites=(),
                upgrades=(),
            )

        quantity_level = state.quantity_level or 0
        quality_level = state.quality_level or 0
        return KakeralootBudgetPlan(
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            kakera_balance=kakera.kakera_balance,
            loot_cost=settings.loot_cost,
            affordable_loot_count=kakera.kakera_balance // settings.loot_cost,
            status="Costs and affordability only; reward odds are not modeled yet.",
            missing_prerequisites=(),
            upgrades=(
                self._next_upgrade("Quantity", quantity_level, kakera.kakera_balance, settings.quantity_quality_base_cost, settings.quantity_quality_level_increment),
                self._next_upgrade("Quality", quality_level, kakera.kakera_balance, settings.quantity_quality_base_cost, settings.quantity_quality_level_increment),
            ),
        )

    @staticmethod
    def _next_upgrade(
        name: str,
        current_level: int,
        balance: int,
        base_cost: int,
        level_increment: int,
    ) -> KakeralootUpgradeOption:
        cost = base_cost + current_level * level_increment
        affordable = balance >= cost
        return KakeralootUpgradeOption(
            name=name,
            current_level=current_level,
            next_level=current_level + 1,
            cost=cost,
            affordable=affordable,
            remaining_kakera=balance - cost if affordable else None,
        )
