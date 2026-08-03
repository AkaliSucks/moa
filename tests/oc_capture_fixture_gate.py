"""Fail-closed deterministic gate for the authorized ``$oc`` capture."""

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


class OcFixtureGateError(ValueError):
    """A source record does not satisfy the authorized structural case."""


SOURCE_SCHEMA_VERSION = "moa.discord-event-capture.v1"
FIXTURE_SCHEMA_VERSION = 1
EVENT_TOPOLOGY = (
    ("MESSAGE_CREATE", "tu"),
    ("MESSAGE_CREATE", "board"),
    ("MESSAGE_CREATE", "reward"),
    ("MESSAGE_UPDATE", "board"),
    ("MESSAGE_UPDATE", "reward"),
    ("MESSAGE_UPDATE", "reward"),
    ("MESSAGE_UPDATE", "board"),
    ("MESSAGE_UPDATE", "board"),
    ("MESSAGE_UPDATE", "reward"),
    ("MESSAGE_UPDATE", "board"),
    ("MESSAGE_UPDATE", "reward"),
    ("MESSAGE_UPDATE", "board"),
    ("MESSAGE_UPDATE", "reward"),
)
TOP_KEYS = {
    "author_id",
    "capture_schema_version",
    "captured_at",
    "channel_id",
    "gateway_event_type",
    "guild_id",
    "message",
    "message_id",
    "sequence",
}
MESSAGE_CREATE_KEYS = {"author_id", "components", "content", "created_at", "embeds", "id", "type"}
MESSAGE_UPDATE_KEYS = MESSAGE_CREATE_KEYS | {"edited_at"}
ROW_KEYS = {"components", "path", "type"}
LEAF_KEYS = {
    "custom_id_length",
    "custom_id_sha256",
    "disabled",
    "emoji",
    "path",
    "type",
}
EMOJI_KEYS = {"animated", "id_sha256", "kind", "name_length", "name_sha256"}
HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_PATHS = {(row, column) for row in range(5) for column in range(5)}


def _fail(rule: str) -> None:
    raise OcFixtureGateError(f"invalid authorized $oc capture: {rule}")


