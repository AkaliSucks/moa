from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

import pytest

from moa.models.ourochest import OurochestObservation, OurochestSphere
from moa.services.ourochest_candidate_service import (
    OurochestCandidateService,
    UnsupportedOurochestSemanticError,
    initial_red_candidates,
    reduce_red_candidates,
)


ANNOTATION_PATH = Path(__file__).parent / "fixtures" / "discord" / "oc_characterization.v1.json"
ALL_COORDINATES = {(row, column) for row in range(5) for column in range(5)}
SERVICE = OurochestCandidateService()


def _observation(coordinate: tuple[int, int], sphere: OurochestSphere) -> OurochestObservation:
    return OurochestObservation(coordinate=coordinate, sphere=sphere)


def _human_coordinate(value: str) -> tuple[int, int]:
    return int(value[1]) - 1, int(value[3]) - 1


def _annotation() -> dict[str, object]:
    return json.loads(ANNOTATION_PATH.read_text(encoding="utf-8"))


def test_initial_red_universe_is_immutable_complete_and_excludes_center() -> None:
    candidates = initial_red_candidates()

    assert isinstance(candidates, tuple)
    assert len(candidates) == 24
    assert set(candidates) == ALL_COORDINATES - {(2, 2)}
    assert candidates == tuple(sorted(candidates))


def test_red_observation_keeps_only_possible_coordinate() -> None:
    candidates = initial_red_candidates()

    assert reduce_red_candidates(candidates, _observation((4, 1), OurochestSphere.RED)) == ((4, 1),)
    assert reduce_red_candidates(candidates, _observation((2, 2), OurochestSphere.RED)) == ()


@pytest.mark.parametrize(
    ("observation", "retained", "removed"),
    [
        (
            _observation((3, 3), OurochestSphere.BLUE),
            {(0, 1), (0, 2), (0, 4), (1, 0), (1, 2), (1, 4), (2, 0), (2, 1), (4, 0), (4, 1)},
            {(3, 3), (3, 0), (0, 3), (0, 0), (4, 4), (1, 1), (1, 3), (2, 4), (4, 2)},
        ),
        (
            _observation((0, 0), OurochestSphere.BLUE),
            {
                (1, 2),
                (1, 3),
                (1, 4),
                (2, 1),
                (2, 3),
                (2, 4),
                (3, 1),
                (3, 2),
                (3, 4),
                (4, 1),
                (4, 2),
                (4, 3),
            },
            {(0, 0), (0, 1), (1, 0), (1, 1), (2, 2), (3, 3), (4, 4)},
        ),
    ],
)
def test_blue_removes_row_column_and_both_diagonals(
    observation: OurochestObservation,
    retained: set[tuple[int, int]],
    removed: set[tuple[int, int]],
) -> None:
    result = set(reduce_red_candidates(initial_red_candidates(), observation))

    assert retained <= result
    assert not result & removed
    assert observation.coordinate not in result


def test_green_retains_same_row_or_column_but_not_observed_cell() -> None:
    candidates = tuple(sorted(ALL_COORDINATES))
    result = set(
        reduce_red_candidates(candidates, _observation((1, 1), OurochestSphere.GREEN))
    )

    assert result == {
        (1, 0),
        (1, 2),
        (1, 3),
        (1, 4),
        (0, 1),
        (2, 1),
        (3, 1),
        (4, 1),
    }
    assert (0, 0) not in result
    assert (1, 1) not in result


def test_yellow_retains_long_and_immediate_diagonals_only() -> None:
    result = set(
        reduce_red_candidates(
            tuple(sorted(ALL_COORDINATES)), _observation((2, 2), OurochestSphere.YELLOW)
        )
    )

    assert result == {
        (0, 0),
        (0, 4),
        (1, 1),
        (1, 3),
        (3, 1),
        (3, 3),
        (4, 0),
        (4, 4),
    }
    assert (2, 0) not in result
    assert (0, 2) not in result
    assert (2, 2) not in result


def test_teal_retains_row_column_or_diagonal_but_not_observed_cell() -> None:
    result = set(
        reduce_red_candidates(
            tuple(sorted(ALL_COORDINATES)), _observation((1, 1), OurochestSphere.TEAL)
        )
    )

    assert result == {
        (1, 0),
        (1, 2),
        (1, 3),
        (1, 4),
        (0, 1),
        (2, 1),
        (3, 1),
        (4, 1),
        (0, 0),
        (2, 2),
        (3, 3),
        (4, 4),
        (0, 2),
        (2, 0),
    }
    assert (0, 4) not in result
    assert (1, 1) not in result


@pytest.mark.parametrize("sphere", [OurochestSphere.ORANGE, OurochestSphere.UNKNOWN])
def test_unsupported_semantics_fail_closed_without_mutating_input(
    sphere: OurochestSphere,
) -> None:
    candidates = initial_red_candidates()

    with pytest.raises(UnsupportedOurochestSemanticError, match="unresolved"):
        reduce_red_candidates(candidates, _observation((0, 0), sphere))

    assert candidates == initial_red_candidates()


@pytest.mark.parametrize(
    "sphere",
    [
        OurochestSphere.BLUE,
        OurochestSphere.GREEN,
        OurochestSphere.YELLOW,
        OurochestSphere.TEAL,
    ],
)
def test_supported_reductions_are_monotonic_subsets(sphere: OurochestSphere) -> None:
    candidates = initial_red_candidates()
    result = reduce_red_candidates(candidates, _observation((0, 0), sphere))

    assert set(result) <= set(candidates)
    assert candidates == initial_red_candidates()


def test_supported_contradiction_can_produce_empty_candidates() -> None:
    candidates = ((0, 0),)

    assert reduce_red_candidates(
        candidates, _observation((1, 1), OurochestSphere.GREEN)
    ) == ()


def test_capture_one_candidate_regression_uses_only_grounded_first_three_observations() -> None:
    reasoning = _annotation()["candidate_reasoning"]
    assert reasoning["initial"]["count"] == 24

    candidates = SERVICE.initial_red_candidates()
    candidates = SERVICE.reduce_red_candidates(
        candidates, _observation(_human_coordinate("R4C4"), OurochestSphere.BLUE)
    )
    assert len(candidates) == 10

    candidates = SERVICE.reduce_red_candidates(
        candidates, _observation(_human_coordinate("R2C2"), OurochestSphere.GREEN)
    )
    assert candidates == tuple(
        sorted(_human_coordinate(value) for value in reasoning["after_R2C2_Green"])
    )

    candidates = SERVICE.reduce_red_candidates(
        candidates, _observation(_human_coordinate("R2C1"), OurochestSphere.BLUE)
    )
    assert candidates == (_human_coordinate("R5C2"),)

    candidates = SERVICE.reduce_red_candidates(
        candidates, _observation(_human_coordinate("R5C2"), OurochestSphere.RED)
    )
    assert candidates == (_human_coordinate("R5C2"),)

    assert reasoning["after_R2C1_Blue"] == ["R5C2"]


def test_observation_api_contains_only_coordinate_and_semantic() -> None:
    observation = _observation((0, 0), OurochestSphere.BLUE)

    assert {field.name for field in fields(observation)} == {"coordinate", "sphere"}
    assert observation.coordinate == (0, 0)
    assert observation.sphere is OurochestSphere.BLUE
    assert not hasattr(observation, "reward")
    assert not hasattr(observation, "message_id")
    assert not hasattr(observation, "disabled")
