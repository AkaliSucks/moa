"""Parse copied wishlist responses from Mudae."""

from collections.abc import Callable
import re

from moa.models.character import WishlistEntry, WishlistSnapshot


class WishlistParser:
    """Parse wishlist and Starwish state from a copied Mudae ``$wl`` response."""

    _WISHLIST_HEADER = re.compile(
        r"Wishlist\s*-\s*(?P<wishlist_count>\d+)\s*/\s*(?P<wishlist_capacity>\d+)\s*\$wl,\s*"
        r"(?P<starwish_count>\d+)\s*/\s*(?P<starwish_capacity>\d+)\s*\$sw",
        re.IGNORECASE,
    )

    def __init__(
        self,
        error_type: type[ValueError],
        lines_converter: Callable[[str], list[str]],
    ) -> None:
        self._error_type = error_type
        self._lines = lines_converter

    def parse(self, text: str) -> WishlistSnapshot:
        """Parse one wishlist response, preserving entry order and marker evidence."""
        lines = self._lines(text)
        header = next(
            (self._WISHLIST_HEADER.search(line) for line in lines if self._WISHLIST_HEADER.search(line)),
            None,
        )
        if header is None:
            raise self._error_type("Expected a Mudae $wl header with $wl and $sw capacities.")

        entries: list[WishlistEntry] = []
        header_line = header.group(0)
        for line in lines:
            if header_line in line:
                continue
            name = (
                line.replace("✅", "")
                .replace("⭐", "")
                .replace(":kakera:", "")
                .strip()
                .strip("*")
                .strip()
            )
            if not name:
                continue
            entries.append(
                WishlistEntry(
                    name=name,
                    is_starwish="⭐" in line,
                    is_owned_marker_present="✅" in line,
                    kakera_marker_present=":kakera:" in line,
                )
            )

        if not entries:
            raise self._error_type("No wishlist entries found in the Mudae $wl output.")
        return WishlistSnapshot(
            wishlist_count=int(header.group("wishlist_count")),
            wishlist_capacity=int(header.group("wishlist_capacity")),
            starwish_count=int(header.group("starwish_count")),
            starwish_capacity=int(header.group("starwish_capacity")),
            entries=tuple(entries),
        )
