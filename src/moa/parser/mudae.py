"""Parsers for copied text from Mudae bot messages.

The parsers deliberately operate on text copied by a user or an authorized
companion bot. They do not automate Discord accounts or invoke Mudae commands.
"""

import re

from moa.models.character import (
    CharacterDetails,
    ClaimConfirmation,
    DivorceConfirmation,
    DivorcePrompt,
    AntidisablePage,
    KakeraReactionBlocked,
    KakeraReactionReceipt,
    KakeralootSettingsSnapshot,
    MudapinSnapshot,
    ProfileSnapshot,
    PersonalRareSnapshot,
    ServerSettingsSnapshot,
    SphereResultSnapshot,
    TimerStateSnapshot,
    RankedHaremPage,
    KakeraStateSnapshot,
    KakeralootStateSnapshot,
    TowerStateSnapshot,
    DisableListSnapshot,
    HaremKeyPage,
    PlayerBonusSnapshot,
    RollObservation,
    TopPage,
    UnavailableCharacterPage,
    WishlistSnapshot,
)
from moa.parser.antidisable import AntidisablePageParser
from moa.parser.character_details import CharacterDetailsParser
from moa.parser.claim import ClaimParser
from moa.parser.divorce_confirmation import DivorceConfirmationParser
from moa.parser.divorce_declined import DivorceDeclinedValidator
from moa.parser.divorce_prompt import DivorcePromptParser
from moa.parser.disablelist import DisableListParser
from moa.parser.harem_key import HaremKeyParser
from moa.parser.harem_ranked import RankedHaremParser
from moa.parser.kakera_reaction_blocked import KakeraReactionBlockedParser
from moa.parser.kakera_reaction_receipt import KakeraReactionReceiptParser
from moa.parser.kakera_state import KakeraStateParser
from moa.parser.kakeraloot_state import KakeralootStateParser
from moa.parser.kakeraloot_settings import KakeralootSettingsParser
from moa.parser.personal_rare import PersonalRareParser
from moa.parser.player_bonus import PlayerBonusParser
from moa.parser.primitives import comma_int, first_named_rank, normalize_custom_emojis
from moa.parser.profile import ProfileParser
from moa.parser.roll import RollParser
from moa.parser.server_settings import ServerSettingsParser
from moa.parser.sphere_result import SphereResultParser
from moa.parser.top import TopParser
from moa.parser.transaction import TransactionParser
from moa.parser.timer_state import TimerStateParser
from moa.parser.tower_state import TowerStateParser
from moa.parser.unavailable_characters import UnavailableCharacterPageParser
from moa.parser.wishlist import WishlistParser


class MudaeParseError(ValueError):
    """Raised when copied Mudae output does not match a supported format."""


