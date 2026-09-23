"""Parse copied Kakera Tower state from Mudae."""

from collections.abc import Callable
import re

from moa.models.character import TowerStateSnapshot


class TowerStateParser:
    """Parse the tower level, upgrade cost, balance, and built perks from ``$kt``."""

    _TOWER_LEVEL = re.compile(
        r"current level is.*?tow(?P<level>\d+):?(?:.*?\(\+\s*(?P<towers>\d+)\s+towers?)?",
        re.IGNORECASE,
    )
    _TOWER_NEXT_COST = re.compile(
        r"next level costs\s+(?P<value>[\d,]+):kakera:", re.IGNORECASE
    )
    _TOWER_MARKDOWN_NUMBERS = (
        re.compile(
            r"(?P<prefix>\(\+\s+)\*\*(?P<value>[\d,]+)\*\*"
            r"(?P<suffix>\s+towers?\))",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?P<prefix>next level costs\s+)\*\*(?P<value>[\d,]+)\*\*"
            r"(?P<suffix>:kakera:)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?P<prefix>You have\s+)\*\*(?P<value>[\d,]+)\*\*"
            r"(?P<suffix>:kakera:)",
            re.IGNORECASE,
        ),
    )
    _TOWER_PERK = re.compile(r"^.*?\[(?P<id>\d+)\]")
    _ERROR_MESSAGE = (
        "Expected a Mudae $kt response with current level, next cost, and balance."
    )

    def __init__(
        self,
        error_type: type[ValueError],
        lines_converter: Callable[[str], list[str]],
        number_converter: Callable[[str], int],
        balance_pattern: re.Pattern[str],
    ) -> None:
        self._error_type = error_type
        self._lines = lines_converter
        self._number = number_converter
        self._balance = balance_pattern

    def parse(self, text: str) -> TowerStateSnapshot:
        """Parse one copied Mudae ``$kt`` response."""
        lines = [
            self._normalize_markdown_numbers(line)
            for line in self._lines(text)
        ]

        level = next(
            (match for line in lines if (match := self._TOWER_LEVEL.search(line))),
            None,
        )
        next_cost = next(
            (match for line in lines if (match := self._TOWER_NEXT_COST.search(line))),
            None,
        )
        balance = next(
            (match for line in lines if (match := self._balance.match(line))),
            None,
        )
        if level is None or next_cost is None or balance is None:
            raise self._error_type(self._ERROR_MESSAGE)

        built_perks: list[int] = []
        for line in lines:
            perk = self._TOWER_PERK.match(line)
            if perk is None or "☑" not in line:
                continue
            try:
                built_perks.append(int(perk.group("id")))
            except ValueError:
                continue

        return TowerStateSnapshot(
            current_level=int(level.group("level")),
            completed_towers=(
                int(level.group("towers")) if level.group("towers") else None
            ),
            next_level_cost=self._number(next_cost.group("value")),
            kakera_balance=self._number(balance.group("value")),
            built_perk_ids=tuple(built_perks),
        )

    @classmethod
    def _normalize_markdown_numbers(cls, line: str) -> str:
        for pattern in cls._TOWER_MARKDOWN_NUMBERS:
            line = pattern.sub(r"\g<prefix>\g<value>\g<suffix>", line)
        return line
