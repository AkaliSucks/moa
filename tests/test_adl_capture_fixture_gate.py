from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from adl_capture_fixture_gate import AdlFixtureGateError, convert_file, sanitize_jsonl

IDS = {"guild_id": "synthetic-guild", "channel_id": "synthetic-channel", "mudae_user_id": "synthetic-mudae", "user_ids": ["synthetic-user-a", "synthetic-user-b"]}
SCHEMA = "moa.discord-event-capture.v1"


def _record(sequence: int, event: str, message_id: str, author_id: str, *, edited: str | None = None, components: bool = False, footer: bool = False) -> dict:
    message = {"id": message_id, "author_id": author_id, "type": 0, "created_at": f"2024-01-01T00:00:0{sequence}Z"}
    if sequence in (2, 3, 4, 6):
        message["embeds"] = [{"type": "rich", "has_footer": footer}]
    if edited is not None:
        message["edited_at"] = edited
    if components:
        message["components"] = [{"path": [0], "type": 1, "components": [{"path": [0, 0], "type": 2, "custom_id_sha256": "a" * 64, "custom_id_length": 3}, {"path": [0, 1], "type": 2, "custom_id_sha256": "b" * 64, "custom_id_length": 4}]}]
    return {"sequence": sequence, "captured_at": f"2024-01-01T00:00:0{sequence}Z", "capture_schema_version": SCHEMA, "gateway_event_type": event, "guild_id": IDS["guild_id"], "channel_id": IDS["channel_id"], "message_id": message_id, "author_id": author_id, "message": message}


def _capture() -> list[dict]:
    return [_record(1, "MESSAGE_CREATE", "request-a", "synthetic-user-a"), _record(2, "MESSAGE_CREATE", "response-a", "synthetic-mudae", components=True, footer=True), _record(3, "MESSAGE_UPDATE", "response-a", "synthetic-mudae", edited="2024-01-01T00:00:03Z", components=True, footer=True), _record(4, "MESSAGE_UPDATE", "response-a", "synthetic-mudae", edited="2024-01-01T00:00:04Z", components=True, footer=True), _record(5, "MESSAGE_CREATE", "request-b", "synthetic-user-b"), _record(6, "MESSAGE_CREATE", "response-b", "synthetic-mudae")]


def _jsonl(records: list[dict]) -> str:
    return "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)


def _expected_artifact() -> dict:
    def message(alias: str, author: str, created_at: str, edited_at: str | None, components: list[dict], embeds: list[dict]) -> dict:
        return {"alias": alias, "author": author, "components": components, "created_at": created_at, "edited_at": edited_at, "interaction_metadata": None, "reference": None, "type": 0, "embeds": embeds}

    component_1 = {"path": [0, 0], "type": 2, "token": "component_token_1"}
    component_2 = {"path": [0, 1], "type": 2, "token": "component_token_2"}
    response_components = [{"path": [0], "type": 1}, component_1, component_2]
    return {
        "fixture_schema_version": 1,
        "source_schema_version": SCHEMA,
        "provenance": {
            "contains_message_embed_or_component_text": False,
            "contains_raw_discord_ids": False,
            "limitations": ["no_response_to_user_association", "not_durable_workflow_evidence"],
            "sanitization": "deterministic_purpose_specific_gate",
            "source_kind": "authorized_structural_only_diagnostic_capture",
        },
        "records": [
            {"channel": "channel_1", "event": "MESSAGE_CREATE", "guild": "guild_1", "message": message("request_message_1", "user_1", "2000-01-01T00:00:01Z", None, [], []), "sequence": 1},
            {"channel": "channel_1", "event": "MESSAGE_CREATE", "guild": "guild_1", "message": message("response_message_1", "mudae", "2000-01-01T00:00:02Z", None, response_components, [{"has_footer": True, "type": "rich"}]), "sequence": 2},
            {"channel": "channel_1", "event": "MESSAGE_UPDATE", "guild": "guild_1", "message": message("response_message_1", "mudae", "2000-01-01T00:00:02Z", "2000-01-01T00:00:03Z", response_components, [{"has_footer": True, "type": "rich"}]), "sequence": 3},
            {"channel": "channel_1", "event": "MESSAGE_UPDATE", "guild": "guild_1", "message": message("response_message_1", "mudae", "2000-01-01T00:00:02Z", "2000-01-01T00:00:04Z", response_components, [{"has_footer": True, "type": "rich"}]), "sequence": 4},
            {"channel": "channel_1", "event": "MESSAGE_CREATE", "guild": "guild_1", "message": message("request_message_2", "user_2", "2000-01-01T00:00:05Z", None, [], []), "sequence": 5},
            {"channel": "channel_1", "event": "MESSAGE_CREATE", "guild": "guild_1", "message": message("response_message_2", "mudae", "2000-01-01T00:00:06Z", None, [], [{"has_footer": False, "type": "rich"}]), "sequence": 6},
        ],
    }


