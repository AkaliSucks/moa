import sqlite3
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.discord_message_repository import (
    DiscordMessageProcessingConflictError,
    DiscordMessageProcessingNotFoundError,
    DiscordMessageReceiveConflictError,
    DiscordSourceEventAccountAttribution,
    DiscordSourceEventAccountAttributionConflictError,
    DiscordSourceEventAccountAttributionNotFoundError,
    DiscordSourceEventAccountAttributionValidationError,
    DiscordSourceEventServerAttributionConflictError,
    DiscordSourceEventServerAttributionNotFoundError,
    DiscordSourceEventServerAttributionValidationError,
    DiscordSourceEventServerAttribution,
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


def record_attribution(
    repository: DiscordMessageRepository,
    source_event_id: int,
    *,
    status: str = "resolved",
    server_name: str | None = "Server A",
    recorded_at: datetime = datetime(2026, 7, 18, 0, 2, tzinfo=timezone.utc),
):
    return repository.record_server_attribution(
        source_event_id,
        status=status,
        server_name=server_name,
        recorded_at=recorded_at,
    )


def record_account_attribution(
    repository: DiscordMessageRepository,
    source_event_id: int,
    *,
    status: str = "resolved",
    server_name: str | None = "Server A",
    account_name: str | None = "Account A",
    recorded_at: datetime = datetime(2026, 7, 18, 0, 2, tzinfo=timezone.utc),
):
    return repository.record_account_attribution(
        source_event_id,
        status=status,
        server_name=server_name,
        account_name=account_name,
        recorded_at=recorded_at,
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


def test_begin_processing_attempt_creates_attempt_one_and_marks_event_processing(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    started_at = datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc)
    lease_expires_at = datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc)

    result = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=started_at,
        lease_expires_at=lease_expires_at,
    )

    assert result.source_event_id == received.source_event_id
    assert result.attempt_number == 1
    assert result.attempt_status == "processing"
    assert result.source_event_status == "processing"
    assert result.retryable is False
    assert result.started_at == started_at
    assert result.finished_at is None
    assert result.lease_expires_at == lease_expires_at
    with connect(database_path) as connection:
        event = connection.execute("SELECT * FROM discord_source_events").fetchone()
        attempt = connection.execute("SELECT * FROM discord_processing_attempts").fetchone()
    assert event["status"] == "processing"
    assert attempt["attempt_number"] == 1
    assert attempt["retryable"] == 0
    assert attempt["finished_at"] is None
    assert attempt["parser_version"] == "parser-1"
    assert attempt["router_version"] == "router-1"


def test_successful_completion_updates_both_rows_and_links_valid_import_event(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    started_at = datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc)
    finished_at = datetime(2026, 7, 18, 2, 1, tzinfo=timezone.utc)
    attempt = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=started_at,
    )
    with connect(database_path) as connection:
        import_event_id = int(
            connection.execute(
                """
                INSERT INTO import_events (kind, source, observed_at, raw_message)
                VALUES ('test', 'test', ?, 'test')
                """,
                (finished_at.isoformat(),),
            ).lastrowid
        )

    result = repository.mark_processing_success(
        source_event_id=received.source_event_id,
        attempt_id=attempt.attempt_id,
        finished_at=finished_at,
        legacy_import_event_id=import_event_id,
    )

    assert result.attempt_status == "succeeded"
    assert result.source_event_status == "succeeded"
    assert result.finished_at == finished_at
    assert result.legacy_import_event_id == import_event_id
    with connect(database_path) as connection:
        event = connection.execute("SELECT * FROM discord_source_events").fetchone()
        row = connection.execute("SELECT * FROM discord_processing_attempts").fetchone()
    assert event["status"] == "succeeded"
    assert event["legacy_import_event_id"] == import_event_id
    assert row["status"] == "succeeded"
    assert row["finished_at"] == finished_at.isoformat()
    assert row["retryable"] == 0


