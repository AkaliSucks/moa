"""SQLite persistence for account-scoped Kakera state observations."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from moa.database.sqlite import connect, run_write_transaction
from moa.models.catalog import (
    KakeraProgressPoint,
    KakeraStateImportResult,
    KakeraStateObservation,
)
from moa.models.character import KakeraStateSnapshot
from moa.repositories._catalog_identity import normalize, upsert_account, upsert_server


@dataclass(frozen=True, slots=True)
class _KakeraStateImportConnectionResult:
    """Rows created by one Kakera-state import on a caller-owned connection."""

    import_event_id: int
    kakera_state_observation_id: int


class KakeraStateRepository:
    """Persist account-scoped Kakera state in an initialized database."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    @property
    def database_path(self) -> Path:
        """Return the explicit SQLite database path used by this repository."""
        return self._database_path

    def import_kakera_state(
        self,
        state: KakeraStateSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> KakeraStateImportResult:
        """Store a complete account-scoped `$k` snapshot."""
        observed_at = datetime.now(timezone.utc)
        imported = run_write_transaction(
            self._database_path,
            lambda connection: self._import_kakera_state_with_connection(
                connection,
                state=state,
                server=server_name,
                account=account_name,
                raw=raw_message,
                source=source,
                observed_at=observed_at,
            ),
        )
        return KakeraStateImportResult(
            import_event_id=imported.import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observed_at=observed_at,
        )

    def _import_kakera_state_with_connection(
        self,
        connection: sqlite3.Connection,
        *,
        state: KakeraStateSnapshot,
        server: str,
        account: str,
        raw: str,
        source: str,
        observed_at: datetime,
    ) -> _KakeraStateImportConnectionResult:
        """Store one Kakera-state snapshot without taking transaction ownership."""
        cursor = connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
            ("kakera_state", source, observed_at.isoformat(), raw),
        )
        import_event_id = int(cursor.lastrowid)
        server_id = upsert_server(connection, server, observed_at)
        account_id = upsert_account(connection, server_id, account, observed_at)
        kakera_state_observation_id = int(
            connection.execute(
                """
                INSERT INTO kakera_state_observations (
                    account_context_id, kakera_balance, badges_json, observed_at, import_event_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    state.kakera_balance,
                    json.dumps([badge.model_dump() for badge in state.badges]),
                    observed_at.isoformat(),
                    import_event_id,
                ),
            ).lastrowid
        )
        return _KakeraStateImportConnectionResult(
            import_event_id=import_event_id,
            kakera_state_observation_id=kakera_state_observation_id,
        )

    def kakera_state(self, server_name: str, account_name: str) -> KakeraStateObservation | None:
        """Return the latest `$k` snapshot for one server/account pair."""
        with connect(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT kakera_state_observations.*, server_contexts.name AS server_name,
                       account_contexts.name AS account_name
                FROM account_contexts
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                JOIN kakera_state_observations ON kakera_state_observations.id = (
                    SELECT observations.id FROM kakera_state_observations AS observations
                    WHERE observations.account_context_id = account_contexts.id
                    ORDER BY observations.id DESC LIMIT 1
                )
                WHERE server_contexts.normalized_name = ?
                  AND account_contexts.normalized_name = ?
                """,
                (normalize(server_name), normalize(account_name)),
            ).fetchone()
        if row is None:
            return None
        return KakeraStateObservation(
            server_name=row["server_name"],
            account_name=row["account_name"],
            kakera_balance=row["kakera_balance"],
            badges=tuple(json.loads(row["badges_json"])),
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )

    def kakera_history(
        self, server_name: str, account_name: str
    ) -> tuple[KakeraProgressPoint, ...]:
        """Return every imported `$k` snapshot in chronological order."""
        with connect(self._database_path) as connection:
            rows = connection.execute(
                """
                SELECT kakera_state_observations.*, server_contexts.name AS server_name,
                       account_contexts.name AS account_name
                FROM kakera_state_observations
                JOIN account_contexts ON account_contexts.id = kakera_state_observations.account_context_id
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                WHERE server_contexts.normalized_name = ?
                  AND account_contexts.normalized_name = ?
                ORDER BY kakera_state_observations.observed_at ASC, kakera_state_observations.id ASC
                """,
                (normalize(server_name), normalize(account_name)),
            ).fetchall()
        return tuple(
            KakeraProgressPoint(
                kakera_balance=row["kakera_balance"],
                max_badge_count=sum(badge["max_reached"] for badge in json.loads(row["badges_json"])),
                observed_at=datetime.fromisoformat(row["observed_at"]),
            )
            for row in rows
        )
