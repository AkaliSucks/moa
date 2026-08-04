"""Offline tests for pure ``$oc`` workflow advancement."""

from __future__ import annotations

from dataclasses import fields
import json
from pathlib import Path
from typing import Any

import pytest

from moa.models.ourochest import OurochestObservation, OurochestSphere
from moa.models.ourochest_workflow import (
    OUROCHEST_WORKFLOW_KIND,
    OurochestWorkflowState,
    OurochestWorkflowStatus,
)
from moa.models.ourosphere import OuroHuntBoard
from moa.services import ourochest_workflow_coordinator as coordinator_module
from moa.services.ourochest_candidate_service import initial_red_candidates
from moa.services.ourochest_transition_service import (
    OurochestTransitionKind,
    OurochestTransitionResult,
    OurochestTransitionService,
)
from moa.services.ourochest_workflow_coordinator import (
    OurochestWorkflowAdvanceReason,
    OurochestWorkflowCoordinator,
    advance_ourochest_workflow,
)
from moa.services.ourosphere_board_service import OuroHuntBoardService


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "discord" / "oc_structural_capture.v1.json"
BOARD_SERVICE = OuroHuntBoardService()
TRANSITION_SERVICE = OurochestTransitionService()


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _boards() -> list[OuroHuntBoard]:
    return [BOARD_SERVICE.project(record["message"]) for record in _fixture()["records"]]


def _workflow(
    board: OuroHuntBoard | None = None,
    candidates: tuple[tuple[int, int], ...] | None = None,
    *,
    status: OurochestWorkflowStatus = OurochestWorkflowStatus.ACTIVE,
) -> OurochestWorkflowState:
    return OurochestWorkflowState(
        guild_id="guild-a",
        channel_id="channel-a",
        initiating_user_id="user-a",
        workflow_kind=OUROCHEST_WORKFLOW_KIND,
        status=status,
        created_at=100.0,
        expires_at=220.0,
        board_message_id="board-message-a",
        bound_at=110.0,
        last_board=_boards()[0] if board is None else board,
        red_candidates=initial_red_candidates() if candidates is None else candidates,
    )


def _transition(
    kind: OurochestTransitionKind,
    observation: OurochestObservation | None = None,
) -> OurochestTransitionResult:
    return OurochestTransitionResult(kind, observation)


def _observation(coordinate: tuple[int, int], sphere: OurochestSphere) -> OurochestObservation:
    return OurochestObservation(coordinate, sphere)


def test_only_active_bound_workflows_are_accepted() -> None:
    pending = OurochestWorkflowState(
        guild_id="guild-a",
        channel_id="channel-a",
        initiating_user_id="user-a",
        workflow_kind=OUROCHEST_WORKFLOW_KIND,
        status=OurochestWorkflowStatus.PENDING_BOARD,
        created_at=100.0,
        expires_at=220.0,
    )

    result = advance_ourochest_workflow(
        _workflow(), _boards()[0], _transition(OurochestTransitionKind.NO_CHANGE)
    )
    assert result.status is OurochestWorkflowStatus.ACTIVE

    with pytest.raises(ValueError, match="ACTIVE"):
        advance_ourochest_workflow(
            pending, _boards()[0], _transition(OurochestTransitionKind.NO_CHANGE)
        )
    with pytest.raises(ValueError, match="ACTIVE"):
        advance_ourochest_workflow(
            _workflow(status=OurochestWorkflowStatus.TERMINAL),
            _boards()[0],
            _transition(OurochestTransitionKind.NO_CHANGE),
        )