def test_failed_completion_stores_details_and_marks_event_failed(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    attempt = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
    )
    finished_at = datetime(2026, 7, 18, 2, 1, tzinfo=timezone.utc)

    result = repository.mark_processing_failure(
        source_event_id=received.source_event_id,
        attempt_id=attempt.attempt_id,
        status="failed",
        retryable=True,
        failure_code="parser_error",
        failure_detail="malformed payload",
        finished_at=finished_at,
    )

    assert result.attempt_status == "failed"
    assert result.source_event_status == "failed"
    assert result.retryable is True
    assert result.failure_code == "parser_error"
    assert result.failure_detail == "malformed payload"
    with connect(database_path) as connection:
        event_status = connection.execute("SELECT status FROM discord_source_events").fetchone()[0]
        row = connection.execute("SELECT * FROM discord_processing_attempts").fetchone()
    assert event_status == "failed"
    assert row["status"] == "failed"
    assert row["retryable"] == 1
    assert row["finished_at"] == finished_at.isoformat()


def test_unresolved_attribution_marks_both_rows(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    attempt = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
    )

    result = repository.mark_processing_failure(
        source_event_id=received.source_event_id,
        attempt_id=attempt.attempt_id,
        status="unresolved_attribution",
        retryable=False,
        failure_code="ambiguous_account",
        failure_detail=None,
        finished_at=datetime(2026, 7, 18, 2, 1, tzinfo=timezone.utc),
    )

    assert result.attempt_status == "unresolved_attribution"
    assert result.source_event_status == "unresolved_attribution"
    assert result.retryable is False
    with connect(database_path) as connection:
        statuses = connection.execute(
            "SELECT status FROM discord_source_events UNION ALL "
            "SELECT status FROM discord_processing_attempts"
        ).fetchall()
    assert [row[0] for row in statuses] == ["unresolved_attribution", "unresolved_attribution"]


def test_retryable_failure_allows_attempt_two_but_nonretryable_failure_does_not(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    first = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
    )
    repository.mark_processing_failure(
        source_event_id=received.source_event_id,
        attempt_id=first.attempt_id,
        status="failed",
        retryable=True,
        failure_code="temporary",
        failure_detail=None,
        finished_at=datetime(2026, 7, 18, 2, 1, tzinfo=timezone.utc),
    )
    second = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-2",
        router_version="router-2",
        started_at=datetime(2026, 7, 18, 2, 2, tzinfo=timezone.utc),
    )
    assert second.attempt_number == 2
    repository.mark_processing_failure(
        source_event_id=received.source_event_id,
        attempt_id=second.attempt_id,
        status="failed",
        retryable=False,
        failure_code="permanent",
        failure_detail=None,
        finished_at=datetime(2026, 7, 18, 2, 3, tzinfo=timezone.utc),
    )

    with pytest.raises(DiscordMessageProcessingConflictError):
        repository.begin_processing_attempt(
            source_event_id=received.source_event_id,
            parser_version="parser-3",
            router_version="router-3",
            started_at=datetime(2026, 7, 18, 2, 4, tzinfo=timezone.utc),
        )
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM discord_processing_attempts").fetchone()[0] == 2


def test_active_processing_and_terminal_success_reject_begin_without_duplicate_attempts(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    first = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
    )

    with pytest.raises(DiscordMessageProcessingConflictError):
        repository.begin_processing_attempt(
            source_event_id=received.source_event_id,
            parser_version="parser-2",
            router_version="router-2",
            started_at=datetime(2026, 7, 18, 2, 1, tzinfo=timezone.utc),
        )
    assert counts(database_path)[3] == 1
    repository.mark_processing_success(
        source_event_id=received.source_event_id,
        attempt_id=first.attempt_id,
        finished_at=datetime(2026, 7, 18, 2, 2, tzinfo=timezone.utc),
    )
    with pytest.raises(DiscordMessageProcessingConflictError):
        repository.begin_processing_attempt(
            source_event_id=received.source_event_id,
            parser_version="parser-3",
            router_version="router-3",
            started_at=datetime(2026, 7, 18, 2, 3, tzinfo=timezone.utc),
        )
    assert counts(database_path)[3] == 1


