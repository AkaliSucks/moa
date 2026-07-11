from dataclasses import dataclass


@dataclass(frozen=True)
class TowerPerk:
    id: int
    name: str
    description: str
    max_level: int | None