def test_no_change_preserves_workflow_and_does_not_reduce(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = _workflow()

    def fail_if_called(*_: object) -> object:
        raise AssertionError("NO_CHANGE must not reduce candidates")

    monkeypatch.setattr(coordinator_module, "reduce_red_candidates", fail_if_called)
    result = advance_ourochest_workflow(
        workflow, _boards()[0], _transition(OurochestTransitionKind.NO_CHANGE)
    )

    assert result.workflow is workflow
    assert result.status is OurochestWorkflowStatus.ACTIVE
    assert result.red_candidates == workflow.red_candidates
    assert result.last_board == workflow.last_board
    assert result.candidate_count == 24
    assert result.unique_red is None
    assert workflow.expires_at == 220.0


def test_terminal_retains_candidates_and_current_final_board_without_reduction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _workflow()

    def fail_if_called(*_: object) -> object:
        raise AssertionError("TERMINAL must not reduce candidates")

    monkeypatch.setattr(coordinator_module, "reduce_red_candidates", fail_if_called)
    terminal = _boards()[-1]
    result = advance_ourochest_workflow(
        workflow, terminal, _transition(OurochestTransitionKind.TERMINAL)
    )

    assert result.status is OurochestWorkflowStatus.TERMINAL
    assert result.red_candidates == workflow.red_candidates
    assert result.last_board == terminal
    assert result.unique_red is None


@pytest.mark.parametrize(
    ("sphere", "coordinate", "expected"),
    [
        (
            OurochestSphere.BLUE,
            (3, 3),
            ((0, 1), (0, 2), (0, 4), (1, 0), (1, 2), (1, 4), (2, 0), (2, 1), (4, 0), (4, 1)),
        ),
        (
            OurochestSphere.GREEN,
            (1, 1),
            ((0, 1), (1, 0), (1, 2), (1, 3), (1, 4), (2, 1), (3, 1), (4, 1)),
        ),
        (
            OurochestSphere.YELLOW,
            (2, 2),
            ((0, 0), (0, 4), (1, 1), (1, 3), (3, 1), (3, 3), (4, 0), (4, 4)),
        ),
        (
            OurochestSphere.TEAL,
            (1, 1),
            ((0, 0), (0, 1), (0, 2), (1, 0), (1, 2), (1, 3), (1, 4), (2, 0), (2, 1), (3, 1), (3, 3), (4, 1), (4, 4)),
        ),
        (OurochestSphere.RED, (4, 1), ((4, 1),)),
    ],
)
def test_supported_observations_reduce_and_advance(
    sphere: OurochestSphere,
    coordinate: tuple[int, int],
    expected: tuple[tuple[int, int], ...],
) -> None:
    current = _boards()[1]
    result = advance_ourochest_workflow(
        _workflow(),
        current,
        _transition(
            OurochestTransitionKind.OBSERVATION,
            _observation(coordinate, sphere),
        ),
    )

    assert result.status is OurochestWorkflowStatus.ACTIVE
    assert result.red_candidates == expected
    assert result.last_board == current
    assert result.candidate_count == len(expected)
    if len(expected) == 1:
        assert result.unique_red == expected[0]
    else:
        assert result.unique_red is None


def test_orange_is_unsupported_with_current_board_and_no_reducer_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _workflow()

    def fail_if_called(*_: object) -> object:
        raise AssertionError("Orange must not reduce candidates")

    monkeypatch.setattr(coordinator_module, "reduce_red_candidates", fail_if_called)
    current = _boards()[1]
    result = advance_ourochest_workflow(
        workflow,
        current,
        _transition(
            OurochestTransitionKind.OBSERVATION,
            _observation((0, 0), OurochestSphere.ORANGE),
        ),
    )

    assert result.status is OurochestWorkflowStatus.UNSUPPORTED
    assert result.reason is OurochestWorkflowAdvanceReason.ORANGE_GEOMETRY_UNSUPPORTED
    assert result.red_candidates == workflow.red_candidates
    assert result.last_board == current


def test_unknown_visual_is_unsupported_with_distinct_reason_and_current_board(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _workflow()

    def fail_if_called(*_: object) -> object:
        raise AssertionError("UNKNOWN_VISUAL must not reduce candidates")

    monkeypatch.setattr(coordinator_module, "reduce_red_candidates", fail_if_called)
    current = _boards()[1]
    result = advance_ourochest_workflow(
        workflow, current, _transition(OurochestTransitionKind.UNKNOWN_VISUAL)
    )

    assert result.status is OurochestWorkflowStatus.UNSUPPORTED
    assert result.reason is OurochestWorkflowAdvanceReason.UNKNOWN_VISUAL
    assert result.reason is not OurochestWorkflowAdvanceReason.ORANGE_GEOMETRY_UNSUPPORTED
    assert result.red_candidates == workflow.red_candidates
    assert result.last_board == current


def test_malformed_retains_last_known_good_board_and_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _workflow()

    def fail_if_called(*_: object) -> object:
        raise AssertionError("MALFORMED_TRANSITION must not reduce candidates")

    monkeypatch.setattr(coordinator_module, "reduce_red_candidates", fail_if_called)
    malformed_current = _boards()[1]
    result = advance_ourochest_workflow(
        workflow,
        malformed_current,
        _transition(OurochestTransitionKind.MALFORMED_TRANSITION),
    )

    assert result.status is OurochestWorkflowStatus.MALFORMED
    assert result.reason is OurochestWorkflowAdvanceReason.MALFORMED_TRANSITION
    assert result.red_candidates == workflow.red_candidates
    assert result.last_board == workflow.last_board
    assert result.last_board != malformed_current


def test_empty_supported_reduction_is_contradiction() -> None:
    result = advance_ourochest_workflow(
        _workflow(candidates=((0, 0),)),
        _boards()[1],
        _transition(
            OurochestTransitionKind.OBSERVATION,
            _observation((1, 1), OurochestSphere.GREEN),
        ),
    )

    assert result.status is OurochestWorkflowStatus.CONTRADICTION
    assert result.red_candidates == ()
    assert result.candidate_count == 0
    assert result.unique_red is None
    assert result.last_board == _boards()[1]


def test_unknown_semantic_observation_fails_closed() -> None:
    with pytest.raises(ValueError, match="UNKNOWN sphere"):
        advance_ourochest_workflow(
            _workflow(),
            _boards()[1],
            _transition(
                OurochestTransitionKind.OBSERVATION,
                _observation((0, 0), OurochestSphere.UNKNOWN),
            ),
        )


def test_metadata_and_expiry_are_preserved_across_active_update() -> None:
    workflow = _workflow()
    result = OurochestWorkflowCoordinator.advance(
        workflow,
        _boards()[1],
        _transition(
            OurochestTransitionKind.OBSERVATION,
            _observation((3, 3), OurochestSphere.BLUE),
        ),
    )

    updated = result.workflow
    assert (updated.guild_id, updated.channel_id, updated.initiating_user_id) == (
        workflow.guild_id,
        workflow.channel_id,
        workflow.initiating_user_id,
    )
    assert updated.workflow_kind == workflow.workflow_kind
    assert updated.board_message_id == workflow.board_message_id
    assert updated.created_at == workflow.created_at
    assert updated.bound_at == workflow.bound_at
    assert updated.expires_at == workflow.expires_at


def test_advancement_is_deterministic_and_does_not_mutate_inputs() -> None:
    workflow = _workflow()
    current = _boards()[1]
    transition = _transition(
        OurochestTransitionKind.OBSERVATION,
        _observation((3, 3), OurochestSphere.BLUE),
    )
    workflow_snapshot = workflow
    current_snapshot = current
    transition_snapshot = transition

    first = advance_ourochest_workflow(workflow, current, transition)
    second = advance_ourochest_workflow(workflow, current, transition)

    assert first == second
    assert workflow == workflow_snapshot
    assert current == current_snapshot
    assert transition == transition_snapshot


def test_capture_sequence_reduces_to_red_then_terminal_without_orange_inference() -> None:
    boards = _boards()
    workflow = _workflow(board=boards[0])
    results = []
    for previous, current in zip(boards[:-1], boards[1:], strict=True):
        transition = TRANSITION_SERVICE.interpret(previous, current)
        result = advance_ourochest_workflow(workflow, current, transition)
        results.append(result)
        workflow = result.workflow

    assert [result.candidate_count for result in results[:3]] == [10, 6, 1]
    assert results[2].unique_red == (4, 1)
    assert results[3].status is OurochestWorkflowStatus.ACTIVE
    assert results[3].red_candidates == ((4, 1),)
    assert results[4].status is OurochestWorkflowStatus.TERMINAL
    assert results[4].red_candidates == ((4, 1),)
    assert results[4].last_board == boards[-1]


def test_result_has_no_recommendation_or_payout_state() -> None:
    result = advance_ourochest_workflow(
        _workflow(), _boards()[0], _transition(OurochestTransitionKind.NO_CHANGE)
    )

    assert {field.name for field in fields(result)} == {"workflow", "reason"}
    for forbidden in ("next_cell", "recommendation", "ev", "reward", "payout"):
        assert not hasattr(result, forbidden)
