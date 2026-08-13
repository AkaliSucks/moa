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
        kakeraloot_state_observation_id = int(
            connection.execute(
                """
                INSERT INTO kakeraloot_state_observations (
                    account_context_id, has_kakeraloots, status_note, rolls_stacked, disable_wa_ha_reduction,
                    disable_wg_hg_reduction, protected_wish_level, protected_wish_denominator,
                    mudapins, rt_cooldown_reduction_hours, permanent_roll_bonus,
                    star_branches, starwish_slots_from_branches, quantity_level, quality_level,
                    usage_count, kakera_balance, observed_at, import_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    int(state.has_kakeraloots),
                    state.status_note,
                    state.rolls_stacked or 0,
                    state.disable_wa_ha_reduction or 0,
                    state.disable_wg_hg_reduction or 0,
                    state.protected_wish_level or 0,
                    state.protected_wish_denominator or 0,
                    state.mudapins or 0,
                    state.rt_cooldown_reduction_hours or 0,
                    state.permanent_roll_bonus or 0,
                    state.star_branches or 0,
                    state.starwish_slots_from_branches or 0,
                    state.quantity_level or 0,
                    state.quality_level or 0,
                    state.usage_count or 0,
                    state.kakera_balance or 0,
                    observed_at.isoformat(),
                    import_event_id,
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
        return KakeralootStateObservation(
            server_name=row["server_name"],
            account_name=row["account_name"],
            has_kakeraloots=bool(row["has_kakeraloots"]),
            status_note=row["status_note"],
            rolls_stacked=row["rolls_stacked"] if row["has_kakeraloots"] else None,
            disable_wa_ha_reduction=row["disable_wa_ha_reduction"] if row["has_kakeraloots"] else None,
            disable_wg_hg_reduction=row["disable_wg_hg_reduction"] if row["has_kakeraloots"] else None,
            protected_wish_level=row["protected_wish_level"] if row["has_kakeraloots"] else None,
            protected_wish_denominator=row["protected_wish_denominator"] if row["has_kakeraloots"] else None,
            mudapins=row["mudapins"] if row["has_kakeraloots"] else None,
            rt_cooldown_reduction_hours=row["rt_cooldown_reduction_hours"] if row["has_kakeraloots"] else None,
            permanent_roll_bonus=row["permanent_roll_bonus"] if row["has_kakeraloots"] else None,
            star_branches=row["star_branches"] if row["has_kakeraloots"] else None,
            starwish_slots_from_branches=row["starwish_slots_from_branches"] if row["has_kakeraloots"] else None,
            quantity_level=row["quantity_level"] if row["has_kakeraloots"] else None,
            quality_level=row["quality_level"] if row["has_kakeraloots"] else None,
            usage_count=row["usage_count"] if row["has_kakeraloots"] else None,
            kakera_balance=row["kakera_balance"] if row["has_kakeraloots"] else None,
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )
