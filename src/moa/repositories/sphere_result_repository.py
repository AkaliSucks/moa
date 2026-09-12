"""SQLite persistence for account-scoped Sphere Result observations."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from moa.database.sqlite import connect_read_only, run_write_transaction
from moa.models.catalog import SphereResultImportResult, SphereResultObservation
from moa.models.character import SphereResultSnapshot
from moa.repositories._catalog_identity import normalize, upsert_account, upsert_server


@dataclass(frozen=True, slots=True)
class _SphereResultImportConnectionResult:
    """Rows created by one Sphere Result import on a caller-owned connection."""

    import_event_id: int
    sphere_result_observation_id: int


class SphereResultRepository:
    """Persist account-within-server Sphere Result observations."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    @property
    def database_path(self) -> Path:
        """Return the explicit SQLite database path used by this repository."""
        return self._database_path

    def import_sphere_result(
        self,
        state: SphereResultSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> SphereResultImportResult:
        """Store one account-scoped `$oq` sphere payout."""
        observed_at = datetime.now(timezone.utc)
        imported = run_write_transaction(
            self._database_path,
            lambda connection: self._import_sphere_result_with_connection(
                connection,
                state=state,
                server=server_name,
                account=account_name,
                raw=raw_message,
                source=source,
                observed_at=observed_at,
            ),
        )
        return SphereResultImportResult(
            import_event_id=imported.import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observed_at=observed_at,
        )

    def _import_sphere_result_with_connection(
        self,
        connection: sqlite3.Connection,
        *,
        state: SphereResultSnapshot,
        server: str,
        account: str,
        raw: str,
        source: str,
        observed_at: datetime,
    ) -> _SphereResultImportConnectionResult:
        """Store one Sphere Result without taking transaction ownership."""
        cursor = connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
            ("sphere_result", source, observed_at.isoformat(), raw),
        )
        import_event_id = int(cursor.lastrowid)
        server_id = upsert_server(connection, server, observed_at)
        account_id = upsert_account(connection, server_id, account, observed_at)
        sphere_result_observation_id = int(
            connection.execute(
                """
                INSERT INTO sphere_result_observations (
                    account_context_id, snapshot_json, total_gained, stock,
                    observed_at, import_event_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    json.dumps(state.model_dump()),
                    state.total_gained,
                    state.stock,
                    observed_at.isoformat(),
                    import_event_id,
                ),
            ).lastrowid
        )
        return _SphereResultImportConnectionResult(
            import_event_id=import_event_id,
            sphere_result_observation_id=sphere_result_observation_id,
        )

    def sphere_result(self, server_name: str, account_name: str) -> SphereResultObservation | None:
        """Return the newest imported `$oq` result for one account."""
        with connect_read_only(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT sphere_result_observations.*, server_contexts.name AS server_name,
                       account_contexts.name AS account_name
                FROM account_contexts
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                JOIN sphere_result_observations ON sphere_result_observations.id = (
                    SELECT observations.id FROM sphere_result_observations AS observations
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
        return SphereResultObservation(
            server_name=row["server_name"],
            account_name=row["account_name"],
            snapshot=SphereResultSnapshot.model_validate(json.loads(row["snapshot_json"])),
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )
