"""Repository for immutable Mudae character-key definitions."""

from typing import Protocol

from moa.loader.key_loader import load_key_tiers
from moa.models.key import KeyTierDefinition


class KeyRepositoryProtocol(Protocol):
    """Storage contract required by :class:`KeyService`."""

    def all(self) -> tuple[KeyTierDefinition, ...]: ...

    def get(self, key_id: str) -> KeyTierDefinition | None: ...


class KeyRepository:
    """Read-only access to validated universal Mudae key definitions."""

    def all(self) -> tuple[KeyTierDefinition, ...]:
        return load_key_tiers()

    def get(self, key_id: str) -> KeyTierDefinition | None:
        normalized_id = key_id.strip().upper()
        return next((tier for tier in self.all() if tier.id == normalized_id), None)
