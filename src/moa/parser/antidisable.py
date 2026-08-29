"""Parse copied antidisable pages from Mudae."""

from collections.abc import Callable
import re

from moa.models.character import AntidisablePage


class AntidisablePageParser:
    """Parse one copied Mudae ``$adl`` page as a series-level list."""

    _ANTIDISABLE_HEADER = re.compile(
        r"Antidisablelist\s*\((?P<used>\d+)\s*/\s*(?P<capacity>\d+)\)",
        re.IGNORECASE,
    )
    _ANTIDISABLED_COUNT = re.compile(
        r"^(?P<count>[\d,]+)\s+antidisabled\s+characters$", re.IGNORECASE
    )
    _PAGE = re.compile(r"^Page\s+(?P<page>\d+)\s*/\s*(?P<pages>\d+)$", re.IGNORECASE)

    def __init__(
        self,
        error_type: type[ValueError],
        lines_converter: Callable[[str], list[str]],
        number_converter: Callable[[str], int],
    ) -> None:
        self._error_type = error_type
        self._lines = lines_converter
        self._number = number_converter

    def parse(self, text: str) -> AntidisablePage:
        """Parse one copied ``$adl`` page, preserving series order and evidence."""
        lines = self._lines(text)
        header = next(
            (
                self._ANTIDISABLE_HEADER.search(line)
                for line in lines
                if self._ANTIDISABLE_HEADER.search(line)
            ),
            None,
        )
        count = next(
            (
                self._ANTIDISABLED_COUNT.match(line)
                for line in lines
                if self._ANTIDISABLED_COUNT.match(line)
            ),
            None,
        )
        page = next((self._PAGE.match(line) for line in lines if self._PAGE.match(line)), None)
        if header is None:
            raise self._error_type("Expected a Mudae `$adl` header with antidisable slot counts.")

        header_line = header.group(0)
        series_names: list[str] = []
        for line in lines:
            if (
                header_line in line
                or self._ANTIDISABLED_COUNT.match(line)
                or self._PAGE.match(line)
            ):
                continue
            name = line.strip().strip("*").strip("【】").strip()
            if name:
                series_names.append(name)

        if not series_names:
            raise self._error_type("No antidisable series found in the Mudae `$adl` page.")
        return AntidisablePage(
            page_number=int(page.group("page")) if page else None,
            page_count=int(page.group("pages")) if page else None,
            slots_used=int(header.group("used")),
            slots_capacity=int(header.group("capacity")),
            antidisabled_character_count=(
                self._number(count.group("count")) if count is not None else None
            ),
            series_names=tuple(series_names),
        )
