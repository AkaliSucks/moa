"""Parse copied Kakera balance and badge state from Mudae."""

from collections.abc import Callable
import re

from moa.models.character import BadgeLevel, KakeraStateSnapshot


class KakeraStateParser:
    """Parse the balance and badge levels shown by Mudae's ``$k`` response."""

    _BADGE_LEVEL = re.compile(
        r"(?P<name>Bronze|Silver|Gold|Sapphire|Ruby|Emerald|Diamond)\s+"
        r"(?P<level>I|II|III|IV)\s*[·\u00b7]\s*(?P<status>.+)$",
        re.IGNORECASE,
    )
    _ROMAN_LEVELS = {"I": 1, "II": 2, "III": 3, "IV": 4}

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

    def parse(self, text: str) -> KakeraStateSnapshot:
        """Parse one copied Mudae ``$k`` response."""
        lines = [re.sub(r"\*", "", line) for line in self._lines(text)]

        balance = None
        for line in lines:
            match = self._balance.match(line)
            if match is not None:
                balance = match
                break
        if balance is None:
            raise self._error_type(
                "Expected a Mudae $k response with a Kakera balance."
            )

        badges: list[BadgeLevel] = []
        for line in lines:
            match = self._BADGE_LEVEL.search(line)
            if match is None:
                continue
            badges.append(
                BadgeLevel(
                    badge_name=match.group("name").lower(),
                    level=self._ROMAN_LEVELS[match.group("level").upper()],
                    max_reached="max reached" in match.group("status").casefold(),
                )
            )
        if not badges:
            raise self._error_type(
                "No Kakera badge levels found in the Mudae $k output."
            )
        return KakeraStateSnapshot(
            kakera_balance=self._number(balance.group("value")),
            badges=tuple(badges),
        )
