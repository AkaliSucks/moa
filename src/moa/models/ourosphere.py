"""Structural `$oh` board state models.

These models deliberately preserve only sanitized component evidence. They do
not assign meaning to a visual identity or infer claims from disabled state.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


Coordinate = tuple[int, int]
"""A zero-based ``(row, column)`` coordinate on the 5x5 board."""


_BOARD_COORDINATES = frozenset((row, column) for row in range(5) for column in range(5))
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def _require_sha256(value: str, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return value


def _require_coordinate(value: Coordinate, field_name: str = "coordinate") -> Coordinate:
    if (
        not isinstance(value, tuple)
        or len(value) != 2
        or any(isinstance(part, bool) or not isinstance(part, int) for part in value)
        or value not in _BOARD_COORDINATES
    ):
        raise ValueError(f"{field_name} must be a zero-based coordinate in the 5x5 board")
    return value


@dataclass(frozen=True, slots=True)
class OuroHuntVisualIdentity:
    """Opaque sanitized visual identity from one projected component."""

    kind: str
    id_sha256: str
    name_sha256: str
    name_length: int

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("visual identity kind must be a non-blank string")
        _require_sha256(self.id_sha256, "visual identity id_sha256")
        _require_sha256(self.name_sha256, "visual identity name_sha256")
        if isinstance(self.name_length, bool) or not isinstance(self.name_length, int):
            raise ValueError("visual identity name_length must be an integer")
        if self.name_length < 0:
            raise ValueError("visual identity name_length must not be negative")


@dataclass(frozen=True, slots=True)
class OuroHuntCell:
    """One structurally projected board cell."""

    coordinate: Coordinate
    component_identity: str
    visual_identity: OuroHuntVisualIdentity
    disabled: bool

    def __post_init__(self) -> None:
        _require_coordinate(self.coordinate)
        _require_sha256(self.component_identity, "component_identity")
        if not isinstance(self.visual_identity, OuroHuntVisualIdentity):
            raise TypeError("visual_identity must be an OuroHuntVisualIdentity")
        if not isinstance(self.disabled, bool):
            raise TypeError("disabled must be a boolean")


@dataclass(frozen=True, slots=True)
class OuroHuntBoard:
    """Immutable, complete 5x5 structural board snapshot."""

    cells: tuple[OuroHuntCell, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.cells, tuple):
            raise TypeError("cells must be a tuple")
        if len(self.cells) != 25:
            raise ValueError("board must contain exactly 25 cells")
        if any(not isinstance(cell, OuroHuntCell) for cell in self.cells):
            raise TypeError("board cells must be OuroHuntCell instances")
        coordinates = tuple(cell.coordinate for cell in self.cells)
        if len(set(coordinates)) != 25 or set(coordinates) != _BOARD_COORDINATES:
            raise ValueError("board must cover every unique zero-based coordinate in the 5x5 grid")
        if coordinates != tuple(sorted(coordinates)):
            raise ValueError("board cells must be in deterministic coordinate order")

    @property
    def coordinates(self) -> tuple[Coordinate, ...]:
        """All coordinates in deterministic zero-based row-major order."""

        return tuple(cell.coordinate for cell in self.cells)

    @property
    def is_terminal(self) -> bool:
        """Whether every observable component is disabled."""

        return all(cell.disabled for cell in self.cells)

    def cell_at(self, coordinate: Coordinate) -> OuroHuntCell:
        """Return the cell at a zero-based ``(row, column)`` coordinate."""

        _require_coordinate(coordinate)
        for cell in self.cells:
            if cell.coordinate == coordinate:
                return cell
        raise LookupError(f"board has no cell at coordinate {coordinate}")


@dataclass(frozen=True, slots=True)
class OuroHuntCellTransition:
    """Observable structural change for one coordinate between snapshots."""

    coordinate: Coordinate
    component_identity: str
    visual_identity_before: OuroHuntVisualIdentity
    visual_identity_after: OuroHuntVisualIdentity
    disabled_before: bool
    disabled_after: bool

    @property
    def visual_changed(self) -> bool:
        return self.visual_identity_before != self.visual_identity_after

    @property
    def disabled_changed(self) -> bool:
        return self.disabled_before != self.disabled_after

    @property
    def became_disabled(self) -> bool:
        return not self.disabled_before and self.disabled_after

    @property
    def became_enabled(self) -> bool:
        return self.disabled_before and not self.disabled_after


@dataclass(frozen=True, slots=True)
class OuroHuntBoardTransition:
    """Per-cell observable comparison of two complete board snapshots."""

    before: OuroHuntBoard
    after: OuroHuntBoard
    cells: tuple[OuroHuntCellTransition, ...]

    def cell_at(self, coordinate: Coordinate) -> OuroHuntCellTransition:
        """Return the transition at a zero-based ``(row, column)`` coordinate."""

        _require_coordinate(coordinate)
        for cell in self.cells:
            if cell.coordinate == coordinate:
                return cell
        raise LookupError(f"transition has no cell at coordinate {coordinate}")
