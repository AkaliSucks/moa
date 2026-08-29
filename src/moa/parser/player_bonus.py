"""Parse copied player-bonus metrics from Mudae."""

from collections.abc import Callable
import re

from moa.models.character import PlayerBonusMetric, PlayerBonusSnapshot


class PlayerBonusParser:
    """Parse labelled player modifiers from a copied Mudae ``$bonus`` response."""

    _BONUS_METRIC = re.compile(r"^(?P<label>[^:]+):\s*(?P<detail>\S.*)$")

    def __init__(
        self,
        error_type: type[ValueError],
        lines_converter: Callable[[str], list[str]],
    ) -> None:
        self._error_type = error_type
        self._lines = lines_converter

    def parse(self, text: str) -> PlayerBonusSnapshot:
        """Parse metrics without requiring Mudae's ``Player Bonuses`` header."""
        metrics: list[PlayerBonusMetric] = []
        for line in self._lines(text):
            content = line.split(" · ", 1)[-1]
            match = self._BONUS_METRIC.match(content)
            if match is None:
                continue
            label = match.group("label").strip()
            if not label:
                continue
            metrics.append(
                PlayerBonusMetric(
                    label=label,
                    detail=match.group("detail").strip(),
                )
            )

        if not metrics:
            raise self._error_type("No player bonus metrics found in the Mudae $bonus output.")

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
