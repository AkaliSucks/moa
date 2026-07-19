from datetime import datetime, timedelta, timezone

import pytest

from moa.models.discord_identity import SourcePlatform
from moa.models.discord_message_mapping import (
    MESSAGE_REVISION_EVENT_KIND,
    build_message_receive_envelope,
    build_message_revision_event_key,
    build_source_revision_marker,
    build_text_payload_hash,
)


RECEIVED_AT = datetime(2026, 7, 19, 12, 0, 0, 123456, tzinfo=timezone.utc)
SOURCE_REVISION_AT = datetime(2026, 7, 19, 11, 59, 58, 654321, tzinfo=timezone.utc)


def envelope(**overrides):
    values = {
        "guild_id": "guild-1",
        "channel_id": "channel-1",
        "message_id": "message-1",
        "raw_text": "Hello, **Discord**!\n",
        "source_revision_at": SOURCE_REVISION_AT,
        "received_at": RECEIVED_AT,
    }
    values.update(overrides)
    return build_message_receive_envelope(**values)


def test_same_inputs_produce_equal_mapping_outputs() -> None:
    first = envelope(payload_json='{"content":"Hello"}', payload_capture_version="v1")
    second = envelope(payload_json='{"content":"Hello"}', payload_capture_version="v1")

    assert first == second
    assert first.revision_key == second.revision_key
    assert first.event_key == second.event_key
    assert build_text_payload_hash(first.raw_text) == build_text_payload_hash(second.raw_text)


def test_nfc_and_nfd_text_have_the_same_hash() -> None:
    assert build_text_payload_hash("caf\N{LATIN SMALL LETTER E WITH ACUTE}") == build_text_payload_hash(
        "cafe\N{COMBINING ACUTE ACCENT}"
    )


def test_crlf_cr_and_lf_text_have_the_same_hash() -> None:
    assert build_text_payload_hash("one\r\ntwo\rthree\nfour") == build_text_payload_hash(
        "one\ntwo\nthree\nfour"
    )


@pytest.mark.parametrize(
    "changed_text",
    ["Hello", "hello!", " hello", "hello ", "hel  lo", "hello\nworld"],
)
def test_case_punctuation_and_whitespace_changes_remain_distinct(changed_text: str) -> None:
    assert build_text_payload_hash(changed_text) != build_text_payload_hash("hello")


def test_message_scope_changes_aggregate_and_event_identity() -> None:
    first = envelope()
    different_message = envelope(message_id="message-2")
    different_scope = envelope(guild_id="guild-2", channel_id="channel-2")

    assert first.aggregate_key != different_message.aggregate_key
    assert first.event_key != different_message.event_key
    assert first.aggregate_key != different_scope.aggregate_key
    assert first.event_key != different_scope.event_key
    assert first.aggregate_key.platform is SourcePlatform.DISCORD


def test_revision_marker_changes_revision_and_event_identity() -> None:
    first = envelope()
    second = envelope(source_revision_at=SOURCE_REVISION_AT + timedelta(microseconds=1))

    assert first.revision_key != second.revision_key
    assert first.event_key != second.event_key


def test_equivalent_instants_have_the_same_marker_and_event_key() -> None:
    utc_time = SOURCE_REVISION_AT
    pacific_time = utc_time.astimezone(timezone(timedelta(hours=-8)))

    assert build_source_revision_marker(utc_time) == build_source_revision_marker(pacific_time)
    assert envelope(source_revision_at=utc_time).event_key == envelope(
        source_revision_at=pacific_time
    ).event_key


def test_missing_source_marker_is_unversioned_with_unknown_ordering() -> None:
    result = envelope(source_revision_at=None)

    assert result.revision_key.source_revision_marker is None
    assert result.revision_key.ordering_known is False
    assert result.source_observed_at is None


def test_same_unversioned_aggregate_and_text_have_same_revision_and_event_key() -> None:
    first = envelope(source_revision_at=None, received_at=RECEIVED_AT)
    second = envelope(
        source_revision_at=None,
        received_at=RECEIVED_AT + timedelta(hours=1),
    )

    assert first.revision_key == second.revision_key
    assert first.event_key == second.event_key


def test_different_text_changes_hash_and_event_key() -> None:
    first = envelope(raw_text="first")
    second = envelope(raw_text="second")

    assert first.revision_key.normalized_payload_hash != second.revision_key.normalized_payload_hash
    assert first.event_key != second.event_key


def test_received_at_does_not_affect_revision_or_event_identity() -> None:
    first = envelope(received_at=RECEIVED_AT)
    second = envelope(received_at=RECEIVED_AT + timedelta(days=1))

    assert first.revision_key == second.revision_key
    assert first.event_key == second.event_key
    assert first.received_at != second.received_at


def test_event_kind_is_one_logical_message_revision_kind() -> None:
    assert envelope().event_kind == MESSAGE_REVISION_EVENT_KIND == "message_revision"


def test_callback_create_or_edit_kind_is_not_an_input() -> None:
    create = envelope()
    edit = envelope()

    assert create.revision_key == edit.revision_key
    assert create.event_key == edit.event_key
    assert create.event_kind == edit.event_kind == "message_revision"


def test_raw_text_and_payload_json_are_preserved_exactly() -> None:
    raw_text = "  **raw**\n\n"
    payload_json = ' { "content": "raw" } '

    result = envelope(raw_text=raw_text, payload_json=payload_json)

    assert result.raw_text is raw_text
    assert result.payload_json is payload_json


@pytest.mark.parametrize(
    "field, value",
    [
        ("source_revision_at", datetime(2026, 7, 19, 12, 0)),
        ("received_at", datetime(2026, 7, 19, 12, 0)),
    ],
)
def test_naive_datetimes_are_rejected(field: str, value: datetime) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        envelope(**{field: value})


def test_received_and_source_datetimes_are_normalized_to_utc() -> None:
    local_zone = timezone(timedelta(hours=5, minutes=30))
    result = envelope(
        source_revision_at=SOURCE_REVISION_AT.astimezone(local_zone),
        received_at=RECEIVED_AT.astimezone(local_zone),
    )

    assert result.source_observed_at == SOURCE_REVISION_AT
    assert result.received_at == RECEIVED_AT
    assert result.revision_key.source_revision_marker == build_source_revision_marker(
        SOURCE_REVISION_AT
    )


def test_event_key_does_not_contain_raw_message_text() -> None:
    raw_text = "secret message text"

    result = envelope(raw_text=raw_text)

    assert raw_text not in result.event_key
    assert "secret" not in result.event_key


def test_event_key_builder_requires_a_revision_key() -> None:
    with pytest.raises(TypeError):
        build_message_revision_event_key(object())


def test_envelope_is_immutable() -> None:
    result = envelope()

    with pytest.raises(AttributeError):
        result.event_kind = "message_created"


def test_empty_text_is_valid_for_the_mapping_contract() -> None:
    result = envelope(raw_text="")

    assert result.raw_text == ""
    assert result.revision_key.normalized_payload_hash.startswith("v1:text:sha256:")
