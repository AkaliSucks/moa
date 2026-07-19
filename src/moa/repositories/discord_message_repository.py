"""Atomic durable receipt of Discord message observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import sqlite3
from pathlib import Path

from moa.database.sqlite import connect
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey
from moa.repositories.catalog_repository import CatalogRepository


class DiscordMessageReceiveConflictError(RuntimeError):
    """Raised when a durable Discord identity conflicts with stored data."""


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

    def _connection(self) -> sqlite3.Connection:
        return connect(self._database_path)

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
