"""Business operations for MOA's persisted character catalog."""

from moa.models.catalog import RankedCatalogCharacter, TopImportResult
from moa.models.character import TopPage
from moa.repositories.catalog_repository import CatalogRepository, CatalogRepositoryProtocol


class CatalogService:
    """Catalog operations independent of the SQLite implementation."""

    def __init__(self, repository: CatalogRepositoryProtocol | None = None) -> None:
        self._repository = repository or CatalogRepository()

    def import_top_page(self, page: TopPage, raw_message: str, source: str) -> TopImportResult:
        return self._repository.import_top_page(page, raw_message, source)

    def top(self, limit: int = 15) -> tuple[RankedCatalogCharacter, ...]:
        return self._repository.top(limit)

    def character_count(self) -> int:
        return self._repository.character_count()
