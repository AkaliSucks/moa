from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from moa.services.ourosphere_board_service import OuroHuntBoardService


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "discord" / "oc_structural_capture.v1.json"
ANNOTATION_PATH = Path(__file__).parent / "fixtures" / "discord" / "oc_characterization.v1.json"
SERVICE = OuroHuntBoardService()
EXPECTED_COORDINATES = {(row, column) for row in range(5) for column in range(5)}


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _annotation() -> dict[str, Any]:
    return json.loads(ANNOTATION_PATH.read_text(encoding="utf-8"))


def _boards() -> list[Any]:
    return [SERVICE.project(record["message"]) for record in _fixture()["records"]]


def test_structural_fixture_has_six_complete_stable_boards() -> None:
    records = _fixture()["records"]
    boards = _boards()
    assert len(records) == 6
    assert all(len(board.cells) == 25 for board in boards)
    assert all(set(board.coordinates) == EXPECTED_COORDINATES for board in boards)
    assert all(board.coordinates == tuple(sorted(EXPECTED_COORDINATES)) for board in boards)
    identities = {cell.coordinate: cell.component_identity for cell in boards[0].cells}
    assert all(
        {cell.coordinate: cell.component_identity for cell in board.cells} == identities
        for board in boards[1:]
    )
    assert all(isinstance(cell.disabled, bool) for board in boards for cell in board.cells)
    assert not boards[-2].is_terminal
    assert boards[-1].is_terminal
    assert all(cell.disabled for cell in boards[-1].cells)


def test_structural_transitions_preserve_neutral_action_contract() -> None:
    boards = _boards()
    transitions = [
        SERVICE.compare(before, after)
        for before, after in zip(boards[:-1], boards[1:], strict=True)
    ]
    for transition in transitions[:4]:
        action_cells = [
            cell
            for cell in transition.cells
            if cell.visual_changed or cell.disabled_changed
        ]
        assert len(action_cells) == 1
        action = action_cells[0]
        assert action.visual_changed
        assert action.disabled_before is False
        assert action.disabled_after is True

    terminal = transitions[-1]
    assert len([cell for cell in terminal.cells if cell.visual_changed or cell.disabled_changed]) > 1
    assert not hasattr(terminal, "clicked_coordinate")


def test_annotation_has_exactly_seven_opaque_visual_concepts() -> None:
    visuals = _annotation()["semantic_visual_aliases"]["visuals"]
    assert set(visuals) == {
        "hidden/question",
        "Red",
        "Orange",
        "Yellow",
        "Green",
        "Teal",
        "Blue",
    }


def test_annotation_records_exact_action_sequence_and_totals() -> None:
    annotation = _annotation()
    assert [
        (action["coordinate"], action["transition"], action["reward"])
        for action in annotation["actions"]
    ] == [
        ("R4C4", "hidden/question -> Blue", 10),
        ("R2C2", "hidden/question -> Green", 35),
        ("R2C1", "hidden/question -> Blue", 10),
        ("R5C2", "hidden/question -> Red", 150),
        ("R5C1", "hidden/question -> Orange", 90),
    ]
    assert annotation["actions"][-1]["terminal_action"] is True
    assert annotation["final_stock"] == 1909
    assert annotation["observed_total_gain"] == 295


def test_annotation_distinguishes_final_click_provenance_and_counts() -> None:
    annotation = _annotation()
    assert annotation["final_click_provenance"] == {
        "coordinate": "R5C1",
        "status": "operator-observed",
        "structural_inference": "not inferred from terminal all-disabled state",
    }
    assert annotation["count_provenance"]["mudae_stated_fixed"] == {
        "Red": 1,
        "Orange": 2,
        "Yellow": 3,
        "Green": 4,
    }
    assert annotation["count_provenance"]["capture_realized_only"] == {"Teal": 3, "Blue": 12}


def test_annotation_preserves_orange_wording_without_adjacency_semantics() -> None:
    ambiguity = _annotation()["orange_ambiguity"]
    assert ambiguity["captured_wording"] == "Orange is always next to Red."
    assert ambiguity["universal_definition_proven"] is False
    assert ambiguity["capture_observation"] == {
        "red": "R5C2",
        "orange": ["R5C1", "R5C3"],
    }
    assert len(ambiguity["unresolved_semantics"]) == 3


def test_annotation_records_candidate_reasoning_as_evidence() -> None:
    reasoning = _annotation()["candidate_reasoning"]
    assert reasoning["initial"]["count"] == 24
    assert reasoning["initial"]["excluded"] == "R3C3"
    assert reasoning["after_R2C2_Green"] == [
        "R1C2",
        "R2C1",
        "R2C3",
        "R2C5",
        "R3C2",
        "R5C2",
    ]
    assert reasoning["after_R2C1_Blue"] == ["R5C2"]
    assert reasoning["red_click"]["terminal_inference"] is False


def test_annotation_records_opening_strategy_without_recommendation() -> None:
    strategy = _annotation()["opening_strategy"]
    assert strategy["preferred_squares"] == ["R2C2", "R2C4", "R4C2", "R4C4"]
    assert strategy["symmetry"]
    assert strategy["globally_optimal"] is False
    assert strategy["capture_used"] == "R4C4"
