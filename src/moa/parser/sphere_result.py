"""Parse copied sphere-result responses from Mudae."""

from collections.abc import Callable
import re

from moa.models.character import SphereGain, SphereResultSnapshot


class SphereResultParser:
    """Parse the payout and stock summary from one copied ``$oq`` response."""

    _SPHERE_CLICKS = re.compile(
        r"You can click\s+(?P<clicks>\d+)\s+times.*?\((?P<minutes>\d+)\s+minutes?\)",
        re.IGNORECASE,
    )
    _SPHERE_GOAL = re.compile(
        r"Find\s+(?P<target>\d+)\s+purple spheres?\s+\(out of\s+(?P<total>\d+)\)",
        re.IGNORECASE,
    )
    _SPHERE_GAIN = re.compile(
        r"^:(?P<marker>sp[a-z0-9_]*):\s*(?P<free>\(Free\)\s*)?"
        r"\+(?P<amount>[\d,]+)(?:\s+\(Stock:\s*(?P<stock>[\d,]+)\))?$",
        re.IGNORECASE,
    )
    _ERROR_MESSAGE = "Expected a Mudae $oq response with sphere gains."

    def __init__(
        self,
        error_type: type[ValueError],
        lines_converter: Callable[[str], list[str]],
        number_converter: Callable[[str], int],
    ) -> None:
        self._error_type = error_type
        self._lines = lines_converter
        self._number = number_converter

    def parse(self, text: str) -> SphereResultSnapshot:
        """Parse one copied Mudae ``$oq`` response."""
        lines = self._lines(text)
        clicks = next(
            (match for line in lines if (match := self._SPHERE_CLICKS.search(line))),
            None,
        )
        goal = next(
            (match for line in lines if (match := self._SPHERE_GOAL.search(line))),
            None,
        )
        gains: list[SphereGain] = []
        total_gained: int | None = None
        stock: int | None = None
        for line in lines:
            match = self._SPHERE_GAIN.match(line)
            if match is None:
                continue
            amount = self._number(match.group("amount"))
            marker = match.group("marker").casefold()
            if marker == "sp":
                total_gained = amount
            else:
                gains.append(
                    SphereGain(
                        sphere_type=marker.removeprefix("sp"),
                        amount=amount,
                        is_free=match.group("free") is not None,
                    )
                )
            if match.group("stock") is not None:
                stock = self._number(match.group("stock"))

        if total_gained is None and not gains:
            raise self._error_type(self._ERROR_MESSAGE)
        return SphereResultSnapshot(
            clicks_available=int(clicks.group("clicks")) if clicks else None,
            click_window_minutes=int(clicks.group("minutes")) if clicks else None,
            purple_target=int(goal.group("target")) if goal else None,
            purple_total=int(goal.group("total")) if goal else None,
            gains=tuple(gains),
            total_gained=(
                total_gained
                if total_gained is not None
                else sum(gain.amount for gain in gains)
            ),
            stock=stock,
        )
