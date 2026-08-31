"""Generation-qualified access to durable Discord projection links."""

from __future__ import annotations

from datetime import datetime
import sqlite3


class ProjectionLinkIntegrityError(RuntimeError):
    """Raised when projection-link state cannot be changed unambiguously."""


class ProjectionLinkRepository:
    """Operate on projection links inside a caller-owned connection scope."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def resolve_current_generation_id(self) -> int:
        """Return the sole positive current generation, or fail closed."""
        rows = self._connection.execute(
            "SELECT id FROM projection_generations WHERE is_current = 1"
        ).fetchall()
        if len(rows) != 1:
            raise ProjectionLinkIntegrityError(
                "projection links require exactly one current generation"
            )
        generation_id = int(rows[0][0])
        if generation_id <= 0:
            raise ProjectionLinkIntegrityError(
                "the current projection generation must have a positive id"
            )
        return generation_id

    def switch_current_generation(self) -> int:
        """Create and select a new generation inside the caller's transaction."""
        if not self._connection.in_transaction:
            raise ProjectionLinkIntegrityError(
                "projection generation switchover requires a caller-owned transaction"
            )

        savepoint = "projection_generation_switchover"
        self._connection.execute(f"SAVEPOINT {savepoint}")
        try:
            current_generation_id = self.resolve_current_generation_id()
            maximum_id = self._connection.execute(
                "SELECT MAX(id) FROM projection_generations"
            ).fetchone()[0]
            if maximum_id is None:
                raise ProjectionLinkIntegrityError(
                    "projection generation switchover requires an existing generation"
                )
            new_generation_id = int(maximum_id) + 1
            if new_generation_id <= 0:
                raise ProjectionLinkIntegrityError(
                    "the new projection generation must have a positive id"
                )

            inserted = self._connection.execute(
                "INSERT INTO projection_generations (id, is_current) VALUES (?, 0)",
                (new_generation_id,),
            )
            if inserted.rowcount != 1:
                raise ProjectionLinkIntegrityError(
                    "exactly one new projection generation must be inserted"
                )

            retired = self._connection.execute(
                "UPDATE projection_generations SET is_current = 0 "
                "WHERE id = ? AND is_current = 1",
                (current_generation_id,),
            )
            if retired.rowcount != 1:
                raise ProjectionLinkIntegrityError(
                    "exactly one current projection generation must be retired"
                )

            activated = self._connection.execute(
                "UPDATE projection_generations SET is_current = 1 "
                "WHERE id = ? AND is_current = 0",
                (new_generation_id,),
            )
            if activated.rowcount != 1:
                raise ProjectionLinkIntegrityError(
                    "exactly one new projection generation must become current"
                )
            if self.resolve_current_generation_id() != new_generation_id:
                raise ProjectionLinkIntegrityError(
                    "projection generation switchover did not select the new generation"
                )

            self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            return new_generation_id
        except Exception:
            self._connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise

    def load_links(self, *, source_event_id: int, generation_id: int) -> tuple[sqlite3.Row, ...]:
        """Load only links belonging to one source event and generation."""
        return tuple(
            self._connection.execute(
                """
                SELECT *
                FROM discord_projection_links
                WHERE source_event_id = ? AND generation_id = ?
                ORDER BY id
                """,
                (source_event_id, generation_id),
            ).fetchall()
        )

    def claim_link(
        self,
        *,
        source_event_id: int,
        generation_id: int,
        projection_kind: str,
        projection_slot: str,
        claimed_at: datetime,
    ) -> int:
        """Claim one explicitly generation-qualified projection identity."""
        value = claimed_at.isoformat()
        cursor = self._connection.execute(
            """
            INSERT INTO discord_projection_links (
                source_event_id, generation_id, projection_kind, projection_slot,
                projection_table, projection_row_id, state,
                claimed_at, completed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, NULL, NULL, 'claimed', ?, NULL, ?, ?)
            """,
            (
                source_event_id,
                generation_id,
                projection_kind,
                projection_slot,
                value,
                value,
                value,
            ),
        )
        return int(cursor.lastrowid)

    def complete_claimed_link(
        self,
        *,
        source_event_id: int,
        generation_id: int,
        projection_kind: str,
        projection_slot: str,
        projection_table: str,
        projection_row_id: int,
        completed_at: datetime,
    ) -> None:
        """Complete exactly one matching claimed link, or fail closed."""
        value = completed_at.isoformat()
        cursor = self._connection.execute(
            """
            UPDATE discord_projection_links
            SET projection_table = ?, projection_row_id = ?, state = 'completed',
                completed_at = ?, updated_at = ?
            WHERE source_event_id = ?
              AND generation_id = ?
              AND projection_kind = ?
              AND projection_slot = ?
              AND state = 'claimed'
            """,
            (
                projection_table,
                projection_row_id,
                value,
                value,
                source_event_id,
                generation_id,
                projection_kind,
                projection_slot,
            ),
        )
        if cursor.rowcount != 1:
            raise ProjectionLinkIntegrityError(
                "exactly one matching claimed projection link must be completed"
            )
