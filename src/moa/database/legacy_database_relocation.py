"""Explicit relocation of MOA's former checkout-local SQLite database."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from moa.database.migrations import CATALOG_MIGRATIONS, CATALOG_TABLES


class LegacyDatabaseRelocationError(ValueError):
    """Base error for legacy database authority and relocation failures."""


class LegacyDatabaseRelocationRequiredError(LegacyDatabaseRelocationError):
    """Raised when the former checkout database must be explicitly relocated."""


class LegacyDatabaseAuthorityConflictError(LegacyDatabaseRelocationError):
    """Raised when both former and new live database paths exist."""


class DatabaseRelocationError(LegacyDatabaseRelocationError):
    """Raised when an explicit database relocation cannot complete safely."""


@dataclass(frozen=True, slots=True)
class DatabaseRelocationResult:
    """Paths established by one completed relocation."""

    source: Path
    target: Path
    source_archive: Path


@dataclass(frozen=True, slots=True)
class _DatabaseFingerprint:
    migration_rows: tuple[tuple[int, str], ...]
    table_counts: tuple[tuple[str, int], ...]
    representative_import_event: tuple[object, ...] | None


def _source_file_path() -> Path:
    return Path(__file__).resolve()


def verified_legacy_database_path() -> Path | None:
    """Return the one verified current-checkout legacy path, if available."""
    source_path = _source_file_path()
    try:
        checkout_root = source_path.parents[3]
    except IndexError:
        return None
    expected_source = checkout_root / "src" / "moa" / "database" / source_path.name
    if source_path != expected_source.resolve(strict=False):
        return None
    if not (checkout_root / ".git").exists():
        return None
    if not (checkout_root / "pyproject.toml").is_file():
        return None
    if not (checkout_root / "src" / "moa" / "database" / "sqlite.py").is_file():
        return None
    return checkout_root / "data" / "database" / "moa.db"


def ensure_default_database_authority(target: Path) -> None:
    """Fail closed when a verified checkout database remains authoritative."""
    legacy_path = verified_legacy_database_path()
    if legacy_path is None or not legacy_path.exists():
        return
    resolved_legacy = legacy_path.resolve(strict=False)
    resolved_target = Path(target).resolve(strict=False)
    if resolved_target.exists():
        raise LegacyDatabaseAuthorityConflictError(
            "Both the legacy MOA database and the new default database exist. "
            f"Legacy: {resolved_legacy}. New default: {resolved_target}. "
            "MOA cannot choose or merge them; resolve the two database authorities explicitly."
        )
    raise LegacyDatabaseRelocationRequiredError(
        "A legacy checkout-local MOA database was detected before the new default "
        f"database was initialized. Legacy: {resolved_legacy}. New default: {resolved_target}. "
        "Stop the Discord listener, then run "
        f"`moa catalog relocate-database {resolved_legacy} --apply`. "
        "MOA will not migrate it automatically."
    )


def relocate_database(source: Path, target: Path) -> DatabaseRelocationResult:
    """Relocate one explicit MOA database with backup, validation, and retirement."""
    source_path = _canonical_file_path(source, label="source")
    target_path = _canonical_file_path(target, label="target")
    if source_path == target_path:
        raise DatabaseRelocationError("Relocation source and target must be distinct paths.")
    if not source_path.is_file():
        raise DatabaseRelocationError(f"Relocation source does not exist: {source_path}")
    if target_path.exists():
        raise DatabaseRelocationError(
            f"Relocation target already exists and will not be overwritten: {target_path}"
        )

    verified_legacy = verified_legacy_database_path()
    if verified_legacy is not None and verified_legacy.exists():
        resolved_legacy = verified_legacy.resolve(strict=False)
        if source_path != resolved_legacy:
            raise DatabaseRelocationError(
                "A verified checkout-local legacy database exists and must be the explicit "
                f"relocation source: {resolved_legacy}"
            )

    target_path.parent.mkdir(parents=True, exist_ok=True)
    stale_paths = tuple(sorted(target_path.parent.glob(f"{target_path.name}.migrating-*")))
    if stale_paths:
        rendered = ", ".join(str(path) for path in stale_paths)
        raise DatabaseRelocationError(
            f"Recognized stale relocation temporary file(s) require cleanup: {rendered}"
        )
    source_fingerprint = _validate_database(source_path)
    _checkpoint_source_for_retirement(source_path)
    if _validate_database(source_path) != source_fingerprint:
        raise DatabaseRelocationError(
            "Relocation source changed while it was being prepared for backup."
        )

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{target_path.name}.migrating-",
        dir=target_path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    promoted = False
    try:
        _backup_database(source_path, temporary_path)
        _checkpoint_source_for_retirement(temporary_path)
        target_fingerprint = _validate_database(temporary_path)
        if target_fingerprint != source_fingerprint:
            raise DatabaseRelocationError(
                "Relocation target validation did not match the source database."
            )
        _checkpoint_source_for_retirement(source_path)
        if _validate_database(source_path) != source_fingerprint:
            raise DatabaseRelocationError(
                "Relocation source changed while its SQLite backup was being created."
            )
        _checkpoint_source_for_retirement(temporary_path)
        _remove_checkpointed_sidecars(temporary_path)
        _promote_target(temporary_path, target_path)
        promoted = True
        try:
            temporary_path.unlink()
        except OSError as error:
            raise DatabaseRelocationError(
                "The new database was promoted, but migration did not complete because "
                f"the temporary path could not be removed: {temporary_path}. Source: "
                f"{source_path}. Target: {target_path}. Resolve this dual-authority "
                "state explicitly."
            ) from error
    except BaseException as error:
        if not promoted:
            _cleanup_temporary_path(temporary_path, error)
        raise

    try:
        archive_path = _retire_source(source_path)
    except BaseException as error:
        raise DatabaseRelocationError(
            "The new database was promoted and remains valid, but migration did not "
            f"complete because the source could not be retired. Source: {source_path}. "
            f"Target: {target_path}. Resolve this dual-authority state explicitly."
        ) from error
    return DatabaseRelocationResult(source_path, target_path, archive_path)


def _canonical_file_path(path: Path, *, label: str) -> Path:
    raw_path = os.fspath(path)
    if raw_path == ":memory:" or raw_path.startswith("file:"):
        raise DatabaseRelocationError(
            f"Relocation {label} must be an ordinary file-backed SQLite path."
        )
    return Path(path).expanduser().resolve(strict=False)


def _open_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _validate_database(path: Path) -> _DatabaseFingerprint:
    try:
        connection = _open_read_only(path)
        try:
            integrity_rows = tuple(
                row[0] for row in connection.execute("PRAGMA integrity_check").fetchall()
            )
            if integrity_rows != ("ok",):
                raise DatabaseRelocationError(
                    f"SQLite integrity validation failed for {path}: {integrity_rows}"
                )
            table_names = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            )
            missing_core = sorted(CATALOG_TABLES - set(table_names))
            if missing_core:
                raise DatabaseRelocationError(
                    f"Database is not a recognized MOA catalog; missing tables: "
                    f"{', '.join(missing_core)}"
                )
            migration_rows = tuple(
                (int(row[0]), str(row[1]))
                for row in connection.execute(
                    "SELECT version, name FROM schema_migrations ORDER BY version"
                ).fetchall()
            )
            expected_migrations = tuple(
                (migration.version, migration.name) for migration in CATALOG_MIGRATIONS
            )
            if migration_rows != expected_migrations:
                raise DatabaseRelocationError(
                    "Database does not have the current recognized MOA migration identity."
                )
            table_counts = tuple(
                (name, _table_count(connection, name)) for name in table_names
            )
            representative_row = connection.execute(
                "SELECT id, kind, source, observed_at, raw_message "
                "FROM import_events ORDER BY id LIMIT 1"
            ).fetchone()
            representative_import_event = (
                tuple(representative_row) if representative_row is not None else None
            )
            return _DatabaseFingerprint(
                migration_rows,
                table_counts,
                representative_import_event,
            )
        finally:
            connection.close()
    except DatabaseRelocationError:
        raise
    except sqlite3.Error as error:
        raise DatabaseRelocationError(
            f"Could not validate MOA SQLite database {path}: {error}"
        ) from error


def _table_count(connection: sqlite3.Connection, table_name: str) -> int:
    quoted_name = table_name.replace('"', '""')
    return int(connection.execute(f'SELECT COUNT(*) FROM "{quoted_name}"').fetchone()[0])


def _backup_database(source: Path, temporary_target: Path) -> None:
    try:
        source_connection = _open_read_only(source)
        try:
            target_connection = sqlite3.connect(temporary_target)
            try:
                source_connection.backup(target_connection)
            finally:
                target_connection.close()
        finally:
            source_connection.close()
    except sqlite3.Error as error:
        raise DatabaseRelocationError(f"SQLite backup failed: {error}") from error


def _checkpoint_source_for_retirement(source: Path) -> None:
    from moa.database.sqlite import connect

    try:
        connection = connect(source)
        try:
            busy, log_frames, checkpointed_frames = connection.execute(
                "PRAGMA wal_checkpoint(TRUNCATE)"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise DatabaseRelocationError(
            "LEGACY_SOURCE_RETIREMENT_BLOCKER: could not checkpoint the explicit source; "
            "stop every listener and close every database connection."
        ) from error
    if busy or log_frames != checkpointed_frames:
        raise DatabaseRelocationError(
            "LEGACY_SOURCE_RETIREMENT_BLOCKER: the explicit source has an active SQLite "
            "user; stop every listener and close every database connection."
        )


def _promote_target(temporary_target: Path, target: Path) -> None:
    try:
        os.link(temporary_target, target)
    except FileExistsError as error:
        raise DatabaseRelocationError(
            f"Relocation target appeared during promotion and was not overwritten: {target}"
        ) from error
    except OSError as error:
        raise DatabaseRelocationError(
            f"Could not atomically promote relocation target {target}: {error}"
        ) from error


def _retire_source(source: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    archive_directory = source.with_name(
        f"{source.name}.migrated-backup-{timestamp}-{uuid4().hex[:8]}"
    )
    try:
        archive_directory.mkdir()
    except OSError as error:
        raise DatabaseRelocationError(
            f"Could not reserve non-authoritative source archive {archive_directory}: {error}"
        ) from error
    archive = archive_directory / source.name
    moved_paths: list[tuple[Path, Path]] = []
    try:
        source.rename(archive)
        moved_paths.append((source, archive))
        for original in (Path(f"{source}-wal"), Path(f"{source}-shm")):
            if original.exists():
                archived_sidecar = archive_directory / original.name
                original.rename(archived_sidecar)
                moved_paths.append((original, archived_sidecar))
    except BaseException as error:
        for original, archived_path in reversed(moved_paths):
            try:
                archived_path.rename(original)
            except BaseException as restore_error:
                error.add_note(
                    f"Could not restore {original} from incomplete archive: {restore_error}"
                )
        try:
            archive_directory.rmdir()
        except BaseException as cleanup_error:
            error.add_note(
                f"Could not remove incomplete archive directory {archive_directory}: "
                f"{cleanup_error}"
            )
        raise
    return archive


def _cleanup_temporary_path(path: Path, primary_error: BaseException) -> None:
    for cleanup_path in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            cleanup_path.unlink(missing_ok=True)
        except BaseException as cleanup_error:
            primary_error.add_note(
                f"Could not remove relocation temporary file {cleanup_path}: {cleanup_error}"
            )


def _remove_checkpointed_sidecars(path: Path) -> None:
    for sidecar in (Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            sidecar.unlink(missing_ok=True)
        except OSError as error:
            raise DatabaseRelocationError(
                f"Could not remove checkpointed relocation sidecar {sidecar}: {error}"
            ) from error
