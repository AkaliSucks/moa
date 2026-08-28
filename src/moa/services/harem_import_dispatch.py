"""Normalize `$mm`-family imports while preserving their response profiles."""

from dataclasses import dataclass
from enum import StrEnum

from moa.commands.registry import (
    COMMAND_REGISTRY,
    CommandCapability,
    CommandMatch,
    InvocationSource,
)
from moa.parser.mudae import MudaeTextParser
from moa.services.catalog_service import CatalogService
from moa.services.command_service import CommandService


class HaremResponseProfile(StrEnum):
    """The supported `$mm` response shapes with distinct durable meanings."""

    KEYED = "keyed_harem"
    RANKED = "ranked_harem"

    @property
    def import_kind(self) -> str:
        """Return the existing automatic-import kind for this profile."""
        return "harem" if self is HaremResponseProfile.KEYED else "ranked_harem"


@dataclass(frozen=True, slots=True)
class CanonicalHaremImport:
    """Registry-normalized identity for one `$mm` import response profile."""

    canonical_command: str
    flags: tuple[str, ...]
    response_profile: HaremResponseProfile


@dataclass(frozen=True, slots=True)
class HaremImportDispatchResult:
    """The existing parsed page and persistence result selected by one profile."""

    dispatch: CanonicalHaremImport
    page: object
    result: object


def resolve_harem_import(raw_invocation: str) -> CanonicalHaremImport | None:
    """Normalize a raw `$mm` invocation through the declarative registry."""
    match = COMMAND_REGISTRY.lookup(
        raw_invocation,
        source=InvocationSource.TEXT,
        capability=CommandCapability.LISTENER,
    )
    return harem_import_from_command_match(match)


def harem_import_from_command_match(
    match: CommandMatch | None,
) -> CanonicalHaremImport | None:
    """Normalize an already-recognized harem command without a second lookup."""
    if match is None or match.canonical_name != "harem":
        return None
    if match.expected_response == "ranked_harem":
        profile = HaremResponseProfile.RANKED
    elif match.expected_response == "harem":
        profile = HaremResponseProfile.KEYED
    else:
        return None
    return CanonicalHaremImport(
        canonical_command="mm",
        flags=_normalized_flags(match),
        response_profile=profile,
    )


def harem_import_from_response_kind(
    response_kind: str,
) -> CanonicalHaremImport | None:
    """Map an already-detected harem response to its canonical command model."""
    if response_kind == "harem":
        return CanonicalHaremImport("mm", (), HaremResponseProfile.KEYED)
    if response_kind == "ranked_harem":
        return CanonicalHaremImport("mm", ("r",), HaremResponseProfile.RANKED)
    return None


def require_harem_import(raw_invocation: str) -> CanonicalHaremImport:
    """Return a supported normalized harem import or fail at the wiring seam."""
    dispatch = resolve_harem_import(raw_invocation)
    if dispatch is None:
        raise RuntimeError(f"Unsupported canonical harem invocation: {raw_invocation}")
    return dispatch


class HaremImportDispatcher:
    """Select existing keyed or ranked adapters from canonical harem metadata."""

    def __init__(
        self,
        parser: MudaeTextParser | None = None,
        catalog_service: CatalogService | None = None,
    ) -> None:
        self._parser = parser
        self._catalog = catalog_service

    def import_page(
        self,
        dispatch: CanonicalHaremImport,
        raw_message: str,
        server_name: str,
        account_name: str,
        source: str,
        scan_id: int | None = None,
    ) -> HaremImportDispatchResult:
        """Use the existing profile-specific parser and persistence adapter."""
        parser = self._parser or MudaeTextParser()
        if dispatch.response_profile is HaremResponseProfile.KEYED:
            page = parser.parse_harem_key_page(raw_message)
            result = (self._catalog or CatalogService()).import_harem_key_page(
                page, server_name, account_name, raw_message, source, scan_id
            )
        else:
            page = parser.parse_ranked_harem_page(raw_message)
            result = (self._catalog or CatalogService()).import_ranked_harem_page(
                page, server_name, account_name, raw_message, source, scan_id
            )
        return HaremImportDispatchResult(dispatch, page, result)


def _normalized_flags(match: CommandMatch) -> tuple[str, ...]:
    """Use the command reference when possible while retaining tolerant listener forms."""
    try:
        query = CommandService().explain(f"${match.normalized_token}")
    except ValueError:
        return ("r",) if match.modifier_text.casefold().startswith("r") else ()
    return tuple(flag.token for flag in query.flags)
