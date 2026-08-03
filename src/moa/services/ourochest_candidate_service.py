"""Pure Red-candidate reduction for resolved `$oc` sphere observations."""

from __future__ import annotations

from collections.abc import Iterable

from moa.models.ourochest import OurochestObservation, OurochestSphere
from moa.models.ourosphere import Coordinate, _require_coordinate


RedCandidates = tuple[Coordinate, ...]
_CENTER: Coordinate = (2, 2)


class UnsupportedOurochestSemanticError(ValueError):
    """Raised when a sphere has no grounded reducer geometry in this slice."""


def initial_red_candidates() -> RedCandidates:
    """Return every 5x5 coordinate except the board center, row-major ordered."""

    return tuple(
        (row, column)
        for row in range(5)
        for column in range(5)
        if (row, column) != _CENTER
    )


def reduce_red_candidates(
    candidates: Iterable[Coordinate], observation: OurochestObservation
) -> RedCandidates:
    """Apply one grounded sphere constraint without mutating the input."""

    if not isinstance(observation, OurochestObservation):
        raise TypeError("observation must be an OurochestObservation")
    normalized = _normalize_candidates(candidates)
    coordinate = observation.coordinate

    if observation.sphere is OurochestSphere.RED:
        return tuple(candidate for candidate in normalized if candidate == coordinate)
    if observation.sphere is OurochestSphere.ORANGE:
        raise UnsupportedOurochestSemanticError(
            "Orange geometry is unresolved; candidate reduction is unsupported"
        )
    if observation.sphere is OurochestSphere.UNKNOWN:
        raise UnsupportedOurochestSemanticError(
            "UNKNOWN sphere geometry is unresolved; candidate reduction is unsupported"
        )

    if observation.sphere is OurochestSphere.BLUE:
        return tuple(
            candidate
            for candidate in normalized
            if not _same_row(candidate, coordinate)
            and not _same_column(candidate, coordinate)
            and not _diagonal(candidate, coordinate)
        )
    if observation.sphere is OurochestSphere.GREEN:
        return tuple(
            candidate
            for candidate in normalized
            if candidate != coordinate
            and (_same_row(candidate, coordinate) or _same_column(candidate, coordinate))
        )
    if observation.sphere is OurochestSphere.YELLOW:
        return tuple(
            candidate
            for candidate in normalized
            if candidate != coordinate and _diagonal(candidate, coordinate)
        )
    if observation.sphere is OurochestSphere.TEAL:
        return tuple(
            candidate
            for candidate in normalized
            if candidate != coordinate
            and (
                _same_row(candidate, coordinate)
                or _same_column(candidate, coordinate)
                or _diagonal(candidate, coordinate)
            )
        )

    raise AssertionError(f"unhandled Ourochest sphere semantic: {observation.sphere!r}")


class OurochestCandidateService:
    """Stateless service facade for the pure `$oc` candidate operations."""

    @staticmethod
    def initial_red_candidates() -> RedCandidates:
        return initial_red_candidates()

    @staticmethod
    def reduce_red_candidates(
        candidates: Iterable[Coordinate], observation: OurochestObservation
    ) -> RedCandidates:
        return reduce_red_candidates(candidates, observation)


def _normalize_candidates(candidates: Iterable[Coordinate]) -> RedCandidates:
    if isinstance(candidates, (str, bytes, bytearray)):
        raise TypeError("candidates must be an iterable of coordinates")
    try:
        normalized = tuple(candidates)
    except TypeError as error:
        raise TypeError("candidates must be an iterable of coordinates") from error

    for candidate in normalized:
        _require_coordinate(candidate, "candidate")
    return tuple(sorted(set(normalized)))


def _same_row(left: Coordinate, right: Coordinate) -> bool:
    return left[0] == right[0]


def _same_column(left: Coordinate, right: Coordinate) -> bool:
    return left[1] == right[1]


def _diagonal(left: Coordinate, right: Coordinate) -> bool:
    return abs(left[0] - right[0]) == abs(left[1] - right[1])
