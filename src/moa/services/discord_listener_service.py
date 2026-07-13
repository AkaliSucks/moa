"""Capture Discord messages and route recognized Mudae responses into MOA."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import discord

from moa.core.config import ConfigAccount, ConfigService
from moa.parser.mudae import MudaeParseError, MudaeTextParser
from moa.parser.message_router import MudaeMessageRouter
from moa.services.automatic_import_service import AutomaticImportService
from moa.services.catalog_service import CatalogService


@dataclass(frozen=True)
class DiscordCommandContext:
    """Configured MOA identity associated with the latest command in a channel."""

    server_id: str
    user_id: str
    identity: ConfigAccount
    captured_at: float
    expected_kind: str | None = None


class DiscordListenerService:
    """Listen for Mudae messages and reuse the existing automatic import pipeline."""

    _CONTEXT_TTL_SECONDS = 300.0
    _EDIT_FETCH_DEBOUNCE_SECONDS = 1.0
    _DEFAULT_STATUS_TEXT = "Mudae progress"
    _MAX_STATUS_LENGTH = 128
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

    def __init__(
        self,
        config_service: ConfigService | None = None,
        catalog_service: CatalogService | None = None,
        importer: AutomaticImportService | None = None,
        profile_name: str | None = None,
        status_text: str = _DEFAULT_STATUS_TEXT,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config_service or ConfigService()
        self._catalog = catalog_service or CatalogService()
        self._importer = importer or AutomaticImportService(self._catalog)
        self._parser = MudaeTextParser()
        self._router = MudaeMessageRouter(self._parser)
        self._profile_name = profile_name
        self._status_text = self._normalize_status_text(status_text)
        self._logger = logger or logging.getLogger("moa.discord")
        self._contexts: dict[int, DiscordCommandContext] = {}
        self._scan_ids: dict[tuple[str, str, str], int] = {}
        self._seen_payloads: set[tuple[int, str]] = set()
        self._message_cache: dict[int, discord.Message] = {}
        self._last_edit_fetch: dict[int, float] = {}
        self._mudae_user_id: int | None = None

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
        content = (message.content or "").lstrip()
        if not content.startswith(("$", "/")):
            return
        identity = self._identity_for_ids(str(message.guild.id), str(message.author.id))
        if identity is None:
            return
        self._contexts[message.channel.id] = DiscordCommandContext(
            server_id=str(message.guild.id),
            user_id=str(message.author.id),
            identity=identity,
            captured_at=time.monotonic(),
            expected_kind=self._expected_kind_for_command(content.split(maxsplit=1)[0]),
        )
        self._logger.info(
            "Tracking Discord command %s for %s / %s",
            content.split(maxsplit=1)[0],
            identity.server,
            identity.account,
        )

    async def handle_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        """Process an edited response, using cache before falling back to REST."""
        cached_message = self._message_cache.get(payload.message_id)
        if cached_message is not None:
            await self.handle_bot_response(cached_message)
            return
        channel = self._client_channel(payload.channel_id)
        if channel is None:
            return
        now = time.monotonic()
        last_fetch = self._last_edit_fetch.get(payload.message_id)
        if last_fetch is not None and now - last_fetch < self._EDIT_FETCH_DEBOUNCE_SECONDS:
            self._logger.debug(
                "Skipping duplicate edited-message fetch for Discord message %s",
                payload.message_id,
            )
            return
        self._last_edit_fetch[payload.message_id] = now
        if len(self._last_edit_fetch) > 2000:
            self._last_edit_fetch = dict(list(self._last_edit_fetch.items())[-1000:])
        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.HTTPException, discord.NotFound, discord.Forbidden) as error:
            self._logger.warning("Could not fetch edited Discord message %s: %s", payload.message_id, error)
            return
        self._message_cache[message.id] = message
        await self.handle_bot_response(message)

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
        identity = self._identity_for_ids(str(payload.guild_id), str(payload.user_id))
        if identity is None:
            return
        self._contexts[payload.channel_id] = DiscordCommandContext(
            server_id=str(payload.guild_id),
            user_id=str(payload.user_id),
            identity=identity,
            captured_at=time.monotonic(),
            expected_kind="reaction_receipt",
        )
        self._logger.info(
            "Tracking reaction %s on Discord message %s for %s / %s",
            payload.emoji,
            payload.message_id,
            identity.server,
            identity.account,
        )

    async def handle_bot_response(self, message: discord.Message) -> None:
        """Import one bot-authored Mudae response when a configured context exists."""
        if message.guild is None or not message.author.bot:
            return
        self._message_cache[message.id] = message
        if len(self._message_cache) > 2000:
            self._message_cache = dict(list(self._message_cache.items())[-1000:])
        if self._mudae_user_id is not None and message.author.id != self._mudae_user_id:
            return
        context = self._contexts.get(message.channel.id)
        if context is None or time.monotonic() - context.captured_at > self._CONTEXT_TTL_SECONDS:
            return
        raw_message = self.extract_message_text(message)
        if not raw_message:
            return
        kind = self._resolve_message_kind(context.expected_kind, raw_message)
        if kind is None:
            return
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
        payload_key = (message.id, raw_message)
        if payload_key in self._seen_payloads:
            return
        self._seen_payloads.add(payload_key)
        if len(self._seen_payloads) > 2000:
            self._seen_payloads = set(list(self._seen_payloads)[-1000:])

        self._logger.info(
            "Detected Mudae %s message %s for %s / %s",
            kind,
            message.id,
            context.identity.server,
            context.identity.account,
        )

        scan_id = self._scan_id_for_page(
            kind,
            raw_message,
            context.identity,
        )
        source = (
            f"discord:guild={message.guild.id}:channel={message.channel.id}:message={message.id}"
        )
        try:
            result = self._importer.import_message(
                raw_message,
                source,
                context.identity.server,
                context.identity.account,
                harem_scan_id=scan_id,
                detected_kind=kind,
            )
        except Exception as error:  # Keep one malformed Discord payload from stopping the listener.
            self._logger.warning("Could not import Mudae message %s: %s", message.id, error)
            return
        self._logger.info(
            "Imported Discord Mudae message %s: %s",
            message.id,
            result.message,
        )
        self._complete_scan_if_last_page(
            kind,
            raw_message,
            context.identity,
            scan_id,
        )

    def _resolve_message_kind(self, expected_kind: str | None, raw_message: str) -> str | None:
        detected_kind = self._router.detect(raw_message).kind
        if detected_kind == "unknown":
            return None
        if expected_kind is None or expected_kind == detected_kind:
            return detected_kind
        if expected_kind == "roll":
            try:
                self._parser.parse_roll(raw_message)
            except MudaeParseError:
                return None
            return "roll"
        if expected_kind == "reaction_receipt":
            return None
        if expected_kind not in self._SCAN_KINDS:
            return detected_kind
        if detected_kind == "roll":
            return detected_kind
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
        if normalized.startswith("mmy") or normalized == "mm":
            return "harem"
        if normalized.startswith("adl"):
            return "antidisable"
        if normalized in DiscordListenerService._ROLL_COMMANDS:
            return "roll"
        return None

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
        page_number, page_count = self._page_metadata(kind, raw_message)
        if page_number is None or page_count is None or page_number != page_count:
            return
        scan_kind = self._SCAN_KINDS[kind]
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
            parts.append(content.strip())
        for embed in getattr(message, "embeds", ()) or ():
            author = getattr(embed, "author", None)
            author_name = getattr(author, "name", None)
            if author_name and author_name.strip():
                parts.append(author_name.strip())
            for value in (
                getattr(embed, "title", None),
                getattr(embed, "description", None),
            ):
                if value and value.strip():
                    parts.append(value.strip())
            for field in getattr(embed, "fields", ()) or ():
                name = getattr(field, "name", "")
                value = getattr(field, "value", "")
                if name and name.strip():
                    parts.append(name.strip())
                if value and value.strip():
                    parts.append(value.strip())
            footer = getattr(embed, "footer", None)
            footer_text = getattr(footer, "text", None)
            if footer_text and footer_text.strip():
                parts.append(footer_text.strip())
        return "\n".join(parts)

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
            "Waiting for configured-user Mudae commands, rolls, message edits, and reactions."
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

    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        await self._listener.handle_message_edit(before, after)

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        await self._listener.handle_raw_message_edit(payload)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._listener.handle_raw_reaction_add(payload)
