"""Parse copied Mudae action-timer responses."""

from collections.abc import Callable
import re

from moa.models.character import TimerStateSnapshot


class TimerStateParser:
    """Parse whichever action categories are currently visible in ``$tu``."""

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

    def __init__(
        self,
        error_type: type[ValueError],
        lines_converter: Callable[[str], list[str]],
        duration_converter: Callable[[str], int],
        number_converter: Callable[[str], int],
    ) -> None:
        self._error_type = error_type
        self._lines = lines_converter
        self._duration_minutes = duration_converter
        self._number = number_converter

    def parse(self, text: str) -> TimerStateSnapshot:
        """Parse one copied Mudae timer response."""
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
        normalized_casefold = normalized_text.casefold()
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
            "$dk is ready!" in normalized_casefold,
            "next $dk in" in normalized_casefold,
            "$rt is available!" in normalized_casefold,
            "next $rt" in normalized_casefold,
        )
        if not any(recognized_categories):
            raise self._error_type("Expected at least one recognizable Mudae $tu timer category.")

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
                if "$dk is ready!" in normalized_casefold
                else False
                if "next $dk in" in normalized_casefold
                else None
            ),
            rt_available=(
                True
                if "$rt is available!" in normalized_casefold
                else False
                if rtu_cooldown is not None or rtu_locked is not None or "next $rt" in normalized_casefold
                else None
            ),
            can_react_kakera_now=(
                True
                if "can react to kakera right now!" in normalized_casefold
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
