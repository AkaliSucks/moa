import pytest

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


def test_automatic_import_does_not_persist_rolls_before_roll_history_exists(tmp_path) -> None:
    service = AutomaticImportService(CatalogService(CatalogRepository(tmp_path / "catalog.db")))
    message = "Hips\nDekoboko Majo no Oyako Jijou\n30:kakera:"

    with pytest.raises(ValueError, match="analyze-roll"):
        service.import_message(message, "test", "Lake", "ernieuuu")
