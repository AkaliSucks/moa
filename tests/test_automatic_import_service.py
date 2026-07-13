import pytest

from moa.parser.mudae import MudaeTextParser
from moa.repositories.catalog_repository import CatalogRepository
from moa.services.automatic_import_service import AutomaticImportService
from moa.services.catalog_service import CatalogService


def test_automatic_import_routes_top_pages_without_server_context(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = "TOP 1000\n#1 - Hatsune Miku - VOCALOID\nPage 1 / 67"

    result = service.import_message(message, "test")

    assert result.kind == "top"
    assert result.imported_count == 1
    assert catalog.character_count() == 1


def test_automatic_import_requires_context_only_for_account_scoped_messages(tmp_path) -> None:
    service = AutomaticImportService(CatalogService(CatalogRepository(tmp_path / "catalog.db")))
    message = "ernieuuu, you can claim right now! The next claim reset is in 2h 32 min."

    with pytest.raises(ValueError, match="--server"):
        service.import_message(message, "test")


def test_automatic_import_persists_rankless_rolls_for_future_history(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = "Hips\nDekoboko Majo no Oyako Jijou\n30:kakera:"

    result = service.import_message(message, "test", "Lake", "ernieuuu")
    rolls = catalog.recent_rolls("Lake", "ernieuuu")

    assert result.kind == "roll"
    assert result.imported_count == 1
    assert rolls[0].character.name == "Hips"
    assert rolls[0].claim_rank is None


def test_automatic_import_routes_keyed_harem_pages(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = "ernieuuu's harem\nAlbedo \u00b7 :goldkey:  (7) 1,453 ka\nPage 1 / 6"

    result = service.import_message(message, "test", "Lake", "ernieuuu")

    assert result.kind == "harem"
    assert result.imported_count == 1
    assert "page 1/6" in result.message
    entries = catalog.harem_keys("Lake", "ernieuuu")
    assert [(entry.character_name, entry.key_count, entry.kakera_value) for entry in entries] == [
        ("Albedo", 7, 1453)
    ]


def test_automatic_import_routes_antidisable_pages(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    service = AutomaticImportService(catalog)
    message = (
        "ernieuuu's Antidisablelist (1/500)\n"
        "10 antidisabled characters\n"
        "Chainsaw Man\n"
        "Page 1 / 1"
    )

    result = service.import_message(message, "test", "Lake", "ernieuuu")

    assert result.kind == "antidisable"
    assert result.imported_count == 1
    assert "page 1/1" in result.message


def test_automatic_import_routes_ranked_harem_pages(tmp_path) -> None:
    catalog = CatalogService(CatalogRepository(tmp_path / "catalog.db"))
    catalog.import_top_page(
        MudaeTextParser().parse_top_page("#2 - Zero Two - DARLING in the FRANXX"),
        "#2 - Zero Two - DARLING in the FRANXX",
        "clipboard",
    )
    service = AutomaticImportService(catalog)
    message = "ernieuuu's harem\n#2 - Zero Two 1,440 ka\nPage 1 / 38"

    result = service.import_message(message, "test", "Lake", "ernieuuu")

    assert result.kind == "ranked_harem"
    assert result.imported_count == 1
    assert "page 1/38" in result.message
    entries = catalog.owned_characters("Lake", "ernieuuu")
    assert [(entry.character_name, entry.claim_rank, entry.kakera_value) for entry in entries] == [
        ("Zero Two", 2, 1440)
    ]
