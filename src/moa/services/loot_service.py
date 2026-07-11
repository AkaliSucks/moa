"""Business operations for universal Kakeraloot reference data."""

from moa.models.loot import KakeralootDefinition
from moa.repositories.loot_repository import KakeralootRepository, KakeralootRepositoryProtocol


class KakeralootService:
    """Kakeraloot reference queries independent of the backing data source."""

    def __init__(self, repository: KakeralootRepositoryProtocol | None = None) -> None:
        self._repository = repository or KakeralootRepository()

    def all(self) -> tuple[KakeralootDefinition, ...]:
        return self._repository.all()

    def get(self, loot_id: str) -> KakeralootDefinition | None:
        return self._repository.get(loot_id)
