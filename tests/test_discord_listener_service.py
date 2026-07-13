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
    assert DiscordListenerService._expected_kind_for_command("$dl") == "disablelist"
    assert DiscordListenerService._expected_kind_for_command("$settings") == "settings"
    assert DiscordListenerService._expected_kind_for_command("$bonus") == "bonus"
    assert DiscordListenerService._expected_kind_for_command("$rolls") == "timers"
    assert DiscordListenerService._expected_kind_for_command("$oq") == "sphere_result"
    assert DiscordListenerService._expected_kind_for_command("$kt") == "towerstate"
    assert DiscordListenerService._expected_kind_for_command("$lk") == "lootstate"
    assert DiscordListenerService._expected_kind_for_command("$im") == "im"


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


def test_listener_does_not_turn_bonus_or_timer_text_into_a_roll(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    )

    timer_text = "You have 0 rolls left. Next rolls reset in 40 min."
    bonus_text = "Player Bonuses\nRolls per hour: +9"

    assert listener._resolve_message_kind("bonus", timer_text) is None
    assert listener._resolve_message_kind("bonus", bonus_text) == "bonus"
    assert listener._resolve_message_kind("timers", timer_text) == "timers"


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

    context = listener._contexts[789]
    assert context.identity.account == "ernieuuu"
    assert context.expected_kind == "reaction_receipt"


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


def test_listener_presence_uses_watching_status_and_truncates_custom_text(tmp_path) -> None:
    listener = DiscordListenerService(
        catalog_service=CatalogService(CatalogRepository(tmp_path / "catalog.db")),
        status_text="  Tracking Mudae data  ",
    )

    activity = listener.presence_activity()

    assert activity.type.value == 3
    assert activity.name == "Tracking Mudae data"
