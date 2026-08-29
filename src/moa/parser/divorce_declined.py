"""Validate Mudae's response when a pending divorce is declined."""

import re

from moa.parser.primitives import normalize_custom_emojis


class DivorceDeclinedValidator:
    """Validate Mudae's short divorce-declined response."""

    _DIVORCE_DECLINED = re.compile(r"^Divorce declined\.$", re.IGNORECASE)

    def __init__(self, error_type: type[ValueError]) -> None:
        self._error_type = error_type

    @staticmethod
    def _lines(text: str) -> list[str]:
        normalized = normalize_custom_emojis(text)
        return [
            line.strip().replace("\u200b", "") for line in normalized.splitlines() if line.strip()
        ]

    def parse(self, text: str) -> None:
        """Validate a copied Mudae divorce-declined response."""
        if any(self._DIVORCE_DECLINED.match(line) for line in self._lines(text)):
            return None
        raise self._error_type("Expected Mudae's divorce-declined response.")
