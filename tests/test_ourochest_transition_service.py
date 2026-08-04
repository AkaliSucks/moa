from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any

import pytest

from moa.models.ourochest import (
    OurochestObservation,
    OurochestSphere,
)
from moa.models.ourosphere import OuroHuntBoard, OuroHuntVisualIdentity
from moa.services.ourochest_transition_service import (
    OurochestTransitionKind,
    OurochestTransitionResult,
    OurochestTransitionService,
)
from moa.services.ourosphere_board_service import OuroHuntBoardService


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "discord" / "oc_structural_capture.v1.json"
ANNOTATION_PATH = Path(__file__).parent / "fixtures" / "discord" / "oc_characterization.v1.json"
BOARD_SERVICE = OuroHuntBoardService()
TRANSITION_SERVICE = OurochestTransitionService()


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _annotation() -> dict[str, Any]:
    return json.loads(ANNOTATION_PATH.read_text(encoding="utf-8"))


def _boards() -> list[OuroHuntBoard]:
    return [BOARD_SERVICE.project(record["message"]) for record in _fixture()["records"]]


def _visuals() -> dict[str, OuroHuntVisualIdentity]:
    values = _annotation()["semantic_visual_aliases"]["visuals"]
    return {
        name: OuroHuntVisualIdentity(**value)
        for name, value in values.items()
    }


def _changed_board(
    board: OuroHuntBoard,
    coordinate: tuple[int, int],
    *,
    visual: OuroHuntVisualIdentity,
    disabled: bool,
) -> OuroHuntBoard:
    cells = [
        replace(cell, visual_identity=visual, disabled=disabled)
        if cell.coordinate == coordinate
        else cell
        for cell in board.cells
    ]
    return OuroHuntBoard(tuple(cells))


def test_no_change_returns_no_observation() -> None:
    board = _boards()[0]

    result = TRANSITION_SERVICE.interpret(board, board)

    assert result == OurochestTransitionResult(OurochestTransitionKind.NO_CHANGE)
    assert result.observation is None


def test_terminal_has_precedence_over_mass_reveals(monkeypatch: pytest.MonkeyPatch) -> None:
    previous, current = _boards()[-2:]

    def fail_if_visual_is_resolved(_: OuroHuntVisualIdentity) -> object:
        raise AssertionError("terminal transitions must not resolve mass reveals")

    monkeypatch.setattr(
        "moa.services.ourochest_transition_service.resolve_ourochest_visual",
        fail_if_visual_is_resolved,
    )

    result = TRANSITION_SERVICE.interpret(previous, current)

    assert result == OurochestTransitionResult(OurochestTransitionKind.TERMINAL)
    assert result.observation is None


def test_valid_blue_transition_emits_exact_observation() -> None:
    before = _boards()[0]
    current = _changed_board(
        before, (3, 3), visual=_visuals()["Blue"], disabled=True
    )

    result = TRANSITION_SERVICE.interpret(before, current)

    assert result.kind is OurochestTransitionKind.OBSERVATION
    assert result.observation == OurochestObservation((3, 3), OurochestSphere.BLUE)


@pytest.mark.parametrize(
    ("visual", "sphere"),
    [
        ("Red", OurochestSphere.RED),
        ("Green", OurochestSphere.GREEN),
        ("Yellow", OurochestSphere.YELLOW),
        ("Teal", OurochestSphere.TEAL),
    ],
)
def test_known_supported_semantics_emit_observations(
    visual: str, sphere: OurochestSphere
) -> None:
    before = _boards()[0]
    current = _changed_board(before, (0, 0), visual=_visuals()[visual], disabled=True)

    result = TRANSITION_SERVICE.interpret(before, current)

    assert result == OurochestTransitionResult(
        OurochestTransitionKind.OBSERVATION,
        OurochestObservation((0, 0), sphere),
    )


def test_orange_is_a_valid_observation_without_candidate_reduction() -> None:
    before = _boards()[0]
    current = _changed_board(before, (0, 0), visual=_visuals()["Orange"], disabled=True)

    result = TRANSITION_SERVICE.interpret(before, current)

    assert result == OurochestTransitionResult(
        OurochestTransitionKind.OBSERVATION,
        OurochestObservation((0, 0), OurochestSphere.ORANGE),
    )


def test_unknown_current_visual_has_no_observation() -> None:
    before = _boards()[0]
    unknown = OuroHuntVisualIdentity(
        kind="custom",
        id_sha256="a" * 64,
        name_sha256="b" * 64,
        name_length=7,
    )
    current = _changed_board(before, (0, 0), visual=unknown, disabled=True)

    result = TRANSITION_SERVICE.interpret(before, current)

    assert result == OurochestTransitionResult(OurochestTransitionKind.UNKNOWN_VISUAL)
    assert result.observation is None


def test_current_still_hidden_is_malformed() -> None:
    before = _boards()[0]
    current = _changed_board(
        before, (0, 0), visual=_visuals()["hidden/question"], disabled=True
    )

    result = TRANSITION_SERVICE.interpret(before, current)

    assert result.kind is OurochestTransitionKind.MALFORMED_TRANSITION
    assert result.observation is None


