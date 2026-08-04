"""Immutable transient workflow state for the pure ``$oc`` boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math

from moa.models.ourosphere import Coordinate, OuroHuntBoard, _require_coordinate


WorkflowIdentifier = int | str
OUROCHEST_WORKFLOW_KIND = "ourochest"


class OurochestWorkflowStatus(str, Enum):
    """Supported lifecycle states for an in-memory Ourochest workflow."""

    PENDING_BOARD = "pending_board"
    ACTIVE = "active"
    UNSUPPORTED = "unsupported"
    MALFORMED = "malformed"
    CONTRADICTION = "contradiction"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class OurochestWorkflowState:
    """Immutable pending or board-bound Ourochest workflow state."""

    guild_id: WorkflowIdentifier
    channel_id: WorkflowIdentifier
    initiating_user_id: WorkflowIdentifier
    workflow_kind: str
    status: OurochestWorkflowStatus
    created_at: float
    expires_at: float
    board_message_id: WorkflowIdentifier | None = None
    bound_at: float | None = None
    last_board: OuroHuntBoard | None = None
    red_candidates: tuple[Coordinate, ...] | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.guild_id, "guild_id")
        _require_identifier(self.channel_id, "channel_id")
        _require_identifier(self.initiating_user_id, "initiating_user_id")
        if self.workflow_kind != OUROCHEST_WORKFLOW_KIND:
            raise ValueError(f"workflow_kind must be {OUROCHEST_WORKFLOW_KIND!r}")
        try:
            status = OurochestWorkflowStatus(self.status)
        except (TypeError, ValueError) as error:
            raise ValueError("status must be an OurochestWorkflowStatus") from error
        object.__setattr__(self, "status", status)
        _require_time(self.created_at, "created_at")
        _require_time(self.expires_at, "expires_at")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")

        if status is OurochestWorkflowStatus.PENDING_BOARD:
            if self.board_message_id is not None:
                raise ValueError("pending workflow must not have a board_message_id")
            if self.bound_at is not None:
                raise ValueError("pending workflow must not have bound_at")
            if self.last_board is not None:
                raise ValueError("pending workflow must not have last_board")
            if self.red_candidates is not None:
                raise ValueError("pending workflow must not have red_candidates")
            return

        if self.board_message_id is None:
            raise ValueError("bound workflow requires a board_message_id")
        _require_identifier(self.board_message_id, "board_message_id")
        if self.bound_at is None:
            raise ValueError("bound workflow requires bound_at")
        _require_time(self.bound_at, "bound_at")
        if self.bound_at < self.created_at:
            raise ValueError("bound_at must not be before created_at")
        if self.last_board is None:
            raise ValueError("bound workflow requires last_board")
        if not isinstance(self.last_board, OuroHuntBoard):
            raise TypeError("last_board must be an OuroHuntBoard")
        if self.red_candidates is None:
            raise ValueError("bound workflow requires red_candidates")
        if not isinstance(self.red_candidates, tuple):
            raise TypeError("red_candidates must be a tuple")
        if len(set(self.red_candidates)) != len(self.red_candidates):
            raise ValueError("red_candidates must contain unique coordinates")
        for candidate in self.red_candidates:
            _require_coordinate(candidate, "red candidate")
        if self.expires_at <= self.bound_at:
            raise ValueError("active workflow expires_at must be after bound_at")
        if status is OurochestWorkflowStatus.CONTRADICTION:
            if self.red_candidates:
                raise ValueError("contradiction workflow must have no red candidates")
        elif not self.red_candidates:
            raise ValueError(f"{status.value} workflow must have red candidates")


def _require_identifier(value: WorkflowIdentifier, field_name: str) -> WorkflowIdentifier:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TypeError(f"{field_name} must be an integer or string identifier")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


def _require_time(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a finite number")
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number")
    return float(value)