class MudaeTextParser:
    """Parse stable, high-value fields from common Mudae message formats."""

    _MUDAPIN_MARKER = re.compile(r":(?:pin|logopin)\d+:", re.IGNORECASE)

    _NO_MUDAPINS = re.compile(
        r"No mudapins found!.*kakeraloots", re.IGNORECASE
    )

    _KAKERA_BALANCE = re.compile(
        r"^You have\s+(?P<value>[\d,]+)\s*:kakera:\s*!?$", re.IGNORECASE
    )
    @staticmethod
    def _lines(text: str) -> list[str]:
        normalized = normalize_custom_emojis(text)
        return [line.strip().replace("\u200b", "") for line in normalized.splitlines() if line.strip()]

    @staticmethod
    def _number(value: str) -> int:
        return comma_int(value)

    def parse_top_page(self, text: str) -> TopPage:
        """Parse one copied `$top` page into ranked character observations."""
        return TopParser(MudaeParseError).parse(text)

    @staticmethod
    def _duration_minutes(value: str) -> int:
        """Convert Mudae's `2h 32 min`/`32 min` wording to whole minutes."""
        value = re.sub(r"\*+", "", value)
        hours = re.search(r"(?P<value>\d+)h", value, re.IGNORECASE)
        minutes = re.search(r"(?P<value>\d+)\s*min", value, re.IGNORECASE)
        if hours is None and minutes is None:
            raise MudaeParseError(f"Unsupported Mudae timer duration: {value!r}")
        return (int(hours.group("value")) * 60 if hours else 0) + (
            int(minutes.group("value")) if minutes else 0
        )

    def parse_character_details(self, text: str) -> CharacterDetails:
        """Parse the key fields from a copied `$im <character>` response."""
        return CharacterDetailsParser(MudaeParseError).parse(text)

    def parse_roll(self, text: str) -> RollObservation:
        """Parse the key fields from a copied standard Mudae roll card."""
        return RollParser(MudaeParseError).parse(text)

    def parse_claim_confirmation(self, text: str) -> ClaimConfirmation:
        """Parse Mudae's short confirmation sent after a character is claimed."""
        return ClaimParser(MudaeParseError).parse(text)

    def parse_transaction(self, text: str, kind: str) -> None:
        """Validate one response in a Mudae gift or trade flow."""
        return TransactionParser(MudaeParseError).parse(text, kind)

    def parse_divorce_prompt(self, text: str) -> DivorcePrompt:
        """Parse the first response from Mudae's two-step `$divorce` flow."""
        return DivorcePromptParser(MudaeParseError).parse(text)

    def parse_divorce_declined(self, text: str) -> None:
        """Validate Mudae's response when a pending divorce is declined."""
        return DivorceDeclinedValidator(MudaeParseError).parse(text)

    def parse_divorce_confirmation(
        self, text: str, expected_account: str | None = None
    ) -> DivorceConfirmation:
        """Parse Mudae's completion message after a confirmed `$divorce`."""
        return DivorceConfirmationParser(MudaeParseError).parse(
            text, expected_account=expected_account
        )

    def parse_kakera_reaction_receipt(self, text: str) -> KakeraReactionReceipt:
        """Parse the standalone Mudae message shown after a Kakera reaction."""
        return KakeraReactionReceiptParser(MudaeParseError).parse(text)

    def parse_kakera_reaction_blocked(self, text: str) -> KakeraReactionBlocked:
        """Parse the one-line response shown after an unaffordable Kakera click."""
        return KakeraReactionBlockedParser(
            MudaeParseError, self._duration_minutes
        ).parse(text)

    def parse_harem_key_page(self, text: str) -> HaremKeyPage:
        """Parse one copied keyed-harem page, with optional current Kakera values."""
        return HaremKeyParser(
            MudaeParseError, self._lines, self._number
        ).parse(text)

    def parse_ranked_harem_page(self, text: str) -> RankedHaremPage:
        """Parse direct owned-character evidence from `$mmr` or `$mmrk`."""
        return RankedHaremParser(
            MudaeParseError, self._lines, self._number
        ).parse(text)

    def parse_player_bonus(self, text: str) -> PlayerBonusSnapshot:
        """Parse stable player modifiers from a copied Mudae `$bonus` message."""
        return PlayerBonusParser(MudaeParseError, self._lines).parse(text)

    def parse_wishlist(self, text: str) -> WishlistSnapshot:
        """Parse one copied Mudae `$wl` response, including Starwish markers."""
        return WishlistParser(MudaeParseError, self._lines).parse(text)

    def parse_antidisable_page(self, text: str) -> AntidisablePage:
        """Parse one copied `$adl` page as a series-level list."""
        return AntidisablePageParser(
            MudaeParseError, self._lines, self._number
        ).parse(text)

    def parse_disablelist(self, text: str) -> DisableListSnapshot:
        """Parse account-specific disable-list settings from a copied `$dl` reply."""
        return DisableListParser(MudaeParseError, self._lines, self._number).parse(text)

    def parse_unavailable_characters(self, text: str) -> UnavailableCharacterPage:
        """Parse the currently unrollable characters listed by Mudae `$topx`."""
        return UnavailableCharacterPageParser(
            MudaeParseError, self._lines, self._number
        ).parse(text)

    def parse_kakera_state(self, text: str) -> KakeraStateSnapshot:
        """Parse current Kakera balance and badge levels from a copied `$k` response."""
        return KakeraStateParser(
            MudaeParseError, self._lines, self._number, self._KAKERA_BALANCE
        ).parse(text)

    def parse_personal_rare(self, text: str) -> PersonalRareSnapshot:
        """Parse the account-scoped `$personalrare` value from `$persr` output."""
        return PersonalRareParser(MudaeParseError, self._lines).parse(text)

    def parse_timer_state(self, text: str) -> TimerStateSnapshot:
        """Parse whichever action categories are currently visible in `$tu`."""
        return TimerStateParser(
            MudaeParseError,
            self._lines,
            self._duration_minutes,
            self._number,
        ).parse(text)

    def parse_tower_state(self, text: str) -> TowerStateSnapshot:
        """Parse current level, cost, balance, and owned floors from a copied `$kt` response."""
        return TowerStateParser(
            MudaeParseError, self._lines, self._number, self._KAKERA_BALANCE
        ).parse(text)

    def parse_kakeraloot_state(self, text: str) -> KakeralootStateSnapshot:
        """Parse current Kakeraloot progress and balance from a copied `$lk` response."""
        return KakeralootStateParser(
            MudaeParseError, self._lines, self._number
        ).parse(text)

    def parse_sphere_result(self, text: str) -> SphereResultSnapshot:
        """Parse the payout and stock summary from one `$oq` response."""
        return SphereResultParser(
            MudaeParseError, self._lines, self._number
        ).parse(text)

    def parse_kakeraloot_settings(self, text: str) -> KakeralootSettingsSnapshot:
        """Parse server-configurable and universal Kakeraloot costs from `$infokl`."""
        return KakeralootSettingsParser(
            MudaeParseError, self._lines, self._number
        ).parse(text)

    def parse_profile(self, text: str) -> ProfileSnapshot:
        """Parse account progress totals from a copied `$profile` response."""
        return ProfileParser(
            MudaeParseError, self._lines, self._number
        ).parse(text)

    def parse_mudapins(self, text: str) -> MudapinSnapshot:
        """Parse a `$mp` inventory, including Mudae's empty response."""
        if self._NO_MUDAPINS.search(text):
            return MudapinSnapshot(pin_markers=())
        markers = tuple(match.group(0) for match in self._MUDAPIN_MARKER.finditer(text))
        if not markers:
            raise MudaeParseError("Expected a Mudae `$mp` Mudapin inventory response.")
        return MudapinSnapshot(pin_markers=markers)

    def parse_server_settings(self, text: str) -> ServerSettingsSnapshot:
        """Parse core server rules and retain all visible `$settings` options."""
        return ServerSettingsParser(MudaeParseError, self._lines).parse(text)

    def _first_number(self, lines: list[str], pattern: re.Pattern[str]) -> int | None:
        return first_named_rank(lines, pattern)
