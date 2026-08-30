"""Parse raw copied Mudapin inventory responses from Mudae."""

import re

from moa.models.character import MudapinSnapshot


class MudapinsParser:
    """Parse a copied Mudae ``$mp`` inventory without normalizing its text."""

    _MUDAPIN_MARKER = re.compile(r":(?:pin|logopin)\d+:", re.IGNORECASE)
    _NO_MUDAPINS = re.compile(
        r"No mudapins found!.*kakeraloots", re.IGNORECASE
    )
    _ERROR_MESSAGE = "Expected a Mudae `$mp` Mudapin inventory response."

    def __init__(self, error_type: type[ValueError]) -> None:
        self._error_type = error_type

    def parse(self, text: str) -> MudapinSnapshot:
        """Parse one raw copied Mudae Mudapin inventory response."""
        if self._NO_MUDAPINS.search(text):
            return MudapinSnapshot(pin_markers=())
        markers = tuple(match.group(0) for match in self._MUDAPIN_MARKER.finditer(text))
        if not markers:
            raise self._error_type(self._ERROR_MESSAGE)
        return MudapinSnapshot(pin_markers=markers)
