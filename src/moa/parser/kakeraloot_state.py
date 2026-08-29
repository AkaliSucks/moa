"""Parse copied Kakeraloot state from Mudae."""

from collections.abc import Callable
import re

from moa.models.character import KakeralootStateSnapshot


class KakeralootStateParser:
    """Parse the account-scoped progress and balance shown by ``$lk``."""

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
    _LOOT_PERMANENT_ROLL = re.compile(
        r"\+(?P<value>\d+)\s+permanent roll", re.IGNORECASE
    )
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
    _ERROR_MESSAGE = "Expected a complete Mudae $lk Kakeraloot stats response."
    _NO_LOOTS_NOTE = "No Kakeraloots bought; Mudae did not report loot statistics."

    def __init__(
        self,
        error_type: type[ValueError],
        lines_converter: Callable[[str], list[str]],
        number_converter: Callable[[str], int],
    ) -> None:
        self._error_type = error_type
        self._lines = lines_converter
        self._number = number_converter

    def parse(self, text: str) -> KakeralootStateSnapshot:
        """Parse one copied Mudae ``$lk`` response."""
        lines = self._lines(text)

        no_loots = next(
            (match for line in lines if (match := self._NO_KAKERALOOTS.search(line))),
            None,
        )
        if no_loots is not None:
            return KakeralootStateSnapshot(
                has_kakeraloots=False,
                status_note=self._NO_LOOTS_NOTE,
            )

        def first_match(pattern: re.Pattern[str]) -> re.Match[str] | None:
            return next(
                (match for line in lines if (match := pattern.search(line))),
                None,
            )

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
            (match for line in lines if (match := self._LOOT_BALANCE.match(line))),
            None,
        )
        if any(match is None for match in (quantity, quality, usage, balance)):
            raise self._error_type(self._ERROR_MESSAGE)

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
