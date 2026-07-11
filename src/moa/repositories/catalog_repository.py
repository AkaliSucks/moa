"""SQLite-backed repository for MOA's imported character catalog."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from moa.database.sqlite import connect
from moa.models.catalog import CatalogCharacter, RankedCatalogCharacter, TopImportResult
from moa.models.character import TopPage


class CatalogRepositoryProtocol(Protocol):
    """Storage contract required by :class:`CatalogService`."""

    def import_top_page(self, page: TopPage, raw_message: str, source: str) -> TopImportResult: ...

    def top(self, limit: int) -> tuple[RankedCatalogCharacter, ...]: ...

    def character_count(self) -> int: ...


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
                normalized_name = self._normalize(ranked_character.name)
                normalized_series = self._normalize(ranked_character.series)
                connection.execute(
                    """
                    INSERT INTO characters (
                        name, series, normalized_name, normalized_series, gender, roulette, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)
                    ON CONFLICT(normalized_name, normalized_series) DO UPDATE SET
                        name = excluded.name,
                        series = excluded.series,
                        updated_at = excluded.updated_at
                    """,
                    (
                        ranked_character.name,
                        ranked_character.series,
                        normalized_name,
                        normalized_series,
                        observed_at.isoformat(),
                        observed_at.isoformat(),
                    ),
                )
                character_id = connection.execute(
                    """
                    SELECT id FROM characters
                    WHERE normalized_name = ? AND normalized_series = ?
                    """,
                    (normalized_name, normalized_series),
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
                """
            )

    def _connection(self) -> sqlite3.Connection:
        return connect(self._database_path)

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.casefold().split())
