"""Parsers for copied text from Mudae bot messages.

The parsers deliberately operate on text copied by a user or an authorized
companion bot. They do not automate Discord accounts or invoke Mudae commands.
"""

import re

from moa.models.character import (
    CharacterDetails,
    RankedCharacter,
    RollObservation,
    TopPage,
)


class MudaeParseError(ValueError):
    """Raised when copied Mudae output does not match a supported format."""


class MudaeTextParser:
    """Parse stable, high-value fields from common Mudae message formats."""

    _TOP_HEADER = re.compile(r"\bTOP\s+(?P<limit>[\d,]+)\b", re.IGNORECASE)
    _TOP_ENTRY = re.compile(
        r"^#(?P<rank>[\d,]+)\s+-\s+(?P<name>.+?)\s+-\s+(?P<series>.+)$"
    )
    _PAGE = re.compile(r"^Page\s+(?P<page>\d+)\s*/\s*(?P<pages>\d+)$", re.IGNORECASE)
    _ROULETTE = re.compile(
        r"^(?P<roulette>.+?)\s+roulette\s+·\s+(?P<value>[\d,]+):kakera:$",
        re.IGNORECASE,
    )
    _CLAIM_RANK = re.compile(r"^Claim Rank:\s*#(?P<rank>[\d,]+)$", re.IGNORECASE)
    _LIKE_RANK = re.compile(r"^Like Rank:\s*#(?P<rank>[\d,]+)$", re.IGNORECASE)
    _ROLL_CLAIMS = re.compile(r"^Claims:\s*#(?P<rank>[\d,]+)$", re.IGNORECASE)
    _KAKERA = re.compile(r"^(?P<value>[\d,]+):kakera:$", re.IGNORECASE)
    _GENDER = re.compile(r"\s+:(?P<gender>female|male):\s*$", re.IGNORECASE)

    @staticmethod
    def _lines(text: str) -> list[str]:
        return [line.strip().replace("\u200b", "") for line in text.splitlines() if line.strip()]

    @staticmethod
    def _number(value: str) -> int:
        return int(value.replace(",", ""))

    def parse_top_page(self, text: str) -> TopPage:
        """Parse one copied `$top` page into ranked character observations."""
        lines = self._lines(text)
        header = next((self._TOP_HEADER.search(line) for line in lines if self._TOP_HEADER.search(line)), None)
        page = next((self._PAGE.match(line) for line in lines if self._PAGE.match(line)), None)

        characters: list[RankedCharacter] = []
        for line in lines:
            entry = self._TOP_ENTRY.match(line)
            if entry is None:
                continue
            name = entry.group("name").removesuffix(" 💞").strip()
            characters.append(
                RankedCharacter(
                    name=name,
                    series=entry.group("series").strip(),
                    claim_rank=self._number(entry.group("rank")),
                )
            )

        if not characters:
            raise MudaeParseError("No ranked characters found in the Mudae $top output.")

        return TopPage(
            limit=self._number(header.group("limit")) if header else None,
            page_number=int(page.group("page")) if page else None,
            page_count=int(page.group("pages")) if page else None,
            characters=tuple(characters),
        )

    def parse_character_details(self, text: str) -> CharacterDetails:
        """Parse the key fields from a copied `$im <character>` response."""
        lines = self._lines(text)
        roulette_index = next(
            (index for index, line in enumerate(lines) if self._ROULETTE.match(line)),
            None,
        )
        if roulette_index is None or roulette_index < 2:
            raise MudaeParseError("Expected a Mudae $im response with a roulette line.")

        roulette_line = self._ROULETTE.match(lines[roulette_index])
        if roulette_line is None:
            raise MudaeParseError("Could not parse the Mudae roulette line.")

        gender_match = self._GENDER.search(lines[roulette_index - 1])
        series = self._GENDER.sub("", lines[roulette_index - 1]).strip()

        claim_rank = self._first_number(lines, self._CLAIM_RANK)
        like_rank = self._first_number(lines, self._LIKE_RANK)

        return CharacterDetails(
            name=lines[roulette_index - 2],
            series=series,
            gender=gender_match.group("gender").lower() if gender_match else None,
            roulette=roulette_line.group("roulette").strip().lower(),
            kakera_value=self._number(roulette_line.group("value")),
            claim_rank=claim_rank,
            like_rank=like_rank,
        )

    def parse_roll(self, text: str) -> RollObservation:
        """Parse the key fields from a copied standard Mudae roll card."""
        lines = self._lines(text)
        claims_index = next(
            (index for index, line in enumerate(lines) if self._ROLL_CLAIMS.match(line)),
            None,
        )
        if claims_index is None or claims_index < 2:
            raise MudaeParseError("Expected a Mudae roll card with a Claims line.")

        claims_line = self._ROLL_CLAIMS.match(lines[claims_index])
        if claims_line is None:
            raise MudaeParseError("Could not parse the Mudae Claims line.")

        kakera = next(
            (self._KAKERA.match(line) for line in lines[claims_index + 1 :] if self._KAKERA.match(line)),
            None,
        )
        return RollObservation(
            name=lines[claims_index - 2],
            series=lines[claims_index - 1],
            claim_rank=self._number(claims_line.group("rank")),
            kakera_value=self._number(kakera.group("value")) if kakera else None,
        )

    def _first_number(self, lines: list[str], pattern: re.Pattern[str]) -> int | None:
        match = next((pattern.match(line) for line in lines if pattern.match(line)), None)
        return self._number(match.group("rank")) if match else None
