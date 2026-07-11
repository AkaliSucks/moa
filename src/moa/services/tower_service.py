from moa.models.tower import TowerPerk
from moa.repositories.tower_repository import TowerRepository, TowerRepositoryProtocol


class TowerService:
    """Tower business operations independent of the backing data source."""

    def __init__(self, repository: TowerRepositoryProtocol | None = None) -> None:
        self._repository = repository or TowerRepository()

    def all(self) -> tuple[TowerPerk, ...]:
        return self._repository.all()

    def get(self, perk_id: int) -> TowerPerk | None:
        return self._repository.get(perk_id)
