"""Transactional coordination for durable Discord profile projections."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from moa.database.sqlite import DEFAULT_DATABASE_PATH, connect
from moa.models.character import ProfileSnapshot
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository


class ProfileProjectionCoordinatorError(RuntimeError):
    """Base error for a profile projection coordination failure."""


class ProfileProjectionStateError(ProfileProjectionCoordinatorError):
    """Raised when a source event or processing attempt is not usable."""


class ProfileProjectionIntegrityError(ProfileProjectionCoordinatorError):
    """Raised when durable profile projection-link state cannot be trusted."""


class ProfileProjectionTargetError(ProfileProjectionIntegrityError):
    """Raised when a completed profile projection target is missing or mismatched."""


class ProfileProjectionDatabasePathError(ProfileProjectionCoordinatorError, ValueError):
    """Raised when the repositories do not point at one database."""


@dataclass(frozen=True, slots=True)
class ProfileProjectionResult:
    """The durable outcome of one coordinated profile projection."""

    imported_count: int
    import_event_id: int
    profile_observation_id: int
    replay_skipped: bool
    durable_success_recorded: bool
    projection_target: tuple[str, int]


class ProfileProjectionCoordinator:
    """Own one SQLite transaction for a Discord profile and its projection."""

    _PROJECTION_KIND = "catalog.profile"
    _PROJECTION_TABLE = "profile_observations"
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
            raise ProfileProjectionDatabasePathError(
                "catalog and Discord repositories must use the same database path"
            )
        self._database_path = catalog_path

    def coordinate_profile(
        self,
        *,
        source_event_id: int,
        attempt_id: int | None,
        profile: ProfileSnapshot,
        server: str,
        account: str,
        raw: str,
        source: str,
        observed_at: datetime,
        finished_at: datetime,
    ) -> ProfileProjectionResult:
        """Coordinate one first-processing attempt or validate a completed replay."""
        self._validate_identity(source_event_id, "source_event_id")
        if attempt_id is not None:
            self._validate_identity(attempt_id, "attempt_id")
        observed_at = self._normalize_datetime(observed_at, "observed_at")
        finished_at = self._normalize_datetime(finished_at, "finished_at")
        projection_slot = self._profile_slot(server, account)

        connection = connect(self._database_path)
        try:
            connection.execute("BEGIN")
            event = self._load_source_event(connection, source_event_id)
            if str(event["status"]) == "succeeded":
                if attempt_id is not None:
                    raise ProfileProjectionStateError(
                        f"Discord source event {source_event_id} has already succeeded"
                    )
                return self._coordinate_replay(
                    connection,
                    event,
                    projection_slot,
                )

            if attempt_id is None:
                raise ProfileProjectionStateError(
                    f"Discord source event {source_event_id} has no active processing attempt"
                )
            self._validate_active_attempt(connection, source_event_id, attempt_id)
            links = self._load_links(connection, source_event_id)
            expected_key = (self._PROJECTION_KIND, projection_slot)
            unexpected = set(links) - {expected_key}
            if unexpected:
                raise ProfileProjectionIntegrityError(
                    f"source event {source_event_id} has unexpected projection links"
                )
            existing = links.get(expected_key)
            if existing is not None:
                state = str(existing["state"])
                if state == "claimed":
                    raise ProfileProjectionIntegrityError(
                        f"profile projection for source event {source_event_id} is still claimed"
                    )
                raise ProfileProjectionIntegrityError(
                    f"source event {source_event_id} already has a completed profile projection"
                )
            self._claim_projection_link(
                connection,
                source_event_id=source_event_id,
                projection_slot=projection_slot,
                claimed_at=observed_at,
            )

            imported = self._catalog._import_profile_with_connection(
                connection,
                profile=profile,
                server=server,
                account=account,
                raw=raw,
                source=source,
                observed_at=observed_at,
            )
            target = (self._PROJECTION_TABLE, int(imported.profile_observation_id))
            self._validate_profile_target(
                connection,
                target[1],
                int(imported.import_event_id),
                projection_slot,
            )
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
                legacy_import_event_id=int(imported.import_event_id),
            )
            if success.attempt_status != "succeeded" or success.source_event_status != "succeeded":
                raise ProfileProjectionStateError(
                    f"processing success was not recorded for source event {source_event_id}"
                )
            connection.commit()
            return ProfileProjectionResult(
                imported_count=1,
                import_event_id=int(imported.import_event_id),
                profile_observation_id=target[1],
                replay_skipped=False,
                durable_success_recorded=True,
                projection_target=target,
            )
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _coordinate_replay(
        self,
        connection: sqlite3.Connection,
        event: sqlite3.Row,
        projection_slot: str,
    ) -> ProfileProjectionResult:
        import_event_id = event["legacy_import_event_id"]
        if import_event_id is None:
            raise ProfileProjectionIntegrityError(
                f"succeeded source event {event['id']} has no legacy import event"
            )
        import_event = connection.execute(
            "SELECT id, kind FROM import_events WHERE id = ?", (int(import_event_id),)
        ).fetchone()
        if import_event is None or str(import_event["kind"]) != "profile":
            raise ProfileProjectionTargetError(
                f"legacy profile import event {import_event_id} for source event {event['id']} is missing or wrong"
            )

        links = self._load_links(connection, int(event["id"]))
        key = (self._PROJECTION_KIND, projection_slot)
        if set(links) != {key}:
            raise ProfileProjectionIntegrityError(
                f"succeeded source event {event['id']} has an inconsistent profile projection link"
            )
        link = links[key]
        if str(link["state"]) != "completed":
            raise ProfileProjectionIntegrityError(
                f"profile projection for source event {event['id']} is not completed"
            )
        if str(link["projection_table"]) != self._PROJECTION_TABLE:
            raise ProfileProjectionIntegrityError(
                f"succeeded source event {event['id']} has an inconsistent profile projection link"
            )
        projection_row_id = link["projection_row_id"]
        if projection_row_id is None:
            raise ProfileProjectionTargetError(
                f"profile projection for source event {event['id']} has no target"
            )
        target = (self._PROJECTION_TABLE, int(projection_row_id))
        self._validate_profile_target(
            connection,
            target[1],
            int(import_event_id),
            projection_slot,
        )
        return ProfileProjectionResult(
            imported_count=0,
            import_event_id=int(import_event_id),
            profile_observation_id=target[1],
            replay_skipped=True,
            durable_success_recorded=True,
            projection_target=target,
        )

    @staticmethod
    def _load_source_event(connection: sqlite3.Connection, source_event_id: int) -> sqlite3.Row:
        event = connection.execute(
            "SELECT * FROM discord_source_events WHERE id = ?", (source_event_id,)
        ).fetchone()
        if event is None:
            raise ProfileProjectionStateError(
                f"Discord source event {source_event_id} was not found"
            )
        return event

    @staticmethod
    def _validate_active_attempt(
        connection: sqlite3.Connection,
        source_event_id: int,
        attempt_id: int,
    ) -> None:
        event = connection.execute(
            "SELECT status FROM discord_source_events WHERE id = ?", (source_event_id,)
        ).fetchone()
        if event is None or str(event["status"]) != "processing":
            raise ProfileProjectionStateError(
                f"Discord source event {source_event_id} is not processing"
            )
        attempt = connection.execute(
            "SELECT source_event_id, status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()
        if attempt is None:
            raise ProfileProjectionStateError(
                f"Discord processing attempt {attempt_id} was not found"
            )
        if int(attempt["source_event_id"]) != source_event_id:
            raise ProfileProjectionStateError(
                f"Discord processing attempt {attempt_id} belongs to another source event"
            )
        if str(attempt["status"]) != "processing":
            raise ProfileProjectionStateError(
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
                raise ProfileProjectionIntegrityError(
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
            raise ProfileProjectionIntegrityError("profile import returned an invalid projection target")
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
            raise ProfileProjectionIntegrityError("profile projection link could not be completed")

    def _validate_profile_target(
        self,
        connection: sqlite3.Connection,
        profile_observation_id: int,
        import_event_id: int,
        projection_slot: str,
    ) -> None:
        if self._PROJECTION_TABLE not in self._TARGET_TABLES:
            raise ProfileProjectionIntegrityError("profile projection table is not allowlisted")
        row = connection.execute(
            """
            SELECT po.import_event_id, ac.normalized_name AS account,
                   sc.normalized_name AS server
            FROM profile_observations AS po
            JOIN account_contexts AS ac ON ac.id = po.account_context_id
            JOIN server_contexts AS sc ON sc.id = ac.server_context_id
            WHERE po.id = ?
            """,
            (profile_observation_id,),
        ).fetchone()
        if row is None:
            raise ProfileProjectionTargetError(
                f"projection target profile_observations:{profile_observation_id} is missing"
            )
        if int(row["import_event_id"]) != import_event_id:
            raise ProfileProjectionTargetError(
                f"projection target profile_observations:{profile_observation_id} belongs to another import event"
            )
        slot = json.loads(projection_slot)
        if row["server"] != slot["server"] or row["account"] != slot["account"]:
            raise ProfileProjectionTargetError(
                f"projection target profile_observations:{profile_observation_id} has mismatched profile scope"
            )

    @staticmethod
    def _profile_slot(server: str, account: str) -> str:
        values = {
            "account": CatalogRepository._normalize(account),
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
    def _effective_database_path(repository: Any) -> Path:
        value = getattr(repository, "_database_path", None)
        return Path(value if value is not None else DEFAULT_DATABASE_PATH).resolve()
