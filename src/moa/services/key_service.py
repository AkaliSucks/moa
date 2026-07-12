"""Business operations for universal Mudae character-key data."""

from moa.models.key import KeyTierDefinition
from moa.repositories.key_repository import KeyRepository, KeyRepositoryProtocol


class KeyService:
    """Character-key reference queries independent of the backing data source."""

    def __init__(self, repository: KeyRepositoryProtocol | None = None) -> None:
        self._repository = repository or KeyRepository()

    def all(self) -> tuple[KeyTierDefinition, ...]:
        return self._repository.all()

    def get(self, key_id: str) -> KeyTierDefinition | None:
        return self._repository.get(key_id)
