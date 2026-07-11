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


class CharacterDetailsImportResult(MOAModel):
    """Summary of one persisted `$im` import."""

    import_event_id: int
    character_id: int
    server_name: str
    observed_at: datetime


class ServerKakeraObservation(MOAModel):
    """The most recently observed Kakera value for one character on one server."""

    server_name: str
    kakera_value: int | None
    observed_at: datetime


class CharacterProfile(MOAModel):
    """A catalog character with its latest global and server-specific observations."""

    character: CatalogCharacter
    claim_rank: int | None
    like_rank: int | None
    rank_observed_at: datetime | None
    server_observations: tuple[ServerKakeraObservation, ...]


class ImportEventSummary(MOAModel):
    """A compact view of one raw Mudae import event."""

    id: int
    kind: str
    source: str
    server_name: str | None
    observed_at: datetime


class HaremKeyImportResult(MOAModel):
    """Summary of one persisted `$mmy=` keyed-harem page."""

    import_event_id: int
    server_name: str
    account_name: str
    entries_imported: int
    entries_linked: int
    observed_at: datetime


class HaremKeyObservation(MOAModel):
    """The latest imported key state for one harem entry."""

    character_name: str
    character: CatalogCharacter | None
    key_type: str
    key_count: int
    kakera_value: int | None
    observed_at: datetime
