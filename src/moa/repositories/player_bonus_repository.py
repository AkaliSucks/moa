"""SQLite persistence for account-scoped player-bonus observations."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from moa.database.sqlite import connect_read_only, run_write_transaction
from moa.models.catalog import PlayerBonusImportResult, PlayerBonusObservation
from moa.models.character import PlayerBonusSnapshot
from moa.repositories._catalog_identity import normalize, upsert_account, upsert_server


@dataclass(frozen=True, slots=True)
class _PlayerBonusImportConnectionResult:
    """Rows created by one player-bonus import on a caller-owned connection."""

    import_event_id: int
    player_bonus_observation_id: int


class PlayerBonusRepository:
    """Persist account-scoped player-bonus observations in an initialized database."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    @property
    def database_path(self) -> Path:
        """Return the explicit SQLite database path used by this repository."""
        return self._database_path

    def import_player_bonus(
        self,
        bonus: PlayerBonusSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> PlayerBonusImportResult:
        """Store a complete, account-scoped `$bonus` snapshot."""
        observed_at = datetime.now(timezone.utc)
        imported = run_write_transaction(
            self._database_path,
            lambda connection: self._import_player_bonus_with_connection(
                connection,
                state=bonus,
                server=server_name,
                account=account_name,
                raw=raw_message,
                source=source,
                observed_at=observed_at,
            ),
        )
        return PlayerBonusImportResult(
            import_event_id=imported.import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observed_at=observed_at,
        )

    def _import_player_bonus_with_connection(
        self,
        connection: sqlite3.Connection,
        *,
        state: PlayerBonusSnapshot,
        server: str,
        account: str,
        raw: str,
        source: str,
        observed_at: datetime,
    ) -> _PlayerBonusImportConnectionResult:
        """Store one player-bonus snapshot without taking transaction ownership."""
        cursor = connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
            ("player_bonus", source, observed_at.isoformat(), raw),
        )
        import_event_id = int(cursor.lastrowid)
        server_id = upsert_server(connection, server, observed_at)
        account_id = upsert_account(connection, server_id, account, observed_at)
        player_bonus_observation_id = int(
            connection.execute(
                """
                INSERT INTO player_bonus_observations (
                    account_context_id, metrics_json, rolls_per_hour_bonus, wishlist_slot_bonus,
                    wish_spawn_bonus_percent, starwish_spawn_bonus_percent,
                    starwish_total_spawn_bonus_percent, starwish_slot_bonus,
                    additional_wish_key_chance_percent, kakera_max_power_percent,
                    kakera_button_power_cost_percent, starwish_kakera_button_bonus_percent,
                    light_kakera_minimum, light_kakera_maximum, observed_at, import_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    json.dumps([metric.model_dump() for metric in state.metrics]),
                    state.rolls_per_hour_bonus,
                    state.wishlist_slot_bonus,
                    state.wish_spawn_bonus_percent,
                    state.starwish_spawn_bonus_percent,
                    state.starwish_total_spawn_bonus_percent,
                    state.starwish_slot_bonus,
                    state.additional_wish_key_chance_percent,
                    state.kakera_max_power_percent,
                    state.kakera_button_power_cost_percent,
                    state.starwish_kakera_button_bonus_percent,
                    state.light_kakera_minimum,
                    state.light_kakera_maximum,
                    observed_at.isoformat(),
                    import_event_id,
                ),
            ).lastrowid
        )
        return _PlayerBonusImportConnectionResult(
            import_event_id=import_event_id,
            player_bonus_observation_id=player_bonus_observation_id,
        )

    def player_bonus(
        self, server_name: str, account_name: str
    ) -> PlayerBonusObservation | None:
        """Return the latest player-bonus snapshot for one server/account pair."""
        with connect_read_only(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT player_bonus_observations.*, server_contexts.name AS server_name,
                       account_contexts.name AS account_name
                FROM account_contexts
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                JOIN player_bonus_observations ON player_bonus_observations.id = (
                    SELECT observations.id FROM player_bonus_observations AS observations
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
        return PlayerBonusObservation(
            server_name=row["server_name"],
            account_name=row["account_name"],
            metrics=tuple(json.loads(row["metrics_json"])),
            rolls_per_hour_bonus=row["rolls_per_hour_bonus"],
            wishlist_slot_bonus=row["wishlist_slot_bonus"],
            wish_spawn_bonus_percent=row["wish_spawn_bonus_percent"],
            starwish_spawn_bonus_percent=row["starwish_spawn_bonus_percent"],
            starwish_total_spawn_bonus_percent=row["starwish_total_spawn_bonus_percent"],
            starwish_slot_bonus=row["starwish_slot_bonus"],
            additional_wish_key_chance_percent=row["additional_wish_key_chance_percent"],
            kakera_max_power_percent=row["kakera_max_power_percent"],
            kakera_button_power_cost_percent=row["kakera_button_power_cost_percent"],
            starwish_kakera_button_bonus_percent=row["starwish_kakera_button_bonus_percent"],
            light_kakera_minimum=row["light_kakera_minimum"],
            light_kakera_maximum=row["light_kakera_maximum"],
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )
