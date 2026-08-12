import sqlite3
import threading
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect, run_write_transaction
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories import discord_message_repository as discord_message_repository_module
from moa.repositories.discord_message_repository import (
    AntidisableResponseBinding,
    AntidisableResponseBindingMutationResult,
    AntidisableResponseResolutionResult,
    AntidisableWorkflow,
    AntidisableWorkflowMutationResult,
    DiscordAntidisableWorkflowConflictError,
    DiscordAntidisableWorkflowNotFoundError,
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


_THREAD_TIMEOUT = 5.0


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
    existing = receive(repository)
    with connect(database_path) as connection:
        existing_row = tuple(
            connection.execute(
                "SELECT id, event_key, revision_id, delivery_count, status "
                "FROM discord_source_events WHERE id = ?",
                (existing.source_event_id,),
            ).fetchone()
        )
        connection.execute(
            """
            CREATE TRIGGER fail_discord_source_event_insert
            BEFORE INSERT ON discord_source_events
            BEGIN
                SELECT RAISE(ABORT, 'forced receive failure');
            END
            """
        )

    other_message = aggregate(message_id="message-2")
    with pytest.raises(sqlite3.IntegrityError, match="forced receive failure"):
        receive(
            repository,
            message=other_message,
            message_revision=revision(other_message),
            event_key="event-2",
        )

    assert counts(database_path) == (1, 1, 1, 0)
    with connect(database_path) as connection:
        row_after_failure = tuple(
            connection.execute(
                "SELECT id, event_key, revision_id, delivery_count, status "
                "FROM discord_source_events WHERE id = ?",
                (existing.source_event_id,),
            ).fetchone()
        )
        connection.execute("DROP TRIGGER fail_discord_source_event_insert")
    assert row_after_failure == existing_row

    recovered = receive(
        repository,
        message=other_message,
        message_revision=revision(other_message),
        event_key="event-2",
    )

    assert recovered.aggregate_created is True
    assert recovered.revision_created is True
    assert recovered.source_event_created is True
    assert recovered.delivery_count == 1
    assert recovered.status == "received"
    assert counts(database_path) == (2, 2, 2, 0)


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


def test_public_receive_message_uses_one_runner_callback(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    original_runner = discord_message_repository_module.run_write_transaction
    callback_states: list[bool] = []

    def observed_runner(database_path, callback):
        def observed_callback(connection):
            callback_states.append(connection.in_transaction)
            return callback(connection)

        return original_runner(database_path, observed_callback)

    def unexpected_connection():
        raise AssertionError("message receipt opened an independent repository connection")

    monkeypatch.setattr(
        discord_message_repository_module, "run_write_transaction", observed_runner
    )
    monkeypatch.setattr(repository, "_connection", unexpected_connection)

    result = receive(repository)

    assert result.status == "received"
    assert result.delivery_count == 1
    assert callback_states == [True]


def test_receive_message_serializes_competing_exact_duplicate(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "messages.db"
    first_repository = DiscordMessageRepository(database_path)
    second_repository = DiscordMessageRepository(database_path)
    original_runner = discord_message_repository_module.run_write_transaction
    first_callback_entered = threading.Event()
    release_first_callback = threading.Event()
    second_runner_called = threading.Event()
    second_callback_entered = threading.Event()
    callback_count = 0
    results = {}
    failures: list[BaseException] = []

    def observed_runner(database_path, callback):
        if threading.current_thread().name == "second-message-receipt":
            second_runner_called.set()

        def observed_callback(connection):
            nonlocal callback_count
            callback_count += 1
            if callback_count == 1:
                first_callback_entered.set()
                assert release_first_callback.wait(
                    _THREAD_TIMEOUT
                ), "first message-receipt callback was not released"
            else:
                second_callback_entered.set()
            return callback(connection)

        return original_runner(database_path, observed_callback)

    monkeypatch.setattr(
        discord_message_repository_module, "run_write_transaction", observed_runner
    )
    first_time = datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc)
    duplicate_time = datetime(2026, 7, 18, 4, 0, tzinfo=timezone.utc)

    def receive_first() -> None:
        try:
            results["first"] = receive(first_repository, received_at=first_time)
        except BaseException as error:
            failures.append(error)

    def receive_duplicate() -> None:
        try:
            results["duplicate"] = receive(
                second_repository, received_at=duplicate_time
            )
        except BaseException as error:
            failures.append(error)

    first_thread = threading.Thread(target=receive_first, name="first-message-receipt")
    second_thread = threading.Thread(
        target=receive_duplicate, name="second-message-receipt"
    )
    first_thread.start()
    assert first_callback_entered.wait(_THREAD_TIMEOUT), "first callback did not enter"
    second_thread.start()
    assert second_runner_called.wait(_THREAD_TIMEOUT), "second caller did not reach the runner"
    assert not second_callback_entered.is_set()

    release_first_callback.set()
    first_thread.join(_THREAD_TIMEOUT)
    second_thread.join(_THREAD_TIMEOUT)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert failures == []
    first = results["first"]
    duplicate = results["duplicate"]
    assert (
        duplicate.aggregate_id,
        duplicate.revision_id,
        duplicate.source_event_id,
    ) == (first.aggregate_id, first.revision_id, first.source_event_id)
    assert first.source_event_created is True
    assert duplicate.aggregate_created is False
    assert duplicate.revision_created is False
    assert duplicate.source_event_created is False
    assert duplicate.delivery_count == 2
    assert second_callback_entered.is_set()
    assert callback_count == 2
    assert counts(database_path) == (1, 1, 1, 0)
    with connect(database_path) as connection:
        aggregate_row = connection.execute(
            "SELECT first_received_at, last_received_at FROM discord_message_aggregates"
        ).fetchone()
        revision_row = connection.execute(
            "SELECT first_received_at, last_received_at FROM discord_message_revisions"
        ).fetchone()
        event_row = connection.execute(
            "SELECT received_at, last_seen_at, delivery_count FROM discord_source_events"
        ).fetchone()
    assert tuple(aggregate_row) == (first_time.isoformat(), duplicate_time.isoformat())
    assert tuple(revision_row) == (first_time.isoformat(), duplicate_time.isoformat())
    assert tuple(event_row) == (first_time.isoformat(), duplicate_time.isoformat(), 2)


def test_receive_message_serializes_competing_immutable_conflict(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "messages.db"
    first_repository = DiscordMessageRepository(database_path)
    second_repository = DiscordMessageRepository(database_path)
    original_runner = discord_message_repository_module.run_write_transaction
    first_callback_entered = threading.Event()
    release_first_callback = threading.Event()
    second_runner_called = threading.Event()
    second_callback_entered = threading.Event()
    callback_count = 0
    first_results = []
    second_failures: list[BaseException] = []

    def observed_runner(database_path, callback):
        if threading.current_thread().name == "conflicting-message-receipt":
            second_runner_called.set()

        def observed_callback(connection):
            nonlocal callback_count
            callback_count += 1
            if callback_count == 1:
                first_callback_entered.set()
                assert release_first_callback.wait(
                    _THREAD_TIMEOUT
                ), "first message-receipt callback was not released"
            else:
                second_callback_entered.set()
            return callback(connection)

        return original_runner(database_path, observed_callback)

    monkeypatch.setattr(
        discord_message_repository_module, "run_write_transaction", observed_runner
    )

    def receive_first() -> None:
        first_results.append(receive(first_repository))

    def receive_conflict() -> None:
        try:
            receive(second_repository, raw_text="conflicting immutable text")
        except BaseException as error:
            second_failures.append(error)

    first_thread = threading.Thread(target=receive_first, name="first-message-receipt")
    second_thread = threading.Thread(
        target=receive_conflict, name="conflicting-message-receipt"
    )
    first_thread.start()
    assert first_callback_entered.wait(_THREAD_TIMEOUT), "first callback did not enter"
    second_thread.start()
    assert second_runner_called.wait(_THREAD_TIMEOUT), "second caller did not reach the runner"
    assert not second_callback_entered.is_set()

    release_first_callback.set()
    first_thread.join(_THREAD_TIMEOUT)
    second_thread.join(_THREAD_TIMEOUT)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert len(first_results) == 1
    assert len(second_failures) == 1
    assert isinstance(second_failures[0], DiscordMessageReceiveConflictError)
    assert second_callback_entered.is_set()
    assert callback_count == 2
    assert counts(database_path) == (1, 1, 1, 0)
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT raw_text, delivery_count, status FROM discord_source_events"
        ).fetchone()
    assert tuple(row) == ("raw message", 1, "received")


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


def test_public_begin_processing_attempt_uses_one_runner_callback(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    original_runner = discord_message_repository_module.run_write_transaction
    callback_states: list[bool] = []

    def observed_runner(database_path, callback):
        def observed_callback(connection):
            callback_states.append(connection.in_transaction)
            return callback(connection)

        return original_runner(database_path, observed_callback)

    def unexpected_connection():
        raise AssertionError("processing attempt opened an independent repository connection")

    monkeypatch.setattr(
        discord_message_repository_module, "run_write_transaction", observed_runner
    )
    monkeypatch.setattr(repository, "_connection", unexpected_connection)

    result = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
    )

    assert result.attempt_number == 1
    assert result.source_event_status == "processing"
    assert callback_states == [True]


def test_begin_processing_attempt_rejects_missing_source_without_writes(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)

    with pytest.raises(DiscordMessageProcessingNotFoundError, match="999"):
        repository.begin_processing_attempt(
            source_event_id=999,
            parser_version="parser-1",
            router_version="router-1",
            started_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
        )

    assert counts(database_path) == (0, 0, 0, 0)


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


def test_public_mark_processing_success_uses_one_runner_callback(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    attempt = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
    )
    original_runner = discord_message_repository_module.run_write_transaction
    callback_states: list[bool] = []

    def observed_runner(database_path, callback):
        def observed_callback(connection):
            callback_states.append(connection.in_transaction)
            return callback(connection)

        return original_runner(database_path, observed_callback)

    def unexpected_connection():
        raise AssertionError("processing success opened an independent repository connection")

    monkeypatch.setattr(
        discord_message_repository_module, "run_write_transaction", observed_runner
    )
    monkeypatch.setattr(repository, "_connection", unexpected_connection)

    result = repository.mark_processing_success(
        source_event_id=received.source_event_id,
        attempt_id=attempt.attempt_id,
        finished_at=datetime(2026, 7, 18, 2, 1, tzinfo=timezone.utc),
    )

    assert result.attempt_status == "succeeded"
    assert result.source_event_status == "succeeded"
    assert callback_states == [True]


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


def test_public_mark_processing_failure_uses_one_runner_callback(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    attempt = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
    )
    original_runner = discord_message_repository_module.run_write_transaction
    callback_states: list[bool] = []

    def observed_runner(database_path, callback):
        def observed_callback(connection):
            callback_states.append(connection.in_transaction)
            return callback(connection)

        return original_runner(database_path, observed_callback)

    def unexpected_connection():
        raise AssertionError("processing failure opened an independent repository connection")

    monkeypatch.setattr(
        discord_message_repository_module, "run_write_transaction", observed_runner
    )
    monkeypatch.setattr(repository, "_connection", unexpected_connection)

    result = repository.mark_processing_failure(
        source_event_id=received.source_event_id,
        attempt_id=attempt.attempt_id,
        status="failed",
        retryable=True,
        failure_code="temporary",
        failure_detail="retry later",
        finished_at=datetime(2026, 7, 18, 2, 1, tzinfo=timezone.utc),
    )

    assert result.attempt_status == "failed"
    assert result.source_event_status == "failed"
    assert callback_states == [True]


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


def test_repeated_identical_processing_failure_is_rejected_without_mutation(tmp_path) -> None:
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
    values = {
        "source_event_id": received.source_event_id,
        "attempt_id": attempt.attempt_id,
        "status": "failed",
        "retryable": True,
        "failure_code": "temporary",
        "failure_detail": "retry later",
        "finished_at": finished_at,
    }
    repository.mark_processing_failure(**values)
    with connect(database_path) as connection:
        before = tuple(
            connection.execute(
                "SELECT status, retryable, finished_at, failure_code, failure_detail "
                "FROM discord_processing_attempts WHERE id = ?",
                (attempt.attempt_id,),
            ).fetchone()
        ) + tuple(
            connection.execute(
                "SELECT status, updated_at FROM discord_source_events WHERE id = ?",
                (received.source_event_id,),
            ).fetchone()
        )

    with pytest.raises(DiscordMessageProcessingConflictError, match="not processing"):
        repository.mark_processing_failure(**values)

    with connect(database_path) as connection:
        after = tuple(
            connection.execute(
                "SELECT status, retryable, finished_at, failure_code, failure_detail "
                "FROM discord_processing_attempts WHERE id = ?",
                (attempt.attempt_id,),
            ).fetchone()
        ) + tuple(
            connection.execute(
                "SELECT status, updated_at FROM discord_source_events WHERE id = ?",
                (received.source_event_id,),
            ).fetchone()
        )
    assert after == before


def test_processing_failure_rejects_attempt_owned_by_another_source_without_writes(
    tmp_path,
) -> None:
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

    with pytest.raises(DiscordMessageProcessingConflictError, match="belongs to another"):
        repository.mark_processing_failure(
            source_event_id=second.source_event_id,
            attempt_id=attempt.attempt_id,
            status="failed",
            retryable=True,
            failure_code="temporary",
            failure_detail=None,
            finished_at=datetime(2026, 7, 18, 2, 1, tzinfo=timezone.utc),
        )

    with connect(database_path) as connection:
        event_statuses = connection.execute(
            "SELECT status FROM discord_source_events ORDER BY id"
        ).fetchall()
        attempt_rows = connection.execute(
            "SELECT source_event_id, status, retryable, finished_at, failure_code, failure_detail "
            "FROM discord_processing_attempts"
        ).fetchall()
    assert [row[0] for row in event_statuses] == ["processing", "received"]
    assert [tuple(row) for row in attempt_rows] == [
        (first.source_event_id, "processing", 0, None, None, None)
    ]


def test_retryable_unresolved_attribution_allows_next_processing_attempt(tmp_path) -> None:
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
        status="unresolved_attribution",
        retryable=True,
        failure_code="unresolved_server",
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
    assert second.attempt_status == "processing"
    assert second.source_event_status == "processing"
    with connect(database_path) as connection:
        attempts = connection.execute(
            "SELECT attempt_number, status, retryable "
            "FROM discord_processing_attempts ORDER BY attempt_number"
        ).fetchall()
        event_status = connection.execute(
            "SELECT status FROM discord_source_events WHERE id = ?",
            (received.source_event_id,),
        ).fetchone()[0]
    assert [tuple(row) for row in attempts] == [
        (1, "unresolved_attribution", 1),
        (2, "processing", 0),
    ]
    assert event_status == "processing"


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


def test_begin_mid_transaction_failure_rolls_back_and_same_database_recovers(tmp_path) -> None:
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
        failure_detail="retry",
        finished_at=datetime(2026, 7, 18, 2, 1, tzinfo=timezone.utc),
    )
    with connect(database_path) as connection:
        prior_attempt = tuple(
            connection.execute(
                "SELECT id, attempt_number, status, retryable, finished_at "
                "FROM discord_processing_attempts WHERE id = ?",
                (first.attempt_id,),
            ).fetchone()
        )
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
            parser_version="parser-2",
            router_version="router-2",
            started_at=datetime(2026, 7, 18, 2, 2, tzinfo=timezone.utc),
        )
    assert counts(database_path)[3] == 1
    with connect(database_path) as connection:
        event_status = connection.execute(
            "SELECT status FROM discord_source_events"
        ).fetchone()[0]
        attempts_after_failure = connection.execute(
            "SELECT id, attempt_number, status, retryable, finished_at "
            "FROM discord_processing_attempts ORDER BY attempt_number"
        ).fetchall()
        connection.execute("DROP TRIGGER fail_processing_event_update")
    assert event_status == "failed"
    assert [tuple(row) for row in attempts_after_failure] == [prior_attempt]

    recovered = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-2",
        router_version="router-2",
        started_at=datetime(2026, 7, 18, 2, 3, tzinfo=timezone.utc),
    )

    assert recovered.attempt_number == 2
    assert recovered.attempt_status == "processing"
    assert recovered.source_event_status == "processing"
    with connect(database_path) as connection:
        attempts_after_recovery = connection.execute(
            "SELECT attempt_number, status FROM discord_processing_attempts "
            "ORDER BY attempt_number"
        ).fetchall()
        recovered_event_status = connection.execute(
            "SELECT status FROM discord_source_events"
        ).fetchone()[0]
    assert [tuple(row) for row in attempts_after_recovery] == [
        (1, "failed"),
        (2, "processing"),
    ]
    assert recovered_event_status == "processing"


def test_begin_processing_attempt_serializes_competing_same_event_callers(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "messages.db"
    first_repository = DiscordMessageRepository(database_path)
    second_repository = DiscordMessageRepository(database_path)
    received = receive(first_repository)
    original_runner = discord_message_repository_module.run_write_transaction
    first_callback_completed = threading.Event()
    release_first_callback = threading.Event()
    second_runner_called = threading.Event()
    second_callback_entered = threading.Event()
    callback_count = 0
    first_results = []
    second_failures: list[BaseException] = []

    def observed_runner(database_path, callback):
        if threading.current_thread().name == "second-processing-attempt":
            second_runner_called.set()

        def observed_callback(connection):
            nonlocal callback_count
            callback_count += 1
            if threading.current_thread().name == "first-processing-attempt":
                result = callback(connection)
                first_callback_completed.set()
                assert release_first_callback.wait(
                    _THREAD_TIMEOUT
                ), "first processing-attempt callback was not released"
                return result
            second_callback_entered.set()
            return callback(connection)

        return original_runner(database_path, observed_callback)

    monkeypatch.setattr(
        discord_message_repository_module, "run_write_transaction", observed_runner
    )

    def begin_first() -> None:
        first_results.append(
            first_repository.begin_processing_attempt(
                source_event_id=received.source_event_id,
                parser_version="parser-1",
                router_version="router-1",
                started_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
            )
        )

    def begin_second() -> None:
        try:
            second_repository.begin_processing_attempt(
                source_event_id=received.source_event_id,
                parser_version="parser-2",
                router_version="router-2",
                started_at=datetime(2026, 7, 18, 2, 1, tzinfo=timezone.utc),
            )
        except BaseException as error:
            second_failures.append(error)

    first_thread = threading.Thread(target=begin_first, name="first-processing-attempt")
    second_thread = threading.Thread(target=begin_second, name="second-processing-attempt")
    first_thread.start()
    assert first_callback_completed.wait(_THREAD_TIMEOUT), "first callback did not complete"
    second_thread.start()
    assert second_runner_called.wait(_THREAD_TIMEOUT), "second caller did not reach the runner"
    assert not second_callback_entered.is_set()

    release_first_callback.set()
    first_thread.join(_THREAD_TIMEOUT)
    second_thread.join(_THREAD_TIMEOUT)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert len(first_results) == 1
    assert len(second_failures) == 1
    assert isinstance(second_failures[0], DiscordMessageProcessingConflictError)
    assert second_callback_entered.is_set()
    assert callback_count == 2
    with connect(database_path) as connection:
        attempts = connection.execute(
            "SELECT attempt_number, status FROM discord_processing_attempts"
        ).fetchall()
        event_status = connection.execute(
            "SELECT status FROM discord_source_events"
        ).fetchone()[0]
    assert [tuple(row) for row in attempts] == [(1, "processing")]
    assert event_status == "processing"


def test_processing_failure_serializes_competing_same_attempt_callers(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "messages.db"
    first_repository = DiscordMessageRepository(database_path)
    second_repository = DiscordMessageRepository(database_path)
    received = receive(first_repository)
    attempt = first_repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
    )
    original_runner = discord_message_repository_module.run_write_transaction
    first_callback_completed = threading.Event()
    release_first_callback = threading.Event()
    second_runner_called = threading.Event()
    second_callback_entered = threading.Event()
    callback_count = 0
    first_results = []
    first_failures: list[BaseException] = []
    second_failures: list[BaseException] = []

    def observed_runner(database_path, callback):
        if threading.current_thread().name == "second-processing-failure":
            second_runner_called.set()

        def observed_callback(connection):
            nonlocal callback_count
            callback_count += 1
            if threading.current_thread().name == "first-processing-failure":
                result = callback(connection)
                first_callback_completed.set()
                assert release_first_callback.wait(
                    _THREAD_TIMEOUT
                ), "first processing-failure callback was not released"
                return result
            second_callback_entered.set()
            return callback(connection)

        return original_runner(database_path, observed_callback)

    monkeypatch.setattr(
        discord_message_repository_module, "run_write_transaction", observed_runner
    )

    def fail_first() -> None:
        try:
            first_results.append(
                first_repository.mark_processing_failure(
                    source_event_id=received.source_event_id,
                    attempt_id=attempt.attempt_id,
                    status="failed",
                    retryable=True,
                    failure_code="first_failure",
                    failure_detail="first terminal decision",
                    finished_at=datetime(2026, 7, 18, 2, 1, tzinfo=timezone.utc),
                )
            )
        except BaseException as error:
            first_failures.append(error)

    def fail_second() -> None:
        try:
            second_repository.mark_processing_failure(
                source_event_id=received.source_event_id,
                attempt_id=attempt.attempt_id,
                status="unresolved_attribution",
                retryable=False,
                failure_code="second_failure",
                failure_detail="competing terminal decision",
                finished_at=datetime(2026, 7, 18, 2, 2, tzinfo=timezone.utc),
            )
        except BaseException as error:
            second_failures.append(error)

    first_thread = threading.Thread(target=fail_first, name="first-processing-failure")
    second_thread = threading.Thread(target=fail_second, name="second-processing-failure")
    first_thread.start()
    assert first_callback_completed.wait(_THREAD_TIMEOUT), "first callback did not complete"
    second_thread.start()
    assert second_runner_called.wait(_THREAD_TIMEOUT), "second caller did not reach the runner"
    assert not second_callback_entered.is_set()

    release_first_callback.set()
    first_thread.join(_THREAD_TIMEOUT)
    second_thread.join(_THREAD_TIMEOUT)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert first_failures == []
    assert len(first_results) == 1
    assert first_results[0].attempt_status == "failed"
    assert len(second_failures) == 1
    assert isinstance(second_failures[0], DiscordMessageProcessingConflictError)
    assert second_callback_entered.is_set()
    assert callback_count == 2
    with connect(database_path) as connection:
        attempt_row = connection.execute(
            "SELECT status, retryable, failure_code, failure_detail "
            "FROM discord_processing_attempts WHERE id = ?",
            (attempt.attempt_id,),
        ).fetchone()
        event_status = connection.execute(
            "SELECT status FROM discord_source_events WHERE id = ?",
            (received.source_event_id,),
        ).fetchone()[0]
    assert tuple(attempt_row) == (
        "failed",
        1,
        "first_failure",
        "first terminal decision",
    )
    assert event_status == "failed"


def test_processing_failure_serializes_after_public_processing_success(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "messages.db"
    success_repository = DiscordMessageRepository(database_path)
    failure_repository = DiscordMessageRepository(database_path)
    received = receive(success_repository)
    attempt = success_repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
    )
    original_runner = discord_message_repository_module.run_write_transaction
    success_callback_completed = threading.Event()
    release_success_callback = threading.Event()
    failure_runner_called = threading.Event()
    failure_callback_entered = threading.Event()
    success_results = []
    success_failures: list[BaseException] = []
    failure_failures: list[BaseException] = []

    def observed_runner(database_path, callback):
        if threading.current_thread().name == "public-processing-success":
            def observed_success_callback(connection):
                result = callback(connection)
                success_callback_completed.set()
                assert release_success_callback.wait(
                    _THREAD_TIMEOUT
                ), "public success callback was not released"
                return result

            return original_runner(database_path, observed_success_callback)

        failure_runner_called.set()

        def observed_failure_callback(connection):
            failure_callback_entered.set()
            return callback(connection)

        return original_runner(database_path, observed_failure_callback)

    monkeypatch.setattr(
        discord_message_repository_module,
        "run_write_transaction",
        observed_runner,
    )

    def complete_success() -> None:
        try:
            success_results.append(
                success_repository.mark_processing_success(
                    source_event_id=received.source_event_id,
                    attempt_id=attempt.attempt_id,
                    finished_at=datetime(2026, 7, 18, 2, 1, tzinfo=timezone.utc),
                )
            )
        except BaseException as error:
            success_failures.append(error)

    def complete_failure() -> None:
        try:
            failure_repository.mark_processing_failure(
                source_event_id=received.source_event_id,
                attempt_id=attempt.attempt_id,
                status="failed",
                retryable=True,
                failure_code="late_failure",
                failure_detail="must not overwrite success",
                finished_at=datetime(2026, 7, 18, 2, 2, tzinfo=timezone.utc),
            )
        except BaseException as error:
            failure_failures.append(error)

    success_thread = threading.Thread(target=complete_success, name="public-processing-success")
    failure_thread = threading.Thread(target=complete_failure, name="public-processing-failure")
    success_thread.start()
    assert success_callback_completed.wait(_THREAD_TIMEOUT), "success callback did not complete"
    failure_thread.start()
    assert failure_runner_called.wait(_THREAD_TIMEOUT), "failure caller did not reach the runner"
    assert not failure_callback_entered.is_set()

    release_success_callback.set()
    success_thread.join(_THREAD_TIMEOUT)
    failure_thread.join(_THREAD_TIMEOUT)

    assert not success_thread.is_alive()
    assert not failure_thread.is_alive()
    assert success_failures == []
    assert len(success_results) == 1
    assert success_results[0].attempt_status == "succeeded"
    assert len(failure_failures) == 1
    assert isinstance(failure_failures[0], DiscordMessageProcessingConflictError)
    assert failure_callback_entered.is_set()
    with connect(database_path) as connection:
        attempt_row = connection.execute(
            "SELECT status, retryable, finished_at, failure_code, failure_detail "
            "FROM discord_processing_attempts WHERE id = ?",
            (attempt.attempt_id,),
        ).fetchone()
        event_status = connection.execute(
            "SELECT status FROM discord_source_events WHERE id = ?",
            (received.source_event_id,),
        ).fetchone()[0]
    assert tuple(attempt_row) == (
        "succeeded",
        0,
        "2026-07-18T02:01:00+00:00",
        None,
        None,
    )
    assert event_status == "succeeded"


def test_public_processing_success_serializes_competing_success_callers(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "messages.db"
    first_repository = DiscordMessageRepository(database_path)
    second_repository = DiscordMessageRepository(database_path)
    received = receive(first_repository)
    attempt = first_repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
    )
    original_runner = discord_message_repository_module.run_write_transaction
    first_callback_completed = threading.Event()
    release_first_callback = threading.Event()
    second_runner_called = threading.Event()
    second_callback_entered = threading.Event()
    first_results = []
    first_failures: list[BaseException] = []
    second_failures: list[BaseException] = []

    def observed_runner(database_path, callback):
        if threading.current_thread().name == "first-processing-success":
            def observed_first_callback(connection):
                result = callback(connection)
                first_callback_completed.set()
                assert release_first_callback.wait(
                    _THREAD_TIMEOUT
                ), "first success callback was not released"
                return result

            return original_runner(database_path, observed_first_callback)

        second_runner_called.set()

        def observed_second_callback(connection):
            second_callback_entered.set()
            return callback(connection)

        return original_runner(database_path, observed_second_callback)

    monkeypatch.setattr(
        discord_message_repository_module, "run_write_transaction", observed_runner
    )

    def complete_first() -> None:
        try:
            first_results.append(
                first_repository.mark_processing_success(
                    source_event_id=received.source_event_id,
                    attempt_id=attempt.attempt_id,
                    finished_at=datetime(2026, 7, 18, 2, 1, tzinfo=timezone.utc),
                )
            )
        except BaseException as error:
            first_failures.append(error)

    def complete_second() -> None:
        try:
            second_repository.mark_processing_success(
                source_event_id=received.source_event_id,
                attempt_id=attempt.attempt_id,
                finished_at=datetime(2026, 7, 18, 2, 2, tzinfo=timezone.utc),
            )
        except BaseException as error:
            second_failures.append(error)

    first_thread = threading.Thread(target=complete_first, name="first-processing-success")
    second_thread = threading.Thread(target=complete_second, name="second-processing-success")
    first_thread.start()
    assert first_callback_completed.wait(_THREAD_TIMEOUT), "first callback did not complete"
    second_thread.start()
    assert second_runner_called.wait(_THREAD_TIMEOUT), "second caller did not reach the runner"
    assert not second_callback_entered.is_set()

    release_first_callback.set()
    first_thread.join(_THREAD_TIMEOUT)
    second_thread.join(_THREAD_TIMEOUT)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert first_failures == []
    assert len(first_results) == 1
    assert first_results[0].attempt_status == "succeeded"
    assert len(second_failures) == 1
    assert isinstance(second_failures[0], DiscordMessageProcessingConflictError)
    assert second_callback_entered.is_set()
    with connect(database_path) as connection:
        attempt_row = connection.execute(
            "SELECT status, retryable, finished_at, failure_code, failure_detail "
            "FROM discord_processing_attempts WHERE id = ?",
            (attempt.attempt_id,),
        ).fetchone()
        event_row = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (received.source_event_id,),
        ).fetchone()
    assert tuple(attempt_row) == (
        "succeeded",
        0,
        "2026-07-18T02:01:00+00:00",
        None,
        None,
    )
    assert tuple(event_row) == ("succeeded", None)


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


