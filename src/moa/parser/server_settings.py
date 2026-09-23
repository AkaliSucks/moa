"""Parse copied Mudae server-settings responses."""

from collections.abc import Callable
import re

from moa.models.character import ServerSettingMetric, ServerSettingsSnapshot


class ServerSettingsParser:
    """Parse core server rules and visible options from a ``$settings`` reply."""

    _SERVER_PREMIUM = re.compile(r"Server\s+(?P<status>not\s+premium|premium)", re.IGNORECASE)

    _BOLD_VALUE = re.compile(r"\*\*([^*\r\n]+)\*\*")

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

    _SETTING_LABELS = frozenset(
        {
            "prefix",
            "lang",
            "claim reset",
            "exact minute of the reset",
            "reset shifted",
            "rolls per hour",
            "time before the claim reaction expires",
            "spawn rarity multiplier for already claimed characters",
            "kakera bonus",
            "% kakera bonus",
            "sphere bonus",
            "% sphere bonus",
            "game mode",
            "this channel instance",
        }
    )

    _CORE_SETTING_LABELS = {
        _SETTING_CLAIM_RESET: frozenset({"claim reset"}),
        _SETTING_RESET_MINUTE: frozenset({"exact minute of the reset"}),
        _SETTING_RESET_SHIFT: frozenset({"reset shifted"}),
        _SETTING_ROLLS: frozenset({"rolls per hour"}),
        _SETTING_TIMER: frozenset({"time before the claim reaction expires"}),
        _SETTING_RARE: frozenset({"spawn rarity multiplier for already claimed characters"}),
        _SETTING_KAKERA_BONUS: frozenset({"kakera bonus", "% kakera bonus"}),
        _SETTING_SPHERE_BONUS: frozenset({"sphere bonus", "% sphere bonus"}),
        _SETTING_GAMEMODE: frozenset({"game mode"}),
        _SETTING_CHANNEL_INSTANCE: frozenset({"this channel instance"}),
    }

    def __init__(
        self,
        error_type: type[ValueError],
        lines_converter: Callable[[str], list[str]],
    ) -> None:
        self._error_type = error_type
        self._lines = lines_converter

    def parse(self, text: str) -> ServerSettingsSnapshot:
        """Parse one copied Mudae ``$settings`` response."""
        lines = [self._normalize_setting_value(line) for line in self._lines(text)]

        def first(pattern: re.Pattern[str]) -> re.Match[str] | None:
            labels = self._CORE_SETTING_LABELS.get(pattern)
            for line in lines:
                if labels is not None:
                    setting = self._SETTING_LINE.match(line)
                    if setting is None or setting.group("label").strip().casefold() not in labels:
                        continue
                match = pattern.search(line)
                if match is not None:
                    return match
            return None

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
        assert premium is not None
        assert claim_reset is not None
        assert reset_minute is not None
        assert reset_shift is not None
        assert rolls is not None
        assert timer is not None
        assert rare is not None
        assert kakera_bonus is not None
        assert sphere_bonus is not None
        assert game_mode is not None
        assert channel_instance is not None

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

    @classmethod
    def _normalize_setting_value(cls, line: str) -> str:
        """Remove bold delimiters only from values on recognized settings rows."""
        setting = cls._SETTING_LINE.match(line)
        if setting is None or setting.group("label").strip().casefold() not in cls._SETTING_LABELS:
            return line
        value_start, value_end = setting.span("value")
        value = line[value_start:value_end]
        normalized_value = cls._BOLD_VALUE.sub(r"\1", value)
        return line[:value_start] + normalized_value + line[value_end:]
