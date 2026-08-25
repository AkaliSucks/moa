"""Application service for explicit transactional retention expiry."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from moa.database.sqlite import DEFAULT_DATABASE_PATH, run_write_transaction
from moa.models.retention import RetentionExpiryResult
from moa.repositories.retention_expiry_repository import RetentionExpiryRepository


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RetentionExpiryService:
    """Capture one instant and apply retention inside one write transaction."""

    def __init__(
        self,
        database_path: Path | None = None,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._database_path = database_path
        self._clock = clock

    def apply(self, apply_as_of: datetime | None = None) -> RetentionExpiryResult:
        """Recompute and commit expiry, returning only the committed result."""

        captured_as_of = apply_as_of if apply_as_of is not None else self._clock()
        database_path = self._database_path or DEFAULT_DATABASE_PATH
        return run_write_transaction(
            database_path,
            lambda connection: RetentionExpiryRepository(connection).apply(captured_as_of),
        )
