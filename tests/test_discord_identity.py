from dataclasses import fields

import pytest

from moa.models.discord_identity import (
    MessageAggregateKey,
    MessageRevisionKey,
    ProjectionIdentity,
    ReactionSubjectKey,
    SourcePlatform,
)


def aggregate(
    *, guild_id: str = "guild-1", channel_id: str = "channel-1", message_id: str = "message-1"
) -> MessageAggregateKey:
    return MessageAggregateKey(SourcePlatform.DISCORD, guild_id, channel_id, message_id)


def test_message_aggregate_identity_is_complete_and_hashable() -> None:
    first = aggregate()
    same = aggregate()

    assert first == same
    assert hash(first) == hash(same)
    assert aggregate(channel_id="channel-2") != first
    assert aggregate(guild_id="guild-2") != first
    assert aggregate(message_id="message-2") != first


def test_message_aggregate_is_immutable() -> None:
    key = aggregate()

    with pytest.raises(AttributeError):
        key.message_id = "message-2"


def test_versioned_message_revisions_include_source_ordering_marker() -> None:
    first = MessageRevisionKey.versioned(aggregate(), "hash-1", "revision-1")
    same = MessageRevisionKey.versioned(aggregate(), "hash-1", "revision-1")

    assert first == same
    assert hash(first) == hash(same)
    assert first.ordering_known is True
    assert MessageRevisionKey.versioned(aggregate(), "hash-1", "revision-2") != first
    assert MessageRevisionKey.versioned(aggregate(), "hash-2", "revision-1") != first


def test_unversioned_message_revisions_collapse_and_report_unknown_ordering() -> None:
    first = MessageRevisionKey.unversioned(aggregate(), "hash-1")
    same = MessageRevisionKey.unversioned(aggregate(), "hash-1")

    assert first == same
    assert hash(first) == hash(same)
    assert first.source_revision_marker is None
    assert first.ordering_known is False
    assert MessageRevisionKey.versioned(aggregate(), "hash-1", "revision-1") != first


def test_message_revision_validation_does_not_accept_blank_values() -> None:
    with pytest.raises(ValueError):
        MessageRevisionKey.unversioned(aggregate(), " ")
    with pytest.raises(ValueError):
        MessageRevisionKey.versioned(aggregate(), "hash-1", " ")


def test_reaction_subject_identity_has_no_action_or_occurrence() -> None:
    first = ReactionSubjectKey(
        SourcePlatform.DISCORD, "guild-1", "channel-1", "message-1", "user-1", "custom:emoji-1"
    )
    same = ReactionSubjectKey(
        SourcePlatform.DISCORD, "guild-1", "channel-1", "message-1", "user-1", "custom:emoji-1"
    )

    assert first == same
    assert hash(first) == hash(same)
    assert first != ReactionSubjectKey(
        SourcePlatform.DISCORD, "guild-1", "channel-1", "message-1", "user-2", "custom:emoji-1"
    )
    assert first != ReactionSubjectKey(
        SourcePlatform.DISCORD, "guild-1", "channel-1", "message-2", "user-1", "custom:emoji-1"
    )
    assert first != ReactionSubjectKey(
        SourcePlatform.DISCORD, "guild-1", "channel-1", "message-1", "user-1", "unicode:👍"
    )
    field_names = {field.name for field in fields(first)}
    assert "action" not in field_names
    assert "occurrence_id" not in field_names
    assert "transition_generation" not in field_names


def test_reaction_subject_accepts_one_canonical_identity_for_custom_emoji_presentation() -> None:
    static = ReactionSubjectKey(
        SourcePlatform.DISCORD, "guild-1", "channel-1", "message-1", "user-1", "custom:emoji-1"
    )
    animated = ReactionSubjectKey(
        SourcePlatform.DISCORD, "guild-1", "channel-1", "message-1", "user-1", "custom:emoji-1"
    )

    assert static == animated


def test_projection_identity_is_stable_for_semantic_slots() -> None:
    first = ProjectionIdentity("event-1", "roll", "character:asuna")
    retry = ProjectionIdentity("event-1", "roll", "character:asuna")

    assert first == retry
    assert hash(first) == hash(retry)
    assert ProjectionIdentity("event-1", "roll", "character:megumi") != first
    assert ProjectionIdentity("event-1", "reaction", "character:asuna") != first


def test_projection_identity_does_not_include_parser_version_or_list_position() -> None:
    first = ProjectionIdentity("event-1", "roll", "character:asuna")
    retry_after_parser_update = ProjectionIdentity("event-1", "roll", "character:asuna")

    assert first == retry_after_parser_update


@pytest.mark.parametrize("field", ["source_event_id", "projection_kind", "projection_slot"])
def test_projection_identity_rejects_blank_fields(field: str) -> None:
    values = {"source_event_id": "event-1", "projection_kind": "roll", "projection_slot": "slot-1"}
    values[field] = "  "

    with pytest.raises(ValueError):
        ProjectionIdentity(**values)


def test_identity_values_reject_blank_discord_fields() -> None:
    with pytest.raises(ValueError):
        aggregate(guild_id=" ")
    with pytest.raises(ValueError):
        ReactionSubjectKey(
            SourcePlatform.DISCORD, "guild-1", "channel-1", "message-1", "user-1", " "
        )
