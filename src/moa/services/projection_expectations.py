"""Shared source-family authority for durable expected projection identities."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Callable, Final, Mapping

from moa.repositories.catalog_repository import CatalogRepository
from moa.services.projection_authority import PROJECTION_AUTHORITY_BY_KIND


class DurableProjectionExpectationFactsError(RuntimeError):
    """Raised when required durable source provenance is missing or malformed."""


class Expectedness(Enum):
    """Whether one possible projection identity is known to be expected."""

    EXPECTED = "expected"
    NOT_EXPECTED = "not_expected"
    UNKNOWN = "unknown"


class DurableRollBaseEvidenceState(Enum):
    """Bounded explanations for the durable Roll base-observation branch."""

    ABSENT = "absent"
    COHERENT = "coherent"
    INCOHERENT = "incoherent"
    MULTIPLE = "multiple"


class DurableRollKeyEvidenceState(Enum):
    """Bounded explanations for the durable Roll key-observation branch."""

    ABSENT = "absent"
    COHERENT_MATCH = "coherent_match"
    MISMATCHED = "mismatched"
    MULTIPLE = "multiple"


@dataclass(frozen=True, slots=True)
class ExpectedProjectionIdentity:
    """One canonical durable projection-link identity."""

    projection_kind: str
    projection_slot: str


@dataclass(frozen=True, slots=True)
class ExpectedProjectionAssessment:
    """The expectedness of one source family's possible projection kind."""

    projection_kind: str
    expectedness: Expectedness
    identity: ExpectedProjectionIdentity | None = None

    def __post_init__(self) -> None:
        if self.expectedness is Expectedness.EXPECTED:
            if self.identity is None or self.identity.projection_kind != self.projection_kind:
                raise ValueError("EXPECTED assessments require a same-kind identity")
        elif self.identity is not None:
            raise ValueError("Only EXPECTED assessments may carry an identity")


@dataclass(frozen=True, slots=True)
class ExpectedProjectionSet:
    """The complete possible-kind assessment for one durable source family."""

    assessments: tuple[ExpectedProjectionAssessment, ...]

    def __post_init__(self) -> None:
        kinds = tuple(assessment.projection_kind for assessment in self.assessments)
        if len(kinds) != len(set(kinds)):
            raise ValueError("projection kinds must be unique within an expected set")

    @property
    def known_expected_identities(self) -> tuple[ExpectedProjectionIdentity, ...]:
        """Return all known expected identities in deterministic policy order."""

        return tuple(
            assessment.identity
            for assessment in self.assessments
            if assessment.expectedness is Expectedness.EXPECTED and assessment.identity is not None
        )

    def expectedness_for(self, observed: ExpectedProjectionIdentity) -> Expectedness:
        """Classify one observed identity against this complete expected set."""

        for assessment in self.assessments:
            if assessment.projection_kind != observed.projection_kind:
                continue
            if assessment.expectedness is not Expectedness.EXPECTED:
                return assessment.expectedness
            return (
                Expectedness.EXPECTED
                if assessment.identity == observed
                else Expectedness.NOT_EXPECTED
            )
        return Expectedness.NOT_EXPECTED


@dataclass(frozen=True, slots=True)
class ProjectionExpectationFacts:
    """Normalized, parser-independent facts used by expected-set policies."""

    source_family: str
    server: str | None = None
    account: str | None = None
    character: str | None = None
    series: str | None = None
    scan_id: int | None = None
    page_number: int | None = None
    roll_key_present: bool | None = False
    roll_key_count_present: bool | None = None
    roll_key_type: str | None = None
    roll_rank_present: bool | None = False
    roll_kakera_value_present: bool | None = False


@dataclass(frozen=True, slots=True)
class DurableRollProjectionExpectationEvidence:
    """Privacy-safe evidence behind the canonical durable Roll facts."""

    facts: ProjectionExpectationFacts
    base_roll_row_count: int
    base_roll_coherence: DurableRollBaseEvidenceState
    matching_key_row_count: int
    nonmatching_or_ambiguous_key_row_count: int
    key_evidence_state: DurableRollKeyEvidenceState
    claim_rank_present: bool | None
    kakera_value_present: bool | None


@dataclass(frozen=True, slots=True)
class ProjectionExpectationPolicy:
    """One source family's fixed possible-kind inventory and resolver."""

    source_family: str
    possible_projection_kinds: tuple[str, ...]
    resolver: Callable[[ProjectionExpectationFacts], ExpectedProjectionSet]


def build_projection_expectation_facts(
    source_family: str,
    *,
    server: str,
    account: str | None = None,
    character: str | None = None,
    series: str | None = None,
    scan_id: int | None = None,
    page_number: int | None = None,
    roll_key_present: bool | None = False,
    roll_key_count_present: bool | None = None,
    roll_key_type: str | None = None,
    roll_rank_present: bool | None = False,
    roll_kakera_value_present: bool | None = False,
) -> ProjectionExpectationFacts:
    """Normalize already-known first-processing facts with catalog semantics."""

    normalize = CatalogRepository._normalize
    return ProjectionExpectationFacts(
        source_family=source_family,
        server=normalize(server),
        account=normalize(account) if account is not None else None,
        character=normalize(character) if character is not None else None,
        series=normalize(series) if series is not None else None,
        scan_id=scan_id,
        page_number=page_number,
        roll_key_present=roll_key_present,
        roll_key_count_present=roll_key_count_present,
        roll_key_type=normalize(roll_key_type) if roll_key_type is not None else None,
        roll_rank_present=roll_rank_present,
        roll_kakera_value_present=roll_kakera_value_present,
    )


