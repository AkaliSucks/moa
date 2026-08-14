"""SQLite persistence for account-scoped Mudapin observations."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from moa.database.sqlite import connect, run_write_transaction
from moa.models.catalog import MudapinImportResult, MudapinObservation
from moa.models.character import MudapinSnapshot
from moa.repositories._catalog_identity import normalize, upsert_account, upsert_server


@dataclass(frozen=True, slots=True)
class _MudapinImportConnectionResult:
    """Rows created by one Mudapin import on a caller-owned connection."""

    import_event_id: int
    mudapin_observation_id: int


class MudapinsRepository:
    """Persist account-scoped Mudapin observations in an initialized database."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    @property
    def database_path(self) -> Path:
        """Return the explicit SQLite database path used by this repository."""
        return self._database_path

    def import_mudapins(
        self,
        snapshot: MudapinSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> MudapinImportResult:
        """Store one account-scoped `$mp` Mudapin inventory."""
        observed_at = datetime.now(timezone.utc)
        imported = run_write_transaction(
            self._database_path,
            lambda connection: self._import_mudapins_with_connection(
                connection,
                snapshot=snapshot,
                server=server_name,
                account=account_name,
                raw=raw_message,
                source=source,
                observed_at=observed_at,
            ),
        )
        return MudapinImportResult(
            import_event_id=imported.import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observed_at=observed_at,
        )

    def _import_mudapins_with_connection(
        self,
        connection: sqlite3.Connection,
        *,
        snapshot: MudapinSnapshot,
        server: str,
        account: str,
        raw: str,
        source: str,
        observed_at: datetime,
    ) -> _MudapinImportConnectionResult:
        """Store one Mudapin inventory without taking transaction ownership."""
        cursor = connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
            ("mudapins", source, observed_at.isoformat(), raw),
        )
        import_event_id = int(cursor.lastrowid)
        server_id = upsert_server(connection, server, observed_at)
        account_id = upsert_account(connection, server_id, account, observed_at)
        mudapin_observation_id = int(
            connection.execute(
                """
                INSERT INTO mudapin_observations (
                    account_context_id, pin_markers_json, pin_count, observed_at, import_event_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    json.dumps(list(snapshot.pin_markers)),
                    len(snapshot.pin_markers),
                    observed_at.isoformat(),
                    import_event_id,
                ),
            ).lastrowid
        )
        return _MudapinImportConnectionResult(
            import_event_id=import_event_id,
            mudapin_observation_id=mudapin_observation_id,
        )

    def mudapins(self, server_name: str, account_name: str) -> MudapinObservation | None:
        """Return the latest `$mp` inventory for one account."""
        with connect(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT mudapin_observations.*, server_contexts.name AS server_name,
                       account_contexts.name AS account_name
                FROM account_contexts
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                JOIN mudapin_observations ON mudapin_observations.id = (
                    SELECT observations.id FROM mudapin_observations AS observations
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
        return MudapinObservation(
            server_name=row["server_name"],
            account_name=row["account_name"],
            snapshot=MudapinSnapshot(pin_markers=tuple(json.loads(row["pin_markers_json"]))),
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )
