"""Pure semantic models for the `$oc` Ourochest board."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from moa.models.ourosphere import Coordinate, _require_coordinate


class OurochestSphere(str, Enum):
    """A resolved visible sphere semantic used by the `$oc` rule engine."""

    RED = "red"
    ORANGE = "orange"
    YELLOW = "yellow"
    GREEN = "green"
    TEAL = "teal"
    BLUE = "blue"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class OurochestObservation:
    """One coordinate and its already-resolved `$oc` sphere semantic."""

    coordinate: Coordinate
    sphere: OurochestSphere

    def __post_init__(self) -> None:
        _require_coordinate(self.coordinate)
        try:
            sphere = OurochestSphere(self.sphere)
        except (TypeError, ValueError) as error:
            raise ValueError("sphere must be an OurochestSphere semantic") from error
        object.__setattr__(self, "sphere", sphere)
