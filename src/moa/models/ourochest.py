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


class OurochestVisualResolutionKind(str, Enum):
    """Classification state for one opaque `$oc` visual identity."""

    HIDDEN = "hidden"
    SPHERE = "sphere"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class OurochestVisualResolution:
    """Immutable semantic resolution of one `$oc` visual identity."""

    kind: OurochestVisualResolutionKind
    sphere: OurochestSphere | None = None

    def __post_init__(self) -> None:
        try:
            kind = OurochestVisualResolutionKind(self.kind)
        except (TypeError, ValueError) as error:
            raise ValueError("kind must be an OurochestVisualResolutionKind") from error
        object.__setattr__(self, "kind", kind)

        if kind is OurochestVisualResolutionKind.SPHERE:
            try:
                sphere = OurochestSphere(self.sphere)
            except (TypeError, ValueError) as error:
                raise ValueError("SPHERE resolution requires an OurochestSphere") from error
            if sphere is OurochestSphere.UNKNOWN:
                raise ValueError("SPHERE resolution cannot carry OurochestSphere.UNKNOWN")
            object.__setattr__(self, "sphere", sphere)
        elif self.sphere is not None:
            raise ValueError(f"{kind.value} resolution must not carry a sphere")


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
