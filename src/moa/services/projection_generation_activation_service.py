"""Fail-closed activation of one certified isolated projection generation."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from moa.database.migrations import (
    CATALOG_MIGRATIONS,
    MigrationError,
    validate_current_catalog_schema,
)
from moa.database.projection_generation_backup import ProjectionGenerationBackupResult
from moa.database.sqlite import default_database_path, run_write_transaction
from moa.repositories.projection_link_repository import (
    ProjectionLinkIntegrityError,
    ProjectionLinkRepository,
)
from moa.services.retained_source_reprojection_preflight_service import (
    RetainedSourceReprojectionPreflightError,
    RetainedSourceReprojectionPreflightService,
)

_EXPECTED_SWITCH_MUTATIONS = 3


class ProjectionGenerationActivationError(RuntimeError):
    """The certified activation was rejected before a known commit."""

    retry_safe = True
    outcome_uncertain = False


class ProjectionGenerationActivationOutcomeUncertainError(ProjectionGenerationActivationError):
    """The transaction callback passed, but its commit outcome is not known."""

    retry_safe = False
    outcome_uncertain = True


@dataclass(frozen=True, slots=True)
class ProjectionGenerationActivationResult:
    """Known-committed identity for one newly selected empty generation."""

    database_path: Path
    historical_generation_ids: tuple[int, ...]
    current_generation_id: int
    mutation_count: int
    preflight_fingerprint: str


class ProjectionGenerationActivationService:
    """Activate only a fully certified isolated database generation."""

    def activate(
        self,
        database_path: Path,
        backup_evidence: ProjectionGenerationBackupResult,
        reviewed_preflight_fingerprint: str,
    ) -> ProjectionGenerationActivationResult:
        return activate_projection_generation(
            database_path, backup_evidence, reviewed_preflight_fingerprint
        )


@dataclass(frozen=True, slots=True)
class _ActivationRequest:
    database_path: Path
    evidence: ProjectionGenerationBackupResult
    reviewed_preflight_fingerprint: str


def activate_projection_generation(
    database_path: Path,
    backup_evidence: ProjectionGenerationBackupResult,
    reviewed_preflight_fingerprint: str,
) -> ProjectionGenerationActivationResult:
    """Select the certified hypothetical generation without starting reprojection."""

    request = _validate_request(database_path, backup_evidence, reviewed_preflight_fingerprint)
    callback_completed = False

    def activate(connection: sqlite3.Connection) -> ProjectionGenerationActivationResult:
        nonlocal callback_completed
        result = _activate_in_transaction(connection, request)
        callback_completed = True
        return result

    try:
        return run_write_transaction(request.database_path, activate)
    except ProjectionGenerationActivationError:
        raise
    except BaseException as error:
        if callback_completed:
            raise ProjectionGenerationActivationOutcomeUncertainError(
                "Projection-generation commit outcome is uncertain; do not retry blindly."
            ) from error
        raise ProjectionGenerationActivationError(
            "Projection-generation activation failed before commit."
        ) from error


def _validate_request(
    database_path: Path,
    evidence: ProjectionGenerationBackupResult,
    reviewed_fingerprint: str,
) -> _ActivationRequest:
    source = _require_regular_path(database_path, label="database")
    if source == default_database_path().expanduser().resolve(strict=False):
        raise ProjectionGenerationActivationError(
            "The canonical MOA default database is not an allowed activation target."
        )
    if not isinstance(evidence, ProjectionGenerationBackupResult):
        raise ProjectionGenerationActivationError(
            "Verified projection-generation backup evidence is required."
        )
    backup = _require_regular_path(evidence.backup_path, label="backup")
    if evidence.source_path != source or evidence.backup_path != backup:
        raise ProjectionGenerationActivationError(
            "Backup evidence paths do not identify the explicit activation target."
        )
    if (
        not isinstance(reviewed_fingerprint, str)
        or len(reviewed_fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in reviewed_fingerprint)
        or reviewed_fingerprint != evidence.preflight_fingerprint
    ):
        raise ProjectionGenerationActivationError(
            "Reviewed preflight fingerprint does not match backup certification."
        )
    if (
        evidence.source_size < 0
        or evidence.backup_size < 0
        or evidence.restore_probe_size < 0
        or evidence.backup_sha256 != evidence.restore_probe_sha256
        or evidence.backup_size != evidence.restore_probe_size
        or not _is_sha256(evidence.source_sha256)
        or not _is_sha256(evidence.backup_sha256)
        or not evidence.generation_ids
        or evidence.current_generation_id not in evidence.generation_ids
        or evidence.hypothetical_generation_id in evidence.generation_ids
        or evidence.hypothetical_generation_id != evidence.generation_ids[-1] + 1
    ):
        raise ProjectionGenerationActivationError(
            "Backup certification evidence is internally inconsistent."
        )
    return _ActivationRequest(source, evidence, reviewed_fingerprint)


def _require_regular_path(path: Path, *, label: str) -> Path:
    if not isinstance(path, Path):
        raise ProjectionGenerationActivationError(f"{label.capitalize()} must be an explicit Path.")
    rendered = os.fspath(path)
    if rendered == ":memory:" or rendered.startswith("file:"):
        raise ProjectionGenerationActivationError(
            f"{label.capitalize()} must be an ordinary file-backed path."
        )
    expanded = path.expanduser()
    if expanded.is_symlink() or not expanded.is_file():
        raise ProjectionGenerationActivationError(
            f"{label.capitalize()} must be an existing regular non-symlink file."
        )
    return expanded.resolve(strict=True)


def _activate_in_transaction(
    connection: sqlite3.Connection, request: _ActivationRequest
) -> ProjectionGenerationActivationResult:
    if not connection.in_transaction:
        raise ProjectionGenerationActivationError(
            "Activation requires the runner-owned write transaction."
        )
    evidence = request.evidence
    try:
        _require_identity(
            evidence.backup_path,
            expected_digest=evidence.backup_sha256,
            expected_size=evidence.backup_size,
            label="backup",
        )
        _require_certified_source(connection, request)
        _validate_integrity(connection)
        validate_current_catalog_schema(connection)
        migrations = _migration_identity(connection)
        if migrations != evidence.migration_identity or migrations != _expected_migrations():
            raise ProjectionGenerationActivationError(
                "Schema migration identity changed after backup certification."
            )

        before_generations = _generation_inventory(connection)
        expected_generations = tuple(
            (item, int(item == evidence.current_generation_id)) for item in evidence.generation_ids
        )
        if before_generations != expected_generations:
            raise ProjectionGenerationActivationError(
                "Projection generation inventory changed after backup certification."
            )

        preflight = RetainedSourceReprojectionPreflightService().preflight(connection)
        if (
            preflight.current_generation_id != evidence.current_generation_id
            or preflight.hypothetical_generation_id != evidence.hypothetical_generation_id
            or preflight.inventory_fingerprint != request.reviewed_preflight_fingerprint
        ):
            raise ProjectionGenerationActivationError("Reviewed reprojection preflight is stale.")

        hypothetical_id = evidence.hypothetical_generation_id
        if _generation_exists(connection, hypothetical_id) or _generation_link_count(
            connection, hypothetical_id
        ):
            raise ProjectionGenerationActivationError(
                "Certified hypothetical generation must be absent and empty."
            )
        historical_links = _projection_links(connection)
        changes_before = connection.total_changes
        new_generation_id = ProjectionLinkRepository(connection).switch_current_generation()
        mutation_count = connection.total_changes - changes_before
        if new_generation_id != hypothetical_id or mutation_count != _EXPECTED_SWITCH_MUTATIONS:
            raise ProjectionGenerationActivationError(
                "Projection-generation switch mutation accounting was not exact."
            )

        expected_after = tuple((item, 0) for item in evidence.generation_ids) + (
            (hypothetical_id, 1),
        )
        if _generation_inventory(connection) != expected_after:
            raise ProjectionGenerationActivationError(
                "Projection-generation postconditions were not satisfied."
            )
        if _generation_link_count(connection, hypothetical_id) != 0:
            raise ProjectionGenerationActivationError("The new current generation must be empty.")
        if _projection_links(connection) != historical_links:
            raise ProjectionGenerationActivationError(
                "Historical projection links changed during generation activation."
            )
        if connection.total_changes - changes_before != _EXPECTED_SWITCH_MUTATIONS:
            raise ProjectionGenerationActivationError(
                "Unexpected mutations occurred during activation postcondition checks."
            )
        _validate_integrity(connection)
        return ProjectionGenerationActivationResult(
            request.database_path,
            evidence.generation_ids,
            hypothetical_id,
            mutation_count,
            request.reviewed_preflight_fingerprint,
        )
    except ProjectionGenerationActivationError:
        raise
    except (
        MigrationError,
        ProjectionLinkIntegrityError,
        RetainedSourceReprojectionPreflightError,
        OSError,
        sqlite3.Error,
    ) as error:
        raise ProjectionGenerationActivationError(
            "Certified projection-generation activation preconditions failed."
        ) from error


def _validate_integrity(connection: sqlite3.Connection) -> None:
    integrity = tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check"))
    if integrity != ("ok",):
        raise ProjectionGenerationActivationError("SQLite integrity validation failed.")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise ProjectionGenerationActivationError("SQLite foreign-key validation failed.")


def _migration_identity(connection: sqlite3.Connection) -> tuple[tuple[int, str], ...]:
    return tuple(
        (int(row[0]), str(row[1]))
        for row in connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        )
    )


def _expected_migrations() -> tuple[tuple[int, str], ...]:
    return tuple((migration.version, migration.name) for migration in CATALOG_MIGRATIONS)


def _generation_inventory(connection: sqlite3.Connection) -> tuple[tuple[int, int], ...]:
    return tuple(
        (int(row[0]), int(row[1]))
        for row in connection.execute(
            "SELECT id, is_current FROM projection_generations ORDER BY id"
        )
    )


def _generation_exists(connection: sqlite3.Connection, generation_id: int) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM projection_generations WHERE id = ?", (generation_id,)
        ).fetchone()
        is not None
    )


def _generation_link_count(connection: sqlite3.Connection, generation_id: int) -> int:
    return int(
        connection.execute(
            "SELECT COUNT(*) FROM discord_projection_links WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()[0]
    )


def _projection_links(connection: sqlite3.Connection) -> tuple[tuple[object, ...], ...]:
    return tuple(
        tuple(row)
        for row in connection.execute("SELECT * FROM discord_projection_links ORDER BY id")
    )


def _require_identity(path: Path, *, expected_digest: str, expected_size: int, label: str) -> None:
    digest, size = _file_identity(path)
    if digest != expected_digest or size != expected_size:
        raise ProjectionGenerationActivationError(f"Certified {label} digest or size is stale.")


def _require_certified_source(connection: sqlite3.Connection, request: _ActivationRequest) -> None:
    evidence = request.evidence
    _require_identity(
        request.database_path,
        expected_digest=evidence.source_sha256,
        expected_size=evidence.source_size,
        label="source",
    )
    if _logical_identity(connection) != _logical_path_identity(evidence.backup_path):
        raise ProjectionGenerationActivationError("Certified source digest or size is stale.")


def _logical_path_identity(path: Path) -> tuple[str, int]:
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        connection.execute("BEGIN")
        return _logical_identity(connection)
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def _logical_identity(connection: sqlite3.Connection) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    for statement in connection.iterdump():
        encoded = (statement + "\n").encode("utf-8")
        digest.update(encoded)
        size += len(encoded)
    return digest.hexdigest(), size


def _file_identity(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
