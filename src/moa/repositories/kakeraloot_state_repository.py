"""SQLite persistence for account-scoped Kakeraloot-state observations."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from moa.database.sqlite import connect, run_write_transaction
from moa.models.catalog import KakeralootStateImportResult, KakeralootStateObservation
from moa.models.character import KakeralootStateSnapshot
from moa.repositories._catalog_identity import normalize, upsert_account, upsert_server


_KAKERALOOT_STATE_VALUE_FIELDS = (
    "rolls_stacked",
    "disable_wa_ha_reduction",
    "disable_wg_hg_reduction",
    "protected_wish_level",
    "protected_wish_denominator",
    "mudapins",
    "rt_cooldown_reduction_hours",
    "permanent_roll_bonus",
    "star_branches",
    "starwish_slots_from_branches",
    "quantity_level",
    "quality_level",
    "usage_count",
    "kakera_balance",
)


@dataclass(frozen=True, slots=True)
class _KakeralootStateImportConnectionResult:
    """Rows created by one Kakeraloot-state import on a caller-owned connection."""

    import_event_id: int
    kakeraloot_state_observation_id: int


class KakeralootStateRepository:
    """Persist account-scoped Kakeraloot-state observations in an initialized database."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    @property
    def database_path(self) -> Path:
        """Return the explicit SQLite database path used by this repository."""
        return self._database_path

    def import_kakeraloot_state(
        self,
        state: KakeralootStateSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> KakeralootStateImportResult:
        """Store a complete account-scoped `$lk` snapshot."""
        observed_at = datetime.now(timezone.utc)
        imported = run_write_transaction(
            self._database_path,
            lambda connection: self._import_kakeraloot_state_with_connection(
                connection,
                state=state,
                server=server_name,
                account=account_name,
                raw=raw_message,
                source=source,
                observed_at=observed_at,
            ),
        )
        return KakeralootStateImportResult(
            import_event_id=imported.import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observed_at=observed_at,
        )

    def _import_kakeraloot_state_with_connection(
        self,
        connection: sqlite3.Connection,
        *,
        state: KakeralootStateSnapshot,
        server: str,
        account: str,
        raw: str,
        source: str,
        observed_at: datetime,
    ) -> _KakeralootStateImportConnectionResult:
        """Store one Kakeraloot-state snapshot without taking transaction ownership."""
        cursor = connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
            ("kakeraloot_state", source, observed_at.isoformat(), raw),
        )
        import_event_id = int(cursor.lastrowid)
        server_id = upsert_server(connection, server, observed_at)
        account_id = upsert_account(connection, server_id, account, observed_at)
        supplied_values = tuple(
            getattr(state, field_name) for field_name in _KAKERALOOT_STATE_VALUE_FIELDS
        )
        stored_values = tuple(0 if value is None else value for value in supplied_values)
        observed_values = tuple(0 if value is None else 1 for value in supplied_values)
        columns = (
            "account_context_id",
            "has_kakeraloots",
            "status_note",
            *_KAKERALOOT_STATE_VALUE_FIELDS,
            "observed_at",
            "import_event_id",
            *(f"{field_name}_observed" for field_name in _KAKERALOOT_STATE_VALUE_FIELDS),
        )
        placeholders = ", ".join("?" for _ in columns)
        kakeraloot_state_observation_id = int(
            connection.execute(
                f"INSERT INTO kakeraloot_state_observations "
                f"({', '.join(columns)}) VALUES ({placeholders})",
                (
                    account_id,
                    int(state.has_kakeraloots),
                    state.status_note,
                    *stored_values,
                    observed_at.isoformat(),
                    import_event_id,
                    *observed_values,
                ),
            ).lastrowid
        )
        return _KakeralootStateImportConnectionResult(
            import_event_id=import_event_id,
            kakeraloot_state_observation_id=kakeraloot_state_observation_id,
        )

    def kakeraloot_state(
        self, server_name: str, account_name: str
    ) -> KakeralootStateObservation | None:
        """Return the latest `$lk` snapshot for one server/account pair."""
        with connect(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT kakeraloot_state_observations.*, server_contexts.name AS server_name,
                       account_contexts.name AS account_name
                FROM account_contexts
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                JOIN kakeraloot_state_observations ON kakeraloot_state_observations.id = (
                    SELECT observations.id FROM kakeraloot_state_observations AS observations
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
        has_kakeraloots = bool(row["has_kakeraloots"])
        values = {
            field_name: self._value_from_row(row, field_name)
            for field_name in _KAKERALOOT_STATE_VALUE_FIELDS
        }
        if not has_kakeraloots:
            values = {field_name: None for field_name in _KAKERALOOT_STATE_VALUE_FIELDS}
        return KakeralootStateObservation(
            server_name=row["server_name"],
            account_name=row["account_name"],
            has_kakeraloots=has_kakeraloots,
            status_note=row["status_note"],
            **values,
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )

    @staticmethod
    def _value_from_row(row: sqlite3.Row, field_name: str) -> int | None:
        value = int(row[field_name])
        observed = row[f"{field_name}_observed"]
        if observed == 1:
            return value
        if observed in (0, None) and value == 0:
            return None
        raise sqlite3.IntegrityError(
            f"kakeraloot_state_observations has inconsistent {field_name} presence"
        )
