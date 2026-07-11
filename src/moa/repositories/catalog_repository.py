"""SQLite-backed repository for MOA's imported character catalog."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from moa.database.sqlite import connect
from moa.models.catalog import (
    CatalogCharacter,
    CharacterDetailsImportResult,
    CharacterProfile,
    ImportEventSummary,
    RankedCatalogCharacter,
    ServerKakeraObservation,
    TopImportResult,
)
from moa.models.character import CharacterDetails, TopPage


class CatalogRepositoryProtocol(Protocol):
    """Storage contract required by :class:`CatalogService`."""

    def import_top_page(self, page: TopPage, raw_message: str, source: str) -> TopImportResult: ...

    def import_character_details(
        self,
        details: CharacterDetails,
        server_name: str,
        raw_message: str,
        source: str,
    ) -> CharacterDetailsImportResult: ...

    def top(self, limit: int) -> tuple[RankedCatalogCharacter, ...]: ...

    def character_count(self) -> int: ...

    def get_profile(self, name: str, series: str) -> CharacterProfile | None: ...

    def recent_imports(self, limit: int) -> tuple[ImportEventSummary, ...]: ...

    def delete_import_event(self, import_event_id: int) -> bool: ...


class CatalogRepository:
    """Persist imported Mudae character and rank observations in SQLite."""

    def __init__(self, database_path: Path | None = None) -> None:
        self._database_path = database_path
        self._initialize()

    def import_top_page(self, page: TopPage, raw_message: str, source: str) -> TopImportResult:
        """Upsert characters and append one rank snapshot per imported row."""
        observed_at = datetime.now(timezone.utc)
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO import_events (kind, source, observed_at, raw_message)
                VALUES (?, ?, ?, ?)
                """,
                ("top_page", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)

            for ranked_character in page.characters:
                character_id = self._upsert_character(
                    connection,
                    name=ranked_character.name,
                    series=ranked_character.series,
                    gender=None,
                    roulette=None,
                    observed_at=observed_at,
                ).fetchone()["id"]
                connection.execute(
                    """
                    INSERT INTO rank_snapshots (
                        character_id, claim_rank, like_rank, observed_at, import_event_id
                    ) VALUES (?, ?, NULL, ?, ?)
                    """,
                    (character_id, ranked_character.claim_rank, observed_at.isoformat(), import_event_id),
                )

        return TopImportResult(
            import_event_id=import_event_id,
            characters_imported=len(page.characters),
            observed_at=observed_at,
        )

    def import_character_details(
        self,
        details: CharacterDetails,
        server_name: str,
        raw_message: str,
        source: str,
    ) -> CharacterDetailsImportResult:
        """Upsert one `$im` response and preserve its server-specific value."""
        observed_at = datetime.now(timezone.utc)
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO import_events (kind, source, observed_at, raw_message)
                VALUES (?, ?, ?, ?)
                """,
                ("character_details", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)
            character_id = self._upsert_character(
                connection,
                name=details.name,
                series=details.series,
                gender=details.gender,
                roulette=details.roulette,
                observed_at=observed_at,
            ).fetchone()["id"]

            if details.claim_rank is not None or details.like_rank is not None:
                connection.execute(
                    """
                    INSERT INTO rank_snapshots (
                        character_id, claim_rank, like_rank, observed_at, import_event_id
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        character_id,
                        details.claim_rank,
                        details.like_rank,
                        observed_at.isoformat(),
                        import_event_id,
                    ),
                )

            normalized_server_name = self._normalize(server_name)
            connection.execute(
                """
                INSERT INTO server_contexts (name, normalized_name, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(normalized_name) DO UPDATE SET
                    name = excluded.name,
                    updated_at = excluded.updated_at
                """,
                (
                    server_name.strip(),
                    normalized_server_name,
                    observed_at.isoformat(),
                    observed_at.isoformat(),
                ),
            )
            server_id = connection.execute(
                "SELECT id FROM server_contexts WHERE normalized_name = ?",
                (normalized_server_name,),
            ).fetchone()["id"]
            connection.execute(
                """
                INSERT INTO server_character_observations (
                    server_context_id, character_id, kakera_value, observed_at, import_event_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    server_id,
                    character_id,
                    details.kakera_value,
                    observed_at.isoformat(),
                    import_event_id,
                ),
            )

        return CharacterDetailsImportResult(
            import_event_id=import_event_id,
            character_id=character_id,
            server_name=server_name.strip(),
            observed_at=observed_at,
        )

    def top(self, limit: int) -> tuple[RankedCatalogCharacter, ...]:
        """Return characters ordered by their most recently imported claim rank."""
        if limit <= 0:
            raise ValueError("Catalog limit must be positive.")

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    characters.id,
                    characters.name,
                    characters.series,
                    characters.gender,
                    characters.roulette,
                    rank_snapshots.claim_rank,
                    rank_snapshots.like_rank,
                    rank_snapshots.observed_at
                FROM characters
                JOIN rank_snapshots ON rank_snapshots.id = (
                    SELECT snapshots.id
                    FROM rank_snapshots AS snapshots
                    WHERE snapshots.character_id = characters.id
                    ORDER BY snapshots.id DESC
                    LIMIT 1
                )
                WHERE rank_snapshots.claim_rank IS NOT NULL
                ORDER BY rank_snapshots.claim_rank ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return tuple(
            RankedCatalogCharacter(
                character=CatalogCharacter(
                    id=row["id"],
                    name=row["name"],
                    series=row["series"],
                    gender=row["gender"],
                    roulette=row["roulette"],
                ),
                claim_rank=row["claim_rank"],
                like_rank=row["like_rank"],
                observed_at=datetime.fromisoformat(row["observed_at"]),
            )
            for row in rows
        )

    def character_count(self) -> int:
        with self._connection() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM characters").fetchone()[0])

    def get_profile(self, name: str, series: str) -> CharacterProfile | None:
        """Return the latest known global ranks and per-server values for one character."""
        with self._connection() as connection:
            character_row = connection.execute(
                """
                SELECT id, name, series, gender, roulette
                FROM characters
                WHERE normalized_name = ? AND normalized_series = ?
                """,
                (self._normalize(name), self._normalize(series)),
            ).fetchone()
            if character_row is None:
                return None

            rank_row = connection.execute(
                """
                SELECT claim_rank, like_rank, observed_at
                FROM rank_snapshots
                WHERE character_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (character_row["id"],),
            ).fetchone()
            server_rows = connection.execute(
                """
                SELECT server_contexts.name, server_character_observations.kakera_value,
                       server_character_observations.observed_at
                FROM server_contexts
                JOIN server_character_observations ON server_character_observations.id = (
                    SELECT observations.id
                    FROM server_character_observations AS observations
                    WHERE observations.server_context_id = server_contexts.id
                      AND observations.character_id = ?
                    ORDER BY observations.id DESC
                    LIMIT 1
                )
                ORDER BY server_contexts.name COLLATE NOCASE
                """,
                (character_row["id"],),
            ).fetchall()

        character = CatalogCharacter(
            id=character_row["id"],
            name=character_row["name"],
            series=character_row["series"],
            gender=character_row["gender"],
            roulette=character_row["roulette"],
        )
        return CharacterProfile(
            character=character,
            claim_rank=rank_row["claim_rank"] if rank_row else None,
            like_rank=rank_row["like_rank"] if rank_row else None,
            rank_observed_at=datetime.fromisoformat(rank_row["observed_at"]) if rank_row else None,
            server_observations=tuple(
                ServerKakeraObservation(
                    server_name=row["name"],
                    kakera_value=row["kakera_value"],
                    observed_at=datetime.fromisoformat(row["observed_at"]),
                )
                for row in server_rows
            ),
        )

    def recent_imports(self, limit: int) -> tuple[ImportEventSummary, ...]:
        """Return recent raw imports, including their server label when present."""
        if limit <= 0:
            raise ValueError("Import history limit must be positive.")

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    import_events.id,
                    import_events.kind,
                    import_events.source,
                    import_events.observed_at,
                    server_contexts.name AS server_name
                FROM import_events
                LEFT JOIN server_character_observations
                    ON server_character_observations.import_event_id = import_events.id
                LEFT JOIN server_contexts
                    ON server_contexts.id = server_character_observations.server_context_id
                GROUP BY import_events.id
                ORDER BY import_events.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return tuple(
            ImportEventSummary(
                id=row["id"],
                kind=row["kind"],
                source=row["source"],
                server_name=row["server_name"],
                observed_at=datetime.fromisoformat(row["observed_at"]),
            )
            for row in rows
        )

    def delete_import_event(self, import_event_id: int) -> bool:
        """Delete one raw import and all observations derived from it.

        Canonical character records stay in the catalog. This preserves data
        imported from other messages while removing only the mistaken evidence.
        """
        with self._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM import_events WHERE id = ?", (import_event_id,)
            ).fetchone()
            if exists is None:
                return False

            connection.execute(
                "DELETE FROM server_character_observations WHERE import_event_id = ?",
                (import_event_id,),
            )
            connection.execute(
                "DELETE FROM rank_snapshots WHERE import_event_id = ?",
                (import_event_id,),
            )
            connection.execute("DELETE FROM import_events WHERE id = ?", (import_event_id,))
        return True

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS characters (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    series TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    normalized_series TEXT NOT NULL,
                    gender TEXT,
                    roulette TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(normalized_name, normalized_series)
                );

                CREATE TABLE IF NOT EXISTS import_events (
                    id INTEGER PRIMARY KEY,
                    kind TEXT NOT NULL,
                    source TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    raw_message TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS rank_snapshots (
                    id INTEGER PRIMARY KEY,
                    character_id INTEGER NOT NULL REFERENCES characters(id),
                    claim_rank INTEGER,
                    like_rank INTEGER,
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id)
                );

                CREATE TABLE IF NOT EXISTS server_contexts (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS server_character_observations (
                    id INTEGER PRIMARY KEY,
                    server_context_id INTEGER NOT NULL REFERENCES server_contexts(id),
                    character_id INTEGER NOT NULL REFERENCES characters(id),
                    kakera_value INTEGER,
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id)
                );
                """
            )

    def _connection(self) -> sqlite3.Connection:
        return connect(self._database_path)

    def _upsert_character(
        self,
        connection: sqlite3.Connection,
        *,
        name: str,
        series: str,
        gender: str | None,
        roulette: str | None,
        observed_at: datetime,
    ) -> sqlite3.Cursor:
        normalized_name = self._normalize(name)
        normalized_series = self._normalize(series)
        connection.execute(
            """
            INSERT INTO characters (
                name, series, normalized_name, normalized_series, gender, roulette, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(normalized_name, normalized_series) DO UPDATE SET
                name = excluded.name,
                series = excluded.series,
                gender = COALESCE(excluded.gender, characters.gender),
                roulette = COALESCE(excluded.roulette, characters.roulette),
                updated_at = excluded.updated_at
            """,
            (
                name,
                series,
                normalized_name,
                normalized_series,
                gender,
                roulette,
                observed_at.isoformat(),
                observed_at.isoformat(),
            ),
        )
        return connection.execute(
            "SELECT id FROM characters WHERE normalized_name = ? AND normalized_series = ?",
            (normalized_name, normalized_series),
        )

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.casefold().split())