def test_prior_visual_must_be_hidden() -> None:
    hidden_board = _boards()[0]
    previous = _changed_board(
        hidden_board, (0, 0), visual=_visuals()["Blue"], disabled=False
    )
    current = _changed_board(previous, (0, 0), visual=_visuals()["Green"], disabled=True)

    result = TRANSITION_SERVICE.interpret(previous, current)

    assert result.kind is OurochestTransitionKind.MALFORMED_TRANSITION
    assert result.observation is None


def test_multiple_changed_cells_are_malformed() -> None:
    before = _boards()[0]
    current = _changed_board(before, (0, 0), visual=_visuals()["Blue"], disabled=True)
    current = _changed_board(current, (0, 1), visual=_visuals()["Green"], disabled=True)

    result = TRANSITION_SERVICE.interpret(before, current)

    assert result.kind is OurochestTransitionKind.MALFORMED_TRANSITION
    assert result.observation is None


@pytest.mark.parametrize(
    ("disabled_before", "disabled_after"),
    [(False, False), (True, True), (True, False)],
)
def test_disabled_direction_must_be_enabled_to_disabled(
    disabled_before: bool, disabled_after: bool
) -> None:
    base = _boards()[0]
    before = _changed_board(
        base,
        (0, 0),
        visual=_visuals()["hidden/question"],
        disabled=disabled_before,
    )
    current = _changed_board(
        before, (0, 0), visual=_visuals()["Blue"], disabled=disabled_after
    )

    result = TRANSITION_SERVICE.interpret(before, current)

    assert result.kind is OurochestTransitionKind.MALFORMED_TRANSITION
    assert result.observation is None


def test_visual_unchanged_with_disable_is_malformed() -> None:
    before = _boards()[0]
    current = _changed_board(
        before, (0, 0), visual=_visuals()["hidden/question"], disabled=True
    )

    result = TRANSITION_SERVICE.interpret(before, current)

    assert result.kind is OurochestTransitionKind.MALFORMED_TRANSITION
    assert result.observation is None


def test_component_identity_mismatch_fails_closed() -> None:
    before = _boards()[0]
    changed = replace(before.cell_at((0, 0)), component_identity="c" * 64)
    current = OuroHuntBoard(
        tuple(changed if cell.coordinate == (0, 0) else cell for cell in before.cells)
    )

    result = TRANSITION_SERVICE.interpret(before, current)

    assert result == OurochestTransitionResult(
        OurochestTransitionKind.MALFORMED_TRANSITION
    )
    assert result.observation is None


def test_interpretation_is_deterministic_and_does_not_mutate_inputs() -> None:
    before = _boards()[0]
    current = _changed_board(before, (3, 3), visual=_visuals()["Blue"], disabled=True)
    before_snapshot = before
    current_snapshot = current

    first = TRANSITION_SERVICE.interpret(before, current)
    second = TRANSITION_SERVICE.interpret(before, current)

    assert first == second
    assert before == before_snapshot
    assert current == current_snapshot


@pytest.mark.parametrize(
    "kind",
    [
        OurochestTransitionKind.NO_CHANGE,
        OurochestTransitionKind.TERMINAL,
        OurochestTransitionKind.UNKNOWN_VISUAL,
        OurochestTransitionKind.MALFORMED_TRANSITION,
    ],
)
def test_non_observation_result_states_carry_no_observation(
    kind: OurochestTransitionKind,
) -> None:
    result = OurochestTransitionResult(kind)

    assert result.observation is None


def test_observation_result_requires_exactly_one_observation() -> None:
    with pytest.raises(TypeError, match="requires"):
        OurochestTransitionResult(OurochestTransitionKind.OBSERVATION)

    with pytest.raises(ValueError, match="must not carry"):
        OurochestTransitionResult(
            OurochestTransitionKind.NO_CHANGE,
            OurochestObservation((0, 0), OurochestSphere.BLUE),
        )


def test_capture_one_sequence_never_infers_final_orange() -> None:
    boards = _boards()
    results = [
        TRANSITION_SERVICE.interpret(previous, current)
        for previous, current in zip(boards[:-1], boards[1:], strict=True)
    ]

    assert [
        (result.kind, result.observation.coordinate, result.observation.sphere)
        for result in results[:4]
        if result.observation is not None
    ] == [
        (OurochestTransitionKind.OBSERVATION, (3, 3), OurochestSphere.BLUE),
        (OurochestTransitionKind.OBSERVATION, (1, 1), OurochestSphere.GREEN),
        (OurochestTransitionKind.OBSERVATION, (1, 0), OurochestSphere.BLUE),
        (OurochestTransitionKind.OBSERVATION, (4, 1), OurochestSphere.RED),
    ]
    assert results[4] == OurochestTransitionResult(OurochestTransitionKind.TERMINAL)
    assert results[4].observation is None


def test_transition_api_requires_only_projected_boards() -> None:
    before = _boards()[0]
    current = _changed_board(before, (0, 0), visual=_visuals()["Blue"], disabled=True)

    result = TRANSITION_SERVICE.interpret(before, current)

    assert result.observation == OurochestObservation((0, 0), OurochestSphere.BLUE)
