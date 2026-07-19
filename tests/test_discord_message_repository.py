import sqlite3
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.discord_message_repository import (
    DiscordMessageReceiveConflictError,
    DiscordMessageRepository,
)


def aggregate(
    *, guild_id: str = "guild-1", channel_id: str = "channel-1", message_id: str = "message-1"
) -> MessageAggregateKey:
    return MessageAggregateKey(SourcePlatform.DISCORD, guild_id, channel_id, message_id)


def revision(
    message: MessageAggregateKey, *, payload_hash: str = "hash-1", marker: str | None = "revision-1"
) -> MessageRevisionKey:
    if marker is None:
        return MessageRevisionKey.unversioned(message, payload_hash)
    return MessageRevisionKey.versioned(message, payload_hash, marker)


def receive(
    repository: DiscordMessageRepository,
    *,
    message: MessageAggregateKey | None = None,
    message_revision: MessageRevisionKey | None = None,
    event_key: str = "event-1",
    event_kind: str = "message_create",
    raw_text: str = "raw message",
    payload_json: str | None = '{"content":"raw message"}',
    payload_capture_version: str | None = "capture-1",
    source_observed_at: datetime | None = datetime(2026, 7, 18, 0, 0, tzinfo=timezone.utc),
    received_at: datetime = datetime(2026, 7, 18, 0, 1, tzinfo=timezone.utc),
):
    message = message or aggregate()
    message_revision = message_revision or revision(message)
    return repository.receive_message(
        aggregate_key=message,
        revision_key=message_revision,
        event_key=event_key,
        event_kind=event_kind,
        raw_text=raw_text,
        payload_json=payload_json,
        payload_capture_version=payload_capture_version,
        source_observed_at=source_observed_at,
        received_at=received_at,
    )


def counts(database_path):
    with connect(database_path) as connection:
        return tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "discord_message_aggregates",
                "discord_message_revisions",
                "discord_source_events",
                "discord_processing_attempts",
            )
        )


