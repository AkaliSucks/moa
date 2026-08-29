"""Parse copied unavailable-character pages from Mudae."""

from collections.abc import Callable
import re

from moa.models.character import UnavailableCharacter, UnavailableCharacterPage


class UnavailableCharacterPageParser:
    """Parse direct unavailable-character evidence from a copied ``$topx`` page."""

    _TOP_HEADER = re.compile(r"\bTOP\s+(?P<limit>[\d,]+)\b", re.IGNORECASE)
    _PAGE = re.compile(r"^Page\s+(?P<page>\d+)\s*/\s*(?P<pages>\d+)$", re.IGNORECASE)
    _UNAVAILABLE_ENTRY = re.compile(
        r"^#(?P<rank>[\d,]+)\s+-\s+"
        r"(?P<name>\S(?:.*?\S)?)\s+-\s+(?P<series>\S(?:.*?\S)?)"
        r"\s*🚫(?:\s*\((?P<reason>\S(?:[^)]*?\S)?)\))?$"
    )
    _ERROR_MESSAGE = "No unavailable characters found in the Mudae $topx output."

    def __init__(
        self,
        error_type: type[ValueError],
        lines_converter: Callable[[str], list[str]],
        number_converter: Callable[[str], int],
    ) -> None:
        self._error_type = error_type
        self._lines = lines_converter
        self._number = number_converter

    def parse(self, text: str) -> UnavailableCharacterPage:
        """Parse one copied ``$topx`` page, preserving only direct row evidence."""
        lines = self._lines(text)
        header = next(
            (self._TOP_HEADER.search(line) for line in lines if self._TOP_HEADER.search(line)),
            None,
        )
        page = next((self._PAGE.match(line) for line in lines if self._PAGE.match(line)), None)

        characters: list[UnavailableCharacter] = []
        for line in lines:
            entry = self._UNAVAILABLE_ENTRY.match(line)
            if entry is None:
                continue
            characters.append(
                UnavailableCharacter(
                    name=entry.group("name").removesuffix(" 💞").strip(),
                    series=entry.group("series").strip(),
                    claim_rank=self._number(entry.group("rank")),
                    reason=entry.group("reason"),
                )
            )

        if not characters:
            raise self._error_type(self._ERROR_MESSAGE)

        return UnavailableCharacterPage(
            limit=self._number(header.group("limit")) if header else None,
            page_number=int(page.group("page")) if page else None,
            page_count=int(page.group("pages")) if page else None,
            characters=tuple(characters),
        )