def test_attempt_ownership_and_double_completion_are_rejected(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    first = receive(repository)
    second_message = aggregate(message_id="message-2")
    second = receive(
        repository,
        message=second_message,
        message_revision=revision(second_message),
        event_key="event-2",
    )
    attempt = repository.begin_processing_attempt(
        source_event_id=first.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
    )
    finished_at = datetime(2026, 7, 18, 2, 1, tzinfo=timezone.utc)

    with pytest.raises(DiscordMessageProcessingConflictError):
        repository.mark_processing_success(
            source_event_id=second.source_event_id,
            attempt_id=attempt.attempt_id,
            finished_at=finished_at,
        )
    repository.mark_processing_success(
        source_event_id=first.source_event_id,
        attempt_id=attempt.attempt_id,
        finished_at=finished_at,
    )
    with pytest.raises(DiscordMessageProcessingConflictError):
        repository.mark_processing_success(
            source_event_id=first.source_event_id,
            attempt_id=attempt.attempt_id,
            finished_at=datetime(2026, 7, 18, 2, 2, tzinfo=timezone.utc),
        )
    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT status FROM discord_source_events ORDER BY id"
        ).fetchall()
    assert [row[0] for row in rows] == ["succeeded", "received"]


@pytest.mark.parametrize("field", ["parser_version", "router_version"])
def test_blank_processing_versions_are_rejected(tmp_path, field: str) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    values = {
        "source_event_id": received.source_event_id,
        "parser_version": "parser-1",
        "router_version": "router-1",
        "started_at": datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
    }
    values[field] = "  "

    with pytest.raises(ValueError, match="must not be blank"):
        repository.begin_processing_attempt(**values)
    assert counts(database_path)[3] == 0


