from moa.models.base import MOAModel


class TowerPerk(MOAModel):
    """Immutable reference definition for one Kakera Tower floor type."""

    id: int
    name: str
    category: str
    description: str
    first_tower_effect: str
    progression_note: str
    initial_cap_level: int | None = None
