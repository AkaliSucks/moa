from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from oh_capture_fixture_gate import OhFixtureGateError, convert_file, sanitize_jsonl


ROOT = Path(__file__).parent


def _hex(number: int) -> str:
    return f"{number:064x}"


def _emoji(label: str) -> dict[str, Any]:
    number = sum(ord(character) for character in label) + len(label) * 100
    return {
        "kind": "custom",
        "id_sha256": _hex(number + 1),
        "name_sha256": _hex(number + 2),
        "name_length": len(label),
    }


def _capture() -> list[dict[str, Any]]:
    hidden = _emoji("hidden")
    purple = _emoji("purple")
    blue = _emoji("blue")
    teal = _emoji("teal")
    yellow = _emoji("yellow")
    light = _emoji("light")
    states: list[dict[tuple[int, int], tuple[dict[str, Any], bool]]] = []
    initial: dict[tuple[int, int], tuple[dict[str, Any], bool]] = {
        (row, column): (hidden, False) for row in range(5) for column in range(5)
    }
    initial[(1, 2)] = (purple, False)
    states.append(initial)
    after_purple = copy.deepcopy(initial)
    after_purple[(1, 2)] = (purple, True)
    states.append(after_purple)
    after_blue = copy.deepcopy(after_purple)
    after_blue.update({(0, 0): (blue, True), (0, 4): (blue, False), (1, 0): (yellow, False), (4, 4): (teal, False)})
    states.append(after_blue)
    after_second_blue = copy.deepcopy(after_blue)
    after_second_blue[(0, 4)] = (blue, True)
    after_second_blue.update({(0, 2): (teal, False), (2, 2): (light, False), (4, 2): (teal, False)})
    states.append(after_second_blue)
    after_light = copy.deepcopy(after_second_blue)
    after_light[(2, 2)] = (light, True)
    states.append(after_light)
    after_yellow = copy.deepcopy(after_light)
    after_yellow[(1, 0)] = (yellow, True)
    states.append(after_yellow)
    terminal = copy.deepcopy(after_yellow)
    for coordinate, (emoji, _disabled) in list(terminal.items()):
        terminal[coordinate] = (emoji, True)
    states.append(terminal)

    board_message_id = "raw-board-message-id"
    reward_message_id = "raw-reward-message-id"
    records: list[dict[str, Any]] = []
    roles = (
        ("MESSAGE_CREATE", "board"),
        ("MESSAGE_CREATE", "reward"),
        ("MESSAGE_UPDATE", "reward"),
        ("MESSAGE_UPDATE", "board"),
        ("MESSAGE_UPDATE", "reward"),
        ("MESSAGE_UPDATE", "board"),
        ("MESSAGE_UPDATE", "reward"),
        ("MESSAGE_UPDATE", "board"),
        ("MESSAGE_UPDATE", "reward"),
        ("MESSAGE_UPDATE", "board"),
        ("MESSAGE_UPDATE", "reward"),
        ("MESSAGE_UPDATE", "board"),
        ("MESSAGE_UPDATE", "reward"),
        ("MESSAGE_UPDATE", "board"),
    )
    board_index = 0
    for position, (event, role) in enumerate(roles):
        message_id = board_message_id if role == "board" else reward_message_id
        components: list[dict[str, Any]] = []
        if role == "board":
            for row in range(5):
                leaves = []
                for column in range(5):
                    emoji, disabled = states[board_index][(row, column)]
                    leaf: dict[str, Any] = {
                        "path": [row, column],
                        "type": 2,
                        "custom_id_sha256": _hex(row * 5 + column + 1),
                        "custom_id_length": 12,
                        "emoji": emoji,
                    }
                    if disabled:
                        leaf["disabled"] = True
                    leaves.append(leaf)
                components.append({"path": [row], "type": 1, "components": leaves})
            board_index += 1
        message: dict[str, Any] = {
            "id": message_id,
            "author_id": "raw-mudae-author-id",
            "type": 0,
            "created_at": f"2026-01-01T00:{position:02d}:00+00:00",
            "components": components,
            "embeds": [],
            "content": f"raw message content {position} <:raw:987654321> ",
        }
        if event == "MESSAGE_UPDATE":
            message["edited_at"] = f"2026-01-01T01:{position:02d}:00+00:00"
        records.append(
            {
                "sequence": position + 1,
                "captured_at": f"2026-01-01T02:{position:02d}:00+00:00",
                "capture_schema_version": "moa.discord-event-capture.v1",
                "gateway_event_type": event,
                "guild_id": "raw-guild-id",
                "channel_id": "raw-channel-id",
                "message_id": message_id,
                "author_id": "raw-mudae-author-id",
                "message": message,
            }
        )
    return records


def _jsonl(records: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)


def _sanitize(records: list[dict[str, Any]]) -> dict[str, Any]:
    return sanitize_jsonl(_jsonl(records))


def _board_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in records if len(record["message"]["components"]) == 5]


def _leaves(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [leaf for row in record["message"]["components"] for leaf in row["components"]]


def test_gate_accepts_valid_capture_and_sanitizes_privacy_sensitive_fields(tmp_path: Path) -> None:
    input_path = tmp_path / "capture.jsonl"
    output_path = tmp_path / "fixture.json"
    input_path.write_text(_jsonl(_capture()), encoding="utf-8")
    convert_file(input_path.resolve(), output_path)
    artifact = json.loads(output_path.read_text(encoding="utf-8"))
    assert artifact["records"][0]["message"]["alias"] == "board_message_1"
    assert artifact["records"][1]["message"]["alias"] == "reward_message_1"
    leaves = _leaves(artifact["records"][0])
    assert len(leaves) == 25 and all(leaf["disabled"] is False for leaf in leaves)
    serialized = output_path.read_text(encoding="utf-8")
    for raw_value in ("raw-guild-id", "raw-channel-id", "raw-board-message-id", "raw-reward-message-id", "raw-mudae-author-id", "raw message content", "<:raw:987654321>", "2026-01-01"):
        assert raw_value not in serialized


def test_gate_is_deterministic_for_identical_logical_inputs(tmp_path: Path) -> None:
    input_path = tmp_path / "capture.jsonl"
    output_a = tmp_path / "a.json"
    output_b = tmp_path / "b.json"
    input_path.write_text(_jsonl(_capture()), encoding="utf-8")
    convert_file(input_path.resolve(), output_a)
    convert_file(input_path.resolve(), output_b)
    assert output_a.read_bytes() == output_b.read_bytes()


def test_gate_refuses_to_overwrite_existing_output(tmp_path: Path) -> None:
    input_path = tmp_path / "capture.jsonl"
    output_path = tmp_path / "fixture.json"
    sentinel = b"existing output sentinel"
    input_path.write_text(_jsonl(_capture()), encoding="utf-8")
    output_path.write_bytes(sentinel)
    with pytest.raises(OhFixtureGateError, match="output already exists"):
        convert_file(input_path.resolve(), output_path)
    assert output_path.read_bytes() == sentinel


def test_gate_rejects_relative_input_and_malformed_json_without_output(tmp_path: Path) -> None:
    output_path = tmp_path / "fixture.json"
    with pytest.raises(OhFixtureGateError, match="absolute"):
        convert_file(Path("relative.jsonl"), output_path)
    input_path = tmp_path / "bad.jsonl"
    input_path.write_text("{bad\n", encoding="utf-8")
    with pytest.raises(OhFixtureGateError, match="malformed"):
        convert_file(input_path.resolve(), output_path)
    assert not output_path.exists()


@pytest.mark.parametrize("mutation", ["wrong_count", "event", "third_message", "wrong_order"])
def test_gate_rejects_wrong_event_topology(mutation: str) -> None:
    records = _capture()
    if mutation == "wrong_count":
        records.pop()
    elif mutation == "event":
        records[2]["gateway_event_type"] = "INTERACTION_CREATE"
    elif mutation == "third_message":
        records[3]["message_id"] = records[3]["message"]["id"] = "raw-third-message-id"
    else:
        records[2], records[3] = records[3], records[2]
    with pytest.raises(OhFixtureGateError):
        _sanitize(records)


@pytest.mark.parametrize("field", ["guild_id", "channel_id", "author_id"])
def test_gate_rejects_scope_or_author_changes(field: str) -> None:
    records = _capture()
    records[0][field] = "raw-other-scope"
    if field == "author_id":
        records[0]["message"]["author_id"] = "raw-other-scope"
    with pytest.raises(OhFixtureGateError, match="scope|author|Mudae"):
        _sanitize(records)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "extra", "wrong_leaf_count"])
def test_gate_rejects_board_topology(mutation: str) -> None:
    records = _capture()
    row = records[0]["message"]["components"][0]
    if mutation == "missing":
        row["components"].pop()
    elif mutation == "duplicate":
        row["components"][1]["path"] = [0, 0]
    elif mutation == "extra":
        row["components"][1]["path"] = [0, 5]
    else:
        row["components"].append(copy.deepcopy(row["components"][0]))
    with pytest.raises(OhFixtureGateError, match="board|coordinate|leaf"):
        _sanitize(records)


def test_gate_rejects_component_identity_change() -> None:
    records = _capture()
    records[3]["message"]["components"][2]["components"][2]["custom_id_sha256"] = _hex(999)
    with pytest.raises(OhFixtureGateError, match="identity"):
        _sanitize(records)


@pytest.mark.parametrize("mutation", ["missing", "malformed"])
def test_gate_rejects_missing_or_malformed_emoji(mutation: str) -> None:
    records = _capture()
    leaf = records[0]["message"]["components"][0]["components"][0]
    if mutation == "missing":
        leaf.pop("emoji")
    else:
        leaf["emoji"]["id_sha256"] = "not-a-digest"
    with pytest.raises(OhFixtureGateError, match="emoji"):
        _sanitize(records)


@pytest.mark.parametrize("transition", ["enabled_reveal", "revealed_claim", "hidden_claim"])
def test_gate_rejects_capture_missing_each_required_transition_class(transition: str) -> None:
    records = _capture()
    boards = _board_records(records)
    if transition == "enabled_reveal":
        first = _leaves(boards[0])
        for board in boards[1:]:
            for leaf, initial in zip(_leaves(board), first, strict=True):
                leaf["emoji"] = copy.deepcopy(initial["emoji"])
    elif transition == "revealed_claim":
        for before, after in zip(boards, boards[1:]):
            for before_leaf, after_leaf in zip(_leaves(before), _leaves(after), strict=True):
                if before_leaf.get("disabled", False) is False and after_leaf.get("disabled", False) is True:
                    after_leaf["emoji"] = _emoji(f"claim-{after_leaf['path']}-{after['message']['id']}")
    else:
        for before, after in zip(boards, boards[1:]):
            for before_leaf, after_leaf in zip(_leaves(before), _leaves(after), strict=True):
                if before_leaf.get("disabled", False) is False and after_leaf.get("disabled", False) is True:
                    after_leaf["emoji"] = copy.deepcopy(before_leaf["emoji"])
    with pytest.raises(OhFixtureGateError, match="transition|reveal|claimed|disabled"):
        _sanitize(records)


def test_gate_rejects_nonterminal_board() -> None:
    records = _capture()
    final_leaf = _leaves(_board_records(records)[-1])[0]
    final_leaf.pop("disabled", None)
    with pytest.raises(OhFixtureGateError, match="final|disabled"):
        _sanitize(records)


def test_gate_rejects_unexpected_schema_and_does_not_write(tmp_path: Path) -> None:
    records = _capture()
    records[0]["unexpected"] = True
    input_path = tmp_path / "capture.jsonl"
    output_path = tmp_path / "fixture.json"
    input_path.write_text(_jsonl(records), encoding="utf-8")
    with pytest.raises(OhFixtureGateError, match="unapproved"):
        convert_file(input_path.resolve(), output_path)
    assert not output_path.exists()


def test_cli_returns_nonzero_without_echoing_source_values(tmp_path: Path) -> None:
    input_path = tmp_path / "capture.jsonl"
    output_path = tmp_path / "fixture.json"
    input_path.write_text("{bad\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "oh_capture_fixture_gate.py"), "--input", str(input_path.resolve()), "--output", str(output_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert not output_path.exists()
    assert "raw-" not in result.stdout + result.stderr
