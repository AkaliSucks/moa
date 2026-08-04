"""Pure deterministic advancement of an already-bound ``$oc`` workflow."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from moa.models.ourochest import OurochestObservation, OurochestSphere
from moa.models.ourochest_workflow import (
    OurochestWorkflowState,
    OurochestWorkflowStatus,
)
from moa.models.ourosphere import Coordinate, OuroHuntBoard
from moa.services.ourochest_candidate_service import reduce_red_candidates
from moa.services.ourochest_transition_service import (
    OurochestTransitionKind,
    OurochestTransitionResult,
)


class OurochestWorkflowAdvanceReason(str, Enum):
    """Bounded reasons for coordinator outcomes that stop normal solving."""

    ORANGE_GEOMETRY_UNSUPPORTED = "orange_geometry_unsupported"
    UNKNOWN_VISUAL = "unknown_visual"
    MALFORMED_TRANSITION = "malformed_transition"


@dataclass(frozen=True, slots=True)
class OurochestWorkflowAdvanceResult:
    """Immutable result of one pure bound-workflow advancement."""

    workflow: OurochestWorkflowState
    reason: OurochestWorkflowAdvanceReason | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.workflow, OurochestWorkflowState):
            raise TypeError("workflow must be an OurochestWorkflowState")
        if self.workflow.status is OurochestWorkflowStatus.PENDING_BOARD:
            raise ValueError("advance result must contain a bound workflow")

        if self.workflow.status is OurochestWorkflowStatus.UNSUPPORTED:
            if self.reason not in {
                OurochestWorkflowAdvanceReason.ORANGE_GEOMETRY_UNSUPPORTED,
                OurochestWorkflowAdvanceReason.UNKNOWN_VISUAL,
            }:
                raise ValueError("unsupported result requires an unsupported reason")
        elif self.workflow.status is OurochestWorkflowStatus.MALFORMED:
            if self.reason is not OurochestWorkflowAdvanceReason.MALFORMED_TRANSITION:
                raise ValueError("malformed result requires a malformed reason")
        elif self.reason is not None:
            raise ValueError("normal workflow results must not carry a stop reason")

    @property
    def status(self) -> OurochestWorkflowStatus:
        """Lifecycle status after the transition is applied."""

        return self.workflow.status

    @property
    def red_candidates(self) -> tuple[Coordinate, ...]:
        """Candidates retained by the result in deterministic tuple order."""

        assert self.workflow.red_candidates is not None
        return self.workflow.red_candidates

    @property
    def candidate_count(self) -> int:
        """Number of retained Red candidates."""

        return len(self.red_candidates)

    @property
    def unique_red(self) -> Coordinate | None:
        """The sole Red candidate, when exactly one remains."""

        return self.red_candidates[0] if self.candidate_count == 1 else None

    @property
    def last_board(self) -> OuroHuntBoard:
        """Board snapshot retained as the workflow's relevant baseline."""

        assert self.workflow.last_board is not None
        return self.workflow.last_board


def advance_ourochest_workflow(
    workflow: OurochestWorkflowState,
    current_board: OuroHuntBoard,
    transition: OurochestTransitionResult,
) -> OurochestWorkflowAdvanceResult:
    """Advance one already-bound workflow without index or runtime side effects."""

    if not isinstance(workflow, OurochestWorkflowState):
        raise TypeError("workflow must be an OurochestWorkflowState")
    if workflow.status is not OurochestWorkflowStatus.ACTIVE:
        raise ValueError("workflow must be ACTIVE")
    if not isinstance(current_board, OuroHuntBoard):
        raise TypeError("current_board must be an OuroHuntBoard")
    if not isinstance(transition, OurochestTransitionResult):
        raise TypeError("transition must be an OurochestTransitionResult")

    if transition.kind is OurochestTransitionKind.NO_CHANGE:
        return OurochestWorkflowAdvanceResult(workflow)

    if transition.kind is OurochestTransitionKind.TERMINAL:
        return OurochestWorkflowAdvanceResult(
            _replace_workflow(
                workflow,
                status=OurochestWorkflowStatus.TERMINAL,
                last_board=current_board,
            )
        )

    if transition.kind is OurochestTransitionKind.UNKNOWN_VISUAL:
        return OurochestWorkflowAdvanceResult(
            _replace_workflow(
                workflow,
                status=OurochestWorkflowStatus.UNSUPPORTED,
                last_board=current_board,
            ),
            OurochestWorkflowAdvanceReason.UNKNOWN_VISUAL,
        )

    if transition.kind is OurochestTransitionKind.MALFORMED_TRANSITION:
        return OurochestWorkflowAdvanceResult(
            _replace_workflow(
                workflow,
                status=OurochestWorkflowStatus.MALFORMED,
            ),
            OurochestWorkflowAdvanceReason.MALFORMED_TRANSITION,
        )

    observation = transition.observation
    assert transition.kind is OurochestTransitionKind.OBSERVATION
    assert isinstance(observation, OurochestObservation)
    if observation.sphere is OurochestSphere.ORANGE:
        return OurochestWorkflowAdvanceResult(
            _replace_workflow(
                workflow,
                status=OurochestWorkflowStatus.UNSUPPORTED,
                last_board=current_board,
            ),
            OurochestWorkflowAdvanceReason.ORANGE_GEOMETRY_UNSUPPORTED,
        )
    if observation.sphere is OurochestSphere.UNKNOWN:
        raise ValueError("UNKNOWN sphere observations are invalid coordinator input")

    assert workflow.red_candidates is not None
    candidates = reduce_red_candidates(workflow.red_candidates, observation)
    if not candidates:
        status = OurochestWorkflowStatus.CONTRADICTION
    else:
        status = OurochestWorkflowStatus.ACTIVE
    return OurochestWorkflowAdvanceResult(
        _replace_workflow(
            workflow,
            status=status,
            last_board=current_board,
            red_candidates=candidates,
        )
    )


class OurochestWorkflowCoordinator:
    """Stateless facade for pure bound-workflow advancement."""

    @staticmethod
    def advance(
        workflow: OurochestWorkflowState,
        current_board: OuroHuntBoard,
        transition: OurochestTransitionResult,
    ) -> OurochestWorkflowAdvanceResult:
        return advance_ourochest_workflow(workflow, current_board, transition)


def _replace_workflow(
    workflow: OurochestWorkflowState,
    *,
    status: OurochestWorkflowStatus,
    last_board: OuroHuntBoard | None = None,
    red_candidates: tuple[Coordinate, ...] | None = None,
) -> OurochestWorkflowState:
    """Copy bound state while preserving all ownership and clock metadata."""

    return replace(
        workflow,
        status=status,
        last_board=workflow.last_board if last_board is None else last_board,
        red_candidates=(
            workflow.red_candidates if red_candidates is None else red_candidates
        ),
    )
