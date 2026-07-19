"""Canonical, storage-independent identities for Discord observations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SourcePlatform(str, Enum):
    """The source platform represented by these identity objects."""

    DISCORD = "discord"


def _require_non_blank(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


def _require_discord(platform: SourcePlatform) -> SourcePlatform:
    if not isinstance(platform, SourcePlatform):
        try:
            platform = SourcePlatform(platform)
        except (TypeError, ValueError) as error:
            raise ValueError("platform must be SourcePlatform.DISCORD") from error
    if platform is not SourcePlatform.DISCORD:
        raise ValueError("platform must be SourcePlatform.DISCORD")
    return platform


@dataclass(frozen=True, slots=True)
class MessageAggregateKey:
    """Stable identity for one Discord message, excluding message content."""

    platform: SourcePlatform
    guild_id: str
    channel_id: str
    message_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "platform", _require_discord(self.platform))
        for field_name in ("guild_id", "channel_id", "message_id"):
            _require_non_blank(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True)
class MessageRevisionKey:
    """Identity for one observed payload revision of a Discord message."""

    aggregate: MessageAggregateKey
    normalized_payload_hash: str
    source_revision_marker: str | None

    @classmethod
    def versioned(
        cls,
        aggregate: MessageAggregateKey,
        normalized_payload_hash: str,
        source_revision_marker: str,
    ) -> MessageRevisionKey:
        """Build a revision whose source ordering marker is known."""

        _require_non_blank(normalized_payload_hash, "normalized_payload_hash")
        _require_non_blank(source_revision_marker, "source_revision_marker")
        return cls(aggregate, normalized_payload_hash, source_revision_marker)

    @classmethod
    def unversioned(
        cls,
        aggregate: MessageAggregateKey,
        normalized_payload_hash: str,
    ) -> MessageRevisionKey:
        """Build a revision whose source ordering is explicitly unknown."""

        _require_non_blank(normalized_payload_hash, "normalized_payload_hash")
        return cls(aggregate, normalized_payload_hash, None)

    def __post_init__(self) -> None:
        if not isinstance(self.aggregate, MessageAggregateKey):
            raise TypeError("aggregate must be a MessageAggregateKey")
        _require_non_blank(self.normalized_payload_hash, "normalized_payload_hash")
        if self.source_revision_marker is not None:
            _require_non_blank(self.source_revision_marker, "source_revision_marker")

    @property
    def ordering_known(self) -> bool:
        """Whether the source supplied an ordering marker for this revision."""

        return self.source_revision_marker is not None


@dataclass(frozen=True, slots=True)
class ReactionSubjectKey:
    """Stable subject identity for a Discord reaction state."""

    platform: SourcePlatform
    guild_id: str
    channel_id: str
    target_message_id: str
    reactor_id: str
    canonical_emoji_identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "platform", _require_discord(self.platform))
        for field_name in (
            "guild_id",
            "channel_id",
            "target_message_id",
            "reactor_id",
            "canonical_emoji_identity",
        ):
            _require_non_blank(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True)
class ProjectionIdentity:
    """Stable identity for one semantic projection of a source event."""

    source_event_id: str
    projection_kind: str
    projection_slot: str

    def __post_init__(self) -> None:
        for field_name in ("source_event_id", "projection_kind", "projection_slot"):
            _require_non_blank(getattr(self, field_name), field_name)
