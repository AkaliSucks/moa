from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

import message_interaction_capture_fixture_gate as gate
from message_interaction_capture_fixture_gate import (
    MessageInteractionFixtureGateError,
    convert_file,
    sanitize_jsonl,
)


SCHEMA = "moa.discord-event-capture.v1"
RAW_IDS = {
    "guild": "101",
    "channel": "202",
    "message": "303",
    "author": "404",
    "application": "505",
    "interaction": "606",
    "user": "707",
}


def _record() -> dict:
    return {
        "sequence": 1,
        "captured_at": "2026-08-21T12:34:56.789+00:00",
        "capture_schema_version": SCHEMA,
        "gateway_event_type": "MESSAGE_CREATE",
        "guild_id": RAW_IDS["guild"],
        "channel_id": RAW_IDS["channel"],
        "message_id": RAW_IDS["message"],
        "author_id": RAW_IDS["author"],
        "message": {
            "id": RAW_IDS["message"],
            "author_id": RAW_IDS["author"],
            "application_id": RAW_IDS["application"],
            "type": 20,
            "created_at": "2026-08-21T12:34:57.789+00:00",
            "components": [],
            "embeds": [],
            "interaction_metadata": {
                "id": RAW_IDS["interaction"],
                "type": 2,
                "user_id": RAW_IDS["user"],
            },
        },
    }


def _jsonl(record: dict | None = None) -> str:
    return json.dumps(record or _record(), sort_keys=True) + "\n"


def test_gate_admits_exact_reduced_real_message_create_schema() -> None:
    artifact = sanitize_jsonl(_jsonl())

    assert artifact["fixture_schema_version"] == 1
    assert artifact["source_schema_version"] == SCHEMA
    assert artifact["provenance"]["evidence_classification"] == "REAL_OBSERVABLE_MESSAGE_TRANSPORT"
    assert artifact["provenance"]["gateway_event_type"] == "MESSAGE_CREATE"
    assert artifact["provenance"]["interaction_metadata_type"] == 2
    assert artifact["provenance"]["contains_message_text"] is False
    record = artifact["records"][0]
    assert record["gateway_event_type"] == "MESSAGE_CREATE"
    assert record["message"]["type"] == 20
    assert record["interaction_metadata"] == {
        "id": "interaction_1",
        "type": 2,
        "user": "user_1",
    }
    assert "command_name" not in json.dumps(artifact)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("capture_schema_version", "wrong-schema", "source schema"),
        ("gateway_event_type", "INTERACTION_CREATE", "Gateway event type"),
        ("sequence", 2, "sequence"),
        ("captured_at", "not-a-timestamp", "timestamp"),
    ),
)
def test_gate_rejects_invalid_top_level_contract(field: str, value: object, message: str) -> None:
    record = _record()
    record[field] = value
    with pytest.raises(MessageInteractionFixtureGateError, match=message):
        sanitize_jsonl(_jsonl(record))


@pytest.mark.parametrize("interaction_type", (1, 3, "2", None, True))
def test_gate_requires_type2_interaction_metadata(interaction_type: object) -> None:
    record = _record()
    record["message"]["interaction_metadata"]["type"] = interaction_type
    with pytest.raises(MessageInteractionFixtureGateError, match="type must be two"):
        sanitize_jsonl(_jsonl(record))


def test_gate_rejects_missing_interaction_metadata_and_interaction_create() -> None:
    record = _record()
    del record["message"]["interaction_metadata"]
    with pytest.raises(MessageInteractionFixtureGateError, match="exactly approved"):
        sanitize_jsonl(_jsonl(record))

    record = _record()
    record["gateway_event_type"] = "INTERACTION_CREATE"
    with pytest.raises(MessageInteractionFixtureGateError):
        sanitize_jsonl(_jsonl(record))


