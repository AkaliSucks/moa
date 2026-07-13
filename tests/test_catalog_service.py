import sqlite3

import pytest

from moa.parser.mudae import MudaeTextParser
from moa.repositories.catalog_repository import CatalogRepository
from moa.services.catalog_service import CatalogService
from moa.services.top_search_service import TopSearchService


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
    assert len(service.top(None)) == 2

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


def test_import_topo_page_persists_claimed_owner_name(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    page_text = (
        "#1 - Hatsune Miku \U0001f49e => xuppii - VOCALOID\n"
        "#10 - 2B - NieR: Automata"
    )

    service.import_top_page(
        MudaeTextParser().parse_top_page(page_text),
        page_text,
        "clipboard",
        server_name="Lake Arrowhead 2025",
    )

    assert service.top()[0].owner_name == "xuppii"
    observations = service.top_owner_observations("Lake Arrowhead 2025")
    assert [(entry.character.name, entry.owner_name) for entry in observations] == [
        ("2B", None),
        ("Hatsune Miku", "xuppii"),
    ]


def test_import_topo_page_requires_server_context_for_owner_claims(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    page_text = "#1 - Hatsune Miku \U0001f49e => xuppii - VOCALOID"

    with pytest.raises(ValueError, match="owner claims requires --server"):
        service.import_top_page(
            MudaeTextParser().parse_top_page(page_text),
            page_text,
            "clipboard",
        )


def test_topo_owner_observations_are_isolated_by_server(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    lake_page = "#1 - Hatsune Miku \U0001f49e => xuppii - VOCALOID"
    personal_page = "#1 - Hatsune Miku \U0001f49e => ernieuuu - VOCALOID"

    service.import_top_page(
        MudaeTextParser().parse_top_page(lake_page),
        lake_page,
        "clipboard",
        server_name="Lake Arrowhead 2025",
    )
    service.import_top_page(
        MudaeTextParser().parse_top_page(personal_page),
        personal_page,
        "clipboard",
        server_name="ernieuuu's server",
    )

    assert service.top_owner_observations("Lake Arrowhead 2025")[0].owner_name == "xuppii"
    assert service.top_owner_observations("ernieuuu's server")[0].owner_name == "ernieuuu"


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


def test_import_mmrk_page_persists_direct_owned_evidence(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    service = CatalogService(CatalogRepository(database_path))
    service.import_top_page(
        MudaeTextParser().parse_top_page("#2 - Zero Two - DARLING in the FRANXX"),
        "#2 - Zero Two - DARLING in the FRANXX",
        "clipboard",
    )
    page_text = (
        "ernieuuu's harem\n"
        "#2 - Zero Two 1,440 ka\n"
        "#57 - Unresolved Character 800 ka\n"
        "Page 1 / 38"
    )

    result = service.import_ranked_harem_page(
        MudaeTextParser().parse_ranked_harem_page(page_text),
        "Lake Arrowhead 2025",
        "ernieuuu",
        page_text,
        "clipboard",
    )

    assert result.entries_imported == 2
    assert result.entries_linked == 1
    entries = service.owned_characters("Lake Arrowhead 2025", "ernieuuu")
    assert [(entry.character_name, entry.claim_rank, entry.kakera_value) for entry in entries] == [
        ("Zero Two", 2, 1440),
        ("Unresolved Character", 57, 800),
    ]
    assert entries[0].character is not None
    assert entries[0].character.series == "DARLING in the FRANXX"
    assert entries[1].character is None


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


def test_complete_owned_harem_scan_activates_ranked_pages_for_ownership(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    scan = service.begin_harem_scan("Lake Arrowhead 2025", "ernieuuu", "owned")
    first_page = "ernieuuu's harem\n#2 - Zero Two 1,440 ka\nPage 1 / 2"
    second_page = "ernieuuu's harem\n#3 - Rem 1,426 ka\nPage 2 / 2"

    service.import_ranked_harem_page(
        MudaeTextParser().parse_ranked_harem_page(first_page),
        "Lake Arrowhead 2025",
        "ernieuuu",
        first_page,
        "clipboard",
        scan.id,
    )
    assert service.owned_characters("Lake Arrowhead 2025", "ernieuuu") == ()
    with pytest.raises(ValueError, match="incomplete"):
        service.complete_harem_scan(scan.id)

    service.import_ranked_harem_page(
        MudaeTextParser().parse_ranked_harem_page(second_page),
        "Lake Arrowhead 2025",
        "ernieuuu",
        second_page,
        "clipboard",
        scan.id,
    )
    completed = service.complete_harem_scan(scan.id)

    assert completed.scan_kind == "owned"
    assert service.has_complete_harem_scan("Lake Arrowhead 2025", "ernieuuu", "owned")
    assert [entry.character_name for entry in service.owned_characters("Lake Arrowhead 2025", "ernieuuu")] == [
        "Zero Two",
        "Rem",
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


def test_import_antidisable_scan_activates_series_only_when_complete(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    scan = service.begin_antidisable_scan("Lake Arrowhead 2025", "ernieuuu")
    page_one = (
        "ernieuuu's Antidisablelist (2/500)\n"
        "20 antidisabled characters\n"
        "【OSHI NO KO】\n"
        "Page 1 / 2"
    )
    page_two = (
        "ernieuuu's Antidisablelist (2/500)\n"
        "Chainsaw Man\n"
        "Page 2 / 2"
    )

    service.import_antidisable_page(
        MudaeTextParser().parse_antidisable_page(page_one),
        "Lake Arrowhead 2025",
        "ernieuuu",
        page_one,
        "clipboard",
        scan.id,
    )
    assert service.antidisable_series("Lake Arrowhead 2025", "ernieuuu") == ()
    service.import_antidisable_page(
        MudaeTextParser().parse_antidisable_page(page_two),
        "Lake Arrowhead 2025",
        "ernieuuu",
        page_two,
        "clipboard",
        scan.id,
    )

    service.complete_antidisable_scan(scan.id)

    assert service.antidisable_series("Lake Arrowhead 2025", "ernieuuu") == (
        "Chainsaw Man",
        "OSHI NO KO",
    )


def test_catalog_top_marks_matching_series_antidisabled(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    top_text = "#7 - Power - Chainsaw Man"
    service.import_top_page(
        MudaeTextParser().parse_top_page(top_text), top_text, "clipboard"
    )
    scan = service.begin_antidisable_scan("Lake Arrowhead 2025", "ernieuuu")
    adl_text = (
        "ernieuuu's Antidisablelist (1/500)\n"
        "10 antidisabled characters\n"
        "Chainsaw Man\n"
        "Page 1 / 1"
    )
    service.import_antidisable_page(
        MudaeTextParser().parse_antidisable_page(adl_text),
        "Lake Arrowhead 2025",
        "ernieuuu",
        adl_text,
        "clipboard",
        scan.id,
    )
    service.complete_antidisable_scan(scan.id)

    power = TopSearchService(service).search(
        server_name="Lake Arrowhead 2025", account_name="ernieuuu"
    )[0]

    assert power.character.name == "Power"
    assert power.rollability_status == "Antidisabled"


def test_reinitializing_catalog_backfills_unclaimed_topo_rows(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    service = CatalogService(CatalogRepository(database_path))
    page_text = (
        "#1 - Hatsune Miku \U0001f49e => xuppii - VOCALOID\n"
        "#10 - 2B - NieR: Automata"
    )
    result = service.import_top_page(
        MudaeTextParser().parse_top_page(page_text),
        page_text,
        "clipboard",
        server_name="Lake Arrowhead 2025",
    )

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "DELETE FROM top_owner_observations WHERE import_event_id = ? "
            "AND owner_name IS NULL",
            (result.import_event_id,),
        )

    refreshed = CatalogService(CatalogRepository(database_path))
    observations = refreshed.top_owner_observations("Lake Arrowhead 2025")

    assert [(entry.character.name, entry.owner_name) for entry in observations] == [
        ("2B", None),
        ("Hatsune Miku", "xuppii"),
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


def test_import_kakeraloot_state_persists_when_mudae_reports_no_loots(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    text = "No kakeraloots bought! ($kl)\nType $infokl to get more infos about kakeraloots."

    service.import_kakeraloot_state(
        MudaeTextParser().parse_kakeraloot_state(text),
        "ernieuuu's server",
        "cute_beagle_91130",
        text,
        "clipboard",
    )
    state = service.kakeraloot_state("ernieuuu's server", "cute_beagle_91130")

    assert state is not None
    assert not state.has_kakeraloots
    assert state.quantity_level is None


def test_import_server_settings_persists_server_scoped_configuration(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    text = (
        "(Server not premium)\n"
        "· Prefix: $ ($prefix)\n"
        "· Lang: en ($lang)\n"
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

    result = service.import_server_settings(
        MudaeTextParser().parse_server_settings(text),
        "Lake Arrowhead 2025",
        text,
        "clipboard",
    )
    settings = service.server_settings("Lake Arrowhead 2025")

    assert result.server_name == "Lake Arrowhead 2025"
    assert settings is not None
    assert settings.game_mode == 1
    assert settings.rolls_per_hour == 10
    assert not settings.server_premium


def test_import_personal_rare_persists_an_account_scoped_override(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    raw_message = "Your current $personalrare: 1"

    result = service.import_personal_rare(
        MudaeTextParser().parse_personal_rare(raw_message),
        "Lake Arrowhead 2025",
        "ernieuuu",
        raw_message,
        "clipboard",
    )
    state = service.personal_rare("Lake Arrowhead 2025", "ernieuuu")

    assert result.account_name == "ernieuuu"
    assert state is not None
    assert state.personal_rare_multiplier == 1


def test_import_kakeraloot_settings_persists_server_pricing(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    raw_message = (
        "Each $kl costs 500:kakera:\n"
        "Reaching the level 1 of quantity or quality costs 2,000:kakera: (increased by 200/level)"
    )

    result = service.import_kakeraloot_settings(
        MudaeTextParser().parse_kakeraloot_settings(raw_message),
        "Lake Arrowhead 2025",
        raw_message,
        "clipboard",
    )
    settings = service.kakeraloot_settings("Lake Arrowhead 2025")

    assert result.server_name == "Lake Arrowhead 2025"
    assert settings is not None
    assert settings.loot_cost == 500
    assert settings.quantity_quality_level_increment == 200


def test_import_timer_state_persists_a_short_lived_account_snapshot(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    raw_message = (
        "ernieuuu, you can claim right now! The next claim reset is in 2h 32 min.\n"
        "You have 0 rolls left. Next rolls reset in 32 min.\n"
        "Power: 72%\n"
        "Stock: 12,114:kakera:"
    )

    result = service.import_timer_state(
        MudaeTextParser().parse_timer_state(raw_message),
        "Lake Arrowhead 2025",
        "ernieuuu",
        raw_message,
        "clipboard",
    )
    state = service.timer_state("Lake Arrowhead 2025", "ernieuuu")

    assert result.account_name == "ernieuuu"
    assert state is not None
    assert state.snapshot.claim_reset_minutes == 152
    assert state.snapshot.kakera_stock == 12114


def test_kakera_reaction_summary_groups_receipts_and_scopes_account(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    parser = MudaeTextParser()
    lake = "Lake Arrowhead 2025"

    for raw_message in (
        ":kakeraY: ernieuuu +319 ($k)",
        ":kakeraG: ernieuuu +497 ($k)",
        ":kakeraY: ernieuuu +100 ($k)",
    ):
        service.import_kakera_reaction(
            parser.parse_kakera_reaction_receipt(raw_message),
            lake,
            raw_message,
            "clipboard",
        )
    service.import_kakera_reaction(
        parser.parse_kakera_reaction_receipt(":kakeraG: cute_beagle_91130 +999 ($k)"),
        lake,
        ":kakeraG: cute_beagle_91130 +999 ($k)",
        "clipboard",
    )

    summary = service.kakera_reaction_summary(lake, "ernieuuu")

    assert summary.receipt_count == 3
    assert summary.total_kakera_earned == 916
    assert summary.average_kakera_earned == pytest.approx(305.3333333333)
    assert summary.highest_kakera_earned == 497
    assert summary.by_reaction == ((":kakeraG:", 1, 497), (":kakeraY:", 2, 419))
    latest = service.kakera_reactions(lake, "ernieuuu", 1)
    assert [(entry.reaction_label, entry.kakera_earned) for entry in latest] == [
        (":kakeraY:", 100)
    ]


def test_import_roll_keeps_rankless_and_ranked_observations_in_history(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    rankless = "Hips\nDekoboko Majo no Oyako Jijou\n30:kakera:"
    ranked = "Chisato Nishikigi\nLycoris Recoil\nClaims: #484\n209:kakera:"

    service.import_roll(
        MudaeTextParser().parse_roll(rankless), "Lake", "ernieuuu", rankless, "clipboard"
    )
    service.import_roll(
        MudaeTextParser().parse_roll(ranked), "Lake", "ernieuuu", ranked, "clipboard"
    )
    rolls = service.recent_rolls("Lake", "ernieuuu")

    assert len(rolls) == 2
    assert rolls[0].character.name == "Chisato Nishikigi"
    assert rolls[0].claim_rank == 484
    assert rolls[1].claim_rank is None


def test_roll_statistics_describe_only_imported_roll_observations(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    rankless = "Hips\nDekoboko Majo no Oyako Jijou\n30:kakera:"
    ranked = "Chisato Nishikigi\nLycoris Recoil\nClaims: #484\n209:kakera:"

    service.import_roll(
        MudaeTextParser().parse_roll(rankless), "Lake", "ernieuuu", rankless, "clipboard"
    )
    service.import_roll(
        MudaeTextParser().parse_roll(ranked), "Lake", "ernieuuu", ranked, "clipboard"
    )

    statistics = service.roll_statistics("Lake", "ernieuuu")

    assert statistics.roll_count == 2
    assert statistics.best_claim_rank == 484
    assert statistics.average_claim_rank == 484.0
    assert statistics.average_kakera_value == 119.5
    assert statistics.highest_kakera_value == 209


def test_recent_key_gains_uses_only_key_states_displayed_on_rolls(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    raw = "Mai Sakurajima\nSeishun Buta Yarou\n+1,386:kakera:\n:goldkey: (7) +10% kakera value"
    service.import_roll(MudaeTextParser().parse_roll(raw), "Lake", "ernieuuu", raw, "clipboard")

    gains = service.recent_key_gains("Lake", "ernieuuu")

    assert len(gains) == 1
    assert gains[0].character_name == "Mai Sakurajima"
    assert gains[0].key_count == 7
    assert gains[0].key_type == "gold"


def test_rank_history_keeps_direct_rank_observations_for_one_character(tmp_path) -> None:
    service = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    first = (
        "Mai Sakurajima\nSeishun Buta Yarou\nAnimanga roulette · 900:kakera:\n"
        "Claim Rank: #10\nLike Rank: #20"
    )
    second = (
        "Mai Sakurajima\nSeishun Buta Yarou\nAnimanga roulette · 900:kakera:\n"
        "Claim Rank: #9\nLike Rank: #19"
    )

    service.import_character_details(
        MudaeTextParser().parse_character_details(first), "Lake", first, "clipboard"
    )
    service.import_character_details(
        MudaeTextParser().parse_character_details(second), "Lake", second, "clipboard"
    )

    history = service.rank_history("Mai Sakurajima", "Seishun Buta Yarou")

    assert [(point.claim_rank, point.like_rank) for point in history] == [(9, 19), (10, 20)]
