"""Typed observations parsed from Mudae character output."""

from moa.models.base import MOAModel


class RankedCharacter(MOAModel):
    """One character entry from a global Mudae ranking page."""

    name: str
    series: str
    claim_rank: int


class TopPage(MOAModel):
    """A single parsed page from Mudae's `$top` output."""

    limit: int | None
    page_number: int | None
    page_count: int | None
    characters: tuple[RankedCharacter, ...]


class CharacterDetails(MOAModel):
    """The currently observed fields from a Mudae `$im` response."""

    name: str
    series: str
    gender: str | None
    roulette: str | None
    kakera_value: int | None
    claim_rank: int | None
    like_rank: int | None


class RollObservation(MOAModel):
    """The currently observed fields from one Mudae roll card."""

    name: str
    series: str
    claim_rank: int | None
    kakera_value: int | None
