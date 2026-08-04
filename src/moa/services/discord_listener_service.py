"""Capture Discord messages and route recognized Mudae responses into MOA."""

from __future__ import annotations

import json
import hashlib
import asyncio
import inspect
import logging
import re
import time
import warnings
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

import discord

from moa.core.config import ConfigAccount, ConfigService
from moa.database.sqlite import connect
from moa.parser.mudae import MudaeParseError, MudaeTextParser
from moa.parser.message_router import MudaeMessageRouter
from moa.models.discord_identity import MessageAggregateKey, SourcePlatform
from moa.models.discord_message_mapping import build_message_receive_envelope
from moa.repositories.discord_message_repository import (
    DiscordMessageProcessingConflictError,
    DiscordMessageProcessingError,
    DiscordMessageRepository,
    DiscordSourceEventAccountAttribution,
    DiscordSourceEventServerAttribution,
    ReceivedMessageEvent,
)
from moa.services.automatic_import_service import (
    AutomaticImportService,
    DurableAntidisablePageImportContext,
    DurableClaimImportContext,
    DurableDisableListImportContext,
    DurableInfoklImportContext,
    DurableKakeraImportContext,
    DurableKakeralootStateImportContext,
    DurableMudapinsImportContext,
    DurablePlayerBonusImportContext,
    DurableProfileImportContext,
    DurableRollImportContext,
    DurableSettingsImportContext,
    DurableSphereResultImportContext,
    DurableTimerImportContext,
    DurableTowerStateImportContext,
    DurableWishlistImportContext,
)
from moa.services.catalog_service import CatalogService
from moa.services.ourochest_workflow_service import OurochestWorkflowService


@dataclass(frozen=True)
class DiscordCommandContext:
    """Configured MOA identity associated with the latest command in a channel."""

    server_id: str
    user_id: str
    identity: ConfigAccount
    captured_at: float
    expected_kind: str | None = None
    personal_rare_value: int | None = None
    personal_rare_argument_supplied: bool = False
    evidence_source: str = "context"


@dataclass(frozen=True, slots=True)
class _ServerAttributionDecision:
    """The listener-local decision before it is persisted by the repository."""

    status: Literal["resolved", "unresolved", "ambiguous"]
    server_name: str | None
    authoritative_evidence: bool = False


@dataclass(frozen=True, slots=True)
class _AccountAttributionDecision:
    """The listener-local account decision before durable persistence."""

    status: Literal["resolved", "unresolved", "ambiguous"]
    server_name: str | None
    account_name: str | None
    authoritative_evidence: bool = False


@dataclass(frozen=True, slots=True)
class DiscordEventCaptureConfig:
    """Explicit filters for a sensitive, diagnostic-only Gateway capture."""

    output_path: Path
    guild_id: str
    channel_id: str
    mudae_user_id: str
    user_ids: frozenset[str]
    enabled: bool = False
    include_message_text: bool = False


class DiscordEventCaptureError(RuntimeError):
    """Safe diagnostic-capture failure without source payload details."""


