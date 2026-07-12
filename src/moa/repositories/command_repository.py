from typing import Protocol

from moa.loader.command_loader import load_commands
from moa.models.command import MudaeFlagDefinition


class CommandRepositoryProtocol(Protocol):
    """Storage contract for Mudae command and flag knowledge."""

    def all(self) -> tuple[MudaeFlagDefinition, ...]: ...


class CommandRepository:
    """Read-only access to validated Mudae flag definitions."""

    def all(self) -> tuple[MudaeFlagDefinition, ...]:
        return load_commands()
