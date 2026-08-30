"""Parse copied account profile responses from Mudae."""

from collections.abc import Callable
import re

from moa.models.character import ProfileSnapshot


class ProfileParser:
    """Parse account progress totals from a copied Mudae ``$profile`` response."""

    _COLLECTION = re.compile(
        r"Collection size:\s*(?P<size>[\d,]+)\s*"
        r"\((?P<female>\d+)%\s*:female:\s*"
        r"(?P<male>\d+)%\s*:male:\s*\)",
        re.IGNORECASE,
    )
    _POKEDEX = re.compile(
        r"Pok(?:\u00e9|e)dex:\s*(?P<count>[\d,]+)\s+Pok(?:\u00e9|e)mon(?P<items>.*)$",
        re.IGNORECASE,
    )
    _POKEDEX_FALLBACK = re.compile(
        r"Pok.*?dex:\s*(?P<count>[\d,]+)\s+Pok.*?mon(?P<items>.*)$",
        re.IGNORECASE,
    )
    _MUDAPINS = re.compile(
        r"Mudapins:\s*(?P<collected>[\d,]+)\s*/\s*(?P<total>[\d,]+)",
        re.IGNORECASE,
    )
    _KAKERA_BALANCE = re.compile(
        r"^(?P<value>[\d,]+)\s*:kakera:\s*$", re.IGNORECASE
    )
    _KAKERA_BALANCE_LINE = re.compile(
        r"^[\d,]+\s*:kakera:", re.IGNORECASE
    )
    _SPHERE_STOCK = re.compile(
        r"^(?P<value>[\d,]+)\s*:sp:\s*$", re.IGNORECASE
    )
    _SPHERE_STOCK_LINE = re.compile(r"^[\d,]+\s*:sp:\s*$", re.IGNORECASE)
    _BADGE_MARKERS = (":bronzeiv:", ":silveriv:", ":diamondiv:", ":diamondi:")
    _COMPLETE_ERROR = "Expected a complete Mudae $profile response with account totals."
    _REACTIONS_ERROR = "Expected a Mudae $profile reactions section with reaction counts."

    def __init__(
        self,
        error_type: type[ValueError],
        lines_converter: Callable[[str], list[str]],
        number_converter: Callable[[str], int],
    ) -> None:
        self._error_type = error_type
        self._lines = lines_converter
        self._number = number_converter

    @staticmethod
    def _marker_counts(
        line: str, prefix: str, number_converter: Callable[[str], int]
    ) -> dict[str, int]:
        return {
            f":{marker}:": number_converter(value)
            for value, marker in re.findall(
                rf"([\d,]+)\s*x\s*:({prefix}[A-Za-z0-9_]*)\s*:",
                line,
                re.IGNORECASE,
            )
        }

    def parse(self, text: str) -> ProfileSnapshot:
        """Parse one copied Mudae ``$profile`` response."""
        lines = [re.sub(r"\*", "", line) for line in self._lines(text)]

        collection = next(
            (
                self._COLLECTION.search(line)
                for line in lines
                if "collection size:" in line.casefold()
            ),
            None,
        )
        pokedex = next(
            (
                self._POKEDEX.search(line)
                for line in lines
                if "dex:" in line.casefold()
            ),
            None,
        )
        if pokedex is None:
            pokedex = next(
                (
                    self._POKEDEX_FALLBACK.search(line)
                    for line in lines
                    if "dex:" in line.casefold()
                ),
                None,
            )
        mudapins = next(
            (
                self._MUDAPINS.search(line)
                for line in lines
                if line.casefold().startswith("mudapins:")
            ),
            None,
        )
        kakera_balance = next(
            (
                self._KAKERA_BALANCE.match(line)
                for line in lines
                if self._KAKERA_BALANCE_LINE.match(line)
            ),
            None,
        )
        keys_line = next(
            (line for line in lines if line.casefold().startswith("keys:")),
            None,
        )
        key_counts = {
            marker.casefold(): self._number(value)
            for value, marker in re.findall(
                r"([\d,]+)\s*:([a-z]+key):", keys_line or "", re.IGNORECASE
            )
        }
        sphere_stock = next(
            (
                self._SPHERE_STOCK.match(line)
                for line in lines
                if self._SPHERE_STOCK_LINE.match(line)
            ),
            None,
        )
        if collection is None:
            raise self._error_type(self._COMPLETE_ERROR)

        reacts_index = next(
            (
                index
                for index, line in enumerate(lines)
                if line.casefold() == "reacts:"
            ),
            None,
        )
        reactions_observed = reacts_index is not None
        reacts = None
        if reacts_index is not None:
            if reacts_index + 1 >= len(lines):
                raise self._error_type(self._REACTIONS_ERROR)
            reacts = self._marker_counts(lines[reacts_index + 1], "kakera", self._number)
            if not reacts:
                raise self._error_type(self._REACTIONS_ERROR)

        sphere_index = next(
            (
                index
                for index, line in enumerate(lines)
                if self._SPHERE_STOCK_LINE.match(line)
            ),
            None,
        )
        spheres = None
        if sphere_index is not None and sphere_index + 1 < len(lines):
            parsed_spheres = self._marker_counts(
                lines[sphere_index + 1], "sp", self._number
            )
            spheres = parsed_spheres or None

        badge_line = next(
            (
                line
                for line in reversed(lines)
                if any(marker in line.casefold() for marker in self._BADGE_MARKERS)
            ),
            None,
        )
        displayed_badges = (
            tuple(
                f":{marker}:"
                for marker in re.findall(r":([A-Za-z0-9_]+):", badge_line)
            )
            if badge_line is not None
            else None
        )

        return ProfileSnapshot(
            profile_name=lines[0],
            collection_size=self._number(collection.group("size")),
            female_percent=int(collection.group("female")),
            male_percent=int(collection.group("male")),
            pokedex_count=self._number(pokedex.group("count")) if pokedex else None,
            pokedex_pokemon=(
                tuple(re.findall(r":([A-Za-z0-9_]+):", pokedex.group("items")))
                if pokedex
                else None
            ),
            kakera_reacts=reacts,
            mudapins_collected=(
                self._number(mudapins.group("collected")) if mudapins else None
            ),
            mudapins_total=(
                self._number(mudapins.group("total")) if mudapins else None
            ),
            kakera_balance=(
                self._number(kakera_balance.group("value"))
                if kakera_balance
                else None
            ),
            bronze_keys=key_counts.get("bronzekey"),
            silver_keys=key_counts.get("silverkey"),
            gold_keys=key_counts.get("goldkey"),
            sphere_stock=(
                self._number(sphere_stock.group("value")) if sphere_stock else None
            ),
            spheres=spheres,
            displayed_badges=displayed_badges,
            pokedex_observed=pokedex is not None,
            reactions_observed=reactions_observed,
            mudapins_observed=mudapins is not None,
            kakera_balance_observed=kakera_balance is not None,
            keys_observed=keys_line is not None,
            bronze_keys_observed="bronzekey" in key_counts,
            silver_keys_observed="silverkey" in key_counts,
            gold_keys_observed="goldkey" in key_counts,
            sphere_stock_observed=sphere_stock is not None,
            sphere_counts_observed=spheres is not None,
            badges_observed=badge_line is not None,
        )
