"""Pure mapping from Discord message observations to durable identities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import unicodedata

from moa.models.discord_identity import (
    MessageAggregateKey,
    MessageRevisionKey,
    SourcePlatform,
)


MESSAGE_REVISION_EVENT_KIND = "message_revision"
_TEXT_PAYLOAD_HASH_PREFIX = "v1:text:sha256:"
_MESSAGE_REVISION_EVENT_KEY_PREFIX = "discord-message-revision:v1:"


@dataclass(frozen=True, slots=True)
class DiscordMessageReceiveEnvelope:
    """The pure inputs needed by ``DiscordMessageRepository.receive_message``."""

    aggregate_key: MessageAggregateKey
    revision_key: MessageRevisionKey
    event_key: str
    event_kind: str
    raw_text: str
    payload_json: str | None
    payload_capture_version: str | None
    source_observed_at: datetime | None
    received_at: datetime


def build_text_payload_hash(raw_text: str) -> str:
    """Hash provisional flattened message text after canonical text normalization."""

    if not isinstance(raw_text, str):
        raise TypeError("raw_text must be a string")
    normalized_text = unicodedata.normalize("NFC", raw_text)
    normalized_text = normalized_text.replace("\r\n", "\n").replace("\r", "\n")
    digest = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
    return f"{_TEXT_PAYLOAD_HASH_PREFIX}{digest}"


def build_source_revision_marker(source_revision_at: datetime | None) -> str | None:
    """Return an ISO 8601 UTC marker for an aware source timestamp."""

    if source_revision_at is None:
        return None
    return _normalize_aware_datetime(source_revision_at).isoformat(timespec="microseconds")


def build_message_revision_event_key(revision_key: MessageRevisionKey) -> str:
    """Build a stable, content-independent event key for one message revision."""

    if not isinstance(revision_key, MessageRevisionKey):
        raise TypeError("revision_key must be a MessageRevisionKey")
    aggregate = revision_key.aggregate
    canonical_fields = [
        aggregate.platform.value,
        aggregate.guild_id,
        aggregate.channel_id,
        aggregate.message_id,
        "versioned" if revision_key.ordering_known else "unversioned",
        revision_key.source_revision_marker,
        revision_key.normalized_payload_hash,
    ]
    canonical_json = json.dumps(canonical_fields, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    return f"{_MESSAGE_REVISION_EVENT_KEY_PREFIX}{digest}"


def build_message_receive_envelope(
    *,
    guild_id: str,
    channel_id: str,
    message_id: str,
    raw_text: str,
    source_revision_at: datetime | None,
    received_at: datetime,
    payload_json: str | None = None,
    payload_capture_version: str | None = None,
) -> DiscordMessageReceiveEnvelope:
    """Map one observed Discord message into repository receive arguments."""

    normalized_received_at = _normalize_aware_datetime(received_at)
    normalized_source_revision_at = (
        _normalize_aware_datetime(source_revision_at)
        if source_revision_at is not None
        else None
    )
    aggregate_key = MessageAggregateKey(
        platform=SourcePlatform.DISCORD,
        guild_id=guild_id,
        channel_id=channel_id,
        message_id=message_id,
    )
    normalized_payload_hash = build_text_payload_hash(raw_text)
    source_revision_marker = build_source_revision_marker(normalized_source_revision_at)
    if source_revision_marker is None:
        revision_key = MessageRevisionKey.unversioned(aggregate_key, normalized_payload_hash)
    else:
        revision_key = MessageRevisionKey.versioned(
            aggregate_key,
            normalized_payload_hash,
            source_revision_marker,
        )
    event_key = build_message_revision_event_key(revision_key)
    return DiscordMessageReceiveEnvelope(
        aggregate_key=aggregate_key,
        revision_key=revision_key,
        event_key=event_key,
        event_kind=MESSAGE_REVISION_EVENT_KIND,
        raw_text=raw_text,
        payload_json=payload_json,
        payload_capture_version=payload_capture_version,
        source_observed_at=normalized_source_revision_at,
        received_at=normalized_received_at,
    )


def _normalize_aware_datetime(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("datetime value must be a datetime")
    if value.utcoffset() is None:
        raise ValueError("datetime value must be timezone-aware")
    return value.astimezone(timezone.utc)
