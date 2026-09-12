"""SQLite persistence for account-scoped Tower-state observations."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from moa.database.sqlite import connect_read_only, run_write_transaction
from moa.models.catalog import TowerStateImportResult, TowerStateObservation
from moa.models.character import TowerStateSnapshot
from moa.repositories._catalog_identity import normalize, upsert_account, upsert_server


@dataclass(frozen=True, slots=True)
class _TowerStateImportConnectionResult:
    """Rows created by one Tower-state import on a caller-owned connection."""

    import_event_id: int
    tower_state_observation_id: int


class TowerStateRepository:
    """Persist account-scoped Tower-state observations in an initialized database."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    @property
    def database_path(self) -> Path:
        """Return the explicit SQLite database path used by this repository."""
        return self._database_path

    def import_tower_state(
        self,
        state: TowerStateSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> TowerStateImportResult:
        """Store a complete account-scoped `$kt` snapshot."""
        observed_at = datetime.now(timezone.utc)
        imported = run_write_transaction(
            self._database_path,
            lambda connection: self._import_tower_state_with_connection(
                connection,
                state=state,
                server=server_name,
                account=account_name,
                raw=raw_message,
                source=source,
                observed_at=observed_at,
            ),
        )
        return TowerStateImportResult(
            import_event_id=imported.import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observed_at=observed_at,
        )

    def _import_tower_state_with_connection(
        self,
        connection: sqlite3.Connection,
        *,
        state: TowerStateSnapshot,
        server: str,
        account: str,
        raw: str,
        source: str,
        observed_at: datetime,
    ) -> _TowerStateImportConnectionResult:
        """Store one Tower-state snapshot without taking transaction ownership."""
        cursor = connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
            ("tower_state", source, observed_at.isoformat(), raw),
        )
        import_event_id = int(cursor.lastrowid)
        server_id = upsert_server(connection, server, observed_at)
        account_id = upsert_account(connection, server_id, account, observed_at)
        completed_towers = state.completed_towers
        tower_state_observation_id = int(
            connection.execute(
                """
                INSERT INTO tower_state_observations (
                    account_context_id, current_level, completed_towers, next_level_cost,
                    kakera_balance, built_perk_ids_json, observed_at, import_event_id,
                    completed_towers_observed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    state.current_level,
                    0 if completed_towers is None else completed_towers,
                    state.next_level_cost,
                    state.kakera_balance,
                    json.dumps(state.built_perk_ids),
                    observed_at.isoformat(),
                    import_event_id,
                    0 if completed_towers is None else 1,
                ),
            ).lastrowid
        )
        return _TowerStateImportConnectionResult(
            import_event_id=import_event_id,
            tower_state_observation_id=tower_state_observation_id,
        )

    def tower_state(self, server_name: str, account_name: str) -> TowerStateObservation | None:
        """Return the latest `$kt` snapshot for one server/account pair."""
        with connect_read_only(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT tower_state_observations.*, server_contexts.name AS server_name,
                       account_contexts.name AS account_name
                FROM account_contexts
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                JOIN tower_state_observations ON tower_state_observations.id = (
                    SELECT observations.id FROM tower_state_observations AS observations
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
        completed_towers = self._completed_towers_from_row(row)
        return TowerStateObservation(
            server_name=row["server_name"],
            account_name=row["account_name"],
            current_level=row["current_level"],
            completed_towers=completed_towers,
            next_level_cost=row["next_level_cost"],
            kakera_balance=row["kakera_balance"],
            built_perk_ids=tuple(json.loads(row["built_perk_ids_json"])),
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )

    @staticmethod
    def _completed_towers_from_row(row: sqlite3.Row) -> int | None:
        completed_towers = int(row["completed_towers"])
        observed = row["completed_towers_observed"]
        if observed == 1:
            return completed_towers
        if observed in (0, None) and completed_towers == 0:
            return None
        raise sqlite3.IntegrityError(
            "tower_state_observations has inconsistent completed_towers presence"
        )
