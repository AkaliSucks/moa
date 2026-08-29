"""Parse copied text from Mudae claim confirmations."""

import re

from moa.models.character import ClaimConfirmation
from moa.parser.primitives import normalize_custom_emojis


class ClaimParser:
    """Parse the supported response variant for a Mudae claim confirmation."""

    _CLAIM_CONFIRMATION = re.compile(
        r"^(?P<account>.+?)\s+and\s+(?P<character>.+?)\s+are now married!",
        re.IGNORECASE,
    )

    def __init__(self, error_type: type[ValueError]) -> None:
        self._error_type = error_type

    @staticmethod
    def _lines(text: str) -> list[str]:
        normalized = normalize_custom_emojis(text)
        return [line.strip().replace("\u200b", "") for line in normalized.splitlines() if line.strip()]

    def parse(self, text: str) -> ClaimConfirmation:
        """Parse the account and character from a marriage confirmation."""
        for line in self._lines(text):
            match = self._CLAIM_CONFIRMATION.match(line)
            if match is None:
                continue
            account_name = re.sub(r"^[^\w]+", "", match.group("account").replace("*", "")).strip()
            character_name = match.group("character").replace("*", "").strip()
            if account_name and character_name:
                return ClaimConfirmation(
                    account_name=account_name,
                    character_name=character_name,
                )
        raise self._error_type("Expected a Mudae claim confirmation.")
