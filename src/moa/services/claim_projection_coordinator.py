"""Transactional coordination for durable Discord claim projections."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from moa.database.sqlite import DEFAULT_DATABASE_PATH, run_write_transaction
from moa.models.character import ClaimConfirmation
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository


class ClaimProjectionCoordinatorError(RuntimeError):
    """Base error for a claim projection coordination failure."""


class ClaimProjectionStateError(ClaimProjectionCoordinatorError):
    """Raised when a source event or processing attempt is not usable."""


class ClaimProjectionIntegrityError(ClaimProjectionCoordinatorError):
    """Raised when durable claim projection-link state is not trustworthy."""


class ClaimProjectionTargetError(ClaimProjectionIntegrityError):
    """Raised when a completed claim target is missing or mismatched."""


class ClaimProjectionDatabasePathError(ClaimProjectionCoordinatorError, ValueError):
    """Raised when the repositories do not point at one database."""


@dataclass(frozen=True, slots=True)
class ClaimProjectionResult:
    """The durable outcome of one coordinated claim projection."""

    imported_count: int
    import_event_id: int
    claim_observation_id: int
    character_id: int | None
    replay_skipped: bool
    durable_success_recorded: bool
    projection_target: tuple[str, int]


class ClaimProjectionCoordinator:
    """Own one SQLite transaction for a Discord claim and its projection."""

    _PROJECTION_KIND = "catalog.claim"
    _PROJECTION_TABLE = "claim_observations"
    _TARGET_TABLES = frozenset({_PROJECTION_TABLE})

    def __init__(
        self,
        catalog_repository: CatalogRepository,
        discord_message_repository: DiscordMessageRepository,
    ) -> None:
        self._catalog = catalog_repository
        self._discord = discord_message_repository
        catalog_path = self._effective_database_path(catalog_repository)
        discord_path = self._effective_database_path(discord_message_repository)
        if catalog_path != discord_path:
            raise ClaimProjectionDatabasePathError(
                "catalog and Discord repositories must use the same database path"
            )
        self._database_path = catalog_path

    def coordinate_claim(
        self,
        *,
        source_event_id: int,
        attempt_id: int | None,
        claim: ClaimConfirmation,
        server: str,
        account: str,
        raw: str,
        source: str,
        observed_at: datetime,
        finished_at: datetime,
    ) -> ClaimProjectionResult:
        """Coordinate one first-processing attempt or validate a completed replay."""
        self._validate_identity(source_event_id, "source_event_id")
        if attempt_id is not None:
            self._validate_identity(attempt_id, "attempt_id")
        observed_at = self._normalize_datetime(observed_at, "observed_at")
        finished_at = self._normalize_datetime(finished_at, "finished_at")
        projection_slot = self._claim_slot(server, account, claim.character_name)

        def coordinate_with_connection(
            connection: sqlite3.Connection,
        ) -> ClaimProjectionResult:
            event = self._load_source_event(connection, source_event_id)
            if str(event["status"]) == "succeeded":
                if attempt_id is not None:
                    raise ClaimProjectionStateError(
                        f"Discord source event {source_event_id} has already succeeded"
                    )
                persisted_account = self._validate_attribution(
                    connection,
                    source_event_id=source_event_id,
                    server=server,
                    account=account,
                )
                self._validate_claimant(
                    claim,
                    account=account,
                    persisted_account=persisted_account,
                )
                return self._coordinate_replay(connection, event, projection_slot)

            if attempt_id is None:
                raise ClaimProjectionStateError(
                    f"Discord source event {source_event_id} has no active processing attempt"
                )
            self._validate_active_attempt(connection, event, source_event_id, attempt_id)
            persisted_account = self._validate_attribution(
                connection,
                source_event_id=source_event_id,
                server=server,
                account=account,
            )
            self._validate_claimant(
                claim,
                account=account,
                persisted_account=persisted_account,
            )
            links = self._load_links(connection, source_event_id)
            expected_key = (self._PROJECTION_KIND, projection_slot)
            if set(links) - {expected_key}:
                raise ClaimProjectionIntegrityError(
                    f"source event {source_event_id} has unexpected projection links"
                )
            existing = links.get(expected_key)
            if existing is not None:
                raise ClaimProjectionIntegrityError(
                    f"claim projection for source event {source_event_id} already exists"
                )
            self._claim_projection_link(
                connection,
                source_event_id=source_event_id,
                projection_slot=projection_slot,
                claimed_at=observed_at,
            )

            imported = self._catalog._import_claim_with_connection(
                connection,
                claim=claim,
                server=server,
                account=account,
                raw=raw,
                source=source,
                observed_at=observed_at,
            )
            import_event_id = self._positive_id(imported.import_event_id, "import_event_id")
            claim_observation_id = self._positive_id(
                imported.claim_observation_id, "claim_observation_id"
            )
            self._validate_claim_target(
                connection,
                claim_observation_id=claim_observation_id,
                import_event_id=import_event_id,
                projection_slot=projection_slot,
                expected_character_id=imported.character_id,
            )
            target = (self._PROJECTION_TABLE, claim_observation_id)
            self._complete_projection_link(
                connection,
                source_event_id=source_event_id,
                projection_slot=projection_slot,
                target=target,
                completed_at=finished_at,
            )
            success = self._discord._mark_processing_success_with_connection(
                connection,
                source_event_id=source_event_id,
                attempt_id=attempt_id,
                finished_at=finished_at,
                legacy_import_event_id=import_event_id,
            )
            if success.attempt_status != "succeeded" or success.source_event_status != "succeeded":
                raise ClaimProjectionStateError(
                    f"processing success was not recorded for source event {source_event_id}"
                )
            return ClaimProjectionResult(
                imported_count=1,
                import_event_id=import_event_id,
                claim_observation_id=claim_observation_id,
                character_id=(
                    int(imported.character_id) if imported.character_id is not None else None
                ),
                replay_skipped=False,
                durable_success_recorded=True,
                projection_target=target,
            )

        return run_write_transaction(self._database_path, coordinate_with_connection)

    def _validate_attribution(
        self,
        connection: sqlite3.Connection,
        *,
        source_event_id: int,
        server: str,
        account: str,
    ) -> str:
        server_attribution = self._discord._get_server_attribution_with_connection(
            connection, source_event_id
        )
        if server_attribution is None:
            raise ClaimProjectionIntegrityError(
                f"source event {source_event_id} has no persisted server attribution"
            )
        if server_attribution.status != "resolved":
            raise ClaimProjectionIntegrityError(
                f"source event {source_event_id} has non-resolved server attribution"
            )
        if server_attribution.server_name is None or self._normalize(
            server_attribution.server_name
        ) != self._normalize(server):
            raise ClaimProjectionIntegrityError(
                f"source event {source_event_id} is attributed to another server"
            )

        account_attribution = self._discord._get_account_attribution_with_connection(
            connection, source_event_id
        )
        if account_attribution is None:
            raise ClaimProjectionIntegrityError(
                f"source event {source_event_id} has no persisted account attribution"
            )
        if account_attribution.status != "resolved":
            raise ClaimProjectionIntegrityError(
                f"source event {source_event_id} has non-resolved account attribution"
            )
        if account_attribution.server_name is None or self._normalize(
            account_attribution.server_name
        ) != self._normalize(server_attribution.server_name):
            raise ClaimProjectionIntegrityError(
                f"source event {source_event_id} has mismatched account attribution server"
            )
        if account_attribution.account_name is None or self._normalize(
            account_attribution.account_name
        ) != self._normalize(account):
            raise ClaimProjectionIntegrityError(
                f"source event {source_event_id} is attributed to another account"
            )
        return account_attribution.account_name

    @staticmethod
    def _validate_claimant(
        claim: ClaimConfirmation,
        *,
        account: str,
        persisted_account: str,
    ) -> None:
        if not isinstance(claim.account_name, str) or not claim.account_name.strip():
            raise ClaimProjectionIntegrityError("claim has no valid typed claimant")
        claimant = CatalogRepository._normalize(claim.account_name)
        if claimant != CatalogRepository._normalize(account):
            raise ClaimProjectionIntegrityError(
                "parsed claim claimant does not match the resolved account"
            )
        if claimant != CatalogRepository._normalize(persisted_account):
            raise ClaimProjectionIntegrityError(
                "parsed claim claimant does not match persisted account attribution"
            )

    def _coordinate_replay(
        self,
        connection: sqlite3.Connection,
        event: sqlite3.Row,
        projection_slot: str,
    ) -> ClaimProjectionResult:
        import_event_id = event["legacy_import_event_id"]
        if import_event_id is None:
            raise ClaimProjectionIntegrityError(
                f"succeeded source event {event['id']} has no legacy import event"
            )
        import_event = connection.execute(
            "SELECT id, kind FROM import_events WHERE id = ?", (int(import_event_id),)
        ).fetchone()
        if import_event is None or str(import_event["kind"]) != "claim":
            raise ClaimProjectionTargetError(
                f"legacy claim import event {import_event_id} for source event {event['id']} is missing or wrong"
            )

        links = self._load_links(connection, int(event["id"]))
        key = (self._PROJECTION_KIND, projection_slot)
        if set(links) != {key}:
            raise ClaimProjectionIntegrityError(
                f"succeeded source event {event['id']} has an inconsistent claim projection link"
            )
        link = links[key]
        if str(link["state"]) != "completed" or str(link["projection_table"]) != self._PROJECTION_TABLE:
            raise ClaimProjectionIntegrityError(
                f"succeeded source event {event['id']} has an inconsistent claim projection link"
            )
        projection_row_id = link["projection_row_id"]
        if projection_row_id is None:
            raise ClaimProjectionTargetError(
                f"claim projection for source event {event['id']} has no target"
            )
        claim_observation_id = int(projection_row_id)
        character_id = self._validate_claim_target(
            connection,
            claim_observation_id=claim_observation_id,
            import_event_id=int(import_event_id),
            projection_slot=projection_slot,
            expected_character_id=None,
        )
        return ClaimProjectionResult(
            imported_count=0,
            import_event_id=int(import_event_id),
            claim_observation_id=claim_observation_id,
            character_id=character_id,
            replay_skipped=True,
            durable_success_recorded=True,
            projection_target=(self._PROJECTION_TABLE, claim_observation_id),
        )

    @staticmethod
    def _load_source_event(connection: sqlite3.Connection, source_event_id: int) -> sqlite3.Row:
        event = connection.execute(
            "SELECT * FROM discord_source_events WHERE id = ?", (source_event_id,)
        ).fetchone()
        if event is None:
            raise ClaimProjectionStateError(
                f"Discord source event {source_event_id} was not found"
            )
        return event

    @staticmethod
    def _validate_active_attempt(
        connection: sqlite3.Connection,
        event: sqlite3.Row,
        source_event_id: int,
        attempt_id: int,
    ) -> None:
        if str(event["status"]) != "processing":
            raise ClaimProjectionStateError(
                f"Discord source event {source_event_id} is not processing"
            )
        attempt = connection.execute(
            "SELECT source_event_id, status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()
        if attempt is None:
            raise ClaimProjectionStateError(
                f"Discord processing attempt {attempt_id} was not found"
            )
        if int(attempt["source_event_id"]) != source_event_id:
            raise ClaimProjectionStateError(
                f"Discord processing attempt {attempt_id} belongs to another source event"
            )
        if str(attempt["status"]) != "processing":
            raise ClaimProjectionStateError(
                f"Discord processing attempt {attempt_id} is not processing"
            )

    @staticmethod
    def _load_links(
        connection: sqlite3.Connection, source_event_id: int
    ) -> dict[tuple[str, str], sqlite3.Row]:
        links: dict[tuple[str, str], sqlite3.Row] = {}
        for link in connection.execute(
            "SELECT * FROM discord_projection_links WHERE source_event_id = ?",
            (source_event_id,),
        ).fetchall():
            key = (str(link["projection_kind"]), str(link["projection_slot"]))
            if key in links:
                raise ClaimProjectionIntegrityError(
                    f"duplicate projection link identity for source event {source_event_id}"
                )
            links[key] = link
        return links

    def _claim_projection_link(
        self,
        connection: sqlite3.Connection,
        *,
        source_event_id: int,
        projection_slot: str,
        claimed_at: datetime,
    ) -> None:
        value = claimed_at.isoformat()
        connection.execute(
            """
            INSERT INTO discord_projection_links (
                source_event_id, projection_kind, projection_slot,
                projection_table, projection_row_id, state,
                claimed_at, completed_at, created_at, updated_at
            ) VALUES (?, ?, ?, NULL, NULL, 'claimed', ?, NULL, ?, ?)
            """,
            (source_event_id, self._PROJECTION_KIND, projection_slot, value, value, value),
        )

    def _complete_projection_link(
        self,
        connection: sqlite3.Connection,
        *,
        source_event_id: int,
        projection_slot: str,
        target: tuple[str, int],
        completed_at: datetime,
    ) -> None:
        table, row_id = target
        if table not in self._TARGET_TABLES or row_id <= 0:
            raise ClaimProjectionIntegrityError("claim import returned an invalid projection target")
        value = completed_at.isoformat()
        updated = connection.execute(
            """
            UPDATE discord_projection_links
            SET projection_table = ?, projection_row_id = ?, state = 'completed',
                completed_at = ?, updated_at = ?
            WHERE source_event_id = ? AND projection_kind = ? AND projection_slot = ?
              AND state = 'claimed'
            """,
            (
                table,
                row_id,
                value,
                value,
                source_event_id,
                self._PROJECTION_KIND,
                projection_slot,
            ),
        )
        if updated.rowcount != 1:
            raise ClaimProjectionIntegrityError("claim projection link could not be completed")

    def _validate_claim_target(
        self,
        connection: sqlite3.Connection,
        *,
        claim_observation_id: int,
        import_event_id: int,
        projection_slot: str,
        expected_character_id: int | None,
    ) -> int | None:
        if self._PROJECTION_TABLE not in self._TARGET_TABLES:
            raise ClaimProjectionIntegrityError("claim projection table is not allowlisted")
        row = connection.execute(
            """
            SELECT co.import_event_id, co.character_id,
                   co.normalized_character_name,
                   ac.normalized_name AS account,
                   sc.normalized_name AS server
            FROM claim_observations AS co
            JOIN account_contexts AS ac ON ac.id = co.account_context_id
            JOIN server_contexts AS sc ON sc.id = ac.server_context_id
            WHERE co.id = ?
            """,
            (claim_observation_id,),
        ).fetchone()
        if row is None:
            raise ClaimProjectionTargetError(
                f"projection target claim_observations:{claim_observation_id} is missing"
            )
        if int(row["import_event_id"]) != import_event_id:
            raise ClaimProjectionTargetError(
                f"projection target claim_observations:{claim_observation_id} belongs to another import event"
            )
        slot = json.loads(projection_slot)
        if (
            row["server"] != slot["server"]
            or row["account"] != slot["account"]
            or row["normalized_character_name"] != slot["character_name"]
        ):
            raise ClaimProjectionTargetError(
                f"projection target claim_observations:{claim_observation_id} has mismatched claim scope"
            )
        actual_character_id = (
            int(row["character_id"]) if row["character_id"] is not None else None
        )
        if expected_character_id is not None and actual_character_id != int(expected_character_id):
            raise ClaimProjectionTargetError(
                f"projection target claim_observations:{claim_observation_id} has mismatched character identity"
            )
        return actual_character_id

    @staticmethod
    def _claim_slot(server: str, account: str, character_name: str) -> str:
        values = {
            "account": CatalogRepository._normalize(account),
            "character_name": CatalogRepository._normalize(character_name),
            "server": CatalogRepository._normalize(server),
        }
        return json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _normalize_datetime(value: datetime, field_name: str) -> datetime:
        if not isinstance(value, datetime):
            raise TypeError(f"{field_name} must be a datetime")
        if value.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _validate_identity(value: int, field_name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{field_name} must be an integer")

    @staticmethod
    def _positive_id(value: Any, field_name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ClaimProjectionIntegrityError(f"claim import returned an invalid {field_name}")
        return int(value)

    @staticmethod
    def _normalize(value: str) -> str:
        return CatalogRepository._normalize(value)

    @staticmethod
    def _effective_database_path(repository: Any) -> Path:
        value = getattr(repository, "_database_path", None)
        return Path(value if value is not None else DEFAULT_DATABASE_PATH).resolve()
