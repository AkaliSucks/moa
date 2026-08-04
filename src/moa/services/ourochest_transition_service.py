"""Pure `$oc` interpretation between projected Ourochest boards."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from moa.models.ourochest import OurochestObservation, OurochestVisualResolutionKind
from moa.models.ourosphere import OuroHuntBoard
from moa.services.ourochest_visual_alias_service import resolve_ourochest_visual
from moa.services.ourosphere_board_service import (
    OuroHuntBoardProjectionError,
    OuroHuntBoardService,
)


class OurochestTransitionKind(str, Enum):
    """Explicit outcomes of interpreting one projected `$oc` transition."""

    NO_CHANGE = "no_change"
    TERMINAL = "terminal"
    OBSERVATION = "observation"
    UNKNOWN_VISUAL = "unknown_visual"
    MALFORMED_TRANSITION = "malformed_transition"


@dataclass(frozen=True, slots=True)
class OurochestTransitionResult:
    """Immutable `$oc` transition outcome with an optional observation."""

    kind: OurochestTransitionKind
    observation: OurochestObservation | None = None

    def __post_init__(self) -> None:
        try:
            kind = OurochestTransitionKind(self.kind)
        except (TypeError, ValueError) as error:
            raise ValueError("kind must be an OurochestTransitionKind") from error
        object.__setattr__(self, "kind", kind)

        if kind is OurochestTransitionKind.OBSERVATION:
            if not isinstance(self.observation, OurochestObservation):
                raise TypeError("OBSERVATION requires an OurochestObservation")
        elif self.observation is not None:
            raise ValueError(f"{kind.value} must not carry an observation")


class OurochestTransitionService:
    """Interpret only the grounded one-cell `$oc` board transition contract."""

    def __init__(self) -> None:
        self._board_service = OuroHuntBoardService()

    def interpret(
        self, previous: OuroHuntBoard, current: OuroHuntBoard
    ) -> OurochestTransitionResult:
        """Interpret two immutable projected boards without mutating either one."""

        if current.is_terminal:
            return OurochestTransitionResult(OurochestTransitionKind.TERMINAL)

        try:
            transition = self._board_service.compare(previous, current)
        except OuroHuntBoardProjectionError:
            return OurochestTransitionResult(OurochestTransitionKind.MALFORMED_TRANSITION)

        changed = [
            cell
            for cell in transition.cells
            if cell.visual_changed or cell.disabled_changed
        ]
        if not changed:
            return OurochestTransitionResult(OurochestTransitionKind.NO_CHANGE)
        if len(changed) != 1:
            return OurochestTransitionResult(OurochestTransitionKind.MALFORMED_TRANSITION)

        action = changed[0]
        if not action.visual_changed or not action.became_disabled:
            return OurochestTransitionResult(OurochestTransitionKind.MALFORMED_TRANSITION)

        previous_resolution = resolve_ourochest_visual(action.visual_identity_before)
        if previous_resolution.kind is not OurochestVisualResolutionKind.HIDDEN:
            return OurochestTransitionResult(OurochestTransitionKind.MALFORMED_TRANSITION)

        current_resolution = resolve_ourochest_visual(action.visual_identity_after)
        if current_resolution.kind is OurochestVisualResolutionKind.HIDDEN:
            return OurochestTransitionResult(OurochestTransitionKind.MALFORMED_TRANSITION)
        if current_resolution.kind is OurochestVisualResolutionKind.UNKNOWN:
            return OurochestTransitionResult(OurochestTransitionKind.UNKNOWN_VISUAL)

        return OurochestTransitionResult(
            OurochestTransitionKind.OBSERVATION,
            OurochestObservation(
                coordinate=action.coordinate,
                sphere=current_resolution.sphere,
            ),
        )


def interpret_ourochest_transition(
    previous: OuroHuntBoard, current: OuroHuntBoard
) -> OurochestTransitionResult:
    """Interpret one projected board transition using the stateless service."""

    return OurochestTransitionService().interpret(previous, current)
