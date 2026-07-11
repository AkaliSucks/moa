"""Business operations for MOA's persisted character catalog."""

from moa.models.catalog import (
    CharacterDetailsImportResult,
    CharacterProfile,
    ImportEventSummary,
    RankedCatalogCharacter,
    TopImportResult,
)
from moa.models.character import CharacterDetails, TopPage
from moa.repositories.catalog_repository import CatalogRepository, CatalogRepositoryProtocol


class CatalogService:
    """Catalog operations independent of the SQLite implementation."""

    def __init__(self, repository: CatalogRepositoryProtocol | None = None) -> None:
        self._repository = repository or CatalogRepository()

    def import_top_page(self, page: TopPage, raw_message: str, source: str) -> TopImportResult:
        return self._repository.import_top_page(page, raw_message, source)

    def import_character_details(
        self,
        details: CharacterDetails,
        server_name: str,
        raw_message: str,
        source: str,
    ) -> CharacterDetailsImportResult:
        return self._repository.import_character_details(details, server_name, raw_message, source)

    def top(self, limit: int = 15) -> tuple[RankedCatalogCharacter, ...]:
        return self._repository.top(limit)

    def character_count(self) -> int:
        return self._repository.character_count()

    def get_profile(self, name: str, series: str) -> CharacterProfile | None:
        return self._repository.get_profile(name, series)

    def recent_imports(self, limit: int = 20) -> tuple[ImportEventSummary, ...]:
        return self._repository.recent_imports(limit)

    def delete_import_event(self, import_event_id: int) -> bool:
        return self._repository.delete_import_event(import_event_id)