def test_processing_success_second_write_failure_rolls_back_link_and_same_database_recovers(
    tmp_path,
) -> None:
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
        prior_attempt = tuple(
            connection.execute(
                "SELECT status, retryable, finished_at, failure_code, failure_detail "
                "FROM discord_processing_attempts WHERE id = ?",
                (attempt.attempt_id,),
            ).fetchone()
        )
        prior_event = tuple(
            connection.execute(
                "SELECT status, legacy_import_event_id, updated_at "
                "FROM discord_source_events WHERE id = ?",
                (received.source_event_id,),
            ).fetchone()
        )
        connection.execute(
            """
            CREATE TRIGGER fail_processing_success_event_update
            BEFORE UPDATE OF status ON discord_source_events
            WHEN NEW.status = 'succeeded'
            BEGIN
                SELECT RAISE(ABORT, 'forced processing success event update');
            END
            """
        )

    with pytest.raises(
        sqlite3.IntegrityError, match="forced processing success event update"
    ):
        repository.mark_processing_success(
            source_event_id=received.source_event_id,
            attempt_id=attempt.attempt_id,
            finished_at=finished_at,
            legacy_import_event_id=import_event_id,
        )

    with connect(database_path) as connection:
        rolled_back_attempt = tuple(
            connection.execute(
                "SELECT status, retryable, finished_at, failure_code, failure_detail "
                "FROM discord_processing_attempts WHERE id = ?",
                (attempt.attempt_id,),
            ).fetchone()
        )
        rolled_back_event = tuple(
            connection.execute(
                "SELECT status, legacy_import_event_id, updated_at "
                "FROM discord_source_events WHERE id = ?",
                (received.source_event_id,),
            ).fetchone()
        )
        connection.execute("DROP TRIGGER fail_processing_success_event_update")
    assert rolled_back_attempt == prior_attempt == ("processing", 0, None, None, None)
    assert rolled_back_event == prior_event
    assert rolled_back_event[0] == "processing"
    assert rolled_back_event[1] is None

    result = repository.mark_processing_success(
        source_event_id=received.source_event_id,
        attempt_id=attempt.attempt_id,
        finished_at=finished_at,
        legacy_import_event_id=import_event_id,
    )

    assert result.attempt_status == "succeeded"
    assert result.source_event_status == "succeeded"
    assert result.legacy_import_event_id == import_event_id
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_processing_attempts WHERE source_event_id = ?",
            (received.source_event_id,),
        ).fetchone()[0] == 1
        terminal_attempt = connection.execute(
            "SELECT status, retryable, finished_at, failure_code, failure_detail "
            "FROM discord_processing_attempts WHERE id = ?",
            (attempt.attempt_id,),
        ).fetchone()
        terminal_event = connection.execute(
            "SELECT status, legacy_import_event_id, updated_at "
            "FROM discord_source_events WHERE id = ?",
            (received.source_event_id,),
        ).fetchone()
    assert tuple(terminal_attempt) == (
        "succeeded",
        0,
        finished_at.isoformat(),
        None,
        None,
    )
    assert tuple(terminal_event) == (
        "succeeded",
        import_event_id,
        finished_at.isoformat(),
    )


