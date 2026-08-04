"""Transactional coordination for durable Discord `$dl` projections."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from moa.database.sqlite import DEFAULT_DATABASE_PATH, run_write_transaction
from moa.models.character import DisableListSnapshot
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository


class DisableListProjectionCoordinatorError(RuntimeError):
    """Base error for a disablelist projection coordination failure."""


class DisableListProjectionStateError(DisableListProjectionCoordinatorError):
    """Raised when a source event or processing attempt is not usable."""


class DisableListProjectionIntegrityError(DisableListProjectionCoordinatorError):
    """Raised when durable disablelist projection state cannot be trusted."""


class DisableListProjectionTargetError(DisableListProjectionIntegrityError):
    """Raised when a disablelist projection target is missing or mismatched."""


class DisableListProjectionDatabasePathError(DisableListProjectionCoordinatorError, ValueError):
    """Raised when the repositories do not point at one database."""


@dataclass(frozen=True, slots=True)
class DisableListProjectionResult:
    """The durable outcome of one coordinated `$dl` disablelist projection."""

    imported_count: int
    import_event_id: int
    disablelist_observation_id: int
    replay_skipped: bool
    durable_success_recorded: bool
    projection_target: tuple[str, int]


class DisableListProjectionCoordinator:
    """Own one SQLite transaction for an account-scoped `$dl` projection."""

    _PROJECTION_KIND = "catalog.disablelist"
    _PROJECTION_TABLE = "disablelist_observations"
    _IMPORT_KIND = "disablelist"
    _TARGET_TABLES = frozenset({_PROJECTION_TABLE})

    def __init__(self, catalog_repository: CatalogRepository, discord_message_repository: DiscordMessageRepository) -> None:
        self._catalog = catalog_repository
        self._discord = discord_message_repository
        catalog_path = self._effective_database_path(catalog_repository)
        discord_path = self._effective_database_path(discord_message_repository)
        if catalog_path != discord_path:
            raise DisableListProjectionDatabasePathError(
                "catalog and Discord repositories must use the same database path"
            )
        self._database_path = catalog_path

    def coordinate_disablelist(
        self, *, source_event_id: int, attempt_id: int | None, state: DisableListSnapshot,
        server: str, account: str, raw: str, source: str, observed_at: datetime, finished_at: datetime,
    ) -> DisableListProjectionResult:
        """Coordinate one first-processing attempt or validate a completed replay."""
        self._validate_identity(source_event_id, "source_event_id")
        if attempt_id is not None:
            self._validate_identity(attempt_id, "attempt_id")
        observed_at = self._normalize_datetime(observed_at, "observed_at")
        finished_at = self._normalize_datetime(finished_at, "finished_at")
        projection_slot = self._disablelist_slot(server, account)

        def coordinate_with_connection(
            connection: sqlite3.Connection,
        ) -> DisableListProjectionResult:
            event = self._load_source_event(connection, source_event_id)
            self._validate_attribution(connection, source_event_id=source_event_id, server=server, account=account)
            self._validate_lifecycle(connection, event, source_event_id, attempt_id)
            if str(event["status"]) == "succeeded":
                return self._coordinate_replay(connection, event, projection_slot, state=state)
            links = self._load_links(connection, source_event_id)
            key = (self._PROJECTION_KIND, projection_slot)
            if set(links) - {key}:
                raise DisableListProjectionIntegrityError(f"source event {source_event_id} has unexpected projection links")
            existing = links.get(key)
            if existing is not None:
                raise DisableListProjectionIntegrityError(
                    f"disablelist projection for source event {source_event_id} is already {existing['state']}"
                )
            self._claim_projection_link(connection, source_event_id, projection_slot, observed_at)
            imported = self._catalog._import_disablelist_with_connection(
                connection, state=state, server=server, account=account, raw=raw, source=source, observed_at=observed_at
            )
            import_event_id = self._positive_id(imported.import_event_id, "import_event_id")
            observation_id = self._positive_id(imported.disablelist_observation_id, "disablelist_observation_id")
            self._validate_disablelist_target(connection, observation_id, import_event_id, projection_slot, state)
            target = (self._PROJECTION_TABLE, observation_id)
            self._complete_projection_link(connection, source_event_id, projection_slot, target, finished_at)
            success = self._discord._mark_processing_success_with_connection(
                connection, source_event_id=source_event_id, attempt_id=attempt_id,
                finished_at=finished_at, legacy_import_event_id=import_event_id,
            )
            if success.attempt_status != "succeeded" or success.source_event_status != "succeeded":
                raise DisableListProjectionStateError(f"processing success was not recorded for source event {source_event_id}")
            return DisableListProjectionResult(1, import_event_id, observation_id, False, True, target)

        return run_write_transaction(self._database_path, coordinate_with_connection)

    def _coordinate_replay(self, connection: sqlite3.Connection, event: sqlite3.Row, projection_slot: str, *, state: DisableListSnapshot) -> DisableListProjectionResult:
        import_event_id = self._positive_id(event["legacy_import_event_id"], "legacy_import_event_id")
        import_event = connection.execute("SELECT id, kind FROM import_events WHERE id = ?", (import_event_id,)).fetchone()
        if import_event is None or str(import_event["kind"]) != self._IMPORT_KIND:
            raise DisableListProjectionTargetError("legacy disablelist import event is missing or wrong")
        links = self._load_links(connection, int(event["id"]))
        key = (self._PROJECTION_KIND, projection_slot)
        if set(links) != {key}:
            raise DisableListProjectionIntegrityError("succeeded source event has an inconsistent disablelist projection link")
        link = links[key]
        if str(link["state"]) != "completed" or str(link["projection_table"]) != self._PROJECTION_TABLE:
            raise DisableListProjectionIntegrityError("disablelist projection link is not completed")
        observation_id = self._positive_id(link["projection_row_id"], "projection_row_id")
        self._validate_disablelist_target(connection, observation_id, import_event_id, projection_slot, state)
        return DisableListProjectionResult(0, import_event_id, observation_id, True, True, (self._PROJECTION_TABLE, observation_id))

    def _validate_attribution(self, connection: sqlite3.Connection, *, source_event_id: int, server: str, account: str) -> None:
        server_attribution = self._discord._get_server_attribution_with_connection(connection, source_event_id)
        if server_attribution is None or server_attribution.status != "resolved":
            raise DisableListProjectionIntegrityError("source event has no resolved server attribution")
        if server_attribution.server_name is None or CatalogRepository._normalize(server_attribution.server_name) != CatalogRepository._normalize(server):
            raise DisableListProjectionIntegrityError("source event is attributed to another server")
        account_attribution = self._discord._get_account_attribution_with_connection(connection, source_event_id)
        if account_attribution is None or account_attribution.status != "resolved":
            raise DisableListProjectionIntegrityError("source event has no resolved account attribution")
        if account_attribution.server_name is None or CatalogRepository._normalize(account_attribution.server_name) != CatalogRepository._normalize(server_attribution.server_name):
            raise DisableListProjectionIntegrityError("source event has mismatched account attribution server")
        if account_attribution.account_name is None or CatalogRepository._normalize(account_attribution.account_name) != CatalogRepository._normalize(account):
            raise DisableListProjectionIntegrityError("source event is attributed to another account")

    @staticmethod
    def _load_source_event(connection: sqlite3.Connection, source_event_id: int) -> sqlite3.Row:
        event = connection.execute("SELECT * FROM discord_source_events WHERE id = ?", (source_event_id,)).fetchone()
        if event is None:
            raise DisableListProjectionStateError(f"Discord source event {source_event_id} was not found")
        return event

    @staticmethod
    def _validate_lifecycle(connection: sqlite3.Connection, event: sqlite3.Row, source_event_id: int, attempt_id: int | None) -> None:
        if str(event["status"]) == "succeeded":
            if attempt_id is not None:
                raise DisableListProjectionStateError("succeeded source event received an attempt")
            return
        if str(event["status"]) != "processing" or attempt_id is None:
            raise DisableListProjectionStateError("source event has no active processing attempt")
        attempt = connection.execute("SELECT source_event_id, status FROM discord_processing_attempts WHERE id = ?", (attempt_id,)).fetchone()
        if attempt is None or int(attempt["source_event_id"]) != source_event_id or str(attempt["status"]) != "processing":
            raise DisableListProjectionStateError("processing attempt is not active for source event")

    @staticmethod
    def _load_links(connection: sqlite3.Connection, source_event_id: int) -> dict[tuple[str, str], sqlite3.Row]:
        links: dict[tuple[str, str], sqlite3.Row] = {}
        for link in connection.execute("SELECT * FROM discord_projection_links WHERE source_event_id = ?", (source_event_id,)).fetchall():
            key = (str(link["projection_kind"]), str(link["projection_slot"]))
            if key in links:
                raise DisableListProjectionIntegrityError("duplicate projection link identity")
            links[key] = link
        return links

    def _claim_projection_link(self, connection: sqlite3.Connection, source_event_id: int, projection_slot: str, claimed_at: datetime) -> None:
        value = claimed_at.isoformat()
        connection.execute(
            """INSERT INTO discord_projection_links (source_event_id, projection_kind, projection_slot, projection_table, projection_row_id, state, claimed_at, completed_at, created_at, updated_at)
               VALUES (?, ?, ?, NULL, NULL, 'claimed', ?, NULL, ?, ?)""",
            (source_event_id, self._PROJECTION_KIND, projection_slot, value, value, value),
        )

    def _complete_projection_link(self, connection: sqlite3.Connection, source_event_id: int, projection_slot: str, target: tuple[str, int], completed_at: datetime) -> None:
        table, row_id = target
        if table not in self._TARGET_TABLES or isinstance(row_id, bool) or row_id <= 0:
            raise DisableListProjectionIntegrityError("disablelist import returned an invalid projection target")
        value = completed_at.isoformat()
        updated = connection.execute(
            """UPDATE discord_projection_links SET projection_table = ?, projection_row_id = ?, state = 'completed', completed_at = ?, updated_at = ?
               WHERE source_event_id = ? AND projection_kind = ? AND projection_slot = ? AND state = 'claimed'""",
            (table, row_id, value, value, source_event_id, self._PROJECTION_KIND, projection_slot),
        )
        if updated.rowcount != 1:
            raise DisableListProjectionIntegrityError("disablelist projection link could not be completed")

    def _validate_disablelist_target(self, connection: sqlite3.Connection, observation_id: int, import_event_id: int, projection_slot: str, state: DisableListSnapshot) -> None:
        row = connection.execute(
            """SELECT d.import_event_id, d.slots_used, d.slots_capacity, d.total_disabled, d.disabled_wa, d.disabled_ha, d.disabled_wg, d.disabled_hg, d.wa_pool_limit, d.ha_pool_limit, d.western_disabled, d.irl_disabled, d.entries_json, ac.normalized_name AS account, sc.normalized_name AS server
               FROM disablelist_observations AS d JOIN account_contexts AS ac ON ac.id = d.account_context_id JOIN server_contexts AS sc ON sc.id = ac.server_context_id WHERE d.id = ?""",
            (observation_id,),
        ).fetchone()
        if row is None or int(row["import_event_id"]) != import_event_id:
            raise DisableListProjectionTargetError("disablelist observation is missing or owned by another import event")
        event = connection.execute("SELECT kind FROM import_events WHERE id = ?", (import_event_id,)).fetchone()
        if event is None or str(event["kind"]) != self._IMPORT_KIND:
            raise DisableListProjectionTargetError("disablelist import event is missing or wrong kind")
        try:
            slot = json.loads(projection_slot)
        except (TypeError, ValueError) as error:
            raise DisableListProjectionTargetError("disablelist projection slot is not valid JSON") from error
        if not isinstance(slot, dict) or set(slot) != {"account", "server"} or row["server"] != slot["server"] or row["account"] != slot["account"]:
            raise DisableListProjectionTargetError("disablelist observation has mismatched scope")
        fields = ("slots_used", "slots_capacity", "total_disabled", "disabled_wa", "disabled_ha", "disabled_wg", "disabled_hg", "wa_pool_limit", "ha_pool_limit", "western_disabled", "irl_disabled")
        for field in fields:
            if row[field] != getattr(state, field):
                raise DisableListProjectionTargetError(f"disablelist observation has mismatched {field}")
        if row["entries_json"] != json.dumps([entry.model_dump() for entry in state.entries]):
            raise DisableListProjectionTargetError("disablelist observation has mismatched entries")

    @staticmethod
    def _disablelist_slot(server: str, account: str) -> str:
        return json.dumps({"account": CatalogRepository._normalize(account), "server": CatalogRepository._normalize(server)}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

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
            raise DisableListProjectionIntegrityError(f"disablelist import returned an invalid {field_name}")
        return int(value)

    @staticmethod
    def _effective_database_path(repository: Any) -> Path:
        value = getattr(repository, "_database_path", None)
        return Path(value if value is not None else DEFAULT_DATABASE_PATH).resolve()
