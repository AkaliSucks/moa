import sqlite3

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
