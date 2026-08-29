"""Parse copied disable-list responses from Mudae."""

from collections.abc import Callable
import re

from moa.models.character import DisableListEntry, DisableListSnapshot


class DisableListParser:
    """Parse account-specific roll-pool settings from a copied ``$dl`` reply."""

    _DISABLELIST_HEADER = re.compile(
        r"Disablelist\s*\((?P<used>\d+)\s*/\s*(?P<capacity>\d+)\)", re.IGNORECASE
    )
    _DISABLELIST_TOTALS = re.compile(
        r"(?P<total>[\d,]+)\s+disabled.*?(?P<wa>[\d,]+)\s*\$wa.*?"
        r"(?P<ha>[\d,]+)\s*\$ha.*?(?P<wg>[\d,]+)\s*\$wg.*?"
        r"(?P<hg>[\d,]+)\s*\$hg",
        re.IGNORECASE,
    )
    _POOL_LIMIT = re.compile(
        r"Pool limit reached:\s*(?P<limit>[\d,]+)\s+\$(?P<roulette>wa|ha|wg|hg)",
        re.IGNORECASE,
    )
    _DISABLELIST_ENTRY = re.compile(r"^(?P<name>.+?)\s*\((?P<count>[\d,]+)\)$")

    def __init__(
        self,
        error_type: type[ValueError],
        lines_converter: Callable[[str], list[str]],
        number_converter: Callable[[str], int],
    ) -> None:
        self._error_type = error_type
        self._lines = lines_converter
        self._number = number_converter

    def parse(self, text: str) -> DisableListSnapshot:
        """Parse one copied ``$dl`` response, preserving factual evidence and order."""
        lines = self._lines(text)
        header = next(
            (
                self._DISABLELIST_HEADER.search(line)
                for line in lines
                if self._DISABLELIST_HEADER.search(line)
            ),
            None,
        )
        totals = self._DISABLELIST_TOTALS.search(" ".join(lines))
        if header is None or totals is None:
            raise self._error_type("Expected a Mudae $dl header and disabled-pool totals.")

        limits: dict[str, int] = {}
        entries: list[DisableListEntry] = []
        for line in lines:
            pool_limit = self._POOL_LIMIT.search(line)
            if pool_limit is not None:
                limits[pool_limit.group("roulette").lower()] = self._number(pool_limit.group("limit"))
                continue
            entry = self._DISABLELIST_ENTRY.match(line)
            if entry is None:
                continue
            entries.append(
                DisableListEntry(
                    name=entry.group("name").strip(),
                    disabled_count=self._number(entry.group("count")),
                )
            )

        return DisableListSnapshot(
            slots_used=int(header.group("used")),
            slots_capacity=int(header.group("capacity")),
            total_disabled=self._number(totals.group("total")),
            disabled_wa=self._number(totals.group("wa")),
            disabled_ha=self._number(totals.group("ha")),
            disabled_wg=self._number(totals.group("wg")),
            disabled_hg=self._number(totals.group("hg")),
            wa_pool_limit=limits.get("wa"),
            ha_pool_limit=limits.get("ha"),
            western_disabled=(
                True
                if any(
                    "western animanga series are completely disabled" in line.casefold()
                    for line in lines
                )
                else None
            ),
            irl_disabled=(
                True
                if any(
                    "irl series are completely disabled" in line.casefold()
                    for line in lines
                )
                else None
            ),
            entries=tuple(entries),
        )
