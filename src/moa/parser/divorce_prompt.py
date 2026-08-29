"""Parse the first response from Mudae's two-step ``$divorce`` flow."""

import re

from moa.models.character import DivorcePrompt
from moa.parser.primitives import comma_int, normalize_custom_emojis


class DivorcePromptParser:
    """Parse Mudae's divorce confirmation prompt and optional refund."""

    _DIVORCE_PROMPT = re.compile(
        r"^(?P<character>.+?):\s*Do you confirm the divorce\?\s*\(y/n/yes/no\)\s*$",
        re.IGNORECASE,
    )
    _DIVORCE_REFUND = re.compile(
        r"Characters divorced by \$divorce are also removed from the \$restorelist\s*"
        r"\(\+(?P<value>[\d,]+)(?::kakera:|\s+kakera)?\s*if you confirm\)",
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

    def parse(self, text: str) -> DivorcePrompt:
        """Parse the character and optional Kakera refund from a divorce prompt."""
        lines = self._lines(text)
        prompt = next(
            (
                match
                for line in lines
                for match in [self._DIVORCE_PROMPT.match(re.sub(r"\*+", "", line).strip())]
                if match is not None
            ),
            None,
        )
        if prompt is None:
            raise self._error_type("Expected a Mudae divorce confirmation prompt.")
        refund = next(
            (
                match
                for line in lines
                for match in [self._DIVORCE_REFUND.match(re.sub(r"\*+", "", line).strip())]
                if match is not None
            ),
            None,
        )
        return DivorcePrompt(
            character_name=prompt.group("character").strip(),
            kakera_refund=comma_int(refund.group("value")) if refund else None,
        )
