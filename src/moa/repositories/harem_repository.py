"""SQLite persistence for harem key and owned-character scan observations."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from moa.database.sqlite import connect, run_write_transaction
from moa.models.catalog import (
    CatalogCharacter,
    HaremKeyImportResult,
    HaremKeyObservation,
    HaremScanProgress,
    OwnedCharacterObservation,
    RankedHaremImportResult,
)
from moa.models.character import HaremKeyPage, RankedHaremPage
from moa.repositories._catalog_identity import normalize, upsert_account, upsert_server


class HaremRepository:
    """Persist harem key and owned-character observations in an initialized database."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    @property
    def database_path(self) -> Path:
        """Return the explicit SQLite database path used by this repository."""
        return self._database_path

    def import_harem_key_page(
        self,
        page: HaremKeyPage,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
        scan_id: int | None = None,
    ) -> HaremKeyImportResult:
        """Append a keyed-harem page while retaining unresolved names safely."""
        observed_at = datetime.now(timezone.utc)

        def import_with_connection(
            connection: sqlite3.Connection,
        ) -> HaremKeyImportResult:
            cursor = connection.execute(
                """
                INSERT INTO import_events (kind, source, observed_at, raw_message)
                VALUES (?, ?, ?, ?)
                """,
                ("harem_key_page", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)
            server_id = upsert_server(connection, server_name, observed_at)
            account_id = upsert_account(
                connection, server_id, account_name, observed_at
            )
            if scan_id is not None:
                self._prepare_harem_scan_page(
                    connection, scan_id, account_id, page, "keys"
                )
            linked_entries = 0

            for entry in page.entries:
                normalized_name = normalize(entry.name)
                matches = connection.execute(
                    "SELECT id FROM characters WHERE normalized_name = ?",
                    (normalized_name,),
                ).fetchall()
                character_id = matches[0]["id"] if len(matches) == 1 else None
                linked_entries += character_id is not None
                connection.execute(
                    """
                    INSERT INTO harem_key_observations (
                        account_context_id, character_id, character_name,
                        normalized_character_name, key_type, key_count, kakera_value,
                        observed_at, import_event_id, harem_scan_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        character_id,
                        entry.name,
                        normalized_name,
                        entry.key_type,
                        entry.key_count,
                        entry.kakera_value,
                        observed_at.isoformat(),
                        import_event_id,
                        scan_id,
                    ),
                )

            if scan_id is not None:
                connection.execute(
                    "INSERT INTO harem_scan_pages "
                    "(harem_scan_id, page_number, import_event_id) VALUES (?, ?, ?)",
                    (scan_id, page.page_number, import_event_id),
                )

            return HaremKeyImportResult(
                import_event_id=import_event_id,
                server_name=server_name.strip(),
                account_name=account_name.strip(),
                entries_imported=len(page.entries),
                entries_linked=linked_entries,
                observed_at=observed_at,
                scan_id=scan_id,
                page_number=page.page_number,
                page_count=page.page_count,
            )

        return run_write_transaction(self._database_path, import_with_connection)

    def import_ranked_harem_page(
        self,
        page: RankedHaremPage,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
        scan_id: int | None = None,
    ) -> RankedHaremImportResult:
        """Store direct owned-character evidence from one ranked `$mm` page."""
        observed_at = datetime.now(timezone.utc)

        def import_with_connection(
            connection: sqlite3.Connection,
        ) -> RankedHaremImportResult:
            cursor = connection.execute(
                "INSERT INTO import_events "
                "(kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("ranked_harem_page", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)
            server_id = upsert_server(connection, server_name, observed_at)
            account_id = upsert_account(
                connection, server_id, account_name, observed_at
            )
            if scan_id is not None:
                self._prepare_harem_scan_page(
                    connection, scan_id, account_id, page, "owned"
                )
            linked_entries = 0
            for entry in page.entries:
                normalized_name = normalize(entry.name)
                matches = connection.execute(
                    "SELECT id FROM characters WHERE normalized_name = ?",
                    (normalized_name,),
                ).fetchall()
                character_id = matches[0]["id"] if len(matches) == 1 else None
                linked_entries += character_id is not None
                connection.execute(
                    """
                    INSERT INTO owned_character_observations (
                        account_context_id, character_id, character_name,
                        normalized_character_name, claim_rank, kakera_value,
                        roulette_types_json, observed_at, import_event_id, harem_scan_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        character_id,
                        entry.name,
                        normalized_name,
                        entry.claim_rank,
                        entry.kakera_value,
                        json.dumps(list(entry.roulette_types)),
                        observed_at.isoformat(),
                        import_event_id,
                        scan_id,
                    ),
                )
                if entry.key_type is not None and entry.key_count is not None:
                    connection.execute(
                        """
                        INSERT INTO harem_key_observations (
                            account_context_id, character_id, character_name,
                            normalized_character_name, key_type, key_count,
                            kakera_value, observed_at, import_event_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            account_id,
                            character_id,
                            entry.name,
                            normalized_name,
                            entry.key_type,
                            entry.key_count,
                            entry.kakera_value,
                            observed_at.isoformat(),
                            import_event_id,
                        ),
                    )
            if scan_id is not None:
                connection.execute(
                    "INSERT INTO harem_scan_pages "
                    "(harem_scan_id, page_number, import_event_id) VALUES (?, ?, ?)",
                    (scan_id, page.page_number, import_event_id),
                )

            return RankedHaremImportResult(
                import_event_id=import_event_id,
                server_name=server_name.strip(),
                account_name=account_name.strip(),
                entries_imported=len(page.entries),
                entries_linked=linked_entries,
                observed_at=observed_at,
                scan_id=scan_id,
                page_number=page.page_number,
                page_count=page.page_count,
            )

        return run_write_transaction(self._database_path, import_with_connection)

    def owned_characters(
        self, server_name: str, account_name: str
    ) -> tuple[OwnedCharacterObservation, ...]:
        """Return the latest direct owned-character evidence per account/name."""
        with self._connection() as connection:
            active_scan_id = self._active_harem_scan_id(
                connection, server_name, account_name, "owned"
            )
            latest_scan_filter = (
                "AND latest.harem_scan_id = ?"
                if active_scan_id
                else "AND latest.harem_scan_id IS NULL"
            )
            outer_scan_filter = (
                "AND observations.harem_scan_id = ?"
                if active_scan_id
                else "AND observations.harem_scan_id IS NULL"
            )
            params: tuple[object, ...] = (
                (
                    active_scan_id,
                    normalize(server_name),
                    normalize(account_name),
                    active_scan_id,
                )
                if active_scan_id
                else (normalize(server_name), normalize(account_name))
            )
            rows = connection.execute(
                f"""
                SELECT observations.character_name, observations.claim_rank,
                       observations.kakera_value, observations.roulette_types_json,
                       observations.observed_at,
                       characters.id AS character_id, characters.name,
                       characters.series, characters.gender, characters.roulette
                FROM account_contexts
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                JOIN owned_character_observations AS observations
                  ON observations.id = (
                      SELECT latest.id
                      FROM owned_character_observations AS latest
                      WHERE latest.account_context_id = account_contexts.id
                        AND (
                            (
                                latest.character_id IS NOT NULL
                                AND observations.character_id IS NOT NULL
                                AND latest.character_id = observations.character_id
                            )
                            OR (
                                latest.character_id IS NULL
                                AND observations.character_id IS NULL
                                AND latest.normalized_character_name =
                                    observations.normalized_character_name
                            )
                        )
                        {latest_scan_filter}
                      ORDER BY latest.id DESC
                      LIMIT 1
                  )
                LEFT JOIN characters ON characters.id = observations.character_id
                    OR (
                        observations.character_id IS NULL
                        AND characters.normalized_name = observations.normalized_character_name
                        AND 1 = (
                            SELECT COUNT(*)
                            FROM characters AS candidates
                            WHERE candidates.normalized_name = observations.normalized_character_name
                        )
                    )
                WHERE server_contexts.normalized_name = ?
                  AND account_contexts.normalized_name = ?
                  {outer_scan_filter}
                  AND NOT EXISTS (
                      SELECT 1
                      FROM divorce_observations AS divorces
                      WHERE divorces.account_context_id = observations.account_context_id
                        AND divorces.import_event_id > observations.import_event_id
                        AND (
                            (
                                divorces.character_id IS NOT NULL
                                AND observations.character_id IS NOT NULL
                                AND divorces.character_id = observations.character_id
                            )
                            OR (
                                divorces.normalized_character_name =
                                    observations.normalized_character_name
                                AND (
                                    (
                                        divorces.character_id IS NULL
                                        AND observations.character_id IS NULL
                                    )
                                    OR (
                                        (divorces.character_id IS NULL) !=
                                            (observations.character_id IS NULL)
                                        AND 1 = (
                                            SELECT COUNT(*)
                                            FROM characters AS candidates
                                            WHERE candidates.normalized_name =
                                                observations.normalized_character_name
                                        )
                                    )
                                )
                            )
                        )
                  )
                ORDER BY observations.claim_rank ASC,
                         observations.character_name COLLATE NOCASE
                """,
                params,
            ).fetchall()
        return tuple(
            OwnedCharacterObservation(
                character_name=row["character_name"],
                character=self._catalog_character(row),
                claim_rank=row["claim_rank"],
                kakera_value=row["kakera_value"],
                roulette_types=tuple(json.loads(row["roulette_types_json"] or "[]")),
                observed_at=datetime.fromisoformat(row["observed_at"]),
            )
            for row in rows
        )

    def harem_keys(
        self, server_name: str, account_name: str
    ) -> tuple[HaremKeyObservation, ...]:
        """Return latest key observations for one account in one server context."""
        with self._connection() as connection:
            active_scan_id = self._active_harem_scan_id(
                connection, server_name, account_name, "keys"
            )
            scan_filter = (
                "(harem_key_observations.harem_scan_id = ? "
                "OR harem_key_observations.harem_scan_id IS NULL)"
                if active_scan_id
                else "harem_key_observations.harem_scan_id IS NULL"
            )
            scan_params: tuple[object, ...] = (
                (active_scan_id,) if active_scan_id else ()
            )
            rows = connection.execute(
                f"""
                SELECT
                    harem_key_observations.character_name,
                    harem_key_observations.key_type,
                    harem_key_observations.key_count,
                    harem_key_observations.kakera_value,
                    harem_key_observations.observed_at,
                    characters.id AS character_id,
                    characters.name,
                    characters.series,
                    characters.gender,
                    characters.roulette
                FROM account_contexts
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                JOIN harem_key_observations ON harem_key_observations.id = (
                    SELECT observations.id
                    FROM harem_key_observations AS observations
                    WHERE observations.account_context_id = account_contexts.id
                      AND (
                          (
                              observations.character_id IS NOT NULL
                              AND harem_key_observations.character_id IS NOT NULL
                              AND observations.character_id =
                                  harem_key_observations.character_id
                          )
                          OR (
                              observations.character_id IS NULL
                              AND harem_key_observations.character_id IS NULL
                              AND observations.normalized_character_name =
                                  harem_key_observations.normalized_character_name
                          )
                      )
                      AND (observations.harem_scan_id = ? OR observations.harem_scan_id IS NULL)
                    ORDER BY observations.id DESC
                    LIMIT 1
                )
                LEFT JOIN characters ON characters.id = harem_key_observations.character_id
                    OR (
                        harem_key_observations.character_id IS NULL
                        AND characters.normalized_name = harem_key_observations.normalized_character_name
                        AND 1 = (
                            SELECT COUNT(*)
                            FROM characters AS candidates
                            WHERE candidates.normalized_name = harem_key_observations.normalized_character_name
                        )
                    )
                WHERE server_contexts.normalized_name = ?
                  AND account_contexts.normalized_name = ?
                  AND {scan_filter}
                  AND NOT EXISTS (
                      SELECT 1
                      FROM divorce_observations AS divorces
                      WHERE divorces.account_context_id = harem_key_observations.account_context_id
                        AND divorces.import_event_id > harem_key_observations.import_event_id
                        AND (
                            (
                                divorces.character_id IS NOT NULL
                                AND harem_key_observations.character_id IS NOT NULL
                                AND divorces.character_id =
                                    harem_key_observations.character_id
                            )
                            OR (
                                divorces.normalized_character_name =
                                    harem_key_observations.normalized_character_name
                                AND (
                                    (
                                        divorces.character_id IS NULL
                                        AND harem_key_observations.character_id IS NULL
                                    )
                                    OR (
                                        (divorces.character_id IS NULL) !=
                                            (harem_key_observations.character_id IS NULL)
                                        AND 1 = (
                                            SELECT COUNT(*)
                                            FROM characters AS candidates
                                            WHERE candidates.normalized_name =
                                                harem_key_observations.normalized_character_name
                                        )
                                    )
                                )
                            )
                        )
                  )
                ORDER BY harem_key_observations.kakera_value DESC NULLS LAST,
                         harem_key_observations.key_count DESC,
                         harem_key_observations.character_name COLLATE NOCASE
                """,
                (
                    active_scan_id,
                    normalize(server_name),
                    normalize(account_name),
                    *scan_params,
                ),
            ).fetchall()

        return tuple(self._harem_key_observation(row) for row in rows)

    def recent_key_gains(
        self, server_name: str, account_name: str, limit: int
    ) -> tuple[HaremKeyObservation, ...]:
        """Return key states directly observed on imported rolls, newest first."""
        if limit <= 0:
            raise ValueError("Key-gain limit must be positive.")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT observations.character_name, observations.key_type,
                       observations.key_count, observations.kakera_value,
                       observations.observed_at, characters.id AS character_id,
                       characters.name, characters.series, characters.gender,
                       characters.roulette
                FROM harem_key_observations AS observations
                JOIN import_events ON import_events.id = observations.import_event_id
                JOIN account_contexts ON account_contexts.id = observations.account_context_id
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                LEFT JOIN characters ON characters.id = observations.character_id
                WHERE import_events.kind = 'roll'
                  AND server_contexts.normalized_name = ?
                  AND account_contexts.normalized_name = ?
                ORDER BY observations.id DESC
                LIMIT ?
                """,
                (normalize(server_name), normalize(account_name), limit),
            ).fetchall()
        return tuple(self._harem_key_observation(row) for row in rows)

    def begin_harem_scan(
        self, server_name: str, account_name: str, scan_kind: str = "keys"
    ) -> HaremScanProgress:
        """Start a multi-page harem import that must be completed before activation."""
        normalized_kind = scan_kind.strip().casefold()
        if normalized_kind not in {"keys", "owned"}:
            raise ValueError("Harem scan kind must be `keys` or `owned`.")
        observed_at = datetime.now(timezone.utc)

        def begin_with_connection(connection: sqlite3.Connection) -> int:
            server_id = upsert_server(connection, server_name, observed_at)
            account_id = upsert_account(
                connection, server_id, account_name, observed_at
            )
            cursor = connection.execute(
                "INSERT INTO harem_scans "
                "(account_context_id, expected_page_count, started_at, scan_kind) "
                "VALUES (?, NULL, ?, ?)",
                (account_id, observed_at.isoformat(), normalized_kind),
            )
            return int(cursor.lastrowid)

        scan_id = run_write_transaction(self._database_path, begin_with_connection)
        progress = self.harem_scan_progress(scan_id)
        assert progress is not None
        return progress

    def harem_scan_progress(self, scan_id: int) -> HaremScanProgress | None:
        with self._connection() as connection:
            return self._harem_scan_progress_with_connection(connection, scan_id)

    def _harem_scan_progress_with_connection(
        self, connection: sqlite3.Connection, scan_id: int
    ) -> HaremScanProgress | None:
        """Read scan progress using a caller-owned connection."""
        row = connection.execute(
            """
            SELECT harem_scans.id, server_contexts.name AS server_name,
                   account_contexts.name AS account_name, harem_scans.expected_page_count,
                   harem_scans.completed_at, harem_scans.scan_kind
            FROM harem_scans
            JOIN account_contexts ON account_contexts.id = harem_scans.account_context_id
            JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
            WHERE harem_scans.id = ?
            """,
            (scan_id,),
        ).fetchone()
        if row is None:
            return None
        page_rows = connection.execute(
            "SELECT page_number FROM harem_scan_pages WHERE harem_scan_id = ? "
            "ORDER BY page_number",
            (scan_id,),
        ).fetchall()
        return HaremScanProgress(
            id=row["id"],
            server_name=row["server_name"],
            account_name=row["account_name"],
            expected_page_count=row["expected_page_count"],
            imported_pages=tuple(page_row["page_number"] for page_row in page_rows),
            completed_at=(
                datetime.fromisoformat(row["completed_at"])
                if row["completed_at"] is not None
                else None
            ),
            scan_kind=row["scan_kind"],
        )

    def complete_harem_scan(self, scan_id: int) -> HaremScanProgress:
        def complete_with_connection(
            connection: sqlite3.Connection,
        ) -> HaremScanProgress:
            progress = self._harem_scan_progress_with_connection(connection, scan_id)
            if progress is None:
                raise ValueError("Harem scan not found.")
            if not progress.is_complete:
                expected = progress.expected_page_count or "an unknown number of"
                raise ValueError(
                    f"Harem scan is incomplete: imported pages "
                    f"{list(progress.imported_pages)}; expected {expected} pages."
                )
            completed_at = datetime.now(timezone.utc)
            connection.execute(
                "UPDATE harem_scans SET completed_at = ? WHERE id = ?",
                (completed_at.isoformat(), scan_id),
            )
            completed = self._harem_scan_progress_with_connection(connection, scan_id)
            assert completed is not None
            return completed

        return run_write_transaction(self._database_path, complete_with_connection)

    def has_complete_harem_scan(
        self, server_name: str, account_name: str, scan_kind: str = "keys"
    ) -> bool:
        normalized_kind = scan_kind.strip().casefold()
        if normalized_kind not in {"keys", "owned"}:
            raise ValueError("Harem scan kind must be `keys` or `owned`.")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM harem_scans
                JOIN account_contexts ON account_contexts.id = harem_scans.account_context_id
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                WHERE server_contexts.normalized_name = ?
                  AND account_contexts.normalized_name = ?
                  AND harem_scans.scan_kind = ?
                  AND harem_scans.completed_at IS NOT NULL
                LIMIT 1
                """,
                (
                    normalize(server_name),
                    normalize(account_name),
                    normalized_kind,
                ),
            ).fetchone()
        return row is not None

    def _prepare_harem_scan_page(
        self,
        connection: sqlite3.Connection,
        scan_id: int,
        account_id: int,
        page: HaremKeyPage | RankedHaremPage,
        scan_kind: str,
    ) -> None:
        if page.page_number is None or page.page_count is None:
            raise ValueError("A scanned harem page must include its Page X / Y indicator.")
        scan = connection.execute(
            "SELECT account_context_id, expected_page_count, completed_at, scan_kind "
            "FROM harem_scans WHERE id = ?",
            (scan_id,),
        ).fetchone()
        if scan is None:
            raise ValueError("Harem scan not found.")
        if scan["account_context_id"] != account_id:
            raise ValueError("Harem scan belongs to a different server or account.")
        if scan["scan_kind"] != scan_kind:
            expected = "$mmy" if scan["scan_kind"] == "keys" else "$mmr/$mmrk"
            raise ValueError(f"This harem scan expects {expected} pages.")
        if scan["completed_at"] is not None:
            raise ValueError(
                "Harem scan is already complete; begin a new scan to refresh it."
            )
        if scan["expected_page_count"] not in (None, page.page_count):
            raise ValueError(
                "Harem page count does not match the scan's first imported page."
            )
        duplicate = connection.execute(
            "SELECT 1 FROM harem_scan_pages "
            "WHERE harem_scan_id = ? AND page_number = ?",
            (scan_id, page.page_number),
        ).fetchone()
        if duplicate is not None:
            raise ValueError("This harem scan already contains that page.")
        connection.execute(
            "UPDATE harem_scans SET expected_page_count = ? WHERE id = ?",
            (page.page_count, scan_id),
        )

    def _active_harem_scan_id(
        self,
        connection: sqlite3.Connection,
        server_name: str,
        account_name: str,
        scan_kind: str = "keys",
    ) -> int | None:
        row = connection.execute(
            """
            SELECT harem_scans.id
            FROM harem_scans
            JOIN account_contexts ON account_contexts.id = harem_scans.account_context_id
            JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
            WHERE server_contexts.normalized_name = ?
              AND account_contexts.normalized_name = ?
              AND harem_scans.scan_kind = ?
              AND harem_scans.completed_at IS NOT NULL
            ORDER BY harem_scans.completed_at DESC
            LIMIT 1
            """,
            (normalize(server_name), normalize(account_name), scan_kind),
        ).fetchone()
        return int(row["id"]) if row is not None else None

    def _connection(self) -> sqlite3.Connection:
        return connect(self._database_path)

    @staticmethod
    def _catalog_character(row: sqlite3.Row) -> CatalogCharacter | None:
        if row["character_id"] is None:
            return None
        return CatalogCharacter(
            id=row["character_id"],
            name=row["name"],
            series=row["series"],
            gender=row["gender"],
            roulette=row["roulette"],
        )

    @classmethod
    def _harem_key_observation(cls, row: sqlite3.Row) -> HaremKeyObservation:
        return HaremKeyObservation(
            character_name=row["character_name"],
            character=cls._catalog_character(row),
            key_type=row["key_type"],
            key_count=row["key_count"],
            kakera_value=row["kakera_value"],
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )
