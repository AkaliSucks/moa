"""Conservatively identify supported Mudae messages before routing their import."""

import re

from moa.models.character import MudaeMessageDetection
from moa.parser.mudae import MudaeParseError, MudaeTextParser


class MudaeMessageRouter:
    """Detect only formats MOA already knows how to parse reliably."""

    _TOP_HEADER = re.compile(r"\bTOP\s+[\d,]+\b", re.IGNORECASE)

    def __init__(self, parser: MudaeTextParser | None = None) -> None:
        self._parser = parser or MudaeTextParser()

    def detect(self, text: str) -> MudaeMessageDetection:
        """Return a supported kind or `unknown`; never force a best guess."""
        normalized = text.casefold()
        if "server settings" in normalized and "$setclaim" in normalized:
            return self._detected("settings", "Mudae server-settings header and configuration commands found.")
        if "each $kl costs" in normalized and "quantity or quality costs" in normalized:
            return self._detected("infokl", "Mudae Kakeraloot pricing text found.")
        if "player bonuses" in normalized:
            return self._detected("bonus", "Mudae Player Bonuses header found.")
        if "wishlist -" in normalized and "$wl" in normalized:
            return self._detected("wishlist", "Mudae wishlist header found.")
        if "disablelist (" in normalized and "disabled" in normalized:
            return self._detected("disablelist", "Mudae disablelist header and totals found.")
        if "current $personalrare" in normalized:
            return self._detected("personalrare", "Mudae personal rarity value found.")
        if "you can claim right now" in normalized or "you can't claim for another" in normalized:
            return self._detected("timers", "Mudae action-timer claim state found.")
        if "current level is" in normalized and "list of perks" in normalized:
            return self._detected("towerstate", "Mudae Kakera Tower state found.")
        if " - kakeraloots" in normalized or "no kakeraloots bought" in normalized:
            return self._detected("lootstate", "Mudae account Kakeraloot state found.")
        if "how to collect kakera" in normalized and "melt your kakera" in normalized:
            return self._detected("kakera", "Mudae Kakera balance and badge text found.")
        if self._TOP_HEADER.search(text):
            if "🚫" in text or "ðŸš«" in text or "$togglewestern" in normalized:
                return self._detected("topx", "Mudae TOP list includes unavailable-character markers.")
            return self._detected("top", "Mudae TOP ranking header found.")
        if "claim rank:" in normalized and "roulette" in normalized:
            return self._detected("im", "Mudae character-information rank and roulette fields found.")
        try:
            self._parser.parse_roll(text)
        except MudaeParseError:
            return self._detected("unknown", "No unambiguous supported Mudae format was found.")
        return self._detected("roll", "Message matches a supported Mudae roll card.")

    @staticmethod
    def _detected(kind: str, reason: str) -> MudaeMessageDetection:
        return MudaeMessageDetection(kind=kind, reason=reason)
