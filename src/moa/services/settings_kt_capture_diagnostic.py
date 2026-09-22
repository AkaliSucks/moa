"""Bounded, capture-only evidence for one explicitly selected Mudae command family.

Only allowlisted booleans, enums, and deterministic aliases leave this module.
Gateway text is evaluated in memory and is never placed in a durable record.
This is a shadow evaluation of the listener's documented pending-context choice;
it does not construct a listener, repository, or import workflow.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Mapping

from moa.commands import COMMAND_REGISTRY, CommandCapability, InvocationSource
from moa.parser.message_router import MudaeMessageRouter
from moa.parser.mudae import MudaeParseError, MudaeTextParser
from moa.parser.server_settings import ServerSettingsParser
from moa.parser.tower_state import TowerStateParser


@dataclass(frozen=True, slots=True)
class _Pending:
    actor_id: str
    source: str
    command_alias: str
    captured_at: float


@dataclass(frozen=True, slots=True)
class _CustomEmojiShape:
    name: str
    animated: bool
    parser_start: int
    parser_end: int


@dataclass(frozen=True, slots=True)
class _ParserInputPart:
    source: str
    embed_index: int | None
    field_index: int | None
    parser_start: int
    parser_end: int
    custom_emojis: tuple[_CustomEmojiShape, ...]


@dataclass(frozen=True, slots=True)
class _ParserInput:
    text: str
    parts: tuple[_ParserInputPart, ...]
    seam_consistent: bool


@dataclass(frozen=True, slots=True)
class _ParserLine:
    index: int
    text: str
    ending: str
    start: int
    end: int


class SettingsKtCaptureDiagnostic:
    """Project one family of Gateway events into privacy-safe decision evidence."""

    SCHEMA_VERSION = "moa.settings-kt-capture-diagnostic.v2"
    MAX_RECORDS = 128
    MAX_INPUT_CHARS = 32768
    MAX_STRUCTURAL_LINES = 16
    MAX_STRUCTURAL_LINE_CHARS = 512
    MAX_STRUCTURAL_TOTAL_CHARS = 4096
    MAX_CODEPOINTS_PER_LINE = 64
    MAX_BOUNDARIES = 32
    MAX_CUSTOM_EMOJI_TRANSFORMS = 32
    CONTEXT_TTL_SECONDS = 300.0
    _CUSTOM_EMOJI = re.compile(
        r"<(?P<animated>a?):(?P<name>[A-Za-z0-9_]{1,32}):(?P<id>\d+)>"
    )
    _COPIED_EMOJI = re.compile(r":[A-Za-z0-9_]{1,32}:")
    _NUMBER = re.compile(r"\d[\d,]*")
    _WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
    _LINE_ENDINGS = ("\r\n", "\r", "\n", "\x85", "\u2028", "\u2029", "\v", "\f")
    _STRUCTURAL_WHITESPACE = frozenset({"\u200b", "\u200c", "\u200d", "\ufeff"})
    _FAMILY_KIND = {"settings": "settings", "kt": "towerstate"}
    _SETTINGS_FIELDS = {
        "premium": ServerSettingsParser._SERVER_PREMIUM,
        "claim_reset": ServerSettingsParser._SETTING_CLAIM_RESET,
        "reset_minute": ServerSettingsParser._SETTING_RESET_MINUTE,
        "reset_shift": ServerSettingsParser._SETTING_RESET_SHIFT,
        "rolls": ServerSettingsParser._SETTING_ROLLS,
        "claim_timer": ServerSettingsParser._SETTING_TIMER,
        "rarity": ServerSettingsParser._SETTING_RARE,
        "kakera_bonus": ServerSettingsParser._SETTING_KAKERA_BONUS,
        "sphere_bonus": ServerSettingsParser._SETTING_SPHERE_BONUS,
        "game_mode": ServerSettingsParser._SETTING_GAMEMODE,
        "channel_instance": ServerSettingsParser._SETTING_CHANNEL_INSTANCE,
    }
    _SETTINGS_RELEVANT_LINES = (
        (
            "server_settings_marker",
            re.compile(r"server\s+settings", re.IGNORECASE),
            frozenset({"server", "settings"}),
        ),
        (
            "premium_marker",
            re.compile(r"server\s+(?:not\s+)?premium", re.IGNORECASE),
            frozenset({"server", "not", "premium"}),
        ),
        (
            "prefix",
            re.compile(r"\bprefix\s*:", re.IGNORECASE),
            frozenset({"prefix"}),
        ),
        (
            "lang",
            re.compile(r"\blang\s*:", re.IGNORECASE),
            frozenset({"lang"}),
        ),
        (
            "claim_reset",
            re.compile(r"\bclaim\s+reset\s*:", re.IGNORECASE),
            frozenset({"claim", "reset", "every", "min", "setclaim"}),
        ),
        (
            "reset_minute",
            re.compile(r"exact\s+minute\s+of\s+the\s+reset\s*:", re.IGNORECASE),
            frozenset({"exact", "minute", "of", "the", "reset", "setinterval"}),
        ),
        (
            "reset_shift",
            re.compile(r"\breset\s+shifted\s*:", re.IGNORECASE),
            frozenset({"reset", "shifted", "by", "min", "shifthour"}),
        ),
        (
            "rolls",
            re.compile(r"\brolls\s+per\s+hour\s*:", re.IGNORECASE),
            frozenset({"rolls", "per", "hour", "setrolls"}),
        ),
        (
            "claim_timer",
            re.compile(r"time\s+before\s+the\s+claim\s+reaction\s+expires\s*:", re.IGNORECASE),
            frozenset(
                {"time", "before", "the", "claim", "reaction", "expires", "sec", "settimer"}
            ),
        ),
        (
            "rarity",
            re.compile(r"spawn\s+rarity\s+multiplier.*?:", re.IGNORECASE),
            frozenset(
                {
                    "spawn",
                    "rarity",
                    "multiplier",
                    "for",
                    "already",
                    "claimed",
                    "characters",
                    "setrare",
                }
            ),
        ),
        (
            "kakera_bonus",
            re.compile(r"%\s*kakera\s+bonus\s*:", re.IGNORECASE),
            frozenset({"kakera", "bonus", "setkakerabonus"}),
        ),
        (
            "sphere_bonus",
            re.compile(r"%\s*sphere\s+bonus\s*:", re.IGNORECASE),
            frozenset({"sphere", "bonus", "setspherebonus"}),
        ),
        (
            "game_mode",
            re.compile(r"\bgame\s+mode\s*:", re.IGNORECASE),
            frozenset({"game", "mode", "gamemode"}),
        ),
        (
            "channel_instance",
            re.compile(r"this\s+channel\s+instance\s*:", re.IGNORECASE),
            frozenset({"this", "channel", "instance", "channelinstance"}),
        ),
    )
    _KT_RELEVANT_LINES = (
        (
            "current_tower_level",
            re.compile(r"current\s+level\s+is", re.IGNORECASE),
            frozenset({"your", "current", "level", "is", "tower", "towers"}),
        ),
        (
            "next_level_cost",
            re.compile(r"next\s+level\s+costs", re.IGNORECASE),
            frozenset({"the", "next", "level", "costs"}),
        ),
        (
            "kakera_balance",
            re.compile(r"you\s+have.*:kakera:", re.IGNORECASE),
            frozenset({"you", "have"}),
        ),
        (
            "perks_list_marker",
            re.compile(r"list\s+of\s+perks", re.IGNORECASE),
            frozenset({"list", "of", "perks"}),
        ),
    )

    def __init__(
        self,
        *,
        family: str,
        guild_id: str,
        channel_id: str,
        mudae_user_id: str,
        user_ids: frozenset[str],
    ) -> None:
        if family not in self._FAMILY_KIND:
            raise ValueError("Diagnostic family must be settings or kt.")
        self.family = family
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.mudae_user_id = mudae_user_id
        self.user_ids = user_ids
        self._parser = MudaeTextParser()
        self._router = MudaeMessageRouter(self._parser)
        self._aliases: dict[str, dict[str, str]] = {}
        self._pending: dict[str, _Pending] = {}
        self._command_ids: dict[str, str] = {}
        self._response_ids: set[str] = set()

    def project(self, event_type: str, data: Mapping[str, Any]) -> dict[str, Any] | None:
        """Return an allowlisted record or reject the event without serializing it."""
        if event_type == "INTERACTION_CREATE":
            return self._interaction(data)
        if event_type not in {"MESSAGE_CREATE", "MESSAGE_UPDATE"}:
            return None
        author_id = self._id_from(data.get("author"), "id")
        if event_type == "MESSAGE_CREATE" and author_id in self.user_ids:
            return self._command(data, author_id)
        message_id = self._id(data.get("id"))
        if author_id != self.mudae_user_id and not (
            event_type == "MESSAGE_UPDATE"
            and author_id is None
            and message_id in self._response_ids
        ):
            return None
        return self._response(event_type, data, message_id)

    @staticmethod
    def _id(value: Any) -> str | None:
        return str(value) if isinstance(value, (str, int)) and str(value).isdigit() else None

    @classmethod
    def _id_from(cls, value: Any, key: str) -> str | None:
        return cls._id(value.get(key)) if isinstance(value, Mapping) else None

    def _alias(self, kind: str, value: str | None) -> str | None:
        if value is None:
            return None
        aliases = self._aliases.setdefault(kind, {})
        if value not in aliases:
            aliases[value] = f"{kind}_{len(aliases) + 1}"
        return aliases[value]

    def _base(self, event_type: str) -> dict[str, Any]:
        return {
            "capture_schema_version": self.SCHEMA_VERSION,
            "purpose": "settings/kt live-ingestion characterization",
            "enabled_family": self.family,
            "gateway_event_type": event_type,
            "guild_alias": self._alias("guild", self.guild_id),
            "channel_alias": self._alias("channel", self.channel_id),
            "privacy": {
                "sanitized_before_write": True,
                "raw_ids": "excluded",
                "credentials": "excluded",
                "raw_payload": "excluded",
                "text": "bounded-sanitized-parser-structure-only",
                "purpose_scope": "settings-kt-only",
            },
            "limits": {
                "maximum_records": self.MAX_RECORDS,
                "maximum_evaluated_text_characters": self.MAX_INPUT_CHARS,
                "maximum_structural_lines": self.MAX_STRUCTURAL_LINES,
                "maximum_structural_line_characters": self.MAX_STRUCTURAL_LINE_CHARS,
                "maximum_structural_characters": self.MAX_STRUCTURAL_TOTAL_CHARS,
                "maximum_codepoints_per_line": self.MAX_CODEPOINTS_PER_LINE,
                "maximum_flattening_boundaries": self.MAX_BOUNDARIES,
                "maximum_custom_emoji_transforms": self.MAX_CUSTOM_EMOJI_TRANSFORMS,
            },
        }

    def _match(self, token: str, source: InvocationSource) -> bool:
        match = COMMAND_REGISTRY.lookup(token, source=source, capability=CommandCapability.LISTENER)
        return match is not None and match.canonical_name == self.family

    def _remember(self, actor_id: str, source: str, source_id: str | None) -> str | None:
        if source_id is None:
            return None
        command_alias = self._alias("command", source_id)
        assert command_alias is not None
        self._pending[actor_id] = _Pending(actor_id, source, command_alias, time.monotonic())
        self._command_ids[source_id] = actor_id
        if len(self._pending) > 32:
            self._pending.pop(next(iter(self._pending)))
        if len(self._command_ids) > 64:
            self._command_ids.pop(next(iter(self._command_ids)))
        return command_alias

    def _command(self, data: Mapping[str, Any], actor_id: str) -> dict[str, Any] | None:
        content = data.get("content")
        if not isinstance(content, str):
            return None
        parts = content.lstrip().split(maxsplit=1)
        if (
            not parts
            or not parts[0].startswith("$")
            or not self._match(parts[0], InvocationSource.TEXT)
        ):
            return None
        command_alias = self._remember(actor_id, "text", self._id(data.get("id")))
        record = self._base("MESSAGE_CREATE")
        record.update(
            {
                "record_kind": "command",
                "canonical_command": self.family,
                "source_kind": "text",
                "actor_alias": self._alias("user", actor_id),
                "command_alias": command_alias,
                "command_context_present": command_alias is not None,
                "stages": ["COMMAND_RECOGNIZED"],
            }
        )
        return record

    def _interaction(self, data: Mapping[str, Any]) -> dict[str, Any] | None:
        if data.get("type") != 2:
            return None
        actor_id = self._id_from(data.get("user"), "id")
        if actor_id is None and isinstance(data.get("member"), Mapping):
            actor_id = self._id_from(data["member"].get("user"), "id")
        interaction_data = data.get("data")
        if actor_id not in self.user_ids or not isinstance(interaction_data, Mapping):
            return None
        command_name = interaction_data.get("name")
        if (
            not isinstance(command_name, str)
            or re.fullmatch(r"[a-z0-9_-]{1,32}", command_name) is None
            or not self._match(command_name, InvocationSource.INTERACTION_NAME)
        ):
            return None
        command_alias = self._remember(actor_id, "interaction", self._id(data.get("id")))
        record = self._base("INTERACTION_CREATE")
        record.update(
            {
                "record_kind": "command",
                "canonical_command": self.family,
                "source_kind": "interaction",
                "actor_alias": self._alias("user", actor_id),
                "command_alias": command_alias,
                "command_context_present": command_alias is not None,
                "stages": ["COMMAND_RECOGNIZED"],
            }
        )
        return record

    @classmethod
    def _text_parts(cls, data: Mapping[str, Any]) -> tuple[_ParserInput, str, bool]:
        """Use the production listener seam and separately verify bounded boundaries."""
        # Import at runtime to avoid the listener's capture-service import cycle.
        from moa.services.discord_listener_service import DiscordListenerService

        raw_parts: list[tuple[str, int | None, int | None, str]] = []
        content = data.get("content")
        message_content = content if isinstance(content, str) else ""
        if isinstance(content, str):
            raw_parts.append(("content", None, None, content))

        embed_views: list[SimpleNamespace] = []
        embeds = data.get("embeds")
        omitted_parts = False
        if isinstance(embeds, list):
            omitted_parts = len(embeds) > 10
            for embed_index, embed in enumerate(embeds[:10]):
                if not isinstance(embed, Mapping):
                    continue
                author_data = embed.get("author")
                author_name = (
                    author_data.get("name")
                    if isinstance(author_data, Mapping)
                    and isinstance(author_data.get("name"), str)
                    else None
                )
                if author_name is not None:
                    raw_parts.append(("embed_author", embed_index, None, author_name))
                title = embed.get("title") if isinstance(embed.get("title"), str) else None
                description = (
                    embed.get("description")
                    if isinstance(embed.get("description"), str)
                    else None
                )
                if title is not None:
                    raw_parts.append(("embed_title", embed_index, None, title))
                if description is not None:
                    raw_parts.append(("embed_description", embed_index, None, description))

                field_views: list[SimpleNamespace] = []
                fields = embed.get("fields")
                if isinstance(fields, list):
                    omitted_parts = omitted_parts or len(fields) > 25
                    for field_index, field in enumerate(fields[:25]):
                        if not isinstance(field, Mapping):
                            continue
                        name = field.get("name") if isinstance(field.get("name"), str) else ""
                        value = field.get("value") if isinstance(field.get("value"), str) else ""
                        field_views.append(SimpleNamespace(name=name, value=value))
                        if name:
                            raw_parts.append(
                                ("embed_field_name", embed_index, field_index, name)
                            )
                        if value:
                            raw_parts.append(
                                ("embed_field_value", embed_index, field_index, value)
                            )
                footer_data = embed.get("footer")
                footer_text = (
                    footer_data.get("text")
                    if isinstance(footer_data, Mapping)
                    and isinstance(footer_data.get("text"), str)
                    else None
                )
                if footer_text is not None:
                    raw_parts.append(("embed_footer", embed_index, None, footer_text))
                embed_views.append(
                    SimpleNamespace(
                        author=SimpleNamespace(name=author_name),
                        title=title,
                        description=description,
                        fields=tuple(field_views),
                        footer=SimpleNamespace(text=footer_text),
                    )
                )

        message_view = SimpleNamespace(content=message_content, embeds=tuple(embed_views))
        parser_text = DiscordListenerService.extract_message_text(message_view)

        expected_parts: list[str] = []
        boundaries: list[_ParserInputPart] = []
        cursor = 0
        for source, part_embed_index, part_field_index, raw_value in raw_parts:
            if not raw_value.strip():
                continue
            normalized = DiscordListenerService._normalize_discord_text(raw_value)
            if expected_parts:
                cursor += 1
            part_start = cursor
            cursor += len(normalized)
            custom_emojis: list[_CustomEmojiShape] = []
            emoji_search_start = 0
            for match in cls._CUSTOM_EMOJI.finditer(raw_value):
                parser_form = f":{match.group('name')}:"
                local_start = normalized.find(parser_form, emoji_search_start)
                if local_start < 0:
                    continue
                custom_emojis.append(
                    _CustomEmojiShape(
                        name=match.group("name"),
                        animated=bool(match.group("animated")),
                        parser_start=part_start + local_start,
                        parser_end=part_start + local_start + len(parser_form),
                    )
                )
                emoji_search_start = local_start + len(parser_form)
            boundaries.append(
                _ParserInputPart(
                    source=source,
                    embed_index=part_embed_index,
                    field_index=part_field_index,
                    parser_start=part_start,
                    parser_end=cursor,
                    custom_emojis=tuple(custom_emojis),
                )
            )
            expected_parts.append(normalized)

        expected_text = "\n".join(expected_parts)
        parser_input = _ParserInput(
            text=parser_text,
            parts=tuple(boundaries),
            seam_consistent=expected_text == parser_text,
        )
        has_content = any(part.source == "content" for part in boundaries)
        has_embed = any(part.source != "content" for part in boundaries)
        source_shape = (
            "mixed"
            if has_content and has_embed
            else "content"
            if has_content
            else "embed"
            if has_embed
            else "other"
        )
        truncated = omitted_parts or len(parser_text) > cls.MAX_INPUT_CHARS
        return parser_input, source_shape, truncated

    @classmethod
    def _parser_lines(cls, text: str) -> tuple[_ParserLine, ...]:
        lines: list[_ParserLine] = []
        cursor = 0
        for index, fragment in enumerate(text.splitlines(keepends=True)):
            ending = next(
                (candidate for candidate in cls._LINE_ENDINGS if fragment.endswith(candidate)),
                "",
            )
            line_text = fragment[: -len(ending)] if ending else fragment
            lines.append(
                _ParserLine(
                    index=index,
                    text=line_text,
                    ending=ending,
                    start=cursor,
                    end=cursor + len(line_text),
                )
            )
            cursor += len(fragment)
        return tuple(lines)

    def _relevant_line(
        self, line: str
    ) -> tuple[str, frozenset[str]] | None:
        rules = self._SETTINGS_RELEVANT_LINES if self.family == "settings" else self._KT_RELEVANT_LINES
        for line_kind, pattern, allowed_words in rules:
            if pattern.search(line) is not None:
                return line_kind, allowed_words
        return None

    @classmethod
    def _sanitize_line(cls, line: str, allowed_words: frozenset[str]) -> str:
        output: list[str] = []
        cursor = 0
        while cursor < len(line):
            custom_emoji = cls._CUSTOM_EMOJI.match(line, cursor)
            if custom_emoji is not None:
                animated = "a" if custom_emoji.group("animated") else ""
                output.append(f"<{animated}:{custom_emoji.group('name')}:<ID>>")
                cursor = custom_emoji.end()
                continue
            copied_emoji = cls._COPIED_EMOJI.match(line, cursor)
            if copied_emoji is not None:
                output.append(copied_emoji.group(0))
                cursor = copied_emoji.end()
                continue
            number = cls._NUMBER.match(line, cursor)
            if number is not None:
                output.append("<NUMBER>")
                cursor = number.end()
                continue
            word = cls._WORD.match(line, cursor)
            if word is not None:
                token = word.group(0)
                output.append(token if token.casefold() in allowed_words else "<TEXT>")
                cursor = word.end()
                continue
            output.append(line[cursor])
            cursor += 1
        return "".join(output)

    @classmethod
    def _is_structural_whitespace(cls, value: str) -> bool:
        return value.isspace() or value in cls._STRUCTURAL_WHITESPACE

    @classmethod
    def _codepoint_metadata(cls, line: str) -> dict[str, Any] | None:
        non_ascii = [
            {
                "index": index,
                "codepoint": f"U+{ord(value):04X}",
                "category": unicodedata.category(value),
                "name": unicodedata.name(value, "UNNAMED"),
            }
            for index, value in enumerate(line)
            if ord(value) > 127
            and (
                unicodedata.category(value)[0] in {"C", "P", "S", "Z"}
                or value in cls._STRUCTURAL_WHITESPACE
            )
        ]
        if len(non_ascii) > cls.MAX_CODEPOINTS_PER_LINE:
            return None

        leading: list[str] = []
        for value in line:
            if not cls._is_structural_whitespace(value):
                break
            leading.append(f"U+{ord(value):04X}")
        trailing: list[str] = []
        for value in reversed(line):
            if not cls._is_structural_whitespace(value):
                break
            trailing.append(f"U+{ord(value):04X}")
        trailing.reverse()
        return {
            "non_ascii_codepoints": non_ascii,
            "leading_whitespace_codepoints": leading,
            "trailing_whitespace_codepoints": trailing,
        }

    @classmethod
    def _overlaps_line(cls, start: int, end: int, line: _ParserLine) -> bool:
        if line.start == line.end:
            return start <= line.start <= end
        return start < line.end and end > line.start

    def _sanitize_parser_input(
        self,
        parser_input: _ParserInput,
        bounded_text: str,
        *,
        input_truncated: bool,
    ) -> dict[str, Any]:
        if not parser_input.seam_consistent:
            return {
                "status": "SANITIZATION_UNREPRESENTABLE",
                "source": "DiscordListenerService.extract_message_text",
                "retained_lines": [],
                "characterization_sufficient": False,
            }

        all_lines = self._parser_lines(bounded_text)
        relevant = [
            (line, classified)
            for line in all_lines
            if (classified := self._relevant_line(line.text)) is not None
        ]
        retention_truncated = len(relevant) > self.MAX_STRUCTURAL_LINES
        relevant = relevant[: self.MAX_STRUCTURAL_LINES]
        retained_records: list[dict[str, Any]] = []
        retained_lines: list[_ParserLine] = []
        total_characters = 0
        unrepresentable = False
        for line, (line_kind, allowed_words) in relevant:
            if len(line.text) > self.MAX_STRUCTURAL_LINE_CHARS:
                retained_records.append(
                    {"line_index": line.index, "status": "SANITIZATION_UNREPRESENTABLE"}
                )
                unrepresentable = True
                continue
            sanitized = self._sanitize_line(line.text, allowed_words)
            metadata = self._codepoint_metadata(line.text)
            if (
                metadata is None
                or total_characters + len(sanitized) > self.MAX_STRUCTURAL_TOTAL_CHARS
            ):
                retained_records.append(
                    {"line_index": line.index, "status": "SANITIZATION_UNREPRESENTABLE"}
                )
                unrepresentable = True
                continue
            total_characters += len(sanitized)
            retained_lines.append(line)
            retained_records.append(
                {
                    "line_index": line.index,
                    "line_kind": line_kind,
                    "sanitized_text": sanitized,
                    "line_ending_codepoints": [
                        f"U+{ord(value):04X}" for value in line.ending
                    ],
                    **metadata,
                }
            )

        boundaries: list[dict[str, Any]] = []
        custom_emoji_transforms: list[dict[str, Any]] = []
        boundary_truncated = len(parser_input.parts) > self.MAX_BOUNDARIES
        emoji_transforms_truncated = False
        for part_index, part in enumerate(parser_input.parts[: self.MAX_BOUNDARIES]):
            overlapping_lines = [
                line.index
                for line in retained_lines
                if self._overlaps_line(part.parser_start, part.parser_end, line)
            ]
            if not overlapping_lines:
                continue
            boundary: dict[str, Any] = {
                "part_index": part_index,
                "source": part.source,
                "parser_start": part.parser_start,
                "parser_end": min(part.parser_end, len(bounded_text)),
                "retained_line_indexes": overlapping_lines,
            }
            if part.embed_index is not None:
                boundary["embed_index"] = part.embed_index
            if part.field_index is not None:
                boundary["field_index"] = part.field_index
            boundaries.append(boundary)
            for emoji in part.custom_emojis:
                emoji_lines = [
                    line.index
                    for line in retained_lines
                    if self._overlaps_line(emoji.parser_start, emoji.parser_end, line)
                ]
                if not emoji_lines:
                    continue
                if len(custom_emoji_transforms) >= self.MAX_CUSTOM_EMOJI_TRANSFORMS:
                    emoji_transforms_truncated = True
                    continue
                marker = "a" if emoji.animated else ""
                custom_emoji_transforms.append(
                    {
                        "line_indexes": emoji_lines,
                        "sanitized_source": f"<{marker}:{emoji.name}:<ID>>",
                        "parser_text": f":{emoji.name}:",
                    }
                )

        status = (
            "SANITIZATION_UNREPRESENTABLE"
            if unrepresentable
            else "TRUNCATED"
            if (
                input_truncated
                or retention_truncated
                or boundary_truncated
                or emoji_transforms_truncated
            )
            else "COMPLETE"
        )
        return {
            "status": status,
            "source": "DiscordListenerService.extract_message_text",
            "purpose": "settings/kt parser diagnosis only",
            "raw_parser_text": "excluded",
            "raw_payload": "excluded",
            "raw_ids": "excluded",
            "input_line_count": len(all_lines),
            "retained_line_count": len(retained_records),
            "retention_truncated": (
                input_truncated
                or retention_truncated
                or boundary_truncated
                or emoji_transforms_truncated
            ),
            "retained_lines": retained_records,
            "flattening_boundaries": boundaries,
            "custom_emoji_transforms": custom_emoji_transforms,
            "characterization_sufficient": status == "COMPLETE",
        }

    def _structural_diagnostic(
        self,
        parser_input: _ParserInput,
        bounded_text: str,
        *,
        input_truncated: bool,
    ) -> dict[str, Any]:
        try:
            return self._sanitize_parser_input(
                parser_input,
                bounded_text,
                input_truncated=input_truncated,
            )
        except Exception:
            return {
                "status": "SANITIZATION_UNREPRESENTABLE",
                "source": "DiscordListenerService.extract_message_text",
                "retained_lines": [],
                "characterization_sufficient": False,
            }

    def _shape(self, text: str) -> dict[str, Any]:
        lines = self._parser._lines(text)
        if self.family == "settings":
            labels = {
                match.group("label").strip().casefold()
                for line in lines
                if (match := ServerSettingsParser._SETTING_LINE.match(line)) is not None
            }
            return {
                "server_settings_marker": "server settings" in text.casefold(),
                "setclaim_marker": "$setclaim" in text.casefold(),
                "required_fields": {
                    name: any(pattern.search(line) is not None for line in lines)
                    for name, pattern in self._SETTINGS_FIELDS.items()
                }
                | {"prefix": "prefix" in labels, "lang": "lang" in labels},
            }
        return {
            "town_structure": any(
                TowerStateParser._TOWER_LEVEL.search(line) is not None for line in lines
            ),
            "next_level_cost": any(
                TowerStateParser._TOWER_NEXT_COST.search(line) is not None for line in lines
            ),
            "kakera_balance": any(
                self._parser._KAKERA_BALANCE.match(line) is not None for line in lines
            ),
            "list_of_perks_marker": "list of perks" in text.casefold(),
        }

    def _response(
        self, event_type: str, data: Mapping[str, Any], message_id: str | None
    ) -> dict[str, Any] | None:
        parser_input, source_shape, truncated = self._text_parts(data)
        text = parser_input.text[: self.MAX_INPUT_CHARS]
        shape = self._shape(text)
        reference = data.get("message_reference")
        reference_id = self._id_from(reference, "message_id")
        metadata = data.get("interaction_metadata") or data.get("interaction")
        interaction_id = self._id_from(metadata, "id")
        actor_id = self._command_ids.get(reference_id or "") or self._command_ids.get(
            interaction_id or ""
        )
        explicit_link = actor_id is not None
        strong_marker = (
            shape["server_settings_marker"] and shape["setclaim_marker"]
            if self.family == "settings"
            else shape["town_structure"]
            and (shape["next_level_cost"] or shape["list_of_perks_marker"])
        )
        if not (explicit_link or strong_marker or message_id in self._response_ids):
            return None
        now = time.monotonic()
        pending = {
            user_id: context
            for user_id, context in self._pending.items()
            if now - context.captured_at <= self.CONTEXT_TTL_SECONDS
        }
        if explicit_link:
            candidates = [pending[actor_id]] if actor_id in pending else []
        else:
            candidates = list(pending.values())
        if len(candidates) > 1:
            # The normal listener selects a sole compatible context, then a sole
            # pending context. Multiple same-family owners stay unresolved.
            candidates = []
            attribution = "ambiguous"
        elif candidates:
            attribution = "unique"
        else:
            attribution = "absent"
        context = candidates[0] if candidates else None
        router_kind = self._router.detect(text).kind if not truncated else "unknown"
        router_matched = router_kind == self._FAMILY_KIND[self.family]
        parser_input_structure = self._structural_diagnostic(
            parser_input,
            text,
            input_truncated=truncated,
        )
        if truncated:
            parser_result = "not_evaluated_truncated"
        else:
            try:
                if self.family == "settings":
                    self._parser.parse_server_settings(text)
                else:
                    self._parser.parse_tower_state(text)
            except MudaeParseError:
                parser_result = "rejected"
            else:
                parser_result = "accepted"
        resolved_kind = (
            "settings"
            if self.family == "settings" and router_matched
            else "towerstate"
            if self.family == "kt" and parser_result == "accepted"
            else None
        )
        stages = [
            *(["CONTEXT_MATCHED"] if context else []),
            *(["ROUTER_MATCHED"] if router_matched else []),
            *(
                ["PARSER_ACCEPTED"]
                if parser_result == "accepted"
                else ["PARSER_REJECTED"]
                if parser_result == "rejected"
                else []
            ),
            "ATTRIBUTION_RESOLVED" if context else "ATTRIBUTION_FAILED",
            "PROJECTION_NOT_RUN_CAPTURE_ONLY",
        ]
        if message_id is not None:
            self._response_ids.add(message_id)
            if len(self._response_ids) > 64:
                self._response_ids.pop()
        record = self._base(event_type)
        record.update(
            {
                "record_kind": "response",
                "message_alias": self._alias("message", message_id),
                "command_alias": context.command_alias if context else None,
                "source_shape": source_shape,
                "input_truncated": truncated,
                "shape": shape,
                "parser_input_structure": parser_input_structure,
                "command_context_present": context is not None,
                "context_canonical_family": self.family if context else "none",
                "context_source": context.source if context else "none",
                "expected_family": self.family if context else "none",
                "server_attribution": "resolved" if context else attribution,
                "account_attribution": "not-required" if self.family == "settings" else attribution,
                "router_kind": router_kind
                if router_kind in {"settings", "towerstate", "unknown"}
                else "other",
                "router_matched": router_matched,
                "parser_result": parser_result,
                "resolved_message_kind": resolved_kind or "none",
                "stages": stages,
            }
        )
        return record