def test_processing_validation_rejects_naive_times_invalid_failure_status_and_early_finish(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    with pytest.raises(ValueError, match="timezone-aware"):
        repository.begin_processing_attempt(
            source_event_id=received.source_event_id,
            parser_version="parser-1",
            router_version="router-1",
            started_at=datetime(2026, 7, 18, 2, 0),
        )
    attempt = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(ValueError, match="failed.*unresolved"):
        repository.mark_processing_failure(
            source_event_id=received.source_event_id,
            attempt_id=attempt.attempt_id,
            status="succeeded",
            retryable=False,
            failure_code=None,
            failure_detail=None,
            finished_at=datetime(2026, 7, 18, 2, 1, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError, match="earlier"):
        repository.mark_processing_success(
            source_event_id=received.source_event_id,
            attempt_id=attempt.attempt_id,
            finished_at=datetime(2026, 7, 18, 1, 59, tzinfo=timezone.utc),
        )
    with connect(database_path) as connection:
        event = connection.execute("SELECT status FROM discord_source_events").fetchone()[0]
        status = connection.execute("SELECT status FROM discord_processing_attempts").fetchone()[0]
    assert event == "processing"
    assert status == "processing"


def test_nonexistent_legacy_import_event_does_not_complete_attempt(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    attempt = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
    )

    with pytest.raises(DiscordMessageProcessingNotFoundError):
        repository.mark_processing_success(
            source_event_id=received.source_event_id,
            attempt_id=attempt.attempt_id,
            finished_at=datetime(2026, 7, 18, 2, 1, tzinfo=timezone.utc),
            legacy_import_event_id=999,
        )
    with connect(database_path) as connection:
        assert connection.execute("SELECT status FROM discord_source_events").fetchone()[0] == "processing"
        assert connection.execute("SELECT status FROM discord_processing_attempts").fetchone()[0] == "processing"


def test_begin_mid_transaction_failure_rolls_back_attempt_and_event_status(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_processing_event_update
            BEFORE UPDATE OF status ON discord_source_events
            WHEN NEW.status = 'processing'
            BEGIN
                SELECT RAISE(ABORT, 'forced begin failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced begin failure"):
        repository.begin_processing_attempt(
            source_event_id=received.source_event_id,
            parser_version="parser-1",
            router_version="router-1",
            started_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
        )
    assert counts(database_path)[3] == 0
    with connect(database_path) as connection:
        assert connection.execute("SELECT status FROM discord_source_events").fetchone()[0] == "received"


@pytest.mark.parametrize("completion", ["success", "failure"])
def test_completion_mid_transaction_failure_rolls_back_both_rows(tmp_path, completion: str) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    attempt = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
    )
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_completion_event_update
            BEFORE UPDATE OF status ON discord_source_events
            WHEN NEW.status IN ('succeeded', 'failed')
            BEGIN
                SELECT RAISE(ABORT, 'forced completion failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced completion failure"):
        if completion == "success":
            repository.mark_processing_success(
                source_event_id=received.source_event_id,
                attempt_id=attempt.attempt_id,
                finished_at=datetime(2026, 7, 18, 2, 1, tzinfo=timezone.utc),
            )
        else:
            repository.mark_processing_failure(
                source_event_id=received.source_event_id,
                attempt_id=attempt.attempt_id,
                status="failed",
                retryable=True,
                failure_code="temporary",
                failure_detail="failure",
                finished_at=datetime(2026, 7, 18, 2, 1, tzinfo=timezone.utc),
            )
    with connect(database_path) as connection:
        event = connection.execute("SELECT status FROM discord_source_events").fetchone()[0]
        row = connection.execute("SELECT status, finished_at FROM discord_processing_attempts").fetchone()
    assert event == "processing"
    assert row["status"] == "processing"
    assert row["finished_at"] is None


def test_processing_lifecycle_survives_repository_reconstruction(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    first_repository = DiscordMessageRepository(database_path)
    received = receive(first_repository)
    attempt = first_repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
    )

    restarted_repository = DiscordMessageRepository(database_path)
    result = restarted_repository.mark_processing_failure(
        source_event_id=received.source_event_id,
        attempt_id=attempt.attempt_id,
        status="failed",
        retryable=True,
        failure_code="after_restart",
        failure_detail="durable",
        finished_at=datetime(2026, 7, 18, 2, 1, tzinfo=timezone.utc),
    )

    assert result.attempt_number == 1
    assert result.attempt_status == "failed"
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM discord_processing_attempts").fetchone()[0] == 1


@pytest.mark.parametrize(
    "status, server_name",
    [("resolved", "  Configured Server  "), ("unresolved", None), ("ambiguous", None)],
)
def test_record_and_read_server_attribution(
    tmp_path, status: str, server_name: str | None
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    recorded_at = datetime(2026, 7, 18, 3, 2, tzinfo=timezone.utc)

    result = record_attribution(
        repository,
        received.source_event_id,
        status=status,
        server_name=server_name,
        recorded_at=recorded_at,
    )

    assert isinstance(result, DiscordSourceEventServerAttribution)
    assert result.source_event_id == received.source_event_id
    assert result.status == status
    assert result.server_name == server_name
    assert result.created_at == recorded_at
    assert result.updated_at == recorded_at
    assert repository.get_server_attribution(received.source_event_id) == result
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM discord_source_event_server_attributions"
        ).fetchone()
    assert dict(row) == {
        "source_event_id": received.source_event_id,
        "status": status,
        "server_name": server_name,
        "created_at": recorded_at.isoformat(),
        "updated_at": recorded_at.isoformat(),
    }


@pytest.mark.parametrize(
    "status, server_name",
    [("resolved", "Server A"), ("unresolved", None), ("ambiguous", None)],
)
def test_exact_server_attribution_replay_is_idempotent(
    tmp_path, status: str, server_name: str | None
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    first_time = datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc)
    replay_time = datetime(2026, 7, 18, 4, 0, tzinfo=timezone.utc)

    first = record_attribution(
        repository,
        received.source_event_id,
        status=status,
        server_name=server_name,
        recorded_at=first_time,
    )
    replay = record_attribution(
        repository,
        received.source_event_id,
        status=status,
        server_name=server_name,
        recorded_at=replay_time,
    )

    assert replay == first
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_source_event_server_attributions"
        ).fetchone()[0] == 1
        row = connection.execute(
            "SELECT created_at, updated_at FROM discord_source_event_server_attributions"
        ).fetchone()
    assert tuple(row) == (first_time.isoformat(), first_time.isoformat())


@pytest.mark.parametrize("initial_status", ["unresolved", "ambiguous"])
def test_unresolved_or_ambiguous_server_attribution_can_resolve(
    tmp_path, initial_status: str
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    first_time = datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc)
    resolved_time = datetime(2026, 7, 18, 3, 1, tzinfo=timezone.utc)

    first = record_attribution(
        repository,
        received.source_event_id,
        status=initial_status,
        server_name=None,
        recorded_at=first_time,
    )
    resolved = record_attribution(
        repository,
        received.source_event_id,
        status="resolved",
        server_name="Server A",
        recorded_at=resolved_time,
    )

    assert first.status == initial_status
    assert resolved.status == "resolved"
    assert resolved.server_name == "Server A"
    assert resolved.created_at == first_time
    assert resolved.updated_at == resolved_time


def test_resolved_server_attribution_conflicts_with_different_server(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    record_attribution(repository, received.source_event_id)

    with pytest.raises(DiscordSourceEventServerAttributionConflictError, match="immutable"):
        record_attribution(
            repository,
            received.source_event_id,
            server_name="Server B",
            recorded_at=datetime(2026, 7, 18, 4, 0, tzinfo=timezone.utc),
        )


@pytest.mark.parametrize("status", ["unresolved", "ambiguous"])
def test_resolved_server_attribution_cannot_be_downgraded(tmp_path, status: str) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    record_attribution(repository, received.source_event_id)

    with pytest.raises(DiscordSourceEventServerAttributionConflictError, match="immutable"):
        record_attribution(
            repository,
            received.source_event_id,
            status=status,
            server_name=None,
        )


@pytest.mark.parametrize(
    "initial_status, next_status",
    [("unresolved", "ambiguous"), ("ambiguous", "unresolved")],
)
def test_nonresolved_server_attribution_rewrite_conflicts(
    tmp_path, initial_status: str, next_status: str
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    record_attribution(
        repository,
        received.source_event_id,
        status=initial_status,
        server_name=None,
    )

    with pytest.raises(DiscordSourceEventServerAttributionConflictError, match="immutable"):
        record_attribution(
            repository,
            received.source_event_id,
            status=next_status,
            server_name=None,
        )


@pytest.mark.parametrize(
    "status, server_name",
    [
        ("invalid", None),
        ("resolved", None),
        ("resolved", "   "),
        ("unresolved", "Server A"),
        ("ambiguous", "Server A"),
    ],
)
def test_invalid_server_attribution_status_and_server_combination_fails(
    tmp_path, status: str, server_name: str | None
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)

    with pytest.raises(DiscordSourceEventServerAttributionValidationError):
        record_attribution(
            repository,
            received.source_event_id,
            status=status,
            server_name=server_name,
        )
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_source_event_server_attributions"
        ).fetchone()[0] == 0


def test_missing_source_event_attribution_fails_without_writes(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)

    assert repository.get_server_attribution(999) is None
    with pytest.raises(DiscordSourceEventServerAttributionNotFoundError, match="999"):
        record_attribution(repository, 999)
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_source_event_server_attributions"
        ).fetchone()[0] == 0


def test_server_attribution_does_not_change_event_attempt_or_projection_lifecycle(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    attempt = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc),
    )
    with connect(database_path) as connection:
        import_event_id = connection.execute(
            """
            INSERT INTO import_events (kind, source, observed_at, raw_message)
            VALUES ('roll', 'discord', ?, 'legacy')
            """,
            ("2026-07-18T03:01:00+00:00",),
        ).lastrowid
    repository.mark_processing_success(
        source_event_id=received.source_event_id,
        attempt_id=attempt.attempt_id,
        finished_at=datetime(2026, 7, 18, 3, 1, tzinfo=timezone.utc),
        legacy_import_event_id=import_event_id,
    )
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO discord_projection_links (
                source_event_id, projection_kind, projection_slot, state,
                claimed_at, created_at, updated_at
            ) VALUES (?, 'roll', 'account:1', 'claimed', ?, ?, ?)
            """,
            (
                received.source_event_id,
                "2026-07-18T03:01:00+00:00",
                "2026-07-18T03:01:00+00:00",
                "2026-07-18T03:01:00+00:00",
            ),
        )
        before = tuple(
            connection.execute(
                "SELECT status, legacy_import_event_id FROM discord_source_events"
            ).fetchone()
        ) + (
            connection.execute("SELECT COUNT(*) FROM discord_processing_attempts").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM discord_projection_links").fetchone()[0],
        )

    record_attribution(
        repository,
        received.source_event_id,
        recorded_at=datetime(2026, 7, 18, 3, 2, tzinfo=timezone.utc),
    )

    with connect(database_path) as connection:
        after = tuple(
            connection.execute(
                "SELECT status, legacy_import_event_id FROM discord_source_events"
            ).fetchone()
        ) + (
            connection.execute("SELECT COUNT(*) FROM discord_processing_attempts").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM discord_projection_links").fetchone()[0],
        )
    assert after == before


@pytest.mark.parametrize(
    "status, server_name, account_name",
    [
        ("resolved", "  Configured Server  ", "  Configured Account  "),
        ("unresolved", None, None),
        ("ambiguous", None, None),
    ],
)
def test_record_and_read_account_attribution(
    tmp_path, status: str, server_name: str | None, account_name: str | None
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    recorded_at = datetime(2026, 7, 18, 3, 2, tzinfo=timezone.utc)

    result = record_account_attribution(
        repository,
        received.source_event_id,
        status=status,
        server_name=server_name,
        account_name=account_name,
        recorded_at=recorded_at,
    )

    assert isinstance(result, DiscordSourceEventAccountAttribution)
    assert result.source_event_id == received.source_event_id
    assert result.status == status
    assert result.server_name == server_name
    assert result.account_name == account_name
    assert result.created_at == recorded_at
    assert result.updated_at == recorded_at
    assert repository.get_account_attribution(received.source_event_id) == result
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM discord_source_event_account_attributions"
        ).fetchone()
    assert dict(row) == {
        "source_event_id": received.source_event_id,
        "status": status,
        "server_name": server_name,
        "account_name": account_name,
        "created_at": recorded_at.isoformat(),
        "updated_at": recorded_at.isoformat(),
    }


@pytest.mark.parametrize(
    "status, server_name, account_name",
    [
        ("resolved", "Server A", "Account A"),
        ("unresolved", None, None),
        ("ambiguous", None, None),
    ],
)
def test_exact_account_attribution_replay_is_idempotent(
    tmp_path, status: str, server_name: str | None, account_name: str | None
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    first_time = datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc)
    replay_time = datetime(2026, 7, 18, 4, 0, tzinfo=timezone.utc)

    first = record_account_attribution(
        repository,
        received.source_event_id,
        status=status,
        server_name=server_name,
        account_name=account_name,
        recorded_at=first_time,
    )
    replay = record_account_attribution(
        repository,
        received.source_event_id,
        status=status,
        server_name=server_name,
        account_name=account_name,
        recorded_at=replay_time,
    )

    assert replay == first
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_source_event_account_attributions"
        ).fetchone()[0] == 1
        row = connection.execute(
            "SELECT created_at, updated_at "
            "FROM discord_source_event_account_attributions"
        ).fetchone()
    assert tuple(row) == (first_time.isoformat(), first_time.isoformat())


@pytest.mark.parametrize("initial_status", ["unresolved", "ambiguous"])
def test_unresolved_or_ambiguous_account_attribution_can_resolve(
    tmp_path, initial_status: str
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    first_time = datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc)
    resolved_time = datetime(2026, 7, 18, 3, 1, tzinfo=timezone.utc)

    first = record_account_attribution(
        repository,
        received.source_event_id,
        status=initial_status,
        server_name=None,
        account_name=None,
        recorded_at=first_time,
    )
    resolved = record_account_attribution(
        repository,
        received.source_event_id,
        status="resolved",
        server_name="Server A",
        account_name="Account A",
        recorded_at=resolved_time,
    )

    assert first.status == initial_status
    assert resolved.status == "resolved"
    assert resolved.server_name == "Server A"
    assert resolved.account_name == "Account A"
    assert resolved.created_at == first_time
    assert resolved.updated_at == resolved_time


def test_resolved_account_attribution_conflicts_with_different_identity(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    record_account_attribution(repository, received.source_event_id)

    with pytest.raises(DiscordSourceEventAccountAttributionConflictError, match="immutable"):
        record_account_attribution(
            repository,
            received.source_event_id,
            server_name="Server B",
            recorded_at=datetime(2026, 7, 18, 4, 0, tzinfo=timezone.utc),
        )
    with connect(database_path) as connection:
        assert tuple(
            connection.execute(
                "SELECT status, server_name, account_name "
                "FROM discord_source_event_account_attributions"
            ).fetchone()
        ) == ("resolved", "Server A", "Account A")


@pytest.mark.parametrize("status", ["unresolved", "ambiguous"])
def test_resolved_account_attribution_cannot_be_downgraded(tmp_path, status: str) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    record_account_attribution(repository, received.source_event_id)

    with pytest.raises(DiscordSourceEventAccountAttributionConflictError, match="immutable"):
        record_account_attribution(
            repository,
            received.source_event_id,
            status=status,
            server_name=None,
            account_name=None,
        )


@pytest.mark.parametrize(
    "initial_status, next_status",
    [("unresolved", "ambiguous"), ("ambiguous", "unresolved")],
)
def test_nonresolved_account_attribution_rewrite_conflicts(
    tmp_path, initial_status: str, next_status: str
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    record_account_attribution(
        repository,
        received.source_event_id,
        status=initial_status,
        server_name=None,
        account_name=None,
    )

    with pytest.raises(DiscordSourceEventAccountAttributionConflictError, match="immutable"):
        record_account_attribution(
            repository,
            received.source_event_id,
            status=next_status,
            server_name=None,
            account_name=None,
        )


@pytest.mark.parametrize(
    "status, server_name, account_name",
    [
        ("invalid", None, None),
        ("resolved", None, "Account A"),
        ("resolved", "Server A", None),
        ("resolved", "   ", "Account A"),
        ("resolved", "Server A", "   "),
        ("unresolved", "Server A", None),
        ("unresolved", None, "Account A"),
        ("ambiguous", "Server A", None),
        ("ambiguous", None, "Account A"),
    ],
)
def test_invalid_account_attribution_status_and_identity_fails_before_persistence(
    tmp_path,
    status: str,
    server_name: str | None,
    account_name: str | None,
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)

    with pytest.raises(DiscordSourceEventAccountAttributionValidationError):
        record_account_attribution(
            repository,
            received.source_event_id,
            status=status,
            server_name=server_name,
            account_name=account_name,
        )
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_source_event_account_attributions"
        ).fetchone()[0] == 0


def test_missing_source_event_account_attribution_fails_without_writes(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)

    assert repository.get_account_attribution(999) is None
    with pytest.raises(DiscordSourceEventAccountAttributionNotFoundError, match="999"):
        record_account_attribution(repository, 999)
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_source_event_account_attributions"
        ).fetchone()[0] == 0


def test_account_attribution_does_not_change_other_durable_lifecycle_state(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    attempt = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc),
    )
    with connect(database_path) as connection:
        import_event_id = connection.execute(
            """
            INSERT INTO import_events (kind, source, observed_at, raw_message)
            VALUES ('roll', 'discord', ?, 'legacy')
            """,
            ("2026-07-18T03:01:00+00:00",),
        ).lastrowid
    repository.mark_processing_success(
        source_event_id=received.source_event_id,
        attempt_id=attempt.attempt_id,
        finished_at=datetime(2026, 7, 18, 3, 1, tzinfo=timezone.utc),
        legacy_import_event_id=import_event_id,
    )
    record_attribution(repository, received.source_event_id)
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO discord_projection_links (
                source_event_id, projection_kind, projection_slot, state,
                claimed_at, created_at, updated_at
            ) VALUES (?, 'roll', 'account:1', 'claimed', ?, ?, ?)
            """,
            (
                received.source_event_id,
                "2026-07-18T03:01:00+00:00",
                "2026-07-18T03:01:00+00:00",
                "2026-07-18T03:01:00+00:00",
            ),
        )
        before = (
            tuple(
                connection.execute(
                    "SELECT status, legacy_import_event_id "
                    "FROM discord_source_events"
                ).fetchone()
            ),
            tuple(
                connection.execute(
                    "SELECT status, server_name, created_at, updated_at "
                    "FROM discord_source_event_server_attributions"
                ).fetchone()
            ),
            tuple(
                connection.execute(
                    "SELECT status, retryable, finished_at "
                    "FROM discord_processing_attempts"
                ).fetchone()
            ),
            connection.execute("SELECT COUNT(*) FROM discord_projection_links").fetchone()[0],
        )

    record_account_attribution(
        repository,
        received.source_event_id,
        recorded_at=datetime(2026, 7, 18, 3, 2, tzinfo=timezone.utc),
    )

    with connect(database_path) as connection:
        after = (
            tuple(
                connection.execute(
                    "SELECT status, legacy_import_event_id "
                    "FROM discord_source_events"
                ).fetchone()
            ),
            tuple(
                connection.execute(
                    "SELECT status, server_name, created_at, updated_at "
                    "FROM discord_source_event_server_attributions"
                ).fetchone()
            ),
            tuple(
                connection.execute(
                    "SELECT status, retryable, finished_at "
                    "FROM discord_processing_attempts"
                ).fetchone()
            ),
            connection.execute("SELECT COUNT(*) FROM discord_projection_links").fetchone()[0],
        )
        assert tuple(
            connection.execute(
                "SELECT server_name, account_name "
                "FROM discord_source_event_account_attributions"
            ).fetchone()
        ) == ("Server A", "Account A")
    assert after == before


def test_source_event_deletion_cascades_account_attribution(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    record_account_attribution(repository, received.source_event_id)

    with connect(database_path) as connection:
        connection.execute(
            "DELETE FROM discord_source_events WHERE id = ?", (received.source_event_id,)
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_source_event_account_attributions"
        ).fetchone()[0] == 0


def test_caller_owned_attribution_read_seams_use_current_transaction_without_writes(
    tmp_path,
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    timestamp = datetime(2026, 7, 18, 5, 0, tzinfo=timezone.utc).isoformat()

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        connection.execute(
            """
            INSERT INTO discord_source_event_server_attributions (
                source_event_id, status, server_name, created_at, updated_at
            ) VALUES (?, 'resolved', 'Server A', ?, ?)
            """,
            (received.source_event_id, timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO discord_source_event_account_attributions (
                source_event_id, status, server_name, account_name, created_at, updated_at
            ) VALUES (?, 'resolved', 'Server A', 'Account A', ?, ?)
            """,
            (received.source_event_id, timestamp, timestamp),
        )
        before = (
            tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM discord_source_event_server_attributions"
                )
            ),
            tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM discord_source_event_account_attributions"
                )
            ),
            tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT status, legacy_import_event_id FROM discord_source_events"
                )
            ),
            connection.execute(
                "SELECT COUNT(*) FROM discord_processing_attempts"
            ).fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM discord_projection_links").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0],
        )
        statements: list[str] = []
        connection.set_trace_callback(statements.append)
        assert (
            repository._get_server_attribution_with_connection(
                connection, received.source_event_id
            ).server_name
            == "Server A"
        )
        assert (
            repository._get_account_attribution_with_connection(
                connection, received.source_event_id
            ).account_name
            == "Account A"
        )
        assert not any(
            statement.strip().upper().startswith(("COMMIT", "ROLLBACK"))
            for statement in statements
        )
        after = (
            tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM discord_source_event_server_attributions"
                )
            ),
            tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM discord_source_event_account_attributions"
                )
            ),
            tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT status, legacy_import_event_id FROM discord_source_events"
                )
            ),
            connection.execute(
                "SELECT COUNT(*) FROM discord_processing_attempts"
            ).fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM discord_projection_links").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0],
        )
        assert after == before
        connection.rollback()
