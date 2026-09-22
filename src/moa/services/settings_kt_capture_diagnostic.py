"""Bounded, capture-only evidence for one explicitly selected Mudae command family.

Only allowlisted booleans, enums, and deterministic aliases leave this module.
Gateway text is evaluated in memory and is never placed in a durable record.
This is a shadow evaluation of the listener's documented pending-context choice;
it does not construct a listener, repository, or import workflow.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
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


class SettingsKtCaptureDiagnostic:
    """Project one family of Gateway events into privacy-safe decision evidence."""

    SCHEMA_VERSION = "moa.settings-kt-capture-diagnostic.v1"
    MAX_RECORDS = 128
    MAX_INPUT_CHARS = 32768
    CONTEXT_TTL_SECONDS = 300.0
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
                "text": "bounded-shape-only",
            },
            "limits": {
                "maximum_records": self.MAX_RECORDS,
                "maximum_evaluated_text_characters": self.MAX_INPUT_CHARS,
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

    @staticmethod
    def _text_parts(data: Mapping[str, Any]) -> tuple[str, str, bool]:
        content = data.get("content")
        content_parts = [content] if isinstance(content, str) and content.strip() else []
        embed_parts: list[str] = []
        embeds = data.get("embeds")
        omitted_parts = False
        if isinstance(embeds, list):
            omitted_parts = len(embeds) > 10
            for embed in embeds[:10]:
                if not isinstance(embed, Mapping):
                    continue
                author = embed.get("author")
                if isinstance(author, Mapping) and isinstance(author.get("name"), str):
                    embed_parts.append(author["name"])
                for key in ("title", "description"):
                    if isinstance(embed.get(key), str):
                        embed_parts.append(embed[key])
                fields = embed.get("fields")
                if isinstance(fields, list):
                    omitted_parts = omitted_parts or len(fields) > 25
                    for field in fields[:25]:
                        if isinstance(field, Mapping):
                            for key in ("name", "value"):
                                if isinstance(field.get(key), str):
                                    embed_parts.append(field[key])
                footer = embed.get("footer")
                if isinstance(footer, Mapping) and isinstance(footer.get("text"), str):
                    embed_parts.append(footer["text"])
        source_shape = (
            "mixed"
            if content_parts and embed_parts
            else "content"
            if content_parts
            else "embed"
            if embed_parts
            else "other"
        )
        text = "\n".join((*content_parts, *embed_parts))
        truncated = omitted_parts or len(text) > SettingsKtCaptureDiagnostic.MAX_INPUT_CHARS
        return text[: SettingsKtCaptureDiagnostic.MAX_INPUT_CHARS], source_shape, truncated

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
        text, source_shape, truncated = self._text_parts(data)
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
