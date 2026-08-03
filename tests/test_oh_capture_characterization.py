from __future__ import annotations

import json
from pathlib import Path
from typing import Any


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "discord" / "oh_structural_capture.v1.json"
ANNOTATION_PATH = Path(__file__).parent / "fixtures" / "discord" / "oh_characterization.v1.json"
EXPECTED_PATHS = {(row, column) for row in range(5) for column in range(5)}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture() -> dict[str, Any]:
    return _load(FIXTURE_PATH)


def _annotation() -> dict[str, Any]:
    return _load(ANNOTATION_PATH)


def _boards() -> list[dict[str, Any]]:
    return [record for record in _fixture()["records"] if record["message"]["alias"] == "board_message_1"]


def _leaves(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [leaf for row in record["message"]["components"] for leaf in row["components"]]


def test_fixture_structure_and_stable_component_identity() -> None:
    fixture = _fixture()
    records = fixture["records"]
    assert len(records) == 14
    assert {record["message"]["alias"] for record in records} == {"board_message_1", "reward_message_1"}
    boards = _boards()
    rewards = [record for record in records if record["message"]["alias"] == "reward_message_1"]
    assert len(boards) == len(rewards) == 7
    identities: dict[tuple[int, int], str] = {}
    for board in boards:
        leaves = _leaves(board)
        assert len(leaves) == 25
        assert {tuple(leaf["path"]) for leaf in leaves} == EXPECTED_PATHS
        for leaf in leaves:
            coordinate = tuple(leaf["path"])
            identities.setdefault(coordinate, leaf["custom_id_sha256"])
            assert identities[coordinate] == leaf["custom_id_sha256"]
            assert isinstance(leaf["disabled"], bool)
    assert all(leaf["disabled"] for leaf in _leaves(boards[-1]))


def test_generic_structural_transition_classes_use_opaque_hashes_only() -> None:
    transitions = {"emoji_change_enabled": 0, "same_emoji_claim": 0, "emoji_change_claim": 0}
    boards = _boards()
    for before, after in zip(boards, boards[1:]):
        for before_leaf, after_leaf in zip(_leaves(before), _leaves(after), strict=True):
            emoji_changed = before_leaf["emoji"] != after_leaf["emoji"]
            before_enabled = not before_leaf["disabled"]
            after_enabled = not after_leaf["disabled"]
            if emoji_changed and before_enabled and after_enabled:
                transitions["emoji_change_enabled"] += 1
            if not emoji_changed and before_enabled and not after_enabled:
                transitions["same_emoji_claim"] += 1
            if emoji_changed and before_enabled and not after_enabled:
                transitions["emoji_change_claim"] += 1
    assert all(transitions.values())


def test_same_visual_emoji_identity_occurs_at_multiple_coordinates() -> None:
    fingerprints = [
        (leaf["emoji"]["kind"], leaf["emoji"]["id_sha256"], leaf["emoji"]["name_sha256"])
        for leaf in _leaves(_boards()[0])
    ]
    assert len(set(fingerprints)) < len(fingerprints)


def test_annotation_records_exact_operator_action_sequence() -> None:
    annotation = _annotation()
    observed = annotation["operator_observed_facts"]
    assert observed["initial_state"]["revealed_unclaimed"] == [{"coordinate": "R2C3", "emoji": "Purple"}]
    assert observed["initial_state"]["all_other_cells"] == "hidden"
    assert observed["initial_state"]["invested_spheres_reward"] == 60
    assert observed["initial_state"]["ordinary_click_allowance"] == 5
    assert [(action["coordinate"], action["emoji"]) for action in observed["actions"]] == [
        ("R2C3", "Purple"),
        ("R1C1", "Blue"),
        ("R1C5", "Blue"),
        ("R3C3", "Light"),
        ("R2C1", "Yellow"),
        ("R1C2", "Blue"),
    ]


def test_annotation_preserves_corrected_first_blue_reveals() -> None:
    action = _annotation()["operator_observed_facts"]["actions"][1]
    assert action["reveals"] == [
        {"coordinate": "R1C5", "emoji": "Blue"},
        {"coordinate": "R2C1", "emoji": "Yellow"},
        {"coordinate": "R5C5", "emoji": "Teal"},
    ]
    assert "R1C2" not in [entry["coordinate"] for entry in action["reveals"]]
    assert action["still_hidden_after_action"] == ["R1C2"]


def test_annotation_records_second_blue_reveals() -> None:
    action = _annotation()["operator_observed_facts"]["actions"][2]
    assert action["reveals"] == [
        {"coordinate": "R1C3", "emoji": "Teal"},
        {"coordinate": "R3C3", "emoji": "Light"},
        {"coordinate": "R5C3", "emoji": "Teal"},
    ]
    assert action["reveal_targets_remained_unclaimed"] is True


def test_annotation_records_capture_specific_light_evidence() -> None:
    action = _annotation()["operator_observed_facts"]["actions"][3]
    assert action["observed_decomposition"] == ["Blue", "Teal", "Blue", "Teal", "Teal"]
    assert action["total_reward"] == 80
    assert action["decomposition_scope"] == "observed for capture #3 only"
    assert action["universal_light_component_rule"] is False


def test_annotation_distinguishes_terminal_structure_from_final_click_provenance() -> None:
    annotation = _annotation()
    assert annotation["structural_facts"]["terminal_board_all_disabled"] is True
    provenance = annotation["operator_observed_facts"]["final_click_provenance"]
    assert provenance == {
        "coordinate": "R1C2",
        "status": "operator_observed",
        "structurally_inferred": False,
        "structural_fact": "final board snapshot is all disabled",
    }


def test_annotation_records_observed_rewards_and_final_stock() -> None:
    rewards = _annotation()["operator_observed_facts"]["reward_observations"]
    assert rewards["separate_invested_spheres_reward"] == 60
    assert [(entry["emoji"], entry["reward"]) for entry in rewards["action_rewards"]] == [
        ("Purple", 5),
        ("Blue", 10),
        ("Blue", 10),
        ("Light", 80),
        ("Yellow", 55),
        ("Blue", 10),
    ]
    assert rewards["action_rewards"][0]["free_claim_behavior_observed"] is True
    assert rewards["final_stock"] == 1614
