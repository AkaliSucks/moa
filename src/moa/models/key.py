"""Immutable reference definitions for Mudae character keys."""

from moa.models.base import MOAModel


class KeyMilestone(MOAModel):
    """A permanent reward unlocked at one character key count."""

    key_count: int
    effects: tuple[str, ...]


class KeyTierDefinition(MOAModel):
    """One universal key tier, independent of a player's current harem."""

    id: str
    name: str
    minimum_key_count: int
    maximum_key_count: int | None
    description: str
    milestones: tuple[KeyMilestone, ...]
