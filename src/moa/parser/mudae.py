"""Parsers for copied text from Mudae bot messages.

The parsers deliberately operate on text copied by a user or an authorized
companion bot. They do not automate Discord accounts or invoke Mudae commands.
"""

import re

from moa.models.character import (
    CharacterDetails,
    BadgeLevel,
    KakeraStateSnapshot,
    KakeralootStateSnapshot,
    TowerStateSnapshot,
    DisableListEntry,
    DisableListSnapshot,
    HaremKeyEntry,
    HaremKeyPage,
    PlayerBonusMetric,
    PlayerBonusSnapshot,
    RankedCharacter,
    RollObservation,
    TopPage,
    UnavailableCharacter,
    UnavailableCharacterPage,
    WishlistEntry,
    WishlistSnapshot,
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
        r"^(?P<roulette>.+?)(?:\s+roulette)?(?:\s*[\u00b7\u2022]\s*|\s+)"
        r"(?P<value>[\d,]+)(?:\D.*)?$",
        re.IGNORECASE,
    )
    _CLAIM_RANK = re.compile(r"^Claim Rank:\s*#(?P<rank>[\d,]+)$", re.IGNORECASE)
    _LIKE_RANK = re.compile(r"^Like Rank:\s*#(?P<rank>[\d,]+)$", re.IGNORECASE)
    _ROLL_CLAIMS = re.compile(r"^Claims:\s*#(?P<rank>[\d,]+)$", re.IGNORECASE)
    _KAKERA = re.compile(r"^(?P<value>[\d,]+):kakera:$", re.IGNORECASE)
    _HAREM_KEY_ENTRY = re.compile(
        r"^(?P<name>.+?)\s*[\u00b7\u2022]\s*:(?P<key_type>[a-z]+)key:\s*"
        r"\((?P<key_count>\d+)\)(?:\s+(?P<kakera_value>[\d,]+)\s+ka)?$",
        re.IGNORECASE,
    )
    _TOTAL_HAREM_VALUE = re.compile(
        r"^Total value:\s*(?P<value>[\d,]+)(?::kakera:|\s+ka)?$", re.IGNORECASE
    )
    _GENDER = re.compile(r"\s+:(?P<gender>female|male):\s*$", re.IGNORECASE)
    _BONUS_METRIC = re.compile(r"^(?P<label>[^:]+):\s*(?P<detail>.+)$")
    _WISHLIST_HEADER = re.compile(
        r"Wishlist\s*-\s*(?P<wishlist_count>\d+)\s*/\s*(?P<wishlist_capacity>\d+)\s*\$wl,\s*"
        r"(?P<starwish_count>\d+)\s*/\s*(?P<starwish_capacity>\d+)\s*\$sw",
        re.IGNORECASE,
    )
    _DISABLELIST_HEADER = re.compile(
        r"Disablelist\s*\((?P<used>\d+)\s*/\s*(?P<capacity>\d+)\)", re.IGNORECASE
    )
    _DISABLELIST_TOTALS = re.compile(
        r"(?P<total>[\d,]+)\s+disabled.*?(?P<wa>[\d,]+)\s*\$wa.*?"
        r"(?P<ha>[\d,]+)\s*\$ha.*?(?P<wg>[\d,]+)\s*\$wg.*?"
        r"(?P<hg>[\d,]+)\s*\$hg",
        re.IGNORECASE,
    )
    _POOL_LIMIT = re.compile(
        r"Pool limit reached:\s*(?P<limit>[\d,]+)\s+\$(?P<roulette>wa|ha|wg|hg)",
        re.IGNORECASE,
    )
    _DISABLELIST_ENTRY = re.compile(r"^(?P<name>.+?)\s*\((?P<count>[\d,]+)\)$")
    _TOPX_ENTRY = re.compile(
        r"^#(?P<rank>[\d,]+)\s+-\s+(?P<name>.+?)\s+-\s+(?P<series>.+?)"
        r"\s*🚫(?:\s*\((?P<reason>[^)]+)\))?$"
    )
    _KAKERA_BALANCE = re.compile(r"^You have\s+(?P<value>[\d,]+):kakera:!?$", re.IGNORECASE)
    _BADGE_LEVEL = re.compile(
        r"(?P<name>Bronze|Silver|Gold|Sapphire|Ruby|Emerald|Diamond)\s+"
        r"(?P<level>I|II|III|IV)\s*[·\u00b7]\s*(?P<status>.+)$",
        re.IGNORECASE,
    )
    _TOWER_LEVEL = re.compile(
        r"current level is.*?tow(?P<level>\d+).*?\(\+\s*(?P<towers>\d+)\s+tower",
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
    _LOOT_BALANCE = re.compile(r"^(?P<value>[\d,]+):kakera:$", re.IGNORECASE)
    _NO_KAKERALOOTS = re.compile(r"No kakeraloots bought", re.IGNORECASE)

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

    def parse_harem_key_page(self, text: str) -> HaremKeyPage:
        """Parse one copied keyed-harem page, with optional current Kakera values."""
        lines = self._lines(text)
        page = next((self._PAGE.match(line) for line in lines if self._PAGE.match(line)), None)
        total = next(
            (self._TOTAL_HAREM_VALUE.match(line) for line in lines if self._TOTAL_HAREM_VALUE.match(line)),
            None,
        )
        entries: list[HaremKeyEntry] = []

        for line in lines:
            entry = self._HAREM_KEY_ENTRY.match(line)
            if entry is None:
                continue
            entries.append(
                HaremKeyEntry(
                    name=entry.group("name").strip(),
                    key_type=entry.group("key_type").lower(),
                    key_count=int(entry.group("key_count")),
                    kakera_value=(
                        self._number(entry.group("kakera_value"))
                        if entry.group("kakera_value")
                        else None
                    ),
                )
            )

        if not entries:
            raise MudaeParseError("No keyed harem entries found in the Mudae $mmy= output.")

        return HaremKeyPage(
            page_number=int(page.group("page")) if page else None,
            page_count=int(page.group("pages")) if page else None,
            entries=tuple(entries),
            total_harem_value=self._number(total.group("value")) if total else None,
        )

    def parse_player_bonus(self, text: str) -> PlayerBonusSnapshot:
        """Parse stable player modifiers from a copied Mudae `$bonus` message."""
        metrics: list[PlayerBonusMetric] = []
        for line in self._lines(text):
            content = line.split(" · ", 1)[-1]
            match = self._BONUS_METRIC.match(content)
            if match is not None:
                metrics.append(
                    PlayerBonusMetric(
                        label=match.group("label").strip(), detail=match.group("detail").strip()
                    )
                )

        if not metrics:
            raise MudaeParseError("No player bonus metrics found in the Mudae $bonus output.")

        details = {metric.label.casefold(): metric.detail for metric in metrics}
        return PlayerBonusSnapshot(
            metrics=tuple(metrics),
            rolls_per_hour_bonus=self._bonus_number(details, "rolls per hour"),
            wishlist_slot_bonus=self._bonus_number(details, "wishlist slots"),
            wish_spawn_bonus_percent=self._bonus_number(details, "spawn bonus for wishes"),
            starwish_spawn_bonus_percent=self._bonus_number(
                details, "additional % spawn bonus for $starwish"
            ),
            starwish_total_spawn_bonus_percent=self._parenthesized_total(
                details.get("additional % spawn bonus for $starwish")
            ),
            starwish_slot_bonus=self._bonus_number(details, "starwish slots"),
            additional_wish_key_chance_percent=self._bonus_number(
                details, "chance to get an additional key on wishes"
            ),
            kakera_max_power_percent=self._bonus_number(details, "kakera max power"),
            kakera_button_power_cost_percent=self._bonus_number(
                details, "power cost per kakera button"
            ),
            starwish_kakera_button_bonus_percent=self._bonus_number(
                details, "additional bonus for kakera buttons on starwishes"
            ),
            light_kakera_minimum=self._light_kakera_bound(details, 0),
            light_kakera_maximum=self._light_kakera_bound(details, 1),
        )

    def parse_wishlist(self, text: str) -> WishlistSnapshot:
        """Parse one copied Mudae `$wl` response, including Starwish markers."""
        lines = self._lines(text)
        header = next(
            (self._WISHLIST_HEADER.search(line) for line in lines if self._WISHLIST_HEADER.search(line)),
            None,
        )
        if header is None:
            raise MudaeParseError("Expected a Mudae $wl header with $wl and $sw capacities.")

        entries: list[WishlistEntry] = []
        header_line = header.group(0)
        for line in lines:
            if header_line in line:
                continue
            name = (
                line.replace("✅", "")
                .replace("⭐", "")
                .replace(":kakera:", "")
                .strip()
                .strip("*")
                .strip()
            )
            if not name:
                continue
            entries.append(
                WishlistEntry(
                    name=name,
                    is_starwish="⭐" in line,
                    is_owned_marker_present="✅" in line,
                    kakera_marker_present=":kakera:" in line,
                )
            )

        if not entries:
            raise MudaeParseError("No wishlist entries found in the Mudae $wl output.")
        return WishlistSnapshot(
            wishlist_count=int(header.group("wishlist_count")),
            wishlist_capacity=int(header.group("wishlist_capacity")),
            starwish_count=int(header.group("starwish_count")),
            starwish_capacity=int(header.group("starwish_capacity")),
            entries=tuple(entries),
        )

    def parse_disablelist(self, text: str) -> DisableListSnapshot:
        """Parse account-specific disable-list settings from a copied `$dl` reply."""
        lines = self._lines(text)
        header = next(
            (
                self._DISABLELIST_HEADER.search(line)
                for line in lines
                if self._DISABLELIST_HEADER.search(line)
            ),
            None,
        )
        totals = next(
            (
                self._DISABLELIST_TOTALS.search(line)
                for line in lines
                if self._DISABLELIST_TOTALS.search(line)
            ),
            None,
        )
        if header is None or totals is None:
            raise MudaeParseError("Expected a Mudae $dl header and disabled-pool totals.")

        limits: dict[str, int] = {}
        entries: list[DisableListEntry] = []
        for line in lines:
            pool_limit = self._POOL_LIMIT.search(line)
            if pool_limit is not None:
                limits[pool_limit.group("roulette").lower()] = self._number(pool_limit.group("limit"))
                continue
            entry = self._DISABLELIST_ENTRY.match(line)
            if entry is None:
                continue
            entries.append(
                DisableListEntry(
                    name=entry.group("name").strip(),
                    disabled_count=self._number(entry.group("count")),
                )
            )

        return DisableListSnapshot(
            slots_used=int(header.group("used")),
            slots_capacity=int(header.group("capacity")),
            total_disabled=self._number(totals.group("total")),
            disabled_wa=self._number(totals.group("wa")),
            disabled_ha=self._number(totals.group("ha")),
            disabled_wg=self._number(totals.group("wg")),
            disabled_hg=self._number(totals.group("hg")),
            wa_pool_limit=limits.get("wa"),
            ha_pool_limit=limits.get("ha"),
            western_disabled=any("western animanga series are completely disabled" in line.casefold() for line in lines),
            irl_disabled=any("irl series are completely disabled" in line.casefold() for line in lines),
            entries=tuple(entries),
        )

    def parse_unavailable_characters(self, text: str) -> UnavailableCharacterPage:
        """Parse the currently unrollable characters listed by Mudae `$topx`."""
        lines = self._lines(text)
        header = next((self._TOP_HEADER.search(line) for line in lines if self._TOP_HEADER.search(line)), None)
        page = next((self._PAGE.match(line) for line in lines if self._PAGE.match(line)), None)
        characters: list[UnavailableCharacter] = []
        for line in lines:
            entry = self._TOPX_ENTRY.match(line)
            if entry is None:
                continue
            characters.append(
                UnavailableCharacter(
                    name=entry.group("name").removesuffix(" 💞").strip(),
                    series=entry.group("series").strip(),
                    claim_rank=self._number(entry.group("rank")),
                    reason=entry.group("reason"),
                )
            )
        if not characters:
            raise MudaeParseError("No unavailable characters found in the Mudae $topx output.")
        return UnavailableCharacterPage(
            limit=self._number(header.group("limit")) if header else None,
            page_number=int(page.group("page")) if page else None,
            page_count=int(page.group("pages")) if page else None,
            characters=tuple(characters),
        )

    def parse_kakera_state(self, text: str) -> KakeraStateSnapshot:
        """Parse current Kakera balance and badge levels from a copied `$k` response."""
        lines = self._lines(text)
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
            completed_towers=int(level.group("towers")),
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
                rolls,
                disable,
                protected_wish,
                mudapins,
                rt,
                permanent_roll,
                star_branch,
                quantity,
                quality,
                usage,
                balance,
            )
        ):
            raise MudaeParseError("Expected a complete Mudae $lk Kakeraloot stats response.")

        return KakeralootStateSnapshot(
            has_kakeraloots=True,
            rolls_stacked=int(rolls.group("value")),
            disable_wa_ha_reduction=int(disable.group("wa_ha")),
            disable_wg_hg_reduction=int(disable.group("wg_hg")),
            protected_wish_level=int(protected_wish.group("level")),
            protected_wish_denominator=self._number(protected_wish.group("denominator")),
            mudapins=int(mudapins.group("value")),
            rt_cooldown_reduction_hours=int(rt.group("value")),
            permanent_roll_bonus=int(permanent_roll.group("value")),
            star_branches=int(star_branch.group("branches")),
            starwish_slots_from_branches=int(star_branch.group("slots")),
            quantity_level=int(quantity.group("value")),
            quality_level=int(quality.group("value")),
            usage_count=self._number(usage.group("value")),
            kakera_balance=self._number(balance.group("value")),
        )

    @staticmethod
    def _bonus_number(details: dict[str, str], label: str) -> int | None:
        detail = details.get(label)
        if detail is None:
            return None
        match = re.search(r"[+-]?(?P<value>\d+)(?:%|h)?", detail)
        return int(match.group("value")) if match else None

    @staticmethod
    def _parenthesized_total(detail: str | None) -> int | None:
        if detail is None:
            return None
        match = re.search(r"\(=\s*(?P<value>\d+)%\)", detail)
        return int(match.group("value")) if match else None

    @staticmethod
    def _light_kakera_bound(details: dict[str, str], index: int) -> int | None:
        detail = details.get("random kakera per light kakera")
        if detail is None:
            return None
        match = re.match(r"(?P<minimum>\d+)\s*-\s*(?P<maximum>\d+)", detail)
        if match is None:
            return None
        return int(match.group(("minimum", "maximum")[index]))

    def _first_number(self, lines: list[str], pattern: re.Pattern[str]) -> int | None:
        match = next((pattern.match(line) for line in lines if pattern.match(line)), None)
        return self._number(match.group("rank")) if match else None
