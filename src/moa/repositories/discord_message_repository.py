"""Atomic durable receipt of Discord message observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from pathlib import Path
from typing import Literal

from moa.database.sqlite import connect
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey
from moa.repositories.catalog_repository import CatalogRepository


class DiscordMessageReceiveConflictError(RuntimeError):
    """Raised when a durable Discord identity conflicts with stored data."""


class DiscordMessageProcessingError(RuntimeError):
    """Base class for durable Discord processing lifecycle errors."""


class DiscordMessageProcessingNotFoundError(DiscordMessageProcessingError):
    """Raised when a requested source event or processing attempt is missing."""


class DiscordMessageProcessingConflictError(DiscordMessageProcessingError):
    """Raised when a processing lifecycle transition is not currently valid."""


class DiscordSourceEventServerAttributionNotFoundError(RuntimeError):
    """Raised when a source event is missing for an attribution write."""


class DiscordSourceEventServerAttributionValidationError(ValueError):
    """Raised when an attribution status and server name do not agree."""


class DiscordSourceEventServerAttributionConflictError(RuntimeError):
    """Raised when an immutable attribution decision conflicts with a new one."""


class DiscordSourceEventAccountAttributionNotFoundError(RuntimeError):
    """Raised when a source event is missing for an account attribution write."""


class DiscordSourceEventAccountAttributionValidationError(ValueError):
    """Raised when an account attribution status and identity do not agree."""


class DiscordSourceEventAccountAttributionConflictError(RuntimeError):
    """Raised when an immutable account attribution conflicts with a new one."""


@dataclass(frozen=True, slots=True)
class ReceivedMessageEvent:
    """The durable rows affected by one :meth:`receive_message` call."""

    aggregate_id: int
    revision_id: int
    source_event_id: int
    aggregate_created: bool
    revision_created: bool
    source_event_created: bool
    delivery_count: int
    status: str
    event_key: str


@dataclass(frozen=True, slots=True)
class ProcessingAttemptResult:
    """The durable processing state after one lifecycle operation."""

    source_event_id: int
    attempt_id: int
    attempt_number: int
    attempt_status: str
    source_event_status: str
    retryable: bool
    started_at: datetime
    finished_at: datetime | None
    lease_expires_at: datetime | None
    parser_version: str
    router_version: str
    failure_code: str | None
    failure_detail: str | None
    legacy_import_event_id: int | None


@dataclass(frozen=True, slots=True)
class DiscordSourceEventServerAttribution:
    """The durable server attribution decision for one Discord source event."""

    source_event_id: int
    status: str
    server_name: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DiscordSourceEventAccountAttribution:
    """The durable account attribution decision for one Discord source event."""

    source_event_id: int
    status: str
    server_name: str | None
    account_name: str | None
    created_at: datetime
    updated_at: datetime


class DiscordMessageRepository:
    """Persist Discord message aggregates, revisions, and source events."""

    def __init__(self, database_path: Path | None = None) -> None:
        self._database_path = database_path
        # The current migration bootstrap is owned by CatalogRepository.  Reuse
        # it without exposing any catalog repository methods here.
        CatalogRepository(database_path)

    def receive_message(
        self,
        *,
        aggregate_key: MessageAggregateKey,
        revision_key: MessageRevisionKey,
        event_key: str,
        event_kind: str,
        raw_text: str,
        payload_json: str | None,
        payload_capture_version: str | None,
        source_observed_at: datetime | None,
        received_at: datetime,
    ) -> ReceivedMessageEvent:
        """Atomically insert or locate one message, revision, and source event."""
        self._validate_receive(
            aggregate_key=aggregate_key,
            revision_key=revision_key,
            event_key=event_key,
            event_kind=event_kind,
            raw_text=raw_text,
            source_observed_at=source_observed_at,
            received_at=received_at,
        )
        received_at_value = received_at.isoformat()
        source_observed_at_value = (
            source_observed_at.isoformat() if source_observed_at is not None else None
        )

        with self._connection() as connection:
            aggregate, aggregate_created = self._insert_or_get_aggregate(
                connection, aggregate_key, received_at_value
            )
            revision, revision_created = self._insert_or_get_revision(
                connection, revision_key, int(aggregate["id"]), received_at_value, source_observed_at_value
            )
            event, source_event_created = self._insert_or_get_source_event(
                connection,
                event_key=event_key,
                revision_id=int(revision["id"]),
                event_kind=event_kind,
                raw_text=raw_text,
                payload_json=payload_json,
                payload_capture_version=payload_capture_version,
                source_observed_at=source_observed_at_value,
                received_at=received_at_value,
            )

            if not source_event_created:
                self._verify_existing_event(
                    event,
                    event_key=event_key,
                    revision_id=int(revision["id"]),
                    event_kind=event_kind,
                    raw_text=raw_text,
                    payload_json=payload_json,
                    payload_capture_version=payload_capture_version,
                    source_observed_at=source_observed_at_value,
                )
                connection.execute(
                    """
                    UPDATE discord_message_aggregates
                    SET last_received_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (received_at_value, received_at_value, int(aggregate["id"])),
                )
                connection.execute(
                    """
                    UPDATE discord_message_revisions
                    SET last_received_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (received_at_value, received_at_value, int(revision["id"])),
                )
                connection.execute(
                    """
                    UPDATE discord_source_events
                    SET delivery_count = delivery_count + 1,
                        last_seen_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (received_at_value, received_at_value, int(event["id"])),
                )
                event = connection.execute(
                    "SELECT id, delivery_count, status, event_key FROM discord_source_events WHERE id = ?",
                    (int(event["id"]),),
                ).fetchone()

            return ReceivedMessageEvent(
                aggregate_id=int(aggregate["id"]),
                revision_id=int(revision["id"]),
                source_event_id=int(event["id"]),
                aggregate_created=aggregate_created,
                revision_created=revision_created,
                source_event_created=source_event_created,
                delivery_count=int(event["delivery_count"]),
                status=str(event["status"]),
                event_key=str(event["event_key"]),
            )

    def begin_processing_attempt(
        self,
        *,
        source_event_id: int,
        parser_version: str,
        router_version: str,
        started_at: datetime,
        lease_expires_at: datetime | None = None,
    ) -> ProcessingAttemptResult:
        """Atomically create the next active attempt for one source event."""
        self._validate_processing_identity(source_event_id=source_event_id)
        self._validate_version(parser_version, "parser_version")
        self._validate_version(router_version, "router_version")
        normalized_started_at = self._normalize_processing_datetime(started_at, "started_at")
        normalized_lease_expires_at = (
            self._normalize_processing_datetime(lease_expires_at, "lease_expires_at")
            if lease_expires_at is not None
            else None
        )
        started_at_value = normalized_started_at.isoformat()
        lease_expires_at_value = (
            normalized_lease_expires_at.isoformat() if normalized_lease_expires_at is not None else None
        )

        with self._connection() as connection:
            event = connection.execute(
                "SELECT * FROM discord_source_events WHERE id = ?",
                (source_event_id,),
            ).fetchone()
            if event is None:
                raise DiscordMessageProcessingNotFoundError(
                    f"Discord source event {source_event_id} was not found"
                )

            latest_attempt = connection.execute(
                """
                SELECT * FROM discord_processing_attempts
                WHERE source_event_id = ?
                ORDER BY attempt_number DESC
                LIMIT 1
                """,
                (source_event_id,),
            ).fetchone()
            self._validate_begin_state(event, latest_attempt, source_event_id)
            attempt_number = (
                int(latest_attempt["attempt_number"]) + 1 if latest_attempt is not None else 1
            )
            cursor = connection.execute(
                """
                INSERT INTO discord_processing_attempts (
                    source_event_id, attempt_number, status, retryable,
                    parser_version, router_version, started_at, finished_at,
                    lease_expires_at, failure_code, failure_detail, created_at
                ) VALUES (?, ?, 'processing', 0, ?, ?, ?, NULL, ?, NULL, NULL, ?)
                """,
                (
                    source_event_id,
                    attempt_number,
                    parser_version,
                    router_version,
                    started_at_value,
                    lease_expires_at_value,
                    started_at_value,
                ),
            )
            attempt_id = int(cursor.lastrowid)
            updated = connection.execute(
                """
                UPDATE discord_source_events
                SET status = 'processing', updated_at = ?
                WHERE id = ?
                """,
                (started_at_value, source_event_id),
            )
            if updated.rowcount != 1:
                raise DiscordMessageProcessingConflictError(
                    f"Discord source event {source_event_id} could not be marked processing"
                )
            return self._processing_result(connection, source_event_id, attempt_id)

    def mark_processing_success(
        self,
        *,
        source_event_id: int,
        attempt_id: int,
        finished_at: datetime,
        legacy_import_event_id: int | None = None,
    ) -> ProcessingAttemptResult:
        """Atomically complete one active attempt successfully."""
        self._validate_processing_identity(source_event_id=source_event_id)
        self._validate_processing_identity(attempt_id=attempt_id)
        normalized_finished_at = self._normalize_processing_datetime(finished_at, "finished_at")
        with self._connection() as connection:
            return self._mark_processing_success_with_connection(
                connection,
                source_event_id=source_event_id,
                attempt_id=attempt_id,
                finished_at=normalized_finished_at,
                legacy_import_event_id=legacy_import_event_id,
            )

    def _mark_processing_success_with_connection(
        self,
        connection: sqlite3.Connection,
        *,
        source_event_id: int,
        attempt_id: int,
        finished_at: datetime,
        legacy_import_event_id: int | None,
    ) -> ProcessingAttemptResult:
        """Complete one active attempt without owning the surrounding transaction."""
        self._validate_processing_identity(source_event_id=source_event_id)
        self._validate_processing_identity(attempt_id=attempt_id)
        normalized_finished_at = self._normalize_processing_datetime(finished_at, "finished_at")
        event, attempt = self._load_processing_rows(
            connection, source_event_id=source_event_id, attempt_id=attempt_id
        )
        self._validate_completion_state(event, attempt, source_event_id, attempt_id)
        self._validate_finished_at(attempt, normalized_finished_at)
        if legacy_import_event_id is not None:
            self._validate_processing_identity(legacy_import_event_id=legacy_import_event_id)
            import_event = connection.execute(
                "SELECT 1 FROM import_events WHERE id = ?",
                (legacy_import_event_id,),
            ).fetchone()
            if import_event is None:
                raise DiscordMessageProcessingNotFoundError(
                    f"Legacy import event {legacy_import_event_id} was not found"
                )
        finished_at_value = normalized_finished_at.isoformat()
        updated_attempt = connection.execute(
            """
            UPDATE discord_processing_attempts
            SET status = 'succeeded', retryable = 0, finished_at = ?,
                failure_code = NULL, failure_detail = NULL
            WHERE id = ? AND source_event_id = ? AND status = 'processing'
            """,
            (finished_at_value, attempt_id, source_event_id),
        )
        if updated_attempt.rowcount != 1:
            raise DiscordMessageProcessingConflictError(
                f"Discord processing attempt {attempt_id} is no longer processing"
            )
        updated_event = connection.execute(
            """
            UPDATE discord_source_events
            SET status = 'succeeded', legacy_import_event_id = ?, updated_at = ?
            WHERE id = ? AND status = 'processing'
            """,
            (legacy_import_event_id, finished_at_value, source_event_id),
        )
        if updated_event.rowcount != 1:
            raise DiscordMessageProcessingConflictError(
                f"Discord source event {source_event_id} is no longer processing"
            )
        return self._processing_result(connection, source_event_id, attempt_id)

    def mark_processing_failure(
        self,
        *,
        source_event_id: int,
        attempt_id: int,
        status: Literal["failed", "unresolved_attribution"],
        retryable: bool,
        failure_code: str | None,
        failure_detail: str | None,
        finished_at: datetime,
    ) -> ProcessingAttemptResult:
        """Atomically complete one active attempt with a processing failure."""
        self._validate_processing_identity(source_event_id=source_event_id)
        self._validate_processing_identity(attempt_id=attempt_id)
        if status not in {"failed", "unresolved_attribution"}:
            raise ValueError("status must be 'failed' or 'unresolved_attribution'")
        if not isinstance(retryable, bool):
            raise TypeError("retryable must be a bool")
        if failure_code is not None and not isinstance(failure_code, str):
            raise TypeError("failure_code must be a string or None")
        if failure_detail is not None and not isinstance(failure_detail, str):
            raise TypeError("failure_detail must be a string or None")
        normalized_finished_at = self._normalize_processing_datetime(finished_at, "finished_at")

        with self._connection() as connection:
            event, attempt = self._load_processing_rows(
                connection, source_event_id=source_event_id, attempt_id=attempt_id
            )
            self._validate_completion_state(event, attempt, source_event_id, attempt_id)
            self._validate_finished_at(attempt, normalized_finished_at)
            updated_attempt = connection.execute(
                """
                UPDATE discord_processing_attempts
                SET status = ?, retryable = ?, finished_at = ?,
                    failure_code = ?, failure_detail = ?
                WHERE id = ? AND source_event_id = ? AND status = 'processing'
                """,
                (
                    status,
                    int(retryable),
                    normalized_finished_at.isoformat(),
                    failure_code,
                    failure_detail,
                    attempt_id,
                    source_event_id,
                ),
            )
            if updated_attempt.rowcount != 1:
                raise DiscordMessageProcessingConflictError(
                    f"Discord processing attempt {attempt_id} is no longer processing"
                )
            updated_event = connection.execute(
                """
                UPDATE discord_source_events
                SET status = ?, updated_at = ?
                WHERE id = ? AND status = 'processing'
                """,
                (status, normalized_finished_at.isoformat(), source_event_id),
            )
            if updated_event.rowcount != 1:
                raise DiscordMessageProcessingConflictError(
                    f"Discord source event {source_event_id} is no longer processing"
                )
            return self._processing_result(connection, source_event_id, attempt_id)

    def get_server_attribution(
        self, source_event_id: int
    ) -> DiscordSourceEventServerAttribution | None:
        """Read the current durable server attribution for one source event."""
        self._validate_processing_identity(source_event_id=source_event_id)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT source_event_id, status, server_name, created_at, updated_at
                FROM discord_source_event_server_attributions
                WHERE source_event_id = ?
                """,
                (source_event_id,),
            ).fetchone()
            return self._server_attribution_result(row)

    def record_server_attribution(
        self,
        source_event_id: int,
        *,
        status: Literal["resolved", "unresolved", "ambiguous"],
        server_name: str | None,
        recorded_at: datetime,
    ) -> DiscordSourceEventServerAttribution:
        """Record one durable server attribution decision with fail-closed replay."""
        self._validate_processing_identity(source_event_id=source_event_id)
        self._validate_server_attribution(status=status, server_name=server_name)
        normalized_recorded_at = self._normalize_processing_datetime(recorded_at, "recorded_at")
        recorded_at_value = normalized_recorded_at.isoformat()

        with self._connection() as connection:
            source_event = connection.execute(
                "SELECT 1 FROM discord_source_events WHERE id = ?",
                (source_event_id,),
            ).fetchone()
            if source_event is None:
                raise DiscordSourceEventServerAttributionNotFoundError(
                    f"Discord source event {source_event_id} was not found"
                )

            existing = connection.execute(
                """
                SELECT source_event_id, status, server_name, created_at, updated_at
                FROM discord_source_event_server_attributions
                WHERE source_event_id = ?
                """,
                (source_event_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO discord_source_event_server_attributions (
                        source_event_id, status, server_name, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        source_event_id,
                        status,
                        server_name,
                        recorded_at_value,
                        recorded_at_value,
                    ),
                )
            else:
                self._validate_server_attribution_transition(
                    existing_status=str(existing["status"]),
                    existing_server_name=(
                        str(existing["server_name"])
                        if existing["server_name"] is not None
                        else None
                    ),
                    status=status,
                    server_name=server_name,
                    source_event_id=source_event_id,
                )
                if str(existing["status"]) != status:
                    connection.execute(
                        """
                        UPDATE discord_source_event_server_attributions
                        SET status = ?, server_name = ?, updated_at = ?
                        WHERE source_event_id = ?
                        """,
                        (status, server_name, recorded_at_value, source_event_id),
                    )

            row = connection.execute(
                """
                SELECT source_event_id, status, server_name, created_at, updated_at
                FROM discord_source_event_server_attributions
                WHERE source_event_id = ?
                """,
                (source_event_id,),
            ).fetchone()
            result = self._server_attribution_result(row)
            if result is None:
                raise RuntimeError(
                    "Inserted Discord server attribution could not be reloaded"
                )
            return result

    def get_account_attribution(
        self, source_event_id: int
    ) -> DiscordSourceEventAccountAttribution | None:
        """Read the current durable account attribution for one source event."""
        self._validate_processing_identity(source_event_id=source_event_id)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT source_event_id, status, server_name, account_name,
                       created_at, updated_at
                FROM discord_source_event_account_attributions
                WHERE source_event_id = ?
                """,
                (source_event_id,),
            ).fetchone()
            return self._account_attribution_result(row)

    def record_account_attribution(
        self,
        source_event_id: int,
        *,
        status: Literal["resolved", "unresolved", "ambiguous"],
        server_name: str | None,
        account_name: str | None,
        recorded_at: datetime,
    ) -> DiscordSourceEventAccountAttribution:
        """Record one durable account attribution decision with fail-closed replay."""
        self._validate_processing_identity(source_event_id=source_event_id)
        self._validate_account_attribution(
            status=status, server_name=server_name, account_name=account_name
        )
        normalized_recorded_at = self._normalize_processing_datetime(recorded_at, "recorded_at")
        recorded_at_value = normalized_recorded_at.isoformat()

        with self._connection() as connection:
            source_event = connection.execute(
                "SELECT 1 FROM discord_source_events WHERE id = ?",
                (source_event_id,),
            ).fetchone()
            if source_event is None:
                raise DiscordSourceEventAccountAttributionNotFoundError(
                    f"Discord source event {source_event_id} was not found"
                )

            existing = connection.execute(
                """
                SELECT source_event_id, status, server_name, account_name,
                       created_at, updated_at
                FROM discord_source_event_account_attributions
                WHERE source_event_id = ?
                """,
                (source_event_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO discord_source_event_account_attributions (
                        source_event_id, status, server_name, account_name,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_event_id,
                        status,
                        server_name,
                        account_name,
                        recorded_at_value,
                        recorded_at_value,
                    ),
                )
            else:
                self._validate_account_attribution_transition(
                    existing_status=str(existing["status"]),
                    existing_server_name=(
                        str(existing["server_name"])
                        if existing["server_name"] is not None
                        else None
                    ),
                    existing_account_name=(
                        str(existing["account_name"])
                        if existing["account_name"] is not None
                        else None
                    ),
                    status=status,
                    server_name=server_name,
                    account_name=account_name,
                    source_event_id=source_event_id,
                )
                if str(existing["status"]) != status:
                    connection.execute(
                        """
                        UPDATE discord_source_event_account_attributions
                        SET status = ?, server_name = ?, account_name = ?, updated_at = ?
                        WHERE source_event_id = ?
                        """,
                        (
                            status,
                            server_name,
                            account_name,
                            recorded_at_value,
                            source_event_id,
                        ),
                    )

            row = connection.execute(
                """
                SELECT source_event_id, status, server_name, account_name,
                       created_at, updated_at
                FROM discord_source_event_account_attributions
                WHERE source_event_id = ?
                """,
                (source_event_id,),
            ).fetchone()
            result = self._account_attribution_result(row)
            if result is None:
                raise RuntimeError(
                    "Inserted Discord account attribution could not be reloaded"
                )
            return result

    def _connection(self) -> sqlite3.Connection:
        return connect(self._database_path)

    @staticmethod
    def _validate_processing_identity(**values: int) -> None:
        for field_name, value in values.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field_name} must be an integer")

    @staticmethod
    def _validate_version(value: str, field_name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must not be blank")

    @staticmethod
    def _normalize_processing_datetime(value: datetime, field_name: str) -> datetime:
        if not isinstance(value, datetime):
            raise TypeError(f"{field_name} must be a datetime")
        if value.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _validate_server_attribution(
        *,
        status: str,
        server_name: str | None,
    ) -> None:
        if not isinstance(status, str) or status not in {
            "resolved",
            "unresolved",
            "ambiguous",
        }:
            raise DiscordSourceEventServerAttributionValidationError(
                "status must be 'resolved', 'unresolved', or 'ambiguous'"
            )
        if status == "resolved":
            if not isinstance(server_name, str) or not server_name.strip():
                raise DiscordSourceEventServerAttributionValidationError(
                    "resolved attribution requires a nonblank server_name"
                )
            return
        if server_name is not None:
            raise DiscordSourceEventServerAttributionValidationError(
                f"{status} attribution requires server_name to be None"
            )

    @staticmethod
    def _validate_account_attribution(
        *,
        status: str,
        server_name: str | None,
        account_name: str | None,
    ) -> None:
        if not isinstance(status, str) or status not in {
            "resolved",
            "unresolved",
            "ambiguous",
        }:
            raise DiscordSourceEventAccountAttributionValidationError(
                "status must be 'resolved', 'unresolved', or 'ambiguous'"
            )
        if status == "resolved":
            if not isinstance(server_name, str) or not server_name.strip():
                raise DiscordSourceEventAccountAttributionValidationError(
                    "resolved attribution requires a nonblank server_name"
                )
            if not isinstance(account_name, str) or not account_name.strip():
                raise DiscordSourceEventAccountAttributionValidationError(
                    "resolved attribution requires a nonblank account_name"
                )
            return
        if server_name is not None or account_name is not None:
            raise DiscordSourceEventAccountAttributionValidationError(
                f"{status} attribution requires server_name and account_name to be None"
            )

    @staticmethod
    def _validate_server_attribution_transition(
        *,
        existing_status: str,
        existing_server_name: str | None,
        status: str,
        server_name: str | None,
        source_event_id: int,
    ) -> None:
        if existing_status == status and existing_server_name == server_name:
            return
        if existing_status in {"unresolved", "ambiguous"} and status == "resolved":
            return
        raise DiscordSourceEventServerAttributionConflictError(
            "Discord source event "
            f"{source_event_id} has immutable attribution "
            f"{existing_status!r} / {existing_server_name!r}"
        )

    @staticmethod
    def _validate_account_attribution_transition(
        *,
        existing_status: str,
        existing_server_name: str | None,
        existing_account_name: str | None,
        status: str,
        server_name: str | None,
        account_name: str | None,
        source_event_id: int,
    ) -> None:
        if (
            existing_status == status
            and existing_server_name == server_name
            and existing_account_name == account_name
        ):
            return
        if existing_status in {"unresolved", "ambiguous"} and status == "resolved":
            return
        raise DiscordSourceEventAccountAttributionConflictError(
            "Discord source event "
            f"{source_event_id} has immutable account attribution "
            f"{existing_status!r} / {existing_server_name!r} / {existing_account_name!r}"
        )

    @staticmethod
    def _server_attribution_result(
        row: sqlite3.Row | None,
    ) -> DiscordSourceEventServerAttribution | None:
        if row is None:
            return None
        return DiscordSourceEventServerAttribution(
            source_event_id=int(row["source_event_id"]),
            status=str(row["status"]),
            server_name=(str(row["server_name"]) if row["server_name"] is not None else None),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def _account_attribution_result(
        row: sqlite3.Row | None,
    ) -> DiscordSourceEventAccountAttribution | None:
        if row is None:
            return None
        return DiscordSourceEventAccountAttribution(
            source_event_id=int(row["source_event_id"]),
            status=str(row["status"]),
            server_name=(str(row["server_name"]) if row["server_name"] is not None else None),
            account_name=(
                str(row["account_name"]) if row["account_name"] is not None else None
            ),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def _validate_begin_state(
        event: sqlite3.Row,
        latest_attempt: sqlite3.Row | None,
        source_event_id: int,
    ) -> None:
        event_status = str(event["status"])
        if event_status == "succeeded":
            raise DiscordMessageProcessingConflictError(
                f"Discord source event {source_event_id} has already succeeded"
            )
        if event_status == "processing":
            raise DiscordMessageProcessingConflictError(
                f"Discord source event {source_event_id} already has an active attempt"
            )
        if latest_attempt is None:
            if event_status != "received":
                raise DiscordMessageProcessingConflictError(
                    f"Discord source event {source_event_id} cannot begin from status {event_status!r}"
                )
            return
        latest_status = str(latest_attempt["status"])
        if latest_status == "processing":
            raise DiscordMessageProcessingConflictError(
                f"Discord source event {source_event_id} already has an active attempt"
            )
        if latest_status == "succeeded":
            raise DiscordMessageProcessingConflictError(
                f"Discord source event {source_event_id} has already succeeded"
            )
        if latest_status not in {"failed", "unresolved_attribution"}:
            raise DiscordMessageProcessingConflictError(
                f"Discord source event {source_event_id} has invalid latest attempt status {latest_status!r}"
            )
        if not bool(latest_attempt["retryable"]):
            raise DiscordMessageProcessingConflictError(
                f"Discord source event {source_event_id} latest attempt is not retryable"
            )
        if event_status not in {"failed", "unresolved_attribution", "received"}:
            raise DiscordMessageProcessingConflictError(
                f"Discord source event {source_event_id} cannot begin from status {event_status!r}"
            )

    @staticmethod
    def _load_processing_rows(
        connection: sqlite3.Connection,
        *,
        source_event_id: int,
        attempt_id: int,
    ) -> tuple[sqlite3.Row, sqlite3.Row]:
        event = connection.execute(
            "SELECT * FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()
        if event is None:
            raise DiscordMessageProcessingNotFoundError(
                f"Discord source event {source_event_id} was not found"
            )
        attempt = connection.execute(
            """
            SELECT * FROM discord_processing_attempts
            WHERE id = ? AND source_event_id = ?
            """,
            (attempt_id, source_event_id),
        ).fetchone()
        if attempt is None:
            ownership = connection.execute(
                "SELECT source_event_id FROM discord_processing_attempts WHERE id = ?",
                (attempt_id,),
            ).fetchone()
            if ownership is not None:
                raise DiscordMessageProcessingConflictError(
                    f"Discord processing attempt {attempt_id} belongs to another source event"
                )
            raise DiscordMessageProcessingNotFoundError(
                f"Discord processing attempt {attempt_id} was not found"
            )
        return event, attempt

    @staticmethod
    def _validate_completion_state(
        event: sqlite3.Row,
        attempt: sqlite3.Row,
        source_event_id: int,
        attempt_id: int,
    ) -> None:
        if str(event["status"]) != "processing":
            raise DiscordMessageProcessingConflictError(
                f"Discord source event {source_event_id} is not processing"
            )
        if str(attempt["status"]) != "processing":
            raise DiscordMessageProcessingConflictError(
                f"Discord processing attempt {attempt_id} is not processing"
            )

    @classmethod
    def _validate_finished_at(cls, attempt: sqlite3.Row, finished_at: datetime) -> None:
        started_at = datetime.fromisoformat(str(attempt["started_at"]))
        if finished_at < started_at:
            raise ValueError("finished_at must not be earlier than started_at")

    @staticmethod
    def _processing_result(
        connection: sqlite3.Connection,
        source_event_id: int,
        attempt_id: int,
    ) -> ProcessingAttemptResult:
        row = connection.execute(
            """
            SELECT
                a.source_event_id,
                a.id AS attempt_id,
                a.attempt_number,
                a.status AS attempt_status,
                e.status AS source_event_status,
                a.retryable,
                a.started_at,
                a.finished_at,
                a.lease_expires_at,
                a.parser_version,
                a.router_version,
                a.failure_code,
                a.failure_detail,
                e.legacy_import_event_id
            FROM discord_processing_attempts AS a
            JOIN discord_source_events AS e ON e.id = a.source_event_id
            WHERE a.source_event_id = ? AND a.id = ?
            """,
            (source_event_id, attempt_id),
        ).fetchone()
        if row is None:
            raise DiscordMessageProcessingNotFoundError(
                f"Discord processing attempt {attempt_id} was not found"
            )
        return ProcessingAttemptResult(
            source_event_id=int(row["source_event_id"]),
            attempt_id=int(row["attempt_id"]),
            attempt_number=int(row["attempt_number"]),
            attempt_status=str(row["attempt_status"]),
            source_event_status=str(row["source_event_status"]),
            retryable=bool(row["retryable"]),
            started_at=datetime.fromisoformat(str(row["started_at"])),
            finished_at=(
                datetime.fromisoformat(str(row["finished_at"]))
                if row["finished_at"] is not None
                else None
            ),
            lease_expires_at=(
                datetime.fromisoformat(str(row["lease_expires_at"]))
                if row["lease_expires_at"] is not None
                else None
            ),
            parser_version=str(row["parser_version"]),
            router_version=str(row["router_version"]),
            failure_code=(str(row["failure_code"]) if row["failure_code"] is not None else None),
            failure_detail=(
                str(row["failure_detail"]) if row["failure_detail"] is not None else None
            ),
            legacy_import_event_id=(
                int(row["legacy_import_event_id"])
                if row["legacy_import_event_id"] is not None
                else None
            ),
        )

    @staticmethod
    def _validate_receive(
        *,
        aggregate_key: MessageAggregateKey,
        revision_key: MessageRevisionKey,
        event_key: str,
        event_kind: str,
        raw_text: str,
        source_observed_at: datetime | None,
        received_at: datetime,
    ) -> None:
        if not isinstance(aggregate_key, MessageAggregateKey):
            raise TypeError("aggregate_key must be a MessageAggregateKey")
        if not isinstance(revision_key, MessageRevisionKey):
            raise TypeError("revision_key must be a MessageRevisionKey")
        if revision_key.aggregate != aggregate_key:
            raise ValueError("aggregate_key and revision_key must correspond")
        if not isinstance(event_key, str) or not event_key.strip():
            raise ValueError("event_key must not be blank")
        if not isinstance(event_kind, str) or not event_kind.strip():
            raise ValueError("event_kind must not be blank")
        if not isinstance(raw_text, str):
            raise TypeError("raw_text must be a string")
        if not isinstance(received_at, datetime):
            raise TypeError("received_at must be a datetime")
        if source_observed_at is not None and not isinstance(source_observed_at, datetime):
            raise TypeError("source_observed_at must be a datetime or None")

    @staticmethod
    def _insert_or_get_aggregate(
        connection: sqlite3.Connection,
        key: MessageAggregateKey,
        received_at: str,
    ) -> tuple[sqlite3.Row, bool]:
        try:
            cursor = connection.execute(
                """
                INSERT INTO discord_message_aggregates (
                    platform, guild_id, channel_id, message_id,
                    first_received_at, last_received_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key.platform.value,
                    key.guild_id,
                    key.channel_id,
                    key.message_id,
                    received_at,
                    received_at,
                    received_at,
                    received_at,
                ),
            )
        except sqlite3.IntegrityError:
            row = connection.execute(
                """
                SELECT * FROM discord_message_aggregates
                WHERE platform = ? AND guild_id = ? AND channel_id = ? AND message_id = ?
                """,
                (key.platform.value, key.guild_id, key.channel_id, key.message_id),
            ).fetchone()
            if row is None:
                raise
            return row, False
        row = connection.execute(
            "SELECT * FROM discord_message_aggregates WHERE id = ?",
            (int(cursor.lastrowid),),
        ).fetchone()
        if row is None:
            raise RuntimeError("Inserted Discord message aggregate could not be reloaded")
        return row, True

    @staticmethod
    def _insert_or_get_revision(
        connection: sqlite3.Connection,
        key: MessageRevisionKey,
        aggregate_id: int,
        received_at: str,
        source_observed_at: str | None,
    ) -> tuple[sqlite3.Row, bool]:
        marker_clause = "source_revision_marker = ?" if key.source_revision_marker is not None else "source_revision_marker IS NULL"
        lookup_values: tuple[object, ...] = (
            (aggregate_id, key.source_revision_marker, key.normalized_payload_hash)
            if key.source_revision_marker is not None
            else (aggregate_id, key.normalized_payload_hash)
        )
        try:
            cursor = connection.execute(
                """
                INSERT INTO discord_message_revisions (
                    aggregate_id, source_revision_marker, normalized_payload_hash,
                    revision_state, source_observed_at,
                    first_received_at, last_received_at, created_at, updated_at
                ) VALUES (?, ?, ?, 'candidate', ?, ?, ?, ?, ?)
                """,
                (
                    aggregate_id,
                    key.source_revision_marker,
                    key.normalized_payload_hash,
                    source_observed_at,
                    received_at,
                    received_at,
                    received_at,
                    received_at,
                ),
            )
        except sqlite3.IntegrityError:
            row = connection.execute(
                f"""
                SELECT * FROM discord_message_revisions
                WHERE aggregate_id = ? AND {marker_clause}
                  AND normalized_payload_hash = ?
                """,
                lookup_values,
            ).fetchone()
            if row is None:
                raise
            if (
                int(row["aggregate_id"]) != aggregate_id
                or row["normalized_payload_hash"] != key.normalized_payload_hash
                or row["source_revision_marker"] != key.source_revision_marker
            ):
                raise DiscordMessageReceiveConflictError(
                    "Existing Discord revision does not match the requested immutable identity"
                )
            return row, False
        row = connection.execute(
            "SELECT * FROM discord_message_revisions WHERE id = ?",
            (int(cursor.lastrowid),),
        ).fetchone()
        if row is None:
            raise RuntimeError("Inserted Discord message revision could not be reloaded")
        return row, True

    @staticmethod
    def _insert_or_get_source_event(
        connection: sqlite3.Connection,
        *,
        event_key: str,
        revision_id: int,
        event_kind: str,
        raw_text: str,
        payload_json: str | None,
        payload_capture_version: str | None,
        source_observed_at: str | None,
        received_at: str,
    ) -> tuple[sqlite3.Row, bool]:
        try:
            cursor = connection.execute(
                """
                INSERT INTO discord_source_events (
                    event_key, revision_id, event_kind, status, raw_text,
                    payload_json, payload_capture_version, source_observed_at,
                    received_at, last_seen_at, delivery_count, created_at, updated_at
                ) VALUES (?, ?, ?, 'received', ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    event_key,
                    revision_id,
                    event_kind,
                    raw_text,
                    payload_json,
                    payload_capture_version,
                    source_observed_at,
                    received_at,
                    received_at,
                    received_at,
                    received_at,
                ),
            )
        except sqlite3.IntegrityError:
            row = connection.execute(
                "SELECT * FROM discord_source_events WHERE event_key = ?",
                (event_key,),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT * FROM discord_source_events WHERE revision_id = ?",
                    (revision_id,),
                ).fetchone()
            if row is None:
                raise
            return row, False
        row = connection.execute(
            "SELECT * FROM discord_source_events WHERE id = ?",
            (int(cursor.lastrowid),),
        ).fetchone()
        if row is None:
            raise RuntimeError("Inserted Discord source event could not be reloaded")
        return row, True

    @staticmethod
    def _verify_existing_event(
        row: sqlite3.Row,
        *,
        event_key: str,
        revision_id: int,
        event_kind: str,
        raw_text: str,
        payload_json: str | None,
        payload_capture_version: str | None,
        source_observed_at: str | None,
    ) -> None:
        immutable_values_match = (
            row["event_key"] == event_key
            and int(row["revision_id"]) == revision_id
            and row["event_kind"] == event_kind
            and row["raw_text"] == raw_text
            and row["payload_json"] == payload_json
            and row["payload_capture_version"] == payload_capture_version
            and row["source_observed_at"] == source_observed_at
        )
        if not immutable_values_match:
            raise DiscordMessageReceiveConflictError(
                "Existing Discord source event does not match the requested immutable data"
            )
