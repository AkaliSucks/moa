from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GameObject:
    """Base class for all immutable Mudae data."""

    id: int
    name: str