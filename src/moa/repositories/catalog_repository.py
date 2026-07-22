"""SQLite-backed repository for MOA's imported character catalog."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from moa.database.sqlite import connect
from moa.database.migrations import (
    CATALOG_MIGRATIONS,
    run_migrations,
    validate_catalog_schema,
)
from moa.models.catalog import (
    CatalogCharacter,
    CatalogRankSnapshot,
    CharacterDetailsImportResult,
    ClaimImportResult,
    ClaimObservation,
    DivorceImportResult,
    CharacterProfile,
    HaremKeyImportResult,
    HaremKeyObservation,
    HaremScanProgress,
    ImportEventSummary,
    RankedCatalogCharacter,
    RankedHaremImportResult,
    OwnedCharacterObservation,
    ServerKakeraObservation,
    PlayerBonusImportResult,
    PlayerBonusObservation,
    DisableListImportResult,
    DisableListObservation,
    RollabilityImportResult,
    UnavailableCharacterObservation,
    RollImportResult,
    KakeraReactionImportResult,
    KakeraReactionObservation,
    KakeraReactionSummary,
    RollStatistics,
    StoredRollObservation,
    KakeraStateImportResult,
    KakeraStateObservation,
    KakeraProgressPoint,
    PersonalRareImportResult,
    PersonalRareObservation,
    ServerSettingsImportResult,
    ServerSettingsObservation,
    KakeralootStateImportResult,
    KakeralootStateObservation,
    KakeralootSettingsImportResult,
    KakeralootSettingsObservation,
    ProfileImportResult,
    ProfileObservation,
    MudapinImportResult,
    MudapinObservation,
    TowerStateImportResult,
    TowerStateObservation,
    TimerStateImportResult,
    TimerStateObservation,
    SphereResultImportResult,
    SphereResultObservation,
    WishlistImportResult,
    WishlistObservation,
    AntidisableImportResult,
    TopImportResult,
    TopOwnerObservation,
)
from moa.models.character import (
    CharacterDetails,
    ClaimConfirmation,
    DivorceConfirmation,
    AntidisablePage,
    DisableListSnapshot,
    HaremKeyPage,
    RankedHaremPage,
    KakeraStateSnapshot,
    KakeralootStateSnapshot,
    KakeralootSettingsSnapshot,
    ProfileSnapshot,
    MudapinSnapshot,
    PersonalRareSnapshot,
    ServerSettingsSnapshot,
    TowerStateSnapshot,
    TimerStateSnapshot,
    PlayerBonusSnapshot,
    TopPage,
    RollObservation,
    KakeraReactionReceipt,
    SphereResultSnapshot,
    UnavailableCharacterPage,
    WishlistSnapshot,
)
from moa.parser.mudae import MudaeParseError, MudaeTextParser


class CatalogRepositoryProtocol(Protocol):
    """Storage contract required by :class:`CatalogService`."""

    def import_command_observation(
        self, command_name: str, raw_message: str, source: str
    ) -> None:
        """Persist a raw response for a command without a typed state importer."""
        ...

    def import_top_page(
        self,
        page: TopPage,
        raw_message: str,
        source: str,
        server_name: str | None = None,
    ) -> TopImportResult: ...

    def import_character_details(
        self,
        details: CharacterDetails,
        server_name: str,
        raw_message: str,
        source: str,
        account_name: str | None = None,
    ) -> CharacterDetailsImportResult: ...

    def top(self, limit: int | None) -> tuple[RankedCatalogCharacter, ...]: ...

    def top_owner_observations(
        self, server_name: str
    ) -> tuple[TopOwnerObservation, ...]: ...

    def character_count(self) -> int: ...

    def get_profile(self, name: str, series: str) -> CharacterProfile | None: ...

    def server_kakera_values(self, server_name: str) -> dict[int, int | None]: ...

    def rank_history(
        self, name: str, series: str, limit: int
    ) -> tuple[CatalogRankSnapshot, ...]: ...

    def import_roll(
        self,
        roll: RollObservation,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> RollImportResult: ...

    def import_claim(
        self,
        claim: ClaimConfirmation,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> ClaimImportResult: ...

    def claim_observations(
        self, server_name: str, account_name: str
    ) -> tuple[ClaimObservation, ...]: ...

    def import_divorce(
        self,
        divorce: DivorceConfirmation,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> DivorceImportResult: ...

    def recent_rolls(
        self, server_name: str, account_name: str, limit: int
    ) -> tuple[StoredRollObservation, ...]: ...

    def roll_statistics(self, server_name: str, account_name: str) -> RollStatistics: ...

    def import_kakera_reaction(
        self, receipt: KakeraReactionReceipt, server_name: str, raw_message: str, source: str
    ) -> KakeraReactionImportResult: ...

    def kakera_reactions(self, server_name: str, account_name: str, limit: int) -> tuple[KakeraReactionObservation, ...]: ...

    def kakera_reaction_summary(self, server_name: str, account_name: str) -> KakeraReactionSummary: ...

    def recent_imports(self, limit: int) -> tuple[ImportEventSummary, ...]: ...

    def delete_import_event(self, import_event_id: int) -> bool: ...

    def inspect_bugged_imports(self) -> tuple[int, int]: ...

    def repair_bugged_imports(self) -> tuple[int, int]: ...

    def import_harem_key_page(
        self,
        page: HaremKeyPage,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
        scan_id: int | None = None,
    ) -> HaremKeyImportResult: ...

    def import_ranked_harem_page(
        self,
        page: RankedHaremPage,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
        scan_id: int | None = None,
    ) -> RankedHaremImportResult: ...

    def owned_characters(
        self, server_name: str, account_name: str
    ) -> tuple[OwnedCharacterObservation, ...]: ...

    def harem_keys(self, server_name: str, account_name: str) -> tuple[HaremKeyObservation, ...]: ...

    def recent_key_gains(
        self, server_name: str, account_name: str, limit: int
    ) -> tuple[HaremKeyObservation, ...]: ...

    def begin_harem_scan(
        self, server_name: str, account_name: str, scan_kind: str = "keys"
    ) -> HaremScanProgress: ...

    def harem_scan_progress(self, scan_id: int) -> HaremScanProgress | None: ...

    def complete_harem_scan(self, scan_id: int) -> HaremScanProgress: ...

    def has_complete_harem_scan(
        self, server_name: str, account_name: str, scan_kind: str = "keys"
    ) -> bool: ...

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

    def import_antidisable_page(
        self,
        page: AntidisablePage,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
        scan_id: int | None = None,
    ) -> AntidisableImportResult: ...

    def begin_antidisable_scan(
        self, server_name: str, account_name: str
    ) -> HaremScanProgress: ...

    def antidisable_series(
        self, server_name: str, account_name: str
    ) -> tuple[str, ...]: ...

    def complete_antidisable_scan(self, scan_id: int) -> HaremScanProgress: ...

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

    def import_kakera_state(
        self,
        state: KakeraStateSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> KakeraStateImportResult: ...

    def kakera_state(self, server_name: str, account_name: str) -> KakeraStateObservation | None: ...

    def kakera_history(
        self, server_name: str, account_name: str
    ) -> tuple[KakeraProgressPoint, ...]: ...

    def import_personal_rare(
        self,
        state: PersonalRareSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> PersonalRareImportResult: ...

    def personal_rare(
        self, server_name: str, account_name: str
    ) -> PersonalRareObservation | None: ...

    def import_tower_state(
        self,
        state: TowerStateSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> TowerStateImportResult: ...

    def tower_state(self, server_name: str, account_name: str) -> TowerStateObservation | None: ...

    def import_timer_state(
        self,
        state: TimerStateSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> TimerStateImportResult: ...

    def timer_state(self, server_name: str, account_name: str) -> TimerStateObservation | None: ...

    def import_sphere_result(
        self,
        state: SphereResultSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> SphereResultImportResult: ...

    def sphere_result(self, server_name: str, account_name: str) -> SphereResultObservation | None: ...

    def import_kakeraloot_state(
        self,
        state: KakeralootStateSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> KakeralootStateImportResult: ...

    def kakeraloot_state(
        self, server_name: str, account_name: str
    ) -> KakeralootStateObservation | None: ...

    def import_kakeraloot_settings(
        self,
        settings: KakeralootSettingsSnapshot,
        server_name: str,
        raw_message: str,
        source: str,
    ) -> KakeralootSettingsImportResult: ...

    def kakeraloot_settings(self, server_name: str) -> KakeralootSettingsObservation | None: ...

    def import_profile(
        self,
        snapshot: ProfileSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> ProfileImportResult: ...

    def profile(self, server_name: str, account_name: str) -> ProfileObservation | None: ...

    def import_mudapins(
        self,
        snapshot: MudapinSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> MudapinImportResult: ...

    def mudapins(self, server_name: str, account_name: str) -> MudapinObservation | None: ...

    def import_server_settings(
        self,
        settings: ServerSettingsSnapshot,
        server_name: str,
        raw_message: str,
        source: str,
    ) -> ServerSettingsImportResult: ...

    def server_settings(self, server_name: str) -> ServerSettingsObservation | None: ...


@dataclass(frozen=True, slots=True)
class _RollImportConnectionResult:
    """Rows created by one roll import on a caller-owned connection."""

    import_event_id: int
    character_id: int
    roll_observation_id: int
    harem_key_observation_id: int | None
    rank_snapshot_id: int | None
    server_character_observation_id: int | None


class CatalogRepository:
    """Persist imported Mudae character and rank observations in SQLite."""

    def __init__(self, database_path: Path | None = None) -> None:
        self._database_path = database_path
        self._initialize()

    def import_command_observation(
        self, command_name: str, raw_message: str, source: str
    ) -> None:
        """Persist an audited response for a supported state-changing command."""
        normalized_command = command_name.strip().casefold().lstrip("$/") or "unknown"
        observed_at = datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO import_events (kind, source, observed_at, raw_message)
                VALUES (?, ?, ?, ?)
                """,
                (
                    "command_observation",
                    f"{source}:command=${normalized_command}",
                    observed_at.isoformat(),
                    raw_message,
                ),
            )

    def import_sphere_result(
        self,
        state: SphereResultSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> SphereResultImportResult:
        """Store one account-scoped `$oq` sphere payout."""
        observed_at = datetime.now(timezone.utc)
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("sphere_result", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)
            server_id = self._upsert_server(connection, server_name, observed_at)
            account_id = self._upsert_account(connection, server_id, account_name, observed_at)
            connection.execute(
                """
                INSERT INTO sphere_result_observations (
                    account_context_id, snapshot_json, total_gained, stock,
                    observed_at, import_event_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    json.dumps(state.model_dump()),
                    state.total_gained,
                    state.stock,
                    observed_at.isoformat(),
                    import_event_id,
                ),
            )
        return SphereResultImportResult(
            import_event_id=import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observed_at=observed_at,
        )

    def sphere_result(self, server_name: str, account_name: str) -> SphereResultObservation | None:
        """Return the newest imported `$oq` result for one account."""
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT sphere_result_observations.*, server_contexts.name AS server_name,
                       account_contexts.name AS account_name
                FROM account_contexts
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                JOIN sphere_result_observations ON sphere_result_observations.id = (
                    SELECT observations.id FROM sphere_result_observations AS observations
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
        return SphereResultObservation(
            server_name=row["server_name"],
            account_name=row["account_name"],
            snapshot=SphereResultSnapshot.model_validate(json.loads(row["snapshot_json"])),
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )

    def import_top_page(
        self,
        page: TopPage,
        raw_message: str,
        source: str,
        server_name: str | None = None,
    ) -> TopImportResult:
        """Upsert characters and append one rank snapshot per imported row."""
        if any(character.owner_name for character in page.characters) and (
            not server_name or not server_name.strip()
        ):
            raise ValueError("A `$topo` import with owner claims requires --server.")

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
            server_id = (
                self._upsert_server(connection, server_name, observed_at)
                if server_name and server_name.strip()
                else None
            )

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
                        character_id, claim_rank, like_rank, owner_name, observed_at, import_event_id
                    ) VALUES (?, ?, NULL, ?, ?, ?)
                    """,
                    (
                        character_id,
                        ranked_character.claim_rank,
                        ranked_character.owner_name,
                        observed_at.isoformat(),
                        import_event_id,
                    ),
                )
                if server_id is not None:
                    connection.execute(
                        """
                        INSERT INTO top_owner_observations (
                            server_context_id, character_id, owner_name, observed_at, import_event_id
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            server_id,
                            character_id,
                            ranked_character.owner_name,
                            observed_at.isoformat(),
                            import_event_id,
                        ),
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
        account_name: str | None = None,
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
            if account_name and details.key_type is not None and details.key_count is not None:
                account_id = self._upsert_account(connection, server_id, account_name, observed_at)
                connection.execute(
                    """
                    INSERT INTO harem_key_observations (
                        account_context_id, character_id, character_name, normalized_character_name,
                        key_type, key_count, kakera_value, observed_at, import_event_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        character_id,
                        details.name,
                        self._normalize(details.name),
                        details.key_type,
                        details.key_count,
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

    def import_roll(
        self,
        roll: RollObservation,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> RollImportResult:
        """Store one roll and preserve any directly displayed rank/value observations."""
        observed_at = datetime.now(timezone.utc)
        with self._connection() as connection:
            imported = self._import_roll_with_connection(
                connection,
                roll=roll,
                server=server_name,
                account=account_name,
                raw=raw_message,
                source=source,
                observed_at=observed_at,
            )
        return RollImportResult(
            import_event_id=imported.import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            character_id=imported.character_id,
            observed_at=observed_at,
        )

    def _import_roll_with_connection(
        self,
        connection: sqlite3.Connection,
        *,
        roll: RollObservation,
        server: str,
        account: str,
        raw: str,
        source: str,
        observed_at: datetime,
    ) -> _RollImportConnectionResult:
        """Store one roll without taking ownership of the surrounding transaction."""
        cursor = connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
            ("roll", source, observed_at.isoformat(), raw),
        )
        import_event_id = int(cursor.lastrowid)
        character_id = int(
            self._upsert_character(
                connection,
                name=roll.name,
                series=roll.series,
                gender=None,
                roulette=None,
                observed_at=observed_at,
            ).fetchone()["id"]
        )
        server_id = self._upsert_server(connection, server, observed_at)
        account_id = self._upsert_account(connection, server_id, account, observed_at)
        roll_observation_id = int(
            connection.execute(
                """
                INSERT INTO roll_observations (
                    account_context_id, character_id, claim_rank, kakera_value, observed_at, import_event_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    character_id,
                    roll.claim_rank,
                    roll.kakera_value,
                    observed_at.isoformat(),
                    import_event_id,
                ),
            ).lastrowid
        )
        harem_key_observation_id = None
        if roll.displayed_key_count is not None and roll.displayed_key_type is not None:
            harem_key_observation_id = int(
                connection.execute(
                    """
                    INSERT INTO harem_key_observations (
                        account_context_id, character_id, character_name, normalized_character_name,
                        key_type, key_count, kakera_value, observed_at, import_event_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        character_id,
                        roll.name,
                        self._normalize(roll.name),
                        roll.displayed_key_type,
                        roll.displayed_key_count,
                        roll.kakera_value,
                        observed_at.isoformat(),
                        import_event_id,
                    ),
                ).lastrowid
            )
        rank_snapshot_id = None
        if roll.claim_rank is not None:
            rank_snapshot_id = int(
                connection.execute(
                    """
                    INSERT INTO rank_snapshots (
                        character_id, claim_rank, like_rank, observed_at, import_event_id
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (character_id, roll.claim_rank, None, observed_at.isoformat(), import_event_id),
                ).lastrowid
            )
        server_character_observation_id = None
        if roll.kakera_value is not None:
            server_character_observation_id = int(
                connection.execute(
                    """
                    INSERT INTO server_character_observations (
                        server_context_id, character_id, kakera_value, observed_at, import_event_id
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (server_id, character_id, roll.kakera_value, observed_at.isoformat(), import_event_id),
                ).lastrowid
            )
        return _RollImportConnectionResult(
            import_event_id=import_event_id,
            character_id=character_id,
            roll_observation_id=roll_observation_id,
            harem_key_observation_id=harem_key_observation_id,
            rank_snapshot_id=rank_snapshot_id,
            server_character_observation_id=server_character_observation_id,
        )

    def import_claim(
        self,
        claim: ClaimConfirmation,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> ClaimImportResult:
        """Store claim evidence without inventing a character series."""
        observed_at = datetime.now(timezone.utc)
        normalized_name = self._normalize(claim.character_name)
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("claim", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)
            server_id = self._upsert_server(connection, server_name, observed_at)
            account_id = self._upsert_account(connection, server_id, account_name, observed_at)

            character_row = connection.execute(
                """
                SELECT characters.id
                FROM roll_observations
                JOIN characters ON characters.id = roll_observations.character_id
                WHERE roll_observations.account_context_id = ?
                  AND characters.normalized_name = ?
                ORDER BY roll_observations.id DESC
                LIMIT 1
                """,
                (account_id, normalized_name),
            ).fetchone()
            character_id = int(character_row["id"]) if character_row is not None else None
            if character_id is None:
                candidates = connection.execute(
                    "SELECT id FROM characters WHERE normalized_name = ?",
                    (normalized_name,),
                ).fetchall()
                if len(candidates) == 1:
                    character_id = int(candidates[0]["id"])

            connection.execute(
                """
                INSERT INTO claim_observations (
                    account_context_id, character_id, character_name,
                    normalized_character_name, observed_at, import_event_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    character_id,
                    claim.character_name,
                    normalized_name,
                    observed_at.isoformat(),
                    import_event_id,
                ),
            )
        return ClaimImportResult(
            import_event_id=import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            character_name=claim.character_name,
            character_id=character_id,
            observed_at=observed_at,
        )

    def import_divorce(
        self,
        divorce: DivorceConfirmation,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> DivorceImportResult:
        """Store a divorce tombstone so older claim/harem evidence is no longer current."""
        observed_at = datetime.now(timezone.utc)
        normalized_name = self._normalize(divorce.character_name)
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("divorce", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)
            server_id = self._upsert_server(connection, server_name, observed_at)
            account_id = self._upsert_account(connection, server_id, account_name, observed_at)

            character_id: int | None = None
            for table in (
                "claim_observations",
                "owned_character_observations",
                "harem_key_observations",
            ):
                row = connection.execute(
                    f"""
                    SELECT character_id
                    FROM {table}
                    WHERE account_context_id = ?
                      AND normalized_character_name = ?
                      AND character_id IS NOT NULL
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (account_id, normalized_name),
                ).fetchone()
                if row is not None:
                    character_id = int(row["character_id"])
                    break
            if character_id is None:
                row = connection.execute(
                    """
                    SELECT observations.character_id
                    FROM roll_observations AS observations
                    JOIN characters ON characters.id = observations.character_id
                    WHERE observations.account_context_id = ?
                      AND characters.normalized_name = ?
                    ORDER BY observations.id DESC
                    LIMIT 1
                    """,
                    (account_id, normalized_name),
                ).fetchone()
                if row is not None:
                    character_id = int(row["character_id"])
            if character_id is None:
                candidates = connection.execute(
                    "SELECT id FROM characters WHERE normalized_name = ?",
                    (normalized_name,),
                ).fetchall()
                if len(candidates) == 1:
                    character_id = int(candidates[0]["id"])

            connection.execute(
                """
                INSERT INTO divorce_observations (
                    account_context_id, character_id, character_name,
                    normalized_character_name, kakera_refund, observed_at, import_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    character_id,
                    divorce.character_name,
                    normalized_name,
                    divorce.kakera_refund,
                    observed_at.isoformat(),
                    import_event_id,
                ),
            )
        return DivorceImportResult(
            import_event_id=import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            character_name=divorce.character_name,
            character_id=character_id,
            kakera_refund=divorce.kakera_refund,
            observed_at=observed_at,
        )

    def claim_observations(
        self, server_name: str, account_name: str
    ) -> tuple[ClaimObservation, ...]:
        """Return the latest claim evidence per character for one account."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT observations.character_name, observations.observed_at,
                       characters.id AS character_id, characters.name,
                       characters.series, characters.gender, characters.roulette
                FROM claim_observations AS observations
                JOIN account_contexts ON account_contexts.id = observations.account_context_id
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
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
                  AND NOT EXISTS (
                      SELECT 1
                      FROM divorce_observations AS divorces
                      WHERE divorces.account_context_id = observations.account_context_id
                        AND divorces.normalized_character_name = observations.normalized_character_name
                        AND divorces.import_event_id > observations.import_event_id
                  )
                  AND observations.id = (
                      SELECT latest.id
                      FROM claim_observations AS latest
                      WHERE latest.account_context_id = observations.account_context_id
                        AND latest.normalized_character_name = observations.normalized_character_name
                      ORDER BY latest.id DESC
                      LIMIT 1
                  )
                ORDER BY observations.id DESC
                """,
                (self._normalize(server_name), self._normalize(account_name)),
            ).fetchall()
        return tuple(
            ClaimObservation(
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
                observed_at=datetime.fromisoformat(row["observed_at"]),
            )
            for row in rows
        )

    def recent_rolls(
        self, server_name: str, account_name: str, limit: int
    ) -> tuple[StoredRollObservation, ...]:
        """Return recent observed rolls, newest first."""
        if limit <= 0:
            raise ValueError("Roll limit must be positive.")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT roll_observations.*, characters.id AS character_id, characters.name,
                       characters.series, characters.gender, characters.roulette
                FROM roll_observations
                JOIN account_contexts ON account_contexts.id = roll_observations.account_context_id
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                JOIN characters ON characters.id = roll_observations.character_id
                WHERE server_contexts.normalized_name = ?
                  AND account_contexts.normalized_name = ?
                ORDER BY roll_observations.observed_at DESC, roll_observations.id DESC
                LIMIT ?
                """,
                (self._normalize(server_name), self._normalize(account_name), limit),
            ).fetchall()
        return tuple(
            StoredRollObservation(
                character=CatalogCharacter(
                    id=row["character_id"],
                    name=row["name"],
                    series=row["series"],
                    gender=row["gender"],
                    roulette=row["roulette"],
                ),
                claim_rank=row["claim_rank"],
                kakera_value=row["kakera_value"],
                observed_at=datetime.fromisoformat(row["observed_at"]),
            )
            for row in rows
        )

    def roll_statistics(self, server_name: str, account_name: str) -> RollStatistics:
        """Return descriptive statistics for imported rolls in one account context."""
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS roll_count,
                    MIN(claim_rank) AS best_claim_rank,
                    AVG(claim_rank) AS average_claim_rank,
                    AVG(kakera_value) AS average_kakera_value,
                    MAX(kakera_value) AS highest_kakera_value
                FROM roll_observations
                JOIN account_contexts ON account_contexts.id = roll_observations.account_context_id
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                WHERE server_contexts.normalized_name = ?
                  AND account_contexts.normalized_name = ?
                """,
                (self._normalize(server_name), self._normalize(account_name)),
            ).fetchone()
        return RollStatistics(
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            roll_count=row["roll_count"],
            best_claim_rank=row["best_claim_rank"],
            average_claim_rank=row["average_claim_rank"],
            average_kakera_value=row["average_kakera_value"],
            highest_kakera_value=row["highest_kakera_value"],
        )

    def import_kakera_reaction(self, receipt, server_name, raw_message, source):
        observed_at = datetime.now(timezone.utc)
        with self._connection() as connection:
            event_id = connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("kakera_reaction", source, observed_at.isoformat(), raw_message),
            ).lastrowid
            server_id = self._upsert_server(connection, server_name, observed_at)
            account_id = self._upsert_account(connection, server_id, receipt.account_name, observed_at)
            connection.execute(
                "INSERT INTO kakera_reaction_observations (account_context_id, reaction_label, kakera_earned, observed_at, import_event_id) VALUES (?, ?, ?, ?, ?)",
                (account_id, receipt.reaction_label, receipt.kakera_earned, observed_at.isoformat(), event_id),
            )
        return KakeraReactionImportResult(import_event_id=event_id, server_name=server_name.strip(), account_name=receipt.account_name, observed_at=observed_at)

    def kakera_reactions(self, server_name, account_name, limit):
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT reaction_label, kakera_earned, observed_at FROM kakera_reaction_observations JOIN account_contexts ON account_contexts.id = kakera_reaction_observations.account_context_id JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id WHERE server_contexts.normalized_name = ? AND account_contexts.normalized_name = ? ORDER BY kakera_reaction_observations.id DESC LIMIT ?",
                (self._normalize(server_name), self._normalize(account_name), limit),
            ).fetchall()
        return tuple(KakeraReactionObservation(reaction_label=row["reaction_label"], kakera_earned=row["kakera_earned"], observed_at=datetime.fromisoformat(row["observed_at"])) for row in rows)

    def kakera_reaction_summary(self, server_name, account_name):
        query = " FROM kakera_reaction_observations JOIN account_contexts ON account_contexts.id = kakera_reaction_observations.account_context_id JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id WHERE server_contexts.normalized_name = ? AND account_contexts.normalized_name = ?"
        params = (self._normalize(server_name), self._normalize(account_name))
        with self._connection() as connection:
            totals = connection.execute("SELECT COUNT(*) AS count, COALESCE(SUM(kakera_earned), 0) AS total, AVG(kakera_earned) AS average, MAX(kakera_earned) AS highest" + query, params).fetchone()
            rows = connection.execute("SELECT reaction_label, COUNT(*) AS count, SUM(kakera_earned) AS total" + query + " GROUP BY reaction_label ORDER BY total DESC", params).fetchall()
        return KakeraReactionSummary(receipt_count=totals["count"], total_kakera_earned=totals["total"], average_kakera_earned=totals["average"], highest_kakera_earned=totals["highest"], by_reaction=tuple((row["reaction_label"], row["count"], row["total"]) for row in rows))

    def top(self, limit: int | None) -> tuple[RankedCatalogCharacter, ...]:
        """Return characters ordered by their most recently imported claim rank."""
        if limit is not None and limit <= 0:
            raise ValueError("Catalog limit must be positive.")

        limit_clause = "LIMIT ?" if limit is not None else ""
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    characters.id,
                    characters.name,
                    characters.series,
                    characters.gender,
                    characters.roulette,
                    rank_snapshots.claim_rank,
                    rank_snapshots.like_rank,
                    rank_snapshots.owner_name,
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
                {limit_clause}
                """,
                (limit,) if limit is not None else (),
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
                owner_name=row["owner_name"],
            )
            for row in rows
        )

    def top_owner_observations(
        self, server_name: str
    ) -> tuple[TopOwnerObservation, ...]:
        """Return the latest `$topo` owner claim for each character in one server."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    characters.id,
                    characters.name,
                    characters.series,
                    characters.gender,
                    characters.roulette,
                    top_owner_observations.owner_name,
                    top_owner_observations.observed_at
                FROM server_contexts
                JOIN top_owner_observations
                  ON top_owner_observations.server_context_id = server_contexts.id
                JOIN characters ON characters.id = top_owner_observations.character_id
                WHERE server_contexts.normalized_name = ?
                  AND top_owner_observations.id = (
                    SELECT observations.id
                    FROM top_owner_observations AS observations
                    WHERE observations.server_context_id = server_contexts.id
                      AND observations.character_id = top_owner_observations.character_id
                    ORDER BY observations.id DESC
                    LIMIT 1
                )
                ORDER BY characters.name COLLATE NOCASE ASC
                """,
                (self._normalize(server_name),),
            ).fetchall()

        return tuple(
            TopOwnerObservation(
                character=CatalogCharacter(
                    id=row["id"],
                    name=row["name"],
                    series=row["series"],
                    gender=row["gender"],
                    roulette=row["roulette"],
                ),
                owner_name=row["owner_name"],
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

    def server_kakera_values(self, server_name: str) -> dict[int, int | None]:
        """Return the newest server-scoped Kakera value for each character."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT observations.character_id, observations.kakera_value
                FROM server_character_observations AS observations
                JOIN server_contexts ON server_contexts.id = observations.server_context_id
                WHERE server_contexts.normalized_name = ?
                  AND observations.id = (
                      SELECT latest.id
                      FROM server_character_observations AS latest
                      WHERE latest.server_context_id = observations.server_context_id
                        AND latest.character_id = observations.character_id
                      ORDER BY latest.id DESC
                      LIMIT 1
                  )
                """,
                (self._normalize(server_name),),
            ).fetchall()
        return {int(row["character_id"]): row["kakera_value"] for row in rows}

    def rank_history(
        self, name: str, series: str, limit: int
    ) -> tuple[CatalogRankSnapshot, ...]:
        """Return direct global rank observations for one canonical character, newest first."""
        if limit <= 0:
            raise ValueError("Rank-history limit must be positive.")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT rank_snapshots.character_id, rank_snapshots.claim_rank, rank_snapshots.like_rank,
                       rank_snapshots.owner_name, rank_snapshots.observed_at,
                       rank_snapshots.import_event_id
                FROM rank_snapshots
                JOIN characters ON characters.id = rank_snapshots.character_id
                WHERE characters.normalized_name = ? AND characters.normalized_series = ?
                ORDER BY rank_snapshots.observed_at DESC, rank_snapshots.id DESC
                LIMIT ?
                """,
                (self._normalize(name), self._normalize(series), limit),
            ).fetchall()
        return tuple(
            CatalogRankSnapshot(
                character_id=row["character_id"],
                claim_rank=row["claim_rank"],
                like_rank=row["like_rank"],
                observed_at=datetime.fromisoformat(row["observed_at"]),
                import_event_id=row["import_event_id"],
                owner_name=row["owner_name"],
            )
            for row in rows
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
                self._prepare_harem_scan_page(connection, scan_id, account_id, page, "keys")
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
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("ranked_harem_page", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)
            server_id = self._upsert_server(connection, server_name, observed_at)
            account_id = self._upsert_account(connection, server_id, account_name, observed_at)
            if scan_id is not None:
                self._prepare_harem_scan_page(connection, scan_id, account_id, page, "owned")
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
                    INSERT INTO owned_character_observations (
                        account_context_id, character_id, character_name, normalized_character_name,
                        claim_rank, kakera_value, roulette_types_json, observed_at,
                        import_event_id, harem_scan_id
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
                    "INSERT INTO harem_scan_pages (harem_scan_id, page_number, import_event_id) "
                    "VALUES (?, ?, ?)",
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
                (active_scan_id, self._normalize(server_name), self._normalize(account_name), active_scan_id)
                if active_scan_id
                else (self._normalize(server_name), self._normalize(account_name))
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
                        AND latest.normalized_character_name = observations.normalized_character_name
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
                        AND divorces.normalized_character_name = observations.normalized_character_name
                        AND divorces.import_event_id > observations.import_event_id
                  )
                ORDER BY observations.claim_rank ASC, observations.character_name COLLATE NOCASE
                """,
                params,
            ).fetchall()
        return tuple(
            OwnedCharacterObservation(
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
                claim_rank=row["claim_rank"],
                kakera_value=row["kakera_value"],
                roulette_types=tuple(json.loads(row["roulette_types_json"] or "[]")),
                observed_at=datetime.fromisoformat(row["observed_at"]),
            )
            for row in rows
        )

    def harem_keys(self, server_name: str, account_name: str) -> tuple[HaremKeyObservation, ...]:
        """Return latest key observations for one account in one server context."""
        with self._connection() as connection:
            active_scan_id = self._active_harem_scan_id(
                connection, server_name, account_name, "keys"
            )
            scan_filter = "(harem_key_observations.harem_scan_id = ? OR harem_key_observations.harem_scan_id IS NULL)" if active_scan_id else (
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
                        AND divorces.normalized_character_name = harem_key_observations.normalized_character_name
                        AND divorces.import_event_id > harem_key_observations.import_event_id
                  )
                ORDER BY harem_key_observations.kakera_value DESC NULLS LAST,
                         harem_key_observations.key_count DESC,
                         harem_key_observations.character_name COLLATE NOCASE
                """,
                (active_scan_id, self._normalize(server_name), self._normalize(account_name), *scan_params),
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
                (self._normalize(server_name), self._normalize(account_name), limit),
            ).fetchall()
        return tuple(
            HaremKeyObservation(
                character_name=row["character_name"],
                character=(
                    CatalogCharacter(
                        id=row["character_id"], name=row["name"], series=row["series"],
                        gender=row["gender"], roulette=row["roulette"]
                    )
                    if row["character_id"] is not None else None
                ),
                key_type=row["key_type"], key_count=row["key_count"],
                kakera_value=row["kakera_value"],
                observed_at=datetime.fromisoformat(row["observed_at"]),
            )
            for row in rows
        )

    def begin_harem_scan(
        self, server_name: str, account_name: str, scan_kind: str = "keys"
    ) -> HaremScanProgress:
        """Start a multi-page harem import that must be completed before activation."""
        normalized_kind = scan_kind.strip().casefold()
        if normalized_kind not in {"keys", "owned"}:
            raise ValueError("Harem scan kind must be `keys` or `owned`.")
        observed_at = datetime.now(timezone.utc)
        with self._connection() as connection:
            server_id = self._upsert_server(connection, server_name, observed_at)
            account_id = self._upsert_account(connection, server_id, account_name, observed_at)
            cursor = connection.execute(
                "INSERT INTO harem_scans (account_context_id, expected_page_count, started_at, scan_kind) "
                "VALUES (?, NULL, ?, ?)",
                (account_id, observed_at.isoformat(), normalized_kind),
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
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] is not None else None
            ),
            scan_kind=row["scan_kind"],
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
                    self._normalize(server_name),
                    self._normalize(account_name),
                    normalized_kind,
                ),
            ).fetchone()
        return row is not None

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

    def import_antidisable_page(
        self,
        page: AntidisablePage,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
        scan_id: int | None = None,
    ) -> AntidisableImportResult:
        """Store one account-scoped `$adl` series page."""
        if scan_id is not None and (page.page_number is None or page.page_count is None):
            raise ValueError("A scanned antidisable page must include its Page X / Y indicator.")
        observed_at = datetime.now(timezone.utc)
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("antidisable", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)
            server_id = self._upsert_server(connection, server_name, observed_at)
            account_id = self._upsert_account(connection, server_id, account_name, observed_at)
            if scan_id is not None:
                self._prepare_antidisable_scan_page(
                    connection, scan_id, account_id, page.page_number, page.page_count
                )
            for series_name in page.series_names:
                connection.execute(
                    """
                    INSERT INTO antidisable_series_observations (
                        account_context_id, series_name, normalized_series_name,
                        antidisabled_character_count, observed_at, import_event_id, harem_scan_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        series_name,
                        self._normalize(series_name),
                        page.antidisabled_character_count,
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
        return AntidisableImportResult(
            import_event_id=import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            series_imported=len(page.series_names),
            observed_at=observed_at,
            scan_id=scan_id,
            page_number=page.page_number,
            page_count=page.page_count,
        )

    def begin_antidisable_scan(
        self, server_name: str, account_name: str
    ) -> HaremScanProgress:
        """Start a complete multi-page `$adl` scan."""
        observed_at = datetime.now(timezone.utc)
        with self._connection() as connection:
            server_id = self._upsert_server(connection, server_name, observed_at)
            account_id = self._upsert_account(connection, server_id, account_name, observed_at)
            cursor = connection.execute(
                "INSERT INTO harem_scans (account_context_id, expected_page_count, started_at, scan_kind) "
                "VALUES (?, NULL, ?, 'antidisable')",
                (account_id, observed_at.isoformat()),
            )
            scan_id = int(cursor.lastrowid)
        progress = self.harem_scan_progress(scan_id)
        assert progress is not None
        return progress

    def antidisable_series(
        self, server_name: str, account_name: str
    ) -> tuple[str, ...]:
        """Return series from the latest complete `$adl` scan."""
        with self._connection() as connection:
            scan_id = self._active_harem_scan_id(
                connection, server_name, account_name, "antidisable"
            )
            if scan_id is None:
                return ()
            rows = connection.execute(
                """
                SELECT series_name
                FROM antidisable_series_observations
                WHERE harem_scan_id = ?
                GROUP BY normalized_series_name
                ORDER BY series_name COLLATE NOCASE
                """,
                (scan_id,),
            ).fetchall()
        return tuple(row["series_name"] for row in rows)

    def complete_antidisable_scan(self, scan_id: int) -> HaremScanProgress:
        """Activate a complete `$adl` scan."""
        progress = self.harem_scan_progress(scan_id)
        if progress is None:
            raise ValueError("Antidisable scan not found.")
        if progress.scan_kind != "antidisable":
            raise ValueError("The scan is not an antidisable scan.")
        if not progress.is_complete:
            expected = progress.expected_page_count or "an unknown number of"
            raise ValueError(
                f"Antidisable scan is incomplete: imported pages {list(progress.imported_pages)}; "
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

    def import_kakera_state(
        self,
        state: KakeraStateSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> KakeraStateImportResult:
        """Store a complete account-scoped `$k` snapshot."""
        observed_at = datetime.now(timezone.utc)
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("kakera_state", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)
            server_id = self._upsert_server(connection, server_name, observed_at)
            account_id = self._upsert_account(connection, server_id, account_name, observed_at)
            connection.execute(
                """
                INSERT INTO kakera_state_observations (
                    account_context_id, kakera_balance, badges_json, observed_at, import_event_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    state.kakera_balance,
                    json.dumps([badge.model_dump() for badge in state.badges]),
                    observed_at.isoformat(),
                    import_event_id,
                ),
            )
        return KakeraStateImportResult(
            import_event_id=import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observed_at=observed_at,
        )

    def kakera_state(self, server_name: str, account_name: str) -> KakeraStateObservation | None:
        """Return the latest `$k` snapshot for one server/account pair."""
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT kakera_state_observations.*, server_contexts.name AS server_name,
                       account_contexts.name AS account_name
                FROM account_contexts
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                JOIN kakera_state_observations ON kakera_state_observations.id = (
                    SELECT observations.id FROM kakera_state_observations AS observations
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
        return KakeraStateObservation(
            server_name=row["server_name"],
            account_name=row["account_name"],
            kakera_balance=row["kakera_balance"],
            badges=tuple(json.loads(row["badges_json"])),
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )

    def kakera_history(
        self, server_name: str, account_name: str
    ) -> tuple[KakeraProgressPoint, ...]:
        """Return every imported `$k` snapshot in chronological order."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT kakera_state_observations.*, server_contexts.name AS server_name,
                       account_contexts.name AS account_name
                FROM kakera_state_observations
                JOIN account_contexts ON account_contexts.id = kakera_state_observations.account_context_id
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                WHERE server_contexts.normalized_name = ?
                  AND account_contexts.normalized_name = ?
                ORDER BY kakera_state_observations.observed_at ASC, kakera_state_observations.id ASC
                """,
                (self._normalize(server_name), self._normalize(account_name)),
            ).fetchall()
        return tuple(
            KakeraProgressPoint(
                kakera_balance=row["kakera_balance"],
                max_badge_count=sum(badge["max_reached"] for badge in json.loads(row["badges_json"])),
                observed_at=datetime.fromisoformat(row["observed_at"]),
            )
            for row in rows
        )

    def import_personal_rare(
        self,
        state: PersonalRareSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> PersonalRareImportResult:
        """Store one account-scoped `$persr` observation."""
        observed_at = datetime.now(timezone.utc)
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("personal_rare", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)
            server_id = self._upsert_server(connection, server_name, observed_at)
            account_id = self._upsert_account(connection, server_id, account_name, observed_at)
            connection.execute(
                """
                INSERT INTO personal_rare_observations (
                    account_context_id, personal_rare_multiplier, observed_at, import_event_id
                ) VALUES (?, ?, ?, ?)
                """,
                (account_id, state.personal_rare_multiplier, observed_at.isoformat(), import_event_id),
            )
        return PersonalRareImportResult(
            import_event_id=import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observed_at=observed_at,
        )

    def personal_rare(
        self, server_name: str, account_name: str
    ) -> PersonalRareObservation | None:
        """Return the latest `$persr` state for one server/account pair."""
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT personal_rare_observations.*, server_contexts.name AS server_name,
                       account_contexts.name AS account_name
                FROM account_contexts
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                JOIN personal_rare_observations ON personal_rare_observations.id = (
                    SELECT observations.id FROM personal_rare_observations AS observations
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
        return PersonalRareObservation(
            server_name=row["server_name"],
            account_name=row["account_name"],
            personal_rare_multiplier=row["personal_rare_multiplier"],
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )

    def import_tower_state(
        self,
        state: TowerStateSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> TowerStateImportResult:
        """Store a complete account-scoped `$kt` snapshot."""
        observed_at = datetime.now(timezone.utc)
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("tower_state", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)
            server_id = self._upsert_server(connection, server_name, observed_at)
            account_id = self._upsert_account(connection, server_id, account_name, observed_at)
            connection.execute(
                """
                INSERT INTO tower_state_observations (
                    account_context_id, current_level, completed_towers, next_level_cost,
                    kakera_balance, built_perk_ids_json, observed_at, import_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    state.current_level,
                    state.completed_towers or 0,
                    state.next_level_cost,
                    state.kakera_balance,
                    json.dumps(state.built_perk_ids),
                    observed_at.isoformat(),
                    import_event_id,
                ),
            )
        return TowerStateImportResult(
            import_event_id=import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observed_at=observed_at,
        )

    def tower_state(self, server_name: str, account_name: str) -> TowerStateObservation | None:
        """Return the latest `$kt` snapshot for one server/account pair."""
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT tower_state_observations.*, server_contexts.name AS server_name,
                       account_contexts.name AS account_name
                FROM account_contexts
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                JOIN tower_state_observations ON tower_state_observations.id = (
                    SELECT observations.id FROM tower_state_observations AS observations
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
        return TowerStateObservation(
            server_name=row["server_name"],
            account_name=row["account_name"],
            current_level=row["current_level"],
            completed_towers=row["completed_towers"] or None,
            next_level_cost=row["next_level_cost"],
            kakera_balance=row["kakera_balance"],
            built_perk_ids=tuple(json.loads(row["built_perk_ids_json"])),
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )

    def import_timer_state(
        self,
        state: TimerStateSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> TimerStateImportResult:
        """Store one short-lived account action snapshot from `$tu`."""
        observed_at = datetime.now(timezone.utc)
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("timer_state", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)
            server_id = self._upsert_server(connection, server_name, observed_at)
            account_id = self._upsert_account(connection, server_id, account_name, observed_at)
            connection.execute(
                """
                INSERT INTO timer_state_observations (
                    account_context_id, snapshot_json, observed_at, import_event_id
                ) VALUES (?, ?, ?, ?)
                """,
                (account_id, json.dumps(state.model_dump()), observed_at.isoformat(), import_event_id),
            )
        return TimerStateImportResult(
            import_event_id=import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observed_at=observed_at,
        )

    def timer_state(self, server_name: str, account_name: str) -> TimerStateObservation | None:
        """Return the newest imported `$tu` snapshot for one account."""
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT timer_state_observations.*, server_contexts.name AS server_name,
                       account_contexts.name AS account_name
                FROM account_contexts
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                JOIN timer_state_observations ON timer_state_observations.id = (
                    SELECT observations.id FROM timer_state_observations AS observations
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
        return TimerStateObservation(
            server_name=row["server_name"],
            account_name=row["account_name"],
            snapshot=TimerStateSnapshot.model_validate(json.loads(row["snapshot_json"])),
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )

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
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("kakeraloot_state", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)
            server_id = self._upsert_server(connection, server_name, observed_at)
            account_id = self._upsert_account(connection, server_id, account_name, observed_at)
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
            )
        return KakeralootStateImportResult(
            import_event_id=import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observed_at=observed_at,
        )

    def kakeraloot_state(
        self, server_name: str, account_name: str
    ) -> KakeralootStateObservation | None:
        """Return the latest `$lk` snapshot for one server/account pair."""
        with self._connection() as connection:
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
                (self._normalize(server_name), self._normalize(account_name)),
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

    def import_kakeraloot_settings(
        self,
        settings: KakeralootSettingsSnapshot,
        server_name: str,
        raw_message: str,
        source: str,
    ) -> KakeralootSettingsImportResult:
        """Store the latest server-scoped Kakeraloot price configuration."""
        observed_at = datetime.now(timezone.utc)
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("kakeraloot_settings", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)
            server_id = self._upsert_server(connection, server_name, observed_at)
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
            )
        return KakeralootSettingsImportResult(
            import_event_id=import_event_id,
            server_name=server_name.strip(),
            observed_at=observed_at,
        )

    def kakeraloot_settings(self, server_name: str) -> KakeralootSettingsObservation | None:
        """Return the latest `$infokl` price configuration for one server."""
        with self._connection() as connection:
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
                (self._normalize(server_name),),
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
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("profile", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)
            server_id = self._upsert_server(connection, server_name, observed_at)
            account_id = self._upsert_account(connection, server_id, account_name, observed_at)
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
                    snapshot.profile_name,
                    snapshot.collection_size,
                    snapshot.female_percent,
                    snapshot.male_percent,
                    snapshot.pokedex_count,
                    json.dumps(list(snapshot.pokedex_pokemon)),
                    json.dumps(snapshot.kakera_reacts),
                    snapshot.mudapins_collected,
                    snapshot.mudapins_total,
                    snapshot.kakera_balance,
                    snapshot.bronze_keys,
                    snapshot.silver_keys,
                    snapshot.gold_keys,
                    snapshot.sphere_stock,
                    json.dumps(snapshot.spheres),
                    json.dumps(list(snapshot.displayed_badges)),
                    observed_at.isoformat(),
                    import_event_id,
                ),
            )
        return ProfileImportResult(
            import_event_id=import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observed_at=observed_at,
        )

    def profile(self, server_name: str, account_name: str) -> ProfileObservation | None:
        """Return the latest `$profile` snapshot for one account."""
        with self._connection() as connection:
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
                (self._normalize(server_name), self._normalize(account_name)),
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

    def import_mudapins(
        self,
        snapshot: MudapinSnapshot,
        server_name: str,
        account_name: str,
        raw_message: str,
        source: str,
    ) -> MudapinImportResult:
        """Store one account-scoped `$mp` Mudapin inventory."""
        observed_at = datetime.now(timezone.utc)
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("mudapins", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)
            server_id = self._upsert_server(connection, server_name, observed_at)
            account_id = self._upsert_account(connection, server_id, account_name, observed_at)
            connection.execute(
                """
                INSERT INTO mudapin_observations (
                    account_context_id, pin_markers_json, pin_count, observed_at, import_event_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    json.dumps(list(snapshot.pin_markers)),
                    len(snapshot.pin_markers),
                    observed_at.isoformat(),
                    import_event_id,
                ),
            )
        return MudapinImportResult(
            import_event_id=import_event_id,
            server_name=server_name.strip(),
            account_name=account_name.strip(),
            observed_at=observed_at,
        )

    def mudapins(self, server_name: str, account_name: str) -> MudapinObservation | None:
        """Return the latest `$mp` inventory for one account."""
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT mudapin_observations.*, server_contexts.name AS server_name,
                       account_contexts.name AS account_name
                FROM account_contexts
                JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                JOIN mudapin_observations ON mudapin_observations.id = (
                    SELECT observations.id FROM mudapin_observations AS observations
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
        return MudapinObservation(
            server_name=row["server_name"],
            account_name=row["account_name"],
            snapshot=MudapinSnapshot(pin_markers=tuple(json.loads(row["pin_markers_json"]))),
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )

    def import_server_settings(
        self,
        settings: ServerSettingsSnapshot,
        server_name: str,
        raw_message: str,
        source: str,
    ) -> ServerSettingsImportResult:
        """Store a complete server-scoped `$settings` snapshot."""
        observed_at = datetime.now(timezone.utc)
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("server_settings", source, observed_at.isoformat(), raw_message),
            )
            import_event_id = int(cursor.lastrowid)
            server_id = self._upsert_server(connection, server_name, observed_at)
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
            )
        return ServerSettingsImportResult(
            import_event_id=import_event_id,
            server_name=server_name.strip(),
            observed_at=observed_at,
        )

    def server_settings(self, server_name: str) -> ServerSettingsObservation | None:
        """Return the latest `$settings` snapshot for one server."""
        with self._connection() as connection:
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
                (self._normalize(server_name),),
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
            self._delete_import_event_from_connection(connection, import_event_id)
        return True

    def inspect_bugged_imports(self) -> tuple[int, int]:
        """Count timer-as-roll imports and imports reparsed by the fixed parser."""
        with self._connection() as connection:
            import_event_ids, character_ids, _ = self._bugged_import_candidates(connection)
        return len(import_event_ids), len(character_ids)

    def repair_bugged_imports(self) -> tuple[int, int]:
        """Repair or remove only evidence identified from its preserved raw text.

        This intentionally leaves all canonical characters with any remaining
        observation untouched. Timer responses misclassified as rolls are
        removed; older roll and `$im` rows are reparsed with the current parser
        so wrapped series such as `Dungeon ni ... no` + `wa ...` are corrected.
        """
        with self._connection() as connection:
            import_event_ids, character_ids, repairs = self._bugged_import_candidates(connection)
            for import_event_id in import_event_ids - repairs.keys():
                self._delete_import_event_from_connection(connection, import_event_id)
            for import_event_id, (kind, old_character_id, observation) in repairs.items():
                if kind == "roll":
                    self._repair_roll_event(
                        connection, import_event_id, old_character_id, observation
                    )
                else:
                    self._repair_character_details_event(
                        connection, import_event_id, old_character_id, observation
                    )

            deleted_characters = 0
            for character_id in character_ids:
                if self._character_has_references(connection, character_id):
                    continue
                cursor = connection.execute(
                    "DELETE FROM characters WHERE id = ?", (character_id,)
                )
                deleted_characters += cursor.rowcount

        return len(import_event_ids), deleted_characters

    @staticmethod
    def _delete_import_event_from_connection(
        connection: sqlite3.Connection, import_event_id: int
    ) -> None:
        connection.execute(
            "DELETE FROM server_character_observations WHERE import_event_id = ?",
            (import_event_id,),
        )
        for table in (
            "roll_observations",
            "claim_observations",
            "divorce_observations",
            "kakera_reaction_observations",
            "player_bonus_observations",
            "wishlist_observations",
            "disablelist_observations",
            "unavailable_character_observations",
            "kakera_state_observations",
            "personal_rare_observations",
            "tower_state_observations",
            "timer_state_observations",
            "sphere_result_observations",
            "kakeraloot_state_observations",
            "kakeraloot_settings_observations",
            "server_settings_observations",
        ):
            connection.execute(
                f"DELETE FROM {table} WHERE import_event_id = ?",
                (import_event_id,),
            )
        for table in (
            "harem_key_observations",
            "owned_character_observations",
            "harem_scan_pages",
            "antidisable_series_observations",
            "rank_snapshots",
            "top_owner_observations",
        ):
            connection.execute(
                f"DELETE FROM {table} WHERE import_event_id = ?",
                (import_event_id,),
            )
        connection.execute("DELETE FROM import_events WHERE id = ?", (import_event_id,))

    def _bugged_import_candidates(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[
        set[int],
        set[int],
        dict[int, tuple[str, int, CharacterDetails | RollObservation]],
    ]:
        parser = MudaeTextParser()
        import_event_ids: set[int] = set()
        character_ids: set[int] = set()
        repairs: dict[int, tuple[str, int, CharacterDetails | RollObservation]] = {}

        roll_rows = connection.execute(
            """
            SELECT import_events.id AS import_event_id, import_events.raw_message,
                   roll_observations.character_id, characters.name, characters.series
            FROM import_events
            JOIN roll_observations ON roll_observations.import_event_id = import_events.id
            JOIN characters ON characters.id = roll_observations.character_id
            WHERE import_events.kind = 'roll'
            """
        ).fetchall()
        for row in roll_rows:
            event_id = int(row["import_event_id"])
            character_id = int(row["character_id"])
            if self._is_timer_like_message(row["raw_message"]):
                import_event_ids.add(event_id)
                character_ids.add(character_id)
                continue
            try:
                parsed = parser.parse_roll(row["raw_message"])
            except MudaeParseError:
                continue
            if (
                self._normalize(parsed.name) != self._normalize(row["name"])
                or self._normalize(parsed.series) != self._normalize(row["series"])
            ):
                import_event_ids.add(event_id)
                character_ids.add(character_id)
                repairs[event_id] = ("roll", character_id, parsed)

        details_rows = connection.execute(
            """
            SELECT DISTINCT import_events.id AS import_event_id, import_events.raw_message,
                   server_character_observations.character_id,
                   characters.name, characters.series
            FROM import_events
            JOIN server_character_observations
              ON server_character_observations.import_event_id = import_events.id
            JOIN characters ON characters.id = server_character_observations.character_id
            WHERE import_events.kind = 'character_details'
            """
        ).fetchall()
        for row in details_rows:
            event_id = int(row["import_event_id"])
            character_id = int(row["character_id"])
            try:
                parsed = parser.parse_character_details(row["raw_message"])
            except MudaeParseError:
                continue
            if (
                self._normalize(parsed.name) != self._normalize(row["name"])
                or self._normalize(parsed.series) != self._normalize(row["series"])
            ):
                import_event_ids.add(event_id)
                character_ids.add(character_id)
                repairs[event_id] = ("character_details", character_id, parsed)

        malformed_rows = connection.execute(
            """
            SELECT id, name, series
            FROM characters
            WHERE lower(name) LIKE 'each kakera button consumes %'
               OR lower(series) LIKE 'your characters with 10+ keys consume %'
               OR (
                   length(trim(name)) >= 20
                   AND length(trim(series)) <= 4
                   AND substr(trim(series), -1) IN ('!', '?', '.')
               )
            """
        ).fetchall()
        character_ids.update(int(row["id"]) for row in malformed_rows)

        return import_event_ids, character_ids, repairs

    @staticmethod
    def _is_timer_like_message(raw_message: str) -> bool:
        normalized = raw_message.casefold()
        return any(
            marker in normalized
            for marker in (
                "next rolls reset in",
                "you have ",
                "each kakera button consumes",
                "roulette is limited to",
            )
        ) and (
            "next rolls reset in" in normalized
            or "rolls left" in normalized
            or "each kakera button consumes" in normalized
            or "roulette is limited to" in normalized
        )

    def _repair_roll_event(
        self,
        connection: sqlite3.Connection,
        import_event_id: int,
        old_character_id: int,
        roll: RollObservation,
    ) -> None:
        new_character_id = self._upsert_character(
            connection,
            name=roll.name,
            series=roll.series,
            gender=None,
            roulette=None,
            observed_at=datetime.now(timezone.utc),
        ).fetchone()["id"]
        connection.execute(
            """
            UPDATE roll_observations
            SET character_id = ?, claim_rank = ?, kakera_value = ?
            WHERE import_event_id = ?
            """,
            (new_character_id, roll.claim_rank, roll.kakera_value, import_event_id),
        )
        connection.execute(
            """
            UPDATE rank_snapshots
            SET character_id = ?, claim_rank = ?
            WHERE import_event_id = ?
            """,
            (new_character_id, roll.claim_rank, import_event_id),
        )
        connection.execute(
            """
            UPDATE server_character_observations
            SET character_id = ?, kakera_value = ?
            WHERE import_event_id = ?
            """,
            (new_character_id, roll.kakera_value, import_event_id),
        )
        connection.execute(
            """
            UPDATE harem_key_observations
            SET character_id = ?, character_name = ?, normalized_character_name = ?,
                key_type = COALESCE(?, key_type), key_count = COALESCE(?, key_count),
                kakera_value = COALESCE(?, kakera_value)
            WHERE import_event_id = ?
            """,
            (
                new_character_id,
                roll.name,
                self._normalize(roll.name),
                roll.displayed_key_type,
                roll.displayed_key_count,
                roll.kakera_value,
                import_event_id,
            ),
        )
        if new_character_id == old_character_id:
            return

    def _repair_character_details_event(
        self,
        connection: sqlite3.Connection,
        import_event_id: int,
        old_character_id: int,
        details: CharacterDetails,
    ) -> None:
        new_character_id = self._upsert_character(
            connection,
            name=details.name,
            series=details.series,
            gender=details.gender,
            roulette=details.roulette,
            observed_at=datetime.now(timezone.utc),
        ).fetchone()["id"]
        connection.execute(
            """
            UPDATE rank_snapshots
            SET character_id = ?, claim_rank = ?, like_rank = ?
            WHERE import_event_id = ?
            """,
            (new_character_id, details.claim_rank, details.like_rank, import_event_id),
        )
        connection.execute(
            """
            UPDATE server_character_observations
            SET character_id = ?, kakera_value = ?
            WHERE import_event_id = ?
            """,
            (new_character_id, details.kakera_value, import_event_id),
        )
        connection.execute(
            """
            UPDATE harem_key_observations
            SET character_id = ?, character_name = ?, normalized_character_name = ?,
                key_type = COALESCE(?, key_type), key_count = COALESCE(?, key_count),
                kakera_value = COALESCE(?, kakera_value)
            WHERE import_event_id = ?
            """,
            (
                new_character_id,
                details.name,
                self._normalize(details.name),
                details.key_type,
                details.key_count,
                details.kakera_value,
                import_event_id,
            ),
        )
        if new_character_id == old_character_id:
            return

    @staticmethod
    def _character_has_references(
        connection: sqlite3.Connection, character_id: int
    ) -> bool:
        for table in (
            "rank_snapshots",
            "top_owner_observations",
            "server_character_observations",
            "roll_observations",
            "claim_observations",
            "harem_key_observations",
            "owned_character_observations",
            "unavailable_character_observations",
        ):
            if connection.execute(
                f"SELECT 1 FROM {table} WHERE character_id = ? LIMIT 1",
                (character_id,),
            ).fetchone():
                return True
        return False

    def _initialize(self) -> None:
        with self._connection() as connection:
            has_catalog_tables = connection.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                  AND name != 'schema_migrations'
                LIMIT 1
                """
            ).fetchone() is not None
            has_migration_metadata = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'schema_migrations'
                """
            ).fetchone() is not None
            if has_catalog_tables and not has_migration_metadata:
                validate_catalog_schema(connection)
                run_migrations(connection, CATALOG_MIGRATIONS)
                return
            if has_migration_metadata:
                run_migrations(connection, CATALOG_MIGRATIONS)

        self._create_schema()

        with self._connection() as connection:
            run_migrations(connection, CATALOG_MIGRATIONS)

    def _create_schema(self) -> None:
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
                    owner_name TEXT,
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

                CREATE TABLE IF NOT EXISTS top_owner_observations (
                    id INTEGER PRIMARY KEY,
                    server_context_id INTEGER NOT NULL REFERENCES server_contexts(id),
                    character_id INTEGER NOT NULL REFERENCES characters(id),
                    owner_name TEXT,
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id)
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

                CREATE TABLE IF NOT EXISTS roll_observations (
                    id INTEGER PRIMARY KEY,
                    account_context_id INTEGER NOT NULL REFERENCES account_contexts(id),
                    character_id INTEGER NOT NULL REFERENCES characters(id),
                    claim_rank INTEGER,
                    kakera_value INTEGER,
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id)
                );

                CREATE TABLE IF NOT EXISTS claim_observations (
                    id INTEGER PRIMARY KEY,
                    account_context_id INTEGER NOT NULL REFERENCES account_contexts(id),
                    character_id INTEGER REFERENCES characters(id),
                    character_name TEXT NOT NULL,
                    normalized_character_name TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id)
                );

                CREATE TABLE IF NOT EXISTS divorce_observations (
                    id INTEGER PRIMARY KEY,
                    account_context_id INTEGER NOT NULL REFERENCES account_contexts(id),
                    character_id INTEGER REFERENCES characters(id),
                    character_name TEXT NOT NULL,
                    normalized_character_name TEXT NOT NULL,
                    kakera_refund INTEGER,
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id)
                );

                CREATE TABLE IF NOT EXISTS kakera_reaction_observations (
                    id INTEGER PRIMARY KEY,
                    account_context_id INTEGER NOT NULL,
                    reaction_label TEXT NOT NULL,
                    kakera_earned INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL
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

                CREATE TABLE IF NOT EXISTS owned_character_observations (
                    id INTEGER PRIMARY KEY,
                    account_context_id INTEGER NOT NULL REFERENCES account_contexts(id),
                    character_id INTEGER REFERENCES characters(id),
                    character_name TEXT NOT NULL,
                    normalized_character_name TEXT NOT NULL,
                    claim_rank INTEGER NOT NULL,
                    kakera_value INTEGER,
                    roulette_types_json TEXT NOT NULL DEFAULT '[]',
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id),
                    harem_scan_id INTEGER REFERENCES harem_scans(id)
                );

                CREATE TABLE IF NOT EXISTS harem_scans (
                    id INTEGER PRIMARY KEY,
                    account_context_id INTEGER NOT NULL REFERENCES account_contexts(id),
                    expected_page_count INTEGER,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    scan_kind TEXT NOT NULL DEFAULT 'keys'
                );

                CREATE TABLE IF NOT EXISTS harem_scan_pages (
                    harem_scan_id INTEGER NOT NULL REFERENCES harem_scans(id),
                    page_number INTEGER NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id),
                    PRIMARY KEY (harem_scan_id, page_number)
                );

                CREATE TABLE IF NOT EXISTS antidisable_series_observations (
                    id INTEGER PRIMARY KEY,
                    account_context_id INTEGER NOT NULL REFERENCES account_contexts(id),
                    series_name TEXT NOT NULL,
                    normalized_series_name TEXT NOT NULL,
                    antidisabled_character_count INTEGER,
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id),
                    harem_scan_id INTEGER REFERENCES harem_scans(id)
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

                CREATE TABLE IF NOT EXISTS kakera_state_observations (
                    id INTEGER PRIMARY KEY,
                    account_context_id INTEGER NOT NULL REFERENCES account_contexts(id),
                    kakera_balance INTEGER,
                    badges_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id)
                );

                CREATE TABLE IF NOT EXISTS personal_rare_observations (
                    id INTEGER PRIMARY KEY,
                    account_context_id INTEGER NOT NULL REFERENCES account_contexts(id),
                    personal_rare_multiplier INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id)
                );

                CREATE TABLE IF NOT EXISTS tower_state_observations (
                    id INTEGER PRIMARY KEY,
                    account_context_id INTEGER NOT NULL REFERENCES account_contexts(id),
                    current_level INTEGER NOT NULL,
                    completed_towers INTEGER NOT NULL,
                    next_level_cost INTEGER NOT NULL,
                    kakera_balance INTEGER NOT NULL,
                    built_perk_ids_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id)
                );

                CREATE TABLE IF NOT EXISTS timer_state_observations (
                    id INTEGER PRIMARY KEY,
                    account_context_id INTEGER NOT NULL REFERENCES account_contexts(id),
                    snapshot_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id)
                );

                CREATE TABLE IF NOT EXISTS sphere_result_observations (
                    id INTEGER PRIMARY KEY,
                    account_context_id INTEGER NOT NULL REFERENCES account_contexts(id),
                    snapshot_json TEXT NOT NULL,
                    total_gained INTEGER NOT NULL,
                    stock INTEGER,
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id)
                );

                CREATE TABLE IF NOT EXISTS kakeraloot_state_observations (
                    id INTEGER PRIMARY KEY,
                    account_context_id INTEGER NOT NULL REFERENCES account_contexts(id),
                    has_kakeraloots INTEGER NOT NULL DEFAULT 1,
                    status_note TEXT,
                    rolls_stacked INTEGER NOT NULL,
                    disable_wa_ha_reduction INTEGER NOT NULL,
                    disable_wg_hg_reduction INTEGER NOT NULL,
                    protected_wish_level INTEGER NOT NULL,
                    protected_wish_denominator INTEGER NOT NULL,
                    mudapins INTEGER NOT NULL,
                    rt_cooldown_reduction_hours INTEGER NOT NULL,
                    permanent_roll_bonus INTEGER NOT NULL,
                    star_branches INTEGER NOT NULL,
                    starwish_slots_from_branches INTEGER NOT NULL,
                    quantity_level INTEGER NOT NULL,
                    quality_level INTEGER NOT NULL,
                    usage_count INTEGER NOT NULL,
                    kakera_balance INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id)
                );

                CREATE TABLE IF NOT EXISTS kakeraloot_settings_observations (
                    id INTEGER PRIMARY KEY,
                    server_context_id INTEGER NOT NULL REFERENCES server_contexts(id),
                    loot_cost INTEGER NOT NULL,
                    quantity_quality_base_cost INTEGER NOT NULL,
                    quantity_quality_level_increment INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id)
                );

                CREATE TABLE IF NOT EXISTS profile_observations (
                    id INTEGER PRIMARY KEY,
                    account_context_id INTEGER NOT NULL REFERENCES account_contexts(id),
                    profile_name TEXT NOT NULL,
                    collection_size INTEGER NOT NULL,
                    female_percent INTEGER NOT NULL,
                    male_percent INTEGER NOT NULL,
                    pokedex_count INTEGER,
                    pokedex_json TEXT NOT NULL,
                    kakera_reacts_json TEXT NOT NULL,
                    mudapins_collected INTEGER,
                    mudapins_total INTEGER,
                    kakera_balance INTEGER,
                    bronze_keys INTEGER NOT NULL,
                    silver_keys INTEGER NOT NULL,
                    gold_keys INTEGER NOT NULL,
                    sphere_stock INTEGER,
                    spheres_json TEXT NOT NULL,
                    displayed_badges_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id)
                );

                CREATE TABLE IF NOT EXISTS mudapin_observations (
                    id INTEGER PRIMARY KEY,
                    account_context_id INTEGER NOT NULL REFERENCES account_contexts(id),
                    pin_markers_json TEXT NOT NULL,
                    pin_count INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    import_event_id INTEGER NOT NULL REFERENCES import_events(id)
                );

                CREATE TABLE IF NOT EXISTS server_settings_observations (
                    id INTEGER PRIMARY KEY,
                    server_context_id INTEGER NOT NULL REFERENCES server_contexts(id),
                    server_premium INTEGER NOT NULL,
                    prefix TEXT NOT NULL,
                    language TEXT NOT NULL,
                    claim_reset_minutes INTEGER NOT NULL,
                    reset_minute TEXT NOT NULL,
                    reset_shift_minutes INTEGER NOT NULL,
                    rolls_per_hour INTEGER NOT NULL,
                    claim_reaction_expiry_seconds INTEGER NOT NULL,
                    claimed_character_rarity_multiplier INTEGER NOT NULL,
                    kakera_bonus_percent INTEGER NOT NULL,
                    sphere_bonus_percent INTEGER NOT NULL,
                    game_mode INTEGER NOT NULL,
                    channel_instance INTEGER NOT NULL,
                    metrics_json TEXT NOT NULL,
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
            scan_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(harem_scans)").fetchall()
            }
            if "scan_kind" not in scan_columns:
                connection.execute(
                    "ALTER TABLE harem_scans ADD COLUMN scan_kind TEXT NOT NULL DEFAULT 'keys'"
                )
            rank_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(rank_snapshots)").fetchall()
            }
            if "owner_name" not in rank_columns:
                connection.execute("ALTER TABLE rank_snapshots ADD COLUMN owner_name TEXT")
            top_owner_columns = connection.execute(
                "PRAGMA table_info(top_owner_observations)"
            ).fetchall()
            if any(
                row["name"] == "owner_name" and row["notnull"]
                for row in top_owner_columns
            ):
                connection.execute(
                    "ALTER TABLE top_owner_observations RENAME TO top_owner_observations_legacy"
                )
                connection.execute(
                    """
                    CREATE TABLE top_owner_observations (
                        id INTEGER PRIMARY KEY,
                        server_context_id INTEGER NOT NULL REFERENCES server_contexts(id),
                        character_id INTEGER NOT NULL REFERENCES characters(id),
                        owner_name TEXT,
                        observed_at TEXT NOT NULL,
                        import_event_id INTEGER NOT NULL REFERENCES import_events(id)
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO top_owner_observations (
                        id, server_context_id, character_id, owner_name, observed_at, import_event_id
                    )
                    SELECT id, server_context_id, character_id, owner_name, observed_at, import_event_id
                    FROM top_owner_observations_legacy
                    """
                )
                connection.execute("DROP TABLE top_owner_observations_legacy")
            connection.execute(
                """
                INSERT INTO top_owner_observations (
                    server_context_id, character_id, owner_name, observed_at, import_event_id
                )
                SELECT scoped.server_context_id,
                       rank_snapshots.character_id,
                       rank_snapshots.owner_name,
                       rank_snapshots.observed_at,
                       rank_snapshots.import_event_id
                FROM rank_snapshots
                JOIN import_events
                  ON import_events.id = rank_snapshots.import_event_id
                 AND import_events.kind = 'top_page'
                JOIN (
                    SELECT DISTINCT server_context_id, import_event_id
                    FROM top_owner_observations
                ) AS scoped
                  ON scoped.import_event_id = rank_snapshots.import_event_id
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM top_owner_observations AS existing
                    WHERE existing.server_context_id = scoped.server_context_id
                      AND existing.character_id = rank_snapshots.character_id
                      AND existing.import_event_id = rank_snapshots.import_event_id
                )
                """
            )
            owned_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(owned_character_observations)"
                ).fetchall()
            }
            if "harem_scan_id" not in owned_columns:
                connection.execute(
                    "ALTER TABLE owned_character_observations ADD COLUMN harem_scan_id INTEGER "
                    "REFERENCES harem_scans(id)"
                )
            if "roulette_types_json" not in owned_columns:
                connection.execute(
                    "ALTER TABLE owned_character_observations "
                    "ADD COLUMN roulette_types_json TEXT NOT NULL DEFAULT '[]'"
                )
            loot_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(kakeraloot_state_observations)").fetchall()
            }
            if "has_kakeraloots" not in loot_columns:
                connection.execute(
                    "ALTER TABLE kakeraloot_state_observations "
                    "ADD COLUMN has_kakeraloots INTEGER NOT NULL DEFAULT 1"
                )
            if "status_note" not in loot_columns:
                connection.execute("ALTER TABLE kakeraloot_state_observations ADD COLUMN status_note TEXT")
            antidisable_columns = connection.execute(
                "PRAGMA table_info(antidisable_series_observations)"
            ).fetchall()
            if any(
                row["name"] == "antidisabled_character_count" and row["notnull"]
                for row in antidisable_columns
            ):
                connection.execute(
                    "ALTER TABLE antidisable_series_observations "
                    "RENAME TO antidisable_series_observations_legacy"
                )
                connection.execute(
                    """
                    CREATE TABLE antidisable_series_observations (
                        id INTEGER PRIMARY KEY,
                        account_context_id INTEGER NOT NULL REFERENCES account_contexts(id),
                        series_name TEXT NOT NULL,
                        normalized_series_name TEXT NOT NULL,
                        antidisabled_character_count INTEGER,
                        observed_at TEXT NOT NULL,
                        import_event_id INTEGER NOT NULL REFERENCES import_events(id),
                        harem_scan_id INTEGER REFERENCES harem_scans(id)
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO antidisable_series_observations (
                        id, account_context_id, series_name, normalized_series_name,
                        antidisabled_character_count, observed_at, import_event_id, harem_scan_id
                    )
                    SELECT id, account_context_id, series_name, normalized_series_name,
                           antidisabled_character_count, observed_at, import_event_id, harem_scan_id
                    FROM antidisable_series_observations_legacy
                    """
                )
                connection.execute("DROP TABLE antidisable_series_observations_legacy")

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

    def _prepare_antidisable_scan_page(
        self,
        connection: sqlite3.Connection,
        scan_id: int,
        account_id: int,
        page_number: int | None,
        page_count: int | None,
    ) -> None:
        if page_number is None or page_count is None:
            raise ValueError("A scanned antidisable page must include its Page X / Y indicator.")
        scan = connection.execute(
            "SELECT account_context_id, expected_page_count, completed_at, scan_kind "
            "FROM harem_scans WHERE id = ?",
            (scan_id,),
        ).fetchone()
        if scan is None:
            raise ValueError("Antidisable scan not found.")
        if scan["account_context_id"] != account_id:
            raise ValueError("Antidisable scan belongs to a different server or account.")
        if scan["scan_kind"] != "antidisable":
            raise ValueError("The scan is not an antidisable scan.")
        if scan["completed_at"] is not None:
            raise ValueError("Antidisable scan is already complete; begin a new scan to refresh it.")
        if scan["expected_page_count"] not in (None, page_count):
            raise ValueError("Antidisable page count does not match the scan's first imported page.")
        duplicate = connection.execute(
            "SELECT 1 FROM harem_scan_pages WHERE harem_scan_id = ? AND page_number = ?",
            (scan_id, page_number),
        ).fetchone()
        if duplicate is not None:
            raise ValueError("This antidisable scan already contains that page.")
        connection.execute(
            "UPDATE harem_scans SET expected_page_count = ? WHERE id = ?",
            (page_count, scan_id),
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
            (self._normalize(server_name), self._normalize(account_name), scan_kind),
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
