"""SQLite persistence for account-scoped Wishlist observations."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from moa.database.sqlite import connect, run_write_transaction
from moa.models.catalog import WishlistImportResult, WishlistObservation
from moa.models.character import WishlistSnapshot
from moa.repositories._catalog_identity import normalize, upsert_account, upsert_server


@dataclass(frozen=True, slots=True)
class _WishlistImportConnectionResult:
    """Rows created by one Wishlist import on a caller-owned connection."""

    import_event_id: int
    wishlist_observation_id: int


class WishlistRepository:
    """Persist account-scoped Wishlist observations in an initialized database."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    @property
    def database_path(self) -> Path:
        """Return the explicit SQLite database path used by this repository."""
        return self._database_path

    def import_wishlist(
        self,
        wishlist: WishlistSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> WishlistImportResult:
        """Store a complete account-scoped `$wl` snapshot."""
        observed_at = datetime.now(timezone.utc)
        imported = run_write_transaction(
            self._database_path,
            lambda connection: self._import_wishlist_with_connection(
                connection,
                state=wishlist,
                server=server_name,
                account=account_name,
                raw=raw_message,
                source=source,
                observed_at=observed_at,
            ),
        )
        return WishlistImportResult(
            import_event_id=imported.import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observed_at=observed_at,
        )

    def _import_wishlist_with_connection(
        self,
        connection: sqlite3.Connection,
        *,
        state: WishlistSnapshot,
        server: str,
        account: str,
        raw: str,
        source: str,
        observed_at: datetime,
    ) -> _WishlistImportConnectionResult:
        """Store one Wishlist snapshot without taking transaction ownership."""
        cursor = connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
            ("wishlist", source, observed_at.isoformat(), raw),
        )
        import_event_id = int(cursor.lastrowid)
        server_id = upsert_server(connection, server, observed_at)
        account_id = upsert_account(connection, server_id, account, observed_at)
        wishlist_observation_id = int(
            connection.execute(
                """
                INSERT INTO wishlist_observations (
                    account_context_id, wishlist_count, wishlist_capacity, starwish_count,
                    starwish_capacity, entries_json, observed_at, import_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    state.wishlist_count,
                    state.wishlist_capacity,
                    state.starwish_count,
                    state.starwish_capacity,
                    json.dumps([entry.model_dump() for entry in state.entries]),
                    observed_at.isoformat(),
                    import_event_id,
                ),
            ).lastrowid
        )
        return _WishlistImportConnectionResult(
            import_event_id=import_event_id,
            wishlist_observation_id=wishlist_observation_id,
        )

    def wishlist(self, server_name: str, account_name: str) -> WishlistObservation | None:
        """Return the latest `$wl` snapshot for one server/account pair."""
        with connect(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT wishlist_observations.*, server_contexts.name AS server_name,
                       account_contexts.name AS account_name
                FROM account_contexts
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                JOIN wishlist_observations ON wishlist_observations.id = (
                    SELECT observations.id FROM wishlist_observations AS observations
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
        return WishlistObservation(
            server_name=row["server_name"],
            account_name=row["account_name"],
            wishlist_count=row["wishlist_count"],
            wishlist_capacity=row["wishlist_capacity"],
            starwish_count=row["starwish_count"],
            starwish_capacity=row["starwish_capacity"],
            entries=tuple(json.loads(row["entries_json"])),
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )
