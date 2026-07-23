import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from moa.database.sqlite import connect
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.core.config import ConfigService
from moa.services.automatic_import_service import AutomaticImportService
from moa.services.catalog_service import CatalogService
from moa.services.claim_projection_coordinator import ClaimProjectionCoordinator
from moa.services.discord_listener_service import DiscordListenerService
from moa.services.infokl_projection_coordinator import InfoklProjectionCoordinator
from moa.services.profile_projection_coordinator import ProfileProjectionCoordinator
from moa.services.roll_projection_coordinator import RollProjectionCoordinator
from moa.services.settings_projection_coordinator import SettingsProjectionCoordinator


def test_extract_message_text_flattens_discord_embed_content() -> None:
    message = SimpleNamespace(
        content="",
        embeds=(
            SimpleNamespace(
                author=SimpleNamespace(name="ernieuuu's harem"),
                title="Mudae",
                description="#1 - Zero Two - DARLING in the FRANXX",
                fields=(SimpleNamespace(name="Page", value="1 / 67"),),
                footer=SimpleNamespace(text="Mudae"),
            ),
        ),
    )

    text = DiscordListenerService.extract_message_text(message)

    assert text == (
        "ernieuuu's harem\nMudae\n#1 - Zero Two - DARLING in the FRANXX\n"
        "Page\n1 / 67\nMudae"
    )


def test_listener_page_metadata_reads_supported_scan_pages(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )
    raw_message = "ernieuuu's harem\n#2 - Zero Two · ($wa)\nPage 1 / 38"

    assert listener._page_metadata("ranked_harem", raw_message) == (1, 38)


def test_listener_ignores_non_scan_page_metadata(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )

    assert listener._page_metadata("top", "#1 - Zero Two - DARLING in the FRANXX") == (None, None)


def test_listener_rejects_example_bot_token(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )

    with pytest.raises(ValueError, match="Replace YOUR_DISCORD_BOT_TOKEN"):
        listener.run("YOUR_DISCORD_BOT_TOKEN")


def test_listener_maps_owned_harem_command_to_ranked_harem() -> None:
    assert DiscordListenerService._expected_kind_for_command("$mmrkty+") == "ranked_harem"
    assert DiscordListenerService._expected_kind_for_command("$mmyk") == "harem"
    assert DiscordListenerService._expected_kind_for_command("$adl") == "antidisable"
    assert DiscordListenerService._expected_kind_for_command("$wa") == "roll"
    assert DiscordListenerService._expected_kind_for_command("$m") == "roll"
    assert DiscordListenerService._expected_kind_for_command("$k") == "kakera"
    assert DiscordListenerService._expected_kind_for_command("$divorce") == "divorce"
    assert DiscordListenerService._expected_kind_for_command("$dl") == "disablelist"
    assert DiscordListenerService._expected_kind_for_command("$settings") == "settings"
    assert DiscordListenerService._expected_kind_for_command("$bonus") == "bonus"
    assert DiscordListenerService._expected_kind_for_command("$rolls") == "timers"
    assert DiscordListenerService._expected_kind_for_command("$daily") == "timers"
    assert DiscordListenerService._expected_kind_for_command("$help") == "help"
    assert DiscordListenerService._expected_kind_for_command("$infopin") == "help"
    assert DiscordListenerService._expected_kind_for_command("$profile") == "profile"
    assert DiscordListenerService._expected_kind_for_command("$pr") == "profile"
    assert DiscordListenerService._expected_kind_for_command("$mp") == "mudapins"
    assert DiscordListenerService._expected_kind_for_command("$mu") == "timers"
    assert DiscordListenerService._expected_kind_for_command("$ru") == "timers"
    assert DiscordListenerService._expected_kind_for_command("$du") == "timers"
    assert DiscordListenerService._expected_kind_for_command("$ku") == "timers"
    assert DiscordListenerService._expected_kind_for_command("$dku") == "timers"
    assert DiscordListenerService._expected_kind_for_command("$bku") == "timers"
    assert DiscordListenerService._expected_kind_for_command("$rtu") == "timers"
    assert DiscordListenerService._expected_kind_for_command("$dk") == "timers"
    assert DiscordListenerService._expected_kind_for_command("$ohu") == "timers"
    assert DiscordListenerService._expected_kind_for_command("$timersup") == "timers"
    assert DiscordListenerService._expected_kind_for_command("$tuarrange") == "help"
    assert DiscordListenerService._expected_kind_for_command("$tuto") == "tutorial"
    assert DiscordListenerService._expected_kind_for_command("$tutorial") == "tutorial"
    assert DiscordListenerService._expected_kind_for_command("$oq") == "sphere_result"
    assert DiscordListenerService._expected_kind_for_command("$kt") == "towerstate"
    assert DiscordListenerService._expected_kind_for_command("$lk") == "lootstate"
    assert DiscordListenerService._expected_kind_for_command("$im") == "im"
    assert DiscordListenerService._expected_kind_for_command("$givek") == "gift_kakera"
    assert DiscordListenerService._expected_kind_for_command("$givesp") == "gift_spheres"
    assert DiscordListenerService._expected_kind_for_command("$give") == "gift_character"
    assert DiscordListenerService._expected_kind_for_command("$trade") == "trade"


def test_listener_ignores_unsupported_commands_without_creating_context(tmp_path) -> None:
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "LEAGUE OF DRAVEN",
        "lilchipmunk1",
        discord_server_id="123",
        discord_user_id="456",
    )
    listener = DiscordListenerService(
        config_service=config,
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db")),
    )
    message = SimpleNamespace(
        id=987,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
        author=SimpleNamespace(bot=False, id=456),
        content="$tuFUCKU",
    )

    asyncio.run(listener.handle_message(message))

    assert 789 not in listener._contexts


def _listener_with_two_configured_users(tmp_path):
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "Test Server",
        "user_a",
        discord_server_id="123",
        discord_user_id="456",
    )
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


def _import_event_rows(database_path, kind: str):
    with sqlite3.connect(database_path) as connection:
        return connection.execute(
            "SELECT source, raw_message FROM import_events WHERE kind = ? ORDER BY id",
            (kind,),
        ).fetchall()


def test_listener_tracks_interleaved_prefix_commands_from_two_users(tmp_path) -> None:
    listener, _catalog = _listener_with_two_configured_users(tmp_path)
    command_a = SimpleNamespace(
        id=100,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=False, id=456),
        content="$wa",
    )
    command_b = SimpleNamespace(
        id=101,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=False, id=789),
        content="$k",
    )

    asyncio.run(listener.handle_message(command_a))
    asyncio.run(listener.handle_message(command_b))

    assert listener._command_contexts[100].identity.account == "user_a"
    assert listener._command_contexts[101].identity.account == "user_b"
    assert listener._contexts[900].identity.account == "user_a"


def test_listener_does_not_replace_user_a_pending_workflow_with_user_b_command(tmp_path) -> None:
    listener, catalog = _listener_with_two_configured_users(tmp_path)
    command_a = SimpleNamespace(
        id=100,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=False, id=456),
        content="$wa",
    )
    command_b = SimpleNamespace(
        id=101,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=False, id=789),
        content="$k",
    )
    response = SimpleNamespace(
        id=200,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=True, id=999),
        content=(
            "Berry (YD)\nYurei Deco\n28:kakera:\n"
            "Berry (YD) / Yurei Deco - 28 ka"
        ),
        embeds=(),
    )

    asyncio.run(listener.handle_message(command_a))
    asyncio.run(listener.handle_message(command_b))
    asyncio.run(listener.handle_bot_response(response))

    assert len(catalog.recent_rolls("Test Server", "user_a", 1)) == 1
    assert catalog.recent_rolls("Test Server", "user_b", 1) == ()


def test_listener_does_not_clear_user_a_pending_workflow_for_user_b_unsupported_command(
    tmp_path,
) -> None:
    listener, catalog = _listener_with_two_configured_users(tmp_path)
    command_a = SimpleNamespace(
        id=100,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=False, id=456),
        content="$wa",
    )
    command_b = SimpleNamespace(
        id=101,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=False, id=789),
        content="$unsupported",
    )
    response = SimpleNamespace(
        id=200,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=True, id=999),
        content=(
            "Berry (YD)\nYurei Deco\n28:kakera:\n"
            "Berry (YD) / Yurei Deco - 28 ka"
        ),
        embeds=(),
    )

    asyncio.run(listener.handle_message(command_a))
    asyncio.run(listener.handle_message(command_b))
    asyncio.run(listener.handle_bot_response(response))

    assert len(catalog.recent_rolls("Test Server", "user_a", 1)) == 1
    assert catalog.recent_rolls("Test Server", "user_b", 1) == ()


def test_listener_keeps_paginated_response_with_initiating_user_after_user_b_command(
    tmp_path,
) -> None:
    listener, catalog = _listener_with_two_configured_users(tmp_path)

    def user_message(message_id: int, user_id: int, content: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=message_id,
            guild=SimpleNamespace(id=123),
            channel=SimpleNamespace(id=900),
            author=SimpleNamespace(bot=False, id=user_id),
            content=content,
        )

    def mudae_message(message_id: int, content: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=message_id,
            guild=SimpleNamespace(id=123),
            channel=SimpleNamespace(id=900),
            author=SimpleNamespace(bot=True, id=999),
            content=content,
            embeds=(),
        )

    page_one = "user_a's harem\nAlbedo · :goldkey: (7) 1,453 ka\nPage 1 / 2"
    page_two = "user_a's harem\nMiku Nakano · :silverkey: (6) 874 ka\nPage 2 / 2"

    asyncio.run(listener.handle_message(user_message(100, 456, "$mmy")))
    asyncio.run(listener.handle_bot_response(mudae_message(200, page_one)))
    scan_id = next(iter(listener._scan_ids.values()))

    asyncio.run(listener.handle_message(user_message(101, 789, "$wa")))
    asyncio.run(listener.handle_bot_response(mudae_message(200, page_two)))

    progress = catalog.harem_scan_progress(scan_id)
    assert progress is not None
    assert progress.imported_pages == (1, 2)
    assert progress.completed_at is not None


def test_listener_tracks_configured_slash_interaction_before_mudae_response(tmp_path) -> None:
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "LAKE ARROWHEAD 2025",
        "ernieuuu",
        discord_server_id="123",
        discord_user_id="456",
    )
    listener = DiscordListenerService(
        config_service=config,
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db")),
    )
    interaction = SimpleNamespace(
        guild_id=123,
        channel_id=789,
        user=SimpleNamespace(id=456),
        command=SimpleNamespace(name="wa"),
        data={},
    )

    asyncio.run(listener.handle_interaction(interaction))

    context = listener._contexts[789]
    assert context.identity.server == "LAKE ARROWHEAD 2025"
    assert context.identity.account == "ernieuuu"
    assert context.expected_kind == "roll"


@pytest.mark.parametrize(
    ("command", "expected_kind"),
    [
        ("$givek 147839232239599616 1", "gift_kakera"),
        ("$givesp 147839232239599616 1", "gift_spheres"),
        ("$give 147839232239599616 Megumi Sakura", "gift_character"),
        ("$trade 147839232239599616", "trade"),
    ],
)
def test_listener_tracks_numeric_discord_recipient_ids(
    tmp_path,
    command: str,
    expected_kind: str,
) -> None:
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "Lake Arrowhead 2025",
        "ernieuuu",
        discord_server_id="123",
        discord_user_id="456",
    )
    listener = DiscordListenerService(
        config_service=config,
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db")),
    )
    message = SimpleNamespace(
        id=987,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
        author=SimpleNamespace(bot=False, id=456),
        content=command,
    )

    asyncio.run(listener.handle_message(message))

    assert listener._contexts[789].expected_kind == expected_kind
    if expected_kind in {"gift_kakera", "gift_spheres"}:
        follow_up = SimpleNamespace(
            id=988,
            guild=SimpleNamespace(id=123),
            channel=SimpleNamespace(id=789),
            author=SimpleNamespace(bot=False, id=456),
            content="y",
        )
        asyncio.run(listener.handle_message(follow_up))
        assert listener._contexts[789].expected_kind == expected_kind


def test_listener_tracks_user_authored_slash_command_message(tmp_path) -> None:
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "Lake Arrowhead 2025",
        "cute_beagle_91130",
        role="alt",
        discord_server_id="123",
        discord_user_id="456",
    )
    listener = DiscordListenerService(
        config_service=config,
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db")),
    )
    message = SimpleNamespace(
        id=987,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
        author=SimpleNamespace(bot=False, id=456),
        content="",
        interaction_metadata=SimpleNamespace(name="ha"),
    )

    asyncio.run(listener.handle_message(message))

    context = listener._contexts[789]
    assert context.identity.account == "cute_beagle_91130"
    assert context.expected_kind == "roll"


def test_listener_reads_nested_slash_command_name_from_interaction_data() -> None:
    interaction = SimpleNamespace(
        command=None,
        data={"name": "rollsutil", "options": [{"name": "wa", "type": 1}]},
    )

    assert DiscordListenerService._interaction_command_name(interaction) == "wa"


@pytest.mark.parametrize("value", [0, 2])
def test_listener_imports_personal_rare_value_from_mudae_check_reaction(tmp_path, value) -> None:
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "Lake Arrowhead 2025",
        "ernieuuu",
        discord_server_id="123",
        discord_user_id="456",
    )
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    listener = DiscordListenerService(config_service=config, catalog_service=catalog)
    listener._mudae_user_id = 999

    command = SimpleNamespace(
        id=100,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
        author=SimpleNamespace(bot=False, id=456),
        content=f"$persr {value}",
    )
    acknowledgement = SimpleNamespace(
        guild_id=123,
        user_id=999,
        channel_id=789,
        message_id=100,
        emoji=SimpleNamespace(name="✅"),
    )

    asyncio.run(listener.handle_message(command))
    asyncio.run(listener.handle_raw_reaction_add(acknowledgement))

    state = catalog.personal_rare("Lake Arrowhead 2025", "ernieuuu")
    assert state is not None
    assert state.personal_rare_multiplier == value


def test_listener_only_extracts_concrete_personal_rare_values() -> None:
    assert DiscordListenerService._personal_rare_command_value("$persr") is None
    assert DiscordListenerService._personal_rare_command_value("$persr 2") == 2
    assert DiscordListenerService._personal_rare_command_value("$persr 0") == 0
    assert not DiscordListenerService._personal_rare_argument_supplied("$persr")
    assert DiscordListenerService._personal_rare_argument_supplied("$persr 999")


def test_listener_does_not_import_textual_current_value_for_value_setting_errors(tmp_path) -> None:
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "Lake Arrowhead 2025",
        "ernieuuu",
        discord_server_id="123",
        discord_user_id="456",
    )
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    listener = DiscordListenerService(config_service=config, catalog_service=catalog)
    listener._mudae_user_id = 999

    command = SimpleNamespace(
        id=100,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
        author=SimpleNamespace(bot=False, id=456),
        content="$persr 999",
    )
    response = SimpleNamespace(
        id=101,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
        author=SimpleNamespace(bot=True, id=999),
        content="Your current $personalrare: 1",
        embeds=(),
    )

    asyncio.run(listener.handle_message(command))
    asyncio.run(listener.handle_bot_response(response))

    assert catalog.personal_rare("Lake Arrowhead 2025", "ernieuuu") is None


@pytest.mark.parametrize(
    ("command", "expected_kind"),
    [
        ("$mmw", "harem"),
        ("$top", "top"),
        ("$topo", "top"),
        ("$topx", "topx"),
        ("$wl", "wishlist"),
        ("$persr", "personalrare"),
        ("$infokl", "infokl"),
        ("$kl", "lootstate"),
        ("/ha", "roll"),
    ],
)
def test_listener_maps_additional_supported_commands(command: str, expected_kind: str) -> None:
    assert DiscordListenerService._expected_kind_for_command(command) == expected_kind


def test_listener_tracks_and_cancels_divorce_confirmation(tmp_path) -> None:
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "ernieuuu's server",
        "cute_beagle_91130",
        discord_server_id="123",
        discord_user_id="456",
    )
    listener = DiscordListenerService(
        config_service=config,
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db")),
    )
    command = SimpleNamespace(
        id=100,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
        author=SimpleNamespace(bot=False, id=456),
        content="$divorce Professor Layton",
    )
    prompt = SimpleNamespace(
        id=101,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
        author=SimpleNamespace(bot=True, id=999),
        content=(
            "Professor Layton: Do you confirm the divorce? (y/n/yes/no)\n"
            "Characters divorced by $divorce are also removed from the $restorelist "
            "(+54:kakera:if you confirm)"
        ),
        embeds=(),
    )
    decline = SimpleNamespace(
        id=102,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
        author=SimpleNamespace(bot=False, id=456),
        content="no",
    )
    declined = SimpleNamespace(
        id=103,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
        author=SimpleNamespace(bot=True, id=999),
        content="Divorce declined.",
        embeds=(),
    )

    import asyncio

    asyncio.run(listener.handle_message(command))
    asyncio.run(listener.handle_bot_response(prompt))
    assert listener._contexts[789].expected_kind == "divorce"
    asyncio.run(listener.handle_message(decline))
    assert listener._contexts[789].expected_kind == "divorce_confirmation"
    asyncio.run(listener.handle_bot_response(declined))
    assert 789 not in listener._contexts


def test_listener_imports_completed_divorce_after_yes(tmp_path) -> None:
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "ernieuuu's server",
        "cute_beagle_91130",
        discord_server_id="123",
        discord_user_id="456",
    )
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    listener = DiscordListenerService(config_service=config, catalog_service=catalog)
    command = SimpleNamespace(
        id=200,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
        author=SimpleNamespace(bot=False, id=456),
        content="$divorce Professor Layton",
    )
    prompt = SimpleNamespace(
        id=201,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
        author=SimpleNamespace(bot=True, id=999),
        content=(
            "Professor Layton: Do you confirm the divorce? (y/n/yes/no)\n"
            "Characters divorced by $divorce are also removed from the $restorelist "
            "(+54:kakera:if you confirm)"
        ),
        embeds=(),
    )
    answer = SimpleNamespace(
        id=202,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
        author=SimpleNamespace(bot=False, id=456),
        content="yes",
    )
    complete = SimpleNamespace(
        id=203,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
        author=SimpleNamespace(bot=True, id=999),
        content="💔 Professor Layton and cute_beagle_91130 are now divorced. 💔 (+54:kakera:)",
        embeds=(),
    )

    import asyncio

    asyncio.run(listener.handle_message(command))
    asyncio.run(listener.handle_bot_response(prompt))
    asyncio.run(listener.handle_message(answer))
    asyncio.run(listener.handle_bot_response(complete))

    assert 789 not in listener._contexts
    assert catalog.claim_observations("ernieuuu's server", "cute_beagle_91130") == ()


def test_listener_classifies_full_disablelist_response(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )
    raw_message = (
        "ernieuuu's **Disablelist (13/16)**\n"
        "107,529 disabled (**41,247 $wa**, **42,438 $ha**, 20,996 $wg, 14,789 $hg)\n"
        "⚠️ Pool limit reached: **40,861 $wa** (series above this limit are not disabled)\n"
        "⚠️ Pool limit reached: **42,213 $ha** (series above this limit are not disabled)\n"
        "Western animanga series are completely disabled ($togglewestern)\n"
        "IRL series are completely disabled ($toggleirl)\n"
        "Kadokawa Corporation (13,207)\n"
        "Shueisha (10,692)\n"
        "Webcomics (11,073)\n"
        "Kodansha (7,991)\n"
        "Hentai (9,550)\n"
        "Shogakukan (4,702)\n"
        "Square Enix Holdings (6,003)\n"
        "YouTube (3,159)\n"
        "Turn-Based Role-Playing Games (13,190)\n"
        "Isekai (6,197)\n"
        "Ecchi (5,716)\n"
        "Manhwa (7,017)\n"
        "Mobile Games (16,769)"
    )

    assert listener._resolve_message_kind("disablelist", raw_message) == "disablelist"


def test_listener_classifies_actual_topx_marker_for_topx_context(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )
    raw_message = "🏆 TOP 1000\n#10 - 2B - NieR: Automata 🚫\nPage 1 / 67"

    assert listener._resolve_message_kind("topx", raw_message) == "topx"


def test_extract_message_text_normalizes_discord_custom_emojis() -> None:
    message = SimpleNamespace(
        content="",
        embeds=(
            SimpleNamespace(
                author=SimpleNamespace(name="Mudae"),
                title="You have 12,114 <:kakera:123456789>!",
                description="<:goldkey:987654321> (7)",
                fields=(),
                footer=SimpleNamespace(text=""),
            ),
        ),
    )

    assert DiscordListenerService.extract_message_text(message) == (
        "Mudae\nYou have 12,114 :kakera:!\n:goldkey: (7)"
    )


def test_listener_classifies_a_ranked_roll_card_as_a_roll(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )
    raw_message = (
        "$m mai sakurajima\n"
        "Mudae\n"
        "Mai Sakurajima\n"
        "Seishun Buta Yarou :female:\n"
        "Animanga roulette · 1,494:kakera: · :goldkey: (7)\n"
        "Claim Rank: #9\n"
        "Like Rank: #19"
    )

    assert listener._resolve_message_kind("roll", raw_message) == "roll"


def test_listener_preserves_roll_context_for_followup_without_command_name(tmp_path) -> None:
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "ernieuuu's server",
        "cute_beagle_91130",
        discord_server_id="123",
        discord_user_id="456",
    )
    listener = DiscordListenerService(
        config_service=config,
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db")),
    )
    command = SimpleNamespace(
        id=1,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
        author=SimpleNamespace(bot=False, id=456),
        content="$wa",
    )
    asyncio.run(listener.handle_message(command))

    followup = SimpleNamespace(
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
        interaction_metadata=SimpleNamespace(
            user=SimpleNamespace(id=456),
            name=None,
        ),
    )

    context = listener._context_from_interaction(followup)
    assert context is not None
    assert context.expected_kind == "roll"


def test_listener_routes_claim_confirmation_after_a_roll_to_claim_import(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )
    raw_message = "💖 **ernieuuu** and **Pakunoda** are now married! 💖\n+128:kakera:"

    assert listener._resolve_message_kind("roll", raw_message) == "claim"
    assert listener._resolve_message_kind("timers", raw_message) == "claim"


def test_listener_attributes_claim_to_claimant_after_alternating_accounts(tmp_path) -> None:
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "ernieuuu's server",
        "ernieuuu",
        discord_server_id="123",
        discord_user_id="456",
    )
    config.add_account(
        "ernieuuu's server",
        "cute_beagle_91130",
        role="alt",
        discord_server_id="123",
        discord_user_id="789",
    )
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    listener = DiscordListenerService(config_service=config, catalog_service=catalog)

    command = SimpleNamespace(
        id=300,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=False, id=456),
        content="$wa",
    )
    claim = SimpleNamespace(
        id=301,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=True, id=999),
        content="cute_beagle_91130 and Ines Fujin are now married!",
        embeds=(),
    )

    import asyncio

    asyncio.run(listener.handle_message(command))
    asyncio.run(listener.handle_bot_response(claim))

    assert catalog.claim_observations("ernieuuu's server", "ernieuuu") == ()
    observations = catalog.claim_observations("ernieuuu's server", "cute_beagle_91130")
    assert len(observations) == 1
    assert observations[0].character_name == "Ines Fujin"


def test_listener_routes_roll_limit_response_to_timer_import(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )
    raw_message = (
        "cute_beagle_91130, the roulette is limited to 10 uses per hour. **6** min left.\n"
        "Upvote Mudae to reset the timer: $vote. Website: https://mudae.net/"
    )

    assert listener._resolve_message_kind("roll", raw_message) == "timers"


def test_listener_routes_kakera_reaction_block_after_a_receipt_without_timer_import(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )
    raw_message = "cute_beagle_91130, You can't react to kakera for 34 min. ($ku)"

    assert listener._resolve_message_kind("reaction_receipt", raw_message) == "reaction_blocked"


def test_listener_routes_standalone_kakera_timer_to_timer_import(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )
    raw_message = (
        "You can't react to kakera for 11 min.\n"
        "Power: 32%\n"
        "Each kakera button consumes 36% of your reaction power.\n"
        "Stock: 33,441:kakera:"
    )

    assert listener._resolve_message_kind("timers", raw_message) == "timers"


def test_listener_recovers_a_roll_after_stale_wishlist_context(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )
    raw_message = "Hips\nDekoboko Majo no Oyako Jijou\n30:kakera:"

    assert listener._resolve_message_kind("wishlist", raw_message) == "roll"


def test_listener_recovers_roll_after_stale_timer_context(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )
    raw_message = (
        "Inglis\n"
        "Eiyuu-ou, Bu wo Kiwameru Tame Tenseisu: Shoshite, Sekai Saikyou no Minarai Kishi\n"
        "Claims: #5,361\n"
        "61:kakera:\n"
        "Inglis / Eiyuu-ou, Bu wo Kiwameru Tame Tenseisu: Shoshite, Sekai Saikyou no Minarai Kishi - 61 ka"
    )

    assert listener._resolve_message_kind("timers", raw_message) == "roll"


def test_listener_prefers_timer_detection_over_a_false_roll_match(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )
    raw_message = (
        "ernieuuu, you can claim right now! The next claim reset is in 2h 33 min.\n"
        "You have 0 rolls left. Next rolls reset in 33 min.\n"
        "Next $daily reset in 18h 41 min.\n\n"
        "You can react to kakera right now!\n"
        "Power: 100%\n"
        "Each kakera button consumes 100% of your reaction power.\n"
        "Your characters with 10+ keys consume half the power (50%)\n"
        "Stock: 170:kakera:"
    )

    assert listener._resolve_message_kind("roll", raw_message) == "timers"


def test_listener_classifies_bold_kakera_receipt_before_roll_fallback(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )
    raw_message = ":kakeraY: **ernieuuu +524** ($k)"

    assert listener._resolve_message_kind(None, raw_message) == "reaction_receipt"


def test_listener_does_not_leave_receipt_context_for_the_next_mudae_response(tmp_path) -> None:
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "Lake Arrowhead 2025",
        "ernieuuu",
        discord_server_id="123",
        discord_user_id="456",
    )
    listener = DiscordListenerService(
        config_service=config,
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db")),
    )
    message = SimpleNamespace(
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
    )

    context = listener._context_from_reaction_receipt(
        message,
        ":kakeraP: (Free) **ernieuuu +110** ($k)",
    )

    assert context is not None
    assert context.expected_kind == "reaction_receipt"
    assert 789 not in listener._contexts


def test_listener_keeps_scan_commands_from_being_misclassified_as_rolls(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )
    raw_message = (
        "ernieuuu's harem\n"
        "Mai Sakurajima · :goldkey: (7) 1,494 ka\n"
        "Page 1 / 19"
    )

    assert listener._resolve_message_kind("harem", raw_message) == "harem"
    assert listener._resolve_message_kind("harem", "Mai Sakurajima\nSeries\n34:kakera:") is None


def test_listener_keeps_paginated_scan_context_after_intervening_command(tmp_path) -> None:
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "Lake Arrowhead 2025",
        "ernieuuu",
        discord_server_id="123",
        discord_user_id="456",
    )
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    listener = DiscordListenerService(config_service=config, catalog_service=catalog)

    def user_message(message_id: int, content: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=message_id,
            guild=SimpleNamespace(id=123),
            channel=SimpleNamespace(id=789),
            author=SimpleNamespace(bot=False, id=456),
            content=content,
        )

    def mudae_message(message_id: int, content: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=message_id,
            guild=SimpleNamespace(id=123),
            channel=SimpleNamespace(id=789),
            author=SimpleNamespace(bot=True, id=999),
            content=content,
            embeds=(),
        )

    page_one = (
        "ernieuuu's harem\n"
        "Albedo · :goldkey: (7) 1,453 ka\n"
        "Page 1 / 3"
    )
    page_three = (
        "ernieuuu's harem\n"
        "Miku Nakano · :silverkey: (6) 874 ka\n"
        "Page 3 / 3"
    )
    page_two = (
        "ernieuuu's harem\n"
        "Rem · :silverkey: (4) 1,426 ka\n"
        "Page 2 / 3"
    )

    asyncio.run(listener.handle_message(user_message(100, "$mmy")))
    asyncio.run(listener.handle_bot_response(mudae_message(200, page_one)))
    scan_id = next(iter(listener._scan_ids.values()))

    # Mudae edits the same message as the user pages. `$tu` must not replace
    # the scan context used for the next harem edit.
    asyncio.run(listener.handle_message(user_message(101, "$tu")))
    asyncio.run(listener.handle_bot_response(mudae_message(200, page_three)))
    progress = catalog.harem_scan_progress(scan_id)
    assert progress is not None
    assert progress.imported_pages == (1, 3)
    assert progress.completed_at is None

    asyncio.run(listener.handle_bot_response(mudae_message(200, page_two)))
    progress = catalog.harem_scan_progress(scan_id)
    assert progress is not None
    assert progress.imported_pages == (1, 2, 3)
    assert progress.completed_at is not None


def test_listener_does_not_turn_bonus_or_timer_text_into_a_roll(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )

    timer_text = "You have 0 rolls left. Next rolls reset in 40 min."
    bonus_text = "Player Bonuses\nRolls per hour: +9"

    assert listener._resolve_message_kind("bonus", timer_text) is None
    assert listener._resolve_message_kind("bonus", bonus_text) == "bonus"
    assert listener._resolve_message_kind("timers", timer_text) == "timers"


def test_listener_routes_help_and_tutorial_responses_without_character_imports(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )

    assert listener._resolve_message_kind("help", "Mudae help text") == "help"
    assert listener._resolve_message_kind("tutorial", "2/17 - Tutorial") == "tutorial"
    assert listener._resolve_message_kind("tutorial", "Step 1 completed! Reward: +200:kakera:") == "tutorial"


def test_listener_routes_profile_response(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )
    raw_message = (
        "cute_beagle_91130\n"
        "Collection size: 35 (100%:female: 0% :male:)\n"
        "Pokédex: 2 Pokémon :gulpin: :piloswine:\n"
        "Reacts:\n"
        "1x:kakeraP: 7x:kakera: 1x:kakeraT:\n"
        "812:kakera:\n"
        "Keys: 3:bronzekey:\n"
        "110 :sp:\n"
        "2x:spP: 12x:spB: 7x:spT: 4x:spG: 1x:spY: 1x:sp: 4x:spL:\n"
        ":silvmudae::MudaeBirthday7::MudaeBirthday8::DiamondI:"
    )

    assert listener._resolve_message_kind("profile", raw_message) == "profile"


def test_listener_routes_mudapin_responses(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )

    assert listener._resolve_message_kind("mudapins", ":pin139::logopin6:") == "mudapins"
    assert listener._resolve_message_kind(
        "mudapins", "No mudapins found! Collect them with kakeraloots ($kl)"
    ) == "mudapins"


def test_listener_classifies_sphere_result_for_oq_context(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )

    raw_message = ":sp: +158\n:spG: +43 (Stock: 3,655)"

    assert listener._resolve_message_kind("sphere_result", raw_message) == "sphere_result"


def test_listener_classifies_character_details_for_im_context(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )
    raw_message = (
        "Kaede Azusagawa\n"
        "Seishun Buta Yarou :female:\n"
        "Animanga roulette · 238:kakera: · :bronzekey: (**1**)\n"
        "Claim Rank: #505\n"
        "Like Rank: #735"
    )

    assert listener._resolve_message_kind("im", raw_message) == "im"


def test_listener_tracks_configured_user_reactions(tmp_path) -> None:
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "Lake Arrowhead 2025",
        "ernieuuu",
        discord_server_id="123",
        discord_user_id="456",
    )
    listener = DiscordListenerService(
        config_service=config,
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db")),
    )
    payload = SimpleNamespace(
        guild_id=123,
        user_id=456,
        channel_id=789,
        message_id=987,
        emoji="💞",
    )

    asyncio.run(listener.handle_raw_reaction_add(payload))

    assert 789 not in listener._contexts


def test_listener_uses_cached_message_for_raw_edit_without_fetching(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )
    message = SimpleNamespace(id=987, guild=None, author=SimpleNamespace(bot=True))
    listener._message_cache[message.id] = message

    class UnexpectedClient:
        def get_channel(self, _channel_id):
            raise AssertionError("cached message edits should not fetch the channel")

    listener._client = UnexpectedClient()
    payload = SimpleNamespace(channel_id=789, message_id=987)

    asyncio.run(listener.handle_raw_message_edit(payload))


def test_listener_ignores_uncached_raw_edit_without_rest_fetch(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )

    class UnexpectedClient:
        def get_channel(self, _channel_id):
            raise AssertionError("uncached edits should not fetch historical messages")

    listener._client = UnexpectedClient()
    payload = SimpleNamespace(channel_id=789, message_id=988)

    asyncio.run(listener.handle_raw_message_edit(payload))


def test_listener_characterizes_edit_as_completion_of_incomplete_message(
    tmp_path,
) -> None:
    listener, catalog = _listener_with_two_configured_users(tmp_path)
    listener._mudae_user_id = 999
    original = SimpleNamespace(
        id=1200,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=True, id=999),
        interaction_metadata=SimpleNamespace(name="wa", user=SimpleNamespace(id=456)),
        # Sanitized real Discord/Mudae output: an incomplete first delivery.
        content="Berry (YD)\nYurei Deco",
        embeds=(),
    )
    edited = SimpleNamespace(
        id=1200,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=True, id=999),
        interaction_metadata=SimpleNamespace(name="wa", user=SimpleNamespace(id=456)),
        # Sanitized real Discord/Mudae output: an edited version of that same roll.
        content=(
            "Miku Nakano\nThe Quintessential Quintuplets\n44:kakera:\n"
            "Miku Nakano / The Quintessential Quintuplets - 44 ka"
        ),
        embeds=(),
    )

    asyncio.run(listener.handle_bot_response(original))
    asyncio.run(listener.handle_message_edit(original, edited))

    rows = _import_event_rows(tmp_path / "catalog.db", "roll")
    rolls = catalog.recent_rolls("Test Server", "user_a", 10)
    assert len(rows) == 1
    assert rows[0] == (
        "discord:guild=123:channel=900:message=1200",
        edited.content,
    )
    assert [roll.character.name for roll in rolls] == ["Miku Nakano"]
    assert catalog.recent_rolls("Test Server", "user_b", 10) == ()


def test_listener_characterizes_duplicate_message_delivery_as_process_local_only(
    tmp_path,
) -> None:
    listener, catalog = _listener_with_two_configured_users(tmp_path)
    listener._mudae_user_id = 999

    def mudae_roll(message_id: int) -> SimpleNamespace:
        return SimpleNamespace(
            id=message_id,
            guild=SimpleNamespace(id=123),
            channel=SimpleNamespace(id=900),
            author=SimpleNamespace(bot=True, id=999),
            interaction_metadata=SimpleNamespace(name="wa", user=SimpleNamespace(id=456)),
            # Sanitized real Discord/Mudae output: identical text for retry comparisons.
            content=(
                "Berry (YD)\nYurei Deco\n28:kakera:\n"
                "Berry (YD) / Yurei Deco - 28 ka"
            ),
            embeds=(),
        )

    redelivery = mudae_roll(1201)
    separate_message = mudae_roll(1202)
    asyncio.run(listener.handle_bot_response(redelivery))
    asyncio.run(listener.handle_bot_response(redelivery))
    asyncio.run(listener.handle_bot_response(separate_message))

    rows = _import_event_rows(tmp_path / "catalog.db", "roll")
    assert len(rows) == 2
    assert [row[0] for row in rows] == [
        "discord:guild=123:channel=900:message=1201",
        "discord:guild=123:channel=900:message=1202",
    ]
    assert len(catalog.recent_rolls("Test Server", "user_a", 10)) == 2
    assert catalog.recent_rolls("Test Server", "user_b", 10) == ()


def _durable_listener(tmp_path, *, importer=None):
    database_path = tmp_path / "catalog.db"
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "Test Server",
        "user_a",
        discord_server_id="123",
        discord_user_id="456",
    )
    catalog_repository = CatalogRepository(database_path)
    catalog = CatalogService(catalog_repository)
    repository = DiscordMessageRepository(database_path)
    if importer is None:
        importer = AutomaticImportService(
            catalog,
            roll_projection_coordinator=RollProjectionCoordinator(
                catalog_repository,
                repository,
            ),
            profile_projection_coordinator=ProfileProjectionCoordinator(
                catalog_repository,
                repository,
            ),
            claim_projection_coordinator=ClaimProjectionCoordinator(
                catalog_repository,
                repository,
            ),
            settings_projection_coordinator=SettingsProjectionCoordinator(
                catalog_repository,
                repository,
            ),
            infokl_projection_coordinator=InfoklProjectionCoordinator(
                catalog_repository,
                repository,
            ),
        )
    listener = DiscordListenerService(
        config_service=config,
        catalog_service=catalog,
        importer=importer,
        discord_message_repository=repository,
    )
    listener._mudae_user_id = 999
    return listener, repository, database_path


def _attribution_listener(tmp_path, config_name, accounts, *, importer=None):
    database_path = tmp_path / "catalog.db"
    config = ConfigService(tmp_path / config_name)
    for server, account, role, user_id in accounts:
        config.add_account(
            server,
            account,
            role=role,
            discord_server_id="123",
            discord_user_id=user_id,
        )
    catalog_repository = CatalogRepository(database_path)
    catalog = CatalogService(catalog_repository)
    repository = DiscordMessageRepository(database_path)
    if importer is None:
        importer = AutomaticImportService(
            catalog,
            roll_projection_coordinator=RollProjectionCoordinator(
                catalog_repository,
                repository,
            ),
            profile_projection_coordinator=ProfileProjectionCoordinator(
                catalog_repository,
                repository,
            ),
            claim_projection_coordinator=ClaimProjectionCoordinator(
                catalog_repository,
                repository,
            ),
            settings_projection_coordinator=SettingsProjectionCoordinator(
                catalog_repository,
                repository,
            ),
            infokl_projection_coordinator=InfoklProjectionCoordinator(
                catalog_repository,
                repository,
            ),
        )
    listener = DiscordListenerService(
        config_service=config,
        catalog_service=catalog,
        importer=importer,
        discord_message_repository=repository,
    )
    listener._mudae_user_id = 999
    return listener, repository, database_path


def _durable_importer_for(catalog, database_path):
    catalog_repository = CatalogRepository(database_path)
    discord_repository = DiscordMessageRepository(database_path)
    return (
        AutomaticImportService(
            catalog,
            roll_projection_coordinator=RollProjectionCoordinator(
                catalog_repository,
                discord_repository,
            ),
        ),
        discord_repository,
    )


def _durable_roll_message(
    message_id: int = 1209,
    *,
    content: str = "Berry (YD)\nYurei Deco\n28:kakera:\nBerry (YD) / Yurei Deco - 28 ka",
    edited_at: datetime | None = None,
    author_id: int = 999,
):
    return SimpleNamespace(
        id=message_id,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=True, id=author_id),
        interaction_metadata=SimpleNamespace(name="wa", user=SimpleNamespace(id=456)),
        content=content,
        embeds=(),
        edited_at=edited_at,
    )


def _durable_profile_message(message_id: int = 1211, *, content: str | None = None):
    return SimpleNamespace(
        id=message_id,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=True, id=999),
        interaction_metadata=SimpleNamespace(
            name="profile", user=SimpleNamespace(id=456)
        ),
        content=content or "user_a\nCollection size: 0 (0%:female: 0% :male:)",
        embeds=(),
        edited_at=None,
    )


def _durable_claim_message(
    message_id: int = 1212,
    *,
    claimant: str = "user_a",
    interaction_user_id: int = 456,
):
    return SimpleNamespace(
        id=message_id,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=True, id=999),
        interaction_metadata=SimpleNamespace(
            name="wa", user=SimpleNamespace(id=interaction_user_id)
        ),
        content=f"{claimant} and Pakunoda are now married!",
        embeds=(),
        edited_at=None,
    )


def _durable_settings_message(message_id: int = 1213, *, content: str | None = None):
    return SimpleNamespace(
        id=message_id,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=True, id=999),
        interaction_metadata=SimpleNamespace(
            name="settings", user=SimpleNamespace(id=456)
        ),
        # Sanitized real Mudae `$settings` response.
        content=content
        or "\n".join(
            (
                "Server Settings",
                "(Server not premium)",
                "- Prefix: $ ($prefix)",
                "- Lang: en ($lang)",
                "- Claim reset: every 180 min. ($setclaim)",
                "- Exact minute of the reset: xx:14 ($setinterval)",
                "- Reset shifted: by +0 min. ($shifthour)",
                "- Rolls per hour: 10 ($setrolls)",
                "- Time before the claim reaction expires: 45 sec. ($settimer)",
                "- Spawn rarity multiplier for already claimed characters: 4 ($setrare)",
                "- % kakera bonus: +0 ($setkakerabonus)",
                "- % sphere bonus: +0 ($setspherebonus)",
                "- Game mode: 1 ($gamemode)",
                "- This channel instance: 1 ($channelinstance)",
            )
        ),
        embeds=(),
        edited_at=None,
    )


def _durable_infokl_message(message_id: int = 1214, *, interaction=True):
    return SimpleNamespace(
        id=message_id,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=True, id=999),
        interaction_metadata=(
            SimpleNamespace(name="infokl", user=SimpleNamespace(id=456))
            if interaction
            else None
        ),
        # Sanitized real Mudae `$infokl` response.
        content=(
            "Each $kl costs 500:kakera:\n"
            "Reaching the level 1 of quantity or quality costs 2,000:kakera: "
            "(increased by 200/level)"
        ),
        embeds=(),
        edited_at=None,
    )


def _receipt_rows(database_path, table: str):
    with connect(database_path) as connection:
        return connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()


def _server_attribution_rows(database_path):
    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT source_event_id, status, server_name FROM "
            "discord_source_event_server_attributions ORDER BY source_event_id"
        ).fetchall()
    return [tuple(row) for row in rows]


def test_listener_receives_new_bot_message_before_importing(tmp_path) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)
    importer = Mock(wraps=listener._importer)
    listener._importer = importer
    repository.mark_processing_success = Mock(wraps=repository.mark_processing_success)

    asyncio.run(listener.handle_bot_response(_durable_roll_message()))

    durable_context = importer.import_message.call_args.kwargs["durable_roll_context"]
    assert durable_context.source_event_id == 1
    assert durable_context.attempt_id == 1
    assert durable_context.finished_at.tzinfo is not None
    assert durable_context.finished_at.utcoffset() is not None
    repository.mark_processing_success.assert_not_called()
    aggregates = _receipt_rows(database_path, "discord_message_aggregates")
    revisions = _receipt_rows(database_path, "discord_message_revisions")
    events = _receipt_rows(database_path, "discord_source_events")
    assert len(aggregates) == len(revisions) == len(events) == 1
    assert events[0]["event_kind"] == "message_revision"
    assert events[0]["delivery_count"] == 1
    assert revisions[0]["source_observed_at"] is None
    attempts = _receipt_rows(database_path, "discord_processing_attempts")
    assert len(attempts) == 1
    assert attempts[0]["attempt_number"] == 1
    assert attempts[0]["status"] == "succeeded"
    assert attempts[0]["parser_version"] == "mudae-parser-v1"
    assert attempts[0]["router_version"] == "mudae-router-v1"
    assert _receipt_rows(database_path, "import_events")[0]["kind"] == "roll"
    assert len(_receipt_rows(database_path, "roll_observations")) == 1
    assert len(_receipt_rows(database_path, "server_character_observations")) == 1
    assert len(_receipt_rows(database_path, "discord_projection_links")) == 2


def test_listener_first_durable_profile_coordinates_and_owns_success(tmp_path) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)
    importer = Mock(wraps=listener._importer)
    listener._importer = importer
    repository.mark_processing_success = Mock(wraps=repository.mark_processing_success)

    asyncio.run(listener.handle_bot_response(_durable_profile_message()))

    context = importer.import_message.call_args.kwargs["durable_profile_context"]
    assert context.source_event_id == 1
    assert context.attempt_id == 1
    assert context.finished_at.tzinfo is not None
    assert context.finished_at.utcoffset() is not None
    repository.mark_processing_success.assert_not_called()
    assert len(_receipt_rows(database_path, "import_events")) == 1
    assert len(_receipt_rows(database_path, "profile_observations")) == 1
    assert len(_receipt_rows(database_path, "discord_projection_links")) == 1
    assert _receipt_rows(database_path, "discord_source_events")[0]["status"] == "succeeded"
    assert _receipt_rows(database_path, "discord_processing_attempts")[0]["status"] == "succeeded"


def test_listener_succeeded_profile_restart_replays_without_new_projection(tmp_path) -> None:
    first_listener, _repository, database_path = _durable_listener(tmp_path)
    message = _durable_profile_message()
    asyncio.run(first_listener.handle_bot_response(message))

    restarted_listener, _restarted_repository, _ = _durable_listener(tmp_path)
    results = []
    actual_importer = restarted_listener._importer

    def import_replay(*args, **kwargs):
        result = actual_importer.import_message(*args, **kwargs)
        results.append(result)
        return result

    importer = Mock()
    importer.import_message.side_effect = import_replay
    restarted_listener._importer = importer
    asyncio.run(restarted_listener.handle_bot_response(message))

    context = importer.import_message.call_args.kwargs["durable_profile_context"]
    assert context.source_event_id == 1
    assert context.attempt_id is None
    assert results[0].replay_skipped is True
    assert results[0].durable_success_recorded is True
    assert len(_receipt_rows(database_path, "discord_processing_attempts")) == 1
    assert len(_receipt_rows(database_path, "import_events")) == 1
    assert len(_receipt_rows(database_path, "profile_observations")) == 1
    assert len(_receipt_rows(database_path, "discord_projection_links")) == 1


def test_listener_retryable_profile_coordinator_failure_retries_once(tmp_path) -> None:
    first_listener, _repository, database_path = _durable_listener(tmp_path)
    coordinator = first_listener._importer._profile_projection_coordinator
    coordinator.coordinate_profile = Mock(side_effect=RuntimeError("temporary profile failure"))
    message = _durable_profile_message()

    asyncio.run(first_listener.handle_bot_response(message))

    failed_attempt = _receipt_rows(database_path, "discord_processing_attempts")[0]
    assert failed_attempt["status"] == "failed"
    assert failed_attempt["retryable"] == 1
    assert len(_receipt_rows(database_path, "import_events")) == 0
    assert len(_receipt_rows(database_path, "profile_observations")) == 0
    assert len(_receipt_rows(database_path, "discord_projection_links")) == 0

    second_listener, _second_repository, _ = _durable_listener(tmp_path)
    asyncio.run(second_listener.handle_bot_response(message))

    attempts = _receipt_rows(database_path, "discord_processing_attempts")
    assert [attempt["attempt_number"] for attempt in attempts] == [1, 2]
    assert [attempt["status"] for attempt in attempts] == ["failed", "succeeded"]
    assert len(_receipt_rows(database_path, "import_events")) == 1
    assert len(_receipt_rows(database_path, "profile_observations")) == 1
    assert len(_receipt_rows(database_path, "discord_projection_links")) == 1


def test_listener_active_profile_does_not_start_or_import_again(tmp_path) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)
    message = _durable_profile_message()
    listener._receive_message(message, message.content)
    active = repository.begin_processing_attempt(
        source_event_id=1,
        parser_version="mudae-parser-v1",
        router_version="mudae-router-v1",
        started_at=datetime.now(timezone.utc),
    )
    importer = Mock()
    replay_listener, _replay_repository, _ = _durable_listener(tmp_path, importer=importer)

    asyncio.run(replay_listener.handle_bot_response(message))

    attempts = _receipt_rows(database_path, "discord_processing_attempts")
    assert len(attempts) == 1
    assert attempts[0]["id"] == active.attempt_id
    assert attempts[0]["status"] == "processing"
    importer.import_message.assert_not_called()


@pytest.mark.parametrize("terminal_status", ["failed", "unresolved_attribution"])
def test_listener_terminal_profile_does_not_import_or_fallback(tmp_path, terminal_status) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)
    message = _durable_profile_message()
    listener._receive_message(message, message.content)
    attempt = repository.begin_processing_attempt(
        source_event_id=1,
        parser_version="mudae-parser-v1",
        router_version="mudae-router-v1",
        started_at=datetime.now(timezone.utc),
    )
    finished_at = datetime.now(timezone.utc)
    repository.mark_processing_failure(
        source_event_id=1,
        attempt_id=attempt.attempt_id,
        status=terminal_status,
        retryable=False,
        failure_code="terminal_test_failure",
        failure_detail="terminal test failure",
        finished_at=finished_at,
    )
    importer = Mock()
    replay_listener, _replay_repository, _ = _durable_listener(tmp_path, importer=importer)

    asyncio.run(replay_listener.handle_bot_response(message))

    assert len(_receipt_rows(database_path, "discord_processing_attempts")) == 1
    assert _receipt_rows(database_path, "discord_source_events")[0]["status"] == terminal_status
    importer.import_message.assert_not_called()


def test_listener_profile_cleanup_failure_preserves_durable_success(tmp_path, caplog) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)
    repository.mark_processing_failure = Mock(wraps=repository.mark_processing_failure)
    listener._consume_context = Mock(side_effect=RuntimeError("cleanup unavailable"))
    caplog.set_level(logging.ERROR, logger="moa.discord")

    asyncio.run(listener.handle_bot_response(_durable_profile_message()))

    attempt = _receipt_rows(database_path, "discord_processing_attempts")[0]
    event = _receipt_rows(database_path, "discord_source_events")[0]
    assert attempt["status"] == "succeeded"
    assert event["status"] == "succeeded"
    repository.mark_processing_failure.assert_not_called()
    assert "Best-effort cleanup failed after durable profile success" in caplog.text


def test_listener_first_durable_settings_coordinates_and_owns_success(tmp_path) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)
    importer = Mock(wraps=listener._importer)
    listener._importer = importer
    repository.mark_processing_success = Mock(wraps=repository.mark_processing_success)

    asyncio.run(listener.handle_bot_response(_durable_settings_message()))

    durable_context = importer.import_message.call_args.kwargs["durable_settings_context"]
    assert "durable_infokl_context" not in importer.import_message.call_args.kwargs
    assert durable_context.source_event_id == 1
    assert durable_context.attempt_id == 1
    assert durable_context.finished_at.tzinfo is not None
    assert durable_context.finished_at.utcoffset() is not None
    repository.mark_processing_success.assert_not_called()
    assert len(_import_event_rows(database_path, "server_settings")) == 1
    assert len(_receipt_rows(database_path, "server_settings_observations")) == 1
    assert len(_receipt_rows(database_path, "server_contexts")) == 1
    assert len(_receipt_rows(database_path, "discord_projection_links")) == 1
    assert _receipt_rows(database_path, "discord_source_events")[0]["status"] == "succeeded"
    assert _receipt_rows(database_path, "discord_processing_attempts")[0]["status"] == "succeeded"


def test_listener_succeeded_settings_restart_replays_without_new_projection(tmp_path) -> None:
    first_listener, _repository, database_path = _durable_listener(tmp_path)
    message = _durable_settings_message()
    asyncio.run(first_listener.handle_bot_response(message))

    restarted_listener, _restarted_repository, _ = _durable_listener(tmp_path)
    actual_importer = restarted_listener._importer
    results = []

    def import_replay(*args, **kwargs):
        result = actual_importer.import_message(*args, **kwargs)
        results.append(result)
        return result

    importer = Mock()
    importer.import_message.side_effect = import_replay
    restarted_listener._importer = importer
    asyncio.run(restarted_listener.handle_bot_response(message))

    durable_context = importer.import_message.call_args.kwargs["durable_settings_context"]
    assert durable_context.source_event_id == 1
    assert durable_context.attempt_id is None
    assert results[0].imported_count == 0
    assert results[0].replay_skipped is True
    assert results[0].durable_success_recorded is True
    assert len(_receipt_rows(database_path, "discord_processing_attempts")) == 1
    assert len(_import_event_rows(database_path, "server_settings")) == 1
    assert len(_receipt_rows(database_path, "server_settings_observations")) == 1
    assert len(_receipt_rows(database_path, "server_contexts")) == 1
    assert len(_receipt_rows(database_path, "discord_projection_links")) == 1


def test_listener_retryable_settings_coordinator_failure_retries_transactionally(tmp_path) -> None:
    listener, _repository, database_path = _durable_listener(tmp_path)
    coordinator = listener._importer._settings_projection_coordinator
    coordinator.coordinate_settings = Mock(side_effect=RuntimeError("settings coordinator failed"))
    message = _durable_settings_message()

    asyncio.run(listener.handle_bot_response(message))

    first_attempt = _receipt_rows(database_path, "discord_processing_attempts")[0]
    assert first_attempt["status"] == "failed"
    assert first_attempt["retryable"] == 1
    assert _import_event_rows(database_path, "server_settings") == []
    assert _receipt_rows(database_path, "server_settings_observations") == []
    assert _receipt_rows(database_path, "server_contexts") == []
    assert _receipt_rows(database_path, "discord_projection_links") == []

    retry_listener, _retry_repository, _ = _durable_listener(tmp_path)
    asyncio.run(retry_listener.handle_bot_response(message))

    attempts = _receipt_rows(database_path, "discord_processing_attempts")
    assert [(row["attempt_number"], row["status"]) for row in attempts] == [
        (1, "failed"),
        (2, "succeeded"),
    ]
    assert len(_import_event_rows(database_path, "server_settings")) == 1
    assert len(_receipt_rows(database_path, "server_settings_observations")) == 1
    assert len(_receipt_rows(database_path, "server_contexts")) == 1
    assert len(_receipt_rows(database_path, "discord_projection_links")) == 1


def test_listener_active_settings_does_not_start_attempt_or_import(tmp_path) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)
    message = _durable_settings_message()
    received = listener._receive_message(message, message.content)
    assert received is not None
    active = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="mudae-parser-v1",
        router_version="mudae-router-v1",
        started_at=datetime.now(timezone.utc),
    )
    importer = Mock()
    replay_listener, _replay_repository, _ = _durable_listener(tmp_path, importer=importer)

    asyncio.run(replay_listener.handle_bot_response(message))

    importer.import_message.assert_not_called()
    attempts = _receipt_rows(database_path, "discord_processing_attempts")
    assert len(attempts) == 1
    assert attempts[0]["id"] == active.attempt_id
    assert attempts[0]["status"] == "processing"


@pytest.mark.parametrize("terminal_status", ["failed", "unresolved_attribution"])
def test_listener_nonretryable_settings_terminal_state_fails_closed(
    tmp_path, terminal_status
) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)
    message = _durable_settings_message()
    received = listener._receive_message(message, message.content)
    assert received is not None
    attempt = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="mudae-parser-v1",
        router_version="mudae-router-v1",
        started_at=datetime.now(timezone.utc),
    )
    repository.mark_processing_failure(
        source_event_id=received.source_event_id,
        attempt_id=attempt.attempt_id,
        status=terminal_status,
        retryable=False,
        failure_code="terminal_test_failure",
        failure_detail="terminal settings test failure",
        finished_at=datetime.now(timezone.utc),
    )
    importer = Mock()
    replay_listener, _replay_repository, _ = _durable_listener(tmp_path, importer=importer)

    asyncio.run(replay_listener.handle_bot_response(message))

    importer.import_message.assert_not_called()
    assert len(_receipt_rows(database_path, "discord_processing_attempts")) == 1
    assert _receipt_rows(database_path, "discord_source_events")[0]["status"] == terminal_status
    assert _import_event_rows(database_path, "server_settings") == []
    assert _receipt_rows(database_path, "server_settings_observations") == []
    assert _receipt_rows(database_path, "discord_projection_links") == []


def test_listener_settings_cleanup_error_after_durable_success_does_not_complete_failure(
    tmp_path, caplog
) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)
    repository.mark_processing_failure = Mock(wraps=repository.mark_processing_failure)
    listener._consume_context = Mock(side_effect=RuntimeError("cleanup unavailable"))
    caplog.set_level(logging.ERROR, logger="moa.discord")

    asyncio.run(listener.handle_bot_response(_durable_settings_message()))

    attempt = _receipt_rows(database_path, "discord_processing_attempts")[0]
    event = _receipt_rows(database_path, "discord_source_events")[0]
    assert attempt["status"] == "succeeded"
    assert event["status"] == "succeeded"
    repository.mark_processing_failure.assert_not_called()
    assert "Best-effort cleanup failed after durable settings success" in caplog.text


def test_listener_same_process_settings_duplicate_is_suppressed_before_attempt_work(tmp_path) -> None:
    listener, _repository, database_path = _durable_listener(tmp_path)
    importer = Mock(wraps=listener._importer)
    listener._importer = importer
    message = _durable_settings_message()

    asyncio.run(listener.handle_bot_response(message))
    asyncio.run(listener.handle_bot_response(message))

    importer.import_message.assert_called_once()
    assert len(_receipt_rows(database_path, "discord_processing_attempts")) == 1
    assert _receipt_rows(database_path, "discord_source_events")[0]["delivery_count"] == 2
    assert len(_import_event_rows(database_path, "server_settings")) == 1
    assert len(_receipt_rows(database_path, "server_settings_observations")) == 1
    assert len(_receipt_rows(database_path, "server_contexts")) == 1
    assert len(_receipt_rows(database_path, "discord_projection_links")) == 1


def test_listener_first_durable_infokl_coordinates_server_scoped_success(tmp_path) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)
    importer = Mock(wraps=listener._importer)
    listener._importer = importer
    repository.mark_processing_success = Mock(wraps=repository.mark_processing_success)

    asyncio.run(listener.handle_bot_response(_durable_infokl_message()))

    durable_context = importer.import_message.call_args.kwargs["durable_infokl_context"]
    assert durable_context.source_event_id == 1
    assert durable_context.attempt_id == 1
    assert durable_context.finished_at.tzinfo is not None
    assert durable_context.finished_at.utcoffset() is not None
    assert importer.import_message.call_args.args[3] is None
    repository.mark_processing_success.assert_not_called()
    assert len(_import_event_rows(database_path, "kakeraloot_settings")) == 1
    assert len(_receipt_rows(database_path, "kakeraloot_settings_observations")) == 1
    assert len(_receipt_rows(database_path, "server_contexts")) == 1
    assert len(_receipt_rows(database_path, "discord_projection_links")) == 1
    event = _receipt_rows(database_path, "discord_source_events")[0]
    attempt = _receipt_rows(database_path, "discord_processing_attempts")[0]
    assert (event["status"], event["legacy_import_event_id"]) == ("succeeded", 1)
    assert (attempt["attempt_number"], attempt["status"]) == (1, "succeeded")
    assert _server_attribution_rows(database_path) == [(1, "resolved", "Test Server")]


def test_listener_succeeded_infokl_restart_replays_without_live_context_or_duplicates(
    tmp_path,
) -> None:
    first_listener, _repository, database_path = _durable_listener(tmp_path)
    asyncio.run(first_listener.handle_bot_response(_durable_infokl_message()))

    replay_listener, replay_repository, _ = _durable_listener(tmp_path)
    importer = Mock(wraps=replay_listener._importer)
    replay_listener._importer = importer
    replay_repository.mark_processing_success = Mock(wraps=replay_repository.mark_processing_success)
    replay_repository.mark_processing_failure = Mock(wraps=replay_repository.mark_processing_failure)
    replay_message = _durable_infokl_message(interaction=False)

    asyncio.run(replay_listener.handle_bot_response(replay_message))

    durable_context = importer.import_message.call_args.kwargs["durable_infokl_context"]
    assert durable_context.source_event_id == 1
    assert durable_context.attempt_id is None
    assert durable_context.finished_at.tzinfo is not None
    assert replay_listener._contexts == {}
    assert replay_listener._pending_contexts == {}
    replay_repository.mark_processing_success.assert_not_called()
    replay_repository.mark_processing_failure.assert_not_called()
    assert len(_receipt_rows(database_path, "discord_processing_attempts")) == 1
    assert len(_import_event_rows(database_path, "kakeraloot_settings")) == 1
    assert len(_receipt_rows(database_path, "kakeraloot_settings_observations")) == 1
    assert len(_receipt_rows(database_path, "server_contexts")) == 1
    assert len(_receipt_rows(database_path, "discord_projection_links")) == 1
    assert _server_attribution_rows(database_path) == [(1, "resolved", "Test Server")]


def test_listener_retryable_infokl_coordinator_failure_retries_transactionally(tmp_path) -> None:
    listener, _repository, database_path = _durable_listener(tmp_path)
    coordinator = listener._importer._infokl_projection_coordinator
    coordinator.coordinate_infokl = Mock(side_effect=RuntimeError("infokl coordinator failed"))
    message = _durable_infokl_message()

    asyncio.run(listener.handle_bot_response(message))

    first_attempt = _receipt_rows(database_path, "discord_processing_attempts")[0]
    assert (first_attempt["status"], first_attempt["retryable"]) == ("failed", 1)
    assert _import_event_rows(database_path, "kakeraloot_settings") == []
    assert _receipt_rows(database_path, "kakeraloot_settings_observations") == []
    assert _receipt_rows(database_path, "server_contexts") == []
    assert _receipt_rows(database_path, "discord_projection_links") == []
    assert _server_attribution_rows(database_path) == [(1, "resolved", "Test Server")]

    retry_listener, _retry_repository, _ = _durable_listener(tmp_path)
    retry_message = _durable_infokl_message(interaction=False)
    asyncio.run(retry_listener.handle_bot_response(retry_message))

    attempts = _receipt_rows(database_path, "discord_processing_attempts")
    assert [(row["attempt_number"], row["status"]) for row in attempts] == [
        (1, "failed"),
        (2, "succeeded"),
    ]
    assert len(_import_event_rows(database_path, "kakeraloot_settings")) == 1
    assert len(_receipt_rows(database_path, "kakeraloot_settings_observations")) == 1
    assert len(_receipt_rows(database_path, "server_contexts")) == 1
    assert len(_receipt_rows(database_path, "discord_projection_links")) == 1


