"""Capture Discord messages and route recognized Mudae responses into MOA."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import discord

from moa.core.config import ConfigAccount, ConfigService
from moa.parser.mudae import MudaeTextParser
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


class DiscordListenerService:
    """Listen for Mudae messages and reuse the existing automatic import pipeline."""

    _CONTEXT_TTL_SECONDS = 300.0
    _SCAN_KINDS = {
        "harem": "keys",
        "ranked_harem": "owned",
        "antidisable": "antidisable",
    }

    def __init__(
        self,
        config_service: ConfigService | None = None,
        catalog_service: CatalogService | None = None,
        importer: AutomaticImportService | None = None,
        profile_name: str | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config_service or ConfigService()
        self._catalog = catalog_service or CatalogService()
        self._importer = importer or AutomaticImportService(self._catalog)
        self._parser = MudaeTextParser()
        self._router = MudaeMessageRouter(self._parser)
        self._profile_name = profile_name
        self._logger = logger or logging.getLogger("moa.discord")
        self._contexts: dict[int, DiscordCommandContext] = {}
        self._scan_ids: dict[tuple[str, str, str], int] = {}
        self._seen_payloads: set[tuple[int, str]] = set()
        self._mudae_user_id: int | None = None

    def run(self, token: str, mudae_user_id: int | None = None) -> None:
        """Run the blocking Discord gateway client until interrupted."""
        if not token.strip():
            raise ValueError("A Discord bot token is required.")
        self._mudae_user_id = mudae_user_id
        intents = discord.Intents.none()
        intents.guilds = True
        intents.messages = True
        intents.message_content = True
        client = _MOADiscordClient(self, intents=intents)
        client.run(token.strip())

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
        )
        self._logger.info(
            "Tracking Discord command %s for %s / %s",
            content.split(maxsplit=1)[0],
            identity.server,
            identity.account,
        )

    async def handle_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        """Fetch an edited response so Mudae pagination updates are imported."""
        channel = self._client_channel(payload.channel_id)
        if channel is None:
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.HTTPException, discord.NotFound, discord.Forbidden) as error:
            self._logger.warning("Could not fetch edited Discord message %s: %s", payload.message_id, error)
            return
        await self.handle_bot_response(message)

    async def handle_bot_response(self, message: discord.Message) -> None:
        """Import one bot-authored Mudae response when a configured context exists."""
        if message.guild is None or not message.author.bot:
            return
        if self._mudae_user_id is not None and message.author.id != self._mudae_user_id:
            return
        context = self._contexts.get(message.channel.id)
        if context is None or time.monotonic() - context.captured_at > self._CONTEXT_TTL_SECONDS:
            return
        raw_message = self.extract_message_text(message)
        if not raw_message:
            return
        detection = self._router.detect(raw_message)
        if detection.kind == "unknown":
            return
        payload_key = (message.id, raw_message)
        if payload_key in self._seen_payloads:
            return
        self._seen_payloads.add(payload_key)
        if len(self._seen_payloads) > 2000:
            self._seen_payloads = set(list(self._seen_payloads)[-1000:])

        scan_id = self._scan_id_for_page(
            detection.kind,
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
            detection.kind,
            raw_message,
            context.identity,
            scan_id,
        )

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

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            await self._listener.handle_bot_response(message)
        else:
            await self._listener.handle_message(message)

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        await self._listener.handle_raw_message_edit(payload)
