"""Small standard-library parser primitives shared by Mudae response parsers."""

import re


_CUSTOM_EMOJI = re.compile(r"<a?:(?P<name>[A-Za-z0-9_]+):\d+>")


def normalize_custom_emojis(value: str) -> str:
    """Convert Discord custom emoji markup to the short-name form."""
    return _CUSTOM_EMOJI.sub(r":\g<name>:", value)


def comma_int(value: str) -> int:
    """Convert a decimal string that may contain comma separators."""
    return int(value.replace(",", ""))


def first_named_rank(lines: list[str], pattern: re.Pattern[str]) -> int | None:
    """Return the first anchored match's comma-separated ``rank`` value."""
    for line in lines:
        match = pattern.match(line)
        if match is not None:
            return comma_int(match.group("rank"))
    return None
