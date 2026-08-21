"""Fail-closed admission gate for one real Mudae MESSAGE_CREATE record."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any


class MessageInteractionFixtureGateError(ValueError):
    """A reduced MESSAGE_CREATE diagnostic record is not admissible."""


SOURCE_SCHEMA_VERSION = "moa.discord-event-capture.v1"
NORMALIZED_TIMESTAMP = "2000-01-01T00:00:01Z"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TOP_KEYS = {
    "sequence",
    "captured_at",
    "capture_schema_version",
    "gateway_event_type",
    "guild_id",
    "channel_id",
    "message_id",
    "author_id",
    "message",
}
MESSAGE_KEYS = {
    "id",
    "author_id",
    "application_id",
    "type",
    "created_at",
    "components",
    "embeds",
    "interaction_metadata",
}
INTERACTION_METADATA_KEYS = {"id", "type", "user_id"}
POSITIVE_ID_PATTERN = re.compile(r"[1-9][0-9]{0,19}")


def _fail(rule: str) -> None:
    raise MessageInteractionFixtureGateError(f"invalid MESSAGE_CREATE diagnostic: {rule}")


def _mapping(value: Any, rule: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(rule)
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], rule: str) -> None:
    if set(value) != expected:
        _fail(f"{rule} fields are not exactly approved")


def _identifier(value: Any, rule: str) -> str:
    if not isinstance(value, str) or POSITIVE_ID_PATTERN.fullmatch(value) is None:
        _fail(rule)
    return value


def _timestamp(value: Any, rule: str) -> None:
    if not isinstance(value, str):
        _fail(rule)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(rule)
    if parsed.tzinfo is None:
        _fail(rule)


def _alias_identifiers(record: Mapping[str, Any], message: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, str]:
    source_ids = {
        "guild": _identifier(record.get("guild_id"), "guild identifier is invalid"),
        "channel": _identifier(record.get("channel_id"), "channel identifier is invalid"),
        "message": _identifier(record.get("message_id"), "message identifier is invalid"),
        "author": _identifier(record.get("author_id"), "author identifier is invalid"),
        "application": _identifier(message.get("application_id"), "application identifier is invalid"),
        "interaction": _identifier(metadata.get("id"), "interaction identifier is invalid"),
        "user": _identifier(metadata.get("user_id"), "invoking-user identifier is invalid"),
    }
    for left, left_id in source_ids.items():
        for right, right_id in source_ids.items():
            if left < right and left_id == right_id and {left, right} != {"application", "author"}:
                _fail("structural identifiers must be distinct")
    return {
        "guild": "guild_1",
        "channel": "channel_1",
        "message": "message_1",
        "author": "mudae",
        "application": "mudae" if source_ids["application"] == source_ids["author"] else "application_1",
        "interaction": "interaction_1",
        "user": "user_1",
    }


def sanitize_record(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and deterministically sanitize one reduced diagnostic record."""
    record = _mapping(raw, "record must be an object")
    _exact_keys(record, TOP_KEYS, "record")
    if record.get("sequence") != 1:
        _fail("sequence must be one")
    if record.get("capture_schema_version") != SOURCE_SCHEMA_VERSION:
        _fail("source schema version is not approved")
    if record.get("gateway_event_type") != "MESSAGE_CREATE":
        _fail("Gateway event type must be MESSAGE_CREATE")
    _timestamp(record.get("captured_at"), "capture timestamp is invalid")

    message = _mapping(record.get("message"), "message must be an object")
    _exact_keys(message, MESSAGE_KEYS, "message")
    _timestamp(message.get("created_at"), "message timestamp is invalid")
    if type(message.get("type")) is not int or message.get("type") != 20:
        _fail("message type must be twenty")
    if message.get("components") != [] or message.get("embeds") != []:
        _fail("message components and embeds must be empty reduced arrays")

    metadata = _mapping(
        message.get("interaction_metadata"),
        "interaction_metadata must be an object",
    )
    _exact_keys(metadata, INTERACTION_METADATA_KEYS, "interaction_metadata")
    if type(metadata.get("type")) is not int or metadata.get("type") != 2:
        _fail("interaction_metadata type must be two")

    aliases = _alias_identifiers(record, message, metadata)
    if _identifier(message.get("id"), "message identifier is invalid") != _identifier(
        record.get("message_id"), "message identifier is invalid"
    ):
        _fail("message identifier does not match message_id")
    if _identifier(message.get("author_id"), "message author identifier is invalid") != _identifier(
        record.get("author_id"), "author identifier is invalid"
    ):
        _fail("message author identifier does not match author_id")

    return {
        "fixture_schema_version": 1,
        "provenance": {
            "contains_message_text": False,
            "contains_raw_discord_ids": False,
            "contains_sensitive_text": False,
            "evidence_classification": "REAL_OBSERVABLE_MESSAGE_TRANSPORT",
            "gateway_event_type": "MESSAGE_CREATE",
            "interaction_metadata_type": 2,
            "limitations": [
                "raw_source_external_and_uncommitted",
                "third_party_mudae_interaction_create_is_unavailable_to_moa",
                "observed_metadata_does_not_supply_invocation_name",
                "no_invocation_name_assertion",
                "not_command_recognition_evidence",
                "synthetic_interaction_listener_behavior_remains_separate",
            ],
            "sanitization": "tests/message_interaction_capture_fixture_gate.py",
            "source_kind": "external_real_developer_diagnostic_capture",
        },
        "records": [
            {
                "author": "mudae",
                "channel": "channel_1",
                "created_at": NORMALIZED_TIMESTAMP,
                "gateway_event_type": "MESSAGE_CREATE",
                "guild": "guild_1",
                "interaction_metadata": {
                    "id": "interaction_1",
                    "type": 2,
                    "user": "user_1",
                },
                "message": {
                    "application": aliases["application"],
                    "author": "mudae",
                    "components": [],
                    "created_at": NORMALIZED_TIMESTAMP,
                    "embeds": [],
                    "id": "message_1",
                    "interaction_metadata": {
                        "id": "interaction_1",
                        "type": 2,
                        "user": "user_1",
                    },
                    "type": 20,
                },
                "message_id": "message_1",
                "sequence": 1,
            }
        ],
        "source_schema_version": SOURCE_SCHEMA_VERSION,
    }


def sanitize_jsonl(text: str) -> dict[str, Any]:
    records: list[Mapping[str, Any]] = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            _fail("input is not valid JSONL")
        if not isinstance(value, Mapping):
            _fail("each JSONL record must be an object")
        records.append(value)
    if len(records) != 1:
        _fail("exactly one MESSAGE_CREATE record is required")
    return sanitize_record(records[0])


def _write_exclusive(path: Path, data: bytes) -> None:
    if not path.parent.is_dir():
        _fail("output parent directory does not exist")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    except FileExistsError:
        _fail("output already exists")
    finally:
        temporary.unlink(missing_ok=True)


def convert_file(input_path: Path, output_path: Path) -> None:
    if not input_path.is_absolute() or not input_path.is_file():
        _fail("input must be an existing absolute regular JSONL file")
    if input_path.suffix.casefold() != ".jsonl":
        _fail("input must be a JSONL file")
    if input_path.resolve().is_relative_to(REPOSITORY_ROOT.resolve()):
        _fail("raw diagnostic input must remain outside the repository")
    if input_path.resolve() == output_path.resolve():
        _fail("input and output paths must differ")
    if output_path.exists():
        _fail("output already exists")
    try:
        text = input_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        _fail("input cannot be read as UTF-8 JSONL")
    artifact = sanitize_jsonl(text)
    serialized = (json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    _write_exclusive(output_path, serialized)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate and sanitize one external MESSAGE_CREATE interaction-metadata diagnostic record."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        convert_file(args.input, args.output)
    except MessageInteractionFixtureGateError as error:
        parser.exit(2, f"message interaction fixture gate: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
