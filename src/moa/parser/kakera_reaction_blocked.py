"""Parse Mudae's Kakera reaction-blocked responses."""

from collections.abc import Callable
import re

from moa.models.character import KakeraReactionBlocked
from moa.parser.primitives import normalize_custom_emojis


class KakeraReactionBlockedParser:
    """Parse the account and cooldown from a blocked Kakera reaction."""

    _BLOCKED = re.compile(
        r"^(?P<account>.+?),\s*You can't react to kakera for\s*"
        r"(?P<duration>.+?)\.\s*\(\$ku\)$",
        re.IGNORECASE,
    )
    _ERROR_MESSAGE = "Expected a compact Mudae Kakera reaction-blocked response."

    def __init__(
        self,
        error_type: type[ValueError],
        duration_converter: Callable[[str], int],
    ) -> None:
        self._error_type = error_type
        self._duration_converter = duration_converter

    @staticmethod
    def _lines(text: str) -> list[str]:
        normalized = normalize_custom_emojis(text)
        return [line.strip().replace("\u200b", "") for line in normalized.splitlines() if line.strip()]

    def parse(self, text: str) -> KakeraReactionBlocked:
        """Parse one compact account-prefixed Kakera cooldown response."""
        for line in self._lines(text):
            normalized = re.sub(r"\*+", "", line).strip()
            match = self._BLOCKED.match(normalized)
            if match is None:
                continue
            account_name = match.group("account").strip()
            if account_name:
                return KakeraReactionBlocked(
                    account_name=account_name,
                    cooldown_minutes=self._duration_converter(match.group("duration")),
                )
        raise self._error_type(self._ERROR_MESSAGE)
