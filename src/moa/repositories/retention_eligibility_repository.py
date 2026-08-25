"""Read-only SQL and classification for raw evidence retention eligibility."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import sqlite3

from moa.models.retention import (
    DISCORD_SOURCE_RAW_EVIDENCE,
    IMPORT_RAW_MESSAGE,
    PROCESSING_ATTEMPT_FAILURE_DETAIL,
    RetentionCategoryReport,
    RetentionEligibilityReport,
)
from moa.repositories.data_health_repository import DataHealthRepository


RETENTION_DAYS = 90

NOT_SUCCESSFUL = "not-successful"
ACTIVE_OR_UNFINISHED = "active-or-unfinished"
INCOMPLETE_PROJECTION = "incomplete-projection"
MISSING_SUCCESS_ANCHOR = "missing-success-anchor"
AMBIGUOUS_SUCCESS_ANCHOR = "ambiguous-success-anchor"
NOT_OLD_ENOUGH = "not-old-enough"

_BLOCKED_REASONS = frozenset(
    {
        NOT_SUCCESSFUL,
        ACTIVE_OR_UNFINISHED,
        INCOMPLETE_PROJECTION,
        MISSING_SUCCESS_ANCHOR,
        AMBIGUOUS_SUCCESS_ANCHOR,
        NOT_OLD_ENOUGH,
    }
)


class RetentionEligibilityDataError(RuntimeError):
    """Raised when durable lifecycle data cannot be safely classified."""


@dataclass(frozen=True, slots=True)
class _SourceLifecycle:
    anchor: datetime | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class _CategorySelection:
    report: RetentionCategoryReport
    row_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _RetentionEligibilitySelection:
    report: RetentionEligibilityReport
    import_event_ids: tuple[int, ...]
    source_event_ids: tuple[int, ...]
    failure_attempt_ids: tuple[int, ...]


@dataclass(slots=True)
class _CategoryAccumulator:
    eligible_count: int = 0
    retained_blocked_count: int = 0
    already_expired_count: int = 0
    absent_count: int = 0
    eligible_anchors: list[datetime] | None = None
    blocked_reasons: Counter[str] | None = None

    def __post_init__(self) -> None:
        self.eligible_anchors = []
        self.blocked_reasons = Counter()

    def finish(self, category: str) -> RetentionCategoryReport:
        assert self.eligible_anchors is not None
        assert self.blocked_reasons is not None
        reasons = tuple(
            (reason, count)
            for reason, count in sorted(self.blocked_reasons.items())
            if reason in _BLOCKED_REASONS
        )
        return RetentionCategoryReport(
            category=category,
            eligible_count=self.eligible_count,
            retained_blocked_count=self.retained_blocked_count,
            already_expired_count=self.already_expired_count,
            absent_count=self.absent_count,
            oldest_eligible_anchor=min(self.eligible_anchors, default=None),
            newest_eligible_anchor=max(self.eligible_anchors, default=None),
            blocked_reason_counts=reasons,
        )


class RetentionEligibilityRepository:
    """Own the single authoritative, report-only eligibility definition."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def validate_schema(self) -> None:
        """Validate the current catalog without running migrations."""

        DataHealthRepository(self._connection).validate_schema()

    def build_report(self, as_of: datetime) -> RetentionEligibilityReport:
        """Classify all three evidence categories against one captured instant."""

        return self._select_for_expiry(as_of).report

    def _select_for_expiry(self, as_of: datetime) -> _RetentionEligibilitySelection:
        """Return transaction-local aggregates and IDs from the shared policy."""

        as_of = _normalize_datetime(as_of, "as_of")
        cutoff = as_of - timedelta(days=RETENTION_DAYS)
        import_selection = self._scan_import_messages(cutoff)
        source_selection = self._scan_source_evidence(cutoff)
        failure_selection = self._scan_failure_details(cutoff)
        report = RetentionEligibilityReport(
            as_of=as_of,
            cutoff=cutoff,
            categories=(
                import_selection.report,
                source_selection.report,
                failure_selection.report,
            ),
        )
        return _RetentionEligibilitySelection(
            report=report,
            import_event_ids=import_selection.row_ids,
            source_event_ids=source_selection.row_ids,
            failure_attempt_ids=failure_selection.row_ids,
        )

    def _scan_source_evidence(self, cutoff: datetime) -> _CategorySelection:
        result = _CategoryAccumulator()
        selected_ids: list[int] = []
        rows = self._connection.execute(
            """
            SELECT id, status, raw_text, raw_evidence_expired_at
            FROM discord_source_events
            ORDER BY id
            """
        )
        for row in rows:
            state = _evidence_state(row["raw_text"], row["raw_evidence_expired_at"])
            if state == "absent":
                result.absent_count += 1
                continue
            if state == "expired":
                result.already_expired_count += 1
                continue
            row_id = int(row["id"])
            lifecycle = self._source_lifecycle(row_id)
            if self._record_lifecycle(result, lifecycle, cutoff):
                selected_ids.append(row_id)
        return _CategorySelection(
            result.finish(DISCORD_SOURCE_RAW_EVIDENCE), tuple(selected_ids)
        )

    def _scan_failure_details(self, cutoff: datetime) -> _CategorySelection:
        result = _CategoryAccumulator()
        selected_ids: list[int] = []
        rows = self._connection.execute(
            """
            SELECT id, source_event_id, failure_detail, failure_detail_expired_at
            FROM discord_processing_attempts
            ORDER BY id
            """
        )
        for row in rows:
            state = _evidence_state(row["failure_detail"], row["failure_detail_expired_at"])
            if state == "absent":
                result.absent_count += 1
                continue
            if state == "expired":
                result.already_expired_count += 1
                continue
            lifecycle = self._source_lifecycle(int(row["source_event_id"]))
            if self._record_lifecycle(result, lifecycle, cutoff):
                selected_ids.append(int(row["id"]))
        return _CategorySelection(
            result.finish(PROCESSING_ATTEMPT_FAILURE_DETAIL), tuple(selected_ids)
        )

    def _scan_import_messages(self, cutoff: datetime) -> _CategorySelection:
        result = _CategoryAccumulator()
        selected_ids: list[int] = []
        rows = self._connection.execute(
            """
            SELECT id, raw_message, raw_message_expired_at, observed_at
            FROM import_events
            ORDER BY id
            """
        )
        for row in rows:
            state = _evidence_state(row["raw_message"], row["raw_message_expired_at"])
            if state == "absent":
                result.absent_count += 1
                continue
            if state == "expired":
                result.already_expired_count += 1
                continue

            references = tuple(
                self._connection.execute(
                    """
                    SELECT id
                    FROM discord_source_events
                    WHERE legacy_import_event_id = ?
                    ORDER BY id
                    """,
                    (int(row["id"]),),
                )
            )
            if len(references) > 1:
                lifecycle = _SourceLifecycle(None, AMBIGUOUS_SUCCESS_ANCHOR)
            elif references:
                lifecycle = self._source_lifecycle(int(references[0]["id"]))
            else:
                observed_at = _parse_timestamp(row["observed_at"])
                lifecycle = _SourceLifecycle(
                    observed_at,
                    None if observed_at is not None else MISSING_SUCCESS_ANCHOR,
                )
            if self._record_lifecycle(result, lifecycle, cutoff):
                selected_ids.append(int(row["id"]))
        return _CategorySelection(result.finish(IMPORT_RAW_MESSAGE), tuple(selected_ids))

    def _source_lifecycle(self, source_event_id: int) -> _SourceLifecycle:
        source = self._connection.execute(
            "SELECT status FROM discord_source_events WHERE id = ?", (source_event_id,)
        ).fetchone()
        if source is None:
            return _SourceLifecycle(None, MISSING_SUCCESS_ANCHOR)

        active_attempt = self._connection.execute(
            """
            SELECT 1
            FROM discord_processing_attempts
            WHERE source_event_id = ? AND status = 'processing'
            LIMIT 1
            """,
            (source_event_id,),
        ).fetchone()
        if active_attempt is not None:
            return _SourceLifecycle(None, ACTIVE_OR_UNFINISHED)
        if source["status"] != "succeeded":
            unfinished_attempt = self._connection.execute(
                """
                SELECT 1
                FROM discord_processing_attempts
                WHERE source_event_id = ? AND finished_at IS NULL
                LIMIT 1
                """,
                (source_event_id,),
            ).fetchone()
            if unfinished_attempt is not None:
                return _SourceLifecycle(None, ACTIVE_OR_UNFINISHED)
            return _SourceLifecycle(None, NOT_SUCCESSFUL)

        unfinished_attempt = self._connection.execute(
            """
            SELECT 1
            FROM discord_processing_attempts
            WHERE source_event_id = ? AND status != 'succeeded' AND finished_at IS NULL
            LIMIT 1
            """,
            (source_event_id,),
        ).fetchone()
        if unfinished_attempt is not None:
            return _SourceLifecycle(None, ACTIVE_OR_UNFINISHED)

        successful_attempts = tuple(
            self._connection.execute(
                """
                SELECT finished_at
                FROM discord_processing_attempts
                WHERE source_event_id = ? AND status = 'succeeded'
                ORDER BY id
                """,
                (source_event_id,),
            )
        )
        if len(successful_attempts) > 1:
            return _SourceLifecycle(None, AMBIGUOUS_SUCCESS_ANCHOR)
        if not successful_attempts:
            return _SourceLifecycle(None, MISSING_SUCCESS_ANCHOR)
        anchor = _parse_timestamp(successful_attempts[0]["finished_at"])
        if anchor is None:
            return _SourceLifecycle(None, MISSING_SUCCESS_ANCHOR)

        incomplete_link = self._connection.execute(
            """
            SELECT 1
            FROM discord_projection_links
            WHERE source_event_id = ?
              AND (
                  state != 'completed'
                  OR completed_at IS NULL
                  OR projection_table IS NULL
                  OR projection_row_id IS NULL
              )
            LIMIT 1
            """,
            (source_event_id,),
        ).fetchone()
        if incomplete_link is not None:
            return _SourceLifecycle(None, INCOMPLETE_PROJECTION)
        return _SourceLifecycle(anchor, None)

    @staticmethod
    def _record_lifecycle(
        result: _CategoryAccumulator,
        lifecycle: _SourceLifecycle,
        cutoff: datetime,
    ) -> bool:
        if lifecycle.reason is not None:
            result.retained_blocked_count += 1
            assert result.blocked_reasons is not None
            result.blocked_reasons[lifecycle.reason] += 1
            return False
        if lifecycle.anchor is None:
            raise RetentionEligibilityDataError("classified lifecycle has no anchor")
        if lifecycle.anchor <= cutoff:
            result.eligible_count += 1
            assert result.eligible_anchors is not None
            result.eligible_anchors.append(lifecycle.anchor)
            return True
        result.retained_blocked_count += 1
        assert result.blocked_reasons is not None
        result.blocked_reasons[NOT_OLD_ENOUGH] += 1
        return False


def _evidence_state(value: object, expired_at: object) -> str:
    if expired_at is not None:
        return "expired"
    if value is None:
        return "absent"
    return "retained"


def _parse_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _normalize_datetime(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)
