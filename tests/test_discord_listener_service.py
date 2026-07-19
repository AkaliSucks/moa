import asyncio
from types import SimpleNamespace

import pytest

from moa.repositories.catalog_repository import CatalogRepository
from moa.core.config import ConfigService
from moa.services.catalog_service import CatalogService
from moa.services.discord_listener_service import DiscordListenerService


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
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    return DiscordListenerService(config_service=config, catalog_service=catalog), catalog


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
    listener = DiscordListenerService(config_service=config, catalog_service=catalog)
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
    listener = DiscordListenerService(config_service=config, catalog_service=catalog)
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
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    listener = DiscordListenerService(config_service=config, catalog_service=catalog)
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
