"""Small SQLite connection factory for MOA's local state database."""

import os
import sqlite3
import threading
from collections.abc import Callable
from os import PathLike
from pathlib import Path
from typing import TypeVar

from platformdirs import user_data_path

from moa.database.writer_lease import shared_database_writer_lease

_DATABASE_PATH_OVERRIDE_ENV = "MOA_DATABASE_PATH"


def default_database_path() -> Path:
    """Return MOA's per-user, platform-native live database path."""
    return user_data_path(appname="moa", appauthor=False, roaming=False) / "moa.db"


def _database_path_override() -> Path | None:
    configured_path = os.environ.get(_DATABASE_PATH_OVERRIDE_ENV)
    if configured_path is None:
        return None

    configured_path = configured_path.strip()
    if not configured_path:
        raise ValueError(
            f"{_DATABASE_PATH_OVERRIDE_ENV} must be an absolute SQLite database path."
        )

    path = Path(configured_path).expanduser()
    if not path.is_absolute():
        raise ValueError(
            f"{_DATABASE_PATH_OVERRIDE_ENV} must be an absolute SQLite database path; "
            f"got {configured_path!r}."
        )
    return path.resolve(strict=False)


def effective_default_database_path() -> Path:
    """Resolve the implicit database path after checking legacy authority."""
    override = _database_path_override()
    if override is not None:
        return override

    from moa.database.legacy_database_relocation import ensure_default_database_authority

    path = default_database_path()
    ensure_default_database_authority(path)
    return path


class _DefaultDatabasePath(PathLike[str]):
    """Keep imported default bindings live without resolving user state at import time."""

    def __fspath__(self) -> str:
        return str(effective_default_database_path())

    def __str__(self) -> str:
        return self.__fspath__()

    def __repr__(self) -> str:
        return f"DEFAULT_DATABASE_PATH({self.__fspath__()!r})"

    def __getattr__(self, name: str):
        return getattr(effective_default_database_path(), name)


DEFAULT_DATABASE_PATH = _DefaultDatabasePath()

_ResultT = TypeVar("_ResultT")
_writer_locks: dict[Path, threading.Lock] = {}
_writer_locks_guard = threading.Lock()
_write_transaction_state = threading.local()


def connect(database_path: Path | None = None) -> sqlite3.Connection:
    """Open MOA's local SQLite database with the shared connection policy."""
    path = _resolve_database_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def connect_read_only(database_path: Path | None = None) -> sqlite3.Connection:
    """Open an existing MOA SQLite database without creating or configuring it."""
    path = _resolve_database_path(database_path).expanduser().resolve(strict=False)
    if str(path) == ":memory:" or str(path).startswith("file:"):
        raise ValueError("read-only connections require a file-backed SQLite database path")

    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _canonical_database_path(database_path: Path | None) -> Path:
    path = _resolve_database_path(database_path)
    path_text = str(path)
    if path_text == ":memory:" or path_text.startswith("file:"):
        raise ValueError("write transactions require a file-backed SQLite database path")
    return Path(path).resolve(strict=False)


def _resolve_database_path(database_path: Path | None) -> Path:
    if database_path is None or database_path is DEFAULT_DATABASE_PATH:
        return effective_default_database_path()
    return Path(database_path)


def _writer_lock(database_path: Path) -> threading.Lock:
    with _writer_locks_guard:
        lock = _writer_locks.get(database_path)
        if lock is None:
            lock = threading.Lock()
            _writer_locks[database_path] = lock
        return lock


def _rollback_if_active(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        return
    try:
        connection.rollback()
    except Exception:
        # Cleanup must not replace the callback or commit exception being propagated.
        pass


def run_write_transaction(
    database_path: Path | None,
    callback: Callable[[sqlite3.Connection], _ResultT],
) -> _ResultT:
    """Run database-only work in one serialized, runner-owned write transaction.

    The callback must use only the supplied connection for database-local work
    and must not commit, roll back, enter another runner transaction, or perform
    external side effects.
    """
    canonical_path = _canonical_database_path(database_path)
    if getattr(_write_transaction_state, "active", False):
        raise RuntimeError("nested write transactions are not supported")

    lock = _writer_lock(canonical_path)
    with shared_database_writer_lease():
        _write_transaction_state.active = True
        try:
            with lock:
                connection = connect(canonical_path)
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    result = callback(connection)
                    connection.commit()
                except BaseException:
                    _rollback_if_active(connection)
                    try:
                        connection.close()
                    except Exception:
                        # Cleanup must not replace the callback or commit failure.
                        pass
                    raise
                connection.close()
        finally:
            _write_transaction_state.active = False

    return result
