"""Repository for immutable Kakeraloot reward definitions."""

from typing import Protocol

from moa.loader.loot_loader import load_kakeraloots
from moa.models.loot import KakeralootDefinition


class KakeralootRepositoryProtocol(Protocol):
    """Storage contract required by :class:`KakeralootService`."""

    def all(self) -> tuple[KakeralootDefinition, ...]: ...

    def get(self, loot_id: str) -> KakeralootDefinition | None: ...


class KakeralootRepository:
    """Read-only access to validated universal Kakeraloot definitions."""

    def all(self) -> tuple[KakeralootDefinition, ...]:
        return load_kakeraloots()

    def get(self, loot_id: str) -> KakeralootDefinition | None:
        normalized_id = loot_id.strip().upper()
        return next((loot for loot in self.all() if loot.id == normalized_id), None)
