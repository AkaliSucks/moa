from moa.models.base import MOAModel


class TowerPerk(MOAModel):
    id: int
    name: str
    max_level: int
    description: str