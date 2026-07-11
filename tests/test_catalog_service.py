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
