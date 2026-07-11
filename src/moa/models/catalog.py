"""Models for MOA's local character catalog and rank history."""

from datetime import datetime

from moa.models.base import MOAModel


class CatalogCharacter(MOAModel):
    """A canonical character record stored by MOA."""

    id: int
    name: str
    series: str
    gender: str | None
    roulette: str | None


class CatalogRankSnapshot(MOAModel):
    """A timestamped rank observation attached to a catalog character."""

    character_id: int
    claim_rank: int | None
    like_rank: int | None
    observed_at: datetime
    import_event_id: int


class RankedCatalogCharacter(MOAModel):
    """A character and its most recently imported ranking information."""

    character: CatalogCharacter
    claim_rank: int
    like_rank: int | None
    observed_at: datetime


class TopImportResult(MOAModel):
    """Summary of a persisted `$top` import."""

    import_event_id: int
    characters_imported: int
    observed_at: datetime
