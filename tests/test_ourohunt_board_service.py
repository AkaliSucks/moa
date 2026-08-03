from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from moa.models.ourosphere import OuroHuntBoard
from moa.services.ourosphere_board_service import (
    OuroHuntBoardProjectionError,
    OuroHuntBoardService,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "discord" / "oh_structural_capture.v1.json"
OC_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "discord" / "oc_structural_capture.v1.json"
EXPECTED_COORDINATES = {(row, column) for row in range(5) for column in range(5)}
SERVICE = OuroHuntBoardService()


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _board_snapshots() -> list[dict[str, Any]]:
    return [
        record["message"]
        for record in _fixture()["records"]
        if record["message"]["alias"] == "board_message_1"
    ]


def _boards() -> list[OuroHuntBoard]:
    return [SERVICE.project(snapshot) for snapshot in _board_snapshots()]


def _oc_fixture() -> dict[str, Any]:
    return json.loads(OC_FIXTURE_PATH.read_text(encoding="utf-8"))


def _oc_boards() -> list[OuroHuntBoard]:
    return [SERVICE.project(record["message"]) for record in _oc_fixture()["records"]]


def _synthetic_snapshot(cell_count: int = 25) -> dict[str, Any]:
    leaves: list[dict[str, Any]] = []
    for index in range(cell_count):
        row, column = divmod(index, 5)
        leaves.append(
            {
                "path": [row, column],
                "custom_id_sha256": f"{index + 1:064x}",
                "emoji": {
                    "kind": "custom",
                    "id_sha256": f"{index + 101:064x}",
                    "name_sha256": f"{index + 201:064x}",
                    "name_length": 3,
                },
                "disabled": False,
            }
        )
    return {"components": [{"components": leaves[index : index + 5]} for index in range(0, cell_count, 5)]}


def test_fixture_board_discovery_projects_seven_boards() -> None:
    snapshots = _board_snapshots()

    assert len(snapshots) == 7
    assert all(isinstance(SERVICE.project(snapshot), OuroHuntBoard) for snapshot in snapshots)


def test_fixture_boards_project_complete_deterministic_5x5_state() -> None:
    for board in _boards():
        assert len(board.cells) == 25
        assert set(board.coordinates) == EXPECTED_COORDINATES
        assert board.coordinates == tuple(sorted(EXPECTED_COORDINATES))
        assert all(cell.coordinate == coordinate for cell, coordinate in zip(board.cells, board.coordinates, strict=True))
        assert all(cell.component_identity for cell in board.cells)
        assert all(cell.visual_identity for cell in board.cells)
        assert all(isinstance(cell.disabled, bool) for cell in board.cells)
        assert board.cell_at((2, 3)).coordinate == (2, 3)


def test_component_identity_is_stable_at_each_coordinate() -> None:
    boards = _boards()
    identities = {cell.coordinate: cell.component_identity for cell in boards[0].cells}

    for board in boards[1:]:
        assert {cell.coordinate: cell.component_identity for cell in board.cells} == identities


def test_opaque_visual_identity_can_repeat_across_positions_without_semantics() -> None:
    visual_identities = [cell.visual_identity for cell in _boards()[0].cells]

    assert len(set(visual_identities)) < len(visual_identities)


def test_structural_reveal_transition_changes_visual_and_keeps_enabled() -> None:
    boards = _boards()
    transitions = [
        SERVICE.compare(before, after).cells
        for before, after in zip(boards[:-1], boards[1:], strict=True)
    ]

    assert any(
        transition.visual_changed
        and not transition.disabled_before
        and not transition.disabled_after
        and not transition.disabled_changed
        for cells in transitions
        for transition in cells
    )


def test_structural_disable_transition_keeps_visual_and_becomes_disabled() -> None:
    boards = _boards()
    transitions = [
        SERVICE.compare(before, after).cells
        for before, after in zip(boards[:-1], boards[1:], strict=True)
    ]

    assert any(
        not transition.visual_changed
        and transition.disabled_before is False
        and transition.disabled_after is True
        and transition.became_disabled
        for cells in transitions
        for transition in cells
    )


def test_structural_visual_change_and_disable_transition_is_observable() -> None:
    boards = _boards()
    transitions = [
        SERVICE.compare(before, after).cells
        for before, after in zip(boards[:-1], boards[1:], strict=True)
    ]

    assert any(
        transition.visual_changed
        and transition.disabled_before is False
        and transition.disabled_after is True
        for cells in transitions
        for transition in cells
    )


def test_terminal_state_is_all_disabled_without_claim_inference() -> None:
    boards = _boards()

    assert boards[-1].is_terminal
    assert all(cell.disabled for cell in boards[-1].cells)
    assert not boards[-2].is_terminal


def test_terminal_transition_exposes_no_final_clicked_coordinate() -> None:
    transition = SERVICE.compare(_boards()[-2], _boards()[-1])

    assert len([cell for cell in transition.cells if cell.became_disabled]) > 1
    assert not hasattr(transition, "clicked_coordinate")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("24-cell board", "exactly 25"),
        ("duplicate coordinate", "unique"),
        ("missing coordinate", "path"),
        ("out-of-range coordinate", "outside"),
    ],
)
def test_malformed_board_topology_is_rejected(mutation: str, message: str) -> None:
    snapshot = _synthetic_snapshot(24 if mutation == "24-cell board" else 25)
    if mutation == "duplicate coordinate":
        snapshot["components"][0]["components"][1]["path"] = [0, 0]
    elif mutation == "missing coordinate":
        snapshot["components"][0]["components"][0].pop("path")
    elif mutation == "out-of-range coordinate":
        snapshot["components"][0]["components"][0]["path"] = [5, 0]

    with pytest.raises(OuroHuntBoardProjectionError, match=message):
        SERVICE.project(snapshot)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("custom_id_sha256", None, "component_identity"),
        ("emoji", None, "visual identity"),
        ("disabled", "false", "disabled"),
    ],
)
def test_malformed_cell_identity_or_state_is_rejected(
    field: str, value: Any, message: str
) -> None:
    snapshot = _synthetic_snapshot()
    cell = snapshot["components"][0]["components"][0]
    if value is None:
        cell.pop(field)
    else:
        cell[field] = value

    with pytest.raises(OuroHuntBoardProjectionError, match=message):
        SERVICE.project(snapshot)


def test_malformed_visual_identity_hash_is_rejected() -> None:
    snapshot = _synthetic_snapshot()
    snapshot["components"][0]["components"][0]["emoji"]["name_sha256"] = "not-a-digest"

    with pytest.raises(OuroHuntBoardProjectionError, match="SHA-256"):
        SERVICE.project(snapshot)


def test_oc_fixture_discovers_and_projects_six_structural_boards() -> None:
    boards = _oc_boards()

    assert len(boards) == 6
    assert all(len(board.cells) == 25 for board in boards)
    assert all(board.coordinates == tuple(sorted(EXPECTED_COORDINATES)) for board in boards)
    assert all(not board.is_terminal for board in boards[:-1])
    assert boards[-1].is_terminal
    identities = {cell.coordinate: cell.component_identity for cell in boards[0].cells}
    assert all(
        {cell.coordinate: cell.component_identity for cell in board.cells} == identities
        for board in boards[1:]
    )


def test_oc_fixture_compare_represents_nonterminal_action_transition() -> None:
    before, after = _oc_boards()[:2]
    transition = SERVICE.compare(before, after)
    changed = [cell for cell in transition.cells if cell.visual_changed or cell.disabled_changed]

    assert len(changed) == 1
    assert changed[0].visual_changed
    assert changed[0].disabled_before is False
    assert changed[0].disabled_after is True
