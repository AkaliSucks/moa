"""Fail-closed admission gate for one type-2 interaction diagnostic record."""

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

from moa.commands.registry import COMMAND_REGISTRY, CommandCapability, InvocationSource


class InteractionFixtureGateError(ValueError):
    """A diagnostic record does not satisfy the interaction evidence contract."""


SOURCE_SCHEMA_VERSION = "moa.discord-event-capture.v1"
NORMALIZED_TIMESTAMP = "2000-01-01T00:00:01Z"
TOP_KEYS = {
    "sequence",
    "captured_at",
    "capture_schema_version",
    "gateway_event_type",
    "guild_id",
    "channel_id",
    "interaction",
}
INTERACTION_KEYS = {
    "id",
    "type",
    "application_id",
    "acting_user_id",
    "command_name",
}
COMMAND_NAME_PATTERN = re.compile(r"[a-z0-9_-]{1,32}")


def _fail(rule: str) -> None:
    raise InteractionFixtureGateError(f"invalid interaction diagnostic: {rule}")


def _mapping(value: Any, rule: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(rule)
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], rule: str) -> None:
    if set(value) != expected:
        _fail(f"{rule} fields are not exactly approved")


def _identifier(value: Any, rule: str) -> str:
    if not isinstance(value, str) or not value.isdigit() or int(value) <= 0:
        _fail(rule)
    return value


def _timestamp(value: Any) -> None:
    if not isinstance(value, str):
        _fail("capture timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail("capture timestamp is invalid")
    if parsed.tzinfo is None:
        _fail("capture timestamp is invalid")


def _command_name(value: Any) -> str:
    if not isinstance(value, str) or COMMAND_NAME_PATTERN.fullmatch(value) is None:
        _fail("command name is invalid")
    match = COMMAND_REGISTRY.lookup(
        value,
        source=InvocationSource.INTERACTION_NAME,
        capability=CommandCapability.LISTENER,
    )
    if match is None:
        _fail("command name is not an approved listener interaction")
    return value


def sanitize_record(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and deterministically sanitize one reduced diagnostic record."""
    record = _mapping(raw, "record must be an object")
    _exact_keys(record, TOP_KEYS, "record")
    if record.get("sequence") != 1:
        _fail("sequence must be one")
    if record.get("capture_schema_version") != SOURCE_SCHEMA_VERSION:
        _fail("source schema version is not approved")
    if record.get("gateway_event_type") != "INTERACTION_CREATE":
        _fail("Gateway event type must be INTERACTION_CREATE")
    _timestamp(record.get("captured_at"))

    interaction = _mapping(record.get("interaction"), "interaction must be an object")
    _exact_keys(interaction, INTERACTION_KEYS, "interaction")
    if interaction.get("type") != 2:
        _fail("interaction type must be two")
    command_name = _command_name(interaction.get("command_name"))

    identifiers = (
        _identifier(record.get("guild_id"), "guild identifier is invalid"),
        _identifier(record.get("channel_id"), "channel identifier is invalid"),
        _identifier(interaction.get("id"), "interaction identifier is invalid"),
        _identifier(interaction.get("application_id"), "application identifier is invalid"),
        _identifier(interaction.get("acting_user_id"), "acting-user identifier is invalid"),
    )
    if len(set(identifiers)) != len(identifiers):
        _fail("structural identifiers must be distinct")

    return {
        "fixture_schema_version": 1,
        "provenance": {
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
        },
        "records": [
            {
                "captured_at": NORMALIZED_TIMESTAMP,
                "event": "INTERACTION_CREATE",
                "interaction": {
                    "acting_user": "user_1",
                    "application": "application_1",
                    "channel": "channel_1",
                    "command_name": command_name,
                    "guild": "guild_1",
                    "id": "interaction_1",
                    "type": 2,
                },
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
        _fail("exactly one interaction record is required")
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
    repository_root = Path(__file__).resolve().parents[1]
    if not input_path.is_absolute() or not input_path.is_file():
        _fail("input must be an existing absolute regular JSONL file")
    if input_path.suffix.casefold() != ".jsonl":
        _fail("input must be a JSONL file")
    if input_path.resolve().is_relative_to(repository_root):
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
        description="Validate and sanitize one external type-2 interaction diagnostic record."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        convert_file(args.input, args.output)
    except InteractionFixtureGateError as error:
        parser.exit(2, f"interaction fixture gate: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
