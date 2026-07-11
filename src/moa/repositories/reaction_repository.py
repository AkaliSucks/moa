"""Repository for Kakera reaction reference definitions."""

from typing import Protocol

from moa.loader.reaction_loader import load_reactions
from moa.models.reaction import ReactionDefinition


class ReactionRepositoryProtocol(Protocol):
    """Storage contract required by :class:`ReactionService`."""

    def all(self) -> tuple[ReactionDefinition, ...]: ...

    def get(self, reaction_id: str) -> ReactionDefinition | None: ...


class ReactionRepository:
    """Read-only access to validated Kakera reaction data."""

    def all(self) -> tuple[ReactionDefinition, ...]:
        return load_reactions()

    def get(self, reaction_id: str) -> ReactionDefinition | None:
        normalized_id = reaction_id.strip().upper()
        return next(
            (reaction for reaction in self.all() if reaction.id == normalized_id),
            None,
        )
