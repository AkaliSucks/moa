"""Privacy and correlation contracts for opt-in settings/kt Gateway capture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from moa.cli import discord_commands as discord_commands_module
from moa.cli import main
from moa.services.discord_listener_service import (
    DiscordEventCaptureConfig,
    DiscordEventCaptureService,
)


SETTINGS = (
    "Server Settings\n(Server not premium)\n"
    "· Prefix: $ ($prefix)\n· Lang: en ($lang)\n"
    "· Claim reset: every 180 min. ($setclaim)\n"
    "· Exact minute of the reset: xx:14 ($setinterval)\n"
    "· Reset shifted: by +0 min. ($shifthour)\n"
    "· Rolls per hour: 10 ($setrolls)\n"
    "· Time before the claim reaction expires: 45 sec. ($settimer)\n"
    "· Spawn rarity multiplier for already claimed characters: 4 ($setrare)\n"
    "· % kakera bonus: +0 ($setkakerabonus)\n"
    "· % sphere bonus: +0 ($setspherebonus)\n"
    "· Game mode: 1 ($gamemode)\n"
    "· This channel instance: 1 ($channelinstance)"
)
KT = (
    "Your current level is:tow2: (+ 1 tower)\n"
    "The next level costs 75,000:kakera:\n"
    "You have 7,673:kakera:\n"
    "☑️ [5] Unveil 1 random button for the $oh command"
)


def _capture(tmp_path: Path, family: str | None) -> tuple[DiscordEventCaptureService, Path]:
    path = tmp_path / "capture.jsonl"
    capture = DiscordEventCaptureService(
        DiscordEventCaptureConfig(
            output_path=path,
            guild_id="100",
            channel_id="200",
            mudae_user_id="300",
            user_ids=frozenset({"400", "401"}),
            enabled=True,
            diagnostic_family=family,
        )
    )
    capture._open_output()
    return capture, path


def _event(event: str, **data) -> dict:
    return {"t": event, "d": {"guild_id": "100", "channel_id": "200", **data}}


def _command(user: str, token: str, message_id: str) -> dict:
    return _event(
        "MESSAGE_CREATE",
        id=message_id,
        author={"id": user, "username": "private-profile"},
        content=f"{token} unrelated argument 987654321012345678",
    )


def _response(message_id: str, content: str, **extra) -> dict:
    return _event(
        "MESSAGE_CREATE",
        id=message_id,
        author={"id": "300", "username": "Mudae"},
        content=content,
        **extra,
    )


def _records(capture: DiscordEventCaptureService, path: Path) -> list[dict]:
    capture.close()
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _walk_scalar_values(value: Any):
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_scalar_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_scalar_values(child)
    else:
        yield value


def test_default_capture_policy_and_adl_schema_are_unchanged(tmp_path: Path) -> None:
    capture, path = _capture(tmp_path, None)
    assert not capture.capture_gateway_payload(_command("400", "$settings", "500"))
    assert not capture.capture_gateway_payload(_command("400", "$kt", "501"))
    assert capture.capture_gateway_payload(_command("400", "$adl", "502"))
    record = _records(capture, path)[0]
    assert record["capture_schema_version"] == "moa.discord-event-capture.v1"
    assert record["message_id"] == "502"
    assert "enabled_family" not in record
    assert "shape" not in record


@pytest.mark.parametrize(
    ("family", "allowed", "excluded"),
    (("settings", "$settings", "$kt"), ("kt", "$kt", "$settings")),
)
def test_family_opt_in_admits_only_selected_command(
    tmp_path: Path, family: str, allowed: str, excluded: str
) -> None:
    capture, path = _capture(tmp_path, family)
    assert capture.capture_gateway_payload(_command("400", allowed, "500"))
    assert not capture.capture_gateway_payload(_command("400", excluded, "501"))
    assert not capture.capture_gateway_payload(_command("400", "$bonus", "502"))
    assert not capture.capture_gateway_payload(
        _event("MESSAGE_CREATE", id="503", author={"id": "400"}, content="private message")
    )
    assert not capture.capture_gateway_payload(_command("999", allowed, "504"))
    assert not capture.capture_gateway_payload(
        _response("505", "Player Bonuses\nYou have 42:kakera:")
    )
    records = _records(capture, path)
    assert len(records) == 1
    assert records[0]["canonical_command"] == family
    assert records[0]["command_context_present"] is True


@pytest.mark.parametrize("family", ("settings", "kt"))
def test_matching_context_and_parser_evidence(tmp_path: Path, family: str) -> None:
    capture, path = _capture(tmp_path, family)
    token = "$settings" if family == "settings" else "$kt"
    body = SETTINGS if family == "settings" else KT
    assert capture.capture_gateway_payload(_command("400", token, "500"))
    assert capture.capture_gateway_payload(_response("600", body))
    command, response = _records(capture, path)
    assert command["command_alias"] == response["command_alias"]
    assert response["command_context_present"] is True
    assert response["context_canonical_family"] == family
    assert response["context_source"] == "text"
    assert response["expected_family"] == family
    assert response["server_attribution"] == "resolved"
    assert response["account_attribution"] == ("not-required" if family == "settings" else "unique")
    assert response["parser_result"] == "accepted"
    assert response["resolved_message_kind"] == (
        "settings" if family == "settings" else "towerstate"
    )
    assert "PROJECTION_NOT_RUN_CAPTURE_ONLY" in response["stages"]
    assert response["source_shape"] == "content"
    if family == "settings":
        assert response["router_matched"] is True
        assert all(response["shape"]["required_fields"].values())
    else:
        assert response["shape"]["town_structure"] is True
        assert response["shape"]["next_level_cost"] is True
        assert response["shape"]["kakera_balance"] is True


@pytest.mark.parametrize("family,body", (("settings", SETTINGS), ("kt", KT)))
def test_missing_context_is_explicit(tmp_path: Path, family: str, body: str) -> None:
    capture, path = _capture(tmp_path, family)
    assert capture.capture_gateway_payload(_response("600", body))
    response = _records(capture, path)[0]
    assert response["command_context_present"] is False
    assert response["context_canonical_family"] == "none"
    assert response["server_attribution"] == "absent"
    assert response["account_attribution"] == ("not-required" if family == "settings" else "absent")


@pytest.mark.parametrize(
    ("family", "token", "partial"),
    (
        ("settings", "$settings", "Claim reset: every 180 min."),
        ("kt", "$kt", "Your current level is:tow2:"),
    ),
)
def test_explicit_reply_retains_marker_absence_and_parser_rejection(
    tmp_path: Path, family: str, token: str, partial: str
) -> None:
    capture, path = _capture(tmp_path, family)
    assert capture.capture_gateway_payload(_command("400", token, "500"))
    assert capture.capture_gateway_payload(
        _response("600", partial, message_reference={"message_id": "500"})
    )
    response = _records(capture, path)[1]
    assert response["command_context_present"] is True
    assert response["router_matched"] is False
    assert response["parser_result"] == "rejected"
    assert response["resolved_message_kind"] == "none"
    if family == "settings":
        assert response["shape"]["server_settings_marker"] is False
        assert response["shape"]["setclaim_marker"] is False
    else:
        assert response["shape"]["town_structure"] is True
        assert response["shape"]["next_level_cost"] is False


def test_two_selected_users_stay_ambiguous(tmp_path: Path) -> None:
    capture, path = _capture(tmp_path, "kt")
    assert capture.capture_gateway_payload(_command("400", "$kt", "500"))
    assert capture.capture_gateway_payload(_command("401", "$kt", "501"))
    assert capture.capture_gateway_payload(_response("600", KT))
    first_command, second_command, response = _records(capture, path)
    assert first_command["actor_alias"] != second_command["actor_alias"]
    assert first_command["command_alias"] != second_command["command_alias"]
    assert response["command_context_present"] is False
    assert response["account_attribution"] == "ambiguous"
    assert response["server_attribution"] == "ambiguous"
    assert response["command_alias"] is None


def test_interaction_and_embeds_use_only_bounded_fields(tmp_path: Path) -> None:
    capture, path = _capture(tmp_path, "settings")
    interaction = _event(
        "INTERACTION_CREATE",
        id="700",
        type=2,
        member={"user": {"id": "400", "profile": "PRIVATE"}},
        data={
            "name": "settings",
            "resolved": {"secret": "PRIVATE"},
            "options": [{"value": "PRIVATE"}],
        },
    )
    assert capture.capture_gateway_payload(interaction)
    assert capture.capture_gateway_payload(
        _response(
            "600",
            "",
            embeds=[{"description": SETTINGS, "fields": [{"name": "token", "value": "PRIVATE"}]}],
        )
    )
    command, response = _records(capture, path)
    assert command["source_kind"] == "interaction"
    assert response["source_shape"] == "embed"
    assert response["context_source"] == "interaction"
    serialized = path.read_text(encoding="utf-8")
    scalar_values = {
        str(value) for record in (command, response) for value in _walk_scalar_values(record)
    }
    assert not {"700", "600", "400", "100", "200", "300"}.intersection(scalar_values)
    assert "PRIVATE" not in serialized
    assert not {"resolved", "options", "profile", "token"}.intersection(
        {key for record in (command, response) for key in record}
    )
    assert len(serialized) < 8192


def test_sanitized_record_reaches_writer_and_aliases_are_stable(
    tmp_path: Path, monkeypatch
) -> None:
    capture, path = _capture(tmp_path, "settings")
    original_write = capture._write

    def checked_write(record: dict) -> None:
        serialized = json.dumps(record)
        assert "987654321012345678" not in serialized
        assert "secret-value" not in serialized
        assert '"guild_id"' not in serialized
        original_write(record)

    monkeypatch.setattr(capture, "_write", checked_write)
    assert capture.capture_gateway_payload(_command("400", "$settings", "500"))
    assert capture.capture_gateway_payload(
        _response("600", SETTINGS, message_reference={"message_id": "500", "token": "secret-value"})
    )
    records = _records(capture, path)
    assert records[0]["guild_alias"] == records[1]["guild_alias"]
    assert records[0]["channel_alias"] == records[1]["channel_alias"]
    assert records[0]["command_alias"] == records[1]["command_alias"]


def test_response_shape_and_input_limits_are_explicit(tmp_path: Path) -> None:
    capture, path = _capture(tmp_path, "settings")
    assert capture.capture_gateway_payload(_command("400", "$settings", "500"))
    assert capture.capture_gateway_payload(
        _response(
            "600",
            "Server Settings\n$setclaim",
            message_reference={"message_id": "500"},
            embeds=[{"description": "x" * 40000}],
        )
    )
    response = _records(capture, path)[1]
    assert response["source_shape"] == "mixed"
    assert response["input_truncated"] is True
    assert response["parser_result"] == "not_evaluated_truncated"
    assert "PARSER_REJECTED" not in response["stages"]
    assert response["limits"] == {
        "maximum_records": 128,
        "maximum_evaluated_text_characters": 32768,
    }


def test_other_shape_requires_an_explicit_command_link(tmp_path: Path) -> None:
    capture, path = _capture(tmp_path, "kt")
    assert capture.capture_gateway_payload(_command("400", "$kt", "500"))
    assert capture.capture_gateway_payload(
        _response("600", "", message_reference={"message_id": "500"})
    )
    response = _records(capture, path)[1]
    assert response["source_shape"] == "other"
    assert response["parser_result"] == "rejected"


def test_diagnostic_output_has_a_hard_record_limit(tmp_path: Path) -> None:
    capture, path = _capture(tmp_path, "settings")
    admitted = [
        capture.capture_gateway_payload(_command("400", "$settings", str(1000 + index)))
        for index in range(130)
    ]
    assert admitted.count(True) == 128
    assert admitted[-2:] == [False, False]
    assert len(_records(capture, path)) == 128


def test_unrelated_real_sanitized_transport_fixture_is_not_admitted(tmp_path: Path) -> None:
    fixture = json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "discord"
            / "mudae_slash_response_message_create.json"
        ).read_text(encoding="utf-8")
    )
    assert fixture["provenance"]["evidence_classification"] == "REAL_OBSERVABLE_MESSAGE_TRANSPORT"
    capture, path = _capture(tmp_path, "settings")
    # The real sanitized transport record supplies metadata but no family text.
    record = fixture["records"][0]
    assert not capture.capture_gateway_payload(
        _response("600", "", interaction_metadata={"id": record["interaction_metadata"]["id"]})
    )
    assert _records(capture, path) == []


def test_invalid_modes_and_no_clobber(tmp_path: Path) -> None:
    config = dict(
        output_path=tmp_path / "capture.jsonl",
        guild_id="100",
        channel_id="200",
        mudae_user_id="300",
        user_ids=frozenset({"400"}),
        enabled=True,
    )
    with pytest.raises(ValueError, match="settings or kt"):
        DiscordEventCaptureService(DiscordEventCaptureConfig(**config, diagnostic_family="bonus"))
    with pytest.raises(ValueError, match="cannot include message text"):
        DiscordEventCaptureService(
            DiscordEventCaptureConfig(**config, diagnostic_family="kt", include_message_text=True)
        )
    config["output_path"].write_text("prior evidence", encoding="utf-8")
    with pytest.raises(ValueError, match="refusing to overwrite"):
        DiscordEventCaptureService(DiscordEventCaptureConfig(**config, diagnostic_family="kt"))
    assert config["output_path"].read_text(encoding="utf-8") == "prior evidence"


def _cli_args(path: Path) -> list[str]:
    return [
        "discord",
        "listen",
        "--token",
        "synthetic-test-token",
        "--capture-only",
        "--capture-discord-events",
        str(path),
        "--capture-guild-id",
        "100",
        "--capture-channel-id",
        "200",
        "--mudae-user-id",
        "300",
        "--capture-user-id",
        "400",
    ]


def test_cli_rejects_unsupported_family_and_requires_capture_only(tmp_path: Path) -> None:
    path = tmp_path / "capture.jsonl"
    result = CliRunner().invoke(main.app, [*_cli_args(path), "--capture-diagnostic", "bonus"])
    assert result.exit_code == 1
    assert "supports only settings or kt" in result.stdout
    assert not path.exists()
    result = CliRunner().invoke(
        main.app,
        ["discord", "listen", "--token", "synthetic-test-token", "--capture-diagnostic", "kt"],
    )
    assert result.exit_code == 1
    assert "require --capture-only" in result.stdout


def test_cli_diagnostic_has_no_catalog_or_projection_construction(
    monkeypatch, tmp_path: Path
) -> None:
    output = tmp_path / "capture.jsonl"
    database = tmp_path / "disposable.db"
    monkeypatch.setenv("MOA_DATABASE_PATH", str(database))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("capture-only must not construct normal import dependencies")

    for name in (
        "CatalogRepository",
        "DiscordMessageRepository",
        "AutomaticImportService",
        "DiscordListenerService",
    ):
        monkeypatch.setattr(discord_commands_module, name, forbidden)
    original_run = DiscordEventCaptureService.run

    def local_run(self, _token):
        self._open_output()
        assert self.capture_gateway_payload(_command("400", "$settings", "500"))
        assert self.capture_gateway_payload(_response("600", SETTINGS))
        self.close()

    monkeypatch.setattr(DiscordEventCaptureService, "run", local_run)
    try:
        result = CliRunner().invoke(
            main.app, [*_cli_args(output), "--capture-diagnostic", "settings"]
        )
    finally:
        monkeypatch.setattr(DiscordEventCaptureService, "run", original_run)
    assert result.exit_code == 0, result.stdout
    assert len(output.read_text(encoding="utf-8").splitlines()) == 2
    assert not database.exists()
