"""Explicit, parser-backed reconstruction of retained roll key-display presence."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import sqlite3

from moa.database.sqlite import run_write_transaction
from moa.services.roll_key_display_candidate_evaluator import (
    RollKeyDisplayCandidateEvaluator,
    RollKeyDisplayCandidateStatus,
)


class RollKeyDisplayBackfillStatus(str, Enum):
    UPDATED = "updated"
    ALREADY_ESTABLISHED = "already_established"
    UNAVAILABLE = "unavailable"
    INCOHERENT = "incoherent"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class RollKeyDisplayBackfillResult:
    status: RollKeyDisplayBackfillStatus
    source_event_id: int
    import_event_id: int | None = None
    roll_observation_id: int | None = None
    displayed_key_count_present: bool | None = None
    parser_version: str | None = None


class RollKeyDisplayPresenceBackfillService:
    """Backfill one source only after re-evaluation inside the write transaction."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def backfill(self, source_event_id: int) -> RollKeyDisplayBackfillResult:
        """Explicitly inspect and atomically update one roll in a write transaction."""
        if isinstance(source_event_id, bool) or not isinstance(source_event_id, int) or source_event_id <= 0:
            raise ValueError("source_event_id must be a positive integer")
        return run_write_transaction(
            self._database_path,
            lambda connection: self._backfill(connection, source_event_id),
        )

    @staticmethod
    def _backfill(
        connection: sqlite3.Connection, source_event_id: int
    ) -> RollKeyDisplayBackfillResult:
        candidate = RollKeyDisplayCandidateEvaluator.evaluate(
            connection, source_event_id=source_event_id
        )
        status = candidate.status
        presence: bool | None = None
        if status is RollKeyDisplayCandidateStatus.ELIGIBLE_NULL:
            presence = candidate.proposed_value
            assert presence is not None
            assert candidate.roll_observation_id is not None
            assert candidate.import_event_id is not None
            updated = connection.execute(
                "UPDATE roll_observations SET displayed_key_count_present = ? "
                "WHERE id = ? AND import_event_id = ? AND displayed_key_count_present IS NULL",
                (int(presence), candidate.roll_observation_id, candidate.import_event_id),
            ).rowcount
            outcome = (
                RollKeyDisplayBackfillStatus.UPDATED
                if updated == 1
                else RollKeyDisplayBackfillStatus.CONFLICT
            )
        elif status is RollKeyDisplayCandidateStatus.ALREADY_ESTABLISHED_MATCHING:
            presence = candidate.current_displayed_key_count_present
            outcome = RollKeyDisplayBackfillStatus.ALREADY_ESTABLISHED
        elif status is RollKeyDisplayCandidateStatus.ALREADY_ESTABLISHED_CONFLICT:
            assert candidate.current_displayed_key_count_present is not None
            presence = not candidate.current_displayed_key_count_present
            outcome = RollKeyDisplayBackfillStatus.CONFLICT
        elif status in (
            RollKeyDisplayCandidateStatus.SOURCE_UNUSABLE,
            RollKeyDisplayCandidateStatus.SOURCE_EXPIRED,
            RollKeyDisplayCandidateStatus.PARSE_FAILURE,
        ):
            outcome = RollKeyDisplayBackfillStatus.UNAVAILABLE
        else:
            outcome = RollKeyDisplayBackfillStatus.INCOHERENT
        return RollKeyDisplayBackfillResult(
            outcome,
            source_event_id,
            candidate.import_event_id,
            candidate.roll_observation_id,
            presence,
            candidate.parser_version,
        )
