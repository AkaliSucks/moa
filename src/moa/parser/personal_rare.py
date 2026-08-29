"""Parse copied personal rarity responses from Mudae."""

from collections.abc import Callable
import re

from moa.models.character import PersonalRareSnapshot


class PersonalRareParser:
    """Parse the account-scoped ``$personalrare`` value from a copied reply."""

    _PERSONAL_RARE = re.compile(
        r"(?:Your\s+)?current\s+\$personalrare:\s*(?P<value>\d+)", re.IGNORECASE
    )

    def __init__(
        self,
        error_type: type[ValueError],
        lines_converter: Callable[[str], list[str]],
    ) -> None:
        self._error_type = error_type
        self._lines = lines_converter

    def parse(self, text: str) -> PersonalRareSnapshot:
        """Parse one copied Mudae ``$persr`` response."""
        lines = [re.sub(r"[*_]", "", line) for line in self._lines(text)]
        normalized_text = "\n".join(lines)
        match = self._PERSONAL_RARE.search(normalized_text)
        if match is None:
            raise self._error_type(
                "Expected a Mudae $persr response with a current $personalrare value."
            )
        return PersonalRareSnapshot(personal_rare_multiplier=int(match.group("value")))
