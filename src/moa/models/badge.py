from moa.models.base import MOAModel


class BadgeLevel(MOAModel):
    """One purchasable level within a Kakera Badge definition."""

    level: int
    effects: tuple[str, ...]
    prerequisites: tuple[str, ...] = ()


class BadgeDefinition(MOAModel):
    """Immutable reference definition for one Kakera Badge."""

    id: str
    name: str
    default_base_value: int
    levels: tuple[BadgeLevel, ...]
