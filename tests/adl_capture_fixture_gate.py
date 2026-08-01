"""Fail-closed deterministic gate for the authorized six-record ``$adl`` capture."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class AdlFixtureGateError(ValueError):
    """A source record does not satisfy the authorized structural case."""


BASE_TIMESTAMP = datetime(2000, 1, 1, tzinfo=timezone.utc)
SOURCE_SCHEMA_VERSION = "moa.discord-event-capture.v1"
EVENTS = (
    "MESSAGE_CREATE",
    "MESSAGE_CREATE",
    "MESSAGE_UPDATE",
    "MESSAGE_UPDATE",
    "MESSAGE_CREATE",
    "MESSAGE_CREATE",
)
TOP_KEYS = {"sequence", "captured_at", "capture_schema_version", "gateway_event_type", "guild_id", "channel_id", "message_id", "author_id", "message"}
MESSAGE_KEYS = {"id", "author_id", "type", "created_at", "edited_at", "components", "embeds", "application_id", "content", "reference", "interaction_metadata"}
COMPONENT_KEYS = {"path", "type", "custom_id_sha256", "custom_id_length", "values_sha256", "disabled", "components"}
EMBED_KEYS = {"type", "field_count", "has_footer", "title", "description", "fields", "footer"}


def _fail(rule: str) -> None:
    raise AdlFixtureGateError(f"invalid authorized $adl capture: {rule}")


def _mapping(value: Any, rule: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(rule)
    return value


def _keys(value: Mapping[str, Any], allowed: set[str], rule: str) -> None:
    unknown = set(value) - allowed
    if unknown:
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


def _alias(value: str, aliases: dict[str, str], prefix: str) -> str:
    if value not in aliases:
        aliases[value] = f"{prefix}_{len(aliases) + 1}"
    return aliases[value]


def sanitize_records(
    records: Sequence[Mapping[str, Any]],
    *,
    guild_id: str,
    channel_id: str,
    mudae_user_id: str,
    user_ids: Sequence[str],
) -> dict[str, Any]:
    """Validate and deterministically sanitize exactly one authorized six-record capture."""
    if len(user_ids) != 2 or len(set(user_ids)) != 2:
        _fail("authorized role identities must be pairwise distinct")
    if mudae_user_id in user_ids:
        _fail("authorized role identities must be pairwise distinct")
    if len(records) != 6:
        _fail("exactly six records are required")

    source_versions: set[str] = set()
    validated: list[dict[str, Any]] = []
    for position, raw in enumerate(records):
        record = _mapping(raw, "record")
        _keys(record, TOP_KEYS, "record")
        if record.get("sequence") != position + 1:
            _fail("sequence must be 1 through 6 in physical order")
        if record.get("gateway_event_type") != EVENTS[position]:
            _fail("unsupported or reordered Gateway event type")
        version = record.get("capture_schema_version")
        if version != SOURCE_SCHEMA_VERSION:
            _fail("source schema version is not approved")
        source_versions.add(version)
        if record.get("guild_id") != guild_id:
            _fail("unexpected guild")
        if record.get("channel_id") != channel_id:
            _fail("unexpected channel")
        _timestamp(record.get("captured_at"), "invalid capture timestamp")
        message = _mapping(record.get("message"), "message object")
        _keys(message, MESSAGE_KEYS, "message")
        if "application_id" in message:
            _fail("application identity is forbidden")
        for forbidden in ("content", "reference", "interaction_metadata"):
            if forbidden in message:
                _fail(f"message {forbidden} is forbidden")
        if message.get("id") != record.get("message_id") or message.get("author_id") != record.get("author_id"):
            _fail("message identity does not match record identity")
        if message.get("type") != 0:
            _fail("message type must be zero")
        _timestamp(message.get("created_at"), "invalid creation timestamp")
        if position in (0, 1, 4, 5):
            if "edited_at" in message:
                _fail("edited timestamp is present on a create record")
        elif not isinstance(message.get("edited_at"), str):
            _fail("edited timestamp is required on an update record")
        elif not _timestamp(message["edited_at"], "invalid edited timestamp"):
            pass
        validated.append({"record": record, "message": message})

    if len(source_versions) != 1:
        _fail("source schema version must be consistent")
    authors = [item["message"]["author_id"] for item in validated]
    if authors[0] == mudae_user_id or authors[4] == mudae_user_id:
        _fail("authorized role identities must be pairwise distinct")
    if authors[1:4] != [mudae_user_id] * 3 or authors[5] != mudae_user_id:
        _fail("Mudae author relationship is invalid")
    if authors[0] not in user_ids or authors[4] not in user_ids or authors[0] == authors[4]:
        _fail("request author relationship is invalid")
    if set(authors) != {user_ids[0], user_ids[1], mudae_user_id}:
        _fail("exactly two users and one Mudae author are required")

    message_ids = [item["message"]["id"] for item in validated]
    if message_ids[1] != message_ids[2] or message_ids[2] != message_ids[3]:
        _fail("response message identity is not joined across updates")
    if message_ids[0] == message_ids[4] or message_ids[5] == message_ids[1] or len(set(message_ids)) != 4:
        _fail("message identity relationship is invalid")
    if validated[0]["message"]["id"] == validated[1]["message"]["id"]:
        _fail("request and response identities must differ")

    user_aliases: dict[str, str] = {}
    for author in authors:
        if author in user_ids:
            _alias(author, user_aliases, "user")
    guild_alias = "guild_1"
    channel_alias = "channel_1"
    message_aliases: dict[str, str] = {}
    component_aliases: dict[str, str] = {}

    def message_name(message_id: str) -> str:
        if message_id not in message_aliases:
            message_aliases[message_id] = f"response_message_{len([x for x in message_aliases if x]) + 1}"
        return message_aliases[message_id]

    request_names = {message_ids[0]: "request_message_1", message_ids[4]: "request_message_2"}
    response_names = {message_ids[1]: "response_message_1", message_ids[5]: "response_message_2"}

    component_shapes: list[tuple[tuple[int, ...], int, str]] | None = None
    sanitized_records: list[dict[str, Any]] = []
    for position, item in enumerate(validated):
        message = item["message"]
        source_id = message["id"]
        semantic_message = request_names.get(source_id) or response_names.get(source_id) or message_name(source_id)
        initial_create = 1 if position == 0 else 2 if position in (1, 2, 3) else 5 if position == 4 else 6
        normalized_components: list[dict[str, Any]] = []
        components = message.get("components")
        if position in (1, 2, 3):
            rows = components if isinstance(components, list) else None
            if rows is None or len(rows) != 1:
                _fail("response component shape is invalid")
            row = _mapping(rows[0], "component")
            _keys(row, COMPONENT_KEYS, "component")
            if row.get("path") != [0] or row.get("type") != 1 or not isinstance(row.get("components"), list) or len(row["components"]) != 2:
                _fail("response action-row component shape is invalid")
            normalized_components.append({"path": [0], "type": 1})
            current_shape: list[tuple[tuple[int, ...], int, str]] = []
            for index, child in enumerate(row["components"]):
                child_map = _mapping(child, "nested component")
                _keys(child_map, COMPONENT_KEYS, "component")
                if "components" in child_map:
                    _fail("nested component cannot contain children")
                digest = child_map.get("custom_id_sha256")
                if not isinstance(digest, str) or len(digest) != 64:
                    _fail("component hash is invalid")
                if child_map.get("path") != [0, index] or child_map.get("type") != 2:
                    _fail("nested component path or type is invalid")
                current_shape.append((tuple(child_map["path"]), child_map["type"], digest))
                normalized_components.append({"path": [0, index], "type": 2, "token": _alias(digest, component_aliases, "component_token")})
            if current_shape[0][2] == current_shape[1][2]:
                _fail("component positions must represent distinct hash classes")
            if component_shapes is None:
                component_shapes = current_shape
            elif current_shape != component_shapes:
                _fail("component hash equivalence is inconsistent")
        elif "components" in message:
            if not isinstance(components, list) or components:
                _fail("components are not allowed on this record")

        embeds = message.get("embeds")
        normalized_embeds: list[dict[str, Any]] = []
        if position in (0, 4):
            if "embeds" in message and (not isinstance(embeds, list) or embeds):
                _fail("request embeds are not allowed")
        else:
            if not isinstance(embeds, list) or len(embeds) != 1:
                _fail("exactly one embed is required")
            embed = _mapping(embeds[0], "embed")
            _keys(embed, EMBED_KEYS, "embed")
            for forbidden in ("title", "description", "fields", "footer"):
                if forbidden in embed:
                    _fail("embed text is forbidden")
            if embed.get("type") != "rich":
                _fail("embed type must be rich")
            if position in (1, 2, 3) and embed.get("has_footer") is not True:
                _fail("response embed footer is required")
            normalized_embeds.append({"has_footer": embed.get("has_footer") is True, "type": "rich"})
        semantic_author = "mudae" if message["author_id"] == mudae_user_id else user_aliases[message["author_id"]]
        normalized_records_message = {
            "alias": semantic_message,
            "author": semantic_author,
            "components": normalized_components,
            "created_at": (BASE_TIMESTAMP + timedelta(seconds=initial_create)).isoformat().replace("+00:00", "Z"),
            "edited_at": (BASE_TIMESTAMP + timedelta(seconds=position + 1)).isoformat().replace("+00:00", "Z") if position in (2, 3) else None,
            "interaction_metadata": None,
            "reference": None,
            "type": 0,
            "embeds": normalized_embeds,
        }
        sanitized_records.append({"channel": channel_alias, "event": item["record"]["gateway_event_type"], "guild": guild_alias, "message": normalized_records_message, "sequence": position + 1})

    return {
        "fixture_schema_version": 1,
        "provenance": {
            "contains_message_embed_or_component_text": False,
            "contains_raw_discord_ids": False,
            "limitations": ["no_response_to_user_association", "not_durable_workflow_evidence"],
            "sanitization": "deterministic_purpose_specific_gate",
            "source_kind": "authorized_structural_only_diagnostic_capture",
        },
        "records": sanitized_records,
        "source_schema_version": SOURCE_SCHEMA_VERSION,
    }


def sanitize_jsonl(text: str, **authorized: str | Sequence[str]) -> dict[str, Any]:
    records: list[Mapping[str, Any]] = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            _fail("malformed JSON")
        if not isinstance(value, Mapping):
            _fail("each JSONL record must be an object")
        records.append(value)
    return sanitize_records(records, **authorized)  # type: ignore[arg-type]


def _write_exclusive(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=False, exist_ok=True)
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


def convert_file(input_path: Path, output_path: Path, *, guild_id: str, channel_id: str, mudae_user_id: str, user_ids: Sequence[str]) -> None:
    if not input_path.is_file():
        _fail("input must be an existing regular JSONL file")
    if input_path.resolve() == output_path.resolve():
        _fail("input and output paths must differ")
    if output_path.exists():
        _fail("output already exists")
    try:
        text = input_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        _fail("input cannot be read as UTF-8 JSONL")
    artifact = sanitize_jsonl(text, guild_id=guild_id, channel_id=channel_id, mudae_user_id=mudae_user_id, user_ids=user_ids)
    serialized = (json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_exclusive(output_path, serialized)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and sanitize the authorized six-record $adl capture.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--guild-id", required=True)
    parser.add_argument("--channel-id", required=True)
    parser.add_argument("--mudae-user-id", required=True)
    parser.add_argument("--user-id", action="append", required=True)
    args = parser.parse_args(argv)
    try:
        if len(args.user_id) != 2:
            _fail("exactly two user-id arguments are required")
        convert_file(args.input, args.output, guild_id=args.guild_id, channel_id=args.channel_id, mudae_user_id=args.mudae_user_id, user_ids=args.user_id)
    except AdlFixtureGateError as error:
        parser.exit(2, f"adl fixture gate: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
