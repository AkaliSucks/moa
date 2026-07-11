"""Business operations for Kakera reaction reference data."""

from moa.models.reaction import ReactionDefinition
from moa.repositories.reaction_repository import (
    ReactionRepository,
    ReactionRepositoryProtocol,
)


class ReactionService:
    """Reaction queries independent of the backing data source."""

    def __init__(self, repository: ReactionRepositoryProtocol | None = None) -> None:
        self._repository = repository or ReactionRepository()

    def all(self) -> tuple[ReactionDefinition, ...]:
        return self._repository.all()

    def get(self, reaction_id: str) -> ReactionDefinition | None:
        return self._repository.get(reaction_id)
