"""SQLite persistence for account-scoped Timer State observations."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from moa.database.sqlite import connect, run_write_transaction
from moa.models.catalog import TimerStateImportResult, TimerStateObservation
from moa.models.character import TimerStateSnapshot
from moa.repositories._catalog_identity import normalize, upsert_account, upsert_server


@dataclass(frozen=True, slots=True)
class _TimerStateImportConnectionResult:
    """Rows created by one Timer State import on a caller-owned connection."""

    import_event_id: int
    timer_state_observation_id: int


class TimerStateRepository:
    """Persist account-scoped Timer State observations in an initialized database."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    @property
    def database_path(self) -> Path:
        """Return the explicit SQLite database path used by this repository."""
        return self._database_path

    def import_timer_state(
        self,
        state: TimerStateSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> TimerStateImportResult:
        """Store one short-lived account action snapshot from `$tu`."""
        observed_at = datetime.now(timezone.utc)
        imported = run_write_transaction(
            self._database_path,
            lambda connection: self._import_timer_state_with_connection(
                connection,
                state=state,
                server=server_name,
                account=account_name,
                raw=raw_message,
                source=source,
                observed_at=observed_at,
            ),
        )
        return TimerStateImportResult(
            import_event_id=imported.import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observed_at=observed_at,
        )

    def _import_timer_state_with_connection(
        self,
        connection: sqlite3.Connection,
        *,
        state: TimerStateSnapshot,
        server: str,
        account: str,
        raw: str,
        source: str,
        observed_at: datetime,
    ) -> _TimerStateImportConnectionResult:
        """Store one Timer State snapshot without taking transaction ownership."""
        cursor = connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
            ("timer_state", source, observed_at.isoformat(), raw),
        )
        import_event_id = int(cursor.lastrowid)
        server_id = upsert_server(connection, server, observed_at)
        account_id = upsert_account(connection, server_id, account, observed_at)
        timer_state_observation_id = int(
            connection.execute(
                """
                INSERT INTO timer_state_observations (
                    account_context_id, snapshot_json, observed_at, import_event_id
                ) VALUES (?, ?, ?, ?)
                """,
                (account_id, json.dumps(state.model_dump()), observed_at.isoformat(), import_event_id),
            ).lastrowid
        )
        return _TimerStateImportConnectionResult(
            import_event_id=import_event_id,
            timer_state_observation_id=timer_state_observation_id,
        )

    def timer_state(self, server_name: str, account_name: str) -> TimerStateObservation | None:
        """Return the newest imported `$tu` snapshot for one account."""
        with connect(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT timer_state_observations.*, server_contexts.name AS server_name,
                       account_contexts.name AS account_name
                FROM account_contexts
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                JOIN timer_state_observations ON timer_state_observations.id = (
                    SELECT observations.id FROM timer_state_observations AS observations
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
        return TimerStateObservation(
            server_name=row["server_name"],
            account_name=row["account_name"],
            snapshot=TimerStateSnapshot.model_validate(json.loads(row["snapshot_json"])),
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )
