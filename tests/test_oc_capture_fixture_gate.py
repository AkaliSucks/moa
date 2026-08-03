from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from oc_capture_fixture_gate import OcFixtureGateError, convert_file, sanitize_jsonl


SOURCE_SCHEMA_VERSION = "moa.discord-event-capture.v1"
RAW_GUILD = "raw-guild-distinctive"
RAW_CHANNEL = "raw-channel-distinctive"
RAW_AUTHOR = "raw-author-distinctive"
RAW_TIMESTAMP = "2026-08-03T10:00:00.000Z"
ROLE_IDS = {"tu": "raw-tu-message", "board": "raw-board-message", "reward": "raw-reward-message"}
TOPOLOGY = (
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


def _hex(number: int) -> str:
    return f"{number:064x}"


def _emoji(number: int) -> dict[str, Any]:
    return {
        "kind": "custom",
        "id_sha256": _hex(number + 1000),
        "name_sha256": _hex(number + 2000),
        "name_length": 3,
    }


def _board_snapshot(step: int, *, terminal: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    action_coordinates = [(3, 3), (1, 1), (1, 0), (4, 1), (4, 0)]
    for row in range(5):
        leaves: list[dict[str, Any]] = []
        for column in range(5):
            coordinate = (row, column)
            revealed = coordinate in action_coordinates[:step]
            if terminal:
                visual_number = 100 + row * 5 + column
            elif revealed:
                visual_number = 10 + action_coordinates.index(coordinate)
            else:
                visual_number = 1
            leaves.append(
                {
                    "custom_id_length": 18,
                    "custom_id_sha256": _hex(3000 + row * 5 + column),
                    "emoji": _emoji(visual_number),
                    **({"disabled": True} if revealed or terminal else {}),
                    "path": [row, column],
                    "type": 2,
                }
            )
        rows.append({"components": leaves, "path": [row], "type": 1})
    return rows


def _record(sequence: int, event: str, role: str, message: dict[str, Any]) -> dict[str, Any]:
    timestamp = f"2026-08-03T10:{sequence:02d}:00.000Z"
    result = {
        "author_id": RAW_AUTHOR,
        "capture_schema_version": SOURCE_SCHEMA_VERSION,
        "captured_at": timestamp,
        "channel_id": RAW_CHANNEL,
        "gateway_event_type": event,
        "guild_id": RAW_GUILD,
        "message": message,
        "message_id": ROLE_IDS[role],
        "sequence": sequence,
    }
    result["message"]["created_at"] = RAW_TIMESTAMP
    if event == "MESSAGE_UPDATE":
        result["message"]["edited_at"] = timestamp
    return result


def _capture_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    board_index = 0
    reward_updates = 0
    for sequence, (event, role) in enumerate(TOPOLOGY, start=1):
        if role == "board":
            message = {
                "author_id": RAW_AUTHOR,
                "components": _board_snapshot(board_index, terminal=board_index == 5),
                "content": "raw captured board text with <custom:emoji_markup>",
                "embeds": [],
                "id": ROLE_IDS[role],
                "type": 0,
            }
            board_index += 1
        else:
            reward_updates += int(event == "MESSAGE_UPDATE")
            message = {
                "author_id": RAW_AUTHOR,
                "components": [],
                "content": "raw timer or reward content <custom:emoji_markup>",
                "embeds": [],
                "id": ROLE_IDS[role],
                "type": 0,
            }
        records.append(_record(sequence, event, role, message))
    assert board_index == 6
    assert reward_updates == 5
    return records


def _source_text(records: list[dict[str, Any]] | None = None) -> str:
    return "".join(json.dumps(record, sort_keys=True) + "\n" for record in records or _capture_records())


def _admit(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return sanitize_jsonl(_source_text(records))


def test_valid_topology_admits_six_board_snapshots_and_drops_other_roles() -> None:
    artifact = _admit()

    assert len(artifact["records"]) == 6
    assert [record["sequence"] for record in artifact["records"]] == [2, 4, 7, 8, 10, 12]
    assert all(record["message"]["alias"] == "board_message_1" for record in artifact["records"])
    assert all(len(row["components"]) == 5 for record in artifact["records"] for row in record["message"]["components"])
    assert all(
        isinstance(leaf["disabled"], bool)
        for record in artifact["records"]
        for row in record["message"]["components"]
        for leaf in row["components"]
    )
    serialized = json.dumps(artifact, sort_keys=True)
    for forbidden in (RAW_GUILD, RAW_CHANNEL, RAW_AUTHOR, *ROLE_IDS.values(), RAW_TIMESTAMP, "raw captured", "custom:emoji_markup"):
        assert forbidden not in serialized


def test_wrong_role_event_order_is_rejected_with_same_aggregate_counts() -> None:
    records = _capture_records()
    records[2]["gateway_event_type"] = "MESSAGE_UPDATE"
    records[4]["gateway_event_type"] = "MESSAGE_CREATE"
    with pytest.raises(OcFixtureGateError, match="reordered Gateway event"):
        _admit(records)


@pytest.mark.parametrize("mutation", ["wrong_count", "unexpected_event", "missing_message", "fourth_identity"])
def test_record_and_identity_counts_are_fail_closed(mutation: str) -> None:
    records = _capture_records()
    if mutation == "wrong_count":
        records.pop()
    elif mutation == "unexpected_event":
        records[3]["gateway_event_type"] = "INTERACTION_CREATE"
    elif mutation == "missing_message":
        records[1].pop("message")
    else:
        records[12]["message_id"] = "raw-fourth-message"
        records[12]["message"]["id"] = "raw-fourth-message"
    with pytest.raises(OcFixtureGateError):
        _admit(records)


@pytest.mark.parametrize("mutation", ["tu_components", "board_missing_components", "reward_components", "board_identity"])
def test_message_roles_and_stable_identities_are_fail_closed(mutation: str) -> None:
    records = _capture_records()
    if mutation == "tu_components":
        records[0]["message"]["components"] = _board_snapshot(0)
    elif mutation == "board_missing_components":
        records[1]["message"]["components"] = []
    elif mutation == "reward_components":
        records[2]["message"]["components"] = _board_snapshot(0)
    else:
        records[6]["message_id"] = ROLE_IDS["reward"]
        records[6]["message"]["id"] = ROLE_IDS["reward"]
    with pytest.raises(OcFixtureGateError):
        _admit(records)


@pytest.mark.parametrize("mutation", ["24_cells", "missing_coordinate", "duplicate_coordinate", "extra_coordinate"])
def test_board_topology_is_fail_closed(mutation: str) -> None:
    records = _capture_records()
    board = records[1]["message"]["components"]
    if mutation == "24_cells":
        board[4]["components"].pop()
    elif mutation == "missing_coordinate":
        board[0]["components"][0].pop("path")
    elif mutation == "duplicate_coordinate":
        board[0]["components"][1]["path"] = [0, 0]
    else:
        board[0]["components"][0]["path"] = [5, 0]
    with pytest.raises(OcFixtureGateError):
        _admit(records)


def test_component_stability_and_emoji_structure_are_fail_closed() -> None:
    records = _capture_records()
    records[3]["message"]["components"][0]["components"][0]["custom_id_sha256"] = _hex(9999)
    with pytest.raises(OcFixtureGateError, match="component identity"):
        _admit(records)

    records = _capture_records()
    records[1]["message"]["components"][0]["components"][0]["emoji"].pop("name_sha256")
    with pytest.raises(OcFixtureGateError, match="emoji"):
        _admit(records)


def test_disabled_normalization_preserves_omitted_false_and_true() -> None:
    artifact = _admit()
    initial_cell = artifact["records"][0]["message"]["components"][0]["components"][0]
    clicked_cell = artifact["records"][1]["message"]["components"][3]["components"][3]

    assert initial_cell["disabled"] is False
    assert clicked_cell["disabled"] is True


def test_intermediate_action_chronology_is_fail_closed() -> None:
    records = _capture_records()
    action_leaf = records[6]["message"]["components"][1]["components"][1]
    action_leaf["disabled"] = False
    with pytest.raises(OcFixtureGateError, match="intermediate board transition"):
        _admit(records)


def test_terminal_state_requires_all_cells_disabled() -> None:
    records = _capture_records()
    records[11]["message"]["components"][0]["components"][0].pop("disabled", None)
    with pytest.raises(OcFixtureGateError, match="final board"):
        _admit(records)


@pytest.mark.parametrize("field", ["interaction_metadata", "attachments", "message_reference"])
def test_unexpected_meaningful_source_shapes_are_rejected(field: str) -> None:
    records = _capture_records()
    if field == "interaction_metadata":
        records[0][field] = {"raw": "meaningful"}
    else:
        records[1]["message"][field] = [{"raw": "meaningful"}]
    with pytest.raises(OcFixtureGateError):
        _admit(records)


def test_determinism_no_clobber_and_no_write_on_failure(tmp_path: Path) -> None:
    source = tmp_path / "capture.jsonl"
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    source.write_text(_source_text(), encoding="utf-8")

    convert_file(source, first)
    convert_file(source, second)
    assert first.read_bytes() == second.read_bytes()
    before = first.read_bytes()
    with pytest.raises(OcFixtureGateError, match="already exists"):
        convert_file(source, first)
    assert first.read_bytes() == before

    failed = tmp_path / "failed.json"
    bad_records = _capture_records()
    bad_records[1]["message"]["components"][0]["components"].pop()
    bad_source = tmp_path / "bad.jsonl"
    bad_source.write_text(_source_text(bad_records), encoding="utf-8")
    with pytest.raises(OcFixtureGateError):
        convert_file(bad_source, failed)
    assert not failed.exists()


def test_input_path_must_be_absolute_and_outside_repository(tmp_path: Path) -> None:
    source = tmp_path / "capture.jsonl"
    source.write_text(_source_text(), encoding="utf-8")
    with pytest.raises(OcFixtureGateError, match="absolute"):
        convert_file(Path("relative.jsonl"), tmp_path / "output.json")