def test_listener_infokl_missing_attribution_does_not_start_processing(tmp_path) -> None:
    importer = Mock()
    listener, _repository, database_path = _attribution_listener(
        tmp_path,
        "infokl-missing.json",
        (),
        importer=importer,
    )

    asyncio.run(listener.handle_bot_response(_durable_infokl_message(interaction=False)))

    importer.import_message.assert_not_called()
    assert _receipt_rows(database_path, "discord_processing_attempts") == []
    assert _server_attribution_rows(database_path) == [(1, "unresolved", None)]
    assert listener._seen_payloads == set()


def test_listener_infokl_ambiguous_attribution_does_not_guess_server_or_account(tmp_path) -> None:
    importer = Mock()
    listener, _repository, database_path = _attribution_listener(
        tmp_path,
        "infokl-ambiguous.json",
        (
            ("Server A", "user_a", "primary", "456"),
            ("Server B", "user_b", "alt", "789"),
        ),
        importer=importer,
    )

    asyncio.run(listener.handle_bot_response(_durable_infokl_message(interaction=False)))

    importer.import_message.assert_not_called()
    assert _receipt_rows(database_path, "discord_processing_attempts") == []
    assert _server_attribution_rows(database_path) == [(1, "ambiguous", None)]


def test_listener_infokl_uses_unique_server_with_multiple_accounts_without_account_ownership(
    tmp_path,
) -> None:
    listener, _repository, database_path = _attribution_listener(
        tmp_path,
        "infokl-shared-channel.json",
        (
            ("Test Server", "user_a", "primary", "456"),
            ("Test Server", "user_b", "alt", "789"),
        ),
    )
    importer = Mock(wraps=listener._importer)
    listener._importer = importer

    asyncio.run(listener.handle_bot_response(_durable_infokl_message(interaction=False)))

    assert importer.import_message.call_args.args[2:] == ("Test Server", None)
    assert len(_import_event_rows(database_path, "kakeraloot_settings")) == 1
    assert len(_receipt_rows(database_path, "server_contexts")) == 1
    assert _server_attribution_rows(database_path) == [(1, "resolved", "Test Server")]


def test_listener_infokl_persisted_attribution_conflict_fails_closed(tmp_path) -> None:
    first_listener, _repository, database_path = _durable_listener(tmp_path)
    message = _durable_infokl_message()
    asyncio.run(first_listener.handle_bot_response(message))

    importer = Mock()
    conflict_listener, _conflict_repository, _ = _attribution_listener(
        tmp_path,
        "infokl-conflict.json",
        (
            ("Test Server", "user_a", "primary", "456"),
            ("Other Server", "user_b", "alt", "789"),
        ),
        importer=importer,
    )
    message.interaction_metadata = SimpleNamespace(
        name="infokl", user=SimpleNamespace(id=789)
    )

    asyncio.run(conflict_listener.handle_bot_response(message))

    importer.import_message.assert_not_called()
    assert len(_receipt_rows(database_path, "discord_processing_attempts")) == 1
    assert _server_attribution_rows(database_path) == [(1, "resolved", "Test Server")]
    assert len(_import_event_rows(database_path, "kakeraloot_settings")) == 1


def test_listener_active_infokl_does_not_start_attempt_or_import(tmp_path) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)
    message = _durable_infokl_message(interaction=False)
    received = listener._receive_message(message, message.content)
    assert received is not None
    attribution = listener._resolve_and_record_server_attribution(
        message,
        message.content,
        "infokl",
        None,
        None,
        received,
    )
    assert attribution is not None and attribution.status == "resolved"
    active = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="mudae-parser-v1",
        router_version="mudae-router-v1",
        started_at=datetime.now(timezone.utc),
    )
    importer = Mock()
    replay_listener, _replay_repository, _ = _durable_listener(tmp_path, importer=importer)

    asyncio.run(replay_listener.handle_bot_response(message))

    importer.import_message.assert_not_called()
    attempts = _receipt_rows(database_path, "discord_processing_attempts")
    assert len(attempts) == 1
    assert attempts[0]["id"] == active.attempt_id
    assert attempts[0]["status"] == "processing"


@pytest.mark.parametrize("terminal_status", ["failed", "unresolved_attribution"])
def test_listener_terminal_infokl_state_fails_closed(tmp_path, terminal_status) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)
    message = _durable_infokl_message(interaction=False)
    received = listener._receive_message(message, message.content)
    assert received is not None
    listener._resolve_and_record_server_attribution(
        message,
        message.content,
        "infokl",
        None,
        None,
        received,
    )
    attempt = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="mudae-parser-v1",
        router_version="mudae-router-v1",
        started_at=datetime.now(timezone.utc),
    )
    repository.mark_processing_failure(
        source_event_id=received.source_event_id,
        attempt_id=attempt.attempt_id,
        status=terminal_status,
        retryable=False,
        failure_code="terminal_infokl_test",
        failure_detail="terminal infokl test failure",
        finished_at=datetime.now(timezone.utc),
    )
    importer = Mock()
    replay_listener, _replay_repository, _ = _durable_listener(tmp_path, importer=importer)

    asyncio.run(replay_listener.handle_bot_response(message))

    importer.import_message.assert_not_called()
    assert len(_receipt_rows(database_path, "discord_processing_attempts")) == 1
    assert _receipt_rows(database_path, "discord_source_events")[0]["status"] == terminal_status


def test_listener_infokl_cleanup_error_preserves_coordinator_success(tmp_path, caplog) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)
    repository.mark_processing_failure = Mock(wraps=repository.mark_processing_failure)
    listener._complete_scan_if_last_page = Mock(side_effect=RuntimeError("cleanup unavailable"))
    caplog.set_level(logging.ERROR, logger="moa.discord")

    asyncio.run(listener.handle_bot_response(_durable_infokl_message()))

    attempt = _receipt_rows(database_path, "discord_processing_attempts")[0]
    event = _receipt_rows(database_path, "discord_source_events")[0]
    assert attempt["status"] == "succeeded"
    assert event["status"] == "succeeded"
    repository.mark_processing_failure.assert_not_called()
    assert "Best-effort cleanup failed after durable infokl success" in caplog.text


def test_listener_same_process_infokl_duplicate_is_suppressed_before_attempt_work(tmp_path) -> None:
    listener, _repository, database_path = _durable_listener(tmp_path)
    importer = Mock(wraps=listener._importer)
    listener._importer = importer
    message = _durable_infokl_message(interaction=False)

    asyncio.run(listener.handle_bot_response(message))
    asyncio.run(listener.handle_bot_response(message))

    importer.import_message.assert_called_once()
    assert len(_receipt_rows(database_path, "discord_processing_attempts")) == 1
    assert _receipt_rows(database_path, "discord_source_events")[0]["delivery_count"] == 2
    assert len(_import_event_rows(database_path, "kakeraloot_settings")) == 1


def test_listener_non_durable_infokl_keeps_direct_catalog_path_without_context(tmp_path) -> None:
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "Test Server",
        "user_a",
        discord_server_id="123",
        discord_user_id="456",
    )
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    importer = Mock(wraps=AutomaticImportService(catalog))
    listener = DiscordListenerService(
        config_service=config,
        catalog_service=SimpleNamespace(),
        importer=importer,
    )
    listener._mudae_user_id = 999

    asyncio.run(listener.handle_bot_response(_durable_infokl_message()))

    kwargs = importer.import_message.call_args.kwargs
    assert "durable_infokl_context" not in kwargs
    assert importer.import_message.call_args.args[3] is None
    assert len(_import_event_rows(tmp_path / "catalog.db", "kakeraloot_settings")) == 1
    assert len(_receipt_rows(tmp_path / "catalog.db", "kakeraloot_settings_observations")) == 1


def test_listener_settings_without_resolved_attribution_fails_closed(tmp_path) -> None:
    importer = Mock()
    listener, _repository, database_path = _attribution_listener(
        tmp_path,
        "settings-unresolved.json",
        (),
        importer=importer,
    )

    asyncio.run(listener.handle_bot_response(_durable_settings_message()))

    importer.import_message.assert_not_called()
    assert _server_attribution_rows(database_path) == [(1, "unresolved", None)]
    attempt = _receipt_rows(database_path, "discord_processing_attempts")[0]
    assert (attempt["status"], attempt["retryable"], attempt["failure_code"]) == (
        "unresolved_attribution",
        1,
        "unresolved_server_attribution",
    )


def test_listener_conflicting_persisted_settings_attribution_fails_closed(tmp_path) -> None:
    first_listener, _repository, database_path = _durable_listener(tmp_path)
    message = _durable_settings_message()
    asyncio.run(first_listener.handle_bot_response(message))

    importer = Mock()
    restarted_listener, _restarted_repository, _ = _attribution_listener(
        tmp_path,
        "settings-conflict.json",
        (
            ("Test Server", "user_a", "primary", "456"),
            ("Other Server", "user_b", "alt", "789"),
        ),
        importer=importer,
    )
    message.interaction_metadata = SimpleNamespace(
        name="settings", user=SimpleNamespace(id=789)
    )

    asyncio.run(restarted_listener.handle_bot_response(message))

    importer.import_message.assert_not_called()
    assert _server_attribution_rows(database_path) == [(1, "resolved", "Test Server")]
    assert len(_receipt_rows(database_path, "discord_processing_attempts")) == 1
    assert len(_import_event_rows(database_path, "server_settings")) == 1


def test_listener_non_durable_profile_keeps_direct_path_without_context(tmp_path) -> None:
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "Test Server",
        "user_a",
        discord_server_id="123",
        discord_user_id="456",
    )
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    importer = Mock(wraps=AutomaticImportService(catalog))
    listener = DiscordListenerService(
        config_service=config,
        catalog_service=SimpleNamespace(),
        importer=importer,
    )
    listener._mudae_user_id = 999

    asyncio.run(listener.handle_bot_response(_durable_profile_message()))

    kwargs = importer.import_message.call_args.kwargs
    assert "durable_profile_context" not in kwargs
    assert "durable_roll_context" not in kwargs
    assert len(_receipt_rows(tmp_path / "catalog.db", "profile_observations")) == 1


def test_listener_non_durable_settings_keeps_direct_path_without_context(tmp_path) -> None:
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "Test Server",
        "user_a",
        discord_server_id="123",
        discord_user_id="456",
    )
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    importer = Mock(wraps=AutomaticImportService(catalog))
    listener = DiscordListenerService(
        config_service=config,
        catalog_service=SimpleNamespace(),
        importer=importer,
    )
    listener._mudae_user_id = 999

    asyncio.run(listener.handle_bot_response(_durable_settings_message()))

    kwargs = importer.import_message.call_args.kwargs
    assert "durable_settings_context" not in kwargs
    assert len(_receipt_rows(tmp_path / "catalog.db", "server_settings_observations")) == 1


def test_listener_same_process_profile_duplicate_is_suppressed_before_import(tmp_path) -> None:
    listener, _repository, database_path = _durable_listener(tmp_path)
    importer = Mock(wraps=listener._importer)
    listener._importer = importer
    message = _durable_profile_message()

    asyncio.run(listener.handle_bot_response(message))
    asyncio.run(listener.handle_bot_response(message))

    assert importer.import_message.call_count == 1
    assert len(_receipt_rows(database_path, "discord_processing_attempts")) == 1
    assert len(_receipt_rows(database_path, "profile_observations")) == 1


def test_listener_receives_edit_as_new_revision_under_same_aggregate(tmp_path) -> None:
    listener, _repository, database_path = _durable_listener(tmp_path)
    original = _durable_roll_message()
    edited_at = datetime(2026, 7, 19, 20, 0, tzinfo=timezone.utc)
    edited = _durable_roll_message(
        content="Miku Nakano\nThe Quintessential Quintuplets\n44:kakera:\nMiku Nakano / The Quintessential Quintuplets - 44 ka",
        edited_at=edited_at,
    )

    asyncio.run(listener.handle_bot_response(original))
    asyncio.run(listener.handle_message_edit(original, edited))

    aggregates = _receipt_rows(database_path, "discord_message_aggregates")
    revisions = _receipt_rows(database_path, "discord_message_revisions")
    events = _receipt_rows(database_path, "discord_source_events")
    assert len(aggregates) == 1
    assert len(revisions) == len(events) == 2
    assert revisions[0]["aggregate_id"] == revisions[1]["aggregate_id"]
    assert revisions[0]["id"] != revisions[1]["id"]
    assert revisions[1]["source_observed_at"] == edited_at.isoformat()
    assert [event["event_kind"] for event in events] == [
        "message_revision",
        "message_revision",
    ]
    attempts = _receipt_rows(database_path, "discord_processing_attempts")
    assert [attempt["attempt_number"] for attempt in attempts] == [1, 1]
    assert [attempt["status"] for attempt in attempts] == ["succeeded", "succeeded"]


def test_listener_duplicate_delivery_updates_receipt_but_stays_process_local_suppressed(
    tmp_path,
) -> None:
    listener, _repository, database_path = _durable_listener(tmp_path)
    message = _durable_roll_message()

    asyncio.run(listener.handle_bot_response(message))
    first_aggregate_id = _receipt_rows(database_path, "discord_message_aggregates")[0]["id"]
    first_revision_id = _receipt_rows(database_path, "discord_message_revisions")[0]["id"]
    first_event_id = _receipt_rows(database_path, "discord_source_events")[0]["id"]
    asyncio.run(listener.handle_bot_response(message))

    assert _receipt_rows(database_path, "discord_message_aggregates")[0]["id"] == first_aggregate_id
    assert _receipt_rows(database_path, "discord_message_revisions")[0]["id"] == first_revision_id
    events = _receipt_rows(database_path, "discord_source_events")
    assert len(events) == 1
    assert events[0]["id"] == first_event_id
    assert events[0]["delivery_count"] == 2
    assert len(_receipt_rows(database_path, "discord_message_revisions")) == 1
    assert len(_import_event_rows(database_path, "roll")) == 1
    attempts = _receipt_rows(database_path, "discord_processing_attempts")
    assert len(attempts) == 1
    assert attempts[0]["status"] == "succeeded"


def test_listener_receives_duplicate_before_seen_payload_suppression(tmp_path) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)
    seen_sizes: list[int] = []
    original_receive = repository.receive_message

    def receive_message(**kwargs):
        seen_sizes.append(len(listener._seen_payloads))
        return original_receive(**kwargs)

    repository.receive_message = receive_message
    message = _durable_roll_message()
    asyncio.run(listener.handle_bot_response(message))
    asyncio.run(listener.handle_bot_response(message))

    assert seen_sizes == [0, 1]
    assert _receipt_rows(database_path, "discord_source_events")[0]["delivery_count"] == 2


def test_listener_reconstruction_reuses_durable_identity_and_replays_projection(tmp_path) -> None:
    first_listener, _repository, database_path = _durable_listener(tmp_path)
    message = _durable_roll_message()
    asyncio.run(first_listener.handle_bot_response(message))
    first_event = _receipt_rows(database_path, "discord_source_events")[0]

    config = ConfigService(tmp_path / "config.json")
    catalog = CatalogService(CatalogRepository(database_path))
    restarted_listener = DiscordListenerService(
        config_service=config,
        catalog_service=catalog,
        discord_message_repository=DiscordMessageRepository(database_path),
    )
    restarted_listener._mudae_user_id = 999
    asyncio.run(restarted_listener.handle_bot_response(message))

    event = _receipt_rows(database_path, "discord_source_events")[0]
    assert event["id"] == first_event["id"]
    assert event["delivery_count"] == 2
    assert len(_import_event_rows(database_path, "roll")) == 1