def test_gate_converts_authorized_six_record_capture() -> None:
    artifact = sanitize_jsonl(_jsonl(_capture()), **IDS)
    assert artifact == _expected_artifact()


def test_gate_output_is_byte_deterministic() -> None:
    first = json.dumps(sanitize_jsonl(_jsonl(_capture()), **IDS), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    second = json.dumps(sanitize_jsonl(_jsonl(_capture()), **IDS), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    assert first.encode() == second.encode()


def test_gate_aliases_are_stable_and_preserve_relationships() -> None:
    records = sanitize_jsonl(_jsonl(_capture()), **IDS)["records"]
    assert records[0]["message"]["author"] == "user_1"
    assert records[4]["message"]["author"] == "user_2"
    assert records[1]["message"]["author"] == records[5]["message"]["author"] == "mudae"
    assert records[1]["message"]["components"][1]["token"] == records[3]["message"]["components"][1]["token"] == "component_token_1"
    assert records[1]["message"]["components"][2]["token"] == records[3]["message"]["components"][2]["token"] == "component_token_2"
    assert records[1]["message"]["components"][1]["token"] != records[1]["message"]["components"][2]["token"]


@pytest.mark.parametrize("mudae_id, user_ids", [("synthetic-user-a", ["synthetic-user-a", "synthetic-user-c"]), ("synthetic-user-b", ["synthetic-user-a", "synthetic-user-b"]), ("synthetic-user-c", ["synthetic-user-a", "synthetic-user-a"])])
def test_gate_rejects_overlapping_authorized_roles(mudae_id: str, user_ids: list[str]) -> None:
    records = _capture()
    with pytest.raises(AdlFixtureGateError, match="pairwise distinct"):
        sanitize_jsonl(_jsonl(records), guild_id=IDS["guild_id"], channel_id=IDS["channel_id"], mudae_user_id=mudae_id, user_ids=user_ids)


def test_gate_rejects_record_author_collapsing_user_into_mudae() -> None:
    records = _capture()
    records[0]["author_id"] = records[0]["message"]["author_id"] = IDS["mudae_user_id"]
    with pytest.raises(AdlFixtureGateError, match="pairwise distinct"):
        sanitize_jsonl(_jsonl(records), **IDS)


@pytest.mark.parametrize("version", ["moa.discord-event-capture.v0", None])
def test_gate_rejects_missing_or_wrong_source_schema(version: str | None) -> None:
    records = _capture()
    for record in records:
        if version is None:
            del record["capture_schema_version"]
        else:
            record["capture_schema_version"] = version
    with pytest.raises(AdlFixtureGateError, match="source schema version") as raised:
        sanitize_jsonl(_jsonl(records), **IDS)
    if version is not None:
        assert version not in str(raised.value)


def test_gate_rejects_inconsistent_source_schema() -> None:
    records = _capture()
    records[5]["capture_schema_version"] = "moa.discord-event-capture.v2"
    with pytest.raises(AdlFixtureGateError, match="source schema version"):
        sanitize_jsonl(_jsonl(records), **IDS)


@pytest.mark.parametrize("application_id", [None, "synthetic-application"])
def test_gate_rejects_application_id_by_presence(application_id: str | None) -> None:
    records = _capture()
    records[0]["message"]["application_id"] = application_id
    with pytest.raises(AdlFixtureGateError, match="application identity") as raised:
        sanitize_jsonl(_jsonl(records), **IDS)
    if application_id is not None:
        assert application_id not in str(raised.value)


def test_gate_rejects_identical_component_hash_classes() -> None:
    records = _capture()
    records[1]["message"]["components"][0]["components"][1]["custom_id_sha256"] = "a" * 64
    with pytest.raises(AdlFixtureGateError, match="distinct hash classes"):
        sanitize_jsonl(_jsonl(records), **IDS)


def test_gate_rejects_component_hash_change_across_update() -> None:
    records = _capture()
    records[3]["message"]["components"][0]["components"][0]["custom_id_sha256"] = "c" * 64
    with pytest.raises(AdlFixtureGateError, match="equivalence"):
        sanitize_jsonl(_jsonl(records), **IDS)


def test_gate_rejects_reordered_input_records() -> None:
    records = _capture()
    records[0], records[1] = records[1], records[0]
    with pytest.raises(AdlFixtureGateError, match="sequence"):
        sanitize_jsonl(_jsonl(records), **IDS)


def test_gate_rejects_malformed_and_non_object_json() -> None:
    for text in ("{bad\n", "[]\n"):
        with pytest.raises(AdlFixtureGateError):
            sanitize_jsonl(text, **IDS)


@pytest.mark.parametrize("change", ["duplicate", "missing", "non_monotonic", "out_of_range"])
def test_gate_rejects_invalid_sequence_shapes(change: str) -> None:
    records = _capture()
    records[1]["sequence"] = {"duplicate": 1, "missing": 7, "non_monotonic": 1, "out_of_range": 9}[change]
    with pytest.raises(AdlFixtureGateError, match="sequence"):
        sanitize_jsonl(_jsonl(records), **IDS)


def test_gate_rejects_unsupported_or_reordered_event_types() -> None:
    records = _capture()
    records[2]["gateway_event_type"] = "INTERACTION_CREATE"
    with pytest.raises(AdlFixtureGateError, match="event"):
        sanitize_jsonl(_jsonl(records), **IDS)


def test_gate_rejects_unexpected_scope_or_authors() -> None:
    for field, value in (("guild_id", "other"), ("channel_id", "other"), ("author_id", "other")):
        records = _capture()
        records[0][field] = value
        with pytest.raises(AdlFixtureGateError):
            sanitize_jsonl(_jsonl(records), **IDS)


@pytest.mark.parametrize("field", ["content", "reference", "interaction_metadata", "application_id", "flags", "nonce"])
def test_gate_rejects_forbidden_sensitive_fields(field: str) -> None:
    records = _capture()
    records[0]["message"][field] = "synthetic-secret"
    with pytest.raises(AdlFixtureGateError):
        sanitize_jsonl(_jsonl(records), **IDS)


def test_gate_rejects_references_interaction_metadata_and_interaction_events() -> None:
    records = _capture()
    records[0]["message"]["reference"] = None
    with pytest.raises(AdlFixtureGateError):
        sanitize_jsonl(_jsonl(records), **IDS)


def test_gate_rejects_unapproved_message_fields() -> None:
    records = _capture()
    records[0]["message"]["unknown"] = True
    with pytest.raises(AdlFixtureGateError, match="unapproved"):
        sanitize_jsonl(_jsonl(records), **IDS)


@pytest.mark.parametrize("mutation", ["split_response", "reuse_request", "wrong_author", "reuse_record_six"])
def test_gate_rejects_invalid_message_relationships(mutation: str) -> None:
    records = _capture()
    if mutation == "split_response":
        records[3]["message_id"] = records[3]["message"]["id"] = "different"
    if mutation == "reuse_request":
        records[4]["message_id"] = records[4]["message"]["id"] = "request-a"
    if mutation == "wrong_author":
        records[2]["author_id"] = records[2]["message"]["author_id"] = "synthetic-user-a"
    if mutation == "reuse_record_six":
        records[5]["message_id"] = records[5]["message"]["id"] = "response-a"
    with pytest.raises(AdlFixtureGateError):
        sanitize_jsonl(_jsonl(records), **IDS)


def test_gate_rejects_invalid_timestamp_shape() -> None:
    records = _capture()
    records[2]["message"]["edited_at"] = "not-a-timestamp"
    with pytest.raises(AdlFixtureGateError, match="timestamp"):
        sanitize_jsonl(_jsonl(records), **IDS)


def test_gate_rejects_invalid_component_or_embed_shape() -> None:
    records = _capture()
    records[2]["message"]["components"][0]["components"][1]["custom_id_sha256"] = "c" * 64
    with pytest.raises(AdlFixtureGateError, match="component"):
        sanitize_jsonl(_jsonl(records), **IDS)


def test_gate_rejects_unknown_keys_at_each_validated_level() -> None:
    for target in ("record", "message", "component", "embed"):
        records = _capture()
        if target == "record":
            records[0]["unexpected"] = True
        if target == "message":
            records[0]["message"]["unexpected"] = True
        if target == "component":
            records[1]["message"]["components"][0]["unexpected"] = True
        if target == "embed":
            records[1]["message"]["embeds"][0]["unexpected"] = True
        with pytest.raises(AdlFixtureGateError, match="unapproved"):
            sanitize_jsonl(_jsonl(records), **IDS)


def test_gate_output_contains_no_source_identifiers_or_fingerprints() -> None:
    output = json.dumps(sanitize_jsonl(_jsonl(_capture()), **IDS), sort_keys=True)
    for value in ("synthetic-guild", "synthetic-channel", "synthetic-mudae", "synthetic-user-a", "2024-01-01", "a" * 64, "b" * 64):
        assert value not in output


def test_cli_uses_synthetic_files_and_returns_expected_exit_codes(tmp_path: Path) -> None:
    input_path = tmp_path / "synthetic.jsonl"
    output_path = tmp_path / "fixture.json"
    input_path.write_text(_jsonl(_capture()), encoding="utf-8")
    args = [sys.executable, str(Path(__file__).with_name("adl_capture_fixture_gate.py")), "--input", str(input_path), "--output", str(output_path), "--guild-id", IDS["guild_id"], "--channel-id", IDS["channel_id"], "--mudae-user-id", IDS["mudae_user_id"], "--user-id", IDS["user_ids"][0], "--user-id", IDS["user_ids"][1]]
    success = subprocess.run(args, capture_output=True, text=True)
    assert success.returncode == 0 and output_path.is_file()
    input_path.write_text("{bad\n", encoding="utf-8")
    failed_output = tmp_path / "failed.json"
    failed_args = [*args]
    failed_args[failed_args.index(str(output_path))] = str(failed_output)
    failed = subprocess.run(failed_args, capture_output=True, text=True)
    assert failed.returncode != 0 and not failed_output.exists() and "synthetic-guild" not in failed.stderr


def test_gate_refuses_to_overwrite_existing_output(tmp_path: Path) -> None:
    input_path = tmp_path / "synthetic.jsonl"
    output_path = tmp_path / "fixture.json"
    sentinel = b"existing output sentinel; not sanitized fixture JSON"
    input_text = _jsonl(_capture())
    input_path.write_text(input_text, encoding="utf-8")
    output_path.write_bytes(sentinel)
    args = [
        sys.executable,
        str(Path(__file__).with_name("adl_capture_fixture_gate.py")),
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--guild-id",
        IDS["guild_id"],
        "--channel-id",
        IDS["channel_id"],
        "--mudae-user-id",
        IDS["mudae_user_id"],
        "--user-id",
        IDS["user_ids"][0],
        "--user-id",
        IDS["user_ids"][1],
    ]

    result = subprocess.run(args, capture_output=True, text=True)

    assert result.returncode == 2
    assert output_path.is_file()
    assert output_path.read_bytes() == sentinel
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted([input_path.name, output_path.name])
    diagnostics = result.stdout + result.stderr
    sensitive_values = (
        IDS["guild_id"],
        IDS["channel_id"],
        IDS["mudae_user_id"],
        *IDS["user_ids"],
        *[record["message_id"] for record in _capture()],
        "2024-01-01",
        "a" * 64,
        "b" * 64,
        sentinel.decode(),
        input_text,
    )
    for value in sensitive_values:
        assert value not in diagnostics


def test_gate_serializes_complete_artifact_with_one_lf(tmp_path: Path) -> None:
    input_path = tmp_path / "synthetic.jsonl"
    output_path = tmp_path / "fixture.json"
    input_path.write_text(_jsonl(_capture()), encoding="utf-8")
    convert_file(input_path, output_path, **IDS)
    serialized = output_path.read_bytes()
    assert serialized.endswith(b"\n") and not serialized.endswith(b"\n\n")
    assert json.loads(serialized) == _expected_artifact()


def test_gate_does_not_write_output_on_failure(tmp_path: Path) -> None:
    output = tmp_path / "fixture.json"
    with pytest.raises(AdlFixtureGateError):
        sanitize_jsonl(_jsonl(_capture()[:-1]), **IDS)
    assert not output.exists()
