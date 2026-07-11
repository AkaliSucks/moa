from typing import Literal

from moa.models.base import MOAModel


class ReactionDefinition(MOAModel):
    """Immutable reference definition for one Kakera reaction type."""

    id: str
    name: str
    reaction_type: Literal["fixed", "range", "multi", "random"]
    minimum_value: int | None
    maximum_value: int | None
    average_value: float | None
    power_cost_policy: Literal["free", "standard", "variable"]
    description: str
