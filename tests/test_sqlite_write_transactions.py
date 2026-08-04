import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

import moa.database.sqlite as sqlite_module
from moa.database.sqlite import connect, run_write_transaction


_THREAD_TIMEOUT = 5.0


def _create_values_table(database_path: Path) -> None:
    with connect(database_path) as connection:
        connection.execute("CREATE TABLE values_table (value INTEGER NOT NULL)")


def _start_thread(target: Callable[[], None]) -> threading.Thread:
    thread = threading.Thread(target=target)
    thread.start()
    return thread


def _join_thread(thread: threading.Thread) -> None:
    thread.join(timeout=_THREAD_TIMEOUT)
    assert not thread.is_alive(), "test worker did not terminate"


class _ObservedLock:
    def __init__(self, lock, attempt_event: threading.Event | None) -> None:
        self._lock = lock
        self._attempt_event = attempt_event

    def __enter__(self):
        if self._attempt_event is not None:
            self._attempt_event.set()
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._lock.release()


def _observe_second_runner_lock_attempt(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    real_writer_lock = sqlite_module._writer_lock
    lookup_count = 0
    lookup_count_guard = threading.Lock()
    second_attempted = threading.Event()

    def observed_writer_lock(database_path: Path):
        nonlocal lookup_count
        lock = real_writer_lock(database_path)
        with lookup_count_guard:
            lookup_count += 1
            attempt_event = second_attempted if lookup_count == 2 else None
        return _ObservedLock(lock, attempt_event)

    monkeypatch.setattr(sqlite_module, "_writer_lock", observed_writer_lock)
    return second_attempted


def test_successful_transaction_commits_once_and_returns_callback_result(tmp_path) -> None:
    database_path = tmp_path / "success.db"
    _create_values_table(database_path)
    callback_count = 0

    def callback(connection: sqlite3.Connection) -> str:
        nonlocal callback_count
        callback_count += 1
        assert connection.in_transaction is True
        connection.execute("INSERT INTO values_table (value) VALUES (1)")
        return "committed"

    result = run_write_transaction(database_path, callback)

    assert result == "committed"
    assert callback_count == 1
    with connect(database_path) as verification_connection:
        assert [
            row[0]
            for row in verification_connection.execute(
                "SELECT value FROM values_table"
            ).fetchall()
        ] == [1]


def test_callback_failure_rolls_back_all_writes_and_cleans_up(tmp_path) -> None:
    database_path = tmp_path / "callback-failure.db"
    _create_values_table(database_path)
    failure = RuntimeError("sentinel callback failure")

    def failing_callback(connection: sqlite3.Connection) -> None:
        connection.execute("INSERT INTO values_table (value) VALUES (1)")
        connection.execute("INSERT INTO values_table (value) VALUES (2)")
        raise failure

    with pytest.raises(RuntimeError) as raised:
        run_write_transaction(database_path, failing_callback)

    assert raised.value is failure
    run_write_transaction(
        database_path,
        lambda connection: connection.execute(
            "INSERT INTO values_table (value) VALUES (3)"
        ),
    )
    with connect(database_path) as verification_connection:
        assert [
            row[0]
            for row in verification_connection.execute(
                "SELECT value FROM values_table"
            ).fetchall()
        ] == [3]


def test_commit_failure_rolls_back_and_propagates_original_exception(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "commit-failure.db"
    _create_values_table(database_path)
    real_connect = sqlite_module.connect
    failure = sqlite3.OperationalError("sentinel commit failure")

    class CommitFailingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def __getattr__(self, name: str):
            return getattr(self._connection, name)

        def commit(self) -> None:
            raise failure

    def connect_with_failing_commit(path: Path | None = None):
        return CommitFailingConnection(real_connect(path))

    monkeypatch.setattr(sqlite_module, "connect", connect_with_failing_commit)

    with pytest.raises(sqlite3.OperationalError) as raised:
        run_write_transaction(
            database_path,
            lambda connection: connection.execute(
                "INSERT INTO values_table (value) VALUES (1)"
            ),
        )

    assert raised.value is failure
    with real_connect(database_path) as verification_connection:
        assert verification_connection.execute(
            "SELECT COUNT(*) FROM values_table"
        ).fetchone()[0] == 0


def test_begin_failure_does_not_invoke_callback_or_leak_transaction(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "begin-failure.db"
    _create_values_table(database_path)
    real_connect = sqlite_module.connect
    callback_count = 0

    def connect_without_busy_wait(path: Path | None = None) -> sqlite3.Connection:
        connection = real_connect(path)
        connection.execute("PRAGMA busy_timeout = 0")
        return connection

    monkeypatch.setattr(sqlite_module, "connect", connect_without_busy_wait)
    independent_writer = real_connect(database_path)
    try:
        independent_writer.execute("BEGIN IMMEDIATE")

        def callback(connection: sqlite3.Connection) -> None:
            nonlocal callback_count
            callback_count += 1

        with pytest.raises(sqlite3.OperationalError) as raised:
            run_write_transaction(database_path, callback)

        assert raised.value.sqlite_errorcode == sqlite3.SQLITE_BUSY
        assert raised.value.sqlite_errorname == "SQLITE_BUSY"
        assert callback_count == 0
    finally:
        independent_writer.rollback()
        independent_writer.close()

    run_write_transaction(
        database_path,
        lambda connection: connection.execute(
            "INSERT INTO values_table (value) VALUES (1)"
        ),
    )


def test_non_busy_operational_error_is_not_retried(tmp_path) -> None:
    database_path = tmp_path / "non-busy.db"
    callback_count = 0

    def callback(connection: sqlite3.Connection) -> None:
        nonlocal callback_count
        callback_count += 1
        connection.execute("INSERT INTO missing_table (value) VALUES (1)")

    with pytest.raises(sqlite3.OperationalError) as raised:
        run_write_transaction(database_path, callback)

    assert "no such table" in str(raised.value)
    assert raised.value.sqlite_errorcode == sqlite3.SQLITE_ERROR
    assert callback_count == 1


def test_same_database_callbacks_are_serialized(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "same-database.db"
    _create_values_table(database_path)
    second_attempted = _observe_second_runner_lock_attempt(monkeypatch)
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    failures: list[BaseException] = []

    def first_callback(connection: sqlite3.Connection) -> None:
        first_entered.set()
        assert release_first.wait(_THREAD_TIMEOUT), "first callback was not released"
        connection.execute("INSERT INTO values_table (value) VALUES (1)")

    def run_first() -> None:
        try:
            run_write_transaction(database_path, first_callback)
        except BaseException as exc:
            failures.append(exc)

    def run_second() -> None:
        try:
            run_write_transaction(database_path, lambda connection: second_entered.set())
        except BaseException as exc:
            failures.append(exc)

    first_thread = _start_thread(run_first)
    assert first_entered.wait(_THREAD_TIMEOUT), "first callback did not enter"
    second_thread = _start_thread(run_second)
    assert second_attempted.wait(_THREAD_TIMEOUT), "second runner did not attempt the lock"
    assert not second_entered.is_set(), "same-database callbacks overlapped"
    release_first.set()
    _join_thread(first_thread)
    _join_thread(second_thread)

    assert second_entered.is_set()
    assert failures == []


def test_same_database_lock_is_held_through_commit_cleanup(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "commit-lifetime.db"
    _create_values_table(database_path)
    real_connect = sqlite_module.connect
    second_attempted = _observe_second_runner_lock_attempt(monkeypatch)
    commit_started = threading.Event()
    release_commit = threading.Event()
    second_entered = threading.Event()
    factory_calls = 0
    factory_calls_guard = threading.Lock()
    failures: list[BaseException] = []

    class CommitBlockingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def __getattr__(self, name: str):
            return getattr(self._connection, name)

        def commit(self) -> None:
            commit_started.set()
            assert release_commit.wait(_THREAD_TIMEOUT), "commit was not released"
            self._connection.commit()

    def blocking_first_connect(path: Path | None = None):
        nonlocal factory_calls
        connection = real_connect(path)
        with factory_calls_guard:
            factory_calls += 1
            if factory_calls == 1:
                return CommitBlockingConnection(connection)
        return connection

    monkeypatch.setattr(sqlite_module, "connect", blocking_first_connect)

    def run_first() -> None:
        try:
            run_write_transaction(
                database_path,
                lambda connection: connection.execute(
                    "INSERT INTO values_table (value) VALUES (1)"
                ),
            )
        except BaseException as exc:
            failures.append(exc)

    def run_second() -> None:
        try:
            run_write_transaction(database_path, lambda connection: second_entered.set())
        except BaseException as exc:
            failures.append(exc)

    first_thread = _start_thread(run_first)
    assert commit_started.wait(_THREAD_TIMEOUT), "first transaction did not reach commit"
    second_thread = _start_thread(run_second)
    assert second_attempted.wait(_THREAD_TIMEOUT), "second runner did not attempt the lock"
    assert not second_entered.is_set(), "second callback entered before commit completed"
    release_commit.set()
    _join_thread(first_thread)
    _join_thread(second_thread)

    assert second_entered.is_set()
    assert failures == []


def test_different_database_callbacks_are_independent(tmp_path) -> None:
    first_path = tmp_path / "first.db"
    second_path = tmp_path / "second.db"
    _create_values_table(first_path)
    _create_values_table(second_path)
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    failures: list[BaseException] = []

    def first_callback(connection: sqlite3.Connection) -> None:
        first_entered.set()
        assert release_first.wait(_THREAD_TIMEOUT), "first callback was not released"

    def run_first() -> None:
        try:
            run_write_transaction(first_path, first_callback)
        except BaseException as exc:
            failures.append(exc)

    def run_second() -> None:
        try:
            run_write_transaction(second_path, lambda connection: second_entered.set())
        except BaseException as exc:
            failures.append(exc)

    first_thread = _start_thread(run_first)
    assert first_entered.wait(_THREAD_TIMEOUT), "first callback did not enter"
    second_thread = _start_thread(run_second)
    assert second_entered.wait(_THREAD_TIMEOUT), "different database was globally blocked"
    release_first.set()
    _join_thread(first_thread)
    _join_thread(second_thread)

    assert failures == []


def test_relative_and_absolute_same_file_share_serialization_identity(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    absolute_path = tmp_path / "relative-absolute.db"
    _create_values_table(absolute_path)
    monkeypatch.chdir(tmp_path)
    relative_path = Path("relative-absolute.db")
    second_attempted = _observe_second_runner_lock_attempt(monkeypatch)
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    failures: list[BaseException] = []

    def first_callback(connection: sqlite3.Connection) -> None:
        first_entered.set()
        assert release_first.wait(_THREAD_TIMEOUT), "relative callback was not released"

    def run_first() -> None:
        try:
            run_write_transaction(relative_path, first_callback)
        except BaseException as exc:
            failures.append(exc)

    def run_second() -> None:
        try:
            run_write_transaction(absolute_path, lambda connection: second_entered.set())
        except BaseException as exc:
            failures.append(exc)

    first_thread = _start_thread(run_first)
    assert first_entered.wait(_THREAD_TIMEOUT), "relative callback did not enter"
    second_thread = _start_thread(run_second)
    assert second_attempted.wait(_THREAD_TIMEOUT), "absolute runner did not attempt the lock"
    assert not second_entered.is_set(), "relative and absolute callbacks overlapped"
    release_first.set()
    _join_thread(first_thread)
    _join_thread(second_thread)

    assert second_entered.is_set()
    assert failures == []


def test_same_database_reentry_fails_fast_and_rolls_back_outer_work(tmp_path) -> None:
    database_path = tmp_path / "reentry.db"
    _create_values_table(database_path)

    def outer_callback(connection: sqlite3.Connection) -> None:
        connection.execute("INSERT INTO values_table (value) VALUES (1)")
        run_write_transaction(database_path, lambda nested_connection: None)

    with pytest.raises(RuntimeError, match="nested write transactions are not supported"):
        run_write_transaction(database_path, outer_callback)

    with connect(database_path) as verification_connection:
        assert verification_connection.execute(
            "SELECT value FROM values_table"
        ).fetchall() == []


def test_cross_database_reentry_is_also_rejected(tmp_path) -> None:
    outer_path = tmp_path / "outer.db"
    nested_path = tmp_path / "nested.db"
    _create_values_table(outer_path)
    _create_values_table(nested_path)

    def outer_callback(connection: sqlite3.Connection) -> None:
        connection.execute("INSERT INTO values_table (value) VALUES (1)")
        run_write_transaction(nested_path, lambda nested_connection: None)

    with pytest.raises(RuntimeError, match="nested write transactions are not supported"):
        run_write_transaction(outer_path, outer_callback)

    with connect(outer_path) as verification_connection:
        assert verification_connection.execute(
            "SELECT COUNT(*) FROM values_table"
        ).fetchone()[0] == 0
    with connect(nested_path) as verification_connection:
        assert verification_connection.execute(
            "SELECT COUNT(*) FROM values_table"
        ).fetchone()[0] == 0


@pytest.mark.parametrize("database_path", [Path(":memory:"), Path("file:memory-db")])
def test_special_database_targets_fail_closed(database_path) -> None:
    with pytest.raises(
        ValueError, match="write transactions require a file-backed SQLite database path"
    ):
        run_write_transaction(database_path, lambda connection: None)