def _serialize_slot(values: Mapping[str, object]) -> str:
    return json.dumps(
        values,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def server_projection_slot(server: str) -> str:
    """Build the canonical normalized server projection slot."""

    return _serialize_slot({"server": server})


def server_account_projection_slot(server: str, account: str) -> str:
    """Build the canonical normalized server/account projection slot."""

    return _serialize_slot({"account": account, "server": server})


def claim_projection_slot(server: str, account: str, character: str) -> str:
    """Build the canonical normalized Claim projection slot."""

    return _serialize_slot({"account": account, "character_name": character, "server": server})


def roll_projection_slot(
    server: str,
    account: str,
    character: str,
    series: str,
    *,
    key_type: str | None = None,
) -> str:
    """Build the canonical normalized Roll projection slot."""

    values: dict[str, object] = {
        "account": account,
        "character": character,
        "series": series,
        "server": server,
    }
    if key_type is not None:
        values["key_type"] = key_type
    return _serialize_slot(values)


def antidisable_page_projection_slot(
    server: str,
    account: str,
    scan_id: int,
    page_number: int,
) -> str:
    """Build the canonical normalized Antidisable scan/page projection slot."""

    return _serialize_slot(
        {
            "account": account,
            "page_number": page_number,
            "scan_id": scan_id,
            "server": server,
        }
    )


def _assessment(
    kind: str,
    expectedness: Expectedness,
    slot: str | None = None,
) -> ExpectedProjectionAssessment:
    identity = (
        ExpectedProjectionIdentity(kind, slot)
        if expectedness is Expectedness.EXPECTED and slot is not None
        else None
    )
    if expectedness is Expectedness.EXPECTED and identity is None:
        expectedness = Expectedness.UNKNOWN
    return ExpectedProjectionAssessment(kind, expectedness, identity)


def _server_singleton(kind: str) -> Callable[[ProjectionExpectationFacts], ExpectedProjectionSet]:
    def resolve(facts: ProjectionExpectationFacts) -> ExpectedProjectionSet:
        if facts.server is None:
            return ExpectedProjectionSet((_assessment(kind, Expectedness.UNKNOWN),))
        return ExpectedProjectionSet(
            (_assessment(kind, Expectedness.EXPECTED, server_projection_slot(facts.server)),)
        )

    return resolve


def _account_singleton(
    kind: str,
) -> Callable[[ProjectionExpectationFacts], ExpectedProjectionSet]:
    def resolve(facts: ProjectionExpectationFacts) -> ExpectedProjectionSet:
        if facts.server is None or facts.account is None:
            return ExpectedProjectionSet((_assessment(kind, Expectedness.UNKNOWN),))
        return ExpectedProjectionSet(
            (
                _assessment(
                    kind,
                    Expectedness.EXPECTED,
                    server_account_projection_slot(facts.server, facts.account),
                ),
            )
        )

    return resolve


def _resolve_claim(facts: ProjectionExpectationFacts) -> ExpectedProjectionSet:
    kind = "catalog.claim"
    if facts.server is None or facts.account is None or facts.character is None:
        return ExpectedProjectionSet((_assessment(kind, Expectedness.UNKNOWN),))
    return ExpectedProjectionSet(
        (
            _assessment(
                kind,
                Expectedness.EXPECTED,
                claim_projection_slot(facts.server, facts.account, facts.character),
            ),
        )
    )


def _resolve_roll(facts: ProjectionExpectationFacts) -> ExpectedProjectionSet:
    base = (facts.server, facts.account, facts.character, facts.series)
    if any(value is None for value in base):
        return ExpectedProjectionSet(
            tuple(
                _assessment(kind, Expectedness.UNKNOWN)
                for kind in (
                    "catalog.roll",
                    "catalog.roll_key",
                    "catalog.roll_rank",
                    "catalog.roll_server_character",
                )
            )
        )
    server, account, character, series = base
    assert server is not None and account is not None
    assert character is not None and series is not None
    base_slot = roll_projection_slot(server, account, character, series)
    if facts.roll_key_count_present is not None:
        key_expectedness = (
            Expectedness.EXPECTED
            if facts.roll_key_count_present and facts.roll_key_type is not None
            else Expectedness.NOT_EXPECTED
        )
    else:
        key_expectedness = (
            Expectedness.UNKNOWN
            if facts.roll_key_present is None
            else Expectedness.EXPECTED
            if facts.roll_key_present
            else Expectedness.NOT_EXPECTED
        )
    rank_expectedness = (
        Expectedness.UNKNOWN
        if facts.roll_rank_present is None
        else Expectedness.EXPECTED
        if facts.roll_rank_present
        else Expectedness.NOT_EXPECTED
    )
    server_character_expectedness = (
        Expectedness.UNKNOWN
        if facts.roll_kakera_value_present is None
        else Expectedness.EXPECTED
        if facts.roll_kakera_value_present
        else Expectedness.NOT_EXPECTED
    )
    key_slot = (
        roll_projection_slot(
            server,
            account,
            character,
            series,
            key_type=facts.roll_key_type,
        )
        if facts.roll_key_type is not None
        else None
    )
    return ExpectedProjectionSet(
        (
            _assessment("catalog.roll", Expectedness.EXPECTED, base_slot),
            _assessment("catalog.roll_key", key_expectedness, key_slot),
            _assessment("catalog.roll_rank", rank_expectedness, base_slot),
            _assessment(
                "catalog.roll_server_character",
                server_character_expectedness,
                base_slot,
            ),
        )
    )


def _resolve_antidisable(facts: ProjectionExpectationFacts) -> ExpectedProjectionSet:
    kind = "catalog.antidisable_page"
    if (
        facts.server is None
        or facts.account is None
        or facts.scan_id is None
        or facts.page_number is None
    ):
        return ExpectedProjectionSet((_assessment(kind, Expectedness.UNKNOWN),))
    return ExpectedProjectionSet(
        (
            _assessment(
                kind,
                Expectedness.EXPECTED,
                antidisable_page_projection_slot(
                    facts.server,
                    facts.account,
                    facts.scan_id,
                    facts.page_number,
                ),
            ),
        )
    )


_POLICIES: Final[tuple[ProjectionExpectationPolicy, ...]] = (
    ProjectionExpectationPolicy("antidisable", ("catalog.antidisable_page",), _resolve_antidisable),
    ProjectionExpectationPolicy("claim", ("catalog.claim",), _resolve_claim),
    ProjectionExpectationPolicy(
        "disablelist", ("catalog.disablelist",), _account_singleton("catalog.disablelist")
    ),
    ProjectionExpectationPolicy(
        "kakeraloot_settings",
        ("catalog.kakeraloot_settings",),
        _server_singleton("catalog.kakeraloot_settings"),
    ),
    ProjectionExpectationPolicy(
        "kakeraloot_state",
        ("catalog.kakeraloot_state",),
        _account_singleton("catalog.kakeraloot_state"),
    ),
    ProjectionExpectationPolicy(
        "kakera_state", ("catalog.kakera_state",), _account_singleton("catalog.kakera_state")
    ),
    ProjectionExpectationPolicy(
        "mudapins", ("catalog.mudapins",), _account_singleton("catalog.mudapins")
    ),
    ProjectionExpectationPolicy(
        "player_bonus", ("catalog.player_bonus",), _account_singleton("catalog.player_bonus")
    ),
    ProjectionExpectationPolicy(
        "profile", ("catalog.profile",), _account_singleton("catalog.profile")
    ),
    ProjectionExpectationPolicy(
        "roll",
        (
            "catalog.roll",
            "catalog.roll_key",
            "catalog.roll_rank",
            "catalog.roll_server_character",
        ),
        _resolve_roll,
    ),
    ProjectionExpectationPolicy(
        "server_settings",
        ("catalog.server_settings",),
        _server_singleton("catalog.server_settings"),
    ),
    ProjectionExpectationPolicy(
        "sphere_result", ("catalog.sphere_result",), _account_singleton("catalog.sphere_result")
    ),
    ProjectionExpectationPolicy(
        "timer_state", ("catalog.timer_state",), _account_singleton("catalog.timer_state")
    ),
    ProjectionExpectationPolicy(
        "tower_state", ("catalog.tower_state",), _account_singleton("catalog.tower_state")
    ),
    ProjectionExpectationPolicy(
        "top_page", ("catalog.top_page",), _server_singleton("catalog.top_page")
    ),
    ProjectionExpectationPolicy(
        "topx_page", ("catalog.topx_page",), _account_singleton("catalog.topx_page")
    ),
    ProjectionExpectationPolicy(
        "wishlist", ("catalog.wishlist",), _account_singleton("catalog.wishlist")
    ),
)

PROJECTION_EXPECTATION_POLICIES: Final[Mapping[str, ProjectionExpectationPolicy]] = (
    MappingProxyType({policy.source_family: policy for policy in _POLICIES})
)


def resolve_expected_projections(
    facts: ProjectionExpectationFacts,
) -> ExpectedProjectionSet:
    """Resolve normalized source-family facts without I/O or parser dependencies."""

    return PROJECTION_EXPECTATION_POLICIES[facts.source_family].resolver(facts)


def _normalized_attribution_facts(
    connection: sqlite3.Connection, source_event_id: int
) -> tuple[str | None, str | None]:
    server_row = connection.execute(
        """
        SELECT status, server_name
        FROM discord_source_event_server_attributions
        WHERE source_event_id = ?
        """,
        (source_event_id,),
    ).fetchone()
    account_row = connection.execute(
        """
        SELECT status, server_name, account_name
        FROM discord_source_event_account_attributions
        WHERE source_event_id = ?
        """,
        (source_event_id,),
    ).fetchone()
    server = (
        CatalogRepository._normalize(str(server_row["server_name"]))
        if server_row is not None
        and str(server_row["status"]) == "resolved"
        and server_row["server_name"] is not None
        else None
    )
    account_server = (
        CatalogRepository._normalize(str(account_row["server_name"]))
        if account_row is not None
        and str(account_row["status"]) == "resolved"
        and account_row["server_name"] is not None
        else None
    )
    account = (
        CatalogRepository._normalize(str(account_row["account_name"]))
        if account_row is not None
        and str(account_row["status"]) == "resolved"
        and account_row["account_name"] is not None
        and account_server == server
        else None
    )
    return server, account


def _source_expectation_context(
    connection: sqlite3.Connection,
    source_event_id: int,
) -> tuple[int, str, str | None, str | None]:
    if (
        isinstance(source_event_id, bool)
        or not isinstance(source_event_id, int)
        or source_event_id <= 0
    ):
        raise ValueError("source_event_id must be a positive integer")
    source = connection.execute(
        "SELECT legacy_import_event_id FROM discord_source_events WHERE id = ?",
        (source_event_id,),
    ).fetchone()
    if source is None or source["legacy_import_event_id"] is None:
        raise DurableProjectionExpectationFactsError(
            f"source event {source_event_id} has no durable import provenance"
        )
    import_event_id = int(source["legacy_import_event_id"])
    imported = connection.execute(
        "SELECT kind FROM import_events WHERE id = ?", (import_event_id,)
    ).fetchone()
    if imported is None:
        raise DurableProjectionExpectationFactsError(
            f"source event {source_event_id} has missing import provenance"
        )
    source_family = str(imported["kind"])
    if source_family not in PROJECTION_EXPECTATION_POLICIES:
        raise DurableProjectionExpectationFactsError(
            f"source event {source_event_id} has unsupported import kind {source_family!r}"
        )
    server, account = _normalized_attribution_facts(connection, source_event_id)
    return import_event_id, source_family, server, account


def _load_durable_roll_projection_expectation_evidence(
    connection: sqlite3.Connection,
    *,
    import_event_id: int,
    server: str | None,
    account: str | None,
) -> DurableRollProjectionExpectationEvidence:
    rows = connection.execute(
        """
        SELECT ro.claim_rank, ro.kakera_value, ro.account_context_id,
               ac.normalized_name AS account, sc.normalized_name AS server,
               c.normalized_name AS character, c.normalized_series AS series
        FROM roll_observations AS ro
        JOIN account_contexts AS ac ON ac.id = ro.account_context_id
        JOIN server_contexts AS sc ON sc.id = ac.server_context_id
        JOIN characters AS c ON c.id = ro.character_id
        WHERE ro.import_event_id = ?
        """,
        (import_event_id,),
    ).fetchall()
    base_row_count = len(rows)
    if base_row_count != 1:
        state = (
            DurableRollBaseEvidenceState.ABSENT
            if base_row_count == 0
            else DurableRollBaseEvidenceState.MULTIPLE
        )
        facts = ProjectionExpectationFacts(
            "roll",
            server,
            account,
            roll_key_present=None,
            roll_rank_present=None,
            roll_kakera_value_present=None,
        )
        return DurableRollProjectionExpectationEvidence(
            facts,
            base_row_count,
            state,
            0,
            0,
            DurableRollKeyEvidenceState.ABSENT,
            None,
            None,
        )

    row = rows[0]
    durable_server = str(row["server"])
    durable_account = str(row["account"])
    character = str(row["character"])
    series = str(row["series"])
    if server != durable_server or account != durable_account or not character or not series:
        facts = ProjectionExpectationFacts(
            "roll",
            server,
            account,
            roll_key_present=None,
            roll_rank_present=None,
            roll_kakera_value_present=None,
        )
        return DurableRollProjectionExpectationEvidence(
            facts,
            1,
            DurableRollBaseEvidenceState.INCOHERENT,
            0,
            0,
            DurableRollKeyEvidenceState.ABSENT,
            None,
            None,
        )

    key_rows = connection.execute(
        """
        SELECT hko.key_type, hko.account_context_id,
               c.normalized_name AS character, c.normalized_series AS series
        FROM harem_key_observations AS hko
        LEFT JOIN characters AS c ON c.id = hko.character_id
        WHERE hko.import_event_id = ?
        """,
        (import_event_id,),
    ).fetchall()
    matching_key_types: list[str] = []
    for key_row in key_rows:
        candidate = CatalogRepository._normalize(str(key_row["key_type"]))
        if (
            int(key_row["account_context_id"]) == int(row["account_context_id"])
            and key_row["character"] is not None
            and str(key_row["character"]) == character
            and key_row["series"] is not None
            and str(key_row["series"]) == series
            and candidate
        ):
            matching_key_types.append(candidate)
    matching_count = len(matching_key_types)
    nonmatching_count = len(key_rows) - matching_count
    if not key_rows:
        key_state = DurableRollKeyEvidenceState.ABSENT
    elif len(key_rows) > 1:
        key_state = DurableRollKeyEvidenceState.MULTIPLE
    elif matching_count == 1:
        key_state = DurableRollKeyEvidenceState.COHERENT_MATCH
    else:
        key_state = DurableRollKeyEvidenceState.MISMATCHED
    key_type = (
        matching_key_types[0] if key_state is DurableRollKeyEvidenceState.COHERENT_MATCH else None
    )
    facts = ProjectionExpectationFacts(
        "roll",
        server,
        account,
        character,
        series,
        roll_key_present=(
            True if key_state is DurableRollKeyEvidenceState.COHERENT_MATCH else None
        ),
        roll_key_type=key_type,
        roll_rank_present=row["claim_rank"] is not None,
        roll_kakera_value_present=row["kakera_value"] is not None,
    )
    return DurableRollProjectionExpectationEvidence(
        facts,
        1,
        DurableRollBaseEvidenceState.COHERENT,
        matching_count,
        nonmatching_count,
        key_state,
        facts.roll_rank_present,
        facts.roll_kakera_value_present,
    )


def load_durable_roll_projection_expectation_evidence(
    connection: sqlite3.Connection,
    source_event_id: int,
) -> DurableRollProjectionExpectationEvidence:
    """Load bounded evidence used by the canonical durable Roll expectedness rule."""

    import_event_id, source_family, server, account = _source_expectation_context(
        connection, source_event_id
    )
    if source_family != "roll":
        raise DurableProjectionExpectationFactsError(
            f"source event {source_event_id} is not a roll source"
        )
    return _load_durable_roll_projection_expectation_evidence(
        connection,
        import_event_id=import_event_id,
        server=server,
        account=account,
    )


def load_durable_projection_expectation_facts(
    connection: sqlite3.Connection,
    source_event_id: int,
) -> ProjectionExpectationFacts:
    """Load normalized expectedness facts using caller-owned, read-only SQL."""

    import_event_id, source_family, server, account = _source_expectation_context(
        connection, source_event_id
    )

    if source_family == "claim":
        rows = connection.execute(
            """
            SELECT co.normalized_character_name, ac.normalized_name AS account,
                   sc.normalized_name AS server
            FROM claim_observations AS co
            JOIN account_contexts AS ac ON ac.id = co.account_context_id
            JOIN server_contexts AS sc ON sc.id = ac.server_context_id
            WHERE co.import_event_id = ?
            """,
            (import_event_id,),
        ).fetchall()
        if len(rows) == 1:
            row = rows[0]
            durable_server = str(row["server"])
            durable_account = str(row["account"])
            character = str(row["normalized_character_name"])
            if server == durable_server and account == durable_account and character:
                return ProjectionExpectationFacts(
                    source_family, server, account, character=character
                )
        return ProjectionExpectationFacts(source_family, server, account)

    if source_family == "roll":
        return _load_durable_roll_projection_expectation_evidence(
            connection,
            import_event_id=import_event_id,
            server=server,
            account=account,
        ).facts

    if source_family == "antidisable":
        rows = connection.execute(
            """
            SELECT hsp.harem_scan_id, hsp.page_number,
                   ac.normalized_name AS account, sc.normalized_name AS server,
                   hs.scan_kind
            FROM harem_scan_pages AS hsp
            JOIN harem_scans AS hs ON hs.id = hsp.harem_scan_id
            JOIN account_contexts AS ac ON ac.id = hs.account_context_id
            JOIN server_contexts AS sc ON sc.id = ac.server_context_id
            WHERE hsp.import_event_id = ?
            """,
            (import_event_id,),
        ).fetchall()
        if len(rows) == 1:
            row = rows[0]
            durable_server = str(row["server"])
            durable_account = str(row["account"])
            scan_id = int(row["harem_scan_id"])
            page_number = int(row["page_number"])
            if (
                str(row["scan_kind"]) == "antidisable"
                and server == durable_server
                and account == durable_account
                and scan_id > 0
                and page_number > 0
            ):
                return ProjectionExpectationFacts(
                    source_family,
                    server,
                    account,
                    scan_id=scan_id,
                    page_number=page_number,
                )
        return ProjectionExpectationFacts(source_family, server, account)

    return ProjectionExpectationFacts(source_family, server, account)


if len(PROJECTION_EXPECTATION_POLICIES) != 17:
    raise RuntimeError("projection expectation registry must own exactly 17 families")
_owned_kind_sequence = tuple(
    kind
    for policy in PROJECTION_EXPECTATION_POLICIES.values()
    for kind in policy.possible_projection_kinds
)
if len(_owned_kind_sequence) != len(set(_owned_kind_sequence)) or set(_owned_kind_sequence) != set(
    PROJECTION_AUTHORITY_BY_KIND
):
    raise RuntimeError("projection expectation registry must own all projection kinds exactly")
