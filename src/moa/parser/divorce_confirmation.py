"""Parse Mudae's response after a divorce is confirmed."""

import re

from moa.models.character import DivorceConfirmation
from moa.parser.primitives import comma_int, normalize_custom_emojis


class DivorceConfirmationParser:
    """Parse Mudae's completed-divorce response and optional refund."""

    _DIVORCE_COMPLETE = re.compile(
        r"^(?P<character>.+?)\s+and\s+(?P<account>.+?)\s+are now divorced\."
        r"(?:\s*\W*\s*\(\+(?P<value>[\d,]+)(?::kakera:|\s+kakera)?\))?\s*$",
        re.IGNORECASE,
    )

    def __init__(self, error_type: type[ValueError]) -> None:
        self._error_type = error_type

    @staticmethod
    def _lines(text: str) -> list[str]:
        normalized = normalize_custom_emojis(text)
        return [
            line.strip().replace("\u200b", "") for line in normalized.splitlines() if line.strip()
        ]

    def parse(self, text: str, expected_account: str | None = None) -> DivorceConfirmation:
        """Parse one completed-divorce response from copied Mudae output."""
        for raw_line in self._lines(text):
            line = re.sub(r"\*+", "", raw_line).strip()
            match = self._DIVORCE_COMPLETE.match(line)
            if match is None:
                continue
            account_name = re.sub(r"^[^\w]+|[^\w]+$", "", match.group("account")).strip()
            character_name = re.sub(r"^[^\w]+|[^\w]+$", "", match.group("character")).strip()
            if (
                expected_account is not None
                and account_name.casefold() != expected_account.casefold()
            ):
                continue
            if character_name and account_name:
                return DivorceConfirmation(
                    account_name=account_name,
                    character_name=character_name,
                    kakera_refund=(
                        comma_int(match.group("value")) if match.group("value") else None
                    ),
                )
        raise self._error_type("Expected a Mudae completed-divorce response.")
