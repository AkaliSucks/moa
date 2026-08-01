from __future__ import annotations

import json
from pathlib import Path
from typing import Any


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "discord" / "adl_structural_capture.v1.json"


def _fixture() -> dict[str, Any]:
    with FIXTURE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def test_fixture_provenance_preserves_structural_only_limitations() -> None:
    fixture = _fixture()
    provenance = fixture["provenance"]

    assert fixture["fixture_schema_version"] == 1
    assert fixture["source_schema_version"] == "moa.discord-event-capture.v1"
    assert provenance == {
        "contains_message_embed_or_component_text": False,
        "contains_raw_discord_ids": False,
        "limitations": [
            "no_response_to_user_association",
            "not_durable_workflow_evidence",
        ],
        "sanitization": "deterministic_purpose_specific_gate",
        "source_kind": "authorized_structural_only_diagnostic_capture",
    }
    assert len(fixture["records"]) == 6


def test_fixture_has_the_observed_gateway_event_sequence() -> None:
    records = _fixture()["records"]

    assert [record["sequence"] for record in records] == [1, 2, 3, 4, 5, 6]
    assert [record["event"] for record in records] == [
        "MESSAGE_CREATE",
        "MESSAGE_CREATE",
        "MESSAGE_UPDATE",
        "MESSAGE_UPDATE",
        "MESSAGE_CREATE",
        "MESSAGE_CREATE",
    ]
    assert sum(record["event"] == "MESSAGE_CREATE" for record in records) == 4
    assert sum(record["event"] == "MESSAGE_UPDATE" for record in records) == 2
    assert all(record["event"] != "INTERACTION_CREATE" for record in records)
    assert [record["message"]["edited_at"] is not None for record in records] == [
        False,
        False,
        True,
        True,
        False,
        False,
    ]


def test_scenario_a_retains_one_stable_response_message_aggregate() -> None:
    records = _fixture()["records"]
    request, initial, update_one, update_two = records[:4]

    assert request["message"]["alias"] == "request_message_1"
    assert request["message"]["author"] == "user_1"
    assert initial["message"]["alias"] == "response_message_1"
    assert [record["event"] for record in records[2:4]] == [
        "MESSAGE_UPDATE",
        "MESSAGE_UPDATE",
    ]
    assert [record["message"]["alias"] for record in records[1:4]] == [
        "response_message_1",
        "response_message_1",
        "response_message_1",
    ]
    assert all(record["message"]["author"] == "mudae" for record in records[1:4])
    assert initial["message"]["edited_at"] is None
    assert update_one["message"]["edited_at"] is not None
    assert update_two["message"]["edited_at"] is not None
    assert initial["message"]["created_at"] == update_one["message"]["created_at"] == update_two["message"]["created_at"]

    expected_components = [
        {"path": [0], "type": 1},
        {"path": [0, 0], "token": "component_token_1", "type": 2},
        {"path": [0, 1], "token": "component_token_2", "type": 2},
    ]
    assert [record["message"]["components"] for record in records[1:4]] == [
        expected_components,
        expected_components,
        expected_components,
    ]


def test_fixture_keeps_two_users_and_two_responses_distinct_without_attribution() -> None:
    records = _fixture()["records"]
    request_one, response_one, _, _, request_two, response_two = records

    assert request_one["message"]["alias"] != request_two["message"]["alias"]
    assert request_one["message"]["author"] != request_two["message"]["author"]
    assert response_one["message"]["alias"] != response_two["message"]["alias"]
    assert response_two["message"]["alias"] == "response_message_2"
    assert response_two["message"]["author"] == "mudae"

    message_keys = {
        "alias",
        "author",
        "components",
        "created_at",
        "edited_at",
        "embeds",
        "interaction_metadata",
        "reference",
        "type",
    }
    assert all(set(record["message"]) == message_keys for record in records)
    assert all(record["message"]["reference"] is None for record in records)
    assert all(record["message"]["interaction_metadata"] is None for record in records)
    assert response_one["message"]["alias"] not in {
        request_one["message"]["alias"],
        request_two["message"]["alias"],
    }
    assert response_two["message"]["alias"] not in {
        request_one["message"]["alias"],
        request_two["message"]["alias"],
    }
    assert all(set(record) == {"channel", "event", "guild", "message", "sequence"} for record in records)


def test_fixture_has_no_passive_response_to_user_correlation_evidence() -> None:
    records = _fixture()["records"]

    for record in records:
        message = record["message"]
        assert message["reference"] is None
        assert message["interaction_metadata"] is None
        assert set(message) == {
            "alias",
            "author",
            "components",
            "created_at",
            "edited_at",
            "embeds",
            "interaction_metadata",
            "reference",
            "type",
        }


def test_fixture_characterizes_only_the_observed_component_and_embed_shapes() -> None:
    records = _fixture()["records"]

    for record in (records[0], records[4]):
        assert record["message"]["components"] == []
        assert record["message"]["embeds"] == []

    for record in records[1:4]:
        assert len(record["message"]["components"]) == 3
        assert record["message"]["components"][0] == {"path": [0], "type": 1}
        assert [component["path"] for component in record["message"]["components"][1:]] == [[0, 0], [0, 1]]
        assert [component["token"] for component in record["message"]["components"][1:]] == [
            "component_token_1",
            "component_token_2",
        ]
        assert record["message"]["embeds"] == [{"has_footer": True, "type": "rich"}]

    assert records[5]["message"]["components"] == []
    assert records[5]["message"]["embeds"] == [{"has_footer": False, "type": "rich"}]