class DiscordEventCaptureService:
    """Write narrowly filtered Discord Gateway events without importing MOA data.

    This service is deliberately separate from ``DiscordListenerService`` so capture-only
    runs cannot construct repositories, import services, or scan workflow state. discord.py
    dispatches ``on_socket_raw_receive`` only when ``enable_debug_events=True``; that callback
    supplies the Gateway envelope needed to preserve message/update/interaction relationships.
    """

    _SCHEMA_VERSION = "moa.discord-event-capture.v1"
    _EVENT_TYPES = frozenset({"MESSAGE_CREATE", "MESSAGE_UPDATE", "INTERACTION_CREATE"})
    _SECRET_KEY_PARTS = frozenset(
        {
            "token",
            "authorization",
            "cookie",
            "session",
            "webhook",
            "auth",
            "email",
            "phone",
            "username",
            "globalname",
            "discriminator",
            "avatar",
            "banner",
            "displayname",
            "member",
        }
    )

    def __init__(self, config: DiscordEventCaptureConfig) -> None:
        self._validate_config(config)
        self._config = config
        self._output_file: Any | None = None
        self._sequence = 0
        self._failed = False
        self._client: Any | None = None
        self._shutdown_requested = False
        self._shutdown_task: asyncio.Task[None] | None = None
        self._close_attempted = False
        self._mudae_text_message_ids: set[str] = set()

    @staticmethod
    def _validate_config(config: DiscordEventCaptureConfig) -> None:
        if not config.enabled:
            raise ValueError("Diagnostic capture must be explicitly enabled.")
        if not isinstance(config.output_path, Path):
            raise ValueError("Diagnostic capture output path is required.")
        path = config.output_path.expanduser()
        repository_root = Path(__file__).resolve().parents[3]
        if not path.is_absolute() or path.resolve(strict=False).is_relative_to(repository_root):
            raise ValueError("Diagnostic capture output must be an absolute path outside the repository.")
        if path.exists() and path.is_dir():
            raise ValueError("Diagnostic capture output must name a file, not a directory.")
        if path.exists():
            raise ValueError("Diagnostic capture output already exists; refusing to overwrite it.")
        if not path.parent.is_dir():
            raise ValueError("Diagnostic capture output parent directory does not exist.")
        identifiers = (config.guild_id, config.channel_id, config.mudae_user_id, *config.user_ids)
        if not config.user_ids or any(
            not isinstance(value, str) or not value.isdigit() or int(value) <= 0
            for value in identifiers
        ):
            raise ValueError("Diagnostic capture IDs must be positive numeric Discord IDs.")

    def run(self, token: str) -> None:
        """Run only the diagnostic Gateway client until it is interrupted."""
        normalized_token = token.strip()
        if not normalized_token:
            raise ValueError("A Discord bot token is required.")
        if normalized_token.casefold() in {
            "your_discord_bot_token",
            "your-bot-token",
            "your bot token",
        }:
            raise ValueError(
                "Replace YOUR_DISCORD_BOT_TOKEN with the real token from the Discord Developer Portal."
            )
        self._open_output()
        intents = discord.Intents.none()
        intents.guilds = True
        intents.messages = True
        intents.message_content = True
        client = _MOADiagnosticDiscordClient(self, intents=intents, enable_debug_events=True)
        self._client = client
        try:
            client.run(normalized_token)
        finally:
            self.close()
            self._observe_shutdown_task()

    def _open_output(self) -> None:
        if self._output_file is not None:
            return
        try:
            self._output_file = self._config.output_path.open("x", encoding="utf-8", newline="\n")
        except OSError as error:
            self._fail(error)

    def close(self) -> None:
        if self._close_attempted:
            return
        self._close_attempted = True
        output_file, self._output_file = self._output_file, None
        if output_file is not None:
            try:
                output_file.close()
            except Exception:
                pass

    def capture_gateway_payload(self, payload: Mapping[str, Any]) -> bool:
        """Transform one supported Gateway envelope after every filter has matched."""
        if self._failed:
            return False
        event_type = payload.get("t")
        data = payload.get("d")
        if event_type not in self._EVENT_TYPES or not isinstance(data, Mapping):
            return False
        if not self._matches_location(data):
            return False
        if event_type == "MESSAGE_CREATE" and not self._matches_message_create(data):
            return False
        if event_type == "MESSAGE_UPDATE" and not self._matches_message_update(data):
            return False
        if event_type == "INTERACTION_CREATE" and not self._matches_interaction(data):
            return False
        text_allowed = self._text_allowed(event_type, data)
        try:
            record = self._record_for(event_type, data, text_allowed=text_allowed)
            if record is None:
                return False
            self._write(record)
        except DiscordEventCaptureError:
            raise
        except Exception as error:
            self._fail(error)
        if self._is_mudae_message(event_type, data):
            message_id = self._id(data.get("id"))
            if message_id is not None:
                self._mudae_text_message_ids.add(message_id)
        return True

    def _matches_location(self, data: Mapping[str, Any]) -> bool:
        return (
            self._id(data.get("guild_id")) == self._config.guild_id
            and self._id(data.get("channel_id")) == self._config.channel_id
        )

    def _matches_message_create(self, data: Mapping[str, Any]) -> bool:
        author_id = self._id_from_mapping(data.get("author"), "id")
        if author_id == self._config.mudae_user_id:
            return True
        content = data.get("content")
        return (
            author_id in self._config.user_ids
            and isinstance(content, str)
            and content.lstrip().casefold().startswith("$adl")
        )

    def _matches_message_update(self, data: Mapping[str, Any]) -> bool:
        author_id = self._id_from_mapping(data.get("author"), "id")
        if author_id is None:
            # Gateway updates commonly omit author data. Guild/channel filtering is the narrowest
            # safe filter in that case; retaining it is necessary to characterize uncached edits.
            return True
        if author_id == self._config.mudae_user_id:
            return True
        content = data.get("content")
        return (
            author_id in self._config.user_ids
            and isinstance(content, str)
            and content.lstrip().casefold().startswith("$adl")
        )

    def _matches_interaction(self, data: Mapping[str, Any]) -> bool:
        return self._interaction_user_id(data) in self._config.user_ids

    def _text_allowed(self, event_type: str, data: Mapping[str, Any]) -> bool:
        if not self._config.include_message_text:
            return False
        if event_type == "MESSAGE_CREATE":
            return self._is_mudae_message(event_type, data) or self._is_selected_adl_request(data)
        if event_type == "MESSAGE_UPDATE":
            return self._is_mudae_message(event_type, data) or (
                self._id(data.get("id")) in self._mudae_text_message_ids
            )
        if event_type == "INTERACTION_CREATE":
            source_message = data.get("message")
            return (
                isinstance(source_message, Mapping)
                and self._id(source_message.get("id")) in self._mudae_text_message_ids
            )
        return False

    def _is_mudae_message(self, event_type: str, data: Mapping[str, Any]) -> bool:
        return event_type in {"MESSAGE_CREATE", "MESSAGE_UPDATE"} and (
            self._id_from_mapping(data.get("author"), "id") == self._config.mudae_user_id
        )

    def _is_selected_adl_request(self, data: Mapping[str, Any]) -> bool:
        content = data.get("content")
        return (
            self._id_from_mapping(data.get("author"), "id") in self._config.user_ids
            and isinstance(content, str)
            and content.lstrip().casefold().startswith("$adl")
        )

    def _record_for(
        self, event_type: str, data: Mapping[str, Any], *, text_allowed: bool
    ) -> dict[str, Any] | None:
        if event_type in {"MESSAGE_CREATE", "MESSAGE_UPDATE"}:
            message = self._message_record(data, include_text=text_allowed)
            return {
                "capture_schema_version": self._SCHEMA_VERSION,
                "gateway_event_type": event_type,
                "guild_id": self._id(data.get("guild_id")),
                "channel_id": self._id(data.get("channel_id")),
                "message_id": message.get("id"),
                "author_id": message.get("author_id"),
                "message": message,
            }
        if event_type == "INTERACTION_CREATE":
            interaction_data = data.get("data")
            interaction_data = interaction_data if isinstance(interaction_data, Mapping) else {}
            source_message = data.get("message")
            source_message = source_message if isinstance(source_message, Mapping) else None
            return {
                "capture_schema_version": self._SCHEMA_VERSION,
                "gateway_event_type": event_type,
                "guild_id": self._id(data.get("guild_id")),
                "channel_id": self._id(data.get("channel_id")),
                "interaction": {
                    "id": self._id(data.get("id")),
                    "type": data.get("type"),
                    "application_id": self._id(data.get("application_id")),
                    "acting_user_id": self._interaction_user_id(data),
                    "component_type": interaction_data.get("component_type"),
                    "custom_id_sha256": self._safe_digest(interaction_data.get("custom_id")),
                    "custom_id_length": len(interaction_data["custom_id"])
                    if isinstance(interaction_data.get("custom_id"), str)
                    else None,
                    "values_sha256": self._safe_digests(interaction_data.get("values")),
                    "source_message_id": self._id_from_mapping(source_message, "id"),
                    "source_message": self._message_record(source_message, include_text=text_allowed)
                    if source_message is not None
                    else None,
                },
            }
        return None

    def _message_record(self, data: Mapping[str, Any], *, include_text: bool = False) -> dict[str, Any]:
        record: dict[str, Any] = {
            "id": self._id(data.get("id")),
            "author_id": self._id_from_mapping(data.get("author"), "id"),
            "application_id": self._id(data.get("application_id")),
            "type": data.get("type"),
            "content": self._sanitize_text(data.get("content"))
            if include_text and isinstance(data.get("content"), str)
            else None,
            "created_at": data.get("timestamp"),
            "edited_at": data.get("edited_timestamp"),
            "reference": self._reference_record(data.get("message_reference")),
            "interaction_metadata": self._interaction_metadata_record(
                data.get("interaction_metadata") or data.get("interaction")
            ),
            "components": self._components_record(data.get("components")),
            "embeds": self._embeds_record(data.get("embeds"), include_text=include_text),
        }
        return self._without_none(record)

    def _reference_record(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, Mapping):
            return None
        return self._without_none(
            {
                "message_id": self._id(value.get("message_id")),
                "channel_id": self._id(value.get("channel_id")),
                "guild_id": self._id(value.get("guild_id")),
                "type": value.get("type"),
            }
        )

    def _interaction_metadata_record(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, Mapping):
            return None
        return self._without_none(
            {
                "id": self._id(value.get("id")),
                "type": value.get("type"),
                "name": None,
                "user_id": self._interaction_user_id(value),
            }
        )

    def _components_record(
        self, value: Any, path: tuple[int, ...] = ()
    ) -> list[dict[str, Any]] | None:
        if not isinstance(value, list):
            return None
        return [
            self._component_record(component, (*path, index))
            for index, component in enumerate(value)
            if isinstance(component, Mapping)
        ]

    def _component_record(self, value: Mapping[str, Any], path: tuple[int, ...]) -> dict[str, Any]:
        return self._without_none(
            {
                "path": list(path),
                "type": value.get("type"),
                "custom_id_sha256": self._safe_digest(value.get("custom_id")),
                "custom_id_length": len(value["custom_id"])
                if isinstance(value.get("custom_id"), str)
                else None,
                "values_sha256": self._safe_digests(value.get("values")),
                "disabled": value.get("disabled") if isinstance(value.get("disabled"), bool) else None,
                "emoji": self._emoji_record(value.get("emoji")),
                "components": self._components_record(value.get("components"), path),
            }
        )

    @classmethod
    def _emoji_record(cls, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, Mapping):
            return None
        emoji_id = cls._id(value.get("id"))
        emoji_name = value.get("name")
        if emoji_id is None and not isinstance(emoji_name, str):
            return None
        kind = "custom" if emoji_id is not None else "unicode"
        return cls._without_none(
            {
                "kind": kind,
                "id_sha256": cls._safe_digest(f"custom-id:{emoji_id}") if emoji_id is not None else None,
                "name_sha256": cls._safe_digest(
                    f"custom-name:{emoji_name}" if kind == "custom" else f"unicode:{emoji_name}"
                )
                if isinstance(emoji_name, str)
                else None,
                "name_length": len(emoji_name) if isinstance(emoji_name, str) else None,
                "animated": value.get("animated")
                if kind == "custom" and isinstance(value.get("animated"), bool)
                else None,
            }
        )

    def _embeds_record(self, value: Any, *, include_text: bool) -> list[dict[str, Any]] | None:
        if not isinstance(value, list):
            return None
        records: list[dict[str, Any]] = []
        for embed in value:
            if not isinstance(embed, Mapping):
                continue
            fields = embed.get("fields")
            records.append(
                self._without_none(
                    {
                        "type": embed.get("type"),
                        "field_count": len(fields) if isinstance(fields, list) else None,
                        "has_footer": True if isinstance(embed.get("footer"), Mapping) else None,
                        "title": self._sanitize_text(embed.get("title"))
                        if include_text and isinstance(embed.get("title"), str)
                        else None,
                        "description": self._sanitize_text(embed.get("description"))
                        if include_text and isinstance(embed.get("description"), str)
                        else None,
                        "fields": [
                            self._without_none(
                                {
                                    "name": self._sanitize_text(field.get("name"))
                                    if include_text and isinstance(field.get("name"), str)
                                    else None,
                                    "value": self._sanitize_text(field.get("value"))
                                    if include_text and isinstance(field.get("value"), str)
                                    else None,
                                }
                            )
                            for field in fields
                            if isinstance(field, Mapping)
                        ]
                        if include_text and isinstance(fields, list)
                        else None,
                        "footer": {
                            "text": self._sanitize_text(embed["footer"]["text"])
                        }
                        if isinstance(embed.get("footer"), Mapping)
                        and include_text
                        and isinstance(embed["footer"].get("text"), str)
                        else None,
                    }
                )
            )
        return records

    @staticmethod
    def _scalar_values(value: Any) -> list[str] | None:
        if not isinstance(value, list):
            return None
        return [str(item) for item in value if isinstance(item, (str, int, float, bool))]

    @staticmethod
    def _id(value: Any) -> str | None:
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value)
        return None

    @classmethod
    def _id_from_mapping(cls, value: Any, key: str) -> str | None:
        return cls._id(value.get(key)) if isinstance(value, Mapping) else None

    @classmethod
    def _interaction_user_id(cls, data: Mapping[str, Any]) -> str | None:
        member = data.get("member")
        if isinstance(member, Mapping):
            user_id = cls._id_from_mapping(member.get("user"), "id")
            if user_id is not None:
                return user_id
        return cls._id_from_mapping(data.get("user"), "id")

    def _write(self, record: dict[str, Any]) -> None:
        if self._output_file is None:
            raise RuntimeError("Diagnostic capture output is not open.")
        position: int | None = None
        try:
            prepared = self._redact(
                {
                    "sequence": self._sequence + 1,
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    **record,
                }
            )
            line = json.dumps(prepared, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
            position = self._output_file.tell()
            self._output_file.write(line)
            self._output_file.flush()
            self._sequence += 1
        except Exception as error:
            try:
                if position is not None:
                    self._output_file.seek(position)
                    self._output_file.truncate()
            except (OSError, ValueError):
                pass
            self._fail(error)

    def _fail(self, error: Exception) -> None:
        if self._failed:
            raise DiscordEventCaptureError("Diagnostic capture failed; output was closed.") from None
        self._failed = True
        self.close()
        self._request_client_shutdown()
        raise DiscordEventCaptureError("Diagnostic capture failed; output was closed.") from error

    def _request_client_shutdown(self) -> None:
        if self._shutdown_requested or self._client is None:
            return
        self._shutdown_requested = True
        try:
            result = self._client.close()
            if inspect.isawaitable(result):
                try:
                    task = asyncio.get_running_loop().create_task(result)
                    self._shutdown_task = task
                    task.add_done_callback(self._observe_shutdown_task_result)
                except RuntimeError:
                    result.close()
        except Exception:
            pass

    def _observe_shutdown_task_result(self, task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except BaseException:
            pass
        finally:
            if self._shutdown_task is task:
                self._shutdown_task = None

    def _observe_shutdown_task(self) -> None:
        task = self._shutdown_task
        if task is not None and task.done():
            self._observe_shutdown_task_result(task)

    @staticmethod
    def _safe_digest(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @classmethod
    def _safe_digests(cls, value: Any) -> list[str] | None:
        if not isinstance(value, list):
            return None
        return [digest for item in value if (digest := cls._safe_digest(item)) is not None]

    @staticmethod
    def _sanitize_text(value: str) -> str:
        text = re.sub(r"<@!?(\d+)>", r"<mention:\1>", value)
        text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[redacted-email]", text)
        text = re.sub(r"https?://\S+|discord\.gg/\S+", "[redacted-url]", text, flags=re.I)
        text = re.sub(r"(?i)\b(?:bearer|token|authorization|cookie|session)\s*[:=]\s*\S+", "[redacted-secret]", text)
        text = re.sub(r"\beyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b", "[redacted-jwt]", text)
        text = re.sub(
            r"(?<!\d)(?:\+\d[ .-]+)?(?:\(\d{3}\)|\d{3})[ .-]+\d{3}[ .-]+\d{4}(?!\d)",
            "[redacted-phone]",
            text,
        )
        text = re.sub(
            r"\b(?=[A-Za-z0-9_-]{24,}\b)(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9_-]+\b",
            "[redacted-long-secret]",
            text,
        )
        return text

    def _redact(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): self._redact(child)
                for key, child in value.items()
                if not self._is_secret_key(str(key))
            }
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        return value

    def _is_secret_key(self, key: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
        if normalized == "authorid":
            return False
        return any(part in normalized for part in self._SECRET_KEY_PARTS)

    @staticmethod
    def _without_none(value: dict[str, Any]) -> dict[str, Any]:
        return {key: child for key, child in value.items() if child is not None}


class DiscordListenerService:
    """Listen for Mudae messages and reuse the existing automatic import pipeline."""

    _CONTEXT_TTL_SECONDS = 300.0
    _UNATTRIBUTED_ROLL_WARNING_TTL_SECONDS = 300.0
    _DEFAULT_STATUS_TEXT = "Testing commands"
    _MAX_STATUS_LENGTH = 128
    _PARSER_VERSION = "mudae-parser-v1"
    _ROUTER_VERSION = "mudae-router-v1"
    _SCAN_KINDS = {
        "harem": "keys",
        "ranked_harem": "owned",
        "antidisable": "antidisable",
    }
    _ROLL_COMMANDS = {
        "m",
        "mx",
        "marry",
        "ma",
        "marrya",
        "mg",
        "marryg",
        "w",
        "wx",
        "waifu",
        "wa",
        "waifua",
        "wg",
        "waifug",
        "h",
        "hx",
        "husbando",
        "ha",
        "husbandoa",
        "hg",
        "husbandog",
    }
    _DURABLE_IMPORT_KINDS = {
        "antidisable",
        "bonus",
        "claim",
        "infokl",
        "kakera",
        "mudapins",
        "profile",
        "roll",
        "settings",
        "sphere_result",
        "timers",
        "towerstate",
        "lootstate",
        "wishlist",
        "disablelist",
    }
    _DURABLE_ACCOUNT_KINDS = {
        "bonus",
        "claim",
        "kakera",
        "mudapins",
        "profile",
        "sphere_result",
        "roll",
        "timers",
        "towerstate",
        "lootstate",
        "wishlist",
        "disablelist",
    }
    _SERVER_INDEPENDENT_KINDS = {"help", "tutorial"}

    def __init__(
        self,
        config_service: ConfigService | None = None,
        catalog_service: CatalogService | None = None,
        importer: AutomaticImportService | None = None,
        discord_message_repository: DiscordMessageRepository | None = None,
        profile_name: str | None = None,
        status_text: str = _DEFAULT_STATUS_TEXT,
        logger: logging.Logger | None = None,
        ourochest_workflow_service: OurochestWorkflowService | None = None,
    ) -> None:
        self._config = config_service or ConfigService()
        self._catalog = catalog_service or CatalogService()
        self._importer = importer or AutomaticImportService(self._catalog)
        self._durable_claim_imports_enabled = discord_message_repository is not None
        self._discord_message_repository = discord_message_repository
        if self._discord_message_repository is None and catalog_service is not None:
            catalog_repository = getattr(catalog_service, "_repository", None)
            database_path = getattr(catalog_repository, "_database_path", None)
            if database_path is not None:
                # Preserve the existing direct-construction test and embedding path while
                # production composition passes this dependency explicitly.
                self._discord_message_repository = DiscordMessageRepository(database_path)
        self._parser = MudaeTextParser()
        self._router = MudaeMessageRouter(self._parser)
        self._profile_name = profile_name
        self._status_text = self._normalize_status_text(status_text)
        self._logger = logger or logging.getLogger("moa.discord")
        self._ourochest_workflow = ourochest_workflow_service or OurochestWorkflowService()
        self._contexts: dict[int, DiscordCommandContext] = {}
        self._pending_contexts: dict[tuple[str, int, str], DiscordCommandContext] = {}
        self._command_contexts: dict[int, DiscordCommandContext] = {}
        self._scan_ids: dict[tuple[str, str, str], int] = {}
        self._scan_contexts: dict[tuple[int, int, str], DiscordCommandContext] = {}
        self._seen_payloads: set[tuple[int, str]] = set()
        self._message_cache: dict[int, discord.Message] = {}
        self._unattributed_roll_warning_at: dict[tuple[int, int], float] = {}
        self._mudae_user_id: int | None = None

    def _is_durable_import_kind(self, kind: str) -> bool:
        """Identify importer kinds using the explicitly composed durable seams."""
        return kind in self._DURABLE_IMPORT_KINDS and (
            kind == "claim" and self._durable_claim_imports_enabled
            or kind != "claim"
        )

    def run(self, token: str, mudae_user_id: int | None = None) -> None:
        """Run the blocking Discord gateway client until interrupted."""
        normalized_token = token.strip()
        if not normalized_token:
            raise ValueError("A Discord bot token is required.")
        if normalized_token.casefold() in {
            "your_discord_bot_token",
            "your-bot-token",
            "your bot token",
        }:
            raise ValueError(
                "Replace YOUR_DISCORD_BOT_TOKEN with the real token from the Discord Developer Portal."
            )
        self._configure_logging()
        self._mudae_user_id = mudae_user_id
        intents = discord.Intents.none()
        intents.guilds = True
        intents.messages = True
        intents.message_content = True
        intents.reactions = True
        client = _MOADiscordClient(self, intents=intents)
        try:
            client.run(normalized_token)
        except discord.LoginFailure as error:
            raise ValueError(
                "Discord rejected the bot token (401 Unauthorized). Check that it is the current "
                "bot token from the Discord Developer Portal, then try again."
            ) from error

    @classmethod
    def _normalize_status_text(cls, status_text: str | None) -> str:
        normalized = (status_text or "").strip()
        return (normalized or cls._DEFAULT_STATUS_TEXT)[: cls._MAX_STATUS_LENGTH]

    def presence_activity(self) -> discord.Activity:
        """Return the friendly Discord presence shown while the listener runs."""
        return discord.Activity(
            type=discord.ActivityType.watching,
            name=self._status_text,
        )

    def _configure_logging(self) -> None:
        """Make listener progress visible even when discord.py owns root logging."""
        self._logger.setLevel(logging.INFO)
        if self._logger.handlers:
            return
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        self._logger.addHandler(handler)
        self._logger.propagate = False

    async def handle_message(self, message: discord.Message) -> None:
        """Track configured user commands and import recognized bot responses."""
        if message.guild is None:
            return
        if message.author.bot:
            await self._handle_bot_message(message)
            return
        identity = self._identity_for_ids(str(message.guild.id), str(message.author.id))
        if identity is None:
            return
        content = (message.content or "").lstrip()
        if content.startswith(("$", "/")):
            command = content.split(maxsplit=1)[0]
        else:
            command = self._message_interaction_command_name(message)
        if not content and command is None:
            self._logger.warning(
                "Configured user's Discord message %s had no readable content; "
                "enable Message Content Intent for prefix-command tracking or use "
                "the slash-command interaction event.",
                message.id,
            )
            return
        if command is None:
            if self._track_divorce_confirmation(message, identity, content):
                return
            if self._track_transaction_input(message, identity, content):
                return
            return
        if self._ourochest_command_kind(command) is not None:
            guild_id = getattr(message.guild, "id", None)
            channel_id = getattr(message.channel, "id", None)
            user_id = getattr(message.author, "id", None)
            if guild_id is None or channel_id is None or user_id is None:
                return
            guild_id = str(guild_id)
            channel_id = str(channel_id)
            user_id = str(user_id)
            if self._ourochest_workflow.has_active_for_owner(
                guild_id, channel_id, user_id
            ):
                self._logger.info(
                    "Ignoring Ourochest command with an active workflow for %s / %s",
                    guild_id,
                    user_id,
                )
                return
            self._ourochest_workflow.create_pending(guild_id, channel_id, user_id)
            return
        expected_kind = self._expected_kind_for_command(command)
        if expected_kind is None:
            self._logger.info(
                "Ignoring unsupported Discord command %s from account %s on server %s",
                command,
                identity.account,
                identity.server,
            )
            return
        context = DiscordCommandContext(
            server_id=str(message.guild.id),
            user_id=str(message.author.id),
            identity=identity,
            captured_at=time.monotonic(),
            expected_kind=expected_kind,
            personal_rare_value=(
                self._personal_rare_command_value(content)
                if expected_kind == "personalrare"
                else None
            ),
            personal_rare_argument_supplied=(
                self._personal_rare_argument_supplied(content)
                if expected_kind == "personalrare" and content
                else False
            ),
            evidence_source="command_author",
        )
        if expected_kind == "antidisable" and self._discord_message_repository is not None:
            durable_context = self._start_antidisable_workflow(message, context, content)
            if durable_context is None:
                return
            context = durable_context
        self._remember_context(message.channel.id, context)
        self._command_contexts[message.id] = context
        if len(self._command_contexts) > 2000:
            self._command_contexts = dict(list(self._command_contexts.items())[-1000:])
        if content:
            self._logger.info(
                "Tracking Discord command %s for %s / %s",
                command,
                identity.server,
                identity.account,
            )
        else:
            self._logger.info(
                "Tracking Discord slash command /%s for %s / %s from the user command event",
                command.lstrip("$/"),
                identity.server,
                identity.account,
            )

    async def handle_interaction(self, interaction: discord.Interaction) -> None:
        """Track a configured user's slash command before its bot response arrives."""
        guild_id = getattr(interaction, "guild_id", None)
        channel_id = getattr(interaction, "channel_id", None)
        user = getattr(interaction, "user", None)
        if guild_id is None or channel_id is None or user is None:
            return
        identity = self._identity_for_ids(str(guild_id), str(user.id))
        if identity is None:
            return
        command = self._interaction_command_name(interaction)
        expected_kind = self._expected_kind_for_command(command or "")
        if expected_kind is None:
            self._logger.info(
                "Ignoring unsupported Discord interaction /%s from account %s on server %s",
                command or "unknown",
                identity.account,
                identity.server,
            )
            return
        context = DiscordCommandContext(
            server_id=str(guild_id),
            user_id=str(user.id),
            identity=identity,
            captured_at=time.monotonic(),
            expected_kind=expected_kind,
            evidence_source="interaction",
        )
        self._remember_context(int(channel_id), context)
        self._logger.info(
            "Tracking Discord interaction /%s for %s / %s",
            command,
            identity.server,
            identity.account,
        )

    @staticmethod
    def _interaction_command_name(interaction: discord.Interaction) -> str | None:
        """Read a slash command name across discord.py command representations."""
        command = getattr(interaction, "command", None)
        command_name = getattr(command, "name", None)
        if command_name:
            return str(command_name)
        data = getattr(interaction, "data", None) or {}
        if not isinstance(data, dict):
            return None
        command_name = data.get("name")
        options = data.get("options") or []
        while options:
            option = options[0]
            if not isinstance(option, dict):
                break
            option_name = option.get("name")
            if option_name:
                command_name = option_name
            options = option.get("options") or []
        return str(command_name) if command_name else None

    @staticmethod
    def _message_interaction_command_name(message: discord.Message) -> str | None:
        """Read a user-authored slash command message when Discord emits one."""
        metadata = getattr(message, "interaction_metadata", None)
        if metadata is None and not isinstance(message, discord.Message):
            with warnings.catch_warnings():
                # discord.py may emit a deprecation warning from the property
                # implementation itself, with a category that varies by
                # supported version. This is only a compatibility probe; do
                # not let it pollute the listener's normal logs.
                warnings.simplefilter("ignore")
                metadata = getattr(message, "interaction", None)
        if metadata is None:
            return None
        command_name = getattr(metadata, "name", None)
        if command_name is None:
            command_name = getattr(getattr(metadata, "command", None), "name", None)
        return str(command_name) if command_name else None

    async def handle_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        """Process an edit only when the original message is already cached.

        Discord can emit raw edit events for messages that predate this listener.
        Fetching every such message through REST causes needless history scans and
        quickly hits Discord rate limits, so uncached edits are intentionally ignored.
        """
        cached_message = self._message_cache.get(payload.message_id)
        if cached_message is not None:
            await self.handle_bot_response(cached_message)

    async def handle_message_edit(
        self,
        _before: discord.Message,
        after: discord.Message,
    ) -> None:
        """Process cached edits without making an avoidable REST request."""
        if after.guild is None or not after.author.bot:
            return
        self._message_cache[after.id] = after
        await self.handle_bot_response(after)

    async def handle_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Track configured-user reactions so Mudae Kakera receipts stay account-scoped."""
        if payload.guild_id is None:
            return
        pending_context = self._command_contexts.get(payload.message_id)
        if pending_context is not None and pending_context.expected_kind == "personalrare":
            self._logger.info(
                "Observed reaction %s from Discord user %s on pending $persr message %s",
                self._reaction_name(payload),
                payload.user_id,
                payload.message_id,
            )
        if self._is_mudae_success_reaction(payload):
            await self._handle_mudae_command_acknowledgement(payload)
            return
        identity = self._identity_for_ids(str(payload.guild_id), str(payload.user_id))
        if identity is None:
            return
        self._logger.info(
            "Observed reaction %s on Discord message %s for %s / %s; "
            "waiting for Mudae's receipt",
            payload.emoji,
            payload.message_id,
            identity.server,
            identity.account,
        )

    async def _handle_mudae_command_acknowledgement(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        """Import state for commands Mudae confirms only with a check reaction."""
        context = self._command_contexts.get(payload.message_id)
        if context is None or context.personal_rare_value is None:
            return
        if time.monotonic() - context.captured_at > self._CONTEXT_TTL_SECONDS:
            return
        acknowledgement_key = (payload.message_id, "personalrare")
        if acknowledgement_key in self._seen_payloads:
            return
        self._seen_payloads.add(acknowledgement_key)
        if len(self._seen_payloads) > 2000:
            self._seen_payloads = set(list(self._seen_payloads)[-1000:])

        value = context.personal_rare_value
        raw_message = f"Your current $personalrare: {value}"
        source = (
            f"discord:guild={payload.guild_id}:channel={payload.channel_id}:"
            f"message={payload.message_id}:reaction={self._reaction_name(payload)}"
        )
        self._logger.info(
            "Detected Mudae acknowledgement for $persr %s on Discord message %s "
            "for %s / %s",
            value,
            payload.message_id,
            context.identity.server,
            context.identity.account,
        )
        try:
            result = self._importer.import_message(
                raw_message,
                source,
                context.identity.server,
                context.identity.account,
                detected_kind="personalrare",
            )
        except Exception as error:  # Keep one acknowledgement from stopping the listener.
            self._logger.warning(
                "Could not import Mudae acknowledgement for message %s: %s",
                payload.message_id,
                error,
            )
            return
        self._logger.info(
            "Imported Discord Mudae acknowledgement %s: %s",
            payload.message_id,
            result.message,
        )

    @staticmethod
    def _personal_rare_command_value(content: str) -> int | None:
        """Return a numeric `$persr` argument, including 0 for server-default mode."""
        arguments = content.split()
        if len(arguments) < 2 or not arguments[1].isdigit():
            return None
        return int(arguments[1])

    @staticmethod
    def _personal_rare_argument_supplied(content: str) -> bool:
        """Tell default/help `$persr` apart from a value-setting attempt."""
        return len(content.split()) >= 2

    def _is_mudae_success_reaction(self, payload: discord.RawReactionActionEvent) -> bool:
        """Identify Mudae's standard checkmark acknowledgement reaction."""
        context = self._command_contexts.get(payload.message_id)
        if context is None or context.personal_rare_value is None:
            return False
        if self._mudae_user_id is not None and payload.user_id != self._mudae_user_id:
            return False
        if self._mudae_user_id is None and payload.user_id == int(context.user_id):
            return False
        return self._reaction_name(payload) in {"✅", "✔️", "☑️", "white_check_mark"}

    @staticmethod
    def _reaction_name(payload: discord.RawReactionActionEvent) -> str:
        emoji = getattr(payload, "emoji", "")
        return str(getattr(emoji, "name", None) or emoji)

    async def handle_bot_response(self, message: discord.Message) -> None:
        """Import one bot-authored Mudae response when a configured context exists."""
        if message.guild is None or not message.author.bot:
            return
        if self._mudae_user_id is not None and message.author.id != self._mudae_user_id:
            return
        raw_message = self.extract_message_text(message)
        if not raw_message:
            return
        received_event = self._receive_message(message, raw_message)
        if self._discord_message_repository is not None and received_event is None:
            return
        persisted_attribution = None
        if received_event is not None:
            try:
                persisted_attribution = self._discord_message_repository.get_server_attribution(
                    received_event.source_event_id
                )
            except Exception as error:
                self._logger.warning(
                    "Could not read Discord server attribution for source event %s; "
                    "downstream processing was not started: %s",
                    received_event.source_event_id,
                    error,
                )
                return
        durable_antidisable = False
        durable_antidisable_scan_id: int | None = None
        if self._discord_message_repository is not None:
            structural_kind = self._resolve_message_kind("antidisable", raw_message)
            if structural_kind == "antidisable":
                durable_resolution = self._resolve_durable_antidisable_response(
                    message,
                    raw_message,
                    persisted_attribution,
                    received_event,
                )
                if durable_resolution is None:
                    return
                context, durable_antidisable_scan_id = durable_resolution
                durable_antidisable = True
                kind = structural_kind
            else:
                context = None
                kind = None
        else:
            context = None
            kind = None

        self._message_cache[message.id] = message
        if len(self._message_cache) > 2000:
            self._message_cache = dict(list(self._message_cache.items())[-1000:])
        # A paginated harem message is edited in place. Its later edits can
        # arrive after another command (for example `$tu`) has replaced the
        # channel's latest-command context, so keep active scan context
        # independent from that per-channel command context.
        if not durable_antidisable:
            context = self._context_from_active_scan(message, raw_message)
            if context is None:
                context = self._context_from_interaction(message, raw_message)
            if context is None:
                context = self._context_from_reaction_receipt(message, raw_message)
            if context is None:
                context = self._context_from_pending_workflow(message, raw_message)
            if context is None:
                context = self._context_from_active_roll(message, raw_message)
            if context is not None and time.monotonic() - context.captured_at > self._CONTEXT_TTL_SECONDS:
                context = self._context_from_active_roll(message, raw_message)
            if context is None:
                context = self._context_from_parsed_account(message, raw_message)
            expected_kind = context.expected_kind if context is not None else None
            kind = self._resolve_message_kind(expected_kind, raw_message)
        attribution = self._resolve_and_record_server_attribution(
            message,
            raw_message,
            kind,
            context,
            persisted_attribution,
            received_event,
        )
        if attribution is None:
            return
        if (
            received_event is not None
            and kind is not None
            and kind not in self._SERVER_INDEPENDENT_KINDS
            and attribution.status != "resolved"
        ):
            if kind == "infokl":
                self._logger.info(
                    "Deferred server-scoped infokl source event %s because server attribution "
                    "is %s; no processing attempt was started",
                    received_event.source_event_id,
                    attribution.status,
                )
                return
            if kind == "mudapins":
                self._logger.info(
                    "Deferred mudapins source event %s because server attribution is %s; "
                    "no processing attempt was started",
                    received_event.source_event_id,
                    attribution.status,
                )
                return
            if kind in {"bonus", "sphere_result", "lootstate", "wishlist", "disablelist"}:
                self._logger.info(
                    "Deferred %s source event %s because server attribution is %s; "
                    "no processing attempt was started",
                    kind,
                    received_event.source_event_id,
                    attribution.status,
                )
                return
            self._record_unresolved_attribution(
                received_event,
                message.id,
                attribution.status,
            )
            return
        persisted_account_attribution = None
        durable_account_identity = None
        if (
            received_event is not None
            and kind in self._DURABLE_ACCOUNT_KINDS
            and self._is_durable_import_kind(kind)
        ):
            try:
                persisted_account_attribution = (
                    self._discord_message_repository.get_account_attribution(
                        received_event.source_event_id
                    )
                )
            except Exception as error:
                self._logger.warning(
                    "Could not read Discord account attribution for source event %s; "
                    "downstream processing was not started: %s",
                    received_event.source_event_id,
                    error,
                )
                return
            account_attribution = self._resolve_and_record_account_attribution(
                message,
                raw_message,
                kind,
                attribution,
                persisted_account_attribution,
                received_event,
            )
            if account_attribution is None:
                return
            if account_attribution.status != "resolved":
                self._logger.info(
                    "Deferred %s source event %s because account attribution is %s; "
                    "no processing attempt was started",
                    kind,
                    received_event.source_event_id,
                    account_attribution.status,
                )
                return
            durable_account_identity = ConfigAccount(
                server=account_attribution.server_name or attribution.server_name or "",
                account=account_attribution.account_name or "",
                discord_server_id=str(message.guild.id),
            )
        elif durable_antidisable and received_event is not None:
            durable_account_identity = self._record_durable_antidisable_account_attribution(
                received_event,
                context.identity,
            )
            if durable_account_identity is None:
                return
        if context is None and kind != "infokl":
            if kind == "roll" and durable_account_identity is None:
                self._warn_once_for_unattributed_roll(message)
            if durable_account_identity is None:
                return
        if context is not None and context.expected_kind == "personalrare" and context.personal_rare_argument_supplied:
            self._logger.info(
                "Ignoring textual Mudae response %s for a value-setting $persr command; "
                "waiting for the command acknowledgement reaction",
                message.id,
            )
            return
        if kind is None:
            detected = self._router.detect(raw_message)
            self._logger.warning(
                "Ignored Mudae response %s while tracking %s; parser did not accept it "
                "(router=%s, lines=%d)",
                message.id,
                context.expected_kind or "unknown",
                detected.kind,
                len(raw_message.splitlines()),
            )
            return
        import_identity = durable_account_identity or (context.identity if context is not None else None)
        if import_identity is not None and attribution.status == "resolved" and attribution.server_name is not None:
            import_identity = import_identity.model_copy(update={"server": attribution.server_name})
        if kind == "claim":
            # The claimant printed in Mudae's marriage confirmation is the
            # authoritative account.  The confirmation can arrive after a
            # burst of rolls from multiple configured accounts, so the
            # channel's latest command context may belong to somebody else.
            claim = self._parser.parse_claim_confirmation(raw_message)
            target_identity = (
                durable_account_identity
                or self._unique_identity_for_account(str(message.guild.id), claim.account_name)
            )
            if target_identity is None:
                self._logger.info(
                    "Ignoring Mudae claim %s for unconfigured account %s in %s",
                    message.id,
                    claim.account_name,
                    context.identity.server if context is not None else attribution.server_name,
                )
                return
            if context is not None and target_identity.account.casefold() != context.identity.account.casefold():
                self._logger.info(
                    "Attributed Mudae claim %s to %s / %s from the confirmation claimant "
                    "instead of stale context %s / %s",
                    message.id,
                    target_identity.server,
                    target_identity.account,
                    context.identity.server,
                    context.identity.account,
                )
            import_identity = target_identity
            if attribution.status == "resolved" and attribution.server_name is not None:
                import_identity = import_identity.model_copy(
                    update={"server": attribution.server_name}
                )
        if kind == "reaction_blocked":
            try:
                blocked = self._parser.parse_kakera_reaction_blocked(raw_message)
            except MudaeParseError:
                return
            if blocked.account_name.casefold() != context.identity.account.casefold():
                self._logger.info(
                    "Ignored blocked Kakera reaction %s for %s while tracking %s",
                    message.id,
                    blocked.account_name,
                    context.identity.account,
                )
                return
        if kind == "profile":
            profile = self._parser.parse_profile(raw_message)
            target_identity = (
                durable_account_identity
                or self._unique_identity_for_account(str(message.guild.id), profile.profile_name)
            )
            if target_identity is None:
                self._logger.info(
                    "Ignoring Mudae profile %s for unconfigured account %s in %s",
                    message.id,
                    profile.profile_name,
                    context.identity.server if context is not None else attribution.server_name,
                )
                return
            import_identity = target_identity
            if attribution.status == "resolved" and attribution.server_name is not None:
                import_identity = import_identity.model_copy(
                    update={"server": attribution.server_name}
                )
        if kind == "reaction_receipt":
            try:
                receipt = self._parser.parse_kakera_reaction_receipt(raw_message)
            except MudaeParseError:
                return
            if receipt.account_name.casefold() != context.identity.account.casefold():
                self._logger.info(
                    "Ignored Kakera receipt %s for %s while tracking %s",
                    message.id,
                    receipt.account_name,
                    context.identity.account,
                )
                return
        payload_key = (message.id, self._dedupe_payload_key(kind, raw_message))
        if payload_key in self._seen_payloads:
            return
        self._seen_payloads.add(payload_key)
        if len(self._seen_payloads) > 2000:
            self._seen_payloads = set(list(self._seen_payloads)[-1000:])

        processing_attempt = None
        if (
            received_event is not None
            and self._is_durable_import_kind(kind)
            and received_event.status == "processing"
        ):
            self._logger.info(
                "Did not import %s source event %s because processing is already active",
                kind,
                received_event.source_event_id,
            )
            return
        if received_event is not None and not (
            self._is_durable_import_kind(kind) and received_event.status == "succeeded"
        ):
            try:
                processing_attempt = self._discord_message_repository.begin_processing_attempt(
                    source_event_id=received_event.source_event_id,
                    parser_version=self._PARSER_VERSION,
                    router_version=self._ROUTER_VERSION,
                    started_at=datetime.now(timezone.utc),
                )
            except DiscordMessageProcessingConflictError as error:
                # Succeeded, terminal-failure, and active-processing replays retain
                # their durable lifecycle state while preserving existing replay behavior.
                self._logger.info(
                    "Did not begin a Discord processing attempt for source event %s: %s",
                    received_event.source_event_id,
                    error,
                )
                if self._is_durable_import_kind(kind):
                    self._logger.info(
                        "Did not import %s source event %s because its durable lifecycle "
                        "is not retryable",
                        kind,
                        received_event.source_event_id,
                    )
                    return
            except DiscordMessageProcessingError as error:
                self._logger.warning(
                    "Could not begin Discord processing for source event %s; "
                    "downstream processing was not started: %s",
                    received_event.source_event_id,
                    error,
                )
                return
            except Exception as error:
                self._logger.warning(
                    "Could not begin Discord processing for source event %s; "
                    "downstream processing was not started: %s",
                    received_event.source_event_id,
                    error,
                )
                return

        self._logger.info(
            "Detected Mudae %s message %s for %s / %s",
            kind,
            message.id,
            attribution.server_name if kind == "infokl" else import_identity.server,
            "server-scoped" if kind == "infokl" else import_identity.account,
        )

        durable_success_recorded = False
        try:
            scan_id = (
                durable_antidisable_scan_id
                if durable_antidisable
                else (
                    self._scan_id_for_page(kind, raw_message, import_identity)
                    if import_identity is not None
                    else None
                )
            )
            if kind in self._SCAN_KINDS:
                self._scan_contexts[(int(message.guild.id), int(message.channel.id), kind)] = replace(
                    context,
                    expected_kind=kind,
                    captured_at=time.monotonic(),
                )
            source = (
                f"discord:guild={message.guild.id}:channel={message.channel.id}:message={message.id}"
            )
            import_kwargs: dict[str, Any] = {
                "harem_scan_id": scan_id,
                "detected_kind": kind,
            }
            if self._is_durable_import_kind(kind) and received_event is not None:
                observed_at = datetime.now(timezone.utc)
                finished_at = datetime.now(timezone.utc)
                context_kwargs = {
                    "source_event_id": received_event.source_event_id,
                    "attempt_id": (
                        processing_attempt.attempt_id if processing_attempt is not None else None
                    ),
                    "finished_at": finished_at,
                }
                if kind == "roll":
                    import_kwargs["durable_roll_context"] = DurableRollImportContext(
                        **context_kwargs
                    )
                elif kind == "profile":
                    import_kwargs["durable_profile_context"] = DurableProfileImportContext(
                        **context_kwargs
                    )
                elif kind == "claim":
                    import_kwargs["durable_claim_context"] = DurableClaimImportContext(
                        **context_kwargs
                    )
                elif kind == "infokl":
                    import_kwargs["durable_infokl_context"] = DurableInfoklImportContext(
                        **context_kwargs
                    )
                elif kind == "settings":
                    import_kwargs["durable_settings_context"] = DurableSettingsImportContext(
                        **context_kwargs
                    )
                elif kind == "timers":
                    import_kwargs["durable_timer_context"] = DurableTimerImportContext(
                        source_event_id=received_event.source_event_id,
                        attempt_id=(
                            processing_attempt.attempt_id if processing_attempt is not None else None
                        ),
                        server=import_identity.server,
                        account=import_identity.account,
                        raw=raw_message,
                        source=source,
                        observed_at=observed_at,
                        finished_at=finished_at,
                    )
                elif kind == "kakera":
                    import_kwargs["durable_kakera_context"] = DurableKakeraImportContext(
                        source_event_id=received_event.source_event_id,
                        attempt_id=(
                            processing_attempt.attempt_id if processing_attempt is not None else None
                        ),
                        server=import_identity.server,
                        account=import_identity.account,
                        raw=raw_message,
                        source=source,
                        observed_at=observed_at,
                        finished_at=finished_at,
                    )
                elif kind == "mudapins":
                    import_kwargs["durable_mudapins_context"] = DurableMudapinsImportContext(
                        source_event_id=received_event.source_event_id,
                        attempt_id=(
                            processing_attempt.attempt_id if processing_attempt is not None else None
                        ),
                        server=import_identity.server,
                        account=import_identity.account,
                        raw=raw_message,
                        source=source,
                        observed_at=observed_at,
                        finished_at=finished_at,
                    )
                elif kind == "towerstate":
                    import_kwargs["durable_tower_state_context"] = (
                        DurableTowerStateImportContext(
                            source_event_id=received_event.source_event_id,
                            attempt_id=(
                                processing_attempt.attempt_id
                                if processing_attempt is not None
                                else None
                            ),
                            server=import_identity.server,
                            account=import_identity.account,
                            raw=raw_message,
                            source=source,
                            observed_at=observed_at,
                            finished_at=finished_at,
                        )
                    )
                elif kind == "lootstate":
                    import_kwargs["durable_kakeraloot_state_context"] = (
                        DurableKakeralootStateImportContext(
                            source_event_id=received_event.source_event_id,
                            attempt_id=(
                                processing_attempt.attempt_id
                                if processing_attempt is not None
                                else None
                            ),
                            server=import_identity.server,
                            account=import_identity.account,
                            raw=raw_message,
                            source=source,
                            observed_at=observed_at,
                            finished_at=finished_at,
                        )
                    )
                elif kind == "sphere_result":
                    import_kwargs["durable_sphere_result_context"] = (
                        DurableSphereResultImportContext(
                            source_event_id=received_event.source_event_id,
                            attempt_id=(
                                processing_attempt.attempt_id
                                if processing_attempt is not None
                                else None
                            ),
                            server=import_identity.server,
                            account=import_identity.account,
                            raw=raw_message,
                            source=source,
                            observed_at=observed_at,
                            finished_at=finished_at,
                        )
                    )
                elif kind == "bonus":
                    import_kwargs["durable_player_bonus_context"] = (
                        DurablePlayerBonusImportContext(
                            source_event_id=received_event.source_event_id,
                            attempt_id=(
                                processing_attempt.attempt_id
                                if processing_attempt is not None
                                else None
                            ),
                            server=import_identity.server,
                            account=import_identity.account,
                            raw=raw_message,
                            source=source,
                            observed_at=observed_at,
                            finished_at=finished_at,
                        )
                    )
                elif kind == "wishlist":
                    import_kwargs["durable_wishlist_context"] = DurableWishlistImportContext(
                        source_event_id=received_event.source_event_id,
                        attempt_id=(
                            processing_attempt.attempt_id
                            if processing_attempt is not None
                            else None
                        ),
                        server=import_identity.server,
                        account=import_identity.account,
                        raw=raw_message,
                        source=source,
                        observed_at=observed_at,
                        finished_at=finished_at,
                    )
                elif kind == "disablelist":
                    import_kwargs["durable_disablelist_context"] = DurableDisableListImportContext(
                        source_event_id=received_event.source_event_id,
                        attempt_id=(
                            processing_attempt.attempt_id
                            if processing_attempt is not None
                            else None
                        ),
                        server=import_identity.server,
                        account=import_identity.account,
                        raw=raw_message,
                        source=source,
                        observed_at=observed_at,
                        finished_at=finished_at,
                    )
                elif kind == "antidisable":
                    import_kwargs["durable_antidisable_page_context"] = (
                        DurableAntidisablePageImportContext(
                            source_event_id=received_event.source_event_id,
                            attempt_id=(
                                processing_attempt.attempt_id
                                if processing_attempt is not None
                                else None
                            ),
                            server=import_identity.server,
                            account=import_identity.account,
                            raw=raw_message,
                            source=source,
                            observed_at=observed_at,
                            finished_at=finished_at,
                        )
                    )
                else:
                    raise RuntimeError(f"Unsupported durable import kind: {kind}")
            result = self._importer.import_message(
                raw_message,
                source,
                attribution.server_name if kind == "infokl" else import_identity.server,
                None if kind == "infokl" else import_identity.account,
                **import_kwargs,
            )
            durable_success_recorded = bool(
                getattr(result, "durable_success_recorded", False)
            )
            if (
                self._is_durable_import_kind(kind)
                and received_event is not None
                and received_event.status == "succeeded"
                and not getattr(result, "replay_skipped", False)
            ):
                raise RuntimeError(
                    f"Succeeded durable {kind} replay did not return a replay-skipped result."
                )
            self._logger.info(
                "Imported Discord Mudae message %s: %s",
                message.id,
                result.message,
            )
            if getattr(result, "replay_skipped", False):
                self._logger.info(
                    "Skipped duplicate durable %s projection for source event %s",
                    kind,
                    received_event.source_event_id if received_event is not None else "unknown",
                )
        except Exception as error:  # Keep one malformed Discord payload from stopping the listener.
            self._record_processing_failure(received_event, processing_attempt, error, message.id)
            return

        try:
            if kind == "divorce_declined" or kind == "divorce_complete":
                self._consume_context(message.channel.id, context)
            if kind in {"gift_kakera", "gift_spheres", "gift_character", "trade"} and self._transaction_is_terminal(kind, raw_message):
                self._consume_context(message.channel.id, context)
            elif context is not None and context.expected_kind not in {
                "divorce",
                "divorce_confirmation",
                *self._SCAN_KINDS,
            }:
                self._consume_context(message.channel.id, context)
            self._complete_scan_if_last_page(
                kind,
                raw_message,
                import_identity,
                scan_id,
            )
        except Exception as cleanup_error:
            if durable_success_recorded:
                durable_label = kind if self._is_durable_import_kind(kind) else "import"
                self._logger.error(
                    "Best-effort cleanup failed after durable %s success for message %s; "
                    "the succeeded source event and attempt remain unchanged: %s",
                    durable_label,
                    message.id,
                    cleanup_error,
                )
            else:
                self._record_processing_failure(
                    received_event,
                    processing_attempt,
                    cleanup_error,
                    message.id,
                )
            return

        if processing_attempt is not None and not durable_success_recorded:
            try:
                self._discord_message_repository.mark_processing_success(
                    source_event_id=processing_attempt.source_event_id,
                    attempt_id=processing_attempt.attempt_id,
                    finished_at=datetime.now(timezone.utc),
                )
            except Exception as error:
                self._logger.error(
                    "Could not mark Discord processing attempt %s successful after downstream "
                    "work completed; lifecycle state was not reported as successful: %s",
                    processing_attempt.attempt_id,
                    error,
                )

    def _resolve_and_record_server_attribution(
        self,
        message: discord.Message,
        raw_message: str,
        kind: str | None,
        context: DiscordCommandContext | None,
        persisted: DiscordSourceEventServerAttribution | None,
        received_event: ReceivedMessageEvent | None,
    ) -> _ServerAttributionDecision | None:
        """Resolve durable server evidence and persist only safe transitions."""
        try:
            live = self._live_server_attribution(message, raw_message, kind, context)
        except Exception as error:
            self._logger.warning(
                "Could not gather Discord server attribution evidence for message %s; "
                "downstream processing was not started: %s",
                message.id,
                error,
            )
            return None

        if persisted is not None and persisted.status == "resolved":
            if (
                live.authoritative_evidence
                and (
                    live.status != "resolved"
                    or live.server_name is None
                    or live.server_name.casefold()
                    != (persisted.server_name or "").casefold()
                )
            ):
                self._logger.warning(
                    "Discord server attribution conflict for source event %s; "
                    "persisted=%s live=%s",
                    persisted.source_event_id,
                    persisted.server_name,
                    live.server_name or live.status,
                )
                return None
            return _ServerAttributionDecision("resolved", persisted.server_name)

        if persisted is not None and live.status != "resolved":
            decision = _ServerAttributionDecision(persisted.status, persisted.server_name)
        else:
            decision = live

        should_record = received_event is not None and (
            persisted is None
            or persisted.status in {"unresolved", "ambiguous"}
            and decision.status == "resolved"
        )
        if not should_record:
            return decision
        try:
            return self._discord_message_repository.record_server_attribution(
                received_event.source_event_id,
                status=decision.status,
                server_name=decision.server_name,
                recorded_at=datetime.now(timezone.utc),
            )
        except Exception as error:
            self._logger.warning(
                "Could not record Discord server attribution for source event %s; "
                "downstream processing was not started: %s",
                received_event.source_event_id,
                error,
            )
            return None

    def _live_server_attribution(
        self,
        message: discord.Message,
        raw_message: str,
        kind: str | None,
        context: DiscordCommandContext | None,
    ) -> _ServerAttributionDecision:
        """Resolve authoritative live server evidence without using weak fallbacks."""
        strong_names: set[str] = set()
        strong_names.update(self._interaction_server_names(message))
        if kind in {"claim", "profile", "reaction_receipt", "reaction_blocked"}:
            account_name = self._parsed_account_name(kind, raw_message)
            if account_name is not None:
                strong_names.update(
                    self._server_names_for_account(str(message.guild.id), account_name)
                )
        if context is not None and context.evidence_source in {
            "command_author",
            "workflow",
            "active_scan",
            "interaction",
        }:
            if context.evidence_source == "command_author":
                strong_names.update(
                    identity.server
                    for identity in self._configured_accounts_for_guild(str(message.guild.id))
                    if identity.discord_user_id == context.user_id
                )
            else:
                strong_names.add(context.identity.server)
        if strong_names:
            if len(strong_names) != 1:
                return _ServerAttributionDecision("ambiguous", None, True)
            return _ServerAttributionDecision("resolved", next(iter(strong_names)), True)

        guild_names = self._server_names_for_guild(str(message.guild.id))
        if kind in {"bonus", "sphere_result", "lootstate", "wishlist", "disablelist"}:
            if len(guild_names) > 1:
                return _ServerAttributionDecision("ambiguous", None)
            return _ServerAttributionDecision("unresolved", None)
        if len(guild_names) == 1:
            return _ServerAttributionDecision("resolved", next(iter(guild_names)), True)
        if len(guild_names) > 1:
            return _ServerAttributionDecision("ambiguous", None)
        return _ServerAttributionDecision("unresolved", None)

    def _resolve_and_record_account_attribution(
        self,
        message: discord.Message,
        raw_message: str,
        kind: str,
        server_attribution: _ServerAttributionDecision,
        persisted: DiscordSourceEventAccountAttribution | None,
        received_event: ReceivedMessageEvent,
    ) -> _AccountAttributionDecision | None:
        """Resolve and durably record account evidence for the durable account routes."""
        if server_attribution.server_name is None:
            return None
        live = self._live_account_attribution(
            message,
            raw_message,
            kind,
            server_attribution.server_name,
        )
        if persisted is not None and persisted.status == "resolved":
            if (
                persisted.server_name is None
                or persisted.server_name.casefold() != server_attribution.server_name.casefold()
                or persisted.account_name is None
            ):
                self._logger.warning(
                    "Discord account attribution conflict for source event %s; "
                    "persisted account=%s / %s, server=%s",
                    persisted.source_event_id,
                    persisted.server_name,
                    persisted.account_name,
                    server_attribution.server_name,
                )
                return None
            if live.authoritative_evidence and (
                live.status != "resolved"
                or live.server_name is None
                or live.account_name is None
                or live.server_name.casefold() != persisted.server_name.casefold()
                or live.account_name.casefold() != persisted.account_name.casefold()
            ):
                self._logger.warning(
                    "Discord account attribution conflict for source event %s; "
                    "persisted=%s / %s, live=%s / %s",
                    persisted.source_event_id,
                    persisted.server_name,
                    persisted.account_name,
                    live.server_name,
                    live.account_name or live.status,
                )
                return None
            return _AccountAttributionDecision(
                "resolved",
                persisted.server_name,
                persisted.account_name,
            )

        if persisted is not None and live.status != "resolved":
            return _AccountAttributionDecision(
                persisted.status,
                persisted.server_name,
                persisted.account_name,
            )

        decision = live
        should_record = persisted is None or (
            persisted.status in {"unresolved", "ambiguous"}
            and decision.status == "resolved"
        )
        if not should_record:
            return decision
        try:
            return self._discord_message_repository.record_account_attribution(
                received_event.source_event_id,
                status=decision.status,
                server_name=decision.server_name,
                account_name=decision.account_name,
                recorded_at=datetime.now(timezone.utc),
            )
        except Exception as error:
            self._logger.warning(
                "Could not record Discord account attribution for source event %s; "
                "downstream processing was not started: %s",
                received_event.source_event_id,
                error,
            )
            return None

    def _live_account_attribution(
        self,
        message: discord.Message,
        raw_message: str,
        kind: str,
        server_name: str,
    ) -> _AccountAttributionDecision:
        """Gather only route-approved strong account evidence."""
        if kind in {"profile", "claim"}:
            account_name = self._parsed_account_name(kind, raw_message)
            if account_name is not None:
                matches = self._configured_accounts_for_server(
                    str(message.guild.id), server_name, account_name
                )
                if len(matches) == 1:
                    identity = matches[0]
                    return _AccountAttributionDecision(
                        "resolved", identity.server, identity.account, True
                    )
                return _AccountAttributionDecision(
                    "ambiguous" if len(matches) > 1 else "unresolved",
                    None,
                    None,
                    True,
                )
        candidates: list[ConfigAccount] = []
        ambiguous = False
        interaction_user_id = self._interaction_user_id(message)
        if interaction_user_id is not None:
            matches = self._configured_accounts_for_server_user(
                str(message.guild.id), server_name, interaction_user_id
            )
            if len(matches) != 1:
                ambiguous = ambiguous or len(matches) > 1
            candidates.extend(matches)

        for context in self._compatible_account_contexts(message, raw_message, kind):
            matches = self._configured_accounts_for_server_user(
                str(message.guild.id), server_name, context.user_id
            )
            if len(matches) != 1:
                ambiguous = ambiguous or len(matches) > 1
            candidates.extend(matches)

        distinct = {
            (identity.server.casefold(), identity.account.casefold()): identity
            for identity in candidates
        }
        if ambiguous or len(distinct) > 1:
            return _AccountAttributionDecision("ambiguous", None, None, True)
        if len(distinct) == 1:
            identity = next(iter(distinct.values()))
            return _AccountAttributionDecision(
                "resolved", identity.server, identity.account, True
            )
        return _AccountAttributionDecision("unresolved", None, None)

    def _configured_accounts_for_server(
        self,
        guild_id: str,
        server_name: str,
        account_name: str,
    ) -> tuple[ConfigAccount, ...]:
        normalized_server = server_name.casefold()
        normalized_account = account_name.casefold()
        return tuple(
            identity
            for identity in self._configured_accounts_for_guild(guild_id)
            if identity.server.casefold() == normalized_server
            and identity.account.casefold() == normalized_account
        )

    def _configured_accounts_for_server_user(
        self,
        guild_id: str,
        server_name: str,
        user_id: str,
    ) -> tuple[ConfigAccount, ...]:
        normalized_server = server_name.casefold()
        return tuple(
            identity
            for identity in self._configured_accounts_for_guild(guild_id)
            if identity.server.casefold() == normalized_server
            and identity.discord_user_id == str(user_id)
        )

    def _compatible_account_contexts(
        self,
        message: discord.Message,
        raw_message: str,
        kind: str,
    ) -> tuple[DiscordCommandContext, ...]:
        """Return active command/interaction contexts compatible with this route."""
        now = time.monotonic()
        contexts: list[DiscordCommandContext] = []
        for key, context in list(self._pending_contexts.items()):
            if key[:2] != (str(message.guild.id), int(message.channel.id)):
                continue
            if now - context.captured_at > self._CONTEXT_TTL_SECONDS:
                self._pending_contexts.pop(key, None)
                continue
            if context.evidence_source not in {"command_author", "interaction"}:
                continue
            if self._resolve_message_kind(context.expected_kind, raw_message) != kind:
                continue
            contexts.append(context)
        return tuple(contexts)

    @staticmethod
    def _interaction_user_id(message: discord.Message) -> str | None:
        metadata = getattr(message, "interaction_metadata", None)
        if metadata is None and not isinstance(message, discord.Message):
            metadata = getattr(message, "interaction", None)
        user = getattr(metadata, "user", None)
        user_id = getattr(user, "id", None)
        return str(user_id) if user_id is not None else None

    def _configured_accounts_for_guild(self, guild_id: str) -> tuple[ConfigAccount, ...]:
        normalized_guild_id = str(guild_id)
        return tuple(
            identity
            for identity in self._config.profile(self._profile_name).accounts
            if identity.discord_server_id == normalized_guild_id
        )

    def _server_names_for_guild(self, guild_id: str) -> set[str]:
        return {identity.server for identity in self._configured_accounts_for_guild(guild_id)}

    def _server_names_for_account(self, guild_id: str, account_name: str) -> set[str]:
        normalized_account = account_name.casefold()
        return {
            identity.server
            for identity in self._configured_accounts_for_guild(guild_id)
            if identity.account.casefold() == normalized_account
        }

    def _unique_identity_for_account(
        self,
        guild_id: str,
        account_name: str,
    ) -> ConfigAccount | None:
        matches = [
            identity
            for identity in self._configured_accounts_for_guild(guild_id)
            if identity.account.casefold() == account_name.casefold()
        ]
        distinct = {
            (identity.server.casefold(), identity.account.casefold()): identity
            for identity in matches
        }
        if len(distinct) != 1:
            return None
        return next(iter(distinct.values()))

    def _interaction_server_names(self, message: discord.Message) -> set[str]:
        metadata = getattr(message, "interaction_metadata", None)
        if metadata is None and not isinstance(message, discord.Message):
            metadata = getattr(message, "interaction", None)
        user = getattr(metadata, "user", None)
        if user is None or message.guild is None:
            return set()
        user_id = str(user.id)
        return {
            identity.server
            for identity in self._configured_accounts_for_guild(str(message.guild.id))
            if identity.discord_user_id == user_id
        }

    def _parsed_account_name(self, kind: str, raw_message: str) -> str | None:
        try:
            if kind == "claim":
                return self._parser.parse_claim_confirmation(raw_message).account_name
            if kind == "profile":
                return self._parser.parse_profile(raw_message).profile_name
            if kind == "reaction_receipt":
                return self._parser.parse_kakera_reaction_receipt(raw_message).account_name
            if kind == "reaction_blocked":
                return self._parser.parse_kakera_reaction_blocked(raw_message).account_name
        except MudaeParseError:
            return None
        return None

    def _record_unresolved_attribution(
        self,
        received_event: ReceivedMessageEvent,
        message_id: int,
        attribution_status: Literal["unresolved", "ambiguous"],
    ) -> None:
        """Keep a server-unresolved event retryable without dispatching imports."""
        repository = self._discord_message_repository
        if repository is None:
            return
        try:
            attempt = repository.begin_processing_attempt(
                source_event_id=received_event.source_event_id,
                parser_version=self._PARSER_VERSION,
                router_version=self._ROUTER_VERSION,
                started_at=datetime.now(timezone.utc),
            )
        except DiscordMessageProcessingConflictError as error:
            self._logger.info(
                "Did not begin unresolved Discord processing for source event %s: %s",
                received_event.source_event_id,
                error,
            )
            return
        except Exception as error:
            self._logger.warning(
                "Could not begin unresolved Discord processing for source event %s; "
                "downstream processing was not started: %s",
                received_event.source_event_id,
                error,
            )
            return
        failure_code = (
            "ambiguous_server_attribution"
            if attribution_status == "ambiguous"
            else "unresolved_server_attribution"
        )
        try:
            repository.mark_processing_failure(
                source_event_id=attempt.source_event_id,
                attempt_id=attempt.attempt_id,
                status="unresolved_attribution",
                retryable=True,
                failure_code=failure_code,
                failure_detail=(
                    "Durable server attribution was ambiguous."
                    if attribution_status == "ambiguous"
                    else "Durable server attribution was unresolved."
                ),
                finished_at=datetime.now(timezone.utc),
            )
        except Exception as error:
            self._logger.warning(
                "Could not record unresolved Discord attribution for source event %s "
                "after message %s: %s",
                received_event.source_event_id,
                message_id,
                error,
            )
            return
        self._logger.info(
            "Deferred Discord message %s because server attribution is %s; "
            "the source event remains retryable",
            message_id,
            attribution_status,
        )

    def _receive_message_record(
        self,
        message: discord.Message,
        raw_message: str,
        *,
        received_at: datetime | None = None,
    ) -> tuple[ReceivedMessageEvent, Any] | None:
        """Persist one accepted message revision and retain its stable envelope."""
        repository = self._discord_message_repository
        if repository is None:
            return None
        try:
            durable_received_at = received_at or datetime.now(timezone.utc)
            envelope = build_message_receive_envelope(
                guild_id=str(message.guild.id),
                channel_id=str(message.channel.id),
                message_id=str(message.id),
                raw_text=raw_message,
                source_revision_at=self._source_revision_at(message),
                received_at=durable_received_at,
                payload_json=None,
                payload_capture_version=None,
            )
            received_event = repository.receive_message(
                aggregate_key=envelope.aggregate_key,
                revision_key=envelope.revision_key,
                event_key=envelope.event_key,
                event_kind=envelope.event_kind,
                raw_text=envelope.raw_text,
                payload_json=envelope.payload_json,
                payload_capture_version=envelope.payload_capture_version,
                source_observed_at=envelope.source_observed_at,
                received_at=envelope.received_at,
            )
            return received_event, envelope
        except Exception as error:  # Keep callback stability while refusing downstream work.
            self._logger.warning(
                "Could not durably receive Discord message "
                "guild=%s channel=%s message=%s: %s",
                getattr(getattr(message, "guild", None), "id", None),
                getattr(getattr(message, "channel", None), "id", None),
                getattr(message, "id", None),
                error,
            )
            return None

    def _receive_message(
        self, message: discord.Message, raw_message: str
    ) -> ReceivedMessageEvent | None:
        """Persist an accepted message revision before downstream processing."""
        received_record = self._receive_message_record(message, raw_message)
        return received_record[0] if received_record is not None else None

    @staticmethod
    def _message_received_at(message: discord.Message) -> datetime:
        """Use Discord's message time when available, otherwise the receive time."""
        created_at = getattr(message, "created_at", None)
        if isinstance(created_at, datetime) and created_at.utcoffset() is not None:
            return created_at.astimezone(timezone.utc)
        return datetime.now(timezone.utc)

    def _start_antidisable_workflow(
        self,
        message: discord.Message,
        context: DiscordCommandContext,
        raw_message: str,
    ) -> DiscordCommandContext | None:
        """Durably receive one `$adl` request and create its workflow atomically."""
        received_at = self._message_received_at(message)
        received_record = self._receive_message_record(
            message,
            raw_message,
            received_at=received_at,
        )
        if received_record is None:
            return None
        _received_event, envelope = received_record
        repository = self._discord_message_repository
        catalog_repository = getattr(self._catalog, "_repository", None)
        if repository is None or catalog_repository is None:
            self._logger.warning(
                "Could not start durable antidisable workflow for Discord message %s: "
                "required repositories are unavailable",
                message.id,
            )
            return None
        try:
            workflow = repository.get_antidisable_workflow_by_request_message(
                envelope.aggregate_key
            )
            if workflow is None:
                database_path = getattr(repository, "_database_path", None)
                connection = connect(database_path)
                try:
                    connection.execute("BEGIN")
                    scan_id = catalog_repository._begin_antidisable_scan_with_connection(
                        connection,
                        server=context.identity.server,
                        account=context.identity.account,
                        observed_at=received_at,
                    )
                    workflow_result = repository._create_antidisable_workflow_with_connection(
                        connection,
                        scan_id=scan_id,
                        request_message_aggregate_key=envelope.aggregate_key,
                        requesting_user_id=context.user_id,
                        created_at=received_at,
                        expires_at=received_at
                        + timedelta(seconds=self._CONTEXT_TTL_SECONDS),
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                finally:
                    connection.close()
                workflow = workflow_result.workflow
            scan_key = (
                context.identity.server.casefold(),
                context.identity.account.casefold(),
                "antidisable",
            )
            self._scan_ids[scan_key] = workflow.harem_scan_id
            refreshed_context = replace(context, captured_at=time.monotonic())
            self._scan_contexts[
                (int(message.guild.id), int(message.channel.id), "antidisable")
            ] = refreshed_context
            return refreshed_context
        except Exception as error:
            self._logger.warning(
                "Could not start durable antidisable workflow for Discord message %s: %s",
                message.id,
                error,
            )
            return None

    @staticmethod
    def _message_aggregate_key(message: discord.Message) -> MessageAggregateKey:
        return MessageAggregateKey(
            SourcePlatform.DISCORD,
            str(message.guild.id),
            str(message.channel.id),
            str(message.id),
        )

    def _resolve_durable_antidisable_response(
        self,
        message: discord.Message,
        raw_message: str,
        persisted_attribution: DiscordSourceEventServerAttribution | None,
        received_event: ReceivedMessageEvent | None,
    ) -> tuple[DiscordCommandContext, int] | None:
        """Resolve one structurally valid `$adl` response through durable ownership."""
        repository = self._discord_message_repository
        if repository is None or received_event is None:
            return None
        response_key = self._message_aggregate_key(message)
        workflow = repository.get_antidisable_workflow_by_response_message(response_key)
        if workflow is None:
            lookup_time = self._message_received_at(message)
            candidates = repository.active_antidisable_workflows_for_channel(
                str(message.guild.id),
                str(message.channel.id),
                lookup_time,
            )
            if len(candidates) != 1:
                attribution = self._resolve_and_record_server_attribution(
                    message,
                    raw_message,
                    "antidisable",
                    None,
                    persisted_attribution,
                    received_event,
                )
                if attribution is not None:
                    self._record_unresolved_antidisable_attribution(
                        received_event,
                        message.id,
                        "ambiguous" if len(candidates) > 1 else "unresolved",
                    )
                return None
            candidate = candidates[0]
            try:
                repository.bind_antidisable_response(
                    scan_id=candidate.harem_scan_id,
                    response_message_aggregate_key=response_key,
                    bound_at=lookup_time,
                )
            except Exception as error:
                # A concurrent/replayed delivery may have completed the binding
                # between the candidate read and this write. Reload it; otherwise
                # fail closed without selecting another candidate.
                workflow = repository.get_antidisable_workflow_by_response_message(response_key)
                if workflow is None:
                    self._logger.warning(
                        "Could not durably bind antidisable response %s: %s",
                        message.id,
                        error,
                    )
                    return None
            else:
                workflow = candidate

        progress = self._catalog.harem_scan_progress(workflow.harem_scan_id)
        if progress is None or progress.scan_kind != "antidisable":
            self._logger.warning(
                "Could not recover antidisable scan %s for response %s",
                workflow.harem_scan_id,
                message.id,
            )
            return None
        identity = ConfigAccount(
            server=progress.server_name,
            account=progress.account_name,
            discord_server_id=str(message.guild.id),
            discord_user_id=workflow.requesting_user_id,
        )
        return (
            DiscordCommandContext(
                server_id=str(message.guild.id),
                user_id=workflow.requesting_user_id,
                identity=identity,
                captured_at=time.monotonic(),
                expected_kind="antidisable",
                evidence_source="workflow",
            ),
            workflow.harem_scan_id,
        )

    def _record_durable_antidisable_account_attribution(
        self,
        received_event: ReceivedMessageEvent,
        identity: ConfigAccount,
    ) -> ConfigAccount | None:
        """Persist account context recovered from the bound durable workflow."""
        repository = self._discord_message_repository
        if repository is None:
            return None
        try:
            existing = repository.get_account_attribution(received_event.source_event_id)
            if existing is not None and existing.status == "resolved":
                if (
                    existing.server_name is None
                    or existing.account_name is None
                    or existing.server_name.casefold() != identity.server.casefold()
                    or existing.account_name.casefold() != identity.account.casefold()
                ):
                    self._logger.warning(
                        "Durable antidisable response %s has conflicting account attribution",
                        received_event.source_event_id,
                    )
                    return None
            repository.record_account_attribution(
                received_event.source_event_id,
                status="resolved",
                server_name=identity.server,
                account_name=identity.account,
                recorded_at=datetime.now(timezone.utc),
            )
            return identity
        except Exception as error:
            self._logger.warning(
                "Could not record durable antidisable account attribution for source event %s: %s",
                received_event.source_event_id,
                error,
            )
            return None

    def _record_unresolved_antidisable_attribution(
        self,
        received_event: ReceivedMessageEvent,
        message_id: int,
        attribution_status: Literal["unresolved", "ambiguous"],
    ) -> None:
        repository = self._discord_message_repository
        if repository is None:
            return
        try:
            repository.record_account_attribution(
                received_event.source_event_id,
                status=attribution_status,
                server_name=None,
                account_name=None,
                recorded_at=datetime.now(timezone.utc),
            )
        except Exception as error:
            self._logger.warning(
                "Could not record unresolved antidisable account attribution for source event %s: %s",
                received_event.source_event_id,
                error,
            )
        self._record_unresolved_attribution(received_event, message_id, attribution_status)

    def _record_processing_failure(
        self,
        received_event: ReceivedMessageEvent | None,
        processing_attempt: Any,
        error: Exception,
        message_id: int,
    ) -> None:
        """Record a retryable downstream failure without masking the original error."""
        if processing_attempt is None or received_event is None:
            self._logger.warning("Could not import Mudae message %s: %s", message_id, error)
            return
        try:
            self._discord_message_repository.mark_processing_failure(
                source_event_id=processing_attempt.source_event_id,
                attempt_id=processing_attempt.attempt_id,
                status="failed",
                retryable=True,
                failure_code="downstream_processing_error",
                failure_detail=str(error),
                finished_at=datetime.now(timezone.utc),
            )
        except Exception as completion_error:
            self._logger.error(
                "Could not record retryable failure for Discord processing attempt %s; "
                "original processing error preserved: %s; lifecycle error: %s",
                processing_attempt.attempt_id,
                error,
                completion_error,
            )
            return
        self._logger.warning(
            "Could not import Mudae message %s; recorded retryable processing failure: %s",
            message_id,
            error,
        )

    @staticmethod
    def _source_revision_at(message: discord.Message) -> datetime | None:
        """Use only Discord's aware edit timestamp as a revision marker."""
        edited_at = getattr(message, "edited_at", None)
        if not isinstance(edited_at, datetime) or edited_at.utcoffset() is None:
            return None
        return edited_at

    def _context_from_active_scan(
        self,
        message: discord.Message,
        raw_message: str,
    ) -> DiscordCommandContext | None:
        """Keep an active page scan attributable after another command runs.

        Mudae reuses one Discord message for pagination. The edited page may
        arrive after the user has run an unrelated command in the channel, so
        the latest-command context is not sufficient for scan pages.
        """
        if message.guild is None:
            return None
        kind = self._router.detect(raw_message).kind
        if kind not in self._SCAN_KINDS:
            return None
        key = (int(message.guild.id), int(message.channel.id), kind)
        context = self._scan_contexts.get(key)
        if context is None:
            return None
        scan_key = (
            context.identity.server.casefold(),
            context.identity.account.casefold(),
            self._SCAN_KINDS[kind],
        )
        if scan_key not in self._scan_ids:
            self._scan_contexts.pop(key, None)
            return None
        return context

    def _remember_context(self, channel_id: int, context: DiscordCommandContext) -> None:
        """Track a workflow by its initiating user without replacing another user's view."""
        key = (context.server_id, int(channel_id), context.user_id)
        self._pending_contexts[key] = context
        existing = self._contexts.get(int(channel_id))
        if (
            existing is None
            or existing.user_id == context.user_id
            or time.monotonic() - existing.captured_at > self._CONTEXT_TTL_SECONDS
        ):
            self._contexts[int(channel_id)] = context

    def _pending_context_for_user(
        self,
        channel_id: int,
        server_id: str,
        user_id: str,
    ) -> DiscordCommandContext | None:
        """Return the live workflow owned by one Discord user in one channel."""
        key = (server_id, int(channel_id), user_id)
        context = self._pending_contexts.get(key)
        if context is None:
            return None
        if time.monotonic() - context.captured_at > self._CONTEXT_TTL_SECONDS:
            self._pending_contexts.pop(key, None)
            if self._contexts.get(int(channel_id)) is context:
                self._contexts.pop(int(channel_id), None)
            return None
        return context

    def _context_from_pending_workflow(
        self,
        message: discord.Message,
        raw_message: str,
    ) -> DiscordCommandContext | None:
        """Select one compatible pending workflow, preserving ambiguity as unresolved."""
        now = time.monotonic()
        candidates: list[DiscordCommandContext] = []
        for key, context in list(self._pending_contexts.items()):
            if key[:2] != (str(message.guild.id), int(message.channel.id)):
                continue
            if now - context.captured_at > self._CONTEXT_TTL_SECONDS:
                self._pending_contexts.pop(key, None)
                if self._contexts.get(message.channel.id) is context:
                    self._contexts.pop(message.channel.id, None)
                continue
            candidates.append(context)
        if not candidates:
            return None
        compatible = [
            context
            for context in candidates
            if self._resolve_message_kind(context.expected_kind, raw_message) is not None
        ]
        if len(compatible) == 1:
            return compatible[0]
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _consume_context(self, channel_id: int, context: DiscordCommandContext) -> None:
        """Retire one completed workflow and promote another owner if present."""
        key = (context.server_id, int(channel_id), context.user_id)
        if self._pending_contexts.get(key) is context:
            self._pending_contexts.pop(key, None)
        if self._contexts.get(int(channel_id)) is not context:
            return
        replacement = max(
            (
                candidate
                for pending_key, candidate in self._pending_contexts.items()
                if pending_key[:2] == (context.server_id, int(channel_id))
            ),
            key=lambda candidate: candidate.captured_at,
            default=None,
        )
        if replacement is None:
            self._contexts.pop(int(channel_id), None)
        else:
            self._contexts[int(channel_id)] = replacement

    def _warn_once_for_unattributed_roll(self, message: discord.Message) -> None:
        """Avoid flooding the console when untagged roll responses arrive in a busy channel."""
        if message.guild is None:
            return
        key = (int(message.guild.id), int(message.channel.id))
        now = time.monotonic()
        last_warning = self._unattributed_roll_warning_at.get(key)
        if (
            last_warning is not None
            and now - last_warning < self._UNATTRIBUTED_ROLL_WARNING_TTL_SECONDS
        ):
            return
        self._unattributed_roll_warning_at[key] = now
        if len(self._unattributed_roll_warning_at) > 2000:
            self._unattributed_roll_warning_at = dict(
                list(self._unattributed_roll_warning_at.items())[-1000:]
            )
        self._logger.warning(
            "Could not attribute Mudae roll %s: Discord provided no user slash "
            "metadata or command context, and multiple accounts are configured "
            "for this server; roll was not imported",
            message.id,
        )

    def _context_from_active_roll(
        self,
        message: discord.Message,
        raw_message: str,
    ) -> DiscordCommandContext | None:
        """Use the selected account for an untagged slash-roll response.

        Mudae is a separate application from MOA, so Discord may omit the
        slash interaction metadata when Mudae posts its response. Restricting
        this fallback to a parsed roll and the active configured guild/account
        keeps it useful for slash-only workflows without guessing between
        configured alts.
        """
        if message.guild is None or self._router.detect(raw_message).kind != "roll":
            return None
        try:
            identity = self._config.active_identity_for_discord_server(
                str(message.guild.id),
                self._profile_name,
            )
        except ValueError:
            return None
        if identity is None:
            return None
        context = DiscordCommandContext(
            server_id=str(message.guild.id),
            user_id=identity.discord_user_id or "",
            identity=identity,
            captured_at=time.monotonic(),
            expected_kind="roll",
            evidence_source="active_account",
        )
        self._contexts[message.channel.id] = context
        self._logger.info(
            "Using active configured context for untagged Mudae roll %s in %s / %s; "
            "slash interaction metadata was unavailable",
            message.id,
            identity.server,
            identity.account,
        )
        return context

    def _context_from_interaction(
        self,
        message: discord.Message,
        raw_message: str | None = None,
    ) -> DiscordCommandContext | None:
        """Recover account context when Mudae answered a slash interaction.

        ``discord.py`` exposes the invoking user on the current
        ``interaction_metadata`` object, but the command name is not part of
        that object. When the legacy interaction name is unavailable, infer
        the response kind from the Mudae payload instead of discarding the
        sender identity.
        """
        metadata = getattr(message, "interaction_metadata", None)
        if metadata is None and not isinstance(message, discord.Message):
            # discord.py 2.7 still exposes interaction data through the legacy
            # property on test doubles and older/webhook-style message shapes.
            # Real discord.py messages expose the deprecated property, which
            # would create a warning for every non-slash Mudae response.
            with warnings.catch_warnings():
                # See the equivalent compatibility probe above.
                warnings.simplefilter("ignore")
                metadata = getattr(message, "interaction", None)
        user = getattr(metadata, "user", None)
        command_name = getattr(metadata, "name", None)
        if command_name is None:
            command_name = getattr(getattr(metadata, "command", None), "name", None)
        if user is None or message.guild is None:
            return None
        identity = self._identity_for_ids(str(message.guild.id), str(user.id))
        if identity is None:
            return None
        existing = self._pending_context_for_user(message.channel.id, str(message.guild.id), str(user.id))
        if command_name is None:
            detected_kind = self._router.detect(raw_message).kind if raw_message else "unknown"
            # Some follow-up Mudae messages carry interaction metadata without
            # the original command name. Preserve the live channel context so
            # a claim confirmation cannot downgrade it to ``unknown``. When
            # the payload is clearly a new kind, refresh the context instead;
            # otherwise an ``$im`` response can swallow the next ``/wa`` roll.
            if (
                existing is not None
                and existing.user_id == str(user.id)
                and (detected_kind == "unknown" or existing.expected_kind == detected_kind)
            ):
                return existing
            expected_kind = detected_kind if detected_kind != "unknown" else None
            if expected_kind is None:
                return None
            context = DiscordCommandContext(
                server_id=str(message.guild.id),
                user_id=str(user.id),
                identity=identity,
                captured_at=time.monotonic(),
                expected_kind=expected_kind,
            )
            self._remember_context(message.channel.id, context)
            self._logger.info(
                "Attributed Mudae response %s to %s / %s via interaction metadata; "
                "command name was unavailable",
                message.id,
                identity.server,
                identity.account,
            )
            return context
        expected_kind = self._expected_kind_for_command(command_name) if command_name else None
        if command_name and expected_kind is None:
            self._logger.info(
                "Ignoring unsupported Discord interaction /%s from account %s on server %s",
                command_name,
                identity.account,
                identity.server,
            )
            return None
        if (
            existing is not None
            and existing.user_id == str(user.id)
            and existing.expected_kind == expected_kind
        ):
            return existing
        context = DiscordCommandContext(
            server_id=str(message.guild.id),
            user_id=str(user.id),
            identity=identity,
            captured_at=time.monotonic(),
            expected_kind=expected_kind,
        )
        self._remember_context(message.channel.id, context)
        self._logger.info(
            "Tracking Discord interaction /%s for %s / %s",
            command_name or "unknown",
            identity.server,
            identity.account,
        )
        return context

    def _context_from_reaction_receipt(
        self,
        message: discord.Message,
        raw_message: str,
    ) -> DiscordCommandContext | None:
        """Resolve a Mudae button-reaction receipt without a Discord reaction event."""
        if message.guild is None:
            return None
        try:
            receipt = self._parser.parse_kakera_reaction_receipt(raw_message)
        except MudaeParseError:
            return None
        identity = self._config.identity_for_discord_server_account(
            str(message.guild.id), receipt.account_name, self._profile_name
        )
        if identity is None:
            return None
        context = DiscordCommandContext(
            server_id=str(message.guild.id),
            user_id=identity.discord_user_id or "",
            identity=identity,
            captured_at=time.monotonic(),
            expected_kind="reaction_receipt",
            evidence_source="parsed_account",
        )
        self._logger.info(
            "Resolved Kakera receipt for %s / %s from Mudae's receipt message",
            identity.server,
            identity.account,
        )
        return context

    def _context_from_parsed_account(
        self,
        message: discord.Message,
        raw_message: str,
    ) -> DiscordCommandContext | None:
        """Recover account context only from a uniquely configured parsed account."""
        detected_kind = self._router.detect(raw_message).kind
        expected_kind = {
            "claim": "claim",
            "profile": "profile",
            "reaction_receipt": "reaction_receipt",
            "reaction_blocked": "reaction_receipt",
        }.get(detected_kind)
        if expected_kind is None:
            return None
        account_name = self._parsed_account_name(detected_kind, raw_message)
        if account_name is None:
            return None
        identity = self._unique_identity_for_account(str(message.guild.id), account_name)
        if identity is None:
            return None
        return DiscordCommandContext(
            server_id=str(message.guild.id),
            user_id=identity.discord_user_id or "",
            identity=identity,
            captured_at=time.monotonic(),
            expected_kind=expected_kind,
            evidence_source="parsed_account",
        )

    def _resolve_message_kind(self, expected_kind: str | None, raw_message: str) -> str | None:
        detected_kind = self._router.detect(raw_message).kind
        if expected_kind in {"gift_kakera", "gift_spheres", "gift_character", "trade"}:
            try:
                self._parser.parse_transaction(raw_message, expected_kind)
            except MudaeParseError:
                return None
            return expected_kind
        if detected_kind == "reaction_receipt":
            return detected_kind
        if detected_kind == "reaction_blocked":
            try:
                self._parser.parse_kakera_reaction_blocked(raw_message)
            except MudaeParseError:
                return None
            return detected_kind
        if detected_kind == "claim":
            # A marriage confirmation is authoritative even when a stale
            # timer, wishlist, or roll context is still attached to the
            # channel. Validate it here before importing claim evidence.
            try:
                self._parser.parse_claim_confirmation(raw_message)
            except MudaeParseError:
                return None
            return "claim"
        if detected_kind == "divorce_prompt":
            try:
                self._parser.parse_divorce_prompt(raw_message)
            except MudaeParseError:
                return None
            return "divorce_prompt"
        if detected_kind == "divorce_declined":
            try:
                self._parser.parse_divorce_declined(raw_message)
            except MudaeParseError:
                return None
            return "divorce_declined"
        if detected_kind == "divorce_complete":
            try:
                self._parser.parse_divorce_confirmation(raw_message)
            except MudaeParseError:
                return None
            return "divorce_complete"
        if detected_kind == "roll" and expected_kind in {"wishlist", "reaction_receipt"}:
            # One-shot commands such as `$wl` can leave a channel context
            # behind. Trust a separately validated character card instead of
            # rejecting it because the previous command had another format.
            try:
                self._parser.parse_roll(raw_message)
            except MudaeParseError:
                return None
            return "roll"
        if detected_kind == "timers" and expected_kind == "reaction_receipt":
            # Recover a real standalone `$ku` snapshot after a receipt
            # context without treating the response as a roll.
            try:
                self._parser.parse_timer_state(raw_message)
            except MudaeParseError:
                return None
            return "timers"
        if expected_kind == "roll" and detected_kind == "timers":
            return "timers"
        if expected_kind == "timers" and detected_kind == "roll":
            # A slash-roll follow-up can arrive without command metadata. In
            # that case the channel may still contain the previous $tu
            # context, but the router has already identified this response as
            # a character card. Let the card parser decide instead of
            # discarding a valid roll because of stale timer context.
            try:
                self._parser.parse_roll(raw_message)
            except MudaeParseError:
                return None
            return "roll"
        if expected_kind == "top":
            try:
                self._parser.parse_top_page(raw_message)
            except MudaeParseError:
                return None
            return "top"
        if expected_kind == "topx":
            try:
                self._parser.parse_unavailable_characters(raw_message)
            except MudaeParseError:
                return None
            return "topx"
        if expected_kind == "roll":
            try:
                self._parser.parse_roll(raw_message)
            except MudaeParseError:
                try:
                    self._parser.parse_timer_state(raw_message)
                except MudaeParseError:
                    return None
                return "timers"
            return "roll"
        if expected_kind == "kakera":
            try:
                self._parser.parse_kakera_state(raw_message)
            except MudaeParseError:
                return None
            return "kakera"
        if expected_kind == "disablelist":
            try:
                self._parser.parse_disablelist(raw_message)
            except MudaeParseError:
                return None
            return "disablelist"
        if expected_kind == "timers":
            try:
                self._parser.parse_timer_state(raw_message)
            except MudaeParseError:
                return None
            return "timers"
        if expected_kind == "sphere_result":
            try:
                self._parser.parse_sphere_result(raw_message)
            except MudaeParseError:
                return None
            return "sphere_result"
        if expected_kind == "towerstate":
            try:
                self._parser.parse_tower_state(raw_message)
            except MudaeParseError:
                return None
            return "towerstate"
        if expected_kind == "lootstate":
            try:
                self._parser.parse_kakeraloot_state(raw_message)
            except MudaeParseError:
                return None
            return "lootstate"
        if expected_kind == "wishlist":
            try:
                self._parser.parse_wishlist(raw_message)
            except MudaeParseError:
                return None
            return "wishlist"
        if expected_kind == "personalrare":
            try:
                self._parser.parse_personal_rare(raw_message)
            except MudaeParseError:
                return None
            return "personalrare"
        if expected_kind == "infokl":
            try:
                self._parser.parse_kakeraloot_settings(raw_message)
            except MudaeParseError:
                return None
            return "infokl"
        if expected_kind == "profile":
            try:
                self._parser.parse_profile(raw_message)
            except MudaeParseError:
                return None
            return "profile"
        if expected_kind == "mudapins":
            try:
                self._parser.parse_mudapins(raw_message)
            except MudaeParseError:
                return None
            return "mudapins"
        if expected_kind == "im":
            try:
                self._parser.parse_character_details(raw_message)
            except MudaeParseError:
                return None
            return "im"
        if expected_kind in {"divorce", "divorce_confirmation"}:
            if detected_kind == "divorce_prompt":
                return "divorce_prompt"
            if detected_kind == "divorce_declined":
                return "divorce_declined"
            if detected_kind == "divorce_complete":
                return "divorce_complete"
            return None
        if expected_kind in {"settings", "bonus"}:
            return expected_kind if detected_kind == expected_kind else None
        if expected_kind == "reaction_receipt":
            return None
        if expected_kind == "help":
            return "help"
        if expected_kind == "tutorial":
            if detected_kind == "tutorial":
                return "tutorial"
            return None
        if expected_kind in self._SCAN_KINDS:
            try:
                self._parse_scan_page(expected_kind, raw_message)
            except MudaeParseError:
                self._logger.debug(
                    "Ignoring Mudae response classified as %s while expecting %s",
                    detected_kind,
                    expected_kind,
                )
                return None
            return expected_kind
        if detected_kind == "unknown":
            return None
        return detected_kind

    def _dedupe_payload_key(self, kind: str, raw_message: str) -> str:
        """Deduplicate embed edits without collapsing distinct scan pages."""
        try:
            if kind == "roll":
                roll = self._parser.parse_roll(raw_message)
                return "roll|" + "|".join(
                    str(value)
                    for value in (
                        roll.name,
                        roll.series,
                        roll.claim_rank,
                        roll.kakera_value,
                        roll.displayed_key_type,
                        roll.displayed_key_count,
                    )
                )
            if kind == "reaction_receipt":
                receipt = self._parser.parse_kakera_reaction_receipt(raw_message)
                return "reaction|" + "|".join(
                    str(value)
                    for value in (
                        receipt.account_name,
                        receipt.reaction_label,
                        receipt.kakera_earned,
                    )
                )
            if kind == "claim":
                claim = self._parser.parse_claim_confirmation(raw_message)
                return f"claim|{claim.account_name}|{claim.character_name}"
        except MudaeParseError:
            pass
        return raw_message

    def _parse_scan_page(self, kind: str, raw_message: str) -> object:
        if kind == "harem":
            return self._parser.parse_harem_key_page(raw_message)
        if kind == "ranked_harem":
            return self._parser.parse_ranked_harem_page(raw_message)
        if kind == "antidisable":
            return self._parser.parse_antidisable_page(raw_message)
        raise MudaeParseError(f"Unsupported listener scan kind: {kind}")

    @staticmethod
    def _expected_kind_for_command(command: str) -> str | None:
        normalized = command.casefold().lstrip("$/")
        if normalized.startswith("mmr"):
            return "ranked_harem"
        if normalized.startswith("mm"):
            return "harem"
        if normalized.startswith("adl"):
            return "antidisable"
        if normalized == "topx":
            return "topx"
        if normalized in {"top", "topo"}:
            return "top"
        if normalized in {"wl", "wishlist"}:
            return "wishlist"
        if normalized in {"persr", "personalrare"}:
            return "personalrare"
        if normalized in {"infokl", "kakeralootinfo"}:
            return "infokl"
        if normalized in {"profile", "pr"}:
            return "profile"
        if normalized in {"mp", "mudapins", "mudapin"}:
            return "mudapins"
        if normalized in {"k", "kakera"}:
            return "kakera"
        if normalized in {"settings", "set"}:
            return "settings"
        if normalized in {"help", "tuarrange", "ta", "infopin"}:
            return "help"
        if normalized in {"tuto", "tutorial"}:
            return "tutorial"
        if normalized in {
            "tu",
            "timersup",
            "mu",
            "ru",
            "du",
            "ku",
            "dk",
            "dku",
            "bku",
            "rtu",
            "ohu",
            "rolls",
            "daily",
        }:
            return "timers"
        if normalized in {"bonus", "bonuses"}:
            return "bonus"
        if normalized in {"oq", "ouroquest"}:
            return "sphere_result"
        if normalized in {"kt", "tower"}:
            return "towerstate"
        if normalized in {"lk", "kakeraloots"}:
            return "lootstate"
        if normalized == "kl":
            return "lootstate"
        if normalized in {"im", "info"}:
            return "im"
        if normalized in {"divorce", "div"}:
            return "divorce"
        if normalized in {"givek", "givekakera"}:
            return "gift_kakera"
        if normalized in {"givesp", "givespheres"}:
            return "gift_spheres"
        if normalized == "give":
            return "gift_character"
        if normalized == "trade":
            return "trade"
        if normalized.startswith("dl"):
            return "disablelist"
        if normalized in DiscordListenerService._ROLL_COMMANDS:
            return "roll"
        return None

    @staticmethod
    def _ourochest_command_kind(command: str) -> str | None:
        """Recognize only the exact prefix forms for the dedicated Ourochest route."""
        if not command.startswith("$"):
            return None
        if command[1:].casefold() in {"oc", "ourochest"}:
            return "ourochest"
        return None

    def _track_divorce_confirmation(
        self,
        message: discord.Message,
        identity: ConfigAccount,
        content: str,
    ) -> bool:
        """Keep the channel linked while the user answers Mudae's divorce prompt."""
        answer = content.casefold().strip()
        if answer not in {"y", "yes", "n", "no"}:
            return False
        existing = self._pending_context_for_user(
            message.channel.id,
            str(message.guild.id),
            str(message.author.id),
        )
        if (
            existing is None
            or existing.expected_kind not in {"divorce", "divorce_confirmation"}
            or existing.user_id != str(message.author.id)
            or time.monotonic() - existing.captured_at > self._CONTEXT_TTL_SECONDS
        ):
            return False
        if answer in {"n", "no"}:
            self._remember_context(message.channel.id, replace(
                existing,
                captured_at=time.monotonic(),
                expected_kind="divorce_confirmation",
            ))
            self._logger.info(
                "Tracking declined divorce confirmation for %s in %s",
                identity.account,
                identity.server,
            )
            return True
        self._remember_context(message.channel.id, replace(
            existing,
            captured_at=time.monotonic(),
            expected_kind="divorce_confirmation",
        ))
        self._logger.info(
            "Tracking divorce confirmation %s for %s / %s",
            answer,
            identity.server,
            identity.account,
        )
        return True

    def _track_transaction_input(
        self,
        message: discord.Message,
        identity: ConfigAccount,
        content: str,
    ) -> bool:
        """Keep a gift/trade flow linked across plain-text follow-up messages."""
        existing = self._pending_context_for_user(
            message.channel.id,
            str(message.guild.id),
            str(message.author.id),
        )
        transaction_kinds = {"gift_kakera", "gift_spheres", "gift_character", "trade"}
        if (
            existing is None
            or existing.expected_kind not in transaction_kinds
            or time.monotonic() - existing.captured_at > self._CONTEXT_TTL_SECONDS
        ):
            return False
        if existing.expected_kind in {"gift_kakera", "gift_spheres"} and existing.user_id != str(message.author.id):
            return False
        if not content.strip():
            return False
        self._remember_context(
            message.channel.id,
            replace(existing, captured_at=time.monotonic()),
        )
        self._logger.info(
            "Tracking %s follow-up from account %s on server %s",
            existing.expected_kind,
            identity.account,
            identity.server,
        )
        return True

    @staticmethod
    def _transaction_is_terminal(kind: str, raw_message: str) -> bool:
        normalized = raw_message.casefold()
        if "syntax:" in normalized:
            return True
        if kind in {"gift_kakera", "gift_spheres"}:
            return "just gifted" in normalized
        if kind == "gift_character":
            return " given to @" in normalized
        return "the exchange is over" in normalized

    def _identity_for_ids(self, server_id: str, user_id: str) -> ConfigAccount | None:
        return self._config.identity_for_discord_ids(server_id, user_id, self._profile_name)

    def _scan_id_for_page(
        self,
        kind: str,
        raw_message: str,
        identity: ConfigAccount,
    ) -> int | None:
        scan_kind = self._SCAN_KINDS.get(kind)
        if scan_kind is None:
            return None
        page_number, page_count = self._page_metadata(kind, raw_message)
        if page_number != 1 or page_count is None:
            return self._scan_ids.get((identity.server.casefold(), identity.account.casefold(), scan_kind))
        key = (identity.server.casefold(), identity.account.casefold(), scan_kind)
        if key in self._scan_ids:
            return self._scan_ids[key]
        if scan_kind == "antidisable":
            scan = self._catalog.begin_antidisable_scan(identity.server, identity.account)
        else:
            scan = self._catalog.begin_harem_scan(identity.server, identity.account, scan_kind)
        self._scan_ids[key] = scan.id
        self._logger.info(
            "Started automatic %s scan %s for %s / %s",
            scan_kind,
            scan.id,
            identity.server,
            identity.account,
        )
        return scan.id

    def _complete_scan_if_last_page(
        self,
        kind: str,
        raw_message: str,
        identity: ConfigAccount,
        scan_id: int | None,
    ) -> None:
        if scan_id is None:
            return
        scan_kind = self._SCAN_KINDS[kind]
        progress = self._catalog.harem_scan_progress(scan_id)
        if progress is None or progress.completed_at is not None or not progress.is_complete:
            # Pagination edits can arrive out of order. A page numbered N is
            # not necessarily the final missing page, so defer completion
            # until the persisted scan actually contains every page.
            return
        try:
            if scan_kind == "antidisable":
                scan = self._catalog.complete_antidisable_scan(scan_id)
            else:
                scan = self._catalog.complete_harem_scan(scan_id)
        except ValueError as error:
            self._logger.warning("Automatic scan %s could not be completed: %s", scan_id, error)
            return
        self._logger.info(
            "Completed automatic %s scan %s for %s / %s",
            scan_kind,
            scan.id,
            identity.server,
            identity.account,
        )
        self._scan_ids.pop((identity.server.casefold(), identity.account.casefold(), scan_kind), None)
        for context_key, scan_context in list(self._scan_contexts.items()):
            if (
                context_key[2] == kind
                and scan_context.identity.server.casefold() == identity.server.casefold()
                and scan_context.identity.account.casefold() == identity.account.casefold()
            ):
                self._scan_contexts.pop(context_key, None)

    def _page_metadata(self, kind: str, raw_message: str) -> tuple[int | None, int | None]:
        try:
            page = (
                self._parser.parse_harem_key_page(raw_message)
                if kind == "harem"
                else self._parser.parse_ranked_harem_page(raw_message)
                if kind == "ranked_harem"
                else self._parser.parse_antidisable_page(raw_message)
                if kind == "antidisable"
                else None
            )
        except ValueError:
            return None, None
        if page is None:
            return None, None
        return page.page_number, page.page_count

    @staticmethod
    def extract_message_text(message: Any) -> str:
        """Flatten Discord content and embed text into the parser's copied-text shape."""
        parts: list[str] = []
        content = getattr(message, "content", "")
        if content and content.strip():
            parts.append(DiscordListenerService._normalize_discord_text(content))
        for embed in getattr(message, "embeds", ()) or ():
            author = getattr(embed, "author", None)
            author_name = getattr(author, "name", None)
            if author_name and author_name.strip():
                parts.append(DiscordListenerService._normalize_discord_text(author_name))
            for value in (
                getattr(embed, "title", None),
                getattr(embed, "description", None),
            ):
                if value and value.strip():
                    parts.append(DiscordListenerService._normalize_discord_text(value))
            for field in getattr(embed, "fields", ()) or ():
                name = getattr(field, "name", "")
                value = getattr(field, "value", "")
                if name and name.strip():
                    parts.append(DiscordListenerService._normalize_discord_text(name))
                if value and value.strip():
                    parts.append(DiscordListenerService._normalize_discord_text(value))
            footer = getattr(embed, "footer", None)
            footer_text = getattr(footer, "text", None)
            if footer_text and footer_text.strip():
                parts.append(DiscordListenerService._normalize_discord_text(footer_text))
        return "\n".join(parts)

    @staticmethod
    def _normalize_discord_text(value: str) -> str:
        """Convert Discord API custom-emoji markup to Mudae's copied-text markers."""
        normalized = re.sub(r"<a?:(?P<name>[A-Za-z0-9_]+):\d+>", r":\g<name>:", value)
        return normalized.strip()

    def _client_channel(self, channel_id: int) -> Any:
        client = getattr(self, "_client", None)
        if client is None:
            return None
        return client.get_channel(channel_id)


class _MOADiscordClient(discord.Client):
    """Thin discord.py adapter kept separate from MOA's import logic."""

    def __init__(self, listener: DiscordListenerService, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._listener = listener
        listener._client = self

    async def on_ready(self) -> None:
        self._listener._logger.info("Discord listener connected as %s", self.user)
        self._listener._logger.info(
            "Mudae bot ID filter: %s",
            self._listener._mudae_user_id if self._listener._mudae_user_id is not None else "not set",
        )
        self._listener._logger.info(
            "Waiting for configured-user and observed-user Mudae commands, rolls, message edits, and reactions."
        )
        await self.change_presence(
            status=discord.Status.online,
            activity=self._listener.presence_activity(),
        )

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            await self._listener.handle_bot_response(message)
        else:
            await self._listener.handle_message(message)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        await self._listener.handle_interaction(interaction)

    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        await self._listener.handle_message_edit(before, after)

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        await self._listener.handle_raw_message_edit(payload)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._listener.handle_raw_reaction_add(payload)


class _MOADiagnosticDiscordClient(discord.Client):
    """Gateway-only adapter for opt-in diagnostic capture runs."""

    def __init__(self, capture: DiscordEventCaptureService, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._capture = capture

    async def on_socket_raw_receive(self, payload: object) -> None:
        decoded_payload: Mapping[str, Any] | None
        if isinstance(payload, Mapping):
            decoded_payload = payload
        else:
            if isinstance(payload, str):
                raw_payload = payload
            elif isinstance(payload, (bytes, bytearray, memoryview)):
                try:
                    raw_payload = bytes(payload).decode("utf-8")
                except UnicodeDecodeError:
                    return
            else:
                return
            try:
                decoded = json.loads(raw_payload)
            except json.JSONDecodeError:
                return
            if not isinstance(decoded, Mapping):
                return
            decoded_payload = decoded
        try:
            self._capture.capture_gateway_payload(decoded_payload)
        except DiscordEventCaptureError:
            raise
