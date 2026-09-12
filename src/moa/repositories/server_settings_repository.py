"""SQLite persistence for server-scoped Server Settings observations."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from moa.database.sqlite import connect_read_only, run_write_transaction
from moa.models.catalog import ServerSettingsImportResult, ServerSettingsObservation
from moa.models.character import ServerSettingsSnapshot
from moa.repositories._catalog_identity import normalize, upsert_server


@dataclass(frozen=True, slots=True)
class _ServerSettingsImportConnectionResult:
    """Rows created by one Server Settings import on a caller-owned connection."""

    import_event_id: int
    server_settings_observation_id: int


class ServerSettingsRepository:
    """Persist server-scoped Server Settings observations in an initialized database."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    @property
    def database_path(self) -> Path:
        """Return the explicit SQLite database path used by this repository."""
        return self._database_path

    def import_server_settings(
        self,
        settings: ServerSettingsSnapshot,
        server_name: str,
        raw_message: str,
        source: str,
    ) -> ServerSettingsImportResult:
        """Store a complete server-scoped `$settings` snapshot."""
        observed_at = datetime.now(timezone.utc)
        imported = run_write_transaction(
            self._database_path,
            lambda connection: self._import_server_settings_with_connection(
                connection,
                settings=settings,
                server=server_name,
                raw=raw_message,
                source=source,
                observed_at=observed_at,
            ),
        )
        return ServerSettingsImportResult(
            import_event_id=imported.import_event_id,
            server_name=server_name.strip(),
            observed_at=observed_at,
        )

    def _import_server_settings_with_connection(
        self,
        connection: sqlite3.Connection,
        *,
        settings: ServerSettingsSnapshot,
        server: str,
        raw: str,
        source: str,
        observed_at: datetime,
    ) -> _ServerSettingsImportConnectionResult:
        """Store one Server Settings snapshot without taking transaction ownership."""
        cursor = connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
            ("server_settings", source, observed_at.isoformat(), raw),
        )
        import_event_id = int(cursor.lastrowid)
        server_id = upsert_server(connection, server, observed_at)
        server_settings_observation_id = int(
            connection.execute(
                """
                INSERT INTO server_settings_observations (
                    server_context_id, server_premium, prefix, language, claim_reset_minutes,
                    reset_minute, reset_shift_minutes, rolls_per_hour, claim_reaction_expiry_seconds,
                    claimed_character_rarity_multiplier, kakera_bonus_percent, sphere_bonus_percent,
                    game_mode, channel_instance, metrics_json, observed_at, import_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    server_id,
                    int(settings.server_premium),
                    settings.prefix,
                    settings.language,
                    settings.claim_reset_minutes,
                    settings.reset_minute,
                    settings.reset_shift_minutes,
                    settings.rolls_per_hour,
                    settings.claim_reaction_expiry_seconds,
                    settings.claimed_character_rarity_multiplier,
                    settings.kakera_bonus_percent,
                    settings.sphere_bonus_percent,
                    settings.game_mode,
                    settings.channel_instance,
                    json.dumps([metric.model_dump() for metric in settings.metrics]),
                    observed_at.isoformat(),
                    import_event_id,
                ),
            ).lastrowid
        )
        return _ServerSettingsImportConnectionResult(
            import_event_id=import_event_id,
            server_settings_observation_id=server_settings_observation_id,
        )

    def server_settings(self, server_name: str) -> ServerSettingsObservation | None:
        """Return the latest `$settings` snapshot for one server."""
        with connect_read_only(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT server_settings_observations.*, server_contexts.name AS server_name
                FROM server_contexts
                JOIN server_settings_observations ON server_settings_observations.id = (
                    SELECT observations.id FROM server_settings_observations AS observations
                    WHERE observations.server_context_id = server_contexts.id
                    ORDER BY observations.id DESC LIMIT 1
                )
                WHERE server_contexts.normalized_name = ?
                """,
                (normalize(server_name),),
            ).fetchone()
        if row is None:
            return None
        return ServerSettingsObservation(
            server_name=row["server_name"],
            server_premium=bool(row["server_premium"]),
            prefix=row["prefix"],
            language=row["language"],
            claim_reset_minutes=row["claim_reset_minutes"],
            reset_minute=row["reset_minute"],
            reset_shift_minutes=row["reset_shift_minutes"],
            rolls_per_hour=row["rolls_per_hour"],
            claim_reaction_expiry_seconds=row["claim_reaction_expiry_seconds"],
            claimed_character_rarity_multiplier=row["claimed_character_rarity_multiplier"],
            kakera_bonus_percent=row["kakera_bonus_percent"],
            sphere_bonus_percent=row["sphere_bonus_percent"],
            game_mode=row["game_mode"],
            channel_instance=row["channel_instance"],
            metrics=tuple(json.loads(row["metrics_json"])),
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )
