"""Validate copied text from Mudae gift and trade transactions."""

import re


class TransactionParser:
    """Validate one response in a supported Mudae gift or trade flow."""

    def __init__(self, error_type: type[ValueError]) -> None:
        self._error_type = error_type

    def parse(self, text: str, kind: str) -> None:
        """Validate one transaction response and return ``None`` on success."""
        normalized = re.sub(r"\*+", "", text).casefold()
        if kind == "gift_kakera":
            valid = (
                re.search(r"syntax:\s*\$givek\b", normalized) is not None
                or ("do you really want to give" in normalized and ":kakera:" in normalized)
                or ("just gifted" in normalized and ":kakera:" in normalized)
            )
        elif kind == "gift_spheres":
            valid = (
                re.search(r"syntax:\s*\$givesp\b", normalized) is not None
                or ("do you really want to give" in normalized and ":sp:" in normalized)
                or ("just gifted" in normalized and ":sp:" in normalized)
            )
        elif kind == "gift_character":
            valid = (
                re.search(r"syntax:\s*\$give\b", normalized) is not None
                or ("wants to give you" in normalized and "do you confirm" in normalized)
                or re.search(r"\bgiven to\s+@", normalized) is not None
            )
        elif kind == "trade":
            valid = (
                re.search(r"syntax:\s*\$trade\b", normalized) is not None
                or "type the name(s) of the character" in normalized
                or "do you confirm the exchange" in normalized
                or "the exchange is over" in normalized
            )
        else:
            raise self._error_type(f"Unsupported transaction kind: {kind}")
        if not valid:
            raise self._error_type(f"Expected a Mudae {kind} transaction response.")
        return None
