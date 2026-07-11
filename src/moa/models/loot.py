"""Immutable reference definitions for Kakeraloot rewards."""

from typing import Literal

from moa.models.base import MOAModel


class KakeralootDefinition(MOAModel):
    """One possible Kakeraloot reward, independent of any player account."""

    id: str
    name: str
    category: Literal["rolls", "kakera", "rolling", "wishes", "utility", "collection"]
    guaranteed: bool
    progression_note: str
    description: str
