"""Parse copied text from standard Mudae roll cards."""

import re

from moa.models.character import RollObservation


def clean_series(value: str) -> str:
    """Remove display-only gender and starwish markers from a series."""
    return RollParser._STARWISH_MARKER.sub(" ", RollParser._GENDER.sub("", value)).strip()


def roll_name_and_series(
    lines: list[str], marker_index: int, error_type: type[ValueError]
) -> tuple[str, str]:
    """Recover the name and all wrapped series lines before a roll marker."""
    content_lines = [
        line
        for line in lines[:marker_index]
        if line.casefold() not in {"mudae", "app"}
        and not line.lstrip().startswith(("$", "/"))
        and not line.casefold().startswith("wished by ")
    ]
    if len(content_lines) < 2:
        raise error_type("Expected character name and series before the Mudae roll marker.")
    return content_lines[0], " ".join(content_lines[1:])


class RollParser:
    """Parse the supported response variants for one Mudae roll family."""

    _ROULETTE = re.compile(
        r"^(?P<roulette>.+?)(?:\s+roulette)?(?:\s*[\u00b7\u2022]\s*|\s+)"
        r"\*{0,2}(?P<value>[\d,]+)\*{0,2}\s*"
        r"(?::kakera[a-z0-9_]*:|\bkakera\b)(?:\D.*)?$",
        re.IGNORECASE,
    )
    _CLAIM_RANK = re.compile(r"^Claim Rank:\s*#(?P<rank>[\d,]+)$", re.IGNORECASE)
    _LIKE_RANK = re.compile(r"^Like Rank:\s*#(?P<rank>[\d,]+)$", re.IGNORECASE)
    _ROLL_CLAIMS = re.compile(r"^Claims:\s*#(?P<rank>[\d,]+)$", re.IGNORECASE)
    _KAKERA = re.compile(
        r"^\s*\*{0,2}\+?(?P<value>[\d,]+)\*{0,2}\s*"
        r"(?::kakera[a-z0-9_]*:|\bkakera\b)\s*$",
        re.IGNORECASE,
    )
    _ROLL_KEY = re.compile(
        r":(?P<key_type>[a-z]+)key:\s*\(\*{0,2}(?P<count>\d+)\*{0,2}\)",
        re.IGNORECASE,
    )
    _GENERIC_KEY_COUNT = re.compile(
        r"\bkeys?\s*\(\*{0,2}(?P<count>\d+)\*{0,2}\)",
        re.IGNORECASE,
    )
    _GENDER = re.compile(
        r"\s+(?P<gender>(?::(?:female|male):)+)\s*$", re.IGNORECASE
    )
    _STARWISH_MARKER = re.compile(r"\s*:sw:\s*", re.IGNORECASE)

    def __init__(self, error_type: type[ValueError]) -> None:
        self._error_type = error_type

    @staticmethod
    def _lines(text: str) -> list[str]:
        normalized = re.sub(r"<a?:(?P<name>[A-Za-z0-9_]+):\d+>", r":\g<name>:", text)
        return [line.strip().replace("\u200b", "") for line in normalized.splitlines() if line.strip()]

    @staticmethod
    def _number(value: str) -> int:
        return int(value.replace(",", ""))

    def _identity_error(self, message: str) -> ValueError:
        return self._error_type(message)

    def _name_and_series(self, lines: list[str], marker_index: int) -> tuple[str, str]:
        return roll_name_and_series(lines, marker_index, self._error_type)

    def _validate_identity(self, name: str, series: str) -> None:
        """Reject a likely title/series split instead of storing a false character."""
        if len(name.strip()) >= 28 and len(series.strip()) <= 8:
            raise self._identity_error(
                "Ambiguous Mudae roll identity: a long character name and short series "
                "were returned; use `$im` to verify it before importing."
            )

    def parse(self, text: str) -> RollObservation:
        """Parse the key fields from a copied standard Mudae roll card."""
        lines = self._lines(text)
        key = next((self._ROLL_KEY.search(line) for line in lines if self._ROLL_KEY.search(line)), None)
        roulette_index = next(
            (index for index, line in enumerate(lines) if self._ROULETTE.match(line)),
            None,
        )
        if roulette_index is not None:
            if roulette_index < 2:
                raise self._error_type(
                    "Expected character name and series before the Mudae roulette line."
                )
            roulette_line = self._ROULETTE.match(lines[roulette_index])
            if roulette_line is None:
                raise self._error_type("Could not parse the Mudae roulette line.")
            name, series_line = self._name_and_series(lines, roulette_index)
            series = clean_series(series_line)
            self._validate_identity(name, series)
            return RollObservation(
                name=name,
                series=series,
                claim_rank=self._first_number(lines, self._CLAIM_RANK),
                kakera_value=self._number(roulette_line.group("value")),
                displayed_key_type=key.group("key_type").lower() if key else None,
                displayed_key_count=int(key.group("count")) if key else None,
            )

        claims_index = next(
            (index for index, line in enumerate(lines) if self._ROLL_CLAIMS.match(line)),
            None,
        )
        if claims_index is None:
            kakera_index = next(
                (index for index, line in enumerate(lines) if self._KAKERA.match(line)),
                None,
            )
            if kakera_index is None or kakera_index < 2:
                raise self._error_type(
                    "Expected a Mudae roll card with either a Claims line or a Kakera value."
                )
            kakera = self._KAKERA.match(lines[kakera_index])
            if kakera is None:
                raise self._error_type("Could not parse the Mudae Kakera value.")
            key_index = next(
                (index for index, line in enumerate(lines) if self._ROLL_KEY.search(line)),
                None,
            )
            if key_index == kakera_index - 1 and key_index >= 2:
                name, series = self._name_and_series(lines, key_index)
            else:
                name, series = self._name_and_series(lines, kakera_index)
            series = clean_series(series)
            self._validate_identity(name, series)
            return RollObservation(
                name=name,
                series=series,
                claim_rank=None,
                kakera_value=self._number(kakera.group("value")),
                displayed_key_type=key.group("key_type").lower() if key else None,
                displayed_key_count=int(key.group("count")) if key else None,
            )
        if claims_index < 2:
            raise self._error_type(
                "Expected character name and series before the Mudae Claims line."
            )

        claims_line = self._ROLL_CLAIMS.match(lines[claims_index])
        if claims_line is None:
            raise self._error_type("Could not parse the Mudae Claims line.")

        kakera = next(
            (self._KAKERA.match(line) for line in lines[claims_index + 1 :] if self._KAKERA.match(line)),
            None,
        )
        name, series = self._name_and_series(lines, claims_index)
        series = clean_series(series)
        self._validate_identity(name, series)
        return RollObservation(
            name=name,
            series=series,
            claim_rank=self._number(claims_line.group("rank")),
            kakera_value=self._number(kakera.group("value")) if kakera else None,
            displayed_key_type=key.group("key_type").lower() if key else None,
            displayed_key_count=int(key.group("count")) if key else None,
        )

    def _first_number(self, lines: list[str], pattern: re.Pattern[str]) -> int | None:
        match = next((pattern.match(line) for line in lines if pattern.match(line)), None)
        return self._number(match.group("rank")) if match else None