def test_listener_restart_replay_is_not_treated_as_projection_idempotent(tmp_path) -> None:
    first_listener, _repository, database_path = _durable_listener(tmp_path)
    message = _durable_roll_message()
    asyncio.run(first_listener.handle_bot_response(message))

    importer = Mock()
    importer.import_message.return_value = SimpleNamespace(message="imported")
    config = ConfigService(tmp_path / "config.json")
    restarted_listener = DiscordListenerService(
        config_service=config,
        catalog_service=CatalogService(CatalogRepository(database_path)),
        importer=importer,
        discord_message_repository=DiscordMessageRepository(database_path),
    )
    restarted_listener._mudae_user_id = 999
    asyncio.run(restarted_listener.handle_bot_response(message))

    importer.import_message.assert_called_once()
    assert _receipt_rows(database_path, "discord_source_events")[0]["status"] == "succeeded"


def test_listener_ignored_bot_message_does_not_create_durable_rows(tmp_path) -> None:
    listener, _repository, database_path = _durable_listener(tmp_path)

    asyncio.run(listener.handle_bot_response(_durable_roll_message(author_id=998)))

    assert _receipt_rows(database_path, "discord_message_aggregates") == []
    assert _receipt_rows(database_path, "discord_message_revisions") == []
    assert _receipt_rows(database_path, "discord_source_events") == []


def test_listener_receipt_failure_prevents_downstream_calls_and_seen_state(tmp_path) -> None:
    importer = Mock()
    importer.import_message.return_value = SimpleNamespace(message="imported")
    listener, repository, database_path = _durable_listener(tmp_path, importer=importer)

    def fail_receive(**_kwargs):
        raise RuntimeError("receive unavailable")

    repository.receive_message = fail_receive
    helper_names = (
        "_context_from_active_scan",
        "_context_from_interaction",
        "_context_from_reaction_receipt",
        "_context_from_pending_workflow",
        "_context_from_active_roll",
        "_resolve_message_kind",
    )
    helper_spies = {name: Mock(wraps=getattr(listener, name)) for name in helper_names}
    for name, spy in helper_spies.items():
        setattr(listener, name, spy)
    router_detect = Mock(wraps=listener._router.detect)
    listener._router.detect = router_detect
    asyncio.run(listener.handle_bot_response(_durable_roll_message()))

    for spy in helper_spies.values():
        spy.assert_not_called()
    router_detect.assert_not_called()
    importer.import_message.assert_not_called()
    assert listener._seen_payloads == set()
    assert _receipt_rows(database_path, "discord_source_events") == []

    repository.receive_message = DiscordMessageRepository(database_path).receive_message
    listener._resolve_message_kind = DiscordListenerService._resolve_message_kind.__get__(listener)
    asyncio.run(listener.handle_bot_response(_durable_roll_message()))
    importer.import_message.assert_called_once()


def test_listener_receipt_failure_precedes_active_scan_router_detection(tmp_path) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)

    repository.receive_message = Mock(side_effect=RuntimeError("receive unavailable"))
    listener._scan_contexts[(123, 900, "harem")] = object()
    listener._router.detect = Mock(side_effect=AssertionError("router ran before receipt"))

    asyncio.run(listener.handle_bot_response(_durable_roll_message()))

    listener._router.detect.assert_not_called()
    assert _receipt_rows(database_path, "discord_source_events") == []


def test_listener_receipt_failure_precedes_pending_workflow_resolution(tmp_path) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)

    repository.receive_message = Mock(side_effect=RuntimeError("receive unavailable"))
    listener._pending_contexts[("123", 900, "456")] = object()
    listener._resolve_message_kind = Mock(
        side_effect=AssertionError("pending workflow resolved before receipt")
    )

    asyncio.run(listener.handle_bot_response(_durable_roll_message()))

    listener._resolve_message_kind.assert_not_called()
    assert _receipt_rows(database_path, "discord_source_events") == []


def test_listener_receipt_failure_precedes_attribution_context_helpers(tmp_path) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)

    repository.receive_message = Mock(side_effect=RuntimeError("receive unavailable"))
    listener._context_from_interaction = Mock(
        side_effect=AssertionError("attribution context resolved before receipt")
    )
    listener._config.identity_for_discord_server_account = Mock(
        side_effect=AssertionError("account attribution ran before receipt")
    )

    asyncio.run(listener.handle_bot_response(_durable_roll_message()))

    listener._context_from_interaction.assert_not_called()
    listener._config.identity_for_discord_server_account.assert_not_called()
    assert _receipt_rows(database_path, "discord_source_events") == []


def test_listener_successful_receipt_precedes_context_and_router_helpers(tmp_path) -> None:
    importer = Mock()
    importer.import_message.return_value = SimpleNamespace(message="imported")
    listener, repository, database_path = _durable_listener(tmp_path, importer=importer)
    order: list[str] = []
    original_receive = repository.receive_message

    def receive_message(**kwargs):
        order.append("receive")
        return original_receive(**kwargs)

    repository.receive_message = receive_message
    router_detect = listener._router.detect
    listener._router.detect = Mock(
        side_effect=lambda *args, **kwargs: (
            order.append("router"),
            router_detect(*args, **kwargs),
        )[1]
    )
    for name in (
        "_context_from_active_scan",
        "_context_from_interaction",
        "_context_from_reaction_receipt",
        "_context_from_pending_workflow",
        "_context_from_active_roll",
        "_resolve_message_kind",
    ):
        original_helper = getattr(listener, name)
        setattr(
            listener,
            name,
            Mock(
                side_effect=lambda *args, _name=name, _helper=original_helper, **kwargs: (
                    order.append(_name),
                    _helper(*args, **kwargs),
                )[1]
            ),
        )

    asyncio.run(listener.handle_bot_response(_durable_roll_message()))

    assert order[0] == "receive"
    assert order.index("receive") < order.index("_context_from_interaction")
    assert order.index("receive") < order.index("router")
    assert _receipt_rows(database_path, "discord_source_events")[0]["event_kind"] == (
        "message_revision"
    )
    importer.import_message.assert_called_once()


def test_listener_create_and_edit_callbacks_use_message_revision_event_kind(tmp_path) -> None:
    listener, _repository, database_path = _durable_listener(tmp_path)
    original = _durable_roll_message()
    edited = _durable_roll_message(
        edited_at=datetime(2026, 7, 19, 20, 1, tzinfo=timezone.utc),
    )

    asyncio.run(listener.handle_bot_response(original))
    asyncio.run(listener.handle_message_edit(original, edited))

    assert [row["event_kind"] for row in _receipt_rows(database_path, "discord_source_events")] == [
        "message_revision",
        "message_revision",
    ]


def test_listener_receipt_creates_processing_attempt_after_deduplication(tmp_path) -> None:
    listener, _repository, database_path = _durable_listener(tmp_path)

    asyncio.run(listener.handle_bot_response(_durable_roll_message()))

    attempts = _receipt_rows(database_path, "discord_processing_attempts")
    assert len(attempts) == 1
    assert attempts[0]["status"] == "succeeded"


def test_listener_zero_projection_observation_still_succeeds(tmp_path) -> None:
    importer = Mock()
    importer.import_message.return_value = SimpleNamespace(message="observed without projections")
    listener, _repository, database_path = _durable_listener(tmp_path, importer=importer)

    asyncio.run(listener.handle_bot_response(_durable_roll_message()))

    importer.import_message.assert_called_once()
    assert _import_event_rows(database_path, "roll") == []
    attempt = _receipt_rows(database_path, "discord_processing_attempts")[0]
    event = _receipt_rows(database_path, "discord_source_events")[0]
    assert attempt["status"] == "succeeded"
    assert event["status"] == "succeeded"


def test_listener_downstream_exception_records_retryable_failure(tmp_path) -> None:
    importer = Mock()
    importer.import_message.side_effect = ValueError("importer exploded")
    listener, _repository, database_path = _durable_listener(tmp_path, importer=importer)

    asyncio.run(listener.handle_bot_response(_durable_roll_message()))

    attempt = _receipt_rows(database_path, "discord_processing_attempts")[0]
    event = _receipt_rows(database_path, "discord_source_events")[0]
    assert attempt["status"] == "failed"
    assert attempt["retryable"] == 1
    assert attempt["failure_code"] == "downstream_processing_error"
    assert attempt["failure_detail"] == "importer exploded"
    assert event["status"] == "failed"


def test_listener_retryable_failure_replay_creates_attempt_two(tmp_path) -> None:
    first_listener, repository, database_path = _durable_listener(tmp_path)
    coordinator = first_listener._importer._roll_projection_coordinator
    original_coordinate_roll = coordinator.coordinate_roll
    coordinator.coordinate_roll = Mock(side_effect=RuntimeError("temporary coordinator failure"))
    message = _durable_roll_message()
    asyncio.run(first_listener.handle_bot_response(message))

    failed_attempt = _receipt_rows(database_path, "discord_processing_attempts")[0]
    assert failed_attempt["status"] == "failed"
    assert failed_attempt["retryable"] == 1
    assert _receipt_rows(database_path, "import_events") == []

    second_listener, second_repository, _ = _durable_listener(tmp_path)
    second_listener._importer._roll_projection_coordinator.coordinate_roll = (
        original_coordinate_roll
    )
    asyncio.run(second_listener.handle_bot_response(message))

    attempts = _receipt_rows(database_path, "discord_processing_attempts")
    assert [attempt["attempt_number"] for attempt in attempts] == [1, 2]
    assert [attempt["status"] for attempt in attempts] == ["failed", "succeeded"]
    assert _receipt_rows(database_path, "discord_source_events")[0]["status"] == "succeeded"
    assert len(_receipt_rows(database_path, "import_events")) == 1
    assert len(_receipt_rows(database_path, "roll_observations")) == 1
    assert len(_receipt_rows(database_path, "discord_projection_links")) == 2
    assert second_repository.mark_processing_success is not None


def test_listener_same_process_duplicate_creates_no_new_attempt(tmp_path) -> None:
    listener, _repository, database_path = _durable_listener(tmp_path)
    message = _durable_roll_message()

    asyncio.run(listener.handle_bot_response(message))
    asyncio.run(listener.handle_bot_response(message))

    attempts = _receipt_rows(database_path, "discord_processing_attempts")
    events = _receipt_rows(database_path, "discord_source_events")
    assert len(attempts) == 1
    assert attempts[0]["status"] == "succeeded"
    assert events[0]["delivery_count"] == 2


def test_listener_succeeded_restart_replay_replays_without_new_attempt(tmp_path) -> None:
    first_listener, _repository, database_path = _durable_listener(tmp_path)
    message = _durable_roll_message()
    asyncio.run(first_listener.handle_bot_response(message))

    importer = Mock()
    importer.import_message.return_value = SimpleNamespace(
        message="replayed",
        replay_skipped=True,
        durable_success_recorded=True,
    )
    restarted_listener = DiscordListenerService(
        config_service=ConfigService(tmp_path / "config.json"),
        catalog_service=CatalogService(CatalogRepository(database_path)),
        importer=importer,
        discord_message_repository=DiscordMessageRepository(database_path),
    )
    restarted_listener._mudae_user_id = 999
    asyncio.run(restarted_listener.handle_bot_response(message))

    attempts = _receipt_rows(database_path, "discord_processing_attempts")
    event = _receipt_rows(database_path, "discord_source_events")[0]
    assert len(attempts) == 1
    assert attempts[0]["status"] == "succeeded"
    assert event["status"] == "succeeded"
    importer.import_message.assert_called_once()


def test_listener_succeeded_restart_replay_passes_none_attempt_and_skips_projection(
    tmp_path, caplog
) -> None:
    first_listener, _repository, database_path = _durable_listener(tmp_path)
    message = _durable_roll_message()
    asyncio.run(first_listener.handle_bot_response(message))

    restarted_listener, _restarted_repository, _ = _durable_listener(tmp_path)
    importer = Mock(wraps=restarted_listener._importer)
    restarted_listener._importer = importer
    caplog.set_level(logging.INFO, logger="moa.discord")
    asyncio.run(restarted_listener.handle_bot_response(message))

    durable_context = importer.import_message.call_args.kwargs["durable_roll_context"]
    assert durable_context.source_event_id == 1
    assert durable_context.attempt_id is None
    assert durable_context.finished_at.tzinfo is not None
    assert "Skipped duplicate durable roll projection" in caplog.text
    assert len(_receipt_rows(database_path, "import_events")) == 1
    assert len(_receipt_rows(database_path, "roll_observations")) == 1
    assert len(_receipt_rows(database_path, "discord_projection_links")) == 2


def test_listener_nonretryable_replay_replays_without_new_attempt(tmp_path) -> None:
    importer = Mock()
    importer.import_message.side_effect = RuntimeError("first failure")
    listener, repository, database_path = _durable_listener(tmp_path, importer=importer)
    message = _durable_roll_message()
    asyncio.run(listener.handle_bot_response(message))

    started_at = datetime.now(timezone.utc)
    active = repository.begin_processing_attempt(
        source_event_id=1,
        parser_version="mudae-parser-v1",
        router_version="mudae-router-v1",
        started_at=started_at,
    )
    repository.mark_processing_failure(
        source_event_id=1,
        attempt_id=active.attempt_id,
        status="failed",
        retryable=False,
        failure_code="terminal_test_failure",
        failure_detail="terminal test failure",
        finished_at=started_at + timedelta(microseconds=1),
    )

    replay_importer = Mock()
    replay_importer.import_message.return_value = SimpleNamespace(message="replayed")
    replay_listener = DiscordListenerService(
        config_service=ConfigService(tmp_path / "config.json"),
        catalog_service=CatalogService(CatalogRepository(database_path)),
        importer=replay_importer,
        discord_message_repository=DiscordMessageRepository(database_path),
    )
    replay_listener._mudae_user_id = 999
    asyncio.run(replay_listener.handle_bot_response(message))

    attempts = _receipt_rows(database_path, "discord_processing_attempts")
    event = _receipt_rows(database_path, "discord_source_events")[0]
    assert len(attempts) == 2
    assert attempts[-1]["status"] == "failed"
    assert attempts[-1]["retryable"] == 0
    assert event["status"] == "failed"
    replay_importer.import_message.assert_not_called()


def test_listener_active_processing_replay_does_not_complete_or_start_attempt(tmp_path) -> None:
    importer = Mock()
    importer.import_message.side_effect = RuntimeError("first failure")
    listener, repository, database_path = _durable_listener(tmp_path, importer=importer)
    message = _durable_roll_message()
    asyncio.run(listener.handle_bot_response(message))

    active = repository.begin_processing_attempt(
        source_event_id=1,
        parser_version="mudae-parser-v1",
        router_version="mudae-router-v1",
        started_at=datetime.now(timezone.utc),
    )
    replay_importer = Mock()
    replay_importer.import_message.return_value = SimpleNamespace(message="replayed")
    replay_listener = DiscordListenerService(
        config_service=ConfigService(tmp_path / "config.json"),
        catalog_service=CatalogService(CatalogRepository(database_path)),
        importer=replay_importer,
        discord_message_repository=DiscordMessageRepository(database_path),
    )
    replay_listener._mudae_user_id = 999
    asyncio.run(replay_listener.handle_bot_response(message))

    attempts = _receipt_rows(database_path, "discord_processing_attempts")
    event = _receipt_rows(database_path, "discord_source_events")[0]
    assert len(attempts) == 2
    assert attempts[-1]["id"] == active.attempt_id
    assert attempts[-1]["status"] == "processing"
    assert event["status"] == "processing"
    replay_importer.import_message.assert_not_called()