def test_first_receive_creates_rows_and_initializes_values(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received_at = datetime(2026, 7, 18, 1, 2, tzinfo=timezone.utc)
    source_observed_at = datetime(2026, 7, 18, 1, 1, tzinfo=timezone.utc)

    result = receive(repository, received_at=received_at, source_observed_at=source_observed_at)

    assert result.aggregate_created is True
    assert result.revision_created is True
    assert result.source_event_created is True
    assert result.delivery_count == 1
    assert result.status == "received"
    assert result.event_key == "event-1"
    assert counts(database_path) == (1, 1, 1, 0)
    with connect(database_path) as connection:
        aggregate_row = connection.execute("SELECT * FROM discord_message_aggregates").fetchone()
        revision_row = connection.execute("SELECT * FROM discord_message_revisions").fetchone()
        event_row = connection.execute("SELECT * FROM discord_source_events").fetchone()
    timestamp = received_at.isoformat()
    assert aggregate_row["first_received_at"] == timestamp
    assert aggregate_row["last_received_at"] == timestamp
    assert aggregate_row["created_at"] == timestamp
    assert aggregate_row["updated_at"] == timestamp
    assert revision_row["source_revision_marker"] == "revision-1"
    assert revision_row["normalized_payload_hash"] == "hash-1"
    assert revision_row["source_observed_at"] == source_observed_at.isoformat()
    assert revision_row["first_received_at"] == timestamp
    assert revision_row["last_received_at"] == timestamp
    assert revision_row["created_at"] == timestamp
    assert revision_row["updated_at"] == timestamp
    assert event_row["status"] == "received"
    assert event_row["delivery_count"] == 1
    assert event_row["received_at"] == timestamp
    assert event_row["last_seen_at"] == timestamp
    assert event_row["created_at"] == timestamp
    assert event_row["updated_at"] == timestamp


def test_exact_replay_returns_same_ids_updates_last_seen_and_preserves_first_seen(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    first = receive(repository, received_at=datetime(2026, 7, 18, 1, 0, tzinfo=timezone.utc))
    second_time = datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc)

    second = receive(repository, received_at=second_time)

    assert (second.aggregate_id, second.revision_id, second.source_event_id) == (
        first.aggregate_id,
        first.revision_id,
        first.source_event_id,
    )
    assert second.aggregate_created is False
    assert second.revision_created is False
    assert second.source_event_created is False
    assert second.delivery_count == 2
    assert second.status == "received"
    with connect(database_path) as connection:
        rows = {
            table: connection.execute(f"SELECT * FROM {table}").fetchone()
            for table in (
                "discord_message_aggregates",
                "discord_message_revisions",
                "discord_source_events",
            )
        }
    first_time = "2026-07-18T01:00:00+00:00"
    last_time = second_time.isoformat()
    for table in ("discord_message_aggregates", "discord_message_revisions"):
        assert rows[table]["first_received_at"] == first_time
        assert rows[table]["last_received_at"] == last_time
        assert rows[table]["created_at"] == first_time
        assert rows[table]["updated_at"] == last_time
    assert rows["discord_source_events"]["received_at"] == first_time
    assert rows["discord_source_events"]["last_seen_at"] == last_time
    assert rows["discord_source_events"]["created_at"] == first_time
    assert rows["discord_source_events"]["updated_at"] == last_time


def test_same_text_with_different_message_id_creates_separate_rows(tmp_path) -> None:
    repository = DiscordMessageRepository(tmp_path / "messages.db")

    first = receive(repository)
    second_message = aggregate(message_id="message-2")
    second = receive(repository, message=second_message, message_revision=revision(second_message), event_key="event-2")

    assert first.aggregate_id != second.aggregate_id
    assert first.revision_id != second.revision_id
    assert first.source_event_id != second.source_event_id
    assert counts(tmp_path / "messages.db")[:3] == (2, 2, 2)


def test_different_version_markers_create_revisions_and_events(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    message = aggregate()

    first = receive(repository, message=message, message_revision=revision(message, marker="revision-1"))
    second = receive(
        repository,
        message=message,
        message_revision=revision(message, marker="revision-2"),
        event_key="event-2",
    )

    assert first.aggregate_id == second.aggregate_id
    assert first.revision_id != second.revision_id
    assert first.source_event_id != second.source_event_id
    assert counts(database_path)[:3] == (1, 2, 2)


def test_identical_unversioned_revision_collapses_on_replay(tmp_path) -> None:
    repository = DiscordMessageRepository(tmp_path / "messages.db")
    message = aggregate()
    unversioned = revision(message, marker=None)

    first = receive(repository, message=message, message_revision=unversioned)
    second = receive(repository, message=message, message_revision=unversioned)

    assert second.revision_id == first.revision_id
    assert second.source_event_id == first.source_event_id
    assert counts(tmp_path / "messages.db")[:3] == (1, 1, 1)


def test_guild_and_channel_remain_part_of_aggregate_identity(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    first_message = aggregate()
    second_message = aggregate(guild_id="guild-2", channel_id="channel-2")

    first = receive(repository, message=first_message, message_revision=revision(first_message))
    second = receive(
        repository,
        message=second_message,
        message_revision=revision(second_message),
        event_key="event-2",
    )

    assert first.aggregate_id != second.aggregate_id
    assert counts(database_path)[:3] == (2, 2, 2)


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_replay_does_not_reset_terminal_event_status(tmp_path, status: str) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    first = receive(repository)
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE discord_source_events SET status = ? WHERE id = ?",
            (status, first.source_event_id),
        )

    result = receive(repository, received_at=datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc))

    assert result.status == status
    assert result.delivery_count == 2


def test_conflicting_event_key_rolls_back_all_changes(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    receive(repository)
    before = counts(database_path)

    other_message = aggregate(message_id="message-2")
    with pytest.raises(DiscordMessageReceiveConflictError):
        receive(
            repository,
            message=other_message,
            message_revision=revision(other_message),
            event_key="event-1",
        )

    assert counts(database_path) == before
    with connect(database_path) as connection:
        assert connection.execute("SELECT delivery_count FROM discord_source_events").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("raw_text", "payload_json"),
    [("different raw", '{"content":"raw message"}'), ("raw message", '{"content":"different"}')],
)
def test_conflicting_immutable_payload_rolls_back_without_overwrite(
    tmp_path, raw_text: str, payload_json: str
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    receive(repository)

    with pytest.raises(DiscordMessageReceiveConflictError):
        receive(repository, raw_text=raw_text, payload_json=payload_json)

    assert counts(database_path) == (1, 1, 1, 0)
    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT raw_text, payload_json, delivery_count FROM discord_source_events"
        ).fetchone()
    assert tuple(event) == ("raw message", '{"content":"raw message"}', 1)


def test_mid_transaction_failure_leaves_no_orphan_rows(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_discord_source_event_insert
            BEFORE INSERT ON discord_source_events
            BEGIN
                SELECT RAISE(ABORT, 'forced receive failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced receive failure"):
        receive(repository)

    assert counts(database_path) == (0, 0, 0, 0)


def test_foreign_keys_are_enabled_and_database_is_valid(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    receive(repository)

    with connect(database_path) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_restart_preserves_replay_identity_and_creates_no_attempt(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    first_repository = DiscordMessageRepository(database_path)
    first = receive(first_repository)

    restarted_repository = DiscordMessageRepository(database_path)
    second = receive(restarted_repository)

    assert (second.aggregate_id, second.revision_id, second.source_event_id) == (
        first.aggregate_id,
        first.revision_id,
        first.source_event_id,
    )
    assert second.delivery_count == 2
    assert counts(database_path)[3] == 0
