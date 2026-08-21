from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from interaction_capture_fixture_gate import (
    InteractionFixtureGateError,
    convert_file,
    sanitize_jsonl,
)
from test_discord_capture_corpus import _listener


SCHEMA = "moa.discord-event-capture.v1"
RAW_IDS = {
    "guild_id": "101",
    "channel_id": "202",
    "interaction_id": "303",
    "application_id": "404",
    "acting_user_id": "505",
}


def _record() -> dict:
    return {
        "sequence": 1,
        "captured_at": "2026-08-20T12:34:56.789+00:00",
        "capture_schema_version": SCHEMA,
        "gateway_event_type": "INTERACTION_CREATE",
        "guild_id": RAW_IDS["guild_id"],
        "channel_id": RAW_IDS["channel_id"],
        "interaction": {
            "id": RAW_IDS["interaction_id"],
            "type": 2,
            "application_id": RAW_IDS["application_id"],
            "acting_user_id": RAW_IDS["acting_user_id"],
            "command_name": "wa",
        },
    }


def _jsonl(record: dict | None = None) -> str:
    return json.dumps(record or _record(), sort_keys=True) + "\n"


def test_gate_admits_one_reduced_type2_record_with_traceable_provenance() -> None:
    artifact = sanitize_jsonl(_jsonl())

    assert artifact["fixture_schema_version"] == 1
    assert artifact["source_schema_version"] == SCHEMA
    assert artifact["provenance"] == {
        "contains_raw_discord_ids": False,
        "contains_sensitive_interaction_data": False,
        "gateway_event_type": "INTERACTION_CREATE",
        "interaction_type": 2,
        "limitations": [
            "raw_source_external_and_uncommitted",
            "single_type_2_application_command_evidence_only",
        ],
        "sanitization": "tests/interaction_capture_fixture_gate.py",
        "source_kind": "external_developer_diagnostic_capture",
    }
    assert artifact["records"] == [
        {
            "captured_at": "2000-01-01T00:00:01Z",
            "event": "INTERACTION_CREATE",
            "interaction": {
                "acting_user": "user_1",
                "application": "application_1",
                "channel": "channel_1",
                "command_name": "wa",
                "guild": "guild_1",
                "id": "interaction_1",
                "type": 2,
            },
            "sequence": 1,
        }
    ]


@pytest.mark.parametrize(
    ("target", "field", "value"),
    (
        ("record", "token", "synthetic-token-secret"),
        ("interaction", "token", "synthetic-token-secret"),
        ("interaction", "options", [{"value": "synthetic-option-secret"}]),
        ("interaction", "resolved", {"users": {"505": {}}}),
        ("interaction", "attachments", {"606": {"filename": "synthetic-private.txt"}}),
        ("interaction", "member", {"roles": ["synthetic-role-secret"]}),
        ("interaction", "user", {"username": "synthetic-private-user"}),
        ("interaction", "profile", {"global_name": "synthetic-private-name"}),
        ("interaction", "roles", ["synthetic-role-secret"]),
        ("interaction", "locale", "synthetic-private-locale"),
        ("interaction", "entitlements", [{"id": "synthetic-entitlement"}]),
        ("interaction", "custom_id", "synthetic-component-secret"),
        ("interaction", "data", {"synthetic_unknown": "synthetic-nested-secret"}),
        ("interaction", "unexpected", "synthetic-unknown-secret"),
    ),
)
def test_gate_rejects_forbidden_and_unknown_fields_without_echoing_values(
    target: str, field: str, value: object
) -> None:
    record = _record()
    destination = record if target == "record" else record["interaction"]
    destination[field] = value

    with pytest.raises(InteractionFixtureGateError) as raised:
        sanitize_jsonl(_jsonl(record))

    assert "synthetic" not in str(raised.value)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        (("gateway_event_type", "MESSAGE_CREATE"), "Gateway event type"),
        (("capture_schema_version", "synthetic-schema"), "source schema"),
        (("sequence", 2), "sequence"),
        (("captured_at", "synthetic-time"), "timestamp"),
    ),
)
def test_gate_rejects_invalid_top_level_contract(
    mutation: tuple[str, object], expected: str
) -> None:
    record = _record()
    field, value = mutation
    record[field] = value

    with pytest.raises(InteractionFixtureGateError, match=expected):
        sanitize_jsonl(_jsonl(record))


@pytest.mark.parametrize("interaction_type", (1, 3, 4, 5, "2", None))
def test_gate_rejects_non_type2_interactions(interaction_type: object) -> None:
    record = _record()
    record["interaction"]["type"] = interaction_type

    with pytest.raises(InteractionFixtureGateError, match="type must be two"):
        sanitize_jsonl(_jsonl(record))


