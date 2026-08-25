"""Transactional write-side application of raw evidence retention expiry."""

from __future__ import annotations

from datetime import datetime
import sqlite3

from moa.models.retention import (
    RetentionCategoryApplyResult,
    RetentionCategoryReport,
    RetentionExpiryResult,
)
from moa.repositories.retention_eligibility_repository import RetentionEligibilityRepository


RAW_EVIDENCE_EXPIRED_SENTINEL = "[moa:raw-evidence-expired:v1]"


class RetentionExpiryError(RuntimeError):
    """Raised with a bounded code when expiry cannot safely complete."""


class RetentionExpiryRepository:
    """Recompute, apply, and verify expiry on one caller-owned transaction."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def apply(self, apply_as_of: datetime) -> RetentionExpiryResult:
        """Apply all eligible categories atomically on the supplied transaction."""

        eligibility = RetentionEligibilityRepository(self._connection)
        eligibility.validate_schema()
        selection = eligibility._select_for_expiry(apply_as_of)
        timestamp = selection.report.as_of.isoformat()
        cutoff = selection.report.cutoff.isoformat()

        import_count = self._expire_import_messages(
            selection.import_event_ids, timestamp, cutoff
        )
        self._verify_count("import-rowcount-mismatch", import_count, selection.import_event_ids)

        source_count = self._expire_source_evidence(
            selection.source_event_ids, timestamp, cutoff
        )
        self._verify_count("source-rowcount-mismatch", source_count, selection.source_event_ids)

        failure_count = self._expire_failure_details(
            selection.failure_attempt_ids, timestamp, cutoff
        )
        self._verify_count(
            "failure-detail-rowcount-mismatch", failure_count, selection.failure_attempt_ids
        )

        counts = (import_count, source_count, failure_count)
        categories = tuple(
            self._category_result(category, expired_count)
            for category, expired_count in zip(
                selection.report.categories, counts, strict=True
            )
        )
        return RetentionExpiryResult(
            apply_as_of=selection.report.as_of,
            cutoff=selection.report.cutoff,
            categories=categories,
        )

    def _expire_import_messages(
        self, row_ids: tuple[int, ...], timestamp: str, cutoff: str
    ) -> int:
        if not row_ids:
            return 0
        cursor = self._connection.executemany(
            """
            UPDATE import_events
            SET raw_message = ?, raw_message_expired_at = ?
            WHERE id = ?
              AND raw_message_expired_at IS NULL
              AND raw_message IS NOT NULL
              AND (
                  (
                      NOT EXISTS (
                          SELECT 1 FROM discord_source_events
                          WHERE legacy_import_event_id = import_events.id
                      )
                      AND julianday(observed_at) <= julianday(?)
                  )
                  OR (
                      (SELECT COUNT(*) FROM discord_source_events
                       WHERE legacy_import_event_id = import_events.id) = 1
                      AND EXISTS (
                          SELECT 1
                          FROM discord_source_events AS source
                          WHERE source.legacy_import_event_id = import_events.id
                            AND source.status = 'succeeded'
                            AND NOT EXISTS (
                                SELECT 1 FROM discord_processing_attempts AS active
                                WHERE active.source_event_id = source.id
                                  AND active.status = 'processing'
                            )
                            AND NOT EXISTS (
                                SELECT 1 FROM discord_processing_attempts AS unfinished
                                WHERE unfinished.source_event_id = source.id
                                  AND unfinished.status != 'succeeded'
                                  AND unfinished.finished_at IS NULL
                            )
                            AND (SELECT COUNT(*) FROM discord_processing_attempts AS success
                                 WHERE success.source_event_id = source.id
                                   AND success.status = 'succeeded'
                                   AND success.finished_at IS NOT NULL) = 1
                            AND NOT EXISTS (
                                SELECT 1 FROM discord_processing_attempts AS invalid_success
                                WHERE invalid_success.source_event_id = source.id
                                  AND invalid_success.status = 'succeeded'
                                  AND invalid_success.finished_at IS NULL
                            )
                            AND NOT EXISTS (
                                SELECT 1 FROM discord_projection_links AS link
                                WHERE link.source_event_id = source.id
                                  AND (
                                      link.state != 'completed'
                                      OR link.completed_at IS NULL
                                      OR link.projection_table IS NULL
                                      OR link.projection_row_id IS NULL
                                  )
                            )
                            AND (SELECT julianday(success.finished_at)
                                 FROM discord_processing_attempts AS success
                                 WHERE success.source_event_id = source.id
                                   AND success.status = 'succeeded') <= julianday(?)
                      )
                  )
              )
            """,
            (
                (RAW_EVIDENCE_EXPIRED_SENTINEL, timestamp, row_id, cutoff, cutoff)
                for row_id in row_ids
            ),
        )
        return cursor.rowcount

    def _expire_source_evidence(
        self, row_ids: tuple[int, ...], timestamp: str, cutoff: str
    ) -> int:
        if not row_ids:
            return 0
        cursor = self._connection.executemany(
            """
            UPDATE discord_source_events
            SET raw_text = ?, payload_json = NULL, raw_evidence_expired_at = ?
            WHERE id = ?
              AND raw_evidence_expired_at IS NULL
              AND raw_text IS NOT NULL
              AND status = 'succeeded'
              AND NOT EXISTS (
                  SELECT 1 FROM discord_processing_attempts AS active
                  WHERE active.source_event_id = discord_source_events.id
                    AND active.status = 'processing'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM discord_processing_attempts AS unfinished
                  WHERE unfinished.source_event_id = discord_source_events.id
                    AND unfinished.status != 'succeeded'
                    AND unfinished.finished_at IS NULL
              )
              AND (SELECT COUNT(*) FROM discord_processing_attempts AS success
                   WHERE success.source_event_id = discord_source_events.id
                     AND success.status = 'succeeded'
                     AND success.finished_at IS NOT NULL) = 1
              AND NOT EXISTS (
                  SELECT 1 FROM discord_processing_attempts AS invalid_success
                  WHERE invalid_success.source_event_id = discord_source_events.id
                    AND invalid_success.status = 'succeeded'
                    AND invalid_success.finished_at IS NULL
              )
              AND NOT EXISTS (
                  SELECT 1 FROM discord_projection_links AS link
                  WHERE link.source_event_id = discord_source_events.id
                    AND (
                        link.state != 'completed'
                        OR link.completed_at IS NULL
                        OR link.projection_table IS NULL
                        OR link.projection_row_id IS NULL
                    )
              )
              AND (SELECT julianday(success.finished_at)
                   FROM discord_processing_attempts AS success
                   WHERE success.source_event_id = discord_source_events.id
                     AND success.status = 'succeeded') <= julianday(?)
            """,
            (
                (RAW_EVIDENCE_EXPIRED_SENTINEL, timestamp, row_id, cutoff)
                for row_id in row_ids
            ),
        )
        return cursor.rowcount

    def _expire_failure_details(
        self, row_ids: tuple[int, ...], timestamp: str, cutoff: str
    ) -> int:
        if not row_ids:
            return 0
        cursor = self._connection.executemany(
            """
            UPDATE discord_processing_attempts
            SET failure_detail = NULL, failure_detail_expired_at = ?
            WHERE id = ?
              AND failure_detail_expired_at IS NULL
              AND failure_detail IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM discord_source_events AS source
                  WHERE source.id = discord_processing_attempts.source_event_id
                    AND source.status = 'succeeded'
                    AND NOT EXISTS (
                        SELECT 1 FROM discord_processing_attempts AS active
                        WHERE active.source_event_id = source.id
                          AND active.status = 'processing'
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM discord_processing_attempts AS unfinished
                        WHERE unfinished.source_event_id = source.id
                          AND unfinished.status != 'succeeded'
                          AND unfinished.finished_at IS NULL
                    )
                    AND (SELECT COUNT(*) FROM discord_processing_attempts AS success
                         WHERE success.source_event_id = source.id
                           AND success.status = 'succeeded'
                           AND success.finished_at IS NOT NULL) = 1
                    AND NOT EXISTS (
                        SELECT 1 FROM discord_processing_attempts AS invalid_success
                        WHERE invalid_success.source_event_id = source.id
                          AND invalid_success.status = 'succeeded'
                          AND invalid_success.finished_at IS NULL
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM discord_projection_links AS link
                        WHERE link.source_event_id = source.id
                          AND (
                              link.state != 'completed'
                              OR link.completed_at IS NULL
                              OR link.projection_table IS NULL
                              OR link.projection_row_id IS NULL
                          )
                    )
                    AND (SELECT julianday(success.finished_at)
                         FROM discord_processing_attempts AS success
                         WHERE success.source_event_id = source.id
                           AND success.status = 'succeeded') <= julianday(?)
              )
            """,
            ((timestamp, row_id, cutoff) for row_id in row_ids),
        )
        return cursor.rowcount

    @staticmethod
    def _verify_count(code: str, affected: int, selected_ids: tuple[int, ...]) -> None:
        if affected != len(selected_ids):
            raise RetentionExpiryError(code)

    @staticmethod
    def _category_result(
        report: RetentionCategoryReport, expired_count: int
    ) -> RetentionCategoryApplyResult:
        return RetentionCategoryApplyResult(
            category=report.category,
            recomputed_eligible_count=report.eligible_count,
            expired_count=expired_count,
            retained_blocked_count=report.retained_blocked_count,
            already_expired_count=report.already_expired_count,
            absent_count=report.absent_count,
            blocked_reason_counts=report.blocked_reason_counts,
        )
