"""Admission and replay checks for the sanitized Discord payload corpus."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from moa.core.config import ConfigService
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.services.antidisable_page_projection_coordinator import (
    AntidisablePageProjectionCoordinator,
)
from moa.services.automatic_import_service import AutomaticImportService
from moa.services.catalog_service import CatalogService
from moa.services.claim_projection_coordinator import ClaimProjectionCoordinator
from moa.services.disablelist_projection_coordinator import DisableListProjectionCoordinator
from moa.services.discord_listener_service import DiscordListenerService
from moa.services.kakera_state_projection_coordinator import KakeraStateProjectionCoordinator
from moa.services.player_bonus_projection_coordinator import PlayerBonusProjectionCoordinator
from moa.services.roll_projection_coordinator import RollProjectionCoordinator
from moa.services.sphere_result_projection_coordinator import SphereResultProjectionCoordinator
from moa.services.wishlist_projection_coordinator import WishlistProjectionCoordinator


CORPUS_PATH = Path(__file__).parent / "fixtures" / "discord" / "captured_payload_corpus.v1.json"
STRUCTURAL_FIXTURE_DIR = CORPUS_PATH.parent


def _corpus() -> dict:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def _scenario(corpus: dict, scenario_id: str) -> dict:
    return next(scenario for scenario in corpus["scenarios"] if scenario["id"] == scenario_id)


def _message(event: dict, *, user_ids: dict[str, int], mudae_id: int = 999) -> SimpleNamespace:
    raw = event["message"]
    author = raw["author"]
    author_id = mudae_id if author == "mudae" else user_ids[author]
    return SimpleNamespace(
        id=raw["id"],
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
        author=SimpleNamespace(bot=author == "mudae", id=author_id),
        content=raw.get("content", ""),
        embeds=(),
    )


def _listener(tmp_path, *, two_users: bool = False) -> tuple[DiscordListenerService, CatalogService]:
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "Test Server",
        "user_a",
        discord_server_id="123",
        discord_user_id="456",
    )
    if two_users:
        config.add_account(
            "Test Server",
            "user_b",
            role="alt",
            discord_server_id="123",
            discord_user_id="789",
        )
    database_path = tmp_path / "catalog.db"
    catalog_repository = CatalogRepository(database_path)
    catalog = CatalogService(catalog_repository)
    discord_repository = DiscordMessageRepository(database_path)
    importer = AutomaticImportService(
        catalog,
        roll_projection_coordinator=RollProjectionCoordinator(
            catalog_repository,
            discord_repository,
        ),
        claim_projection_coordinator=ClaimProjectionCoordinator(
            catalog_repository,
            discord_repository,
        ),
        sphere_result_projection_coordinator=SphereResultProjectionCoordinator(
            catalog_repository,
            discord_repository,
        ),
        player_bonus_projection_coordinator=PlayerBonusProjectionCoordinator(
            catalog_repository,
            discord_repository,
        ),
        wishlist_projection_coordinator=WishlistProjectionCoordinator(
            catalog_repository,
            discord_repository,
        ),
        disablelist_projection_coordinator=DisableListProjectionCoordinator(
            catalog_repository,
            discord_repository,
        ),
        antidisable_page_projection_coordinator=AntidisablePageProjectionCoordinator(
            catalog_repository,
            discord_repository,
        ),
        kakeraloot_state_projection_coordinator=KakeraStateProjectionCoordinator(
            catalog_repository,
            discord_repository,
        ),
    )
    return (
        DiscordListenerService(
            config_service=config,
            catalog_service=catalog,
            importer=importer,
            discord_message_repository=discord_repository,
        ),
        catalog,
    )


def test_corpus_manifest_is_deterministic_and_has_unique_scenario_ids() -> None:
    corpus = _corpus()
    scenario_ids = [scenario["id"] for scenario in corpus["scenarios"]]

    assert corpus["fixture_schema_version"] == 1
    assert len(scenario_ids) == len(set(scenario_ids))
    assert all(scenario["events"] for scenario in corpus["scenarios"])
    assert corpus == json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def test_corpus_has_transport_event_and_variant_coverage() -> None:
    corpus = _corpus()
    event_types = {
        event["event_type"]
        for scenario in corpus["scenarios"]
        for event in scenario["events"]
    }
    variants = {scenario["variant"] for scenario in corpus["scenarios"]}

    assert {"MESSAGE_CREATE", "MESSAGE_UPDATE", "INTERACTION_CREATE", "REACTION_ADD"} <= event_types
    assert {
        "prefix",
        "slash",
        "edit",
        "reaction_confirmation",
        "confirmation",
        "pagination",
        "simultaneous_users",
    } <= variants
    assert all(
        scenario["evidence_status"] in {
            "GROUNDED_EXISTING_CAPTURE",
            "GROUNDED_DETERMINISTIC_TRANSPORT_VARIANT",
        }
        for scenario in corpus["scenarios"]
    )


def test_corpus_contains_no_raw_identifying_values() -> None:
    serialized = CORPUS_PATH.read_text(encoding="utf-8")

    for forbidden in (
        '"123"',
        '"456"',
        '"789"',
        '"999"',
        "ernieuuu",
        "Lake Arrowhead",
        "private@example",
        "discord.gg",
        "Bearer",
        "interaction-secret",
    ):
        assert forbidden not in serialized

    corpus = _corpus()
    assert corpus["provenance"]["contains_raw_discord_ids"] is False
    assert corpus["provenance"]["contains_sensitive_text"] is False


def test_linked_structural_fixtures_are_existing_sanitized_capture_outputs() -> None:
    corpus = _corpus()
    linked = corpus["linked_structural_fixtures"]

    assert {entry["fixture"] for entry in linked} == {
        "adl_structural_capture.v1.json",
        "oh_structural_capture.v1.json",
        "oc_structural_capture.v1.json",
    }
    for entry in linked:
        fixture = json.loads(
            (STRUCTURAL_FIXTURE_DIR / entry["fixture"]).read_text(encoding="utf-8")
        )
        assert fixture["fixture_schema_version"] == 1
        assert fixture["provenance"]["contains_raw_discord_ids"] is False
        assert fixture["provenance"].get("contains_message_embed_or_component_text", False) is False
        assert fixture["records"]


def test_prefix_fixture_replays_through_listener_context(tmp_path) -> None:
    listener, _catalog = _listener(tmp_path)
    scenario = _scenario(_corpus(), "prefix_message_create_response")
    request = _message(scenario["events"][0], user_ids={"user_a": 456})

    asyncio.run(listener.handle_message(request))

    assert listener._command_contexts[request.id].identity.account == "user_a"
    assert listener._command_contexts[request.id].expected_kind == "roll"


def test_slash_fixture_replays_through_listener_interaction_context(tmp_path) -> None:
    listener, _catalog = _listener(tmp_path)
    interaction = _scenario(_corpus(), "slash_interaction_create")["events"][0]["interaction"]
    replayed = SimpleNamespace(
        guild_id=123,
        channel_id=789,
        user=SimpleNamespace(id=456),
        command=SimpleNamespace(name=interaction["command_name"]),
        data=interaction["data"],
    )

    asyncio.run(listener.handle_interaction(replayed))

    assert listener._contexts[789].identity.account == "user_a"
    assert listener._contexts[789].expected_kind == "roll"


def test_message_update_fixture_preserves_partial_update_replay_seam(tmp_path) -> None:
    listener, _catalog = _listener(tmp_path)
    event = _scenario(_corpus(), "message_update_partial_edit")["events"][0]
    cached = _message(
        {
            "message": {
                "id": event["message"]["id"],
                "author": "mudae",
                "content": "",
            }
        },
        user_ids={"user_a": 456},
    )
    listener._message_cache[cached.id] = cached
    listener.handle_bot_response = AsyncMock()

    asyncio.run(
        listener.handle_raw_message_edit(
            SimpleNamespace(channel_id=789, message_id=cached.id)
        )
    )

    listener.handle_bot_response.assert_called_once_with(
        cached,
        process_ourochest_components=False,
    )


def test_reaction_fixture_replays_mudae_confirmation_ack(tmp_path) -> None:
    listener, catalog = _listener(tmp_path)
    listener._mudae_user_id = 999
    event = _scenario(_corpus(), "reaction_add_confirmation_ack")["events"]
    request = _message(event[0], user_ids={"user_a": 456})
    reaction = event[1]["reaction"]
    payload = SimpleNamespace(
        guild_id=123,
        user_id=999,
        channel_id=789,
        message_id=request.id,
        emoji=SimpleNamespace(name=reaction["emoji"]),
    )

    asyncio.run(listener.handle_message(request))
    asyncio.run(listener.handle_raw_reaction_add(payload))

    state = catalog.personal_rare("Test Server", "user_a")
    assert state is not None
    assert state.personal_rare_multiplier == 2


def test_confirmation_fixture_replays_negative_confirmation(tmp_path) -> None:
    listener, _catalog = _listener(tmp_path)
    events = _scenario(_corpus(), "divorce_confirmation_messages")["events"]

    for event in events:
        message = _message(event, user_ids={"user_a": 456})
        asyncio.run(
            listener.handle_bot_response(message)
            if message.author.bot
            else listener.handle_message(message)
        )

    assert 789 not in listener._contexts


def test_pagination_fixture_replays_ordered_pages(tmp_path) -> None:
    listener, catalog = _listener(tmp_path)
    events = _scenario(_corpus(), "harem_pagination_sequence")["events"]
    request = _message(events[0], user_ids={"user_a": 456})
    asyncio.run(listener.handle_message(request))

    asyncio.run(listener.handle_bot_response(_message(events[1], user_ids={"user_a": 456})))
    scan_id = next(iter(listener._scan_ids.values()))
    for event in events[2:]:
        asyncio.run(listener.handle_bot_response(_message(event, user_ids={"user_a": 456})))

    progress = catalog.harem_scan_progress(scan_id)
    assert progress is not None
    assert progress.imported_pages == (1, 2, 3)
    assert progress.completed_at is not None


def test_simultaneous_user_fixture_preserves_independent_attribution(tmp_path) -> None:
    listener, catalog = _listener(tmp_path, two_users=True)
    events = _scenario(_corpus(), "simultaneous_configured_users")["events"]
    user_ids = {"user_a": 456, "user_b": 789}

    asyncio.run(listener.handle_message(_message(events[0], user_ids=user_ids)))
    asyncio.run(listener.handle_message(_message(events[1], user_ids=user_ids)))
    asyncio.run(listener.handle_bot_response(_message(events[2], user_ids=user_ids)))

    assert listener._command_contexts["simultaneous_request_a"].identity.account == "user_a"
    assert listener._command_contexts["simultaneous_request_b"].identity.account == "user_b"
    assert len(catalog.recent_rolls("Test Server", "user_a", 1)) == 1
    assert catalog.recent_rolls("Test Server", "user_b", 1) == ()