@pytest.mark.parametrize(
    "command_name",
    (None, "", "/wa", "$wa", "wa option", "WA", "a" * 33, "synthetic_unsupported"),
)
def test_gate_rejects_missing_malformed_or_unsupported_command_names(
    command_name: object,
) -> None:
    record = _record()
    if command_name is None:
        del record["interaction"]["command_name"]
    else:
        record["interaction"]["command_name"] = command_name

    with pytest.raises(InteractionFixtureGateError):
        sanitize_jsonl(_jsonl(record))


@pytest.mark.parametrize(
    "field",
    ("guild_id", "channel_id"),
)
def test_gate_rejects_invalid_top_level_identifiers(field: str) -> None:
    record = _record()
    record[field] = "synthetic-not-an-id"

    with pytest.raises(InteractionFixtureGateError, match="identifier"):
        sanitize_jsonl(_jsonl(record))


@pytest.mark.parametrize(
    "field",
    ("id", "application_id", "acting_user_id"),
)
def test_gate_rejects_invalid_interaction_identifiers(field: str) -> None:
    record = _record()
    record["interaction"][field] = "synthetic-not-an-id"

    with pytest.raises(InteractionFixtureGateError, match="identifier"):
        sanitize_jsonl(_jsonl(record))


def test_gate_deterministically_aliases_all_ids_and_normalizes_timestamp() -> None:
    first = json.dumps(sanitize_jsonl(_jsonl()), sort_keys=True)
    second = json.dumps(sanitize_jsonl(_jsonl()), sort_keys=True)

    assert first == second
    assert "2026-08-20" not in first
    assert all(raw_id not in first for raw_id in RAW_IDS.values())
    for alias in (
        "guild_1",
        "channel_1",
        "interaction_1",
        "application_1",
        "user_1",
    ):
        assert alias in first


def test_gate_rejects_malformed_or_multiple_jsonl_records() -> None:
    with pytest.raises(InteractionFixtureGateError, match="valid JSONL"):
        sanitize_jsonl("{synthetic malformed\n")
    with pytest.raises(InteractionFixtureGateError, match="exactly one"):
        sanitize_jsonl(_jsonl() + _jsonl())


def test_gate_writes_deterministic_output_without_overwrite(tmp_path: Path) -> None:
    input_path = (tmp_path / "synthetic-interaction.jsonl").resolve()
    first_output = tmp_path / "first.json"
    second_output = tmp_path / "second.json"
    input_path.write_text(_jsonl(), encoding="utf-8")

    convert_file(input_path, first_output)
    convert_file(input_path, second_output)

    assert first_output.read_bytes() == second_output.read_bytes()
    with pytest.raises(InteractionFixtureGateError, match="already exists"):
        convert_file(input_path, first_output)


def test_gate_cli_rejects_sensitive_input_without_writing_or_echoing_it(tmp_path: Path) -> None:
    input_path = (tmp_path / "synthetic-interaction.jsonl").resolve()
    output_path = tmp_path / "fixture.json"
    record = _record()
    record["interaction"]["token"] = "synthetic-cli-token-secret"
    input_path.write_text(_jsonl(record), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("interaction_capture_fixture_gate.py")),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert not output_path.exists()
    assert "synthetic-cli-token-secret" not in result.stdout + result.stderr


def test_admitted_shape_replays_through_current_interaction_listener(tmp_path: Path) -> None:
    admitted = sanitize_jsonl(_jsonl())["records"][0]["interaction"]
    listener, _catalog = _listener(tmp_path)
    replay_ids = {"guild_1": 123, "channel_1": 789, "user_1": 456}
    replayed = SimpleNamespace(
        guild_id=replay_ids[admitted["guild"]],
        channel_id=replay_ids[admitted["channel"]],
        user=SimpleNamespace(id=replay_ids[admitted["acting_user"]]),
        command=SimpleNamespace(name=admitted["command_name"]),
        data={"name": admitted["command_name"]},
    )

    asyncio.run(listener.handle_interaction(replayed))

    assert listener._contexts[789].identity.account == "user_a"
    assert listener._contexts[789].expected_kind == "roll"
    assert listener._contexts[789].evidence_source == "interaction"


def test_synthetic_gate_input_is_not_mutated_or_presented_as_evidence() -> None:
    record = _record()
    original = deepcopy(record)

    artifact = sanitize_jsonl(_jsonl(record))

    assert record == original
    assert artifact["provenance"]["limitations"] == [
        "raw_source_external_and_uncommitted",
        "single_type_2_application_command_evidence_only",
    ]
