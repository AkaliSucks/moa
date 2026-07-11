"""SQLite-backed repository for MOA's imported character catalog."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from moa.database.sqlite import connect
from moa.models.catalog import (
    CatalogCharacter,
    CharacterDetailsImportResult,
    CharacterProfile,
    HaremKeyImportResult,
    HaremKeyObservation,
    HaremScanProgress,
    ImportEventSummary,
    RankedCatalogCharacter,
    ServerKakeraObservation,
    PlayerBonusImportResult,
    PlayerBonusObservation,
    DisableListImportResult,
    DisableListObservation,
    RollabilityImportResult,
    UnavailableCharacterObservation,
    WishlistImportResult,
    WishlistObservation,
    TopImportResult,
)
from moa.models.character import (
    CharacterDetails,
    DisableListSnapshot,
    HaremKeyPage,
    PlayerBonusSnapshot,
    TopPage,
    UnavailableCharacterPage,
    WishlistSnapshot,
)


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

    def import_harem_key_page(
        self,
        page: HaremKeyPage,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
        scan_id: int | None = None,
    ) -> HaremKeyImportResult: ...

    def harem_keys(self, server_name: str, account_name: str) -> tuple[HaremKeyObservation, ...]: ...

    def begin_harem_scan(self, server_name: str, account_name: str) -> HaremScanProgress: ...

    def harem_scan_progress(self, scan_id: int) -> HaremScanProgress | None: ...

    def complete_harem_scan(self, scan_id: int) -> HaremScanProgress: ...

    def import_player_bonus(
        self,
        bonus: PlayerBonusSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> PlayerBonusImportResult: ...

    def player_bonus(self, server_name: str, account_name: str) -> PlayerBonusObservation | None: ...

    def import_wishlist(
        self,
        wishlist: WishlistSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> WishlistImportResult: ...

    def wishlist(self, server_name: str, account_name: str) -> WishlistObservation | None: ...

    def import_disablelist(
        self,
        disablelist: DisableListSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> DisableListImportResult: ...

    def disablelist(self, server_name: str, account_name: str) -> DisableListObservation | None: ...

    def import_unavailable_characters(
        self,
        page: UnavailableCharacterPage,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> RollabilityImportResult: ...

    def unavailable_characters(
        self, server_name: str, account_name: str
    ) -> tuple[UnavailableCharacterObservation, ...]: ...


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
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO import_events (kind, source, observed_at, raw_message)
                VALUES (?, ?, ?, ?)
                """,
                ("harem_key_page", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)
            server_id = self._upsert_server(connection, server_name, observed_at)
            account_id = self._upsert_account(connection, server_id, account_name, observed_at)
            if scan_id is not None:
                self._prepare_harem_scan_page(connection, scan_id, account_id, page)
            linked_entries = 0

            for entry in page.entries:
                normalized_name = self._normalize(entry.name)
                matches = connection.execute(
                    "SELECT id FROM characters WHERE normalized_name = ?",
                    (normalized_name,),
                ).fetchall()
                character_id = matches[0]["id"] if len(matches) == 1 else None
                linked_entries += character_id is not None
                connection.execute(
                    """
                    INSERT INTO harem_key_observations (
                        account_context_id, character_id, character_name, normalized_character_name,
                        key_type, key_count, kakera_value, observed_at, import_event_id
                        , harem_scan_id
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
                    "INSERT INTO harem_scan_pages (harem_scan_id, page_number, import_event_id) "
                    "VALUES (?, ?, ?)",
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

    def harem_keys(self, server_name: str, account_name: str) -> tuple[HaremKeyObservation, ...]:
        """Return latest key observations for one account in one server context."""
        with self._connection() as connection:
            active_scan_id = self._active_harem_scan_id(connection, server_name, account_name)
            scan_filter = "harem_key_observations.harem_scan_id = ?" if active_scan_id else (
                "harem_key_observations.harem_scan_id IS NULL"
            )
            scan_params: tuple[object, ...] = (active_scan_id,) if active_scan_id else ()
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
                      AND observations.normalized_character_name = harem_key_observations.normalized_character_name
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
                ORDER BY harem_key_observations.kakera_value DESC NULLS LAST,
                         harem_key_observations.key_count DESC,
                         harem_key_observations.character_name COLLATE NOCASE
                """,
                (self._normalize(server_name), self._normalize(account_name), *scan_params),
            ).fetchall()

        return tuple(
            HaremKeyObservation(
                character_name=row["character_name"],
                character=(
                    CatalogCharacter(
                        id=row["character_id"],
                        name=row["name"],
                        series=row["series"],
                        gender=row["gender"],
                        roulette=row["roulette"],
                    )
                    if row["character_id"] is not None
                    else None
                ),
                key_type=row["key_type"],
                key_count=row["key_count"],
                kakera_value=row["kakera_value"],
                observed_at=datetime.fromisoformat(row["observed_at"]),
            )
            for row in rows
        )

    def begin_harem_scan(self, server_name: str, account_name: str) -> HaremScanProgress:
        """Start a multi-page harem import that must be completed before activation."""
        observed_at = datetime.now(timezone.utc)
        with self._connection() as connection:
            server_id = self._upsert_server(connection, server_name, observed_at)
            account_id = self._upsert_account(connection, server_id, account_name, observed_at)
            cursor = connection.execute(
                "INSERT INTO harem_scans (account_context_id, expected_page_count, started_at) "
                "VALUES (?, NULL, ?)",
                (account_id, observed_at.isoformat()),
            )
            scan_id = int(cursor.lastrowid)
        progress = self.harem_scan_progress(scan_id)
        assert progress is not None
        return progress

    def harem_scan_progress(self, scan_id: int) -> HaremScanProgress | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT harem_scans.id, server_contexts.name AS server_name,
                       account_contexts.name AS account_name, harem_scans.expected_page_count,
                       harem_scans.completed_at
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
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] is not None else None
            ),
        )

    def complete_harem_scan(self, scan_id: int) -> HaremScanProgress:
        progress = self.harem_scan_progress(scan_id)
        if progress is None:
            raise ValueError("Harem scan not found.")
        if not progress.is_complete:
            expected = progress.expected_page_count or "an unknown number of"
            raise ValueError(
                f"Harem scan is incomplete: imported pages {list(progress.imported_pages)}; "
                f"expected {expected} pages."
            )
        completed_at = datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute(
                "UPDATE harem_scans SET completed_at = ? WHERE id = ?",
                (completed_at.isoformat(), scan_id),
            )
        completed = self.harem_scan_progress(scan_id)
        assert completed is not None
        return completed

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
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("player_bonus", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)
            server_id = self._upsert_server(connection, server_name, observed_at)
            account_id = self._upsert_account(connection, server_id, account_name, observed_at)
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
                    json.dumps([metric.model_dump() for metric in bonus.metrics]),
                    bonus.rolls_per_hour_bonus,
                    bonus.wishlist_slot_bonus,
                    bonus.wish_spawn_bonus_percent,
                    bonus.starwish_spawn_bonus_percent,
                    bonus.starwish_total_spawn_bonus_percent,
                    bonus.starwish_slot_bonus,
                    bonus.additional_wish_key_chance_percent,
                    bonus.kakera_max_power_percent,
                    bonus.kakera_button_power_cost_percent,
                    bonus.starwish_kakera_button_bonus_percent,
                    bonus.light_kakera_minimum,
                    bonus.light_kakera_maximum,
                    observed_at.isoformat(),
                    import_event_id,
                ),
            )
        return PlayerBonusImportResult(
            import_event_id=import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observed_at=observed_at,
        )

    def player_bonus(self, server_name: str, account_name: str) -> PlayerBonusObservation | None:
        """Return the latest player bonus snapshot for one server/account pair."""
        with self._connection() as connection:
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
                (self._normalize(server_name), self._normalize(account_name)),
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
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("wishlist", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)
            server_id = self._upsert_server(connection, server_name, observed_at)
            account_id = self._upsert_account(connection, server_id, account_name, observed_at)
            connection.execute(
                """
                INSERT INTO wishlist_observations (
                    account_context_id, wishlist_count, wishlist_capacity, starwish_count,
                    starwish_capacity, entries_json, observed_at, import_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    wishlist.wishlist_count,
                    wishlist.wishlist_capacity,
                    wishlist.starwish_count,
                    wishlist.starwish_capacity,
                    json.dumps([entry.model_dump() for entry in wishlist.entries]),
                    observed_at.isoformat(),
                    import_event_id,
                ),
            )
        return WishlistImportResult(
            import_event_id=import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observed_at=observed_at,
        )

    def wishlist(self, server_name: str, account_name: str) -> WishlistObservation | None:
        """Return the latest `$wl` snapshot for one server/account pair."""
        with self._connection() as connection:
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
                (self._normalize(server_name), self._normalize(account_name)),
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
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("disablelist", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)
            server_id = self._upsert_server(connection, server_name, observed_at)
            account_id = self._upsert_account(connection, server_id, account_name, observed_at)
            connection.execute(
                """
                INSERT INTO disablelist_observations (
                    account_context_id, slots_used, slots_capacity, total_disabled, disabled_wa,
                    disabled_ha, disabled_wg, disabled_hg, wa_pool_limit, ha_pool_limit,
                    western_disabled, irl_disabled, entries_json, observed_at, import_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    disablelist.slots_used,
                    disablelist.slots_capacity,
                    disablelist.total_disabled,
                    disablelist.disabled_wa,
                    disablelist.disabled_ha,
                    disablelist.disabled_wg,
                    disablelist.disabled_hg,
                    disablelist.wa_pool_limit,
                    disablelist.ha_pool_limit,
                    disablelist.western_disabled,
                    disablelist.irl_disabled,
                    json.dumps([entry.model_dump() for entry in disablelist.entries]),
                    observed_at.isoformat(),
                    import_event_id,
                ),
            )
        return DisableListImportResult(
            import_event_id=import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observed_at=observed_at,
        )

    def disablelist(self, server_name: str, account_name: str) -> DisableListObservation | None:
        """Return the latest `$dl` snapshot for one server/account pair."""
        with self._connection() as connection:
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
                (self._normalize(server_name), self._normalize(account_name)),
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
            western_disabled=bool(row["western_disabled"]),
            irl_disabled=bool(row["irl_disabled"]),
            entries=tuple(json.loads(row["entries_json"])),
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )

    def import_unavailable_characters(
        self,
        page: UnavailableCharacterPage,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> RollabilityImportResult:
        """Store direct Mudae evidence that characters cannot currently roll."""
        observed_at = datetime.now(timezone.utc)
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("topx_page", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)
            server_id = self._upsert_server(connection, server_name, observed_at)
            account_id = self._upsert_account(connection, server_id, account_name, observed_at)
            for character in page.characters:
                character_id = self._upsert_character(
                    connection,
                    name=character.name,
                    series=character.series,
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
                    (character_id, character.claim_rank, observed_at.isoformat(), import_event_id),
                )
                connection.execute(
                    """
                    INSERT INTO unavailable_character_observations (
                        account_context_id, character_id, reason, observed_at, import_event_id
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (account_id, character_id, character.reason, observed_at.isoformat(), import_event_id),
                )
        return RollabilityImportResult(
            import_event_id=import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            characters_imported=len(page.characters),
            observed_at=observed_at,
        )

    def unavailable_characters(
        self, server_name: str, account_name: str
    ) -> tuple[UnavailableCharacterObservation, ...]:
        """Return the latest unavailable observations for one server/account pair."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT characters.id, characters.name, characters.series, characters.gender, characters.roulette,
                       unavailable_character_observations.reason,
                       unavailable_character_observations.observed_at,
                       rank_snapshots.claim_rank
                FROM account_contexts
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                JOIN unavailable_character_observations ON unavailable_character_observations.id = (
                    SELECT observations.id FROM unavailable_character_observations AS observations
                    WHERE observations.account_context_id = account_contexts.id
                      AND observations.character_id = unavailable_character_observations.character_id
                    ORDER BY observations.id DESC LIMIT 1
                )
                JOIN characters ON characters.id = unavailable_character_observations.character_id
                JOIN rank_snapshots ON rank_snapshots.id = (
                    SELECT snapshots.id FROM rank_snapshots AS snapshots
                    WHERE snapshots.character_id = characters.id
                    ORDER BY snapshots.id DESC LIMIT 1
                )
                WHERE server_contexts.normalized_name = ?
                  AND account_contexts.normalized_name = ?
                ORDER BY rank_snapshots.claim_rank ASC
                """,
                (self._normalize(server_name), self._normalize(account_name)),
            ).fetchall()
        return tuple(
            UnavailableCharacterObservation(
                character=CatalogCharacter(
                    id=row["id"],
                    name=row["name"],
                    series=row["series"],
                    gender=row["gender"],
                    roulette=row["roulette"],
                ),
                claim_rank=row["claim_rank"],
                reason=row["reason"],
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
                "DELETE FROM harem_key_observations WHERE import_event_id = ?",
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

                CREATE TABLE IF NOT EXISTS account_contexts (
                    id INTEGER PRIMARY KEY,
                    server_context_id INTEGER NOT NULL REFERENCES server_contexts(id),
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(server_context_id, normalized_name)
                );

                CREATE TABLE IF NOT EXISTS harem_key_observations (
                    id INTEGER PRIMARY KEY,
                    account_context_id INTEGER NOT NULL REFERENCES account_contexts(id),
                    character_id INTEGER REFERENCES characters(id),
                    character_name TEXT NOT NULL,
                    normalized_character_name TEXT NOT NULL,
                    key_type TEXT NOT NULL,
                    key_count INTEGER NOT NULL,
                    kakera_value INTEGER,
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id)
                );

                CREATE TABLE IF NOT EXISTS harem_scans (
                    id INTEGER PRIMARY KEY,
                    account_context_id INTEGER NOT NULL REFERENCES account_contexts(id),
                    expected_page_count INTEGER,
                    started_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS harem_scan_pages (
                    harem_scan_id INTEGER NOT NULL REFERENCES harem_scans(id),
                    page_number INTEGER NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id),
                    PRIMARY KEY (harem_scan_id, page_number)
                );

                CREATE TABLE IF NOT EXISTS player_bonus_observations (
                    id INTEGER PRIMARY KEY,
                    account_context_id INTEGER NOT NULL REFERENCES account_contexts(id),
                    metrics_json TEXT NOT NULL,
                    rolls_per_hour_bonus INTEGER,
                    wishlist_slot_bonus INTEGER,
                    wish_spawn_bonus_percent INTEGER,
                    starwish_spawn_bonus_percent INTEGER,
                    starwish_total_spawn_bonus_percent INTEGER,
                    starwish_slot_bonus INTEGER,
                    additional_wish_key_chance_percent INTEGER,
                    kakera_max_power_percent INTEGER,
                    kakera_button_power_cost_percent INTEGER,
                    starwish_kakera_button_bonus_percent INTEGER,
                    light_kakera_minimum INTEGER,
                    light_kakera_maximum INTEGER,
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id)
                );

                CREATE TABLE IF NOT EXISTS wishlist_observations (
                    id INTEGER PRIMARY KEY,
                    account_context_id INTEGER NOT NULL REFERENCES account_contexts(id),
                    wishlist_count INTEGER NOT NULL,
                    wishlist_capacity INTEGER NOT NULL,
                    starwish_count INTEGER NOT NULL,
                    starwish_capacity INTEGER NOT NULL,
                    entries_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id)
                );

                CREATE TABLE IF NOT EXISTS disablelist_observations (
                    id INTEGER PRIMARY KEY,
                    account_context_id INTEGER NOT NULL REFERENCES account_contexts(id),
                    slots_used INTEGER NOT NULL,
                    slots_capacity INTEGER NOT NULL,
                    total_disabled INTEGER NOT NULL,
                    disabled_wa INTEGER NOT NULL,
                    disabled_ha INTEGER NOT NULL,
                    disabled_wg INTEGER NOT NULL,
                    disabled_hg INTEGER NOT NULL,
                    wa_pool_limit INTEGER,
                    ha_pool_limit INTEGER,
                    western_disabled INTEGER NOT NULL,
                    irl_disabled INTEGER NOT NULL,
                    entries_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id)
                );

                CREATE TABLE IF NOT EXISTS unavailable_character_observations (
                    id INTEGER PRIMARY KEY,
                    account_context_id INTEGER NOT NULL REFERENCES account_contexts(id),
                    character_id INTEGER NOT NULL REFERENCES characters(id),
                    reason TEXT,
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id)
                );
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(harem_key_observations)").fetchall()
            }
            if "kakera_value" not in columns:
                connection.execute("ALTER TABLE harem_key_observations ADD COLUMN kakera_value INTEGER")
            if "harem_scan_id" not in columns:
                connection.execute(
                    "ALTER TABLE harem_key_observations ADD COLUMN harem_scan_id INTEGER "
                    "REFERENCES harem_scans(id)"
                )

    def _prepare_harem_scan_page(
        self,
        connection: sqlite3.Connection,
        scan_id: int,
        account_id: int,
        page: HaremKeyPage,
    ) -> None:
        if page.page_number is None or page.page_count is None:
            raise ValueError("A scanned harem page must include its Page X / Y indicator.")
        scan = connection.execute(
            "SELECT account_context_id, expected_page_count, completed_at FROM harem_scans WHERE id = ?",
            (scan_id,),
        ).fetchone()
        if scan is None:
            raise ValueError("Harem scan not found.")
        if scan["account_context_id"] != account_id:
            raise ValueError("Harem scan belongs to a different server or account.")
        if scan["completed_at"] is not None:
            raise ValueError("Harem scan is already complete; begin a new scan to refresh it.")
        if scan["expected_page_count"] not in (None, page.page_count):
            raise ValueError("Harem page count does not match the scan's first imported page.")
        duplicate = connection.execute(
            "SELECT 1 FROM harem_scan_pages WHERE harem_scan_id = ? AND page_number = ?",
            (scan_id, page.page_number),
        ).fetchone()
        if duplicate is not None:
            raise ValueError("This harem scan already contains that page.")
        connection.execute(
            "UPDATE harem_scans SET expected_page_count = ? WHERE id = ?",
            (page.page_count, scan_id),
        )

    def _active_harem_scan_id(
        self, connection: sqlite3.Connection, server_name: str, account_name: str
    ) -> int | None:
        row = connection.execute(
            """
            SELECT harem_scans.id
            FROM harem_scans
            JOIN account_contexts ON account_contexts.id = harem_scans.account_context_id
            JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
            WHERE server_contexts.normalized_name = ?
              AND account_contexts.normalized_name = ?
              AND harem_scans.completed_at IS NOT NULL
            ORDER BY harem_scans.completed_at DESC
            LIMIT 1
            """,
            (self._normalize(server_name), self._normalize(account_name)),
        ).fetchone()
        return int(row["id"]) if row is not None else None

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

    def _upsert_server(
        self, connection: sqlite3.Connection, server_name: str, observed_at: datetime
    ) -> int:
        normalized_name = self._normalize(server_name)
        connection.execute(
            """
            INSERT INTO server_contexts (name, normalized_name, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(normalized_name) DO UPDATE SET
                name = excluded.name,
                updated_at = excluded.updated_at
            """,
            (server_name.strip(), normalized_name, observed_at.isoformat(), observed_at.isoformat()),
        )
        return connection.execute(
            "SELECT id FROM server_contexts WHERE normalized_name = ?",
            (normalized_name,),
        ).fetchone()["id"]

    def _upsert_account(
        self,
        connection: sqlite3.Connection,
        server_id: int,
        account_name: str,
        observed_at: datetime,
    ) -> int:
        normalized_name = self._normalize(account_name)
        connection.execute(
            """
            INSERT INTO account_contexts (
                server_context_id, name, normalized_name, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(server_context_id, normalized_name) DO UPDATE SET
                name = excluded.name,
                updated_at = excluded.updated_at
            """,
            (
                server_id,
                account_name.strip(),
                normalized_name,
                observed_at.isoformat(),
                observed_at.isoformat(),
            ),
        )
        return connection.execute(
            """
            SELECT id FROM account_contexts
            WHERE server_context_id = ? AND normalized_name = ?
            """,
            (server_id, normalized_name),
        ).fetchone()["id"]

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.casefold().split())
