"""Fail-closed backup and restore proof for an isolated projection generation."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from moa.database.migrations import (
    CATALOG_MIGRATIONS,
    MigrationError,
    validate_current_catalog_schema,
)
from moa.database.sqlite import default_database_path
from moa.services.retained_source_reprojection_preflight_service import (
    RetainedSourceReprojectionPreflightError,
    RetainedSourceReprojectionPreflightService,
)

_WRITER_EXCLUSION_TIMEOUT_MS = 1_000


class ProjectionGenerationBackupError(RuntimeError):
    """The requested backup or its restore proof could not be certified safely."""


@dataclass(frozen=True, slots=True)
class ProjectionGenerationBackupResult:
    """Identity and certification evidence for one backup and restore proof."""

    source_path: Path
    backup_path: Path
    restore_probe_path: Path
    source_sha256: str
    source_size: int
    backup_sha256: str
    backup_size: int
    restore_probe_sha256: str
    restore_probe_size: int
    migration_identity: tuple[tuple[int, str], ...]
    generation_ids: tuple[int, ...]
    current_generation_id: int
    hypothetical_generation_id: int
    preflight_fingerprint: str


@dataclass(frozen=True, slots=True)
class _DatabaseValidation:
    migration_identity: tuple[tuple[int, str], ...]
    generation_ids: tuple[int, ...]
    current_generation_id: int
    hypothetical_generation_id: int
    preflight_fingerprint: str


def create_projection_generation_backup(
    source: Path,
    backup: Path,
    restore_probe: Path,
    worktree_root: Path,
) -> ProjectionGenerationBackupResult:
    """Back up one explicit isolated database and prove an exact disposable restore."""

    source_path = _require_source(source)
    backup_path = _require_output(backup, label="backup")
    restore_path = _require_output(restore_probe, label="restore probe")
    _reject_operational_database_paths(source_path, backup_path, restore_path)
    root_path = _require_worktree_root(worktree_root)
    _validate_path_relationships(source_path, backup_path, restore_path, root_path)

    source_connection: sqlite3.Connection | None = None
    primary_error: BaseException | None = None
    created_outputs: list[Path] = []
    try:
        source_connection = _acquire_writer_exclusion(source_path)
        source_identity = _file_identity(source_path)
        source_validation = _validate_connection(source_connection)

        _backup_and_promote(source_path, backup_path)
        created_outputs.append(backup_path)
        backup_validation = _validate_path(backup_path)
        if backup_validation != source_validation:
            raise ProjectionGenerationBackupError(
                "Promoted backup validation does not match the excluded source snapshot."
            )
        backup_identity = _file_identity(backup_path)

        _backup_and_promote(backup_path, restore_path)
        created_outputs.append(restore_path)
        restore_validation = _validate_path(restore_path)
        if restore_validation != source_validation:
            raise ProjectionGenerationBackupError(
                "Restore-probe validation does not match the excluded source snapshot."
            )
        restore_identity = _file_identity(restore_path)
        if restore_identity != backup_identity:
            raise ProjectionGenerationBackupError(
                "Restore-probe digest or size does not match the promoted backup."
            )
        if _validate_connection(source_connection) != source_validation:
            raise ProjectionGenerationBackupError(
                "Source validation changed despite writer exclusion."
            )
        if _file_identity(source_path) != source_identity:
            raise ProjectionGenerationBackupError(
                "Source file digest or size changed during backup certification."
            )

        return ProjectionGenerationBackupResult(
            source_path=source_path,
            backup_path=backup_path,
            restore_probe_path=restore_path,
            source_sha256=source_identity[0],
            source_size=source_identity[1],
            backup_sha256=backup_identity[0],
            backup_size=backup_identity[1],
            restore_probe_sha256=restore_identity[0],
            restore_probe_size=restore_identity[1],
            migration_identity=source_validation.migration_identity,
            generation_ids=source_validation.generation_ids,
            current_generation_id=source_validation.current_generation_id,
            hypothetical_generation_id=source_validation.hypothetical_generation_id,
            preflight_fingerprint=source_validation.preflight_fingerprint,
        )
    except BaseException as error:
        primary_error = error
        _cleanup_created_outputs(created_outputs, error)
        if isinstance(error, ProjectionGenerationBackupError):
            raise
        raise ProjectionGenerationBackupError(
            "Projection-generation backup or restore certification failed."
        ) from error
    finally:
        if source_connection is not None:
            _release_writer_exclusion(source_connection, primary_error=primary_error)


def _require_source(path: Path) -> Path:
    raw = _ordinary_path(path, label="source")
    if raw.is_symlink() or not raw.is_file():
        raise ProjectionGenerationBackupError(
            "Source must be an existing regular non-symlink file."
        )
    return raw.resolve(strict=True)


def _reject_operational_database_paths(*paths: Path) -> None:
    canonical_default = default_database_path().expanduser().resolve(strict=False)
    if canonical_default in paths:
        raise ProjectionGenerationBackupError(
            "The canonical MOA default database is not an allowed isolated backup path."
        )
    from moa.database.legacy_database_relocation import verified_legacy_database_path

    legacy = verified_legacy_database_path()
    if legacy is not None and legacy.expanduser().resolve(strict=False) in paths:
        raise ProjectionGenerationBackupError(
            "The verified legacy MOA database is not an allowed isolated backup path."
        )


def _require_output(path: Path, *, label: str) -> Path:
    raw = _ordinary_path(path, label=label)
    parent = raw.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ProjectionGenerationBackupError(
            f"{label.capitalize()} parent must be an existing non-symlink directory."
        )
    resolved = raw.resolve(strict=False)
    if raw.exists() or raw.is_symlink() or resolved.exists():
        raise ProjectionGenerationBackupError(
            f"{label.capitalize()} target must be absent and will not be overwritten."
        )
    return resolved


def _require_worktree_root(path: Path) -> Path:
    raw = _ordinary_path(path, label="worktree root")
    if raw.is_symlink() or not raw.is_dir():
        raise ProjectionGenerationBackupError(
            "Worktree root must be an existing non-symlink directory."
        )
    return raw.resolve(strict=True)


def _ordinary_path(path: Path, *, label: str) -> Path:
    if not isinstance(path, Path):
        raise ProjectionGenerationBackupError(f"{label.capitalize()} must be an explicit Path.")
    rendered = os.fspath(path)
    if rendered == ":memory:" or rendered.startswith("file:"):
        raise ProjectionGenerationBackupError(
            f"{label.capitalize()} must be an ordinary file-backed path."
        )
    return path.expanduser()


def _validate_path_relationships(
    source: Path,
    backup: Path,
    restore_probe: Path,
    worktree_root: Path,
) -> None:
    if len({source, backup, restore_probe}) != 3:
        raise ProjectionGenerationBackupError(
            "Source, backup, and restore-probe paths must be canonically distinct."
        )
    for label, output in (("Backup", backup), ("Restore probe", restore_probe)):
        if output == worktree_root or worktree_root in output.parents:
            raise ProjectionGenerationBackupError(f"{label} output must be outside the worktree.")


def _acquire_writer_exclusion(path: Path) -> sqlite3.Connection:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {_WRITER_EXCLUSION_TIMEOUT_MS}")
        connection.execute("BEGIN IMMEDIATE")
        return connection
    except sqlite3.Error as error:
        if connection is not None:
            try:
                connection.close()
            except BaseException as cleanup_error:
                error.add_note(f"Could not close failed source connection: {cleanup_error}")
        raise ProjectionGenerationBackupError(
            "Could not acquire bounded SQLite writer exclusion on the explicit source."
        ) from error


def _release_writer_exclusion(
    connection: sqlite3.Connection,
    *,
    primary_error: BaseException | None,
) -> None:
    cleanup_errors: list[BaseException] = []
    try:
        if connection.in_transaction:
            connection.rollback()
    except BaseException as error:
        cleanup_errors.append(error)
    try:
        connection.close()
    except BaseException as error:
        cleanup_errors.append(error)
    if not cleanup_errors:
        return
    details = "; ".join(str(error) for error in cleanup_errors)
    if primary_error is not None:
        primary_error.add_note(f"Writer-exclusion cleanup uncertainty: {details}")
        return
    raise ProjectionGenerationBackupError(f"Writer-exclusion cleanup uncertainty: {details}")


def _validate_path(path: Path) -> _DatabaseValidation:
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {_WRITER_EXCLUSION_TIMEOUT_MS}")
        connection.execute("BEGIN")
        try:
            return _validate_connection(connection)
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()
    except ProjectionGenerationBackupError:
        raise
    except sqlite3.Error as error:
        raise ProjectionGenerationBackupError(
            f"Could not reopen and validate SQLite database: {path}"
        ) from error


def _validate_connection(connection: sqlite3.Connection) -> _DatabaseValidation:
    try:
        integrity = tuple(
            str(row[0]) for row in connection.execute("PRAGMA integrity_check").fetchall()
        )
        if integrity != ("ok",):
            raise ProjectionGenerationBackupError("SQLite integrity validation failed.")
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise ProjectionGenerationBackupError("SQLite foreign-key validation failed.")
        validate_current_catalog_schema(connection)
        migrations = tuple(
            (int(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall()
        )
        expected_migrations = tuple(
            (migration.version, migration.name) for migration in CATALOG_MIGRATIONS
        )
        if migrations != expected_migrations:
            raise ProjectionGenerationBackupError(
                "Database migration identity is not the current recognized identity."
            )
        generation_rows = connection.execute(
            "SELECT id, is_current FROM projection_generations ORDER BY id"
        ).fetchall()
        generation_ids = tuple(int(row[0]) for row in generation_rows)
        current_ids = tuple(int(row[0]) for row in generation_rows if int(row[1]) == 1)
        if (
            not generation_ids
            or any(item <= 0 for item in generation_ids)
            or generation_ids != tuple(sorted(set(generation_ids)))
            or any(int(row[1]) not in (0, 1) for row in generation_rows)
            or len(current_ids) != 1
        ):
            raise ProjectionGenerationBackupError(
                "Database must have exactly one positive current projection generation."
            )
        preflight = RetainedSourceReprojectionPreflightService().preflight(connection)
        if preflight.current_generation_id != current_ids[0]:
            raise ProjectionGenerationBackupError(
                "Retained-source preflight current generation does not match storage."
            )
        return _DatabaseValidation(
            migrations,
            generation_ids,
            current_ids[0],
            preflight.hypothetical_generation_id,
            preflight.inventory_fingerprint,
        )
    except ProjectionGenerationBackupError:
        raise
    except (MigrationError, RetainedSourceReprojectionPreflightError, sqlite3.Error) as error:
        raise ProjectionGenerationBackupError(
            "Database validation or retained-source preflight failed."
        ) from error


def _backup_and_promote(source: Path, target: Path) -> None:
    descriptor = -1
    temporary_path: Path | None = None
    promoted = False
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.projection-backup-", dir=target.parent
        )
        temporary_path = Path(temporary_name)
        os.close(descriptor)
        descriptor = -1
        source_connection = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
        try:
            target_connection = sqlite3.connect(temporary_path)
            try:
                source_connection.backup(target_connection)
            finally:
                target_connection.close()
        finally:
            source_connection.close()
        with temporary_path.open("r+b") as temporary_file:
            os.fsync(temporary_file.fileno())
        try:
            os.link(temporary_path, target)
            promoted = True
        except FileExistsError as error:
            raise ProjectionGenerationBackupError(
                f"Output target appeared during promotion and was not overwritten: {target}"
            ) from error
        except OSError as error:
            raise ProjectionGenerationBackupError(
                f"Could not atomically promote output target: {target}"
            ) from error
    except ProjectionGenerationBackupError:
        raise
    except (OSError, sqlite3.Error) as error:
        raise ProjectionGenerationBackupError("SQLite backup or promotion failed.") from error
    finally:
        if descriptor != -1:
            os.close(descriptor)
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError as cleanup_error:
                if promoted:
                    try:
                        target.unlink()
                    except OSError as target_cleanup_error:
                        cleanup_error.add_note(
                            f"Promoted output cleanup uncertainty: {target}: {target_cleanup_error}"
                        )
                raise ProjectionGenerationBackupError(
                    f"Temporary backup cleanup uncertainty: {temporary_path}"
                ) from cleanup_error


def _file_identity(path: Path) -> tuple[str, int]:
    try:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as file:
            while chunk := file.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size
    except OSError as error:
        raise ProjectionGenerationBackupError(f"Could not digest database file: {path}") from error


def _cleanup_created_outputs(paths: list[Path], primary_error: BaseException) -> None:
    failures: list[str] = []
    for path in reversed(paths):
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            failures.append(f"{path}: {error}")
    if failures:
        primary_error.add_note("Output cleanup uncertainty: " + "; ".join(failures))
