"""Bounded, read-only inventory of parser-backed roll key-display candidates."""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import re

from moa.database.sqlite import connect_read_only
from moa.services.roll_key_display_candidate_evaluator import (
    RollKeyDisplayCandidate,
    RollKeyDisplayCandidateEvaluator,
    RollKeyDisplayCandidateStatus as Status,
)


class RollKeyDisplayInventoryMode(str, Enum):
    CANDIDATES = "candidates"
    AUDIT = "audit"


@dataclass(frozen=True)
class RollKeyDisplayInventoryRow:
    source_event_id: int | None
    import_event_id: int | None
    roll_observation_id: int
    current_displayed_key_count_present: bool | None
    candidate_status: Status
    failure_category: str | None
    proposed_value: bool | None
    parser_version: str | None
    retention_state: str

    @classmethod
    def from_candidate(cls, candidate: RollKeyDisplayCandidate) -> "RollKeyDisplayInventoryRow":
        assert candidate.roll_observation_id is not None
        return cls(
            candidate.source_event_id,
            candidate.import_event_id,
            candidate.roll_observation_id,
            candidate.current_displayed_key_count_present,
            candidate.status,
            candidate.failure_category,
            candidate.proposed_value,
            candidate.parser_version,
            candidate.retention_state,
        )


@dataclass(frozen=True)
class RollKeyDisplayInventorySummary:
    total_evaluated: int
    null_targets: int
    eligible_proposed_false: int
    eligible_proposed_true: int
    established_matching: int
    established_conflict: int
    source_missing_or_not_succeeded: int
    source_expired: int
    source_unusable: int
    import_link_failures: int
    non_roll_sources: int
    parser_provenance_incoherence: int
    attribution_failures: int
    roll_target_failures: int
    parse_failures: int
    identity_mismatches: int
    truncated: bool
    more_available: bool
    status_counts: dict[str, int]

    @classmethod
    def from_rows(
        cls,
        rows: tuple[RollKeyDisplayInventoryRow, ...],
        more: bool,
    ) -> "RollKeyDisplayInventorySummary":
        counts = {status.value: 0 for status in Status}
        for row in rows:
            counts[row.candidate_status.value] += 1

        def total(*statuses: Status) -> int:
            return sum(counts[status.value] for status in statuses)

        return cls(
            total_evaluated=len(rows),
            null_targets=sum(row.current_displayed_key_count_present is None for row in rows),
            eligible_proposed_false=sum(
                row.candidate_status is Status.ELIGIBLE_NULL and row.proposed_value is False
                for row in rows
            ),
            eligible_proposed_true=sum(
                row.candidate_status is Status.ELIGIBLE_NULL and row.proposed_value is True
                for row in rows
            ),
            established_matching=total(Status.ALREADY_ESTABLISHED_MATCHING),
            established_conflict=total(Status.ALREADY_ESTABLISHED_CONFLICT),
            source_missing_or_not_succeeded=total(
                Status.SOURCE_MISSING, Status.SOURCE_NOT_SUCCEEDED
            ),
            source_expired=total(Status.SOURCE_EXPIRED),
            source_unusable=total(Status.SOURCE_UNUSABLE),
            import_link_failures=total(
                Status.IMPORT_LINK_MISSING, Status.IMPORT_LINK_AMBIGUOUS
            ),
            non_roll_sources=total(Status.NON_ROLL_SOURCE),
            parser_provenance_incoherence=total(
                Status.PARSER_PROVENANCE_MISSING_OR_AMBIGUOUS
            ),
            attribution_failures=total(
                Status.ATTRIBUTION_UNRESOLVED,
                Status.ATTRIBUTION_AMBIGUOUS,
                Status.ATTRIBUTION_CONFLICT,
            ),
            roll_target_failures=total(
                Status.ROLL_TARGET_MISSING, Status.ROLL_TARGET_AMBIGUOUS
            ),
            parse_failures=total(Status.PARSE_FAILURE),
            identity_mismatches=total(Status.IDENTITY_MISMATCH),
            truncated=more,
            more_available=more,
            status_counts=counts,
        )


@dataclass(frozen=True)
class RollKeyDisplayInventoryReport:
    report_schema_version: int
    generated_at_utc: str
    sqlite_user_version: int
    mode: RollKeyDisplayInventoryMode
    ordering_key: str
    after_roll_observation_id: int
    next_cursor: int
    rows_evaluated: int
    truncated: bool
    artifact_label: str | None
    informational_only: bool
    requires_re_evaluation_for_mutation: bool
    rows: tuple[RollKeyDisplayInventoryRow, ...]
    summary: RollKeyDisplayInventorySummary


class RollKeyDisplayCandidateInventoryService:
    """Page durable roll IDs with a read-only connection and no writer lease."""

    MAX_PAGE_SIZE = 1000

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def scan(
        self,
        *,
        mode: RollKeyDisplayInventoryMode = RollKeyDisplayInventoryMode.CANDIDATES,
        limit: int = 100,
        after_roll_observation_id: int = 0,
        artifact_label: str | None = None,
    ) -> RollKeyDisplayInventoryReport:
        if not isinstance(mode, RollKeyDisplayInventoryMode):
            raise ValueError("mode must be a RollKeyDisplayInventoryMode")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= self.MAX_PAGE_SIZE
        ):
            raise ValueError("limit must be between 1 and 1000")
        if (
            isinstance(after_roll_observation_id, bool)
            or not isinstance(after_roll_observation_id, int)
            or after_roll_observation_id < 0
        ):
            raise ValueError("after_roll_observation_id must be nonnegative")
        if artifact_label is not None and (
            not isinstance(artifact_label, str)
            or re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", artifact_label) is None
        ):
            raise ValueError("artifact_label must be a short opaque identifier")

        with closing(connect_read_only(self._database_path)) as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            try:
                user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                condition = (
                    "AND displayed_key_count_present IS NULL"
                    if mode is RollKeyDisplayInventoryMode.CANDIDATES
                    else ""
                )
                targets = connection.execute(
                    "SELECT id FROM roll_observations WHERE id > ? "
                    + condition
                    + " ORDER BY id LIMIT ?",
                    (after_roll_observation_id, limit + 1),
                ).fetchall()
                page = targets[:limit]
                candidates = tuple(
                    RollKeyDisplayCandidateEvaluator.evaluate(
                        connection, roll_observation_id=int(target["id"])
                    )
                    for target in page
                )
            finally:
                connection.rollback()
        rows = tuple(RollKeyDisplayInventoryRow.from_candidate(candidate) for candidate in candidates)
        more = len(targets) > limit
        summary = RollKeyDisplayInventorySummary.from_rows(rows, more)
        return RollKeyDisplayInventoryReport(
            report_schema_version=1,
            generated_at_utc=datetime.now(timezone.utc).isoformat(),
            sqlite_user_version=user_version,
            mode=mode,
            ordering_key="roll_observations.id",
            after_roll_observation_id=after_roll_observation_id,
            next_cursor=rows[-1].roll_observation_id if rows else after_roll_observation_id,
            rows_evaluated=len(rows),
            truncated=more,
            artifact_label=artifact_label,
            informational_only=True,
            requires_re_evaluation_for_mutation=True,
            rows=rows,
            summary=summary,
        )
