import sqlite3

import pytest

from moa.parser.mudae import MudaeTextParser
from moa.repositories.catalog_repository import CatalogRepository
from moa.services.catalog_service import CatalogService


TOP_PAGE = """#1 - Hatsune Miku - VOCALOID
#2 - Zero Two - DARLING in the FRANXX
"""

CHARACTER_DETAILS = """Mai Sakurajima
Seishun Buta Yarou :female:
Animanga roulette · 929:kakera:
Claim Rank: #9
Like Rank: #19
"""


def test_import_top_page_persists_characters_snapshots_and_raw_message(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    service = CatalogService(CatalogRepository(database_path))
    page = MudaeTextParser().parse_top_page(TOP_PAGE)

    result = service.import_top_page(page, TOP_PAGE, "clipboard")

    assert result.characters_imported == 2
    assert service.character_count() == 2

    ranked = service.top()
    assert [character.character.name for character in ranked] == ["Hatsune Miku", "Zero Two"]
    assert [character.claim_rank for character in ranked] == [1, 2]

    with sqlite3.connect(database_path) as connection:
        raw_message = connection.execute("SELECT raw_message FROM import_events").fetchone()[0]
    assert raw_message == TOP_PAGE


def test_import_top_page_updates_a_character_without_duplicate_catalog_rows(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    service = CatalogService(CatalogRepository(database_path))

    service.import_top_page(
        MudaeTextParser().parse_top_page("#2 - Zero Two - DARLING in the FRANXX"),
        "#2 - Zero Two - DARLING in the FRANXX",
        "clipboard",
    )
    service.import_top_page(
        MudaeTextParser().parse_top_page("#1 - Zero Two - DARLING in the FRANXX"),
        "#1 - Zero Two - DARLING in the FRANXX",
        "clipboard",
    )

    assert service.character_count() == 1
    assert service.top()[0].claim_rank == 1


def test_import_im_enriches_global_character_and_keeps_kakera_value_per_server(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    service = CatalogService(CatalogRepository(database_path))
    details = MudaeTextParser().parse_character_details(CHARACTER_DETAILS)

    service.import_character_details(details, "Optimization Server", CHARACTER_DETAILS, "clipboard")
    service.import_character_details(details, "Collection Server", CHARACTER_DETAILS, "clipboard")

    profile = service.get_profile("Mai Sakurajima", "Seishun Buta Yarou")

    assert profile is not None
    assert profile.character.gender == "female"
    assert profile.character.roulette == "animanga"
    assert profile.claim_rank == 9
    assert profile.like_rank == 19
    assert [(item.server_name, item.kakera_value) for item in profile.server_observations] == [
        ("Collection Server", 929),
        ("Optimization Server", 929),
    ]


def test_delete_import_removes_only_its_derived_observations(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    service = CatalogService(CatalogRepository(database_path))
    details = MudaeTextParser().parse_character_details(CHARACTER_DETAILS)

    mistaken = service.import_character_details(details, "Wrong Server", CHARACTER_DETAILS, "clipboard")
    correct = service.import_character_details(details, "Correct Server", CHARACTER_DETAILS, "clipboard")

    assert [item.server_name for item in service.recent_imports()] == [
        "Correct Server",
        "Wrong Server",
    ]
    assert service.delete_import_event(mistaken.import_event_id)

    profile = service.get_profile("Mai Sakurajima", "Seishun Buta Yarou")
    assert profile is not None
    assert [item.server_name for item in profile.server_observations] == ["Correct Server"]
    assert not service.delete_import_event(mistaken.import_event_id)
    assert service.recent_imports()[0].id == correct.import_event_id


def test_import_mmy_page_keeps_unresolved_names_without_losing_key_data(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    service = CatalogService(CatalogRepository(database_path))
    service.import_top_page(
        MudaeTextParser().parse_top_page("#14 - Albedo - Overlord"),
        "#14 - Albedo - Overlord",
        "clipboard",
    )
    harem_page = MudaeTextParser().parse_harem_key_page(
        "Albedo \u00b7 :goldkey:  (7)\nMiku Nakano \u00b7 :goldkey:  (6)\nPage 1 / 6"
    )

    result = service.import_harem_key_page(
        harem_page,
        "Lake Arrowhead 2025",
        "ernieuuu",
        "Albedo \u00b7 :goldkey:  (7)\nMiku Nakano \u00b7 :goldkey:  (6)\nPage 1 / 6",
        "clipboard",
    )

    assert result.entries_imported == 2
    assert result.entries_linked == 1
    entries = service.harem_keys("Lake Arrowhead 2025", "ernieuuu")
    assert [(entry.character_name, entry.key_count) for entry in entries] == [
        ("Albedo", 7),
        ("Miku Nakano", 6),
    ]
    assert entries[0].character is not None
    assert entries[0].character.series == "Overlord"
    assert entries[1].character is None

    service.import_character_details(
        MudaeTextParser().parse_character_details(
            "Miku Nakano\n"
            "Go-Toubun no Hanayome\n"
            "Animanga roulette \u00b7 500 Kakera\n"
            "Claim Rank: #100\n"
            "Like Rank: #200\n"
        ),
        "Lake Arrowhead 2025",
        "Miku Nakano details",
        "clipboard",
    )

    refreshed_entries = service.harem_keys("Lake Arrowhead 2025", "ernieuuu")
    assert refreshed_entries[1].character is not None
    assert refreshed_entries[1].character.series == "Go-Toubun no Hanayome"


def test_import_mmyk_page_persists_current_harem_kakera_values(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    service = CatalogService(CatalogRepository(database_path))
    page_text = (
        "Megumin · :silverkey:  (5) 1,505 ka\n"
        "Albedo · :goldkey:  (7) 1,453 ka\n"
        "Page 1 / 6"
    )

    service.import_harem_key_page(
        MudaeTextParser().parse_harem_key_page(page_text),
        "Lake Arrowhead 2025",
        "ernieuuu",
        page_text,
        "clipboard",
    )

    entries = service.harem_keys("Lake Arrowhead 2025", "ernieuuu")
    assert [(entry.character_name, entry.kakera_value) for entry in entries] == [
        ("Megumin", 1505),
        ("Albedo", 1453),
    ]


def test_complete_harem_scan_activates_only_after_every_page_is_imported(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    scan = service.begin_harem_scan("Lake Arrowhead 2025", "ernieuuu")
    first_page = "Megumin · :silverkey:  (5) 1,505 ka\nPage 1 / 2"
    second_page = "Albedo · :goldkey:  (7) 1,453 ka\nPage 2 / 2"

    service.import_harem_key_page(
        MudaeTextParser().parse_harem_key_page(first_page),
        "Lake Arrowhead 2025",
        "ernieuuu",
        first_page,
        "clipboard",
        scan.id,
    )
    assert service.harem_scan_progress(scan.id).imported_pages == (1,)
    with pytest.raises(ValueError, match="incomplete"):
        service.complete_harem_scan(scan.id)

    service.import_harem_key_page(
        MudaeTextParser().parse_harem_key_page(second_page),
        "Lake Arrowhead 2025",
        "ernieuuu",
        second_page,
        "clipboard",
        scan.id,
    )
    completed = service.complete_harem_scan(scan.id)

    assert completed.is_complete
    assert completed.completed_at is not None
    assert [entry.character_name for entry in service.harem_keys("Lake Arrowhead 2025", "ernieuuu")] == [
        "Megumin",
        "Albedo",
    ]


def test_import_bonus_persists_latest_account_scoped_player_state(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    text = (
        "Player Bonuses\n"
        "Rolls per hour: +9 (6 $k + 1 $kl + 2 $kt) -3 ($bw)\n"
        "Spawn bonus for wishes: +210% ($k + $bw + slash)\n"
        "Starwish slots: +1 (0 $kl + 1 $sw)\n"
    )

    result = service.import_player_bonus(
        MudaeTextParser().parse_player_bonus(text),
        "Lake Arrowhead 2025",
        "ernieuuu",
        text,
        "clipboard",
    )
    bonus = service.player_bonus("Lake Arrowhead 2025", "ernieuuu")

    assert result.account_name == "ernieuuu"
    assert bonus is not None
    assert bonus.rolls_per_hour_bonus == 9
    assert bonus.wish_spawn_bonus_percent == 210
    assert bonus.starwish_slot_bonus == 1
    assert [metric.label for metric in bonus.metrics] == [
        "Rolls per hour",
        "Spawn bonus for wishes",
        "Starwish slots",
    ]


def test_import_wishlist_persists_starwish_state_per_server_account(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    text = (
        "**ernieuuu's Wishlist - 2/13 $wl, 1/2 $sw**\n"
        "**Emilia** ✅ ⭐\n"
        "**Saber** ✅:kakera:\n"
    )

    result = service.import_wishlist(
        MudaeTextParser().parse_wishlist(text),
        "Lake Arrowhead 2025",
        "ernieuuu",
        text,
        "clipboard",
    )
    wishlist = service.wishlist("Lake Arrowhead 2025", "ernieuuu")

    assert result.account_name == "ernieuuu"
    assert wishlist is not None
    assert wishlist.starwish_count == 1
    assert [(entry.name, entry.is_starwish) for entry in wishlist.entries] == [
        ("Emilia", True),
        ("Saber", False),
    ]


def test_import_disablelist_persists_account_scoped_roll_pool_state(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    text = (
        "ernieuuu's Disablelist (1/16)\n"
        "1,000 disabled (400 $wa, 300 $ha, 200 $wg, 100 $hg)\n"
        "Western animanga series are completely disabled ($togglewestern)\n"
        "Kadokawa Corporation (400)\n"
    )

    result = service.import_disablelist(
        MudaeTextParser().parse_disablelist(text),
        "Lake Arrowhead 2025",
        "ernieuuu",
        text,
        "clipboard",
    )
    disablelist = service.disablelist("Lake Arrowhead 2025", "ernieuuu")

    assert result.account_name == "ernieuuu"
    assert disablelist is not None
    assert disablelist.disabled_wa == 400
    assert disablelist.western_disabled
    assert not disablelist.irl_disabled
    assert [(entry.name, entry.disabled_count) for entry in disablelist.entries] == [
        ("Kadokawa Corporation", 400)
    ]


def test_import_topx_persists_direct_unavailable_character_evidence(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    text = "#10 - 2B - NieR: Automata 🚫\n#88 - Venom - Marvel 🚫 ($togglewestern)"

    result = service.import_unavailable_characters(
        MudaeTextParser().parse_unavailable_characters(text),
        "Lake Arrowhead 2025",
        "ernieuuu",
        text,
        "clipboard",
    )
    unavailable = service.unavailable_characters("Lake Arrowhead 2025", "ernieuuu")

    assert result.characters_imported == 2
    assert [(entry.character.name, entry.reason) for entry in unavailable] == [
        ("2B", None),
        ("Venom", "$togglewestern"),
    ]


def test_import_kakera_state_persists_balance_and_badges(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    text = "You have 7,673:kakera:!\nBronze IV · Max reached!\nSilver III · In progress"

    result = service.import_kakera_state(
        MudaeTextParser().parse_kakera_state(text),
        "Lake Arrowhead 2025",
        "ernieuuu",
        text,
        "clipboard",
    )
    state = service.kakera_state("Lake Arrowhead 2025", "ernieuuu")

    assert result.account_name == "ernieuuu"
    assert state is not None
    assert state.kakera_balance == 7673
    assert [(badge.badge_name, badge.level, badge.max_reached) for badge in state.badges] == [
        ("bronze", 4, True),
        ("silver", 3, False),
    ]


def test_import_tower_state_persists_current_tower_progress(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    text = (
        "Your current level is:tow2: (+ 1 tower)\n"
        "The next level costs 75,000:kakera:\n"
        "You have 7,673:kakera:\n"
        "☑️ [5] Unveil 1 random button for the $oh command\n"
        "☑️ [11] +1 roll per hour"
    )

    result = service.import_tower_state(
        MudaeTextParser().parse_tower_state(text),
        "Lake Arrowhead 2025",
        "ernieuuu",
        text,
        "clipboard",
    )
    state = service.tower_state("Lake Arrowhead 2025", "ernieuuu")

    assert result.account_name == "ernieuuu"
    assert state is not None
    assert state.next_level_cost == 75000
    assert state.built_perk_ids == (5, 11)


def test_import_kakeraloot_state_persists_account_progress(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    text = (
        "Rolls stacked: 1 ($us)\n"
        "$disable limits: -102 $wa/$ha, -68 $wg/$hg\n"
        "Protected wish: LVL 42 (spawn probability: 1/4,642)\n"
        "Mudapins: 22 ($mp)\n"
        "$rt: -2h cooldown\n"
        "+1 permanent roll\n"
        "1 star branch (+0 $sw)\n"
        "Quantity LVL 23\nQuality LVL 6\n$kl usage: 256 (:kakeraC:+1)\n9,210:kakera:"
    )

    result = service.import_kakeraloot_state(
        MudaeTextParser().parse_kakeraloot_state(text),
        "Lake Arrowhead 2025",
        "ernieuuu",
        text,
        "clipboard",
    )
    state = service.kakeraloot_state("Lake Arrowhead 2025", "ernieuuu")

    assert result.account_name == "ernieuuu"
    assert state is not None
    assert state.quantity_level == 23
    assert state.quality_level == 6
    assert state.kakera_balance == 9210