def _mapping(value: Any, rule: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(rule)
    return value


def _keys(value: Mapping[str, Any], allowed: set[str], rule: str) -> None:
    if set(value) - allowed:
        _fail(f"unapproved {rule} field")


def _timestamp(value: Any, rule: str) -> None:
    if not isinstance(value, str):
        _fail(rule)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(rule)
    if parsed.tzinfo is None:
        _fail(rule)


def _digest(value: Any, rule: str) -> str:
    if not isinstance(value, str) or HEX_DIGEST.fullmatch(value) is None:
        _fail(rule)
    return value


def _emoji(value: Any) -> dict[str, Any]:
    emoji = _mapping(value, "board emoji")
    _keys(emoji, EMOJI_KEYS, "emoji")
    if emoji.get("kind") != "custom":
        _fail("board emoji kind is not custom")
    _digest(emoji.get("id_sha256"), "emoji ID hash is invalid")
    _digest(emoji.get("name_sha256"), "emoji name hash is invalid")
    if type(emoji.get("name_length")) is not int or emoji["name_length"] < 0:
        _fail("emoji name length is invalid")
    if "animated" in emoji and type(emoji["animated"]) is not bool:
        _fail("emoji animated flag is invalid")
    return dict(emoji)


def _board_components(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    components = message.get("components")
    if not isinstance(components, list) or len(components) != 5:
        _fail("board must contain exactly five action rows")
    normalized_rows: list[dict[str, Any]] = []
    paths: set[tuple[int, int]] = set()
    for row_index, raw_row in enumerate(components):
        row = _mapping(raw_row, "board action row")
        _keys(row, ROW_KEYS, "action row")
        if row.get("path") != [row_index] or row.get("type") != 1:
            _fail("board row path or type is invalid")
        leaves = row.get("components")
        if not isinstance(leaves, list) or len(leaves) != 5:
            _fail("board row must contain exactly five leaves")
        normalized_leaves: list[dict[str, Any]] = []
        for column_index, raw_leaf in enumerate(leaves):
            leaf = _mapping(raw_leaf, "board leaf")
            _keys(leaf, LEAF_KEYS, "board leaf")
            expected_path = [row_index, column_index]
            if leaf.get("path") != expected_path or leaf.get("type") != 2:
                _fail("board leaf path or type is invalid")
            coordinate = (row_index, column_index)
            if coordinate in paths:
                _fail("board coordinate is duplicated")
            paths.add(coordinate)
            _digest(leaf.get("custom_id_sha256"), "component hash is invalid")
            if type(leaf.get("custom_id_length")) is not int or leaf["custom_id_length"] < 0:
                _fail("component hash length is invalid")
            normalized_leaves.append(
                {
                    "path": expected_path,
                    "type": 2,
                    "custom_id_sha256": leaf["custom_id_sha256"],
                    "custom_id_length": leaf["custom_id_length"],
                    "emoji": _emoji(leaf.get("emoji")),
                    "disabled": leaf.get("disabled", False),
                }
            )
            if type(normalized_leaves[-1]["disabled"]) is not bool:
                _fail("board disabled state is invalid")
        normalized_rows.append({"path": [row_index], "type": 1, "components": normalized_leaves})
    if paths != EXPECTED_PATHS:
        _fail("board coordinates are not exactly [0..4, 0..4]")
    return normalized_rows


def _flatten(snapshot: list[dict[str, Any]]) -> dict[tuple[int, int], dict[str, Any]]:
    return {
        tuple(leaf["path"]): leaf
        for row in snapshot
        for leaf in row["components"]
    }


def _validate_transitions(board_snapshots: Sequence[list[dict[str, Any]]]) -> None:
    if len(board_snapshots) != 6:
        _fail("exactly six board snapshots are required")
    stable_hashes: dict[tuple[int, int], str] = {}
    maps = [_flatten(snapshot) for snapshot in board_snapshots]
    for snapshot_index, snapshot_map in enumerate(maps):
        for coordinate, leaf in snapshot_map.items():
            previous_hash = stable_hashes.setdefault(coordinate, leaf["custom_id_sha256"])
            if leaf["custom_id_sha256"] != previous_hash:
                _fail(f"component identity changed at board snapshot {snapshot_index}")

    for transition_index in range(4):
        before = maps[transition_index]
        after = maps[transition_index + 1]
        changed: list[tuple[tuple[int, int], dict[str, Any], dict[str, Any]]] = []
        for coordinate in EXPECTED_PATHS:
            before_leaf = before[coordinate]
            after_leaf = after[coordinate]
            if before_leaf["emoji"] != after_leaf["emoji"] or before_leaf["disabled"] != after_leaf["disabled"]:
                changed.append((coordinate, before_leaf, after_leaf))
        if len(changed) != 1:
            _fail("intermediate board transition does not change exactly one cell")
        _, before_leaf, after_leaf = changed[0]
        if (
            before_leaf["custom_id_sha256"] != after_leaf["custom_id_sha256"]
            or before_leaf["emoji"] == after_leaf["emoji"]
            or before_leaf["disabled"] is not False
            or after_leaf["disabled"] is not True
        ):
            _fail("intermediate board transition lacks a visual-change false-to-true action")

    if any(leaf["disabled"] is not True for leaf in maps[-1].values()):
        _fail("final board snapshot is not entirely disabled")


def _build_artifact(records: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], set[str]]:
    if len(records) != len(EVENT_TOPOLOGY):
        _fail("exactly thirteen records are required")
    first = _mapping(records[0], "record")
    guild_id = first.get("guild_id")
    channel_id = first.get("channel_id")
    author_id = first.get("author_id")
    if not all(isinstance(value, str) and value for value in (guild_id, channel_id, author_id)):
        _fail("guild, channel, and author identities must be non-empty strings")

    message_ids: dict[str, str] = {}
    board_snapshots: list[list[dict[str, Any]]] = []
    board_records: list[dict[str, Any]] = []
    forbidden_values: set[str] = set()
    for position, raw in enumerate(records):
        record = _mapping(raw, "record")
        if set(record) != TOP_KEYS:
            _fail("unapproved record field")
        expected_event, role = EVENT_TOPOLOGY[position]
        if record.get("sequence") != position + 1:
            _fail("sequence must be one through thirteen in physical order")
        if record.get("gateway_event_type") != expected_event:
            _fail("unsupported or reordered Gateway event type")
        if record.get("capture_schema_version") != SOURCE_SCHEMA_VERSION:
            _fail("source schema version is not approved")
        if record.get("guild_id") != guild_id:
            _fail("guild scope changes within the capture")
        if record.get("channel_id") != channel_id:
            _fail("channel scope changes within the capture")
        if record.get("author_id") != author_id:
            _fail("Mudae author changes within the capture")
        _timestamp(record.get("captured_at"), "invalid capture timestamp")
        for key in ("guild_id", "channel_id", "message_id", "author_id", "captured_at"):
            value = record.get(key)
            if isinstance(value, str) and value:
                forbidden_values.add(value)

        message = _mapping(record.get("message"), "message object")
        expected_message_keys = MESSAGE_CREATE_KEYS if expected_event == "MESSAGE_CREATE" else MESSAGE_UPDATE_KEYS
        if set(message) != expected_message_keys:
            _fail("unapproved message field")
        if message.get("id") != record.get("message_id") or message.get("author_id") != record.get("author_id"):
            _fail("message identity does not match record identity")
        if message.get("type") != 0:
            _fail("message type must be zero")
        if not isinstance(message.get("content"), str):
            _fail("message content field must be text before sanitization")
        if message["content"]:
            forbidden_values.add(message["content"])
        _timestamp(message.get("created_at"), "invalid message creation timestamp")
        forbidden_values.add(message["created_at"])
        if expected_event == "MESSAGE_UPDATE":
            _timestamp(message.get("edited_at"), "invalid message edited timestamp")
            forbidden_values.add(message["edited_at"])
        if message.get("embeds") != []:
            _fail("nonempty embeds are not approved for this fixture")
        if message.get("id") == author_id:
            _fail("message and Mudae identities must differ")
        message_id = message["id"]
        previous_id = message_ids.setdefault(role, message_id)
        if message_id != previous_id:
            _fail(f"{role} message identity is not stable")

        if role == "board":
            components = _board_components(message)
            board_snapshots.append(components)
            board_records.append(
                {
                    "event": expected_event,
                    "message": {
                        "alias": "board_message_1",
                        "components": components,
                        "type": 0,
                    },
                    "sequence": position + 1,
                }
            )
        elif message.get("components") != []:
            _fail(f"{role} message components must be empty")

    if set(message_ids) != {"tu", "board", "reward"} or len(set(message_ids.values())) != 3:
        _fail("exactly three stable timer, board, and reward message identities are required")
    _validate_transitions(board_snapshots)
    artifact = {
        "fixture_schema_version": FIXTURE_SCHEMA_VERSION,
        "provenance": {
            "contains_message_content": False,
            "contains_raw_discord_ids": False,
            "contains_timestamps": False,
            "limitations": [
                "no_interaction_evidence",
                "no_timer_or_reward_message_content",
                "final_click_not_inferred",
            ],
            "sanitization": "deterministic_purpose_specific_gate",
            "source_kind": "authorized_oc_structural_only_diagnostic_capture",
        },
        "records": board_records,
        "source_schema_version": SOURCE_SCHEMA_VERSION,
    }
    return artifact, forbidden_values


def _validate_sanitized_artifact(artifact: Mapping[str, Any]) -> None:
    if set(artifact) != {"fixture_schema_version", "provenance", "records", "source_schema_version"}:
        _fail("sanitized fixture schema is invalid")
    if artifact.get("fixture_schema_version") != FIXTURE_SCHEMA_VERSION:
        _fail("sanitized fixture version is invalid")
    if artifact.get("source_schema_version") != SOURCE_SCHEMA_VERSION:
        _fail("sanitized source version is invalid")
    records = artifact.get("records")
    if not isinstance(records, list) or len(records) != 6:
        _fail("sanitized board record count is invalid")
    for record_value in records:
        record = _mapping(record_value, "sanitized record")
        if set(record) != {"event", "message", "sequence"}:
            _fail("sanitized record fields are invalid")
        if record["event"] not in {"MESSAGE_CREATE", "MESSAGE_UPDATE"}:
            _fail("sanitized event is invalid")
        message = _mapping(record["message"], "sanitized message")
        if set(message) != {"alias", "components", "type"} or message["alias"] != "board_message_1" or message["type"] != 0:
            _fail("sanitized board message is invalid")
        rows = message["components"]
        if not isinstance(rows, list) or len(rows) != 5:
            _fail("sanitized board row count is invalid")
        for row_index, row_value in enumerate(rows):
            row = _mapping(row_value, "sanitized row")
            if set(row) != ROW_KEYS or row["path"] != [row_index] or row["type"] != 1:
                _fail("sanitized row is invalid")
            leaves = row["components"]
            if not isinstance(leaves, list) or len(leaves) != 5:
                _fail("sanitized board leaf count is invalid")
            for column_index, leaf_value in enumerate(leaves):
                leaf = _mapping(leaf_value, "sanitized leaf")
                if set(leaf) != LEAF_KEYS or leaf["path"] != [row_index, column_index] or leaf["type"] != 2:
                    _fail("sanitized board leaf is invalid")
                _digest(leaf["custom_id_sha256"], "sanitized component hash is invalid")
                if type(leaf["custom_id_length"]) is not int or leaf["custom_id_length"] < 0:
                    _fail("sanitized component hash length is invalid")
                _emoji(leaf["emoji"])
                if type(leaf["disabled"]) is not bool:
                    _fail("sanitized disabled state is invalid")


def sanitize_jsonl(text: str) -> dict[str, Any]:
    if not text.strip():
        _fail("input JSONL is empty")
    records: list[Mapping[str, Any]] = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            _fail("malformed JSONL")
        if not isinstance(value, Mapping):
            _fail("each JSONL record must be an object")
        records.append(value)
    artifact, forbidden_values = _build_artifact(records)
    _validate_sanitized_artifact(artifact)
    serialized = json.dumps(artifact, ensure_ascii=False, sort_keys=True)
    if any(value in serialized for value in forbidden_values if value):
        _fail("raw source value survived sanitization")
    return artifact


def _write_exclusive(path: Path, data: bytes) -> None:
    if not path.parent.is_dir():
        _fail("output parent must be an existing directory")
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


def _is_inside(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def convert_file(input_path: Path, output_path: Path) -> None:
    if not input_path.is_absolute():
        _fail("input path must be absolute")
    try:
        resolved_input = input_path.resolve()
        repository_root = Path(__file__).resolve().parents[1]
        if _is_inside(resolved_input, repository_root):
            _fail("input path must remain outside the repository")
    except OSError:
        _fail("input path cannot be resolved")
    if not input_path.is_file():
        _fail("input must be an existing regular JSONL file")
    if os.path.lexists(output_path):
        _fail("output already exists")
    if resolved_input == output_path.resolve():
        _fail("input and output paths must differ")
    try:
        text = input_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        _fail("input cannot be read as UTF-8 JSONL")
    artifact = sanitize_jsonl(text)
    serialized = (json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_exclusive(output_path, serialized)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and sanitize the authorized thirteen-record $oc capture.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        convert_file(args.input, args.output)
    except OcFixtureGateError as error:
        parser.exit(2, f"oc fixture gate: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
