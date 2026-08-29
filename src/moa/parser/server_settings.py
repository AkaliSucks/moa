"""Parse copied Mudae server-settings responses."""

from collections.abc import Callable
import re

from moa.models.character import ServerSettingMetric, ServerSettingsSnapshot


class ServerSettingsParser:
    """Parse core server rules and visible options from a ``$settings`` reply."""

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

    def __init__(
        self,
        error_type: type[ValueError],
        lines_converter: Callable[[str], list[str]],
    ) -> None:
        self._error_type = error_type
        self._lines = lines_converter

    def parse(self, text: str) -> ServerSettingsSnapshot:
        """Parse one copied Mudae ``$settings`` response."""
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
            raise self._error_type(
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
            raise self._error_type("Expected Prefix and Lang in the Mudae $settings response.")
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
