"""Parse copied ranked-harem pages from Mudae."""

from collections.abc import Callable
import re

from moa.models.character import RankedHaremEntry, RankedHaremPage


class RankedHaremParser:
    """Parse the supported response variants for a ranked-harem page."""

    _PAGE = re.compile(r"^Page\s+(?P<page>\d+)\s*/\s*(?P<pages>\d+)$", re.IGNORECASE)
    _RANKED_HAREM_ENTRY = re.compile(
        r"^#(?P<rank>[\d,]+)\s+-\s+(?P<name>.+?)"
        r"(?:\s*[\u00b7\u2022]\s*\((?P<roulette_types>\$?[a-z]+(?:\s*,\s*\$?[a-z]+)*)\))?"
        r"(?:\s*(?:[-\u00b7\u2022]\s*)?:(?P<key_type>[a-z]+)key:\s*"
        r"\(\*{0,2}(?P<key_count>\d+)\*{0,2}\))?"
        r"(?:\s+(?P<kakera_value>[\d,]+)\s+ka)?$",
        re.IGNORECASE,
    )
    _HEADER_ERROR = "Expected a Mudae ranked harem header."
    _ENTRIES_ERROR = "No ranked harem entries found in the Mudae `$mmr` output."

    def __init__(
        self,
        error_type: type[ValueError],
        lines_converter: Callable[[str], list[str]],
        number_converter: Callable[[str], int],
    ) -> None:
        self._error_type = error_type
        self._lines = lines_converter
        self._number = number_converter

    def parse(self, text: str) -> RankedHaremPage:
        """Parse one copied ranked harem page, with optional current values."""
        lines = self._lines(text)
        if not any("harem" in line.casefold() for line in lines):
            raise self._error_type(self._HEADER_ERROR)

        page = next((self._PAGE.match(line) for line in lines if self._PAGE.match(line)), None)
        entries: list[RankedHaremEntry] = []
        for line in lines:
            # Mudae may wrap the rank, name, and/or value in Discord markdown
            # emphasis. The markdown is presentation-only and should not make
            # otherwise valid $mmr/$mmrk entries fail the structured parser.
            match = self._RANKED_HAREM_ENTRY.match(re.sub(r"\*+", "", line))
            if match is None:
                continue
            entries.append(
                RankedHaremEntry(
                    name=match.group("name").strip(),
                    claim_rank=self._number(match.group("rank")),
                    kakera_value=(
                        self._number(match.group("kakera_value"))
                        if match.group("kakera_value")
                        else None
                    ),
                    roulette_types=(
                        tuple(
                            token.strip().removeprefix("$").lower()
                            for token in match.group("roulette_types").split(",")
                            if token.strip()
                        )
                        if match.group("roulette_types") is not None
                        else None
                    ),
                    key_type=(match.group("key_type") or "").lower() or None,
                    key_count=(
                        int(match.group("key_count")) if match.group("key_count") else None
                    ),
                )
            )

        if not entries:
            raise self._error_type(self._ENTRIES_ERROR)
        return RankedHaremPage(
            page_number=int(page.group("page")) if page else None,
            page_count=int(page.group("pages")) if page else None,
            entries=tuple(entries),
        )