def test_processing_failure_second_write_failure_rolls_back_and_same_database_recovers(
    tmp_path,
) -> None:
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
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_processing_failure_event_update
            BEFORE UPDATE OF status ON discord_source_events
            WHEN NEW.status = 'failed'
            BEGIN
                SELECT RAISE(ABORT, 'forced processing failure event update');
            END
            """
        )

    with pytest.raises(
        sqlite3.IntegrityError, match="forced processing failure event update"
    ):
        repository.mark_processing_failure(
            source_event_id=received.source_event_id,
            attempt_id=attempt.attempt_id,
            status="failed",
            retryable=True,
            failure_code="temporary",
            failure_detail="retry later",
            finished_at=finished_at,
        )

    with connect(database_path) as connection:
        event_status = connection.execute(
            "SELECT status FROM discord_source_events WHERE id = ?",
            (received.source_event_id,),
        ).fetchone()[0]
        attempt_after_failure = connection.execute(
            "SELECT status, retryable, finished_at, failure_code, failure_detail "
            "FROM discord_processing_attempts WHERE id = ?",
            (attempt.attempt_id,),
        ).fetchone()
        attempt_count = connection.execute(
            "SELECT COUNT(*) FROM discord_processing_attempts"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER fail_processing_failure_event_update")
    assert event_status == "processing"
    assert tuple(attempt_after_failure) == ("processing", 0, None, None, None)
    assert attempt_count == 1

    recovered = repository.mark_processing_failure(
        source_event_id=received.source_event_id,
        attempt_id=attempt.attempt_id,
        status="failed",
        retryable=True,
        failure_code="temporary",
        failure_detail="retry later",
        finished_at=finished_at,
    )

    assert recovered.attempt_status == "failed"
    assert recovered.source_event_status == "failed"
    assert recovered.retryable is True
    assert recovered.finished_at == finished_at
    assert recovered.failure_code == "temporary"
    assert recovered.failure_detail == "retry later"
    with connect(database_path) as connection:
        final_attempts = connection.execute(
            "SELECT status, retryable, finished_at, failure_code, failure_detail "
            "FROM discord_processing_attempts"
        ).fetchall()
        final_events = connection.execute(
            "SELECT status FROM discord_source_events"
        ).fetchall()
    assert [tuple(row) for row in final_attempts] == [
        ("failed", 1, finished_at.isoformat(), "temporary", "retry later")
    ]
    assert [row[0] for row in final_events] == ["failed"]


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


def test_public_server_attribution_uses_one_runner_callback(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    original_runner = discord_message_repository_module.run_write_transaction
    callback_states: list[bool] = []

    def observed_runner(database_path, callback):
        def observed_callback(connection):
            callback_states.append(connection.in_transaction)
            return callback(connection)

        return original_runner(database_path, observed_callback)

    def unexpected_connection():
        raise AssertionError("server attribution opened an independent repository connection")

    monkeypatch.setattr(
        discord_message_repository_module, "run_write_transaction", observed_runner
    )
    monkeypatch.setattr(repository, "_connection", unexpected_connection)

    result = record_attribution(repository, received.source_event_id)

    assert result.status == "resolved"
    assert result.server_name == "Server A"
    assert callback_states == [True]


def test_server_attribution_failure_rolls_back_and_same_database_recovers(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    recorded_at = datetime(2026, 7, 18, 3, 2, tzinfo=timezone.utc)
    with connect(database_path) as connection:
        source_before = tuple(
            connection.execute(
                """
                SELECT id, event_key, revision_id, status, legacy_import_event_id
                FROM discord_source_events
                WHERE id = ?
                """,
                (received.source_event_id,),
            ).fetchone()
        )
        connection.execute(
            """
            CREATE TRIGGER fail_server_attribution_insert
            BEFORE INSERT ON discord_source_event_server_attributions
            BEGIN
                SELECT RAISE(ABORT, 'forced server attribution failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced server attribution failure"):
        record_attribution(
            repository,
            received.source_event_id,
            recorded_at=recorded_at,
        )

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_source_event_server_attributions"
        ).fetchone()[0] == 0
        source_after_failure = tuple(
            connection.execute(
                """
                SELECT id, event_key, revision_id, status, legacy_import_event_id
                FROM discord_source_events
                WHERE id = ?
                """,
                (received.source_event_id,),
            ).fetchone()
        )
        connection.execute("DROP TRIGGER fail_server_attribution_insert")
    assert source_after_failure == source_before

    recovered = record_attribution(
        repository,
        received.source_event_id,
        recorded_at=recorded_at,
    )

    assert recovered.status == "resolved"
    assert recovered.server_name == "Server A"
    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM discord_source_event_server_attributions"
        ).fetchall()
        source_after_recovery = tuple(
            connection.execute(
                """
                SELECT id, event_key, revision_id, status, legacy_import_event_id
                FROM discord_source_events
                WHERE id = ?
                """,
                (received.source_event_id,),
            ).fetchone()
        )
    assert len(rows) == 1
    assert dict(rows[0]) == {
        "source_event_id": received.source_event_id,
        "status": "resolved",
        "server_name": "Server A",
        "created_at": recorded_at.isoformat(),
        "updated_at": recorded_at.isoformat(),
    }
    assert source_after_recovery == source_before


def test_server_attribution_serializes_competing_exact_replay(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "messages.db"
    first_repository = DiscordMessageRepository(database_path)
    second_repository = DiscordMessageRepository(database_path)
    received = receive(first_repository)
    original_runner = discord_message_repository_module.run_write_transaction
    first_callback_entered = threading.Event()
    release_first_callback = threading.Event()
    second_runner_called = threading.Event()
    second_callback_entered = threading.Event()
    callback_count = 0
    results: list[DiscordSourceEventServerAttribution] = []
    failures: list[BaseException] = []

    def observed_runner(database_path, callback):
        if threading.current_thread().name == "second-server-attribution":
            second_runner_called.set()

        def observed_callback(connection):
            nonlocal callback_count
            callback_count += 1
            if callback_count == 1:
                first_callback_entered.set()
                assert release_first_callback.wait(
                    _THREAD_TIMEOUT
                ), "first server-attribution callback was not released"
            else:
                second_callback_entered.set()
            return callback(connection)

        return original_runner(database_path, observed_callback)

    monkeypatch.setattr(
        discord_message_repository_module, "run_write_transaction", observed_runner
    )
    first_time = datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc)
    replay_time = datetime(2026, 7, 18, 4, 0, tzinfo=timezone.utc)

    def record_first() -> None:
        try:
            results.append(
                record_attribution(
                    first_repository,
                    received.source_event_id,
                    recorded_at=first_time,
                )
            )
        except BaseException as error:
            failures.append(error)

    def record_replay() -> None:
        try:
            results.append(
                record_attribution(
                    second_repository,
                    received.source_event_id,
                    recorded_at=replay_time,
                )
            )
        except BaseException as error:
            failures.append(error)

    first_thread = threading.Thread(target=record_first, name="first-server-attribution")
    second_thread = threading.Thread(target=record_replay, name="second-server-attribution")
    first_thread.start()
    assert first_callback_entered.wait(_THREAD_TIMEOUT), "first callback did not enter"
    second_thread.start()
    assert second_runner_called.wait(_THREAD_TIMEOUT), "second caller did not reach the runner"
    assert not second_callback_entered.is_set()

    release_first_callback.set()
    first_thread.join(_THREAD_TIMEOUT)
    second_thread.join(_THREAD_TIMEOUT)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert failures == []
    assert len(results) == 2
    assert results[0] == results[1]
    assert second_callback_entered.is_set()
    assert callback_count == 2
    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM discord_source_event_server_attributions"
        ).fetchall()
    assert len(rows) == 1
    assert tuple(rows[0]) == (
        received.source_event_id,
        "resolved",
        "Server A",
        first_time.isoformat(),
        first_time.isoformat(),
    )


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


