"""SQLite persistence for account-scoped Disablelist observations."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from moa.database.sqlite import connect_read_only, run_write_transaction
from moa.models.catalog import DisableListImportResult, DisableListObservation
from moa.models.character import DisableListSnapshot
from moa.repositories._catalog_identity import normalize, upsert_account, upsert_server


@dataclass(frozen=True, slots=True)
class _DisableListImportConnectionResult:
    """Rows created by one Disablelist import on a caller-owned connection."""

    import_event_id: int
    disablelist_observation_id: int


class DisableListRepository:
    """Persist account-scoped Disablelist observations in an initialized database."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    @property
    def database_path(self) -> Path:
        """Return the explicit SQLite database path used by this repository."""
        return self._database_path

    def import_disablelist(
        self,
        disablelist: DisableListSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> DisableListImportResult:
        """Store a complete account-scoped `$dl` snapshot."""
        observed_at = datetime.now(timezone.utc)
        imported = run_write_transaction(
            self._database_path,
            lambda connection: self._import_disablelist_with_connection(
                connection,
                state=disablelist,
                server=server_name,
                account=account_name,
                raw=raw_message,
                source=source,
                observed_at=observed_at,
            ),
        )
        return DisableListImportResult(
            import_event_id=imported.import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observed_at=observed_at,
        )

    def _import_disablelist_with_connection(
        self,
        connection: sqlite3.Connection,
        *,
        state: DisableListSnapshot,
        server: str,
        account: str,
        raw: str,
        source: str,
        observed_at: datetime,
    ) -> _DisableListImportConnectionResult:
        """Store one Disablelist snapshot without taking transaction ownership."""
        cursor = connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
            ("disablelist", source, observed_at.isoformat(), raw),
        )
        import_event_id = int(cursor.lastrowid)
        server_id = upsert_server(connection, server, observed_at)
        account_id = upsert_account(connection, server_id, account, observed_at)
        disablelist_observation_id = int(
            connection.execute(
                """
                INSERT INTO disablelist_observations (
                    account_context_id, slots_used, slots_capacity, total_disabled, disabled_wa,
                    disabled_ha, disabled_wg, disabled_hg, wa_pool_limit, ha_pool_limit,
                    western_disabled, irl_disabled, western_disabled_observed,
                    irl_disabled_observed, entries_json, observed_at, import_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    state.slots_used,
                    state.slots_capacity,
                    state.total_disabled,
                    state.disabled_wa,
                    state.disabled_ha,
                    state.disabled_wg,
                    state.disabled_hg,
                    state.wa_pool_limit,
                    state.ha_pool_limit,
                    int(state.western_disabled) if state.western_disabled is not None else 0,
                    int(state.irl_disabled) if state.irl_disabled is not None else 0,
                    int(state.western_disabled is not None),
                    int(state.irl_disabled is not None),
                    json.dumps([entry.model_dump() for entry in state.entries]),
                    observed_at.isoformat(),
                    import_event_id,
                ),
            ).lastrowid
        )
        return _DisableListImportConnectionResult(
            import_event_id=import_event_id,
            disablelist_observation_id=disablelist_observation_id,
        )

    def disablelist(self, server_name: str, account_name: str) -> DisableListObservation | None:
        """Return the latest `$dl` snapshot for one server/account pair."""
        with connect_read_only(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT disablelist_observations.*, server_contexts.name AS server_name,
                       account_contexts.name AS account_name
                FROM account_contexts
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                JOIN disablelist_observations ON disablelist_observations.id = (
                    SELECT observations.id FROM disablelist_observations AS observations
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
        return DisableListObservation(
            server_name=row["server_name"],
            account_name=row["account_name"],
            slots_used=row["slots_used"],
            slots_capacity=row["slots_capacity"],
            total_disabled=row["total_disabled"],
            disabled_wa=row["disabled_wa"],
            disabled_ha=row["disabled_ha"],
            disabled_wg=row["disabled_wg"],
            disabled_hg=row["disabled_hg"],
            wa_pool_limit=row["wa_pool_limit"],
            ha_pool_limit=row["ha_pool_limit"],
            western_disabled=self._toggle_from_row(row, "western_disabled"),
            irl_disabled=self._toggle_from_row(row, "irl_disabled"),
            entries=tuple(json.loads(row["entries_json"])),
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )

    @staticmethod
    def _toggle_from_row(row: sqlite3.Row, field_name: str) -> bool | None:
        value = row[field_name]
        observed = row[f"{field_name}_observed"]
        if value not in (0, 1):
            raise sqlite3.IntegrityError(
                f"disablelist_observations has invalid {field_name} value"
            )
        if observed == 1:
            return bool(value)
        if value == 0 and observed in (0, None):
            return None
        if observed not in (0, 1, None):
            raise sqlite3.IntegrityError(
                f"disablelist_observations has invalid {field_name} presence"
            )
        raise sqlite3.IntegrityError(
            f"disablelist_observations has inconsistent {field_name} presence"
        )
