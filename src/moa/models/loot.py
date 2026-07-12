"""Immutable reference definitions for Kakeraloot rewards."""

from typing import Literal

from moa.models.base import MOAModel


class KakeralootDefinition(MOAModel):
    """One possible Kakeraloot reward, independent of any player account."""

    id: str
    name: str
    category: Literal["rolls", "kakera", "rolling", "wishes", "utility", "collection"]
    guaranteed: bool
    unlock_prerequisites: tuple[str, ...]
    progression_note: str
    description: str


class KakeralootUpgradeOption(MOAModel):
    """The next affordable-state calculation for Quantity or Quality."""

    name: str
    current_level: int
    next_level: int
    cost: int
    affordable: bool
    remaining_kakera: int | None


class KakeralootBudgetPlan(MOAModel):
    """A transparent affordability plan, not an expected-value recommendation."""

    server_name: str
    account_name: str
    kakera_balance: int | None
    loot_cost: int | None
    affordable_loot_count: int | None
    status: str
    missing_prerequisites: tuple[str, ...]
    upgrades: tuple[KakeralootUpgradeOption, ...]