def test_listener_begin_persistence_failure_prevents_downstream_work(tmp_path) -> None:
    importer = Mock()
    importer.import_message.return_value = SimpleNamespace(message="should not run")
    listener, repository, database_path = _durable_listener(tmp_path, importer=importer)
    repository.begin_processing_attempt = Mock(side_effect=RuntimeError("begin unavailable"))

    asyncio.run(listener.handle_bot_response(_durable_roll_message()))

    importer.import_message.assert_not_called()
    assert _receipt_rows(database_path, "discord_processing_attempts") == []
    assert _receipt_rows(database_path, "discord_source_events")[0]["status"] == "received"


def test_listener_coordinator_owns_success_completion(tmp_path) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)
    repository.mark_processing_success = Mock(side_effect=RuntimeError("success persistence failed"))

    asyncio.run(listener.handle_bot_response(_durable_roll_message()))

    attempt = _receipt_rows(database_path, "discord_processing_attempts")[0]
    event = _receipt_rows(database_path, "discord_source_events")[0]
    assert attempt["status"] == "succeeded"
    assert event["status"] == "succeeded"
    repository.mark_processing_success.assert_not_called()


def test_listener_cleanup_failure_after_durable_success_does_not_complete_failure(
    tmp_path, caplog
) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)
    repository.mark_processing_failure = Mock(wraps=repository.mark_processing_failure)
    listener._consume_context = Mock(side_effect=RuntimeError("cleanup unavailable"))
    caplog.set_level(logging.ERROR, logger="moa.discord")

    asyncio.run(listener.handle_bot_response(_durable_roll_message()))

    attempt = _receipt_rows(database_path, "discord_processing_attempts")[0]
    event = _receipt_rows(database_path, "discord_source_events")[0]
    assert attempt["status"] == "succeeded"
    assert event["status"] == "succeeded"
    repository.mark_processing_failure.assert_not_called()
    assert "Best-effort cleanup failed after durable roll success" in caplog.text


def test_listener_non_roll_keeps_listener_managed_success_without_durable_context(
    tmp_path,
) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)
    importer = Mock(wraps=listener._importer)
    listener._importer = importer
    repository.mark_processing_success = Mock(wraps=repository.mark_processing_success)
    message = SimpleNamespace(
        id=1210,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=True, id=999),
        interaction_metadata=SimpleNamespace(name="k", user=SimpleNamespace(id=456)),
        content="You have 12,114 :kakera:!\nBronze IV · Max reached!",
        embeds=(),
    )

    asyncio.run(listener.handle_bot_response(message))

    assert "durable_roll_context" not in importer.import_message.call_args.kwargs
    assert "durable_claim_context" not in importer.import_message.call_args.kwargs
    repository.mark_processing_success.assert_called_once()
    attempt = _receipt_rows(database_path, "discord_processing_attempts")[0]
    assert attempt["status"] == "succeeded"


def test_listener_failure_completion_failure_preserves_original_processing_error(
    tmp_path, caplog
) -> None:
    importer = Mock()
    importer.import_message.side_effect = ValueError("original processing error")
    listener, repository, database_path = _durable_listener(tmp_path, importer=importer)
    repository.mark_processing_failure = Mock(side_effect=RuntimeError("failure persistence failed"))
    caplog.set_level(logging.WARNING, logger="moa.discord")

    asyncio.run(listener.handle_bot_response(_durable_roll_message()))

    attempt = _receipt_rows(database_path, "discord_processing_attempts")[0]
    event = _receipt_rows(database_path, "discord_source_events")[0]
    assert attempt["status"] == "processing"
    assert event["status"] == "processing"
    assert "original processing error" in caplog.text
    assert "failure persistence failed" in caplog.text


def test_listener_early_attribution_return_creates_no_attempt(tmp_path) -> None:
    listener, _catalog = _listener_with_two_configured_users(tmp_path)
    listener._mudae_user_id = 999
    message = _durable_roll_message()
    message.interaction_metadata = None

    asyncio.run(listener.handle_bot_response(message))

    assert _receipt_rows(tmp_path / "catalog.db", "discord_source_events")
    assert _receipt_rows(tmp_path / "catalog.db", "discord_processing_attempts") == []


def test_listener_early_classification_return_creates_no_attempt(tmp_path) -> None:
    listener, _repository, database_path = _durable_listener(tmp_path)
    message = _durable_roll_message(content="This is not a Mudae response")

    asyncio.run(listener.handle_bot_response(message))

    assert _receipt_rows(database_path, "discord_source_events")
    assert _receipt_rows(database_path, "discord_processing_attempts") == []


def test_listener_characterizes_reaction_acknowledgement_identity_and_attribution(
    tmp_path,
) -> None:
    listener, catalog = _listener_with_two_configured_users(tmp_path)
    listener._mudae_user_id = 999
    command = SimpleNamespace(
        id=1203,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=False, id=456),
        content="$persr 2",
    )
    acknowledgement = SimpleNamespace(
        guild_id=123,
        user_id=999,
        channel_id=900,
        message_id=1203,
        # Sanitized real Discord/Mudae reaction payload: Mudae's success reaction.
        emoji=SimpleNamespace(name="white_check_mark"),
    )

    asyncio.run(listener.handle_message(command))
    asyncio.run(listener.handle_raw_reaction_add(acknowledgement))
    asyncio.run(listener.handle_raw_reaction_add(acknowledgement))

    rows = _import_event_rows(tmp_path / "catalog.db", "personal_rare")
    state = catalog.personal_rare("Test Server", "user_a")
    assert len(rows) == 1
    assert rows[0][0] == (
        "discord:guild=123:channel=900:message=1203:reaction=white_check_mark"
    )
    assert state is not None
    assert state.personal_rare_multiplier == 2
    assert catalog.personal_rare("Test Server", "user_b") is None
    assert _receipt_rows(tmp_path / "catalog.db", "discord_processing_attempts") == []


def test_listener_replay_after_restart_skips_the_existing_projection(
    tmp_path,
) -> None:
    config_path = tmp_path / "config.json"
    database_path = tmp_path / "catalog.db"
    config = ConfigService(config_path)
    config.add_account(
        "Test Server",
        "user_a",
        discord_server_id="123",
        discord_user_id="456",
    )
    first_catalog = CatalogService(CatalogRepository(database_path))
    first_importer, first_discord_repository = _durable_importer_for(
        first_catalog,
        database_path,
    )
    first_listener = DiscordListenerService(
        config_service=config,
        catalog_service=first_catalog,
        importer=first_importer,
        discord_message_repository=first_discord_repository,
    )
    first_listener._mudae_user_id = 999
    event = SimpleNamespace(
        id=1204,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=True, id=999),
        interaction_metadata=SimpleNamespace(name="wa", user=SimpleNamespace(id=456)),
        # Sanitized real Discord/Mudae output: the event replayed after reconstruction.
        content=(
            "Berry (YD)\nYurei Deco\n28:kakera:\n"
            "Berry (YD) / Yurei Deco - 28 ka"
        ),
        embeds=(),
    )

    asyncio.run(first_listener.handle_bot_response(event))
    asyncio.run(first_listener.handle_bot_response(event))
    assert len(_import_event_rows(database_path, "roll")) == 1

    restarted_catalog = CatalogService(CatalogRepository(database_path))
    restarted_importer, restarted_discord_repository = _durable_importer_for(
        restarted_catalog,
        database_path,
    )
    restarted_listener = DiscordListenerService(
        config_service=ConfigService(config_path),
        catalog_service=restarted_catalog,
        importer=restarted_importer,
        discord_message_repository=restarted_discord_repository,
    )
    restarted_listener._mudae_user_id = 999
    asyncio.run(restarted_listener.handle_bot_response(event))

    rows = _import_event_rows(database_path, "roll")
    assert len(rows) == 1
    assert [row[0] for row in rows] == ["discord:guild=123:channel=900:message=1204"]
    assert len(restarted_catalog.recent_rolls("Test Server", "user_a", 10)) == 1


def test_listener_pending_workflow_is_lost_after_restart(
    tmp_path,
) -> None:
    config_path = tmp_path / "config.json"
    database_path = tmp_path / "catalog.db"
    config = ConfigService(config_path)
    config.add_account(
        "Test Server",
        "user_a",
        discord_server_id="123",
        discord_user_id="456",
    )
    first_listener = DiscordListenerService(
        config_service=config,
        catalog_service=CatalogService(CatalogRepository(database_path)),
    )
    command = SimpleNamespace(
        id=1205,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=False, id=456),
        content="$divorce Professor Layton",
    )
    asyncio.run(first_listener.handle_message(command))
    assert first_listener._pending_contexts

    restarted_listener = DiscordListenerService(
        config_service=ConfigService(config_path),
        catalog_service=CatalogService(CatalogRepository(database_path)),
    )
    prompt = SimpleNamespace(
        id=1206,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=True, id=999),
        # Sanitized real Discord/Mudae output: the pending confirmation prompt.
        content=(
            "Professor Layton: Do you confirm the divorce? (y/n/yes/no)\n"
            "Characters divorced by $divorce are also removed from the $restorelist "
            "(+54:kakera:if you confirm)"
        ),
        embeds=(),
    )
    answer = SimpleNamespace(
        id=1207,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=False, id=456),
        content="yes",
    )
    complete = SimpleNamespace(
        id=1208,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=900),
        author=SimpleNamespace(bot=True, id=999),
        # Sanitized real Discord/Mudae output: completion after the lost workflow.
        content="Professor Layton and user_a are now divorced. (+54:kakera:)",
        embeds=(),
    )

    asyncio.run(restarted_listener.handle_bot_response(prompt))
    asyncio.run(restarted_listener.handle_message(answer))
    asyncio.run(restarted_listener.handle_bot_response(complete))

    assert restarted_listener._pending_contexts == {}
    assert _import_event_rows(database_path, "divorce") == []
    assert _import_event_rows(database_path, "divorce_complete") == []


def test_listener_recovers_context_from_mudae_interaction_response(tmp_path) -> None:
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "Lake Arrowhead 2025",
        "ernieuuu",
        discord_server_id="123",
        discord_user_id="456",
    )
    listener = DiscordListenerService(
        config_service=config,
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db")),
    )
    message = SimpleNamespace(
        id=987,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
        interaction_metadata=SimpleNamespace(
            name="wa",
            user=SimpleNamespace(id=456),
        ),
    )

    context = listener._context_from_interaction(message)

    assert context is not None
    assert context.identity.account == "ernieuuu"
    assert context.expected_kind == "roll"


def test_listener_recovers_context_from_legacy_mudae_interaction_response(tmp_path) -> None:
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "Lake Arrowhead 2025",
        "ernieuuu",
        discord_server_id="123",
        discord_user_id="456",
    )
    listener = DiscordListenerService(
        config_service=config,
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db")),
    )
    message = SimpleNamespace(
        id=987,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
        interaction_metadata=None,
        interaction=SimpleNamespace(
            command=SimpleNamespace(name="wa"),
            user=SimpleNamespace(id=456),
        ),
    )

    context = listener._context_from_interaction(message)

    assert context is not None
    assert context.identity.account == "ernieuuu"
    assert context.expected_kind == "roll"


def test_listener_uses_active_account_for_untagged_slash_roll(tmp_path) -> None:
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "Lake Arrowhead 2025",
        "ernieuuu",
        discord_server_id="123",
        discord_user_id="456",
    )
    config.use_identity_ids("123", "456")
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    importer, discord_repository = _durable_importer_for(catalog, tmp_path / "catalog.db")
    listener = DiscordListenerService(
        config_service=config,
        catalog_service=catalog,
        importer=importer,
        discord_message_repository=discord_repository,
    )
    listener._mudae_user_id = 999
    response = SimpleNamespace(
        id=987,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
        author=SimpleNamespace(bot=True, id=999),
        content=(
            "Berry (YD)\nYurei Deco\n28:kakera:\n"
            "Berry (YD) / Yurei Deco - 28 ka"
        ),
        embeds=(),
    )

    asyncio.run(listener.handle_bot_response(response))

    rolls = catalog.recent_rolls("Lake Arrowhead 2025", "ernieuuu", 1)
    assert len(rolls) == 1
    assert rolls[0].character.name == "Berry (YD)"


def test_listener_does_not_guess_between_multiple_active_server_accounts(tmp_path) -> None:
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "Lake Arrowhead 2025",
        "ernieuuu",
        discord_server_id="123",
        discord_user_id="456",
    )
    config.add_account(
        "Lake Arrowhead 2025",
        "cute_beagle_91130",
        role="alt",
        discord_server_id="123",
        discord_user_id="789",
    )
    config.use_identity_ids("123", "456")
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    importer, discord_repository = _durable_importer_for(catalog, tmp_path / "catalog.db")
    listener = DiscordListenerService(
        config_service=config,
        catalog_service=catalog,
        importer=importer,
        discord_message_repository=discord_repository,
    )
    listener._mudae_user_id = 999
    response = SimpleNamespace(
        id=987,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
        author=SimpleNamespace(bot=True, id=999),
        content=(
            "Berry (YD)\nYurei Deco\n28:kakera:\n"
            "Berry (YD) / Yurei Deco - 28 ka"
        ),
        embeds=(),
    )

    asyncio.run(listener.handle_bot_response(response))

    assert catalog.recent_rolls("Lake Arrowhead 2025", "ernieuuu", 1) == ()
    assert catalog.recent_rolls("Lake Arrowhead 2025", "cute_beagle_91130", 1) == ()


def test_listener_attributes_metadata_only_slash_roll_to_the_metadata_user(tmp_path) -> None:
    config = ConfigService(tmp_path / "config.json")
    config.add_account(
        "Lake Arrowhead 2025",
        "ernieuuu",
        discord_server_id="123",
        discord_user_id="456",
    )
    config.add_account(
        "Lake Arrowhead 2025",
        "cute_beagle_91130",
        role="alt",
        discord_server_id="123",
        discord_user_id="789",
    )
    database_path = tmp_path / "catalog.db"
    catalog = CatalogService(CatalogRepository(database_path))
    importer, discord_repository = _durable_importer_for(catalog, database_path)
    listener = DiscordListenerService(
        config_service=config,
        catalog_service=catalog,
        importer=importer,
        discord_message_repository=discord_repository,
    )
    listener._mudae_user_id = 999
    response = SimpleNamespace(
        id=987,
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=789),
        author=SimpleNamespace(bot=True, id=999),
        interaction_metadata=SimpleNamespace(user=SimpleNamespace(id=789)),
        content=(
            "Berry (YD)\nYurei Deco\n28:kakera:\n"
            "Berry (YD) / Yurei Deco - 28 ka"
        ),
        embeds=(),
    )

    asyncio.run(listener.handle_bot_response(response))

    rolls = catalog.recent_rolls("Lake Arrowhead 2025", "cute_beagle_91130", 1)
    assert len(rolls) == 1
    assert rolls[0].character.name == "Berry (YD)"
    assert catalog.recent_rolls("Lake Arrowhead 2025", "ernieuuu", 1) == ()


def test_listener_presence_uses_watching_status_and_truncates_custom_text(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db")),
        status_text="  Tracking Mudae data  ",
    )

    activity = listener.presence_activity()

    assert activity.type.value == 3
    assert activity.name == "Tracking Mudae data"


