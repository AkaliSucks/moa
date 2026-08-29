"""Parse copied text from Mudae character-details responses."""

import re

from moa.models.character import CharacterDetails
from moa.parser.roll import RollParser, clean_series


class CharacterDetailsParser:
    """Parse the supported response variants for the Mudae ``$im`` family."""

    def __init__(self, error_type: type[ValueError]) -> None:
        self._roll = RollParser(error_type)

    def parse(self, text: str) -> CharacterDetails:
        """Parse the currently observed fields from a copied ``$im`` response."""
        lines = self._roll._lines(text)
        roulette_index = next(
            (index for index, line in enumerate(lines) if self._roll._ROULETTE.match(line)),
            None,
        )
        if roulette_index is None or roulette_index < 2:
            raise self._roll._error_type("Expected a Mudae $im response with a roulette line.")

        roulette_line = self._roll._ROULETTE.match(lines[roulette_index])
        if roulette_line is None:
            raise self._roll._error_type("Could not parse the Mudae roulette line.")

        name, series_line = self._roll._name_and_series(lines, roulette_index)
        gender_match = self._roll._GENDER.search(series_line)
        key = self._roll._ROLL_KEY.search(lines[roulette_index])
        generic_key = self._roll._GENERIC_KEY_COUNT.search(lines[roulette_index])

        return CharacterDetails(
            name=name,
            series=clean_series(series_line),
            gender=(
                ",".join(
                    re.findall(r"(?::(female|male):)", gender_match.group("gender"), re.IGNORECASE)
                ).lower()
                if gender_match
                else None
            ),
            roulette=roulette_line.group("roulette").strip().lower(),
            kakera_value=self._roll._number(roulette_line.group("value")),
            claim_rank=self._roll._first_number(lines, self._roll._CLAIM_RANK),
            like_rank=self._roll._first_number(lines, self._roll._LIKE_RANK),
            key_type=(key.group("key_type").lower() if key else None),
            key_count=(
                int(key.group("count"))
                if key
                else int(generic_key.group("count"))
                if generic_key
                else None
            ),
        )