def begin_scan(
    database_path,
    *,
    server: str = "Server",
    account: str = "Account",
    scan_kind: str = "antidisable",
) -> int:
    catalog = CatalogRepository(database_path)
    if scan_kind == "antidisable":
        return catalog.begin_antidisable_scan(server, account).id
    return catalog.begin_harem_scan(server, account, scan_kind).id


def complete_antidisable_scan(database_path, scan_id: int) -> None:
    catalog = CatalogRepository(database_path)
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE harem_scans SET expected_page_count = 0 WHERE id = ?",
            (scan_id,),
        )
    catalog.complete_antidisable_scan(scan_id)


def seed_message(
    repository: DiscordMessageRepository,
    *,
    guild_id: str = "guild-1",
    channel_id: str = "channel-1",
    message_id: str = "message-1",
    payload_hash: str = "hash-1",
    marker: str | None = "revision-1",
) -> MessageAggregateKey:
    message = aggregate(
        guild_id=guild_id,
        channel_id=channel_id,
        message_id=message_id,
    )
    receive(
        repository,
        message=message,
        message_revision=revision(message, payload_hash=payload_hash, marker=marker),
        event_key=f"event-{guild_id}-{channel_id}-{message_id}-{payload_hash}",
    )
    return message


def create_workflow(
    repository: DiscordMessageRepository,
    database_path,
    *,
    scan_id: int | None = None,
    request_message: MessageAggregateKey | None = None,
    requesting_user_id: str = "user-1",
    created_at: datetime = datetime(2026, 7, 18, 1, 0, tzinfo=timezone.utc),
    expires_at: datetime = datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
) -> AntidisableWorkflowMutationResult:
    if scan_id is None:
        scan_id = begin_scan(database_path)
    if request_message is None:
        request_message = seed_message(repository)
    return repository.create_antidisable_workflow(
        scan_id=scan_id,
        request_message_aggregate_key=request_message,
        requesting_user_id=requesting_user_id,
        created_at=created_at,
        expires_at=expires_at,
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


def test_public_account_attribution_uses_one_runner_callback(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    original_runner = discord_message_repository_module.run_write_transaction
    callback_states: list[bool] = []

    def observed_runner(database_path, callback):
        def observed_callback(connection):
            callback_states.append(connection.in_transaction)
            return callback(connection)

        return original_runner(database_path, observed_callback)

    def unexpected_connection():
        raise AssertionError("account attribution opened an independent repository connection")

    monkeypatch.setattr(
        discord_message_repository_module, "run_write_transaction", observed_runner
    )
    monkeypatch.setattr(repository, "_connection", unexpected_connection)

    result = record_account_attribution(repository, received.source_event_id)

    assert result.status == "resolved"
    assert result.server_name == "Server A"
    assert result.account_name == "Account A"
    assert callback_states == [True]


def test_account_attribution_failure_rolls_back_and_same_database_recovers(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    received = receive(repository)
    recorded_at = datetime(2026, 7, 18, 3, 2, tzinfo=timezone.utc)
    with connect(database_path) as connection:
        source_before = tuple(
            connection.execute(
                """
                SELECT id, event_key, revision_id, delivery_count, status,
                       legacy_import_event_id
                FROM discord_source_events
                WHERE id = ?
                """,
                (received.source_event_id,),
            ).fetchone()
        )
        connection.execute(
            """
            CREATE TRIGGER fail_account_attribution_insert
            BEFORE INSERT ON discord_source_event_account_attributions
            BEGIN
                SELECT RAISE(ABORT, 'forced account attribution failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced account attribution failure"):
        record_account_attribution(
            repository,
            received.source_event_id,
            recorded_at=recorded_at,
        )

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_source_event_account_attributions"
        ).fetchone()[0] == 0
        source_after_failure = tuple(
            connection.execute(
                """
                SELECT id, event_key, revision_id, delivery_count, status,
                       legacy_import_event_id
                FROM discord_source_events
                WHERE id = ?
                """,
                (received.source_event_id,),
            ).fetchone()
        )
        connection.execute("DROP TRIGGER fail_account_attribution_insert")
    assert source_after_failure == source_before

    recovered = record_account_attribution(
        repository,
        received.source_event_id,
        recorded_at=recorded_at,
    )

    assert recovered.status == "resolved"
    assert recovered.server_name == "Server A"
    assert recovered.account_name == "Account A"
    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM discord_source_event_account_attributions"
        ).fetchall()
        source_after_recovery = tuple(
            connection.execute(
                """
                SELECT id, event_key, revision_id, delivery_count, status,
                       legacy_import_event_id
                FROM discord_source_events
                WHERE id = ?
                """,
                (received.source_event_id,),
            ).fetchone()
        )
    assert len(rows) == 1
    assert dict(rows[0]) == {
        "source_event_id": received.source_event_id,
        "status": "resolved",
        "server_name": "Server A",
        "account_name": "Account A",
        "created_at": recorded_at.isoformat(),
        "updated_at": recorded_at.isoformat(),
    }
    assert source_after_recovery == source_before


def test_account_attribution_serializes_competing_exact_replay(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "messages.db"
    first_repository = DiscordMessageRepository(database_path)
    second_repository = DiscordMessageRepository(database_path)
    received = receive(first_repository)
    original_runner = discord_message_repository_module.run_write_transaction
    first_callback_entered = threading.Event()
    release_first_callback = threading.Event()
    second_runner_called = threading.Event()
    second_callback_entered = threading.Event()
    callback_count = 0
    results: list[DiscordSourceEventAccountAttribution] = []
    failures: list[BaseException] = []

    def observed_runner(database_path, callback):
        if threading.current_thread().name == "second-account-attribution":
            second_runner_called.set()

        def observed_callback(connection):
            nonlocal callback_count
            callback_count += 1
            if callback_count == 1:
                first_callback_entered.set()
                assert release_first_callback.wait(
                    _THREAD_TIMEOUT
                ), "first account-attribution callback was not released"
            else:
                second_callback_entered.set()
            return callback(connection)

        return original_runner(database_path, observed_callback)

    monkeypatch.setattr(
        discord_message_repository_module, "run_write_transaction", observed_runner
    )
    first_time = datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc)
    replay_time = datetime(2026, 7, 18, 4, 0, tzinfo=timezone.utc)

    def record_first() -> None:
        try:
            results.append(
                record_account_attribution(
                    first_repository,
                    received.source_event_id,
                    recorded_at=first_time,
                )
            )
        except BaseException as error:
            failures.append(error)

    def record_replay() -> None:
        try:
            results.append(
                record_account_attribution(
                    second_repository,
                    received.source_event_id,
                    recorded_at=replay_time,
                )
            )
        except BaseException as error:
            failures.append(error)

    first_thread = threading.Thread(target=record_first, name="first-account-attribution")
    second_thread = threading.Thread(target=record_replay, name="second-account-attribution")
    first_thread.start()
    assert first_callback_entered.wait(_THREAD_TIMEOUT), "first callback did not enter"
    second_thread.start()
    assert second_runner_called.wait(_THREAD_TIMEOUT), "second caller did not reach the runner"
    assert not second_callback_entered.is_set()

    release_first_callback.set()
    first_thread.join(_THREAD_TIMEOUT)
    second_thread.join(_THREAD_TIMEOUT)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert failures == []
    assert len(results) == 2
    assert results[0] == results[1]
    assert second_callback_entered.is_set()
    assert callback_count == 2
    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM discord_source_event_account_attributions"
        ).fetchall()
    assert len(rows) == 1
    assert tuple(rows[0]) == (
        received.source_event_id,
        "resolved",
        "Server A",
        "Account A",
        first_time.isoformat(),
        first_time.isoformat(),
    )


def test_account_attribution_serializes_competing_resolved_conflict(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "messages.db"
    first_repository = DiscordMessageRepository(database_path)
    second_repository = DiscordMessageRepository(database_path)
    received = receive(first_repository)
    original_runner = discord_message_repository_module.run_write_transaction
    first_callback_entered = threading.Event()
    release_first_callback = threading.Event()
    second_runner_called = threading.Event()
    second_callback_entered = threading.Event()
    callback_count = 0
    first_results: list[DiscordSourceEventAccountAttribution] = []
    second_failures: list[BaseException] = []

    def observed_runner(database_path, callback):
        if threading.current_thread().name == "conflicting-account-attribution":
            second_runner_called.set()

        def observed_callback(connection):
            nonlocal callback_count
            callback_count += 1
            if callback_count == 1:
                first_callback_entered.set()
                assert release_first_callback.wait(
                    _THREAD_TIMEOUT
                ), "first account-attribution callback was not released"
            else:
                second_callback_entered.set()
            return callback(connection)

        return original_runner(database_path, observed_callback)

    monkeypatch.setattr(
        discord_message_repository_module, "run_write_transaction", observed_runner
    )

    def record_first() -> None:
        first_results.append(
            record_account_attribution(first_repository, received.source_event_id)
        )

    def record_conflict() -> None:
        try:
            record_account_attribution(
                second_repository,
                received.source_event_id,
                server_name="Server B",
                account_name="Account B",
            )
        except BaseException as error:
            second_failures.append(error)

    first_thread = threading.Thread(target=record_first, name="first-account-attribution")
    second_thread = threading.Thread(
        target=record_conflict, name="conflicting-account-attribution"
    )
    first_thread.start()
    assert first_callback_entered.wait(_THREAD_TIMEOUT), "first callback did not enter"
    second_thread.start()
    assert second_runner_called.wait(_THREAD_TIMEOUT), "second caller did not reach the runner"
    assert not second_callback_entered.is_set()

    release_first_callback.set()
    first_thread.join(_THREAD_TIMEOUT)
    second_thread.join(_THREAD_TIMEOUT)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert len(first_results) == 1
    assert len(second_failures) == 1
    assert isinstance(second_failures[0], DiscordSourceEventAccountAttributionConflictError)
    assert second_callback_entered.is_set()
    assert callback_count == 2
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT status, server_name, account_name "
            "FROM discord_source_event_account_attributions"
        ).fetchone()
    assert tuple(row) == ("resolved", "Server A", "Account A")


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


@pytest.mark.parametrize(
    "result_type",
    [
        AntidisableWorkflow,
        AntidisableWorkflowMutationResult,
        AntidisableResponseBinding,
        AntidisableResponseBindingMutationResult,
    ],
)
def test_antidisable_workflow_value_objects_are_frozen_and_slotted(result_type) -> None:
    assert is_dataclass(result_type)
    assert result_type.__dataclass_params__.frozen is True
    assert hasattr(result_type, "__slots__")
    assert all(field.name for field in fields(result_type))


def test_antidisable_workflow_creation_replay_and_reconstruction_are_idempotent(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    scan_id = begin_scan(database_path)
    request_message = seed_message(repository)

    first = create_workflow(
        repository,
        database_path,
        scan_id=scan_id,
        request_message=request_message,
    )
    assert isinstance(first, AntidisableWorkflowMutationResult)
    assert isinstance(first.workflow, AntidisableWorkflow)
    assert first.created is True
    assert first.replayed is False
    assert first.workflow.harem_scan_id == scan_id
    assert first.workflow.request_message_aggregate_key == request_message

    restarted_repository = DiscordMessageRepository(database_path)
    replay = create_workflow(
        restarted_repository,
        database_path,
        scan_id=scan_id,
        request_message=request_message,
    )
    assert replay.created is False
    assert replay.replayed is True
    assert replay.workflow == first.workflow
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_workflows"
        ).fetchone()[0] == 1


def test_antidisable_workflow_creation_conflicts_do_not_overwrite_identity(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    first_scan_id = begin_scan(database_path)
    second_scan_id = begin_scan(database_path, account="Account 2")
    first_request = seed_message(repository, message_id="request-1")
    second_request = seed_message(repository, message_id="request-2", payload_hash="hash-2")
    create_workflow(
        repository,
        database_path,
        scan_id=first_scan_id,
        request_message=first_request,
    )

    with pytest.raises(
        DiscordAntidisableWorkflowConflictError,
        match="different request aggregate",
    ):
        create_workflow(
            repository,
            database_path,
            scan_id=first_scan_id,
            request_message=second_request,
        )
    with pytest.raises(
        DiscordAntidisableWorkflowConflictError,
        match="already attached to another antidisable scan",
    ):
        create_workflow(
            repository,
            database_path,
            scan_id=second_scan_id,
            request_message=first_request,
        )
    with pytest.raises(
        DiscordAntidisableWorkflowConflictError,
        match="conflicting immutable data",
    ):
        create_workflow(
            repository,
            database_path,
            scan_id=first_scan_id,
            request_message=first_request,
            requesting_user_id="different-user",
        )
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_workflows"
        ).fetchone()[0] == 1
        assert tuple(
            connection.execute(
                "SELECT harem_scan_id, request_message_aggregate_id "
                "FROM discord_antidisable_workflows"
            ).fetchone()
        ) == (first_scan_id, 1)


def test_antidisable_workflow_creation_rejects_invalid_lifecycle_identity_and_times(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    request_message = seed_message(repository)

    with pytest.raises(DiscordAntidisableWorkflowNotFoundError, match="scan 999 was not found"):
        create_workflow(
            repository,
            database_path,
            scan_id=999,
            request_message=request_message,
        )

    wrong_kind_scan_id = begin_scan(database_path, scan_kind="keys")
    with pytest.raises(ValueError, match="not an antidisable scan"):
        create_workflow(
            repository,
            database_path,
            scan_id=wrong_kind_scan_id,
            request_message=request_message,
        )

    completed_scan_id = begin_scan(database_path, account="Completed")
    complete_antidisable_scan(database_path, completed_scan_id)
    with pytest.raises(ValueError, match="already completed"):
        create_workflow(
            repository,
            database_path,
            scan_id=completed_scan_id,
            request_message=request_message,
        )

    missing_request_scan_id = begin_scan(database_path, account="Missing Request")
    with pytest.raises(DiscordAntidisableWorkflowNotFoundError, match="Request message aggregate"):
        create_workflow(
            repository,
            database_path,
            scan_id=missing_request_scan_id,
            request_message=aggregate(message_id="missing-request"),
        )

    valid_scan_id = begin_scan(database_path, account="Validation")
    with pytest.raises(ValueError, match="requesting_user_id must not be blank"):
        create_workflow(
            repository,
            database_path,
            scan_id=valid_scan_id,
            request_message=request_message,
            requesting_user_id=" ",
        )
    with pytest.raises(ValueError, match="created_at must be timezone-aware"):
        create_workflow(
            repository,
            database_path,
            scan_id=valid_scan_id,
            request_message=request_message,
            created_at=datetime(2026, 7, 18, 1, 0),
        )
    with pytest.raises(ValueError, match="expires_at must be strictly after created_at"):
        create_workflow(
            repository,
            database_path,
            scan_id=valid_scan_id,
            request_message=request_message,
            expires_at=datetime(2026, 7, 18, 1, 0, tzinfo=timezone.utc),
        )
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_workflows"
        ).fetchone()[0] == 0


def test_antidisable_workflow_request_lookup_uses_stable_aggregate_identity(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    scan_id = begin_scan(database_path)
    request_message = seed_message(repository)
    created = create_workflow(
        repository,
        database_path,
        scan_id=scan_id,
        request_message=request_message,
    )

    assert repository.get_antidisable_workflow_by_request_message(request_message) == created.workflow
    assert repository.get_antidisable_workflow_by_request_message(
        aggregate(message_id="missing")
    ) is None
    restarted_repository = DiscordMessageRepository(database_path)
    assert (
        restarted_repository.get_antidisable_workflow_by_request_message(request_message)
        == created.workflow
    )


def test_active_antidisable_workflows_return_all_compatible_candidates_in_deterministic_order(
    tmp_path,
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    lookup_time = datetime(2026, 7, 18, 6, 0, tzinfo=timezone.utc)
    assert repository.active_antidisable_workflows_for_channel(
        "guild-1", "channel-1", lookup_time
    ) == ()

    first = create_workflow(
        repository,
        database_path,
        scan_id=begin_scan(database_path, account="First"),
        request_message=seed_message(repository, message_id="request-first"),
        requesting_user_id="user-1",
        created_at=datetime(2026, 7, 18, 1, 0, tzinfo=timezone.utc),
        expires_at=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
    )
    second = create_workflow(
        repository,
        database_path,
        scan_id=begin_scan(database_path, account="Second"),
        request_message=seed_message(repository, message_id="request-second", payload_hash="hash-2"),
        requesting_user_id="user-2",
        created_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
        expires_at=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
    )
    third = create_workflow(
        repository,
        database_path,
        scan_id=begin_scan(database_path, account="Third"),
        request_message=seed_message(repository, message_id="request-third", payload_hash="hash-3"),
        requesting_user_id="user-1",
        created_at=datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc),
        expires_at=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
    )
    other_channel = create_workflow(
        repository,
        database_path,
        scan_id=begin_scan(database_path, account="Other Channel"),
        request_message=seed_message(
            repository, channel_id="channel-2", message_id="request-other-channel", payload_hash="hash-4"
        ),
        created_at=datetime(2026, 7, 18, 1, 30, tzinfo=timezone.utc),
        expires_at=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
    )
    other_guild = create_workflow(
        repository,
        database_path,
        scan_id=begin_scan(database_path, account="Other Guild"),
        request_message=seed_message(
            repository, guild_id="guild-2", message_id="request-other-guild", payload_hash="hash-5"
        ),
        created_at=datetime(2026, 7, 18, 1, 45, tzinfo=timezone.utc),
        expires_at=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
    )
    create_workflow(
        repository,
        database_path,
        scan_id=begin_scan(database_path, account="Expired"),
        request_message=seed_message(repository, message_id="request-expired", payload_hash="hash-6"),
        created_at=datetime(2026, 7, 18, 4, 0, tzinfo=timezone.utc),
        expires_at=datetime(2026, 7, 18, 6, 0, tzinfo=timezone.utc),
    )
    completed = create_workflow(
        repository,
        database_path,
        scan_id=begin_scan(database_path, account="Completed"),
        request_message=seed_message(repository, message_id="request-completed", payload_hash="hash-7"),
        created_at=datetime(2026, 7, 18, 4, 30, tzinfo=timezone.utc),
        expires_at=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
    )
    complete_antidisable_scan(database_path, completed.workflow.harem_scan_id)

    candidates = repository.active_antidisable_workflows_for_channel(
        "guild-1", "channel-1", lookup_time
    )
    assert tuple(candidate.harem_scan_id for candidate in candidates) == (
        first.workflow.harem_scan_id,
        second.workflow.harem_scan_id,
        third.workflow.harem_scan_id,
    )
    assert tuple(candidate.requesting_user_id for candidate in candidates) == (
        "user-1",
        "user-2",
        "user-1",
    )
    assert repository.active_antidisable_workflows_for_channel(
        "guild-1", "channel-2", lookup_time
    ) == (other_channel.workflow,)
    assert repository.active_antidisable_workflows_for_channel(
        "guild-2", "channel-1", lookup_time
    ) == (other_guild.workflow,)
    assert repository.active_antidisable_workflows_for_channel(
        "guild-3", "channel-3", lookup_time
    ) == ()


def test_atomic_antidisable_response_resolution_preserves_zero_one_and_multiple_candidates(
    tmp_path,
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    bound_at = datetime(2026, 7, 18, 1, 30, tzinfo=timezone.utc)

    with pytest.raises(
        DiscordAntidisableWorkflowNotFoundError,
        match="Response message aggregate was not found",
    ):
        repository.resolve_antidisable_response(
            response_message_aggregate_key=aggregate(message_id="missing-response"),
            bound_at=bound_at,
        )

    unresolved_response = seed_message(
        repository, message_id="response-unresolved", payload_hash="response-unresolved"
    )

    unresolved = repository.resolve_antidisable_response(
        response_message_aggregate_key=unresolved_response,
        bound_at=bound_at,
    )

    assert isinstance(unresolved, AntidisableResponseResolutionResult)
    assert unresolved.status == "unresolved"
    assert unresolved.workflow is None
    assert unresolved.binding_result is None

    first = create_workflow(
        repository,
        database_path,
        scan_id=begin_scan(database_path, account="First"),
        request_message=seed_message(repository, message_id="request-first"),
        expires_at=datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc),
    )
    bound_response = seed_message(
        repository, message_id="response-bound", payload_hash="response-bound"
    )
    bound = repository.resolve_antidisable_response(
        response_message_aggregate_key=bound_response,
        bound_at=bound_at,
    )

    assert bound.status == "bound"
    assert bound.workflow == first.workflow
    assert bound.binding_result is not None
    assert bound.binding_result.created is True

    create_workflow(
        repository,
        database_path,
        scan_id=begin_scan(database_path, account="Second"),
        request_message=seed_message(
            repository, message_id="request-second", payload_hash="request-second"
        ),
        requesting_user_id="user-2",
        created_at=datetime(2026, 7, 18, 1, 10, tzinfo=timezone.utc),
        expires_at=datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc),
    )
    ambiguous_response = seed_message(
        repository, message_id="response-ambiguous", payload_hash="response-ambiguous"
    )
    ambiguous = repository.resolve_antidisable_response(
        response_message_aggregate_key=ambiguous_response,
        bound_at=bound_at,
    )

    assert ambiguous.status == "ambiguous"
    assert ambiguous.workflow is None
    assert ambiguous.binding_result is None
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_response_bindings"
        ).fetchone()[0] == 1


def test_public_antidisable_binding_and_resolution_use_runner_callbacks(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    workflow = create_workflow(
        repository,
        database_path,
        request_message=seed_message(repository, message_id="request"),
        expires_at=datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc),
    )
    explicit_response = seed_message(
        repository, message_id="response-explicit", payload_hash="explicit"
    )
    resolved_response = seed_message(
        repository, message_id="response-resolved", payload_hash="resolved"
    )
    original_runner = discord_message_repository_module.run_write_transaction
    callback_states: list[bool] = []

    def observed_runner(database_path, callback):
        def observed_callback(connection):
            callback_states.append(connection.in_transaction)
            return callback(connection)

        return original_runner(database_path, observed_callback)

    def unexpected_connection():
        raise AssertionError("antidisable binding opened an independent repository connection")

    monkeypatch.setattr(
        discord_message_repository_module, "run_write_transaction", observed_runner
    )
    monkeypatch.setattr(repository, "_connection", unexpected_connection)

    explicit = repository.bind_antidisable_response(
        scan_id=workflow.workflow.harem_scan_id,
        response_message_aggregate_key=explicit_response,
        bound_at=datetime(2026, 7, 18, 1, 30, tzinfo=timezone.utc),
    )
    resolved = repository.resolve_antidisable_response(
        response_message_aggregate_key=resolved_response,
        bound_at=datetime(2026, 7, 18, 1, 31, tzinfo=timezone.utc),
    )

    assert explicit.created is True
    assert resolved.status == "bound"
    assert resolved.binding_result is not None
    assert resolved.binding_result.created is True
    assert callback_states == [True, True]


def test_atomic_antidisable_resolution_existing_binding_remains_authoritative(
    tmp_path,
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    first = create_workflow(
        repository,
        database_path,
        scan_id=begin_scan(database_path, account="First"),
        request_message=seed_message(repository, message_id="request-first"),
        expires_at=datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc),
    )
    response = seed_message(repository, message_id="response", payload_hash="response")
    original_bound_at = datetime(2026, 7, 18, 1, 30, tzinfo=timezone.utc)
    initial = repository.resolve_antidisable_response(
        response_message_aggregate_key=response,
        bound_at=original_bound_at,
    )
    create_workflow(
        repository,
        database_path,
        scan_id=begin_scan(database_path, account="Second"),
        request_message=seed_message(
            repository, message_id="request-second", payload_hash="request-second"
        ),
        requesting_user_id="user-2",
        created_at=datetime(2026, 7, 18, 1, 10, tzinfo=timezone.utc),
        expires_at=datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc),
    )

    replay = DiscordMessageRepository(database_path).resolve_antidisable_response(
        response_message_aggregate_key=response,
        bound_at=datetime(2026, 7, 18, 1, 45, tzinfo=timezone.utc),
    )

    assert initial.workflow == first.workflow
    assert initial.binding_result is not None
    assert initial.binding_result.created is True
    assert replay.status == "bound"
    assert replay.workflow == first.workflow
    assert replay.binding_result is not None
    assert replay.binding_result.created is False
    assert replay.binding_result.binding.bound_at == original_bound_at
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_response_bindings"
        ).fetchone()[0] == 1


def test_atomic_antidisable_resolution_create_first_observes_ambiguity(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    catalog = CatalogRepository(database_path)
    create_workflow(
        repository,
        database_path,
        scan_id=begin_scan(database_path, account="First"),
        request_message=seed_message(repository, message_id="request-first"),
        expires_at=datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc),
    )
    second_request = seed_message(
        repository, message_id="request-second", payload_hash="request-second"
    )
    response = seed_message(repository, message_id="response", payload_hash="response")
    original_runner = discord_message_repository_module.run_write_transaction
    workflow_committed = threading.Event()
    resolver_called = threading.Event()
    resolver_callback_entered = threading.Event()
    results: list[AntidisableResponseResolutionResult] = []
    failures: list[BaseException] = []

    def observed_runner(database_path, callback):
        resolver_called.set()
        assert workflow_committed.wait(_THREAD_TIMEOUT), "workflow creation did not commit"

        def observed_callback(connection):
            resolver_callback_entered.set()
            return callback(connection)

        return original_runner(database_path, observed_callback)

    monkeypatch.setattr(
        discord_message_repository_module, "run_write_transaction", observed_runner
    )

    def resolve_response() -> None:
        try:
            results.append(
                repository.resolve_antidisable_response(
                    response_message_aggregate_key=response,
                    bound_at=datetime(2026, 7, 18, 1, 30, tzinfo=timezone.utc),
                )
            )
        except BaseException as error:
            failures.append(error)

    def create_second_workflow() -> None:
        try:
            def create_with_connection(connection):
                scan_id = catalog._begin_antidisable_scan_with_connection(
                    connection,
                    server="Server",
                    account="Second",
                    observed_at=datetime(2026, 7, 18, 1, 10, tzinfo=timezone.utc),
                )
                return repository._create_antidisable_workflow_with_connection(
                    connection,
                    scan_id=scan_id,
                    request_message_aggregate_key=second_request,
                    requesting_user_id="user-2",
                    created_at=datetime(2026, 7, 18, 1, 10, tzinfo=timezone.utc),
                    expires_at=datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc),
                )

            run_write_transaction(database_path, create_with_connection)
            workflow_committed.set()
        except BaseException as error:
            failures.append(error)
            workflow_committed.set()

    resolver_thread = threading.Thread(target=resolve_response, name="response-resolver")
    creator_thread = threading.Thread(target=create_second_workflow, name="workflow-creator")
    resolver_thread.start()
    assert resolver_called.wait(_THREAD_TIMEOUT), "resolver did not reach the runner"
    assert not resolver_callback_entered.is_set()
    creator_thread.start()
    creator_thread.join(_THREAD_TIMEOUT)
    resolver_thread.join(_THREAD_TIMEOUT)

    assert not creator_thread.is_alive(), "workflow creator did not terminate"
    assert not resolver_thread.is_alive(), "resolver did not terminate"
    assert failures == []
    assert resolver_callback_entered.is_set()
    assert len(results) == 1
    assert results[0].status == "ambiguous"
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_workflows"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_response_bindings"
        ).fetchone()[0] == 0


def test_atomic_antidisable_resolution_bind_first_precedes_workflow_creation(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    catalog = CatalogRepository(database_path)
    first = create_workflow(
        repository,
        database_path,
        scan_id=begin_scan(database_path, account="First"),
        request_message=seed_message(repository, message_id="request-first"),
        expires_at=datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc),
    )
    second_request = seed_message(
        repository, message_id="request-second", payload_hash="request-second"
    )
    response = seed_message(repository, message_id="response", payload_hash="response")
    original_runner = discord_message_repository_module.run_write_transaction
    binding_created = threading.Event()
    release_resolver = threading.Event()
    creator_called = threading.Event()
    creator_callback_entered = threading.Event()
    results: list[AntidisableResponseResolutionResult] = []
    failures: list[BaseException] = []

    def observed_runner(database_path, callback):
        def observed_callback(connection):
            result = callback(connection)
            binding_created.set()
            assert release_resolver.wait(_THREAD_TIMEOUT), "resolver was not released"
            return result

        return original_runner(database_path, observed_callback)

    monkeypatch.setattr(
        discord_message_repository_module, "run_write_transaction", observed_runner
    )

    def resolve_response() -> None:
        try:
            results.append(
                repository.resolve_antidisable_response(
                    response_message_aggregate_key=response,
                    bound_at=datetime(2026, 7, 18, 1, 30, tzinfo=timezone.utc),
                )
            )
        except BaseException as error:
            failures.append(error)

    def create_second_workflow() -> None:
        creator_called.set()
        try:
            def create_with_connection(connection):
                creator_callback_entered.set()
                scan_id = catalog._begin_antidisable_scan_with_connection(
                    connection,
                    server="Server",
                    account="Second",
                    observed_at=datetime(2026, 7, 18, 1, 10, tzinfo=timezone.utc),
                )
                return repository._create_antidisable_workflow_with_connection(
                    connection,
                    scan_id=scan_id,
                    request_message_aggregate_key=second_request,
                    requesting_user_id="user-2",
                    created_at=datetime(2026, 7, 18, 1, 10, tzinfo=timezone.utc),
                    expires_at=datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc),
                )

            run_write_transaction(database_path, create_with_connection)
        except BaseException as error:
            failures.append(error)

    resolver_thread = threading.Thread(target=resolve_response, name="response-resolver")
    creator_thread = threading.Thread(target=create_second_workflow, name="workflow-creator")
    resolver_thread.start()
    assert binding_created.wait(_THREAD_TIMEOUT), "resolver did not create the binding"
    creator_thread.start()
    assert creator_called.wait(_THREAD_TIMEOUT), "creator did not reach the runner"
    assert not creator_callback_entered.is_set()

    release_resolver.set()
    resolver_thread.join(_THREAD_TIMEOUT)
    creator_thread.join(_THREAD_TIMEOUT)

    assert not resolver_thread.is_alive(), "resolver did not terminate"
    assert not creator_thread.is_alive(), "workflow creator did not terminate"
    assert failures == []
    assert creator_callback_entered.is_set()
    assert len(results) == 1
    assert results[0].workflow == first.workflow
    assert results[0].binding_result is not None
    assert results[0].binding_result.created is True
    replay = repository.resolve_antidisable_response(
        response_message_aggregate_key=response,
        bound_at=datetime(2026, 7, 18, 1, 45, tzinfo=timezone.utc),
    )
    assert replay.workflow == first.workflow
    assert replay.binding_result is not None
    assert replay.binding_result.created is False
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_workflows"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_response_bindings"
        ).fetchone()[0] == 1


def test_atomic_antidisable_resolution_write_failure_rolls_back_and_recovers(
    tmp_path,
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    workflow = create_workflow(
        repository,
        database_path,
        request_message=seed_message(repository, message_id="request"),
        expires_at=datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc),
    )
    response = seed_message(repository, message_id="response", payload_hash="response")
    with connect(database_path) as connection:
        response_id = connection.execute(
            "SELECT id FROM discord_message_aggregates WHERE message_id = 'response'"
        ).fetchone()[0]
        connection.execute(
            """
            CREATE TRIGGER fail_antidisable_binding_insert
            BEFORE INSERT ON discord_antidisable_response_bindings
            BEGIN
                SELECT RAISE(ABORT, 'forced antidisable binding failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced antidisable binding failure"):
        repository.resolve_antidisable_response(
            response_message_aggregate_key=response,
            bound_at=datetime(2026, 7, 18, 1, 30, tzinfo=timezone.utc),
        )

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_response_bindings"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_workflows WHERE harem_scan_id = ?",
            (workflow.workflow.harem_scan_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_message_aggregates WHERE id = ?",
            (response_id,),
        ).fetchone()[0] == 1
        connection.execute("DROP TRIGGER fail_antidisable_binding_insert")

    recovered = repository.resolve_antidisable_response(
        response_message_aggregate_key=response,
        bound_at=datetime(2026, 7, 18, 1, 30, tzinfo=timezone.utc),
    )

    assert recovered.status == "bound"
    assert recovered.workflow == workflow.workflow
    assert recovered.binding_result is not None
    assert recovered.binding_result.created is True
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_response_bindings"
        ).fetchone()[0] == 1


def test_antidisable_response_binding_supports_multiple_responses_replay_and_lookup(
    tmp_path,
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    scan_id = begin_scan(database_path)
    request_message = seed_message(repository, message_id="request")
    workflow = create_workflow(
        repository,
        database_path,
        scan_id=scan_id,
        request_message=request_message,
        expires_at=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
    )
    response_one = seed_message(repository, message_id="response-1", payload_hash="response-hash-1")
    response_two = seed_message(repository, message_id="response-2", payload_hash="response-hash-2")
    assert repository.get_antidisable_workflow_by_response_message(response_one) is None

    first = repository.bind_antidisable_response(
        scan_id=scan_id,
        response_message_aggregate_key=response_one,
        bound_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
    )
    second = repository.bind_antidisable_response(
        scan_id=scan_id,
        response_message_aggregate_key=response_two,
        bound_at=datetime(2026, 7, 18, 2, 1, tzinfo=timezone.utc),
    )
    replay = DiscordMessageRepository(database_path).bind_antidisable_response(
        scan_id=scan_id,
        response_message_aggregate_key=response_one,
        bound_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
    )
    assert isinstance(first.binding, AntidisableResponseBinding)
    assert first.created is True
    assert second.created is True
    assert replay.created is False
    assert replay.replayed is True
    assert replay.binding == first.binding
    with pytest.raises(
        DiscordAntidisableWorkflowConflictError,
        match="conflicting immutable data",
    ):
        repository.bind_antidisable_response(
            scan_id=scan_id,
            response_message_aggregate_key=response_one,
            bound_at=datetime(2026, 7, 18, 2, 5, tzinfo=timezone.utc),
        )
    assert repository.get_antidisable_workflow_by_response_message(response_one) == workflow.workflow
    assert repository.get_antidisable_workflow_by_response_message(response_two) == workflow.workflow
    assert repository.get_antidisable_workflow_by_response_message(
        aggregate(message_id="unbound")
    ) is None
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_response_bindings"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT completed_at FROM harem_scans WHERE id = ?", (scan_id,)
        ).fetchone()[0] is None


def test_antidisable_response_binding_revisions_remain_one_aggregate_binding(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    scan_id = begin_scan(database_path)
    seed_message(repository, message_id="request")
    request_message = aggregate(message_id="request")
    create_workflow(
        repository,
        database_path,
        scan_id=scan_id,
        request_message=request_message,
        expires_at=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
    )
    response_message = seed_message(repository, message_id="response", payload_hash="response-hash-1")
    repository.bind_antidisable_response(
        scan_id=scan_id,
        response_message_aggregate_key=response_message,
        bound_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
    )
    receive(
        repository,
        message=response_message,
        message_revision=revision(response_message, payload_hash="response-hash-2", marker="revision-2"),
        event_key="event-response-revision-2",
    )

    restarted_repository = DiscordMessageRepository(database_path)
    assert restarted_repository.get_antidisable_workflow_by_response_message(response_message) is not None
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_message_revisions WHERE aggregate_id = "
            "(SELECT id FROM discord_message_aggregates WHERE message_id = 'response')"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_response_bindings"
        ).fetchone()[0] == 1


def test_antidisable_response_binding_rejects_duplicate_response_ownership(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    first_scan_id = begin_scan(database_path, account="First")
    second_scan_id = begin_scan(database_path, account="Second")
    first_request = seed_message(repository, message_id="request-1")
    second_request = seed_message(repository, message_id="request-2", payload_hash="hash-2")
    response_message = seed_message(repository, message_id="response", payload_hash="response-hash")
    create_workflow(repository, database_path, scan_id=first_scan_id, request_message=first_request)
    create_workflow(repository, database_path, scan_id=second_scan_id, request_message=second_request)
    repository.bind_antidisable_response(
        scan_id=first_scan_id,
        response_message_aggregate_key=response_message,
        bound_at=datetime(2026, 7, 18, 1, 30, tzinfo=timezone.utc),
    )

    with pytest.raises(
        DiscordAntidisableWorkflowConflictError,
        match=f"already bound to workflow {first_scan_id}",
    ):
        repository.bind_antidisable_response(
            scan_id=second_scan_id,
            response_message_aggregate_key=response_message,
            bound_at=datetime(2026, 7, 18, 1, 31, tzinfo=timezone.utc),
        )
    with connect(database_path) as connection:
        assert tuple(
            connection.execute(
                "SELECT harem_scan_id, COUNT(*) FROM discord_antidisable_response_bindings "
                "GROUP BY response_message_aggregate_id"
            ).fetchone()
        ) == (first_scan_id, 1)


@pytest.mark.parametrize(
    ("guild_id", "channel_id"),
    [("guild-2", "channel-1"), ("guild-1", "channel-2")],
)
def test_antidisable_response_binding_rejects_cross_guild_or_channel(
    tmp_path, guild_id: str, channel_id: str
) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    scan_id = begin_scan(database_path)
    request_message = seed_message(repository, message_id="request")
    create_workflow(repository, database_path, scan_id=scan_id, request_message=request_message)
    response_message = seed_message(
        repository,
        guild_id=guild_id,
        channel_id=channel_id,
        message_id="response",
        payload_hash="response-hash",
    )

    with pytest.raises(
        DiscordAntidisableWorkflowConflictError,
        match="outside the workflow request scope",
    ):
        repository.bind_antidisable_response(
            scan_id=scan_id,
            response_message_aggregate_key=response_message,
            bound_at=datetime(2026, 7, 18, 1, 30, tzinfo=timezone.utc),
        )
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_response_bindings"
        ).fetchone()[0] == 0


def test_antidisable_response_binding_rejects_missing_completed_and_expired_workflows(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    response_message = seed_message(repository, message_id="response")
    with pytest.raises(DiscordAntidisableWorkflowNotFoundError, match="workflow 999 was not found"):
        repository.bind_antidisable_response(
            scan_id=999,
            response_message_aggregate_key=response_message,
            bound_at=datetime(2026, 7, 18, 1, 0, tzinfo=timezone.utc),
        )

    expired_scan_id = begin_scan(database_path, account="Expired")
    expired_request = seed_message(repository, message_id="request-expired", payload_hash="hash-expired")
    create_workflow(
        repository,
        database_path,
        scan_id=expired_scan_id,
        request_message=expired_request,
        expires_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(ValueError, match="expired at bound_at"):
        repository.bind_antidisable_response(
            scan_id=expired_scan_id,
            response_message_aggregate_key=response_message,
            bound_at=datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc),
        )

    completed_scan_id = begin_scan(database_path, account="Completed")
    completed_request = seed_message(repository, message_id="request-completed", payload_hash="hash-completed")
    create_workflow(
        repository,
        database_path,
        scan_id=completed_scan_id,
        request_message=completed_request,
        expires_at=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
    )
    complete_antidisable_scan(database_path, completed_scan_id)
    with pytest.raises(ValueError, match="already completed"):
        repository.bind_antidisable_response(
            scan_id=completed_scan_id,
            response_message_aggregate_key=response_message,
            bound_at=datetime(2026, 7, 18, 1, 0, tzinfo=timezone.utc),
        )


def test_antidisable_response_binding_rejects_missing_response_without_writes(tmp_path) -> None:
    database_path = tmp_path / "messages.db"
    repository = DiscordMessageRepository(database_path)
    scan_id = begin_scan(database_path)
    request_message = seed_message(repository, message_id="request")
    create_workflow(repository, database_path, scan_id=scan_id, request_message=request_message)

    with pytest.raises(DiscordAntidisableWorkflowNotFoundError, match="Response message aggregate"):
        repository.bind_antidisable_response(
            scan_id=scan_id,
            response_message_aggregate_key=aggregate(message_id="missing-response"),
            bound_at=datetime(2026, 7, 18, 1, 30, tzinfo=timezone.utc),
        )
    assert repository.get_antidisable_workflow_by_response_message(
        aggregate(message_id="missing-response")
    ) is None
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_response_bindings"
        ).fetchone()[0] == 0
