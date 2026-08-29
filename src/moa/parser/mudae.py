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
    ServerSettingMetric,
    ServerSettingsSnapshot,
    SphereGain,
    SphereResultSnapshot,
    TimerStateSnapshot,
    RankedHaremPage,
    BadgeLevel,
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
from moa.parser.player_bonus import PlayerBonusParser
from moa.parser.primitives import comma_int, first_named_rank, normalize_custom_emojis
from moa.parser.roll import RollParser
from moa.parser.top import TopParser
from moa.parser.transaction import TransactionParser
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

    _PERSONAL_RARE = re.compile(
        r"(?:Your\s+)?current\s+\$personalrare:\s*(?P<value>\d+)", re.IGNORECASE
    )

    _SPHERE_CLICKS = re.compile(
        r"You can click\s+(?P<clicks>\d+)\s+times.*?\((?P<minutes>\d+)\s+minutes?\)",
        re.IGNORECASE,
    )

    _SPHERE_GOAL = re.compile(
        r"Find\s+(?P<target>\d+)\s+purple spheres?\s+\(out of\s+(?P<total>\d+)\)",
        re.IGNORECASE,
    )

    _SPHERE_GAIN = re.compile(
        r"^:(?P<marker>sp[a-z0-9_]*):\s*(?P<free>\(Free\)\s*)?"
        r"\+(?P<amount>[\d,]+)(?:\s+\(Stock:\s*(?P<stock>[\d,]+)\))?$",
        re.IGNORECASE,
    )

    _LOOT_COST = re.compile(
        r"Each\s+\$kl\s+costs\s+(?P<value>[\d,]+)\s*:(?:kakera):",
        re.IGNORECASE,
    )

    _LOOT_UPGRADE_COST = re.compile(
        r"level\s+1\s+of\s+quantity\s+or\s+quality\s+costs\s+(?P<base>[\d,]+)\s*:(?:kakera):"
        r".*?increased\s+by\s+(?P<increment>[\d,]+)/level",
        re.IGNORECASE,
    )

    _SERVER_PREMIUM = re.compile(r"Server\s+(?P<status>not\s+premium|premium)", re.IGNORECASE)

    _SETTING_LINE = re.compile(
        r"^\s*[^\w\s]*\s*(?P<label>.+?):\s*(?P<value>.+?)\s*\(\$[^)]*\)\s*$"
    )

    _SETTING_CLAIM_RESET = re.compile(r"Claim reset:\s*every\s*(?P<value>\d+)\s*min", re.IGNORECASE)

    _SETTING_RESET_MINUTE = re.compile(r"Exact minute of the reset:\s*(?P<value>\S+)", re.IGNORECASE)

    _SETTING_RESET_SHIFT = re.compile(r"Reset shifted:\s*by\s*(?P<value>[+-]?\d+)\s*min", re.IGNORECASE)

    _SETTING_ROLLS = re.compile(r"Rolls per hour:\s*(?P<value>\d+)", re.IGNORECASE)

    _SETTING_TIMER = re.compile(r"Time before the claim reaction expires:\s*(?P<value>\d+)\s*sec", re.IGNORECASE)

    _SETTING_RARE = re.compile(r"Spawn rarity multiplier.*?:\s*(?P<value>\d+)", re.IGNORECASE)

    _SETTING_KAKERA_BONUS = re.compile(r"% kakera bonus:\s*\+?(?P<value>\d+)", re.IGNORECASE)

    _SETTING_SPHERE_BONUS = re.compile(r"% sphere bonus:\s*\+?(?P<value>\d+)", re.IGNORECASE)

    _SETTING_GAMEMODE = re.compile(r"Game mode:\s*(?P<value>\d+)", re.IGNORECASE)

    _SETTING_CHANNEL_INSTANCE = re.compile(r"This channel instance:\s*(?P<value>\d+)", re.IGNORECASE)

    _TIMER_CLAIM_READY = re.compile(
        r"you can claim right now!\s*The next claim reset is in\s*(?P<duration>.+?)\.",
        re.IGNORECASE,
    )

    _TIMER_CLAIM_WAITING = re.compile(
        r"you can't claim for another\s*(?P<duration>.+?)\.", re.IGNORECASE
    )

    _TIMER_CLAIM_INTERVAL_WAITING = re.compile(
        r"for this server,\s*you can claim once per interval of\s*.+?\.\s*"
        r"the next interval begins in\s*(?P<duration>.+?)\.",
        re.IGNORECASE,
    )

    _TIMER_ROLLS = re.compile(
        r"You have\s*\*{0,2}(?P<rolls>\d+)\*{0,2}\s+rolls? left\.\s*"
        r"Next rolls reset in\s*(?P<duration>.+?)\.",
        re.IGNORECASE,
    )

    _TIMER_ROLL_LIMITED = re.compile(
        r"roulette is limited to\s*\*{0,2}(?P<limit>\d+)\*{0,2}\s+uses? per hour\.\s*"
        r"(?P<duration>.+?)\s+left\.",
        re.IGNORECASE,
    )

    _TIMER_ROLL_VOTE_PROMPT = re.compile(
        r"use this command again to reset your rolls timer for one server",
        re.IGNORECASE,
    )

    _TIMER_ROLL_STOCK = re.compile(
        r"You have\s*\*{0,2}(?P<value>\d+)\*{0,2}\s+rolls? reset in stock",
        re.IGNORECASE,
    )

    _TIMER_VOTE = re.compile(r"You may vote again in\s*(?P<duration>.+?)\.", re.IGNORECASE)

    _TIMER_DAILY = re.compile(r"Next \$daily reset in\s*(?P<duration>.+?)\.", re.IGNORECASE)

    _TIMER_KAKERA_WAITING = re.compile(
        r"^You can't react to kakera for\s*(?P<duration>.+?)\.$",
        re.IGNORECASE,
    )

    _TIMER_RTU_COOLDOWN = re.compile(
        r"^The cooldown of \$rt is not over\.\s*Time left:\s*(?P<duration>.+?)\.\s*\(\$rtu\)$",
        re.IGNORECASE,
    )

    _TIMER_RTU_LOCKED = re.compile(
        r"^You didn't unlock this command yet!.*\(\$kakera\)$",
        re.IGNORECASE,
    )

    _TIMER_POWER = re.compile(r"^Power:\s*(?P<value>\d+)%$", re.IGNORECASE)

    _TIMER_POWER_COST = re.compile(
        r"Each kakera button consumes\s*(?P<value>\d+)%\s+of your reaction power", re.IGNORECASE
    )

    _TIMER_SOULMATE_COST = re.compile(r"half the power \((?P<value>\d+)%\)", re.IGNORECASE)

    _TIMER_STOCK = re.compile(r"^Stock:\s*(?P<value>[\d,]+):kakera:$", re.IGNORECASE)

    _TIMER_GOLD_KEY_STOCK = re.compile(
        r"\(Keys LVL 6\+\)\s*(?P<value>[\d,]+):kakera:to collect before the next reset "
        r"\((?P<duration>.+?)\)",
        re.IGNORECASE,
    )

    _TIMER_BKU_PROBABILITY = re.compile(r"next \$sw:\s*(?P<value>\d+)%", re.IGNORECASE)

    _TIMER_OURO = re.compile(
        r"(?P<oh>\d+)\s+\$oh left for today,\s*(?P<oc>\d+)\s+\$oc,\s*"
        r"(?P<oq>\d+)\s+\$oq(?:\s*\(\+(?P<stored>\d+) stored\))?\s*and\s*"
        r"(?P<ot>\d+)\s+\$ot\.",
        re.IGNORECASE,
    )

    _TIMER_OURO_REFILL = re.compile(r"^(?P<duration>.+?)\s+before the refill\.$", re.IGNORECASE)

    _KAKERA_BALANCE = re.compile(
        r"^You have\s+(?P<value>[\d,]+)\s*:kakera:\s*!?$", re.IGNORECASE
    )
    _BADGE_LEVEL = re.compile(
        r"(?P<name>Bronze|Silver|Gold|Sapphire|Ruby|Emerald|Diamond)\s+"
        r"(?P<level>I|II|III|IV)\s*[·\u00b7]\s*(?P<status>.+)$",
        re.IGNORECASE,
    )
    _TOWER_LEVEL = re.compile(
        r"current level is.*?tow(?P<level>\d+):?(?:.*?\(\+\s*(?P<towers>\d+)\s+towers?)?",
        re.IGNORECASE,
    )
    _TOWER_NEXT_COST = re.compile(
        r"next level costs\s+(?P<value>[\d,]+):kakera:", re.IGNORECASE
    )
    _TOWER_PERK = re.compile(r"^.*?\[(?P<id>\d+)\]")
    _LOOT_ROLLS = re.compile(r"Rolls stacked:\s*(?P<value>\d+)", re.IGNORECASE)
    _LOOT_DISABLE = re.compile(
        r"\$disable limits:\s*-(?P<wa_ha>\d+)\s+\$wa/\$ha,\s*-(?P<wg_hg>\d+)\s+\$wg/\$hg",
        re.IGNORECASE,
    )
    _LOOT_PROTECTED_WISH = re.compile(
        r"Protected wish:\s*LVL\s*(?P<level>\d+)\s*\(spawn probability:\s*1/(?P<denominator>[\d,]+)\)",
        re.IGNORECASE,
    )
    _LOOT_MUDAPINS = re.compile(r"Mudapins:\s*(?P<value>\d+)", re.IGNORECASE)
    _LOOT_RT = re.compile(r"\$rt:\s*-(?P<value>\d+)h\s+cooldown", re.IGNORECASE)
    _LOOT_PERMANENT_ROLL = re.compile(r"\+(?P<value>\d+)\s+permanent roll", re.IGNORECASE)
    _LOOT_STAR_BRANCH = re.compile(
        r"(?P<branches>\d+)\s+star branch(?:es)?\s*\(\+(?P<slots>\d+)\s+\$sw\)",
        re.IGNORECASE,
    )
    _LOOT_QUANTITY = re.compile(r"Quantity\s+LVL\s+(?P<value>\d+)", re.IGNORECASE)
    _LOOT_QUALITY = re.compile(r"Quality\s+LVL\s+(?P<value>\d+)", re.IGNORECASE)
    _LOOT_USAGE = re.compile(r"\$kl usage:\s*(?P<value>[\d,]+)", re.IGNORECASE)
    _LOOT_BALANCE = re.compile(r"^(?P<value>[\d,]+)\s*:\s*kakera\s*:$", re.IGNORECASE)
    _NO_KAKERALOOTS = re.compile(
        r"No kakeraloots bought|need to buy kakeraloots before using this command|"
        r"Prerequisites:\s*Sapphire\s+I\s*\+\s*Ruby\s+I\s*\+\s*Emerald\s+I.*\$infokl",
        re.IGNORECASE,
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
        lines = [re.sub(r"\*", "", line) for line in self._lines(text)]
        balance = next((self._KAKERA_BALANCE.match(line) for line in lines if self._KAKERA_BALANCE.match(line)), None)
        if balance is None:
            raise MudaeParseError("Expected a Mudae $k response with a Kakera balance.")
        roman_levels = {"I": 1, "II": 2, "III": 3, "IV": 4}
        badges: list[BadgeLevel] = []
        for line in lines:
            match = self._BADGE_LEVEL.search(line)
            if match is None:
                continue
            badges.append(
                BadgeLevel(
                    badge_name=match.group("name").lower(),
                    level=roman_levels[match.group("level").upper()],
                    max_reached="max reached" in match.group("status").casefold(),
                )
            )
        if not badges:
            raise MudaeParseError("No Kakera badge levels found in the Mudae $k output.")
        return KakeraStateSnapshot(
            kakera_balance=self._number(balance.group("value")), badges=tuple(badges)
        )

    def parse_personal_rare(self, text: str) -> PersonalRareSnapshot:
        """Parse the account-scoped `$personalrare` value from `$persr` output."""
        normalized_text = "\n".join(
            re.sub(r"[*_]", "", line) for line in self._lines(text)
        )
        match = self._PERSONAL_RARE.search(normalized_text)
        if match is None:
            raise MudaeParseError("Expected a Mudae $persr response with a current $personalrare value.")
        return PersonalRareSnapshot(personal_rare_multiplier=int(match.group("value")))

    def parse_timer_state(self, text: str) -> TimerStateSnapshot:
        """Parse whichever action categories are currently visible in `$tu`."""
        # Discord/Mudae may wrap individual labels or values in Markdown
        # emphasis. Timer fields are plain state values, so remove that
        # presentation layer before applying the anchored line patterns.
        lines = [re.sub(r"\*", "", line) for line in self._lines(text)]
        normalized_text = "\n".join(lines)

        def first(pattern: re.Pattern[str]) -> re.Match[str] | None:
            return next((pattern.search(line) for line in lines if pattern.search(line)), None)

        claim_ready = first(self._TIMER_CLAIM_READY)
        claim_waiting = first(self._TIMER_CLAIM_WAITING)
        claim_interval_waiting = first(self._TIMER_CLAIM_INTERVAL_WAITING)
        rolls = first(self._TIMER_ROLLS)
        limited_rolls = first(self._TIMER_ROLL_LIMITED)
        vote_prompt = first(self._TIMER_ROLL_VOTE_PROMPT)
        roll_stock = first(self._TIMER_ROLL_STOCK)
        vote = first(self._TIMER_VOTE)
        daily = first(self._TIMER_DAILY)
        kakera_waiting = first(self._TIMER_KAKERA_WAITING)
        rtu_cooldown = first(self._TIMER_RTU_COOLDOWN)
        rtu_locked = first(self._TIMER_RTU_LOCKED)
        power = first(self._TIMER_POWER)
        power_cost = first(self._TIMER_POWER_COST)
        soulmate_cost = first(self._TIMER_SOULMATE_COST)
        stock = first(self._TIMER_STOCK)
        gold_key_stock = first(self._TIMER_GOLD_KEY_STOCK)
        bku_probability = first(self._TIMER_BKU_PROBABILITY)
        ouro = first(self._TIMER_OURO)
        ouro_refill = first(self._TIMER_OURO_REFILL)
        recognized_categories = (
            claim_ready,
            claim_waiting,
            claim_interval_waiting,
            rolls,
            limited_rolls,
            vote_prompt,
            roll_stock,
            vote,
            daily,
            kakera_waiting,
            rtu_cooldown,
            rtu_locked,
            power,
            stock,
            gold_key_stock,
            ouro,
            "$dk is ready!" in normalized_text.casefold(),
            "next $dk in" in normalized_text.casefold(),
            "$rt is available!" in normalized_text.casefold(),
            "next $rt" in normalized_text.casefold(),
        )
        if not any(recognized_categories):
            raise MudaeParseError("Expected at least one recognizable Mudae $tu timer category.")

        if claim_ready is not None:
            can_claim_now: bool | None = True
            claim_reset_minutes = self._duration_minutes(claim_ready.group("duration"))
        elif claim_waiting is not None:
            can_claim_now = False
            claim_reset_minutes = self._duration_minutes(claim_waiting.group("duration"))
        elif claim_interval_waiting is not None:
            can_claim_now = False
            claim_reset_minutes = self._duration_minutes(
                claim_interval_waiting.group("duration")
            )
        else:
            can_claim_now = None
            claim_reset_minutes = None
        if rolls is not None:
            rolls_reset_minutes = self._duration_minutes(rolls.group("duration"))
            rolls_reset_status = "timer"
        elif limited_rolls is not None:
            rolls_reset_minutes = self._duration_minutes(limited_rolls.group("duration"))
            rolls_reset_status = "limited_timer"
        elif vote_prompt is not None:
            rolls_reset_minutes = None
            rolls_reset_status = "vote_required"
        else:
            rolls_reset_minutes = None
            rolls_reset_status = None
        return TimerStateSnapshot(
            can_claim_now=can_claim_now,
            claim_reset_minutes=claim_reset_minutes,
            rolls_left=int(rolls.group("rolls")) if rolls else None,
            rolls_reset_minutes=rolls_reset_minutes,
            rolls_reset_stock=int(roll_stock.group("value")) if roll_stock else None,
            vote_reset_minutes=(self._duration_minutes(vote.group("duration")) if vote else None),
            daily_reset_minutes=(self._duration_minutes(daily.group("duration")) if daily else None),
            daily_kakera_ready=(
                True
                if "$dk is ready!" in normalized_text.casefold()
                else False
                if "next $dk in" in normalized_text.casefold()
                else None
            ),
            rt_available=(
                True
                if "$rt is available!" in normalized_text.casefold()
                else False
                if rtu_cooldown is not None or rtu_locked is not None or "next $rt" in normalized_text.casefold()
                else None
            ),
            can_react_kakera_now=(
                True
                if "can react to kakera right now!" in normalized_text.casefold()
                else False
                if kakera_waiting is not None
                else None
            ),
            reaction_power_percent=int(power.group("value")) if power else None,
            kakera_button_power_cost_percent=(int(power_cost.group("value")) if power_cost else None),
            soulmate_button_power_cost_percent=(
                int(soulmate_cost.group("value")) if soulmate_cost else None
            ),
            kakera_stock=self._number(stock.group("value")) if stock else None,
            gold_key_stock_remaining=(
                self._number(gold_key_stock.group("value")) if gold_key_stock else None
            ),
            gold_key_reset_minutes=(
                self._duration_minutes(gold_key_stock.group("duration")) if gold_key_stock else None
            ),
            bku_reset_probability_percent=(
                int(bku_probability.group("value")) if bku_probability else None
            ),
            oh_remaining=int(ouro.group("oh")) if ouro else None,
            oc_remaining=int(ouro.group("oc")) if ouro else None,
            oq_remaining=int(ouro.group("oq")) if ouro else None,
            oq_stored=int(ouro.group("stored")) if ouro and ouro.group("stored") else 0 if ouro else None,
            ot_remaining=int(ouro.group("ot")) if ouro else None,
            ouro_refill_minutes=(
                self._duration_minutes(ouro_refill.group("duration")) if ouro_refill else None
            ),
            rolls_reset_status=rolls_reset_status,
            rolls_per_hour_limit=(int(limited_rolls.group("limit")) if limited_rolls else None),
            rt_reset_minutes=(
                self._duration_minutes(rtu_cooldown.group("duration"))
                if rtu_cooldown
                else None
            ),
        )

    def parse_tower_state(self, text: str) -> TowerStateSnapshot:
        """Parse current level, cost, balance, and owned floors from a copied `$kt` response."""
        lines = self._lines(text)
        level = next((self._TOWER_LEVEL.search(line) for line in lines if self._TOWER_LEVEL.search(line)), None)
        next_cost = next(
            (self._TOWER_NEXT_COST.search(line) for line in lines if self._TOWER_NEXT_COST.search(line)),
            None,
        )
        balance = next((self._KAKERA_BALANCE.match(line) for line in lines if self._KAKERA_BALANCE.match(line)), None)
        if level is None or next_cost is None or balance is None:
            raise MudaeParseError("Expected a Mudae $kt response with current level, next cost, and balance.")

        built_perks: list[int] = []
        for line in lines:
            perk = self._TOWER_PERK.match(line)
            if perk is not None and "☑" in line:
                built_perks.append(int(perk.group("id")))
        return TowerStateSnapshot(
            current_level=int(level.group("level")),
            completed_towers=(int(level.group("towers")) if level.group("towers") else None),
            next_level_cost=self._number(next_cost.group("value")),
            kakera_balance=self._number(balance.group("value")),
            built_perk_ids=tuple(built_perks),
        )

    def parse_kakeraloot_state(self, text: str) -> KakeralootStateSnapshot:
        """Parse current Kakeraloot progress and balance from a copied `$lk` response."""
        lines = self._lines(text)

        no_loots = next(
            (self._NO_KAKERALOOTS.search(line) for line in lines if self._NO_KAKERALOOTS.search(line)),
            None,
        )
        if no_loots is not None:
            return KakeralootStateSnapshot(
                has_kakeraloots=False,
                status_note="No Kakeraloots bought; Mudae did not report loot statistics.",
            )

        def first_match(pattern: re.Pattern[str]) -> re.Match[str] | None:
            return next((pattern.search(line) for line in lines if pattern.search(line)), None)

        rolls = first_match(self._LOOT_ROLLS)
        disable = first_match(self._LOOT_DISABLE)
        protected_wish = first_match(self._LOOT_PROTECTED_WISH)
        mudapins = first_match(self._LOOT_MUDAPINS)
        rt = first_match(self._LOOT_RT)
        permanent_roll = first_match(self._LOOT_PERMANENT_ROLL)
        star_branch = first_match(self._LOOT_STAR_BRANCH)
        quantity = first_match(self._LOOT_QUANTITY)
        quality = first_match(self._LOOT_QUALITY)
        usage = first_match(self._LOOT_USAGE)
        balance = next(
            (self._LOOT_BALANCE.match(line) for line in lines if self._LOOT_BALANCE.match(line)),
            None,
        )
        if any(
            match is None
            for match in (
                quantity,
                quality,
                usage,
                balance,
            )
        ):
            raise MudaeParseError("Expected a complete Mudae $lk Kakeraloot stats response.")

        return KakeralootStateSnapshot(
            has_kakeraloots=True,
            rolls_stacked=int(rolls.group("value")) if rolls else None,
            disable_wa_ha_reduction=(int(disable.group("wa_ha")) if disable else None),
            disable_wg_hg_reduction=(int(disable.group("wg_hg")) if disable else None),
            protected_wish_level=(int(protected_wish.group("level")) if protected_wish else None),
            protected_wish_denominator=(
                self._number(protected_wish.group("denominator"))
                if protected_wish
                else None
            ),
            mudapins=int(mudapins.group("value")) if mudapins else None,
            rt_cooldown_reduction_hours=(int(rt.group("value")) if rt else None),
            permanent_roll_bonus=(int(permanent_roll.group("value")) if permanent_roll else None),
            star_branches=(int(star_branch.group("branches")) if star_branch else None),
            starwish_slots_from_branches=(
                int(star_branch.group("slots")) if star_branch else None
            ),
            quantity_level=int(quantity.group("value")),
            quality_level=int(quality.group("value")),
            usage_count=self._number(usage.group("value")),
            kakera_balance=self._number(balance.group("value")),
        )

    def parse_sphere_result(self, text: str) -> SphereResultSnapshot:
        """Parse the payout and stock summary from one `$oq` response."""
        lines = self._lines(text)
        clicks = next(
            (self._SPHERE_CLICKS.search(line) for line in lines if self._SPHERE_CLICKS.search(line)),
            None,
        )
        goal = next(
            (self._SPHERE_GOAL.search(line) for line in lines if self._SPHERE_GOAL.search(line)),
            None,
        )
        gains: list[SphereGain] = []
        total_gained: int | None = None
        stock: int | None = None
        for line in lines:
            match = self._SPHERE_GAIN.match(line)
            if match is None:
                continue
            amount = self._number(match.group("amount"))
            marker = match.group("marker").casefold()
            if marker == "sp":
                total_gained = amount
            else:
                gains.append(
                    SphereGain(
                        sphere_type=marker.removeprefix("sp"),
                        amount=amount,
                        is_free=match.group("free") is not None,
                    )
                )
            if match.group("stock") is not None:
                stock = self._number(match.group("stock"))

        if total_gained is None and not gains:
            raise MudaeParseError("Expected a Mudae $oq response with sphere gains.")
        return SphereResultSnapshot(
            clicks_available=int(clicks.group("clicks")) if clicks else None,
            click_window_minutes=int(clicks.group("minutes")) if clicks else None,
            purple_target=int(goal.group("target")) if goal else None,
            purple_total=int(goal.group("total")) if goal else None,
            gains=tuple(gains),
            total_gained=(
                total_gained
                if total_gained is not None
                else sum(gain.amount for gain in gains)
            ),
            stock=stock,
        )

    def parse_kakeraloot_settings(self, text: str) -> KakeralootSettingsSnapshot:
        """Parse server-configurable and universal Kakeraloot costs from `$infokl`."""
        normalized_text = "\n".join(
            re.sub(r"[*_]", "", line) for line in self._lines(text)
        )
        loot_cost = self._LOOT_COST.search(normalized_text)
        upgrade_cost = self._LOOT_UPGRADE_COST.search(normalized_text)
        if loot_cost is None or upgrade_cost is None:
            raise MudaeParseError("Expected a Mudae $infokl response with Kakeraloot cost details.")
        return KakeralootSettingsSnapshot(
            loot_cost=self._number(loot_cost.group("value")),
            quantity_quality_base_cost=self._number(upgrade_cost.group("base")),
            quantity_quality_level_increment=self._number(upgrade_cost.group("increment")),
        )

    def parse_profile(self, text: str) -> ProfileSnapshot:
        """Parse account progress totals from a copied `$profile` response."""
        lines = [re.sub(r"\*", "", line) for line in self._lines(text)]

        collection = next(
            (re.search(
                r"Collection size:\s*(?P<size>[\d,]+)\s*"
                r"\((?P<female>\d+)%\s*:female:\s*"
                r"(?P<male>\d+)%\s*:male:\s*\)",
                line,
                re.IGNORECASE,
            ) for line in lines if "collection size:" in line.casefold()),
            None,
        )
        pokedex = next(
            (re.search(
                r"Pok(?:é|e)dex:\s*(?P<count>[\d,]+)\s+Pok(?:é|e)mon(?P<items>.*)$",
                line,
                re.IGNORECASE,
            ) for line in lines if "dex:" in line.casefold()),
            None,
        )
        if pokedex is None:
            pokedex = next(
                (re.search(
                    r"Pok.*?dex:\s*(?P<count>[\d,]+)\s+Pok.*?mon(?P<items>.*)$",
                    line,
                    re.IGNORECASE,
                ) for line in lines if "dex:" in line.casefold()),
                None,
            )
        mudapins = next(
            (re.search(
                r"Mudapins:\s*(?P<collected>[\d,]+)\s*/\s*(?P<total>[\d,]+)",
                line,
                re.IGNORECASE,
            ) for line in lines if line.casefold().startswith("mudapins:")),
            None,
        )
        kakera_balance = next(
            (re.match(r"^(?P<value>[\d,]+)\s*:kakera:\s*$", line, re.IGNORECASE)
             for line in lines if re.match(r"^[\d,]+\s*:kakera:", line, re.IGNORECASE)),
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
            (re.match(r"^(?P<value>[\d,]+)\s*:sp:\s*$", line, re.IGNORECASE)
             for line in lines if re.match(r"^[\d,]+\s*:sp:\s*$", line, re.IGNORECASE)),
            None,
        )
        if collection is None:
            raise MudaeParseError("Expected a complete Mudae $profile response with account totals.")

        def marker_counts(line: str, prefix: str) -> dict[str, int]:
            return {
                f":{marker}:": self._number(value)
                for value, marker in re.findall(
                    rf"([\d,]+)\s*x\s*:({prefix}[A-Za-z0-9_]*)\s*:", line, re.IGNORECASE
                )
            }

        reacts_index = next(
            (index for index, line in enumerate(lines) if line.casefold() == "reacts:"),
            None,
        )
        reactions_observed = reacts_index is not None
        reacts = None
        if reacts_index is not None:
            if reacts_index + 1 >= len(lines):
                raise MudaeParseError(
                    "Expected a Mudae $profile reactions section with reaction counts."
                )
            reacts = marker_counts(lines[reacts_index + 1], "kakera")
            if not reacts:
                raise MudaeParseError(
                    "Expected a Mudae $profile reactions section with reaction counts."
                )
        sphere_index = next(
            (index for index, line in enumerate(lines) if re.match(r"^[\d,]+\s*:sp:\s*$", line, re.IGNORECASE)),
            None,
        )
        spheres = None
        if sphere_index is not None and sphere_index + 1 < len(lines):
            parsed_spheres = marker_counts(lines[sphere_index + 1], "sp")
            spheres = parsed_spheres or None
        badge_line = next(
            (line for line in reversed(lines) if any(
                marker in line.casefold()
                for marker in (":bronzeiv:", ":silveriv:", ":diamondiv:", ":diamondi:")
            )),
            None,
        )
        displayed_badges = (
            tuple(f":{marker}:" for marker in re.findall(r":([A-Za-z0-9_]+):", badge_line))
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
            mudapins_total=(self._number(mudapins.group("total")) if mudapins else None),
            kakera_balance=(
                self._number(kakera_balance.group("value")) if kakera_balance else None
            ),
            bronze_keys=key_counts.get("bronzekey"),
            silver_keys=key_counts.get("silverkey"),
            gold_keys=key_counts.get("goldkey"),
            sphere_stock=(self._number(sphere_stock.group("value")) if sphere_stock else None),
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
        lines = self._lines(text)

        def first(pattern: re.Pattern[str]) -> re.Match[str] | None:
            return next((pattern.search(line) for line in lines if pattern.search(line)), None)

        premium = first(self._SERVER_PREMIUM)
        claim_reset = first(self._SETTING_CLAIM_RESET)
        reset_minute = first(self._SETTING_RESET_MINUTE)
        reset_shift = first(self._SETTING_RESET_SHIFT)
        rolls = first(self._SETTING_ROLLS)
        timer = first(self._SETTING_TIMER)
        rare = first(self._SETTING_RARE)
        kakera_bonus = first(self._SETTING_KAKERA_BONUS)
        sphere_bonus = first(self._SETTING_SPHERE_BONUS)
        game_mode = first(self._SETTING_GAMEMODE)
        channel_instance = first(self._SETTING_CHANNEL_INSTANCE)
        required_settings = {
            "premium": premium,
            "claim reset": claim_reset,
            "reset minute": reset_minute,
            "reset shift": reset_shift,
            "rolls per hour": rolls,
            "claim reaction timer": timer,
            "rarity multiplier": rare,
            "Kakera bonus": kakera_bonus,
            "sphere bonus": sphere_bonus,
            "game mode": game_mode,
            "channel instance": channel_instance,
        }
        missing_settings = tuple(name for name, match in required_settings.items() if match is None)
        if missing_settings:
            missing = ", ".join(missing_settings)
            raise MudaeParseError(
                "Expected a complete Mudae $settings response with core server rules; "
                f"missing: {missing}."
            )

        metrics: list[ServerSettingMetric] = []
        for line in lines:
            setting = self._SETTING_LINE.match(line)
            if setting is not None:
                metrics.append(
                    ServerSettingMetric(
                        label=setting.group("label").strip(),
                        value=setting.group("value").strip(),
                    )
                )
        prefix = next((metric.value for metric in metrics if metric.label.casefold() == "prefix"), None)
        language = next((metric.value for metric in metrics if metric.label.casefold() == "lang"), None)
        if prefix is None or language is None:
            raise MudaeParseError("Expected Prefix and Lang in the Mudae $settings response.")
        return ServerSettingsSnapshot(
            server_premium="not premium" not in premium.group("status").casefold(),
            prefix=prefix,
            language=language,
            claim_reset_minutes=int(claim_reset.group("value")),
            reset_minute=reset_minute.group("value"),
            reset_shift_minutes=int(reset_shift.group("value")),
            rolls_per_hour=int(rolls.group("value")),
            claim_reaction_expiry_seconds=int(timer.group("value")),
            claimed_character_rarity_multiplier=int(rare.group("value")),
            kakera_bonus_percent=int(kakera_bonus.group("value")),
            sphere_bonus_percent=int(sphere_bonus.group("value")),
            game_mode=int(game_mode.group("value")),
            channel_instance=int(channel_instance.group("value")),
            metrics=tuple(metrics),
        )

    def _first_number(self, lines: list[str], pattern: re.Pattern[str]) -> int | None:
        return first_named_rank(lines, pattern)
