"""Parse copied keyed-harem pages from Mudae."""

from collections.abc import Callable
import re

from moa.models.character import HaremKeyEntry, HaremKeyPage


class HaremKeyParser:
    """Parse the supported response variants for a keyed-harem page."""

    _PAGE = re.compile(r"^Page\s+(?P<page>\d+)\s*/\s*(?P<pages>\d+)$", re.IGNORECASE)
    _HAREM_KEY_ENTRY = re.compile(
        r"^(?P<name>.+?)\s*[\u00b7\u2022]\s*:(?P<key_type>[a-z]+)key:\s*"
        r"\((?P<key_count>\d+)\)(?:\s+(?P<kakera_value>[\d,]+)\s+ka)?$",
        re.IGNORECASE,
    )
    _TOTAL_HAREM_VALUE = re.compile(
        r"^Total value:\s*(?P<value>[\d,]+)(?::kakera:|\s+ka)?$", re.IGNORECASE
    )
    _ERROR_MESSAGE = "No keyed harem entries found in the Mudae $mmy= output."

    def __init__(
        self,
        error_type: type[ValueError],
        lines_converter: Callable[[str], list[str]],
        number_converter: Callable[[str], int],
    ) -> None:
        self._error_type = error_type
        self._lines = lines_converter
        self._number = number_converter

    def parse(self, text: str) -> HaremKeyPage:
        """Parse one copied keyed-harem page, with optional current Kakera values."""
        lines = self._lines(text)
        page = next((self._PAGE.match(line) for line in lines if self._PAGE.match(line)), None)
        total = next(
            (
                self._TOTAL_HAREM_VALUE.match(line)
                for line in lines
                if self._TOTAL_HAREM_VALUE.match(line)
            ),
            None,
        )
        entries: list[HaremKeyEntry] = []

        for line in lines:
            entry = self._HAREM_KEY_ENTRY.match(line)
            if entry is None:
                continue
            entries.append(
                HaremKeyEntry(
                    name=entry.group("name").strip(),
                    key_type=entry.group("key_type").lower(),
                    key_count=int(entry.group("key_count")),
                    kakera_value=(
                        self._number(entry.group("kakera_value"))
                        if entry.group("kakera_value")
                        else None
                    ),
                )
            )

        if not entries:
            raise self._error_type(self._ERROR_MESSAGE)

        return HaremKeyPage(
            page_number=int(page.group("page")) if page else None,
            page_count=int(page.group("pages")) if page else None,
            entries=tuple(entries),
            total_harem_value=self._number(total.group("value")) if total else None,
        )
