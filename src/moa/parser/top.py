"""Parse copied text from Mudae top-ranking pages."""

import re

from moa.models.character import RankedCharacter, TopPage
from moa.parser.primitives import comma_int, normalize_custom_emojis


class TopParser:
    """Parse the supported response variants for the Mudae ``$top`` family."""

    _TOP_HEADER = re.compile(r"\bTOP\s+(?P<limit>[\d,]+)\b", re.IGNORECASE)
    _TOP_ENTRY = re.compile(
        r"^#(?P<rank>[\d,]+)\s+-\s+(?P<name>.+?)"
        r"(?:\s*=>\s*(?P<owner>.+?))?\s+-\s+(?P<series>.+)$"
    )
    _PAGE = re.compile(r"^Page\s+(?P<page>\d+)\s*/\s*(?P<pages>\d+)$", re.IGNORECASE)
    _HEART = re.compile(r"(?:\U0001f49e|:heart:)", re.IGNORECASE)

    def __init__(self, error_type: type[ValueError]) -> None:
        self._error_type = error_type

    @staticmethod
    def _lines(text: str) -> list[str]:
        normalized = normalize_custom_emojis(text)
        return [
            line
            for raw_line in normalized.splitlines()
            if (line := raw_line.replace("\u200b", " ").strip())
        ]

    @staticmethod
    def _number(value: str) -> int:
        return comma_int(value)

    def parse(self, text: str) -> TopPage:
        """Parse one copied ``$top`` or ``$topo`` page."""
        lines = self._lines(text)
        header = next(
            (self._TOP_HEADER.search(line) for line in lines if self._TOP_HEADER.search(line)),
            None,
        )
        page = next((self._PAGE.match(line) for line in lines if self._PAGE.match(line)), None)

        characters: list[RankedCharacter] = []
        for line in lines:
            entry = self._TOP_ENTRY.match(line)
            if entry is None:
                continue
            characters.append(
                RankedCharacter(
                    name=self._HEART.sub("", entry.group("name")).strip(),
                    series=entry.group("series").strip(),
                    claim_rank=self._number(entry.group("rank")),
                    owner_name=entry.group("owner").strip() if entry.group("owner") else None,
                )
            )

        if not characters:
            raise self._error_type("No ranked characters found in the Mudae $top output.")

        return TopPage(
            limit=self._number(header.group("limit")) if header else None,
            page_number=int(page.group("page")) if page else None,
            page_count=int(page.group("pages")) if page else None,
            characters=tuple(characters),
        )
