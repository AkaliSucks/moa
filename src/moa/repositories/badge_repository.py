"""Repository for Kakera Badge reference definitions."""

from typing import Protocol

from moa.loader.badge_loader import load_badges
from moa.models.badge import BadgeDefinition


class BadgeRepositoryProtocol(Protocol):
    """Storage contract required by :class:`BadgeService`."""

    def all(self) -> tuple[BadgeDefinition, ...]: ...

    def get(self, badge_id: str) -> BadgeDefinition | None: ...


class BadgeRepository:
    """Read-only access to validated Kakera Badge data."""

    def all(self) -> tuple[BadgeDefinition, ...]:
        return load_badges()

    def get(self, badge_id: str) -> BadgeDefinition | None:
        normalized_id = badge_id.strip().upper()
        return next(
            (badge for badge in self.all() if badge.id == normalized_id),
            None,
        )
