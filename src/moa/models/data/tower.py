from dataclasses import dataclass

from moa.models.base.common import GameObject


@dataclass(frozen=True, slots=True)
class TowerPerk(GameObject):
    description: str
    max_level: int | None