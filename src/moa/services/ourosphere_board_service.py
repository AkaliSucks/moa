"""Pure structural projection and comparison for sanitized `$oh` boards."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from moa.models.ourosphere import (
    Coordinate,
    OuroHuntBoard,
    OuroHuntBoardTransition,
    OuroHuntCell,
    OuroHuntCellTransition,
    OuroHuntVisualIdentity,
    _require_sha256,
)


class OuroHuntBoardProjectionError(ValueError):
    """Raised when a sanitized board snapshot is structurally invalid."""


class OuroHuntBoardService:
    """Project and compare already-sanitized `$oh` component snapshots."""

    def project(self, snapshot: Mapping[str, Any]) -> OuroHuntBoard:
        """Project one sanitized message or capture record into an immutable board."""

        message = self._message_snapshot(snapshot)
        components = message.get("components")
        if not self._is_sequence(components) or len(components) != 5:
            raise OuroHuntBoardProjectionError("board snapshot must contain five component rows")

        cells: list[OuroHuntCell] = []
        for row in components:
            if not isinstance(row, Mapping):
                raise OuroHuntBoardProjectionError("board component row must be an object")
            leaves = row.get("components")
            if not self._is_sequence(leaves):
                raise OuroHuntBoardProjectionError("board component row must contain components")
            for leaf in leaves:
                cells.append(self._project_cell(leaf))

        try:
            return OuroHuntBoard(tuple(sorted(cells, key=lambda cell: cell.coordinate)))
        except (TypeError, ValueError) as error:
            raise OuroHuntBoardProjectionError(str(error)) from error

    def compare(
        self, before: OuroHuntBoard, after: OuroHuntBoard
    ) -> OuroHuntBoardTransition:
        """Compare two complete boards using only observable per-cell state."""

        if not isinstance(before, OuroHuntBoard) or not isinstance(after, OuroHuntBoard):
            raise TypeError("before and after must be OuroHuntBoard instances")

        transitions: list[OuroHuntCellTransition] = []
        for before_cell, after_cell in zip(before.cells, after.cells, strict=True):
            if before_cell.component_identity != after_cell.component_identity:
                raise OuroHuntBoardProjectionError(
                    f"component identity changed at coordinate {before_cell.coordinate}"
                )
            transitions.append(
                OuroHuntCellTransition(
                    coordinate=before_cell.coordinate,
                    component_identity=before_cell.component_identity,
                    visual_identity_before=before_cell.visual_identity,
                    visual_identity_after=after_cell.visual_identity,
                    disabled_before=before_cell.disabled,
                    disabled_after=after_cell.disabled,
                )
            )
        return OuroHuntBoardTransition(before=before, after=after, cells=tuple(transitions))

    @staticmethod
    def _message_snapshot(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(snapshot, Mapping):
            raise OuroHuntBoardProjectionError("board snapshot must be an object")
        if "components" in snapshot:
            return snapshot
        message = snapshot.get("message")
        if isinstance(message, Mapping):
            return message
        raise OuroHuntBoardProjectionError("board snapshot must contain a message with components")

    @classmethod
    def _project_cell(cls, leaf: Any) -> OuroHuntCell:
        if not isinstance(leaf, Mapping):
            raise OuroHuntBoardProjectionError("board cell must be an object")

        coordinate_value = leaf.get("path")
        if (
            not cls._is_sequence(coordinate_value)
            or len(coordinate_value) != 2
            or any(
                isinstance(part, bool) or not isinstance(part, int)
                for part in coordinate_value
            )
        ):
            raise OuroHuntBoardProjectionError("cell path must be a two-part coordinate")
        coordinate: Coordinate = (coordinate_value[0], coordinate_value[1])
        if coordinate not in {(row, column) for row in range(5) for column in range(5)}:
            raise OuroHuntBoardProjectionError("cell coordinate is outside the 5x5 board")

        component_identity = leaf.get("custom_id_sha256")
        try:
            _require_sha256(component_identity, "component_identity")
        except (TypeError, ValueError) as error:
            raise OuroHuntBoardProjectionError(str(error)) from error

        visual_value = leaf.get("emoji")
        if not isinstance(visual_value, Mapping):
            raise OuroHuntBoardProjectionError("cell visual identity must be an object")
        visual_identity = cls._project_visual_identity(visual_value)

        disabled = leaf.get("disabled")
        if not isinstance(disabled, bool):
            raise OuroHuntBoardProjectionError("cell disabled state must be a boolean")

        try:
            return OuroHuntCell(
                coordinate=coordinate,
                component_identity=component_identity,
                visual_identity=visual_identity,
                disabled=disabled,
            )
        except (TypeError, ValueError) as error:
            raise OuroHuntBoardProjectionError(str(error)) from error

    @staticmethod
    def _project_visual_identity(value: Mapping[str, Any]) -> OuroHuntVisualIdentity:
        required = ("kind", "id_sha256", "name_sha256", "name_length")
        missing = [field for field in required if field not in value]
        if missing:
            raise OuroHuntBoardProjectionError(
                f"cell visual identity is missing {', '.join(missing)}"
            )
        try:
            return OuroHuntVisualIdentity(
                kind=value["kind"],
                id_sha256=value["id_sha256"],
                name_sha256=value["name_sha256"],
                name_length=value["name_length"],
            )
        except (TypeError, ValueError) as error:
            raise OuroHuntBoardProjectionError(str(error)) from error

    @staticmethod
    def _is_sequence(value: Any) -> bool:
        return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