def test_listener_first_durable_claim_uses_coordinator_owned_success(tmp_path) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)
    importer = Mock(wraps=listener._importer)
    listener._importer = importer
    repository.mark_processing_success = Mock(wraps=repository.mark_processing_success)

    asyncio.run(listener.handle_bot_response(_durable_claim_message()))

    durable_context = importer.import_message.call_args.kwargs["durable_claim_context"]
    assert durable_context.source_event_id == 1
    assert durable_context.attempt_id == 1
    assert durable_context.finished_at.tzinfo is not None
    assert durable_context.finished_at.utcoffset() is not None
    attempts = _receipt_rows(database_path, "discord_processing_attempts")
    assert [(row["attempt_number"], row["status"]) for row in attempts] == [(1, "succeeded")]
    assert len(_import_event_rows(database_path, "claim")) == 1
    assert len(_receipt_rows(database_path, "claim_observations")) == 1
    assert len(_receipt_rows(database_path, "discord_projection_links")) == 1
    repository.mark_processing_success.assert_not_called()


def test_listener_succeeded_claim_restart_replay_uses_no_attempt_and_no_duplicates(tmp_path) -> None:
    first_listener, _repository, database_path = _durable_listener(tmp_path)
    message = _durable_claim_message()
    asyncio.run(first_listener.handle_bot_response(message))

    restarted_listener, _restarted_repository, _ = _durable_listener(tmp_path)
    importer = Mock(wraps=restarted_listener._importer)
    restarted_listener._importer = importer
    asyncio.run(restarted_listener.handle_bot_response(message))

    durable_context = importer.import_message.call_args.kwargs["durable_claim_context"]
    assert durable_context.source_event_id == 1
    assert durable_context.attempt_id is None
    assert durable_context.finished_at.tzinfo is not None
    assert len(_receipt_rows(database_path, "discord_processing_attempts")) == 1
    assert len(_import_event_rows(database_path, "claim")) == 1
    assert len(_receipt_rows(database_path, "claim_observations")) == 1
    assert len(_receipt_rows(database_path, "discord_projection_links")) == 1


def test_listener_retryable_claim_coordinator_failure_retries_transactionally(tmp_path) -> None:
    listener, _repository, database_path = _durable_listener(tmp_path)
    coordinator = listener._importer._claim_projection_coordinator
    coordinator.coordinate_claim = Mock(side_effect=RuntimeError("claim coordinator failed"))
    message = _durable_claim_message()

    asyncio.run(listener.handle_bot_response(message))

    first_attempt = _receipt_rows(database_path, "discord_processing_attempts")[0]
    assert first_attempt["status"] == "failed"
    assert first_attempt["retryable"] == 1
    assert _receipt_rows(database_path, "claim_observations") == []
    assert _receipt_rows(database_path, "discord_projection_links") == []
    assert _import_event_rows(database_path, "claim") == []

    retry_listener, _retry_repository, _ = _durable_listener(tmp_path)
    asyncio.run(retry_listener.handle_bot_response(message))

    attempts = _receipt_rows(database_path, "discord_processing_attempts")
    assert [(row["attempt_number"], row["status"]) for row in attempts] == [
        (1, "failed"),
        (2, "succeeded"),
    ]
    assert len(_import_event_rows(database_path, "claim")) == 1
    assert len(_receipt_rows(database_path, "claim_observations")) == 1
    assert len(_receipt_rows(database_path, "discord_projection_links")) == 1
    assert _receipt_rows(database_path, "discord_source_events")[0]["status"] == "succeeded"


def test_listener_active_claim_does_not_start_attempt_or_import(tmp_path) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)
    message = _durable_claim_message()
    received = listener._receive_message(message, message.content)
    assert received is not None
    active = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="mudae-parser-v1",
        router_version="mudae-router-v1",
        started_at=datetime.now(timezone.utc),
    )
    importer = Mock()
    replay_listener, _replay_repository, _ = _durable_listener(tmp_path, importer=importer)

    asyncio.run(replay_listener.handle_bot_response(message))

    importer.import_message.assert_not_called()
    attempts = _receipt_rows(database_path, "discord_processing_attempts")
    assert len(attempts) == 1
    assert attempts[0]["id"] == active.attempt_id
    assert attempts[0]["status"] == "processing"


@pytest.mark.parametrize("terminal_status", ["failed", "unresolved_attribution"])
def test_listener_nonretryable_claim_terminal_state_fails_closed(tmp_path, terminal_status) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)
    message = _durable_claim_message()
    received = listener._receive_message(message, message.content)
    assert received is not None
    attempt = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="mudae-parser-v1",
        router_version="mudae-router-v1",
        started_at=datetime.now(timezone.utc),
    )
    repository.mark_processing_failure(
        source_event_id=received.source_event_id,
        attempt_id=attempt.attempt_id,
        status=terminal_status,
        retryable=False,
        failure_code="terminal_test_failure",
        failure_detail="terminal claim test failure",
        finished_at=datetime.now(timezone.utc),
    )
    importer = Mock()
    replay_listener, _replay_repository, _ = _durable_listener(tmp_path, importer=importer)

    asyncio.run(replay_listener.handle_bot_response(message))

    importer.import_message.assert_not_called()
    assert len(_receipt_rows(database_path, "discord_processing_attempts")) == 1
    assert _receipt_rows(database_path, "discord_source_events")[0]["status"] == terminal_status
    assert _receipt_rows(database_path, "claim_observations") == []
    assert _import_event_rows(database_path, "claim") == []


def test_listener_claim_cleanup_error_after_durable_success_does_not_complete_failure(
    tmp_path, caplog
) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)
    repository.mark_processing_failure = Mock(wraps=repository.mark_processing_failure)
    listener._consume_context = Mock(side_effect=RuntimeError("cleanup unavailable"))
    caplog.set_level(logging.ERROR, logger="moa.discord")

    asyncio.run(listener.handle_bot_response(_durable_claim_message()))

    attempt = _receipt_rows(database_path, "discord_processing_attempts")[0]
    event = _receipt_rows(database_path, "discord_source_events")[0]
    assert attempt["status"] == "succeeded"
    assert event["status"] == "succeeded"
    repository.mark_processing_failure.assert_not_called()
    assert "Best-effort cleanup failed after durable claim success" in caplog.text


def test_listener_same_process_duplicate_claim_is_suppressed_before_attempt_work(tmp_path) -> None:
    listener, _repository, database_path = _durable_listener(tmp_path)
    importer = Mock(wraps=listener._importer)
    listener._importer = importer
    message = _durable_claim_message()

    asyncio.run(listener.handle_bot_response(message))
    asyncio.run(listener.handle_bot_response(message))

    importer.import_message.assert_called_once()
    assert len(_receipt_rows(database_path, "discord_processing_attempts")) == 1
    assert _receipt_rows(database_path, "discord_source_events")[0]["delivery_count"] == 2
    assert len(_import_event_rows(database_path, "claim")) == 1
    assert len(_receipt_rows(database_path, "claim_observations")) == 1


def test_listener_claim_uses_parsed_claimant_over_stale_channel_context(tmp_path) -> None:
    listener, catalog = _listener_with_two_configured_users(tmp_path)
    listener._mudae_user_id = 999
    importer = Mock(wraps=listener._importer)
    listener._importer = importer

    asyncio.run(
        listener.handle_bot_response(
            _durable_claim_message(claimant="user_a", interaction_user_id=789)
        )
    )

    assert importer.import_message.call_args.args[3] == "user_a"
    assert len(catalog.claim_observations("Test Server", "user_a")) == 1
    assert catalog.claim_observations("Test Server", "user_b") == ()


def test_listener_records_server_attribution_before_seen_payloads(tmp_path) -> None:
    listener, repository, database_path = _durable_listener(tmp_path)
    seen_sizes = []
    original_record = repository.record_server_attribution

    def record_attribution(*args, **kwargs):
        seen_sizes.append(len(listener._seen_payloads))
        return original_record(*args, **kwargs)

    repository.record_server_attribution = record_attribution

    asyncio.run(listener.handle_bot_response(_durable_roll_message()))

    assert seen_sizes == [0]
    assert _server_attribution_rows(database_path) == [(1, "resolved", "Test Server")]


def test_listener_reuses_persisted_resolved_attribution_after_reconstruction(tmp_path) -> None:
    first_listener, _repository, database_path = _durable_listener(tmp_path)
    message = _durable_roll_message()
    asyncio.run(first_listener.handle_bot_response(message))

    restarted_listener, restarted_repository, _ = _durable_listener(tmp_path)
    get_attribution = Mock(wraps=restarted_repository.get_server_attribution)
    restarted_repository.get_server_attribution = get_attribution
    restarted_listener._contexts.clear()
    restarted_listener._pending_contexts.clear()

    asyncio.run(restarted_listener.handle_bot_response(message))

    get_attribution.assert_called_once_with(1)
    assert _server_attribution_rows(database_path) == [(1, "resolved", "Test Server")]
    assert len(_receipt_rows(database_path, "roll_observations")) == 1


def test_listener_conflicting_live_server_evidence_fails_closed(tmp_path) -> None:
    first_listener, _repository, database_path = _attribution_listener(
        tmp_path,
        "first.json",
        (("Test Server", "user_a", "primary", "456"), ("Other Server", "user_b", "alt", "789")),
    )
    message = _durable_roll_message()
    asyncio.run(first_listener.handle_bot_response(message))

    importer = Mock()
    restarted_listener, restarted_repository, _ = _attribution_listener(
        tmp_path,
        "second.json",
        (("Test Server", "user_a", "primary", "456"), ("Other Server", "user_b", "alt", "789")),
        importer=importer,
    )
    message.interaction_metadata = SimpleNamespace(name="wa", user=SimpleNamespace(id=789))
    asyncio.run(restarted_listener.handle_bot_response(message))

    importer.import_message.assert_not_called()
    assert _server_attribution_rows(database_path) == [(1, "resolved", "Test Server")]
    assert len(_receipt_rows(database_path, "discord_processing_attempts")) == 1
    restarted_repository.record_server_attribution = Mock()


def test_listener_no_server_evidence_records_unresolved_and_blocks_import(tmp_path) -> None:
    importer = Mock()
    listener, _repository, database_path = _attribution_listener(
        tmp_path,
        "empty.json",
        (),
        importer=importer,
    )
    message = _durable_roll_message()
    message.interaction_metadata = None

    asyncio.run(listener.handle_bot_response(message))

    importer.import_message.assert_not_called()
    assert _server_attribution_rows(database_path) == [(1, "unresolved", None)]
    attempt = _receipt_rows(database_path, "discord_processing_attempts")[0]
    assert (attempt["status"], attempt["retryable"], attempt["failure_code"]) == (
        "unresolved_attribution",
        1,
        "unresolved_server_attribution",
    )


def test_listener_conflicting_authoritative_servers_record_ambiguous(tmp_path) -> None:
    importer = Mock()
    listener, _repository, database_path = _attribution_listener(
        tmp_path,
        "ambiguous.json",
        (("Test Server", "user_a", "primary", "456"), ("Other Server", "user_b", "alt", "789")),
        importer=importer,
    )
    message = _durable_claim_message(claimant="user_b", interaction_user_id=456)

    asyncio.run(listener.handle_bot_response(message))

    importer.import_message.assert_not_called()
    assert _server_attribution_rows(database_path) == [(1, "ambiguous", None)]
    attempt = _receipt_rows(database_path, "discord_processing_attempts")[0]
    assert attempt["failure_code"] == "ambiguous_server_attribution"
    assert attempt["status"] == "unresolved_attribution"


def test_listener_parsed_claimant_uniquely_resolves_server(tmp_path) -> None:
    listener, _repository, database_path = _attribution_listener(
        tmp_path,
        "claim.json",
        (("Test Server", "user_a", "primary", "456"),),
    )
    message = _durable_claim_message(interaction_user_id=456)
    message.interaction_metadata = None

    asyncio.run(listener.handle_bot_response(message))

    assert _server_attribution_rows(database_path) == [(1, "resolved", "Test Server")]
    assert len(_receipt_rows(database_path, "claim_observations")) == 1


def test_listener_parsed_profile_account_uniquely_resolves_server(tmp_path) -> None:
    listener, _repository, database_path = _attribution_listener(
        tmp_path,
        "profile.json",
        (("Test Server", "user_a", "primary", "456"),),
    )
    message = _durable_profile_message()
    message.interaction_metadata = None

    asyncio.run(listener.handle_bot_response(message))

    assert _server_attribution_rows(database_path) == [(1, "resolved", "Test Server")]
    assert len(_receipt_rows(database_path, "profile_observations")) == 1


def test_listener_unresolved_attribution_transitions_to_resolved(tmp_path) -> None:
    first_importer = Mock()
    first_listener, _repository, database_path = _attribution_listener(
        tmp_path,
        "unresolved-first.json",
        (),
        importer=first_importer,
    )
    message = _durable_roll_message()
    message.interaction_metadata = None
    asyncio.run(first_listener.handle_bot_response(message))

    second_listener, _second_repository, _ = _attribution_listener(
        tmp_path,
        "resolved-second.json",
        (("Test Server", "user_a", "primary", "456"),),
    )
    message.interaction_metadata = SimpleNamespace(name="wa", user=SimpleNamespace(id=456))
    asyncio.run(second_listener.handle_bot_response(message))

    assert _server_attribution_rows(database_path) == [(1, "resolved", "Test Server")]
    attempts = _receipt_rows(database_path, "discord_processing_attempts")
    assert [attempt["status"] for attempt in attempts] == [
        "unresolved_attribution",
        "succeeded",
    ]


def test_listener_ambiguous_attribution_transitions_to_resolved(tmp_path) -> None:
    first_importer = Mock()
    first_listener, _repository, database_path = _attribution_listener(
        tmp_path,
        "ambiguous-first.json",
        (("Test Server", "user_a", "primary", "456"), ("Other Server", "user_b", "alt", "789")),
        importer=first_importer,
    )
    message = _durable_roll_message()
    message.interaction_metadata = None
    asyncio.run(first_listener.handle_bot_response(message))

    second_listener, _second_repository, _ = _attribution_listener(
        tmp_path,
        "resolved-second.json",
        (("Test Server", "user_a", "primary", "456"),),
    )
    message.interaction_metadata = SimpleNamespace(name="wa", user=SimpleNamespace(id=456))
    asyncio.run(second_listener.handle_bot_response(message))

    assert _server_attribution_rows(database_path) == [(1, "resolved", "Test Server")]


@pytest.mark.parametrize(
    ("initial_accounts", "later_accounts", "expected_status"),
    [
        ((), (("Test Server", "user_a", "primary", "456"), ("Other Server", "user_b", "alt", "789")), "unresolved"),
        ((("Test Server", "user_a", "primary", "456"), ("Other Server", "user_b", "alt", "789")), (), "ambiguous"),
    ],
)
def test_listener_does_not_rewrite_unresolved_or_ambiguous_attribution(
    tmp_path,
    initial_accounts,
    later_accounts,
    expected_status,
) -> None:
    first_listener, _repository, database_path = _attribution_listener(
        tmp_path,
        "initial.json",
        initial_accounts,
        importer=Mock(),
    )
    message = _durable_roll_message()
    message.interaction_metadata = None
    asyncio.run(first_listener.handle_bot_response(message))

    later_listener, _later_repository, _ = _attribution_listener(
        tmp_path,
        "later.json",
        later_accounts,
        importer=Mock(),
    )
    asyncio.run(later_listener.handle_bot_response(message))

    assert _server_attribution_rows(database_path) == [(1, expected_status, None)]


def test_listener_attribution_write_failure_prevents_attempt_and_import(tmp_path) -> None:
    importer = Mock()
    listener, repository, database_path = _durable_listener(tmp_path, importer=importer)
    repository.record_server_attribution = Mock(side_effect=RuntimeError("attribution unavailable"))

    asyncio.run(listener.handle_bot_response(_durable_roll_message()))

    importer.import_message.assert_not_called()
    assert listener._seen_payloads == set()
    assert _receipt_rows(database_path, "discord_processing_attempts") == []
