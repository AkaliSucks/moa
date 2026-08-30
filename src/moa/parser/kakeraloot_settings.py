"""Parse copied Kakeraloot settings responses from Mudae."""

from collections.abc import Callable
import re

from moa.models.character import KakeralootSettingsSnapshot


class KakeralootSettingsParser:
    """Parse the server-scoped costs shown by ``$infokl``."""

    _LOOT_COST = re.compile(
        r"Each\s+\$kl\s+costs\s+(?P<value>[\d,]+)\s*:(?:kakera):",
        re.IGNORECASE,
    )
    _LOOT_UPGRADE_COST = re.compile(
        r"level\s+1\s+of\s+quantity\s+or\s+quality\s+costs\s+(?P<base>[\d,]+)\s*:(?:kakera):"
        r".*?increased\s+by\s+(?P<increment>[\d,]+)/level",
        re.IGNORECASE,
    )
    _ERROR_MESSAGE = "Expected a Mudae $infokl response with Kakeraloot cost details."

    def __init__(
        self,
        error_type: type[ValueError],
        lines_converter: Callable[[str], list[str]],
        number_converter: Callable[[str], int],
    ) -> None:
        self._error_type = error_type
        self._lines = lines_converter
        self._number = number_converter

    def parse(self, text: str) -> KakeralootSettingsSnapshot:
        """Parse one copied Mudae ``$infokl`` response."""
        normalized_text = "\n".join(
            re.sub(r"[*_]", "", line) for line in self._lines(text)
        )
        loot_cost = self._LOOT_COST.search(normalized_text)
        upgrade_cost = self._LOOT_UPGRADE_COST.search(normalized_text)
        if loot_cost is None or upgrade_cost is None:
            raise self._error_type(self._ERROR_MESSAGE)
        return KakeralootSettingsSnapshot(
            loot_cost=self._number(loot_cost.group("value")),
            quantity_quality_base_cost=self._number(upgrade_cost.group("base")),
            quantity_quality_level_increment=self._number(upgrade_cost.group("increment")),
        )
