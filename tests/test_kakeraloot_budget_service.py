from datetime import datetime, timezone

import pytest

from moa.models.catalog import (
    KakeraStateObservation,
    KakeralootSettingsObservation,
    KakeralootStateObservation,
)
from moa.models.character import BadgeLevel
from moa.services.kakeraloot_budget_service import KakeralootBudgetService


class InMemoryKakeralootPlanningCatalog:
    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self._kakera = KakeraStateObservation(
            server_name="Lake",
            account_name="ernieuuu",
            kakera_balance=9283,
            badges=(
                BadgeLevel(badge_name="sapphire", level=4, max_reached=True),
                BadgeLevel(badge_name="ruby", level=4, max_reached=True),
                BadgeLevel(badge_name="emerald", level=4, max_reached=True),
            ),
            observed_at=now,
        )
        self._settings = KakeralootSettingsObservation(
            server_name="Lake",
            loot_cost=500,
            quantity_quality_base_cost=2000,
            quantity_quality_level_increment=200,
            observed_at=now,
        )
        self._state = KakeralootStateObservation(
            server_name="Lake",
            account_name="ernieuuu",
            quantity_level=23,
            quality_level=6,
            usage_count=256,
            observed_at=now,
        )

    def kakera_state(self, server_name: str, account_name: str) -> KakeraStateObservation:
        return self._kakera

    def kakeraloot_settings(self, server_name: str) -> KakeralootSettingsObservation:
        return self._settings

    def kakeraloot_state(self, server_name: str, account_name: str) -> KakeralootStateObservation:
        return self._state


def test_budget_plan_uses_imported_server_price_and_current_levels() -> None:
    plan = KakeralootBudgetService(InMemoryKakeralootPlanningCatalog()).plan("Lake", "ernieuuu")

    quantity, quality = plan.upgrades
    assert plan.loot_cost == 500
    assert plan.affordable_loot_count == 18
    assert quantity.cost == 6600
    assert quantity.affordable
    assert quantity.remaining_kakera == 2683
    assert quality.cost == 3200
    assert quality.affordable


def test_budget_plan_reports_missing_unlock_prerequisites_without_guessing_costs() -> None:
    catalog = InMemoryKakeralootPlanningCatalog()
    catalog._kakera = catalog._kakera.model_copy(
        update={"badges": (BadgeLevel(badge_name="sapphire", level=1, max_reached=False),)}
    )

    plan = KakeralootBudgetService(catalog).plan("Lake", "ernieuuu")

    assert plan.status == "Kakeraloots are locked."
    assert plan.missing_prerequisites == ("Ruby I", "Emerald I")
    assert not plan.upgrades


@pytest.mark.parametrize("unknown_field", ("quantity_level", "quality_level"))
def test_budget_plan_does_not_treat_unknown_level_as_zero(unknown_field) -> None:
    catalog = InMemoryKakeralootPlanningCatalog()
    catalog._state = catalog._state.model_copy(update={unknown_field: None})

    plan = KakeralootBudgetService(catalog).plan("Lake", "ernieuuu")

    assert plan.affordable_loot_count == 18
    assert plan.status == "Import $lk to determine the current Quantity and Quality levels."
    assert not plan.upgrades


def test_budget_plan_keeps_observed_zero_levels_factual() -> None:
    catalog = InMemoryKakeralootPlanningCatalog()
    catalog._state = catalog._state.model_copy(update={"quantity_level": 0, "quality_level": 0})

    plan = KakeralootBudgetService(catalog).plan("Lake", "ernieuuu")

    quantity, quality = plan.upgrades
    assert quantity.current_level == quality.current_level == 0
    assert quantity.cost == quality.cost == 2_000