@pytest.mark.parametrize(
    ("target", "field", "value"),
    (
        ("record", "token", "synthetic-token"),
        ("message", "content", "private message"),
        ("message", "reference", {"message_id": "808"}),
        ("metadata", "name", "wa"),
        ("metadata", "interaction", {"name": "wa"}),
        ("message", "options", [{"name": "private"}]),
        ("message", "username", "private-user"),
        ("record", "unexpected", "future-field"),
    ),
)
def test_gate_rejects_unknown_sensitive_or_invented_fields(
    target: str, field: str, value: object
) -> None:
    record = _record()
    destination = record if target == "record" else record["message"]
    if target == "metadata":
        destination = record["message"]["interaction_metadata"]
    destination[field] = value
    with pytest.raises(MessageInteractionFixtureGateError):
        sanitize_jsonl(_jsonl(record))


@pytest.mark.parametrize("field", ("guild_id", "channel_id", "message_id", "author_id"))
def test_gate_rejects_malformed_top_level_ids(field: str) -> None:
    record = _record()
    record[field] = "not-an-id"
    with pytest.raises(MessageInteractionFixtureGateError, match="identifier"):
        sanitize_jsonl(_jsonl(record))


@pytest.mark.parametrize("field", ("id", "author_id", "application_id"))
def test_gate_rejects_malformed_message_ids(field: str) -> None:
    record = _record()
    record["message"][field] = "not-an-id"
    with pytest.raises(MessageInteractionFixtureGateError, match="identifier"):
        sanitize_jsonl(_jsonl(record))


@pytest.mark.parametrize("field", ("id", "user_id"))
def test_gate_rejects_malformed_metadata_ids(field: str) -> None:
    record = _record()
    record["message"]["interaction_metadata"][field] = "not-an-id"
    with pytest.raises(MessageInteractionFixtureGateError, match="identifier"):
        sanitize_jsonl(_jsonl(record))


def test_gate_rejects_mismatched_repeated_ids_and_nonempty_nested_arrays() -> None:
    record = _record()
    record["message"]["id"] = "808"
    with pytest.raises(MessageInteractionFixtureGateError, match="does not match"):
        sanitize_jsonl(_jsonl(record))

    record = _record()
    record["message"]["components"] = [{"type": 1}]
    with pytest.raises(MessageInteractionFixtureGateError, match="empty reduced arrays"):
        sanitize_jsonl(_jsonl(record))


def test_gate_deterministically_aliases_ids_and_normalizes_timestamps() -> None:
    first = json.dumps(sanitize_jsonl(_jsonl()), sort_keys=True)
    second = json.dumps(sanitize_jsonl(_jsonl()), sort_keys=True)
    assert first == second
    assert all(raw_id not in first for raw_id in RAW_IDS.values())
    assert "2026-08-21" not in first
    for alias in ("guild_1", "channel_1", "message_1", "mudae", "application_1", "interaction_1", "user_1"):
        assert alias in first


def test_gate_preserves_the_observed_mudae_author_application_identity_alias() -> None:
    record = _record()
    record["message"]["application_id"] = record["author_id"]

    artifact = sanitize_jsonl(_jsonl(record))
    message = artifact["records"][0]["message"]
    assert message["author"] == "mudae"
    assert message["application"] == "mudae"


def test_gate_rejects_multiple_records_and_does_not_mutate_input() -> None:
    record = _record()
    original = deepcopy(record)
    with pytest.raises(MessageInteractionFixtureGateError, match="exactly one"):
        sanitize_jsonl(_jsonl(record) + _jsonl(record))
    assert record == original


def test_gate_writes_deterministically_and_refuses_overwrite(tmp_path: Path) -> None:
    input_path = (tmp_path / "candidate.jsonl").resolve()
    first_output = tmp_path / "first.json"
    second_output = tmp_path / "second.json"
    input_path.write_text(_jsonl(), encoding="utf-8")

    convert_file(input_path, first_output)
    convert_file(input_path, second_output)
    assert first_output.read_bytes() == second_output.read_bytes()
    with pytest.raises(MessageInteractionFixtureGateError, match="already exists"):
        convert_file(input_path, first_output)


def test_gate_rejects_repository_local_raw_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    input_path = repository_root / "candidate.jsonl"
    input_path.write_text(_jsonl(), encoding="utf-8")
    monkeypatch.setattr(gate, "REPOSITORY_ROOT", repository_root)

    with pytest.raises(MessageInteractionFixtureGateError, match="outside the repository"):
        convert_file(input_path, tmp_path / "fixture.json")
