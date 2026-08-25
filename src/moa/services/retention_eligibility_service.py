"""Application service for report-only retention eligibility."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from moa.database.sqlite import DEFAULT_DATABASE_PATH, connect_read_only
from moa.models.retention import RetentionEligibilityReport
from moa.repositories.retention_eligibility_repository import RetentionEligibilityRepository


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RetentionEligibilityService:
    """Capture one clock instant and coordinate one read-only report."""

    def __init__(
        self,
        database_path: Path | None = None,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._database_path = database_path
        self._clock = clock

    def report(self, as_of: datetime | None = None) -> RetentionEligibilityReport:
        """Return a deterministic report without mutating the database."""

        captured_as_of = as_of if as_of is not None else self._clock()
        connection = connect_read_only(self._database_path or DEFAULT_DATABASE_PATH)
        try:
            connection.execute("BEGIN")
            repository = RetentionEligibilityRepository(connection)
            repository.validate_schema()
            return repository.build_report(captured_as_of)
        finally:
            try:
                if connection.in_transaction:
                    connection.rollback()
            finally:
                connection.close()
