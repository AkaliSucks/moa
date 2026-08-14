"""SQLite persistence for server-scoped Kakeraloot Settings observations."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from moa.database.sqlite import connect, run_write_transaction
from moa.models.catalog import KakeralootSettingsImportResult, KakeralootSettingsObservation
from moa.models.character import KakeralootSettingsSnapshot
from moa.repositories._catalog_identity import normalize, upsert_server


@dataclass(frozen=True, slots=True)
class _KakeralootSettingsImportConnectionResult:
    """Rows created by one Kakeraloot Settings import on a caller-owned connection."""

    import_event_id: int
    kakeraloot_settings_observation_id: int


class KakeralootSettingsRepository:
    """Persist server-scoped Kakeraloot Settings observations in an initialized database."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    @property
    def database_path(self) -> Path:
        """Return the explicit SQLite database path used by this repository."""
        return self._database_path

    def import_kakeraloot_settings(
        self,
        settings: KakeralootSettingsSnapshot,
        server_name: str,
        raw_message: str,
        source: str,
    ) -> KakeralootSettingsImportResult:
        """Store the latest server-scoped Kakeraloot price configuration."""
        observed_at = datetime.now(timezone.utc)
        imported = run_write_transaction(
            self._database_path,
            lambda connection: self._import_kakeraloot_settings_with_connection(
                connection,
                settings=settings,
                server=server_name,
                raw=raw_message,
                source=source,
                observed_at=observed_at,
            ),
        )
        return KakeralootSettingsImportResult(
            import_event_id=imported.import_event_id,
            server_name=server_name.strip(),
            observed_at=observed_at,
        )

    def _import_kakeraloot_settings_with_connection(
        self,
        connection: sqlite3.Connection,
        *,
        settings: KakeralootSettingsSnapshot,
        server: str,
        raw: str,
        source: str,
        observed_at: datetime,
    ) -> _KakeralootSettingsImportConnectionResult:
        """Store one Kakeraloot Settings snapshot without taking transaction ownership."""
        cursor = connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
            ("kakeraloot_settings", source, observed_at.isoformat(), raw),
        )
        import_event_id = int(cursor.lastrowid)
        server_id = upsert_server(connection, server, observed_at)
        kakeraloot_settings_observation_id = int(
            connection.execute(
                """
                INSERT INTO kakeraloot_settings_observations (
                    server_context_id, loot_cost, quantity_quality_base_cost,
                    quantity_quality_level_increment, observed_at, import_event_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    server_id,
                    settings.loot_cost,
                    settings.quantity_quality_base_cost,
                    settings.quantity_quality_level_increment,
                    observed_at.isoformat(),
                    import_event_id,
                ),
            ).lastrowid
        )
        return _KakeralootSettingsImportConnectionResult(
            import_event_id=import_event_id,
            kakeraloot_settings_observation_id=kakeraloot_settings_observation_id,
        )

    def kakeraloot_settings(self, server_name: str) -> KakeralootSettingsObservation | None:
        """Return the latest `$infokl` price configuration for one server."""
        with connect(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT kakeraloot_settings_observations.*, server_contexts.name AS server_name
                FROM server_contexts
                JOIN kakeraloot_settings_observations ON kakeraloot_settings_observations.id = (
                    SELECT observations.id FROM kakeraloot_settings_observations AS observations
                    WHERE observations.server_context_id = server_contexts.id
                    ORDER BY observations.id DESC LIMIT 1
                )
                WHERE server_contexts.normalized_name = ?
                """,
                (normalize(server_name),),
            ).fetchone()
        if row is None:
            return None
        return KakeralootSettingsObservation(
            server_name=row["server_name"],
            loot_cost=row["loot_cost"],
            quantity_quality_base_cost=row["quantity_quality_base_cost"],
            quantity_quality_level_increment=row["quantity_quality_level_increment"],
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )
