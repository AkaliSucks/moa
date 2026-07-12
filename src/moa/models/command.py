from moa.models.base import MOAModel


class MudaeFlagDefinition(MOAModel):
    """Reference meaning for one Mudae list/search flag."""

    token: str
    category: str
    meaning: str
    notes: str | None = None
    match_prefix: str | None = None


class ParsedMudaeFlag(MOAModel):
    """One flag recognized inside a combined Mudae command token."""

    token: str
    definition: MudaeFlagDefinition


class MudaeCommandQuery(MOAModel):
    """A Mudae command token and its include/exclude search arguments."""

    raw_query: str
    command: str
    flags: tuple[ParsedMudaeFlag, ...]
    arguments: tuple[str, ...]
    exclusions: tuple[str, ...]
