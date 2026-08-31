"""Transactional coordination for durable Discord server-settings projections."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from moa.database.sqlite import DEFAULT_DATABASE_PATH, run_write_transaction
from moa.models.character import ServerSettingsSnapshot
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.repositories.projection_link_repository import ProjectionLinkRepository
from moa.services.projection_authority import SERVER_SETTINGS_PROJECTION
from moa.services.projection_expectations import (
    DurableProjectionExpectationFactsError,
    PROJECTION_EXPECTATION_POLICIES,
    build_projection_expectation_facts,
    load_durable_projection_expectation_facts,
    resolve_expected_projections,
)


class SettingsProjectionCoordinatorError(RuntimeError):
    """Base error for a settings projection coordination failure."""


class SettingsProjectionStateError(SettingsProjectionCoordinatorError):
    """Raised when a source event or processing attempt is not usable."""


class SettingsProjectionIntegrityError(SettingsProjectionCoordinatorError):
    """Raised when durable settings projection state cannot be trusted."""


class SettingsProjectionTargetError(SettingsProjectionIntegrityError):
    """Raised when a settings projection target is missing or mismatched."""


class SettingsProjectionDatabasePathError(SettingsProjectionCoordinatorError, ValueError):
    """Raised when the repositories do not point at one database."""


@dataclass(frozen=True, slots=True)
class SettingsProjectionResult:
    """The durable outcome of one coordinated server-settings projection."""

    imported_count: int
    import_event_id: int
    server_settings_observation_id: int
    replay_skipped: bool
    durable_success_recorded: bool
    projection_target: tuple[str, int]


class SettingsProjectionCoordinator:
    """Own one SQLite transaction for a server-settings projection."""

    _PROJECTION_AUTHORITY = SERVER_SETTINGS_PROJECTION
    _PROJECTION_KIND = PROJECTION_EXPECTATION_POLICIES[
        "server_settings"
    ].possible_projection_kinds[0]
    _PROJECTION_TABLE = _PROJECTION_AUTHORITY.target_table
    _IMPORT_KIND = "server_settings"
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
            raise SettingsProjectionDatabasePathError(
                "catalog and Discord repositories must use the same database path"
            )
        self._database_path = catalog_path

    def coordinate_settings(
        self,
        *,
        source_event_id: int,
        attempt_id: int | None,
        settings: ServerSettingsSnapshot,
        server: str,
        raw: str,
        source: str,
        observed_at: datetime,
        finished_at: datetime,
    ) -> SettingsProjectionResult:
        """Coordinate one first-processing attempt or validate a completed replay."""
        self._validate_identity(source_event_id, "source_event_id")
        if attempt_id is not None:
            self._validate_identity(attempt_id, "attempt_id")
        observed_at = self._normalize_datetime(observed_at, "observed_at")
        finished_at = self._normalize_datetime(finished_at, "finished_at")
        projection_slot = resolve_expected_projections(
            build_projection_expectation_facts(self._IMPORT_KIND, server=server)
        ).known_expected_identities[0].projection_slot

        def coordinate_with_connection(
            connection: sqlite3.Connection,
        ) -> SettingsProjectionResult:
            projection_links = ProjectionLinkRepository(connection)
            generation_id = projection_links.resolve_current_generation_id()
            event = self._load_source_event(connection, source_event_id)
            self._validate_server_attribution(connection, source_event_id, server)
            if str(event["status"]) == "succeeded":
                if attempt_id is not None:
                    raise SettingsProjectionStateError(
                        f"Discord source event {source_event_id} has already succeeded"
                    )
                try:
                    durable_facts = load_durable_projection_expectation_facts(
                        connection, source_event_id
                    )
                except DurableProjectionExpectationFactsError:
                    return self._coordinate_replay(
                        connection,
                        event,
                        projection_slot,
                        projection_links=projection_links,
                        generation_id=generation_id,
                    )
                if durable_facts.source_family != self._IMPORT_KIND:
                    return self._coordinate_replay(
                        connection,
                        event,
                        projection_slot,
                        projection_links=projection_links,
                        generation_id=generation_id,
                    )
                durable = resolve_expected_projections(
                    durable_facts
                ).known_expected_identities
                if len(durable) != 1:
                    raise SettingsProjectionIntegrityError(
                        "durable settings expected identity is unresolved"
                    )
                return self._coordinate_replay(
                    connection,
                    event,
                    durable[0].projection_slot,
                    projection_links=projection_links,
                    generation_id=generation_id,
                )

            if attempt_id is None:
                raise SettingsProjectionStateError(
                    f"Discord source event {source_event_id} has no active processing attempt"
                )
            self._validate_active_attempt(connection, event, source_event_id, attempt_id)
            links = self._load_links(
                projection_links,
                source_event_id=source_event_id,
                generation_id=generation_id,
            )
            expected_key = (self._PROJECTION_KIND, projection_slot)
            if set(links) - {expected_key}:
                raise SettingsProjectionIntegrityError(
                    f"source event {source_event_id} has unexpected projection links"
                )
            existing = links.get(expected_key)
            if existing is not None:
                if str(existing["state"]) == "claimed":
                    raise SettingsProjectionIntegrityError(
                        f"settings projection for source event {source_event_id} is still claimed"
                    )
                raise SettingsProjectionIntegrityError(
                    f"source event {source_event_id} already has a settings projection"
                )
            self._claim_projection_link(
                projection_links,
                source_event_id=source_event_id,
                generation_id=generation_id,
                projection_slot=projection_slot,
                claimed_at=observed_at,
            )

            imported = self._catalog._import_server_settings_with_connection(
                connection,
                settings=settings,
                server=server,
                raw=raw,
                source=source,
                observed_at=observed_at,
            )
            import_event_id = self._positive_id(imported.import_event_id, "import_event_id")
            observation_id = self._positive_id(
                imported.server_settings_observation_id,
                "server_settings_observation_id",
            )
            self._validate_settings_target(
                connection,
                observation_id=observation_id,
                import_event_id=import_event_id,
                projection_slot=projection_slot,
            )
            target = (self._PROJECTION_TABLE, observation_id)
            self._complete_projection_link(
                projection_links,
                source_event_id=source_event_id,
                generation_id=generation_id,
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
                raise SettingsProjectionStateError(
                    f"processing success was not recorded for source event {source_event_id}"
                )
            return SettingsProjectionResult(
                imported_count=1,
                import_event_id=import_event_id,
                server_settings_observation_id=observation_id,
                replay_skipped=False,
                durable_success_recorded=True,
                projection_target=target,
            )

        return run_write_transaction(self._database_path, coordinate_with_connection)

    def _coordinate_replay(
        self,
        connection: sqlite3.Connection,
        event: sqlite3.Row,
        projection_slot: str,
        *,
        projection_links: ProjectionLinkRepository,
        generation_id: int,
    ) -> SettingsProjectionResult:
        import_event_id = event["legacy_import_event_id"]
        if import_event_id is None:
            raise SettingsProjectionIntegrityError(
                f"succeeded source event {event['id']} has no legacy import event"
            )
        import_event = connection.execute(
            "SELECT id, kind FROM import_events WHERE id = ?", (int(import_event_id),)
        ).fetchone()
        if import_event is None or str(import_event["kind"]) != self._IMPORT_KIND:
            raise SettingsProjectionTargetError(
                f"legacy settings import event {import_event_id} for source event {event['id']} is missing or wrong"
            )

        links = self._load_links(
            projection_links,
            source_event_id=int(event["id"]),
            generation_id=generation_id,
        )
        key = (self._PROJECTION_KIND, projection_slot)
        if set(links) != {key}:
            raise SettingsProjectionIntegrityError(
                f"succeeded source event {event['id']} has an inconsistent settings projection link"
            )
        link = links[key]
        if str(link["state"]) != "completed":
            raise SettingsProjectionIntegrityError(
                f"settings projection for source event {event['id']} is not completed"
            )
        if str(link["projection_table"]) != self._PROJECTION_TABLE:
            raise SettingsProjectionIntegrityError(
                f"succeeded source event {event['id']} has an inconsistent settings projection link"
            )
        projection_row_id = link["projection_row_id"]
        if projection_row_id is None:
            raise SettingsProjectionTargetError(
                f"settings projection for source event {event['id']} has no target"
            )
        observation_id = self._positive_id(projection_row_id, "projection_row_id")
        self._validate_settings_target(
            connection,
            observation_id=observation_id,
            import_event_id=int(import_event_id),
            projection_slot=projection_slot,
        )
        return SettingsProjectionResult(
            imported_count=0,
            import_event_id=int(import_event_id),
            server_settings_observation_id=observation_id,
            replay_skipped=True,
            durable_success_recorded=True,
            projection_target=(self._PROJECTION_TABLE, observation_id),
        )

    @staticmethod
    def _load_source_event(connection: sqlite3.Connection, source_event_id: int) -> sqlite3.Row:
        event = connection.execute(
            "SELECT * FROM discord_source_events WHERE id = ?", (source_event_id,)
        ).fetchone()
        if event is None:
            raise SettingsProjectionStateError(
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
            raise SettingsProjectionStateError(
                f"Discord source event {source_event_id} is not processing"
            )
        attempt = connection.execute(
            "SELECT source_event_id, status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()
        if attempt is None:
            raise SettingsProjectionStateError(
                f"Discord processing attempt {attempt_id} was not found"
            )
        if int(attempt["source_event_id"]) != source_event_id:
            raise SettingsProjectionStateError(
                f"Discord processing attempt {attempt_id} belongs to another source event"
            )
        if str(attempt["status"]) != "processing":
            raise SettingsProjectionStateError(
                f"Discord processing attempt {attempt_id} is not processing"
            )

    @staticmethod
    def _validate_server_attribution(
        connection: sqlite3.Connection,
        source_event_id: int,
        server: str,
    ) -> None:
        attribution = connection.execute(
            """
            SELECT status, server_name
            FROM discord_source_event_server_attributions
            WHERE source_event_id = ?
            """,
            (source_event_id,),
        ).fetchone()
        if attribution is None:
            raise SettingsProjectionStateError(
                f"source event {source_event_id} has no persisted server attribution"
            )
        if str(attribution["status"]) != "resolved":
            raise SettingsProjectionStateError(
                f"source event {source_event_id} has non-resolved server attribution"
            )
        if attribution["server_name"] != server:
            raise SettingsProjectionStateError(
                f"source event {source_event_id} is attributed to another server"
            )

    @staticmethod
    def _load_links(
        repository: ProjectionLinkRepository,
        *,
        source_event_id: int,
        generation_id: int,
    ) -> dict[tuple[str, str], sqlite3.Row]:
        links: dict[tuple[str, str], sqlite3.Row] = {}
        for link in repository.load_links(
            source_event_id=source_event_id,
            generation_id=generation_id,
        ):
            key = (str(link["projection_kind"]), str(link["projection_slot"]))
            if key in links:
                raise SettingsProjectionIntegrityError(
                    f"duplicate projection link identity for source event {source_event_id}"
                )
            links[key] = link
        return links

    def _claim_projection_link(
        self,
        repository: ProjectionLinkRepository,
        *,
        source_event_id: int,
        generation_id: int,
        projection_slot: str,
        claimed_at: datetime,
    ) -> None:
        repository.claim_link(
            source_event_id=source_event_id,
            generation_id=generation_id,
            projection_kind=self._PROJECTION_KIND,
            projection_slot=projection_slot,
            claimed_at=claimed_at,
        )

    def _complete_projection_link(
        self,
        repository: ProjectionLinkRepository,
        *,
        source_event_id: int,
        generation_id: int,
        projection_slot: str,
        target: tuple[str, int],
        completed_at: datetime,
    ) -> None:
        table, row_id = target
        if table not in self._TARGET_TABLES or row_id <= 0:
            raise SettingsProjectionIntegrityError("settings import returned an invalid projection target")
        repository.complete_claimed_link(
            source_event_id=source_event_id,
            generation_id=generation_id,
            projection_kind=self._PROJECTION_KIND,
            projection_slot=projection_slot,
            projection_table=table,
            projection_row_id=row_id,
            completed_at=completed_at,
        )

    def _validate_settings_target(
        self,
        connection: sqlite3.Connection,
        *,
        observation_id: int,
        import_event_id: int,
        projection_slot: str,
    ) -> None:
        if self._PROJECTION_TABLE not in self._TARGET_TABLES:
            raise SettingsProjectionIntegrityError("settings projection table is not allowlisted")
        row = connection.execute(
            """
            SELECT sso.import_event_id, sc.normalized_name AS server
            FROM server_settings_observations AS sso
            JOIN server_contexts AS sc ON sc.id = sso.server_context_id
            WHERE sso.id = ?
            """,
            (observation_id,),
        ).fetchone()
        if row is None:
            raise SettingsProjectionTargetError(
                f"projection target server_settings_observations:{observation_id} is missing"
            )
        if int(row["import_event_id"]) != import_event_id:
            raise SettingsProjectionTargetError(
                f"projection target server_settings_observations:{observation_id} belongs to another import event"
            )
        import_event = connection.execute(
            "SELECT kind FROM import_events WHERE id = ?", (import_event_id,)
        ).fetchone()
        if import_event is None or str(import_event["kind"]) != self._IMPORT_KIND:
            raise SettingsProjectionTargetError(
                f"import event {import_event_id} is missing or has the wrong kind"
            )
        slot = json.loads(projection_slot)
        if row["server"] != slot["server"]:
            raise SettingsProjectionTargetError(
                f"projection target server_settings_observations:{observation_id} has mismatched server scope"
            )

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
            raise SettingsProjectionIntegrityError(f"settings import returned an invalid {field_name}")
        return int(value)

    @staticmethod
    def _effective_database_path(repository: Any) -> Path:
        value = getattr(repository, "_database_path", None)
        return Path(value if value is not None else DEFAULT_DATABASE_PATH).resolve()
