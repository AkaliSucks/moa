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
        try:
            self._parser.parse_kakera_reaction_receipt(text)
        except MudaeParseError:
            pass
        else:
            return self._detected("reaction_receipt", "Mudae Kakera reaction amount and recipient found.")
        try:
            self._parser.parse_kakera_reaction_blocked(text)
        except MudaeParseError:
            pass
        else:
            return self._detected(
                "reaction_blocked",
                "Mudae Kakera reaction was blocked by the account cooldown.",
            )
        if (
            "substep completed" in normalized
            or "step completed!" in normalized
            or re.search(r"\bstep\s+\d+\s+completed!", normalized)
            or re.search(r"\b\d+\s*/\s*\d+\s*-\s*tutorial\b", normalized)
        ):
            return self._detected("tutorial", "Mudae tutorial progress response found.")
        if "mudae help" in normalized or "looking for a specific command" in normalized:
            return self._detected("help", "Mudae help response found.")
        for transaction_kind in ("gift_kakera", "gift_spheres", "gift_character", "trade"):
            try:
                self._parser.parse_transaction(text, transaction_kind)
            except MudaeParseError:
                continue
            return self._detected(transaction_kind, f"Mudae {transaction_kind} response found.")
        if "mudapins are collectable badges" in normalized and "$mp" in normalized:
            return self._detected("help", "Mudae Mudapin information response found.")
        try:
            self._parser.parse_mudapins(text)
        except MudaeParseError:
            pass
        else:
            return self._detected("mudapins", "Mudae Mudapin inventory found.")
        if "collection size:" in normalized:
            try:
                self._parser.parse_profile(text)
            except MudaeParseError:
                pass
            else:
                return self._detected("profile", "Mudae account profile summary found.")
        if "harem" in normalized:
            try:
                self._parser.parse_ranked_harem_page(text)
            except MudaeParseError:
                pass
            else:
                return self._detected("ranked_harem", "Mudae ranked harem page found.")
            try:
                self._parser.parse_harem_key_page(text)
            except MudaeParseError:
                pass
            else:
                return self._detected("harem", "Mudae keyed-harem page found.")
        if "server settings" in normalized and "$setclaim" in normalized:
            return self._detected("settings", "Mudae server-settings header and configuration commands found.")
        if "each $kl costs" in normalized and "quantity or quality costs" in normalized:
            return self._detected("infokl", "Mudae Kakeraloot pricing text found.")
        if "player bonuses" in normalized:
            return self._detected("bonus", "Mudae Player Bonuses header found.")
        try:
            self._parser.parse_claim_confirmation(text)
        except MudaeParseError:
            pass
        else:
            return self._detected("claim", "Mudae character-claim confirmation found.")
        try:
            self._parser.parse_divorce_prompt(text)
        except MudaeParseError:
            pass
        else:
            return self._detected("divorce_prompt", "Mudae divorce confirmation prompt found.")
        try:
            self._parser.parse_divorce_declined(text)
        except MudaeParseError:
            pass
        else:
            return self._detected("divorce_declined", "Mudae divorce was declined.")
        try:
            self._parser.parse_divorce_confirmation(text)
        except MudaeParseError:
            pass
        else:
            return self._detected("divorce_complete", "Mudae completed a character divorce.")
        try:
            self._parser.parse_sphere_result(text)
        except MudaeParseError:
            pass
        else:
            return self._detected("sphere_result", "Mudae sphere payout result found.")
        if "wishlist -" in normalized and "$wl" in normalized:
            return self._detected("wishlist", "Mudae wishlist header found.")
        if "antidisablelist" in normalized:
            try:
                self._parser.parse_antidisable_page(text)
            except MudaeParseError:
                pass
            else:
                return self._detected("antidisable", "Mudae antidisable series list found.")
        if "disablelist (" in normalized and "disabled" in normalized:
            return self._detected("disablelist", "Mudae disablelist header and totals found.")
        if "current $personalrare" in normalized:
            return self._detected("personalrare", "Mudae personal rarity value found.")
        if (
            "you can claim right now" in normalized
            or "you can't claim for another" in normalized
            or "the next interval begins in" in normalized
            or "roulette is limited to" in normalized
            or re.search(
                r"(?m)^you can't react to kakera for\s+\*?.+?\*?\.\s*$",
                normalized,
            )
            or "next rolls reset in" in normalized
            or re.search(r"you have\s+\d+\s+rolls? left\.", normalized)
            or "reset your rolls timer for one server" in normalized
        ):
            return self._detected("timers", "Mudae action-timer claim state found.")
        if "current level is" in normalized and "list of perks" in normalized:
            return self._detected("towerstate", "Mudae Kakera Tower state found.")
        if (
            " - kakeraloots" in normalized
            or "no kakeraloots bought" in normalized
            or "need to buy kakeraloots before using this command" in normalized
            or (
                "prerequisites:" in normalized
                and "sapphire i" in normalized
                and "ruby i" in normalized
                and "emerald i" in normalized
                and "$infokl" in normalized
            )
        ):
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
