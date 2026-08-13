"""SQLite persistence for account-scoped profile observations."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from moa.database.sqlite import connect, run_write_transaction
from moa.models.catalog import ProfileImportResult, ProfileObservation
from moa.models.character import ProfileSnapshot
from moa.repositories._catalog_identity import normalize, upsert_account, upsert_server


@dataclass(frozen=True, slots=True)
class _ProfileImportConnectionResult:
    """Rows created by one profile import on a caller-owned connection."""

    import_event_id: int
    profile_observation_id: int


class ProfileRepository:
    """Persist account-scoped profile observations in an initialized database."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    @property
    def database_path(self) -> Path:
        """Return the explicit SQLite database path used by this repository."""
        return self._database_path

    def import_profile(
        self,
        snapshot: ProfileSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> ProfileImportResult:
        """Store one account-scoped `$profile` progress snapshot."""
        observed_at = datetime.now(timezone.utc)
        imported = run_write_transaction(
            self._database_path,
            lambda connection: self._import_profile_with_connection(
                connection,
                profile=snapshot,
                server=server_name,
                account=account_name,
                raw=raw_message,
                source=source,
                observed_at=observed_at,
            ),
        )
        return ProfileImportResult(
            import_event_id=imported.import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observed_at=observed_at,
        )

    def _import_profile_with_connection(
        self,
        connection: sqlite3.Connection,
        *,
        profile: ProfileSnapshot,
        server: str,
        account: str,
        raw: str,
        source: str,
        observed_at: datetime,
    ) -> _ProfileImportConnectionResult:
        """Store one profile without taking ownership of the surrounding transaction."""
        cursor = connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
            ("profile", source, observed_at.isoformat(), raw),
        )
        import_event_id = int(cursor.lastrowid)
        server_id = upsert_server(connection, server, observed_at)
        account_id = upsert_account(connection, server_id, account, observed_at)
        profile_observation_id = int(
            connection.execute(
                """
                INSERT INTO profile_observations (
                    account_context_id, profile_name, collection_size, female_percent, male_percent,
                    pokedex_count, pokedex_json, kakera_reacts_json, mudapins_collected,
                    mudapins_total, kakera_balance, bronze_keys, silver_keys, gold_keys,
                    sphere_stock, spheres_json, displayed_badges_json, observed_at, import_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    profile.profile_name,
                    profile.collection_size,
                    profile.female_percent,
                    profile.male_percent,
                    profile.pokedex_count,
                    json.dumps(list(profile.pokedex_pokemon)),
                    json.dumps(profile.kakera_reacts),
                    profile.mudapins_collected,
                    profile.mudapins_total,
                    profile.kakera_balance,
                    profile.bronze_keys,
                    profile.silver_keys,
                    profile.gold_keys,
                    profile.sphere_stock,
                    json.dumps(profile.spheres),
                    json.dumps(list(profile.displayed_badges)),
                    observed_at.isoformat(),
                    import_event_id,
                ),
            ).lastrowid
        )
        return _ProfileImportConnectionResult(
            import_event_id=import_event_id,
            profile_observation_id=profile_observation_id,
        )

    def profile(self, server_name: str, account_name: str) -> ProfileObservation | None:
        """Return the latest `$profile` snapshot for one account."""
        with connect(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT profile_observations.*, server_contexts.name AS server_name,
                       account_contexts.name AS account_name
                FROM account_contexts
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                JOIN profile_observations ON profile_observations.id = (
                    SELECT observations.id FROM profile_observations AS observations
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
        snapshot = ProfileSnapshot(
            profile_name=row["profile_name"],
            collection_size=row["collection_size"],
            female_percent=row["female_percent"],
            male_percent=row["male_percent"],
            pokedex_count=row["pokedex_count"],
            pokedex_pokemon=tuple(json.loads(row["pokedex_json"])),
            kakera_reacts=dict(json.loads(row["kakera_reacts_json"])),
            mudapins_collected=row["mudapins_collected"],
            mudapins_total=row["mudapins_total"],
            kakera_balance=row["kakera_balance"],
            bronze_keys=row["bronze_keys"],
            silver_keys=row["silver_keys"],
            gold_keys=row["gold_keys"],
            sphere_stock=row["sphere_stock"],
            spheres=dict(json.loads(row["spheres_json"])),
            displayed_badges=tuple(json.loads(row["displayed_badges_json"])),
        )
        return ProfileObservation(
            server_name=row["server_name"],
            account_name=row["account_name"],
            snapshot=snapshot,
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )
