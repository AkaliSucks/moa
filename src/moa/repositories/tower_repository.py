"""Repository for Kakera Tower reference definitions.

The implementation currently reads packaged JSON knowledge. Services depend on
this repository, rather than on the storage format, so a SQLite-backed
implementation can replace it later without changing business logic.
"""

from typing import Protocol

from moa.loader.knowledge_loader import load_tower
from moa.models.tower import TowerPerk


class TowerRepositoryProtocol(Protocol):
    """Storage contract required by TowerService."""

    def all(self) -> tuple[TowerPerk, ...]: ...

    def get(self, perk_id: int) -> TowerPerk | None: ...


class TowerRepository:
    """Read-only Kakera Tower reference data access."""

    def all(self) -> tuple[TowerPerk, ...]:
        return tuple(load_tower())

    def get(self, perk_id: int) -> TowerPerk | None:
        return next((perk for perk in self.all() if perk.id == perk_id), None)
