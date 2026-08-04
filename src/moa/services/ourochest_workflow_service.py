"""In-memory pending/bound workflow state for the pure ``$oc`` seam."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import time

from moa.models.ourochest import OurochestVisualResolutionKind
from moa.models.ourochest_workflow import (
    OUROCHEST_WORKFLOW_KIND,
    OurochestWorkflowState,
    OurochestWorkflowStatus,
    WorkflowIdentifier,
    _require_identifier,
)
from moa.models.ourosphere import OuroHuntBoard
from moa.services.ourochest_candidate_service import initial_red_candidates
from moa.services.ourochest_visual_alias_service import resolve_ourochest_visual


WORKFLOW_TTL_SECONDS = 120.0
PendingKey = tuple[WorkflowIdentifier, WorkflowIdentifier, WorkflowIdentifier, str]
ActiveKey = tuple[WorkflowIdentifier, WorkflowIdentifier, WorkflowIdentifier]


class OurochestBindKind(str, Enum):
    """Explicit outcome of attempting an initial board bind."""

    BOUND = "bound"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"
    INELIGIBLE_BOARD = "ineligible_board"
    ALREADY_BOUND = "already_bound"


@dataclass(frozen=True, slots=True)
class OurochestBindResult:
    """Immutable initial-bind outcome and optional newly active state."""

    kind: OurochestBindKind
    workflow: OurochestWorkflowState | None = None

    def __post_init__(self) -> None:
        try:
            kind = OurochestBindKind(self.kind)
        except (TypeError, ValueError) as error:
            raise ValueError("kind must be an OurochestBindKind") from error
        object.__setattr__(self, "kind", kind)
        if kind is OurochestBindKind.BOUND:
            if self.workflow is None:
                raise ValueError("BOUND requires workflow state")
            if self.workflow.status is not OurochestWorkflowStatus.ACTIVE:
                raise ValueError("BOUND requires active workflow state")
        elif self.workflow is not None:
            raise ValueError(f"{kind.value} must not carry workflow state")


class OurochestWorkflowService:
    """Own separate exact-scope pending and board-bound in-memory indexes."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._pending: dict[PendingKey, OurochestWorkflowState] = {}
        self._active: dict[ActiveKey, OurochestWorkflowState] = {}

    def create_pending(
        self,
        guild_id: WorkflowIdentifier,
        channel_id: WorkflowIdentifier,
        user_id: WorkflowIdentifier,
    ) -> OurochestWorkflowState:
        """Create or replace one user's unbound Ourochest workflow."""

        self._validate_scope(guild_id, channel_id, user_id)
        now = self._now()
        key = self._pending_key(guild_id, channel_id, user_id)
        self._expire_pending_key(key, now)
        state = OurochestWorkflowState(
            guild_id=guild_id,
            channel_id=channel_id,
            initiating_user_id=user_id,
            workflow_kind=OUROCHEST_WORKFLOW_KIND,
            status=OurochestWorkflowStatus.PENDING_BOARD,
            created_at=now,
            expires_at=now + WORKFLOW_TTL_SECONDS,
        )
        self._pending[key] = state
        return state

    def get_pending(
        self,
        guild_id: WorkflowIdentifier,
        channel_id: WorkflowIdentifier,
        user_id: WorkflowIdentifier,
    ) -> OurochestWorkflowState | None:
        """Return one exact pending workflow without refreshing its expiry."""

        self._validate_scope(guild_id, channel_id, user_id)
        key = self._pending_key(guild_id, channel_id, user_id)
        state = self._pending.get(key)
        if state is not None and self._expired(state, self._now()):
            self._pending.pop(key, None)
            return None
        return state

    def bind_initial_board(
        self,
        guild_id: WorkflowIdentifier,
        channel_id: WorkflowIdentifier,
        board_message_id: WorkflowIdentifier,
        board: OuroHuntBoard,
    ) -> OurochestBindResult:
        """Bind exactly one eligible pending workflow to an initial board."""

        _require_identifier(guild_id, "guild_id")
        _require_identifier(channel_id, "channel_id")
        _require_identifier(board_message_id, "board_message_id")
        if not isinstance(board, OuroHuntBoard):
            raise TypeError("board must be an OuroHuntBoard")

        now = self._now()
        active_key = (guild_id, channel_id, board_message_id)
        existing = self._active.get(active_key)
        if existing is not None:
            if self._expired(existing, now):
                self._active.pop(active_key, None)
            else:
                return OurochestBindResult(OurochestBindKind.ALREADY_BOUND)

        self._expire_pending_scope(guild_id, channel_id, now)
        if not self._eligible_initial_board(board):
            return OurochestBindResult(OurochestBindKind.INELIGIBLE_BOARD)

        matches = tuple(
            state
            for key, state in self._pending.items()
            if key[0] == guild_id and key[1] == channel_id
        )
        if not matches:
            return OurochestBindResult(OurochestBindKind.NO_MATCH)
        if len(matches) > 1:
            return OurochestBindResult(OurochestBindKind.AMBIGUOUS)

        candidates = initial_red_candidates()
        if len(candidates) != 24:
            raise RuntimeError("initial_red_candidates must contain exactly 24 coordinates")
        pending = matches[0]
        active = OurochestWorkflowState(
            guild_id=pending.guild_id,
            channel_id=pending.channel_id,
            initiating_user_id=pending.initiating_user_id,
            workflow_kind=OUROCHEST_WORKFLOW_KIND,
            status=OurochestWorkflowStatus.ACTIVE,
            created_at=pending.created_at,
            expires_at=now + WORKFLOW_TTL_SECONDS,
            board_message_id=board_message_id,
            bound_at=now,
            last_board=board,
            red_candidates=candidates,
        )
        pending_key = self._pending_key(
            pending.guild_id, pending.channel_id, pending.initiating_user_id
        )
        self._pending.pop(pending_key, None)
        self._active[active_key] = active
        return OurochestBindResult(OurochestBindKind.BOUND, active)

    def get_active(
        self,
        guild_id: WorkflowIdentifier,
        channel_id: WorkflowIdentifier,
        board_message_id: WorkflowIdentifier,
    ) -> OurochestWorkflowState | None:
        """Return one exact board-bound workflow without refreshing expiry."""

        _require_identifier(guild_id, "guild_id")
        _require_identifier(channel_id, "channel_id")
        _require_identifier(board_message_id, "board_message_id")
        key = (guild_id, channel_id, board_message_id)
        state = self._active.get(key)
        if state is not None and self._expired(state, self._now()):
            self._active.pop(key, None)
            return None
        return state

    def remove_active(
        self,
        guild_id: WorkflowIdentifier,
        channel_id: WorkflowIdentifier,
        board_message_id: WorkflowIdentifier,
    ) -> OurochestWorkflowState | None:
        """Remove one exact active workflow, if it remains live."""

        _require_identifier(guild_id, "guild_id")
        _require_identifier(channel_id, "channel_id")
        _require_identifier(board_message_id, "board_message_id")
        key = (guild_id, channel_id, board_message_id)
        state = self._active.get(key)
        if state is not None and self._expired(state, self._now()):
            self._active.pop(key, None)
            return None
        return self._active.pop(key, None)

    def _now(self) -> float:
        now = self._clock()
        if isinstance(now, bool) or not isinstance(now, (int, float)):
            raise TypeError("clock must return a finite number")
        return float(now)

    @staticmethod
    def _pending_key(
        guild_id: WorkflowIdentifier,
        channel_id: WorkflowIdentifier,
        user_id: WorkflowIdentifier,
    ) -> PendingKey:
        return (guild_id, channel_id, user_id, OUROCHEST_WORKFLOW_KIND)

    @staticmethod
    def _validate_scope(
        guild_id: WorkflowIdentifier,
        channel_id: WorkflowIdentifier,
        user_id: WorkflowIdentifier,
    ) -> None:
        _require_identifier(guild_id, "guild_id")
        _require_identifier(channel_id, "channel_id")
        _require_identifier(user_id, "user_id")

    def _expire_pending_scope(
        self,
        guild_id: WorkflowIdentifier,
        channel_id: WorkflowIdentifier,
        now: float,
    ) -> None:
        for key in tuple(self._pending):
            if key[0] == guild_id and key[1] == channel_id:
                self._expire_pending_key(key, now)

    def _expire_pending_key(self, key: PendingKey, now: float) -> None:
        state = self._pending.get(key)
        if state is not None and self._expired(state, now):
            self._pending.pop(key, None)

    @staticmethod
    def _expired(state: OurochestWorkflowState, now: float) -> bool:
        return now >= state.expires_at

    @staticmethod
    def _eligible_initial_board(board: OuroHuntBoard) -> bool:
        if any(cell.disabled is not False for cell in board.cells):
            return False
        return all(
            resolve_ourochest_visual(cell.visual_identity).kind
            is OurochestVisualResolutionKind.HIDDEN
            for cell in board.cells
        )
