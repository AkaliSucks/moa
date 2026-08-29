"""Parse Mudae's Kakera reaction receipts."""

import re

from moa.models.character import KakeraReactionReceipt
from moa.parser.primitives import comma_int, normalize_custom_emojis


class KakeraReactionReceiptParser:
    """Parse direct and breakdown receipts from a Kakera reaction."""

    _RECEIPT = re.compile(
        r"^(?P<reaction>:[a-z0-9_]+:|\S+)\s+(?:\(Free\)\s*)?\*{0,2}(?P<account>.+?)\s+"
        r"\+(?P<value>[\d,]+)\*{0,2}\s+\(\$k\)$",
        re.IGNORECASE,
    )
    _BREAKDOWN_RECEIPT = re.compile(
        r"^(?P<reaction>:[a-z0-9_]+:)\s+breaks down into.+?=>\s*"
        r"(?:\(Free\)\s*)?\*{0,2}(?P<account>.+?)\s+"
        r"\+(?P<value>[\d,]+)\*{0,2}\s+\(\$k\)$",
        re.IGNORECASE,
    )
    _ERROR_MESSAGE = (
        "Expected a Mudae Kakera reaction receipt such as `:kakeraY: user +497 ($k)`."
    )

    def __init__(self, error_type: type[ValueError]) -> None:
        self._error_type = error_type

    @staticmethod
    def _lines(text: str) -> list[str]:
        normalized = normalize_custom_emojis(text)
        return [
            line.strip().replace("\u200b", "")
            for line in normalized.splitlines()
            if line.strip()
        ]

    def parse(self, text: str) -> KakeraReactionReceipt:
        """Parse one standalone direct or breakdown Kakera receipt."""
        for line in self._lines(text):
            for pattern in (self._BREAKDOWN_RECEIPT, self._RECEIPT):
                match = pattern.match(line)
                if match is None:
                    continue
                return KakeraReactionReceipt(
                    reaction_label=match.group("reaction"),
                    account_name=match.group("account").strip(),
                    kakera_earned=comma_int(match.group("value")),
                )
        raise self._error_type(self._ERROR_MESSAGE)
