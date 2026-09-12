"""Explicit relocation of MOA's former checkout-local SQLite database."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import file_digest, sha256
from pathlib import Path
from uuid import uuid4

from moa.database.migrations import CATALOG_MIGRATIONS, CATALOG_TABLES
from moa.models.data_health import DataHealthFinding
from moa.repositories.data_health_repository import DataHealthRepository
from moa.services.listener_process_guard import (
    ListenerProcessGuard,
    ListenerProcessGuardError,
)
from moa.services.retained_source_reprojection_preflight_service import (
    RetainedSourceReprojectionPreflightService,
)


_INCOMPLETE_RETIREMENT_MARKER_NAME = ".moa-relocation-incomplete"
_RETIREMENT_TOMBSTONE_MARKER_NAME = ".moa-relocated"
_RETIREMENT_TOMBSTONE_FORMAT = "moa-legacy-database-retirement"
_RETIREMENT_TOMBSTONE_VERSION = 1
_CERTIFICATION_FORMAT = "moa-database-relocation-identity-certification"
_CERTIFICATION_VERSION = 1
_CERTIFICATION_HASH_SCOPE = "canonical-json-without-certification-record-sha256"
_JOURNAL_MODE_PREPARATION_FORMAT = (
    "moa-database-relocation-journal-mode-preparation-evidence"
)
_JOURNAL_MODE_PREPARATION_VERSION = 1
_JOURNAL_MODE_PREPARATION_HASH_SCOPE = (
    "canonical-json-without-evidence-record-sha256"
)
_WAL_RECOVERY_AUTHORIZATION_FORMAT = "moa-database-wal-recovery-authorization"
_WAL_RECOVERY_AUTHORIZATION_VERSION = 1
_WAL_RECOVERY_ACTION = "AUTHORIZE_ONE_DATABASE_WAL_RECOVERY_ATTEMPT"
_WAL_RECOVERY_MAX_ATTEMPTS = 1
_WAL_RECOVERY_AUTHORIZATION_HASH_SCOPE = (
    "canonical-json-without-authorization-record-sha256"
)
_WAL_RECOVERY_EVIDENCE_FORMAT = "moa-database-wal-recovery-evidence"
_WAL_RECOVERY_EVIDENCE_VERSION = 1
_WAL_RECOVERY_EVIDENCE_HASH_SCOPE = "canonical-json-without-evidence-record-sha256"
_WAL_RECOVERY_COMPLETED = "RECOVERY_COMPLETED"
_WAL_RECOVERY_NO_ACTION = "NO_ACTION_ALREADY_NORMALIZED"
_SOURCE_QUIESCENCE_BUSY_TIMEOUT_MS = 5000
_PERSISTENT_ROLLBACK_JOURNAL_MODES = frozenset(
    {"delete", "truncate", "persist"}
)


class LegacyDatabaseRelocationError(ValueError):
    """Base error for legacy database authority and relocation failures."""


class LegacyDatabaseRelocationRequiredError(LegacyDatabaseRelocationError):
    """Raised when the former checkout database must be explicitly relocated."""


class LegacyDatabaseAuthorityConflictError(LegacyDatabaseRelocationError):
    """Raised when both former and new live database paths exist."""


class DatabaseRelocationError(LegacyDatabaseRelocationError):
    """Raised when an explicit database relocation cannot complete safely."""


class DatabaseRelocationJournalModePreparationError(DatabaseRelocationError):
    """Raised after a WAL transition attempt with preserved failure evidence."""

    def __init__(self, message: str, evidence: dict[str, object]) -> None:
        super().__init__(message)
        self.evidence = evidence

    def to_json(self) -> str:
        """Serialize the canonical evidence preserved after a transition attempt."""
        return _canonical_json(self.evidence)


@dataclass(frozen=True, slots=True)
class DatabaseRelocationResult:
    """Paths established by one completed relocation."""

    source: Path
    target: Path
    source_archive: Path


@dataclass(frozen=True, slots=True)
class DatabaseRelocationAuthorizationIdentity:
    """Exact normalized physical and semantic identity authorized for relocation."""

    normalized_sha256: str
    normalized_size: int
    migration_identity: tuple[tuple[int, str], ...]
    generation_inventory: tuple[tuple[int, bool], ...]
    source_event_count: int
    generation_1_projection_link_count: int
    projection_gaps: tuple[DataHealthFinding, ...]
    retained_source_preflight_fingerprint: str


@dataclass(frozen=True, slots=True)
class DatabaseFileObservation:
    """Informational physical state observed before source normalization."""

    present: bool
    size: int | None
    sha256: str | None


@dataclass(frozen=True, slots=True)
class DatabaseSidecarObservation:
    """Non-semantic sidecar presence and size observation."""

    present: bool
    size: int | None


@dataclass(frozen=True, slots=True)
class DatabaseWalRecoveryAuthority:
    """Exact, one-attempt authority for recovery-only WAL normalization."""

    authorization_format: str
    authorization_version: int
    action: str
    authorization_id: str
    source: Path
    intended_destination: Path
    expected_moa_checkpoint: str
    expected_main: DatabaseFileObservation
    expected_wal: DatabaseFileObservation
    expected_shm: DatabaseSidecarObservation
    expected_journal_mode: str
    listener_known_writers_stopped_attested: bool
    max_attempts: int
    authorization_record_sha256: str

    def as_dict(self) -> dict[str, object]:
        """Return the canonical bound authority including its supplied digest."""
        document = _wal_recovery_authority_document(self)
        document["authorization_record_sha256"] = self.authorization_record_sha256
        return document

    def to_json(self) -> str:
        """Serialize the canonical bound recovery authority."""
        return _canonical_json(self.as_dict())


@dataclass(frozen=True, slots=True)
class DatabaseWalRecoveryEvidence:
    """Canonical evidence for one recovery-only WAL normalization attempt."""

    status: str
    run_id: str
    started_at: str
    completed_at: str
    authority: DatabaseWalRecoveryAuthority
    sqlite_version: str
    listener_guard_acquired_at: str
    listener_guard_released_at: str
    sqlite_exclusive_acquired_at: str
    sqlite_exclusive_released_at: str
    pre_lock_main: DatabaseFileObservation
    pre_lock_wal: DatabaseFileObservation
    pre_lock_shm: DatabaseSidecarObservation
    locked_main: DatabaseFileObservation
    locked_wal: DatabaseFileObservation
    locked_shm: DatabaseSidecarObservation
    pre_normalization_journal_mode: str
    pre_semantic_identity: DatabaseRelocationAuthorizationIdentity
    pre_integrity_check: tuple[str, ...]
    pre_foreign_key_check: tuple[tuple[object, ...], ...]
    truncate_checkpoint: tuple[int, int, int] | None
    post_semantic_identity: DatabaseRelocationAuthorizationIdentity
    post_integrity_check: tuple[str, ...]
    post_foreign_key_check: tuple[tuple[object, ...], ...]
    normalized_main: DatabaseFileObservation
    post_normalization_wal: DatabaseFileObservation
    post_normalization_shm: DatabaseSidecarObservation
    final_post_close_wal: DatabaseFileObservation
    final_post_close_shm: DatabaseSidecarObservation
    evidence_record_sha256: str

    def as_dict(self) -> dict[str, object]:
        """Return stable recovery evidence including its self-verifying digest."""
        document = _wal_recovery_evidence_document(
            status=self.status,
            run_id=self.run_id,
            started_at=self.started_at,
            completed_at=self.completed_at,
            authority=self.authority,
            sqlite_version=self.sqlite_version,
            listener_guard_acquired_at=self.listener_guard_acquired_at,
            listener_guard_released_at=self.listener_guard_released_at,
            sqlite_exclusive_acquired_at=self.sqlite_exclusive_acquired_at,
            sqlite_exclusive_released_at=self.sqlite_exclusive_released_at,
            pre_lock_main=self.pre_lock_main,
            pre_lock_wal=self.pre_lock_wal,
            pre_lock_shm=self.pre_lock_shm,
            locked_main=self.locked_main,
            locked_wal=self.locked_wal,
            locked_shm=self.locked_shm,
            pre_normalization_journal_mode=self.pre_normalization_journal_mode,
            pre_semantic_identity=self.pre_semantic_identity,
            pre_integrity_check=self.pre_integrity_check,
            pre_foreign_key_check=self.pre_foreign_key_check,
            truncate_checkpoint=self.truncate_checkpoint,
            post_semantic_identity=self.post_semantic_identity,
            post_integrity_check=self.post_integrity_check,
            post_foreign_key_check=self.post_foreign_key_check,
            normalized_main=self.normalized_main,
            post_normalization_wal=self.post_normalization_wal,
            post_normalization_shm=self.post_normalization_shm,
            final_post_close_wal=self.final_post_close_wal,
            final_post_close_shm=self.final_post_close_shm,
        )
        document["evidence_record_sha256"] = self.evidence_record_sha256
        return document

    def to_json(self) -> str:
        """Serialize canonical machine-readable recovery evidence."""
        return _canonical_json(self.as_dict())


@dataclass(frozen=True, slots=True)
class DatabaseRelocationJournalModePreparationAuthority:
    """Exact pre-transition representation authorized for WAL preparation."""

    source: Path
    intended_destination: Path
    expected_moa_checkpoint: str
    expected_main: DatabaseFileObservation
    expected_journal_mode: str
    expected_journal: DatabaseFileObservation
    expected_wal: DatabaseFileObservation
    expected_shm: DatabaseFileObservation
    listener_known_writers_stopped_attested: bool
    authorization_id: str


@dataclass(frozen=True, slots=True)
class DatabaseRelocationJournalModePreparationEvidence:
    """Canonical evidence for one explicit non-WAL to WAL transition."""

    run_id: str
    started_at: str
    completed_at: str
    authority: DatabaseRelocationJournalModePreparationAuthority
    sqlite_version: str
    exclusion_acquired_at: str
    exclusion_released_at: str
    pre_transition_main: DatabaseFileObservation
    pre_transition_journal_mode: str
    pre_transition_journal: DatabaseFileObservation
    pre_transition_wal: DatabaseFileObservation
    pre_transition_shm: DatabaseFileObservation
    pre_semantic_identity: DatabaseRelocationAuthorizationIdentity
    post_semantic_identity: DatabaseRelocationAuthorizationIdentity
    pre_integrity_check: tuple[str, ...]
    pre_foreign_key_check: tuple[tuple[object, ...], ...]
    post_integrity_check: tuple[str, ...]
    post_foreign_key_check: tuple[tuple[object, ...], ...]
    returned_journal_mode: str
    header_write_version: int
    header_read_version: int
    post_transition_main: DatabaseFileObservation
    post_transition_journal: DatabaseFileObservation
    post_transition_wal: DatabaseFileObservation
    post_transition_shm: DatabaseFileObservation
    final_post_close_journal: DatabaseFileObservation
    final_post_close_wal: DatabaseFileObservation
    final_post_close_shm: DatabaseFileObservation
    evidence_record_sha256: str

    def as_dict(self) -> dict[str, object]:
        """Return stable preparation evidence including its self-verifying digest."""
        document = _journal_mode_preparation_document(
            run_id=self.run_id,
            started_at=self.started_at,
            completed_at=self.completed_at,
            authority=self.authority,
            sqlite_version=self.sqlite_version,
            exclusion_acquired_at=self.exclusion_acquired_at,
            exclusion_released_at=self.exclusion_released_at,
            pre_transition_main=self.pre_transition_main,
            pre_transition_journal_mode=self.pre_transition_journal_mode,
            pre_transition_journal=self.pre_transition_journal,
            pre_transition_wal=self.pre_transition_wal,
            pre_transition_shm=self.pre_transition_shm,
            pre_semantic_identity=self.pre_semantic_identity,
            post_semantic_identity=self.post_semantic_identity,
            pre_integrity_check=self.pre_integrity_check,
            pre_foreign_key_check=self.pre_foreign_key_check,
            post_integrity_check=self.post_integrity_check,
            post_foreign_key_check=self.post_foreign_key_check,
            returned_journal_mode=self.returned_journal_mode,
            header_write_version=self.header_write_version,
            header_read_version=self.header_read_version,
            post_transition_main=self.post_transition_main,
            post_transition_journal=self.post_transition_journal,
            post_transition_wal=self.post_transition_wal,
            post_transition_shm=self.post_transition_shm,
            final_post_close_journal=self.final_post_close_journal,
            final_post_close_wal=self.final_post_close_wal,
            final_post_close_shm=self.final_post_close_shm,
        )
        document["evidence_record_sha256"] = self.evidence_record_sha256
        return document

    def to_json(self) -> str:
        """Serialize canonical machine-readable preparation evidence."""
        return _canonical_json(self.as_dict())


@dataclass(frozen=True, slots=True)
class DatabaseRelocationIdentityCertification:
    """Versioned evidence for one certification-only normalization epoch."""

    run_id: str
    certified_at: str
    source: Path
    destination: Path
    moa_checkpoint: str
    identity: DatabaseRelocationAuthorizationIdentity
    truncate_checkpoint: tuple[int, int, int]
    passive_checkpoint: tuple[int, int, int]
    exclusion_acquired_at: str
    exclusion_released_at: str
    integrity_check: tuple[str, ...]
    foreign_key_check: tuple[tuple[object, ...], ...]
    listener_known_writers_stopped_attested: bool
    pre_normalization_main: DatabaseFileObservation
    pre_normalization_wal: DatabaseFileObservation
    pre_normalization_shm: DatabaseFileObservation
    certification_record_sha256: str

    def as_dict(self) -> dict[str, object]:
        """Return the stable JSON document, including its self-verifying digest."""
        document = _certification_document(
            run_id=self.run_id,
            certified_at=self.certified_at,
            source=self.source,
            destination=self.destination,
            moa_checkpoint=self.moa_checkpoint,
            identity=self.identity,
            truncate_checkpoint=self.truncate_checkpoint,
            passive_checkpoint=self.passive_checkpoint,
            exclusion_acquired_at=self.exclusion_acquired_at,
            exclusion_released_at=self.exclusion_released_at,
            integrity_check=self.integrity_check,
            foreign_key_check=self.foreign_key_check,
            listener_known_writers_stopped_attested=(
                self.listener_known_writers_stopped_attested
            ),
            pre_normalization_main=self.pre_normalization_main,
            pre_normalization_wal=self.pre_normalization_wal,
            pre_normalization_shm=self.pre_normalization_shm,
        )
        document["certification_record_sha256"] = self.certification_record_sha256
        return document

    def to_json(self) -> str:
        """Serialize canonical machine-readable certification JSON."""
        return _canonical_json(self.as_dict())


@dataclass(frozen=True, slots=True)
class _DatabaseFingerprint:
    migration_rows: tuple[tuple[int, str], ...]
    table_counts: tuple[tuple[str, int], ...]
    representative_import_event: tuple[object, ...] | None


@dataclass(slots=True)
class _NormalizedRelocationAuthority:
    connection: sqlite3.Connection | None
    identity: DatabaseRelocationAuthorizationIdentity
    source_fingerprint: _DatabaseFingerprint
    truncate_checkpoint: tuple[int, int, int]
    passive_checkpoint: tuple[int, int, int]
    exclusion_acquired_at: str
    certified_at: str
    integrity_check: tuple[str, ...]
    foreign_key_check: tuple[tuple[object, ...], ...]
    pre_normalization_main: DatabaseFileObservation
    pre_normalization_wal: DatabaseFileObservation
    pre_normalization_shm: DatabaseFileObservation
    exclusion_released_at: str | None = None

    def release(self, *, primary_error: BaseException | None = None) -> None:
        connection, self.connection = self.connection, None
        if connection is None:
            return
        _release_source_quiescence(connection, primary_error=primary_error)
        self.exclusion_released_at = _utc_timestamp()


@dataclass(frozen=True, slots=True)
class _RetirementTombstone:
    target: Path
    archive: Path


def _source_file_path() -> Path:
    return Path(__file__).resolve()


def _verified_checkout_root() -> Path | None:
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
    return checkout_root


def verified_legacy_database_path() -> Path | None:
    """Return the one verified current-checkout legacy path, if available."""
    checkout_root = _verified_checkout_root()
    if checkout_root is None:
        return None
    return checkout_root / "data" / "database" / "moa.db"


def _verified_moa_checkout_checkpoint() -> str:
    checkout_root = _verified_checkout_root()
    if checkout_root is None:
        raise DatabaseRelocationError(
            "RELOCATION_CERTIFICATION_CHECKPOINT_MISMATCH: the running MOA source "
            "is not in a verified checkout."
        )
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={checkout_root}",
                "-C",
                str(checkout_root),
                "rev-parse",
                "HEAD",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise DatabaseRelocationError(
            "RELOCATION_CERTIFICATION_CHECKPOINT_MISMATCH: the running MOA checkout "
            "checkpoint could not be verified."
        ) from error
    checkpoint = result.stdout.strip()
    if not _is_git_checkpoint(checkpoint):
        raise DatabaseRelocationError(
            "RELOCATION_CERTIFICATION_CHECKPOINT_MISMATCH: the running MOA checkout "
            "returned an invalid checkpoint."
        )
    return checkpoint


def ensure_default_database_authority(target: Path) -> None:
    """Fail closed when a verified checkout database remains authoritative."""
    legacy_path = verified_legacy_database_path()
    if legacy_path is None:
        return
    incomplete_retirements = _incomplete_retirement_markers(legacy_path)
    if incomplete_retirements:
        rendered = ", ".join(str(path.parent) for path in incomplete_retirements)
        raise LegacyDatabaseAuthorityConflictError(
            "An incomplete legacy database retirement requires explicit recovery. "
            f"Legacy: {legacy_path.resolve(strict=False)}. Incomplete archive(s): {rendered}. "
            f"New default: {Path(target).resolve(strict=False)}. MOA cannot select an authority."
        )
    resolved_legacy = legacy_path.resolve(strict=False)
    resolved_target = Path(target).resolve(strict=False)
    if legacy_path.is_dir() or legacy_path.is_symlink():
        try:
            _validate_retirement_tombstone(legacy_path, resolved_target)
        except DatabaseRelocationError as error:
            raise LegacyDatabaseAuthorityConflictError(
                "The legacy database pathname is occupied by an invalid or unrecognized "
                f"retirement obstruction. Legacy: {resolved_legacy}. New default: "
                f"{resolved_target}. Resolve this state explicitly."
            ) from error
        if not resolved_target.is_file():
            raise LegacyDatabaseAuthorityConflictError(
                "A valid legacy database retirement tombstone exists, but its relocated "
                f"target is missing or is not a file. Legacy: {resolved_legacy}. "
                f"New default: {resolved_target}. Restore or recover the relocated database "
                "before starting MOA."
            )
        return
    if not legacy_path.exists():
        return
    if not legacy_path.is_file():
        raise LegacyDatabaseAuthorityConflictError(
            "The legacy database pathname contains an unexpected non-file obstruction. "
            f"Legacy: {resolved_legacy}. New default: {resolved_target}. Resolve this state "
            "explicitly."
        )
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


def relocate_database(
    source: Path,
    target: Path,
    *,
    _test_hook: Callable[[str], None] | None = None,
) -> DatabaseRelocationResult:
    """Relocate one explicit MOA database with backup and fail-closed retirement."""
    return _relocate_database(source, target, expected_identity=None, _test_hook=_test_hook)


def relocate_database_with_authorization(
    source: Path,
    target: Path,
    expected_identity: DatabaseRelocationAuthorizationIdentity,
    *,
    _test_hook: Callable[[str], None] | None = None,
) -> DatabaseRelocationResult:
    """Relocate only when the normalized source identity matches trusted expectations."""
    _validate_expected_authorization_identity(expected_identity)
    return _relocate_database(
        source,
        target,
        expected_identity=expected_identity,
        _test_hook=_test_hook,
    )


def recover_database_wal(
    authority: DatabaseWalRecoveryAuthority,
    *,
    _test_hook: Callable[[str], None] | None = None,
) -> DatabaseWalRecoveryEvidence:
    """Normalize one exactly authorized WAL source without relocating it."""
    _validate_wal_recovery_authority(authority)
    source_path, destination_path = _resolve_wal_recovery_paths(
        authority.source, authority.intended_destination
    )
    if source_path != authority.source or destination_path != authority.intended_destination:
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_AUTHORIZATION_MISMATCH: source and intended "
            "destination authority must contain canonical paths."
        )
    _require_expected_moa_checkpoint(authority.expected_moa_checkpoint)
    if not authority.listener_known_writers_stopped_attested:
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_BLOCKER: explicit known-writer-stopped attestation "
            "is required."
        )

    pre_lock_main = _observe_database_file(source_path)
    pre_lock_wal = _observe_database_file(Path(f"{source_path}-wal"))
    pre_lock_shm = _observe_sidecar_presence(Path(f"{source_path}-shm"))
    _require_matching_recovery_file_observation(
        authority.expected_main, pre_lock_main, label="main file"
    )
    _require_matching_recovery_file_observation(
        authority.expected_wal, pre_lock_wal, label="WAL sidecar"
    )
    _require_matching_recovery_sidecar_observation(
        authority.expected_shm, pre_lock_shm, label="SHM sidecar"
    )

    run_id = str(uuid4())
    started_at = _utc_timestamp()
    sqlite_version = sqlite3.sqlite_version
    guard = ListenerProcessGuard(source_path)
    try:
        guard.acquire()
    except ListenerProcessGuardError as error:
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_BLOCKER: the source listener guard could not be "
            "acquired; stop the guard-aware listener."
        ) from error
    listener_guard_acquired_at = _utc_timestamp()
    _notify_test_hook(_test_hook, "LISTENER_GUARD_ACQUIRED")

    connection: sqlite3.Connection | None = None
    primary_error: BaseException | None = None
    sqlite_exclusive_acquired_at: str | None = None
    sqlite_exclusive_released_at: str | None = None
    listener_guard_released_at: str | None = None
    locked_main: DatabaseFileObservation | None = None
    locked_wal: DatabaseFileObservation | None = None
    locked_shm: DatabaseSidecarObservation | None = None
    pre_journal_mode: str | None = None
    pre_identity: DatabaseRelocationAuthorizationIdentity | None = None
    pre_integrity: tuple[str, ...] | None = None
    pre_foreign_keys: tuple[tuple[object, ...], ...] | None = None
    truncate_checkpoint: tuple[int, int, int] | None = None
    post_identity: DatabaseRelocationAuthorizationIdentity | None = None
    post_integrity: tuple[str, ...] | None = None
    post_foreign_keys: tuple[tuple[object, ...], ...] | None = None
    normalized_main: DatabaseFileObservation | None = None
    post_wal: DatabaseFileObservation | None = None
    post_shm: DatabaseSidecarObservation | None = None
    final_wal: DatabaseFileObservation | None = None
    final_shm: DatabaseSidecarObservation | None = None
    status: str | None = None

    try:
        guarded_main = _observe_database_file(source_path)
        guarded_wal = _observe_database_file(Path(f"{source_path}-wal"))
        guarded_shm = _observe_sidecar_presence(Path(f"{source_path}-shm"))
        _require_matching_recovery_file_observation(
            authority.expected_main, guarded_main, label="guarded main file"
        )
        _require_matching_recovery_file_observation(
            authority.expected_wal, guarded_wal, label="guarded WAL sidecar"
        )
        _require_matching_recovery_sidecar_observation(
            authority.expected_shm, guarded_shm, label="guarded SHM sidecar"
        )

        connection = _open_neutral_source_connection(source_path)
        pre_journal_mode = _query_exact_journal_mode(connection)
        if pre_journal_mode != authority.expected_journal_mode:
            raise DatabaseRelocationError(
                "DATABASE_WAL_RECOVERY_AUTHORIZATION_MISMATCH: observed journal mode "
                "differs from the explicit recovery authority."
            )
        if pre_journal_mode != "wal":
            raise DatabaseRelocationError(
                "DATABASE_WAL_RECOVERY_BLOCKER: source journal mode must already be WAL."
            )
        locking_mode = _query_single_pragma_value(
            connection, "PRAGMA main.locking_mode=EXCLUSIVE"
        )
        if locking_mode.casefold() != "exclusive":
            raise DatabaseRelocationError(
                "DATABASE_WAL_RECOVERY_BLOCKER: SQLite did not establish exclusive "
                "connection locking mode."
            )
        try:
            connection.execute("BEGIN EXCLUSIVE")
            connection.rollback()
        except sqlite3.Error as error:
            raise DatabaseRelocationError(
                "DATABASE_WAL_RECOVERY_BLOCKER: could not acquire bounded SQLite "
                "exclusive source ownership; stop every source reader and writer."
            ) from error
        sqlite_exclusive_acquired_at = _utc_timestamp()
        _notify_test_hook(_test_hook, "SQLITE_EXCLUSIVE_ACQUIRED")

        locked_main = _observe_database_file(source_path)
        locked_wal = _observe_database_file(Path(f"{source_path}-wal"))
        locked_shm = _observe_sidecar_presence(Path(f"{source_path}-shm"))
        _require_matching_recovery_file_observation(
            authority.expected_main, locked_main, label="locked main file"
        )
        _require_equivalent_locked_wal(authority.expected_wal, locked_wal)
        if _query_exact_journal_mode(connection) != "wal":
            raise DatabaseRelocationError(
                "DATABASE_WAL_RECOVERY_BLOCKER: WAL journal mode was not retained under "
                "exclusive ownership."
            )

        pre_identity, pre_integrity, pre_foreign_keys = (
            _compute_recovery_semantic_validation(connection, source_path)
        )
        _notify_test_hook(_test_hook, "PRE_SEMANTIC_VALIDATED")

        already_normalized = not locked_wal.present or locked_wal.size == 0
        if already_normalized:
            status = _WAL_RECOVERY_NO_ACTION
        else:
            truncate_checkpoint = _checkpoint_wal_truncate_on_owned_connection(
                connection
            )
            _notify_test_hook(_test_hook, "TRUNCATE_CHECKPOINT_COMPLETED")
            status = _WAL_RECOVERY_COMPLETED

        post_identity, post_integrity, post_foreign_keys = (
            _compute_recovery_semantic_validation(connection, source_path)
        )
        _require_unchanged_wal_recovery_semantics(pre_identity, post_identity)
        normalized_main = _observe_database_file(source_path)
        post_wal = _observe_database_file(Path(f"{source_path}-wal"))
        post_shm = _observe_sidecar_presence(Path(f"{source_path}-shm"))
        _notify_test_hook(_test_hook, "RECOVERY_VALIDATED")
    except BaseException as error:
        primary_error = error
    finally:
        if connection is not None:
            try:
                if connection.in_transaction:
                    connection.rollback()
            except BaseException as cleanup_error:
                if primary_error is None:
                    primary_error = cleanup_error
                else:
                    primary_error.add_note(
                        f"Recovery rollback cleanup also failed: {cleanup_error}"
                    )
            try:
                connection.close()
            except BaseException as cleanup_error:
                if primary_error is None:
                    primary_error = cleanup_error
                else:
                    primary_error.add_note(
                        f"Recovery connection cleanup also failed: {cleanup_error}"
                    )
            else:
                if sqlite_exclusive_acquired_at is not None:
                    sqlite_exclusive_released_at = _utc_timestamp()
        try:
            final_wal = _observe_database_file(Path(f"{source_path}-wal"))
            final_shm = _observe_sidecar_presence(Path(f"{source_path}-shm"))
        except BaseException as cleanup_error:
            if primary_error is None:
                primary_error = cleanup_error
            else:
                primary_error.add_note(
                    f"Final post-close recovery observation also failed: {cleanup_error}"
                )
        try:
            guard.release()
        except BaseException as cleanup_error:
            if primary_error is None:
                primary_error = cleanup_error
            else:
                primary_error.add_note(
                    f"Recovery listener-guard cleanup also failed: {cleanup_error}"
                )
        else:
            listener_guard_released_at = _utc_timestamp()

    if primary_error is not None:
        if isinstance(primary_error, DatabaseRelocationError):
            raise primary_error
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_FAILURE: recovery failed closed."
        ) from primary_error
    if (
        status is None
        or sqlite_exclusive_acquired_at is None
        or sqlite_exclusive_released_at is None
        or listener_guard_released_at is None
        or locked_main is None
        or locked_wal is None
        or locked_shm is None
        or pre_journal_mode is None
        or pre_identity is None
        or pre_integrity is None
        or pre_foreign_keys is None
        or post_identity is None
        or post_integrity is None
        or post_foreign_keys is None
        or normalized_main is None
        or post_wal is None
        or post_shm is None
        or final_wal is None
        or final_shm is None
    ):
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_FAILURE: completed evidence is internally incomplete."
        )

    completed_at = _utc_timestamp()
    document = _wal_recovery_evidence_document(
        status=status,
        run_id=run_id,
        started_at=started_at,
        completed_at=completed_at,
        authority=authority,
        sqlite_version=sqlite_version,
        listener_guard_acquired_at=listener_guard_acquired_at,
        listener_guard_released_at=listener_guard_released_at,
        sqlite_exclusive_acquired_at=sqlite_exclusive_acquired_at,
        sqlite_exclusive_released_at=sqlite_exclusive_released_at,
        pre_lock_main=pre_lock_main,
        pre_lock_wal=pre_lock_wal,
        pre_lock_shm=pre_lock_shm,
        locked_main=locked_main,
        locked_wal=locked_wal,
        locked_shm=locked_shm,
        pre_normalization_journal_mode=pre_journal_mode,
        pre_semantic_identity=pre_identity,
        pre_integrity_check=pre_integrity,
        pre_foreign_key_check=pre_foreign_keys,
        truncate_checkpoint=truncate_checkpoint,
        post_semantic_identity=post_identity,
        post_integrity_check=post_integrity,
        post_foreign_key_check=post_foreign_keys,
        normalized_main=normalized_main,
        post_normalization_wal=post_wal,
        post_normalization_shm=post_shm,
        final_post_close_wal=final_wal,
        final_post_close_shm=final_shm,
    )
    evidence_record_sha256 = sha256(
        _canonical_json(document).encode("ascii")
    ).hexdigest()
    return DatabaseWalRecoveryEvidence(
        status=status,
        run_id=run_id,
        started_at=started_at,
        completed_at=completed_at,
        authority=authority,
        sqlite_version=sqlite_version,
        listener_guard_acquired_at=listener_guard_acquired_at,
        listener_guard_released_at=listener_guard_released_at,
        sqlite_exclusive_acquired_at=sqlite_exclusive_acquired_at,
        sqlite_exclusive_released_at=sqlite_exclusive_released_at,
        pre_lock_main=pre_lock_main,
        pre_lock_wal=pre_lock_wal,
        pre_lock_shm=pre_lock_shm,
        locked_main=locked_main,
        locked_wal=locked_wal,
        locked_shm=locked_shm,
        pre_normalization_journal_mode=pre_journal_mode,
        pre_semantic_identity=pre_identity,
        pre_integrity_check=pre_integrity,
        pre_foreign_key_check=pre_foreign_keys,
        truncate_checkpoint=truncate_checkpoint,
        post_semantic_identity=post_identity,
        post_integrity_check=post_integrity,
        post_foreign_key_check=post_foreign_keys,
        normalized_main=normalized_main,
        post_normalization_wal=post_wal,
        post_normalization_shm=post_shm,
        final_post_close_wal=final_wal,
        final_post_close_shm=final_shm,
        evidence_record_sha256=evidence_record_sha256,
    )


def prepare_database_relocation_journal_mode(
    authority: DatabaseRelocationJournalModePreparationAuthority,
) -> DatabaseRelocationJournalModePreparationEvidence:
    """Change one exactly authorized rollback-mode source to persistent WAL mode."""
    _validate_journal_mode_preparation_authority(authority)
    source_path, destination_path, _tombstone_required = _resolve_relocation_paths(
        authority.source, authority.intended_destination
    )
    if source_path != authority.source or destination_path != authority.intended_destination:
        raise DatabaseRelocationError(
            "RELOCATION_JOURNAL_MODE_PREPARATION_MISMATCH: source and destination "
            "authority must contain canonical paths."
        )
    _require_clean_journal_mode_preparation_context(source_path, destination_path)
    _require_expected_moa_checkpoint(authority.expected_moa_checkpoint)
    if not authority.listener_known_writers_stopped_attested:
        raise DatabaseRelocationError(
            "RELOCATION_JOURNAL_MODE_PREPARATION_BLOCKER: explicit known-writer-stopped "
            "attestation is required."
        )

    run_id = str(uuid4())
    started_at = _utc_timestamp()
    sqlite_version = sqlite3.sqlite_version
    guard = ListenerProcessGuard(source_path)
    try:
        guard.acquire()
    except ListenerProcessGuardError as error:
        raise DatabaseRelocationError(
            "RELOCATION_JOURNAL_MODE_PREPARATION_BLOCKER: the source listener guard "
            "could not be acquired; stop the guard-aware listener."
        ) from error

    connection: sqlite3.Connection | None = None
    primary_error: BaseException | None = None
    transition_attempted = False
    returned_journal_mode: str | None = None
    exclusion_acquired_at: str | None = None
    exclusion_released_at: str | None = None
    pre_main: DatabaseFileObservation | None = None
    pre_journal: DatabaseFileObservation | None = None
    pre_wal: DatabaseFileObservation | None = None
    pre_shm: DatabaseFileObservation | None = None
    pre_journal_mode: str | None = None
    pre_semantic_identity: DatabaseRelocationAuthorizationIdentity | None = None
    post_semantic_identity: DatabaseRelocationAuthorizationIdentity | None = None
    pre_integrity_check: tuple[str, ...] | None = None
    pre_foreign_key_check: tuple[tuple[object, ...], ...] | None = None
    post_integrity_check: tuple[str, ...] | None = None
    post_foreign_key_check: tuple[tuple[object, ...], ...] | None = None
    header_write_version: int | None = None
    header_read_version: int | None = None
    post_main: DatabaseFileObservation | None = None
    post_journal: DatabaseFileObservation | None = None
    post_wal: DatabaseFileObservation | None = None
    post_shm: DatabaseFileObservation | None = None
    final_journal: DatabaseFileObservation | None = None
    final_wal: DatabaseFileObservation | None = None
    final_shm: DatabaseFileObservation | None = None
    guard_released = False

    try:
        pre_main, pre_journal, pre_wal, pre_shm = _observe_database_representation(
            source_path
        )
        _require_matching_file_observation(
            authority.expected_main, pre_main, label="main file"
        )
        _require_matching_file_observation(
            authority.expected_journal, pre_journal, label="rollback journal"
        )
        _require_matching_file_observation(
            authority.expected_wal, pre_wal, label="WAL sidecar"
        )
        _require_matching_file_observation(
            authority.expected_shm, pre_shm, label="SHM sidecar"
        )

        connection = _open_neutral_source_connection(source_path)
        pre_journal_mode = _query_exact_journal_mode(connection)
        _require_matching_journal_mode(
            authority.expected_journal_mode, pre_journal_mode
        )
        locking_mode = _query_single_pragma_value(
            connection, "PRAGMA main.locking_mode=EXCLUSIVE"
        )
        if locking_mode.casefold() != "exclusive":
            raise DatabaseRelocationError(
                "RELOCATION_JOURNAL_MODE_PREPARATION_BLOCKER: SQLite did not establish "
                "exclusive connection locking mode."
            )
        try:
            connection.execute("BEGIN EXCLUSIVE")
            connection.rollback()
        except sqlite3.Error as error:
            raise DatabaseRelocationError(
                "RELOCATION_JOURNAL_MODE_PREPARATION_BLOCKER: could not acquire bounded "
                "SQLite exclusive source ownership; stop every source writer."
            ) from error
        exclusion_acquired_at = _utc_timestamp()

        locked_main, locked_journal, locked_wal, locked_shm = (
            _observe_database_representation(source_path)
        )
        locked_journal_mode = _query_exact_journal_mode(connection)
        _require_matching_file_observation(
            authority.expected_main, locked_main, label="main file under exclusion"
        )
        _require_matching_file_observation(
            authority.expected_journal,
            locked_journal,
            label="rollback journal under exclusion",
        )
        _require_matching_file_observation(
            authority.expected_wal, locked_wal, label="WAL sidecar under exclusion"
        )
        _require_matching_file_observation(
            authority.expected_shm, locked_shm, label="SHM sidecar under exclusion"
        )
        _require_matching_journal_mode(
            authority.expected_journal_mode, locked_journal_mode
        )

        connection.execute("BEGIN IMMEDIATE")
        try:
            pre_semantic_identity = _compute_relocation_authorization_identity(
                connection, source_path
            )
            _fingerprint, pre_integrity_check, pre_foreign_key_check = (
                _validate_held_database_with_evidence(connection, source_path)
            )
        finally:
            connection.rollback()

        transition_attempted = True
        returned_journal_mode = _transition_journal_mode_to_wal(connection)
        if returned_journal_mode != "wal":
            raise DatabaseRelocationError(
                "RELOCATION_JOURNAL_MODE_PREPARATION_FAILURE: SQLite did not return WAL "
                "for the requested journal-mode transition."
            )
        if _query_exact_journal_mode(connection) != "wal":
            raise DatabaseRelocationError(
                "RELOCATION_JOURNAL_MODE_PREPARATION_FAILURE: SQLite did not retain WAL "
                "after the requested journal-mode transition."
            )
        header_write_version, header_read_version = _database_header_versions(
            source_path
        )
        if (header_write_version, header_read_version) != (2, 2):
            raise DatabaseRelocationError(
                "RELOCATION_JOURNAL_MODE_PREPARATION_FAILURE: the SQLite main header "
                "does not encode WAL read/write versions."
            )
        post_main, post_journal, post_wal, post_shm = (
            _observe_database_representation(source_path)
        )

        connection.execute("BEGIN IMMEDIATE")
        try:
            post_semantic_identity = _compute_relocation_authorization_identity(
                connection, source_path
            )
            _fingerprint, post_integrity_check, post_foreign_key_check = (
                _validate_held_database_with_evidence(connection, source_path)
            )
        finally:
            connection.rollback()
        _require_unchanged_journal_preparation_semantics(
            pre_semantic_identity, post_semantic_identity
        )
    except BaseException as error:
        primary_error = error
    finally:
        if connection is not None:
            if connection.in_transaction:
                try:
                    connection.rollback()
                except BaseException as cleanup_error:
                    if primary_error is None:
                        primary_error = cleanup_error
                    else:
                        primary_error.add_note(
                            f"Preparation rollback cleanup also failed: {cleanup_error}"
                        )
            try:
                connection.close()
            except BaseException as cleanup_error:
                if primary_error is None:
                    primary_error = cleanup_error
                else:
                    primary_error.add_note(
                        f"Preparation connection cleanup also failed: {cleanup_error}"
                    )
            else:
                exclusion_released_at = _utc_timestamp()
        try:
            final_journal, final_wal, final_shm = _observe_database_sidecars(
                source_path
            )
        except BaseException as cleanup_error:
            if primary_error is None:
                primary_error = cleanup_error
            else:
                primary_error.add_note(
                    f"Final post-close sidecar observation also failed: {cleanup_error}"
                )
        try:
            guard.release()
        except BaseException as cleanup_error:
            if primary_error is None:
                primary_error = cleanup_error
            else:
                primary_error.add_note(
                    f"Preparation listener-guard cleanup also failed: {cleanup_error}"
                )
        else:
            guard_released = True

    completed_at = _utc_timestamp()
    if primary_error is not None:
        if transition_attempted:
            assert pre_main is not None
            assert pre_journal_mode is not None
            assert pre_journal is not None
            assert pre_wal is not None
            assert pre_shm is not None
            assert pre_semantic_identity is not None
            assert pre_integrity_check is not None
            assert pre_foreign_key_check is not None
            failure_document = _journal_mode_preparation_failure_document(
                run_id=run_id,
                started_at=started_at,
                completed_at=completed_at,
                authority=authority,
                sqlite_version=sqlite_version,
                exclusion_acquired_at=exclusion_acquired_at,
                exclusion_released_at=exclusion_released_at,
                returned_journal_mode=returned_journal_mode,
                failure=primary_error,
                pre_transition_main=pre_main,
                pre_transition_journal_mode=pre_journal_mode,
                pre_transition_journal=pre_journal,
                pre_transition_wal=pre_wal,
                pre_transition_shm=pre_shm,
                pre_semantic_identity=pre_semantic_identity,
                post_semantic_identity=post_semantic_identity,
                pre_integrity_check=pre_integrity_check,
                pre_foreign_key_check=pre_foreign_key_check,
                post_integrity_check=post_integrity_check,
                post_foreign_key_check=post_foreign_key_check,
                listener_guard_released=guard_released,
                final_journal=final_journal,
                final_wal=final_wal,
                final_shm=final_shm,
            )
            failure_document["evidence_record_sha256"] = sha256(
                _canonical_json(failure_document).encode("ascii")
            ).hexdigest()
            raise DatabaseRelocationJournalModePreparationError(
                "RELOCATION_JOURNAL_MODE_PREPARATION_FAILURE: the transition was "
                "attempted and was not automatically reversed.",
                failure_document,
            ) from primary_error
        if isinstance(primary_error, DatabaseRelocationError):
            raise primary_error
        raise DatabaseRelocationError(
            "RELOCATION_JOURNAL_MODE_PREPARATION_BLOCKER: preparation failed before "
            "the WAL transition was attempted."
        ) from primary_error

    if (
        exclusion_acquired_at is None
        or exclusion_released_at is None
        or pre_main is None
        or pre_journal is None
        or pre_wal is None
        or pre_shm is None
        or pre_journal_mode is None
        or pre_semantic_identity is None
        or post_semantic_identity is None
        or pre_integrity_check is None
        or pre_foreign_key_check is None
        or post_integrity_check is None
        or post_foreign_key_check is None
        or returned_journal_mode is None
        or header_write_version is None
        or header_read_version is None
        or post_main is None
        or post_journal is None
        or post_wal is None
        or post_shm is None
        or final_journal is None
        or final_wal is None
        or final_shm is None
        or not guard_released
    ):
        raise DatabaseRelocationError(
            "RELOCATION_JOURNAL_MODE_PREPARATION_FAILURE: completed evidence is "
            "internally incomplete."
        )
    document = _journal_mode_preparation_document(
        run_id=run_id,
        started_at=started_at,
        completed_at=completed_at,
        authority=authority,
        sqlite_version=sqlite_version,
        exclusion_acquired_at=exclusion_acquired_at,
        exclusion_released_at=exclusion_released_at,
        pre_transition_main=pre_main,
        pre_transition_journal_mode=pre_journal_mode,
        pre_transition_journal=pre_journal,
        pre_transition_wal=pre_wal,
        pre_transition_shm=pre_shm,
        pre_semantic_identity=pre_semantic_identity,
        post_semantic_identity=post_semantic_identity,
        pre_integrity_check=pre_integrity_check,
        pre_foreign_key_check=pre_foreign_key_check,
        post_integrity_check=post_integrity_check,
        post_foreign_key_check=post_foreign_key_check,
        returned_journal_mode=returned_journal_mode,
        header_write_version=header_write_version,
        header_read_version=header_read_version,
        post_transition_main=post_main,
        post_transition_journal=post_journal,
        post_transition_wal=post_wal,
        post_transition_shm=post_shm,
        final_post_close_journal=final_journal,
        final_post_close_wal=final_wal,
        final_post_close_shm=final_shm,
    )
    evidence_record_sha256 = sha256(
        _canonical_json(document).encode("ascii")
    ).hexdigest()
    return DatabaseRelocationJournalModePreparationEvidence(
        run_id=run_id,
        started_at=started_at,
        completed_at=completed_at,
        authority=authority,
        sqlite_version=sqlite_version,
        exclusion_acquired_at=exclusion_acquired_at,
        exclusion_released_at=exclusion_released_at,
        pre_transition_main=pre_main,
        pre_transition_journal_mode=pre_journal_mode,
        pre_transition_journal=pre_journal,
        pre_transition_wal=pre_wal,
        pre_transition_shm=pre_shm,
        pre_semantic_identity=pre_semantic_identity,
        post_semantic_identity=post_semantic_identity,
        pre_integrity_check=pre_integrity_check,
        pre_foreign_key_check=pre_foreign_key_check,
        post_integrity_check=post_integrity_check,
        post_foreign_key_check=post_foreign_key_check,
        returned_journal_mode=returned_journal_mode,
        header_write_version=header_write_version,
        header_read_version=header_read_version,
        post_transition_main=post_main,
        post_transition_journal=post_journal,
        post_transition_wal=post_wal,
        post_transition_shm=post_shm,
        final_post_close_journal=final_journal,
        final_post_close_wal=final_wal,
        final_post_close_shm=final_shm,
        evidence_record_sha256=evidence_record_sha256,
    )


def certify_database_relocation_identity(
    source: Path,
    intended_destination: Path,
    expected_moa_checkpoint: str,
    *,
    listener_known_writers_stopped_attested: bool = False,
) -> DatabaseRelocationIdentityCertification:
    """Certify one normalized source identity without performing relocation."""
    source_path, destination_path, _tombstone_required = _resolve_relocation_paths(
        source, intended_destination
    )
    _require_clean_authorization_context(source_path, destination_path)
    _require_expected_moa_checkpoint(expected_moa_checkpoint)
    run_id = str(uuid4())
    authority = _establish_normalized_relocation_authority(source_path)
    authority.release()
    if authority.exclusion_released_at is None:
        raise DatabaseRelocationError(
            "RELOCATION_CERTIFICATION_BLOCKER: source writer exclusion release was not "
            "certified."
        )

    document = _certification_document(
        run_id=run_id,
        certified_at=authority.certified_at,
        source=source_path,
        destination=destination_path,
        moa_checkpoint=expected_moa_checkpoint,
        identity=authority.identity,
        truncate_checkpoint=authority.truncate_checkpoint,
        passive_checkpoint=authority.passive_checkpoint,
        exclusion_acquired_at=authority.exclusion_acquired_at,
        exclusion_released_at=authority.exclusion_released_at,
        integrity_check=authority.integrity_check,
        foreign_key_check=authority.foreign_key_check,
        listener_known_writers_stopped_attested=(
            listener_known_writers_stopped_attested
        ),
        pre_normalization_main=authority.pre_normalization_main,
        pre_normalization_wal=authority.pre_normalization_wal,
        pre_normalization_shm=authority.pre_normalization_shm,
    )
    record_digest = sha256(_canonical_json(document).encode("ascii")).hexdigest()
    return DatabaseRelocationIdentityCertification(
        run_id=run_id,
        certified_at=authority.certified_at,
        source=source_path,
        destination=destination_path,
        moa_checkpoint=expected_moa_checkpoint,
        identity=authority.identity,
        truncate_checkpoint=authority.truncate_checkpoint,
        passive_checkpoint=authority.passive_checkpoint,
        exclusion_acquired_at=authority.exclusion_acquired_at,
        exclusion_released_at=authority.exclusion_released_at,
        integrity_check=authority.integrity_check,
        foreign_key_check=authority.foreign_key_check,
        listener_known_writers_stopped_attested=(
            listener_known_writers_stopped_attested
        ),
        pre_normalization_main=authority.pre_normalization_main,
        pre_normalization_wal=authority.pre_normalization_wal,
        pre_normalization_shm=authority.pre_normalization_shm,
        certification_record_sha256=record_digest,
    )


def _relocate_database(
    source: Path,
    target: Path,
    *,
    expected_identity: DatabaseRelocationAuthorizationIdentity | None,
    _test_hook: Callable[[str], None] | None,
) -> DatabaseRelocationResult:
    if expected_identity is not None:
        _validate_expected_authorization_identity(expected_identity)
    source_path, target_path, retirement_tombstone_required = _resolve_relocation_paths(
        source, target
    )

    if expected_identity is None:
        _prepare_relocation_target(target_path)
        _validate_database(source_path)
        _checkpoint_source_for_retirement(source_path)
        source_connection = _acquire_source_quiescence(source_path)
        authorization_authority = None
        source_fingerprint = None
    else:
        _require_clean_authorization_context(source_path, target_path)
        authorization_authority = _establish_normalized_relocation_authority(source_path)
        source_connection = authorization_authority.connection
        source_fingerprint = authorization_authority.source_fingerprint
        if source_connection is None:
            raise DatabaseRelocationError(
                "RELOCATION_AUTHORIZATION_BLOCKER: source writer exclusion is not held."
            )
    primary_error: BaseException | None = None
    try:
        _notify_test_hook(_test_hook, "SOURCE_QUIESCENCE_HELD")
        if expected_identity is not None:
            _notify_test_hook(_test_hook, "AUTHORIZATION_IDENTITY_COMPUTED")
            assert authorization_authority is not None
            _require_matching_authorization_identity(
                expected_identity, authorization_authority.identity
            )
            _notify_test_hook(_test_hook, "RELOCATION_AUTHORIZED")
            _prepare_relocation_target(target_path)
        else:
            source_fingerprint = _validate_held_database(source_connection, source_path)
        assert source_fingerprint is not None
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
            if _validate_held_database(source_connection, source_path) != source_fingerprint:
                raise DatabaseRelocationError(
                    "Relocation source changed despite source writer exclusion."
                )
            _notify_test_hook(_test_hook, "FINAL_SOURCE_SNAPSHOT_ESTABLISHED")
            _checkpoint_source_for_retirement(temporary_path)
            _remove_checkpointed_sidecars(temporary_path)
            target_image_digest = _database_file_digest(temporary_path)
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
            if _source_handle_blocks_retirement():
                if authorization_authority is None:
                    _release_source_quiescence(source_connection)
                else:
                    authorization_authority.release()
                source_connection = None
                _notify_test_hook(_test_hook, "SOURCE_QUIESCENCE_RELEASED")
                archive_path = _retire_source(source_path)
                _notify_test_hook(_test_hook, "SOURCE_RETIRED")
                if retirement_tombstone_required:
                    _install_retirement_tombstone(source_path, target_path, archive_path)
                    _notify_test_hook(_test_hook, "RETIREMENT_TOMBSTONE_INSTALLED")
                if _validate_database(archive_path) != source_fingerprint:
                    raise DatabaseRelocationError(
                        "LEGACY_SOURCE_RETIREMENT_BLOCKER: the archived source changed "
                        "after the final migration snapshot; the promoted target is not "
                        "authoritative."
                    )
                if _backup_image_digest(archive_path) != target_image_digest:
                    raise DatabaseRelocationError(
                        "LEGACY_SOURCE_RETIREMENT_BLOCKER: the archived source image changed "
                        "after the final migration snapshot; the promoted target is not "
                        "authoritative."
                    )
                _notify_test_hook(_test_hook, "ARCHIVED_SOURCE_CERTIFIED")
                if retirement_tombstone_required:
                    _validate_retirement_tombstone(
                        source_path,
                        target_path,
                        expected_archive=archive_path,
                    )
                elif source_path.exists():
                    raise DatabaseRelocationError(
                        "LEGACY_SOURCE_RETIREMENT_BLOCKER: a new legacy source appeared "
                        "during Windows retirement."
                    )
            else:
                archive_path = _retire_source(source_path)
                _notify_test_hook(_test_hook, "SOURCE_RETIRED")
                if retirement_tombstone_required:
                    _install_retirement_tombstone(source_path, target_path, archive_path)
                    _notify_test_hook(_test_hook, "RETIREMENT_TOMBSTONE_INSTALLED")
                if authorization_authority is None:
                    _release_source_quiescence(source_connection)
                else:
                    authorization_authority.release()
                source_connection = None
                if retirement_tombstone_required:
                    _validate_retirement_tombstone(
                        source_path,
                        target_path,
                        expected_archive=archive_path,
                    )
            _complete_source_retirement(archive_path)
        except BaseException as error:
            raise DatabaseRelocationError(
                "The new database was promoted and remains valid, but migration did not "
                f"complete because the source could not be retired. Source: {source_path}. "
                f"Target: {target_path}. Resolve this dual-authority state explicitly."
            ) from error
        return DatabaseRelocationResult(source_path, target_path, archive_path)
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if source_connection is not None:
            if authorization_authority is None:
                _release_source_quiescence(
                    source_connection, primary_error=primary_error
                )
            else:
                authorization_authority.release(primary_error=primary_error)


def _resolve_relocation_paths(
    source: Path, target: Path
) -> tuple[Path, Path, bool]:
    source_path = _canonical_file_path(source, label="source")
    target_path = _canonical_file_path(target, label="target")
    if source_path == target_path:
        raise DatabaseRelocationError("Relocation source and target must be distinct paths.")
    if not source_path.is_file():
        raise DatabaseRelocationError(
            f"Relocation source does not exist as a database file: {source_path}"
        )
    if target_path.exists():
        raise DatabaseRelocationError(
            f"Relocation target already exists and will not be overwritten: {target_path}"
        )

    verified_legacy = verified_legacy_database_path()
    retirement_tombstone_required = (
        verified_legacy is not None
        and source_path == verified_legacy.resolve(strict=False)
    )
    if verified_legacy is not None and verified_legacy.exists():
        resolved_legacy = verified_legacy.resolve(strict=False)
        if source_path != resolved_legacy:
            raise DatabaseRelocationError(
                "A verified checkout-local legacy database exists and must be the explicit "
                f"relocation source: {resolved_legacy}"
            )
    return source_path, target_path, retirement_tombstone_required


def _resolve_wal_recovery_paths(source: Path, destination: Path) -> tuple[Path, Path]:
    source_path = _canonical_file_path(source, label="recovery source")
    destination_path = _canonical_file_path(
        destination, label="intended recovery destination"
    )
    if source_path == destination_path:
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_AUTHORIZATION_MISMATCH: source and intended "
            "destination must be distinct paths."
        )
    if not source_path.is_file():
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_BLOCKER: source does not exist as an ordinary file: "
            f"{source_path}"
        )
    if destination_path.exists():
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_BLOCKER: intended relocation destination must remain "
            f"absent: {destination_path}"
        )
    return source_path, destination_path


def _require_clean_authorization_context(source: Path, target: Path) -> None:
    incomplete_retirements = _incomplete_retirement_markers(source)
    if incomplete_retirements:
        raise DatabaseRelocationError(
            "RELOCATION_CERTIFICATION_BLOCKER: an incomplete source retirement requires "
            "explicit recovery before identity certification."
        )
    stale_paths = (
        tuple(sorted(target.parent.glob(f"{target.name}.migrating-*")))
        if target.parent.exists()
        else ()
    )
    if stale_paths:
        raise DatabaseRelocationError(
            "RELOCATION_CERTIFICATION_BLOCKER: recognized stale relocation temporary "
            "state requires explicit recovery."
        )


def _require_clean_journal_mode_preparation_context(
    source: Path, target: Path
) -> None:
    if _incomplete_retirement_markers(source):
        raise DatabaseRelocationError(
            "RELOCATION_JOURNAL_MODE_PREPARATION_BLOCKER: an incomplete source "
            "retirement requires explicit recovery."
        )
    stale_certification_paths = tuple(
        sorted(source.parent.glob(f"{source.name}.certifying-*"))
    )
    stale_relocation_paths = (
        tuple(sorted(target.parent.glob(f"{target.name}.migrating-*")))
        if target.parent.exists()
        else ()
    )
    if stale_certification_paths or stale_relocation_paths:
        raise DatabaseRelocationError(
            "RELOCATION_JOURNAL_MODE_PREPARATION_BLOCKER: recognized stale "
            "certification or relocation temporary state requires explicit recovery."
        )


def _require_expected_moa_checkpoint(expected_checkpoint: str) -> None:
    if not _is_git_checkpoint(expected_checkpoint):
        raise DatabaseRelocationError(
            "RELOCATION_CERTIFICATION_CHECKPOINT_MISMATCH: expected MOA checkpoint "
            "must be a lowercase 40-character hexadecimal commit identity."
        )
    actual_checkpoint = _verified_moa_checkout_checkpoint()
    if actual_checkpoint != expected_checkpoint:
        raise DatabaseRelocationError(
            "RELOCATION_CERTIFICATION_CHECKPOINT_MISMATCH: expected and running MOA "
            "checkpoints differ."
        )


def _establish_normalized_relocation_authority(
    source: Path,
) -> _NormalizedRelocationAuthority:
    _validate_database(source)
    pre_main = _observe_database_file(source)
    pre_wal = _observe_database_file(Path(f"{source}-wal"))
    pre_shm = _observe_database_file(Path(f"{source}-shm"))
    truncate_checkpoint = _checkpoint_source_truncate_neutral(source)
    connection = _acquire_authorization_exclusion(source)
    acquired_at = _utc_timestamp()
    primary_error: BaseException | None = None
    try:
        passive_checkpoint = _checkpoint_source_passive_under_exclusion(source)
        identity = _compute_relocation_authorization_identity(connection, source)
        source_fingerprint, integrity_check, foreign_key_check = (
            _validate_held_database_with_evidence(connection, source)
        )
        certified_at = _utc_timestamp()
        return _NormalizedRelocationAuthority(
            connection=connection,
            identity=identity,
            source_fingerprint=source_fingerprint,
            truncate_checkpoint=truncate_checkpoint,
            passive_checkpoint=passive_checkpoint,
            exclusion_acquired_at=acquired_at,
            certified_at=certified_at,
            integrity_check=integrity_check,
            foreign_key_check=foreign_key_check,
            pre_normalization_main=pre_main,
            pre_normalization_wal=pre_wal,
            pre_normalization_shm=pre_shm,
        )
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if primary_error is not None:
            _release_source_quiescence(connection, primary_error=primary_error)


def _incomplete_retirement_markers(source: Path) -> tuple[Path, ...]:
    pattern = f"{source.name}.migrated-backup-*/{_INCOMPLETE_RETIREMENT_MARKER_NAME}"
    return tuple(sorted(source.parent.glob(pattern)))


def _install_retirement_tombstone(source: Path, target: Path, archive: Path) -> None:
    try:
        source.mkdir(exist_ok=False)
    except OSError as error:
        raise DatabaseRelocationError(
            "LEGACY_PATH_RECREATION_BLOCKER: could not atomically install the persistent "
            f"retirement tombstone at {source}: {error}"
        ) from error

    marker = source / _RETIREMENT_TOMBSTONE_MARKER_NAME
    payload = {
        "format": _RETIREMENT_TOMBSTONE_FORMAT,
        "version": _RETIREMENT_TOMBSTONE_VERSION,
        "target": str(target.resolve(strict=False)),
        "archive": str(archive.resolve(strict=False)),
    }
    try:
        _write_retirement_tombstone_marker(marker, payload)
    except BaseException as error:
        raise DatabaseRelocationError(
            "LEGACY_PATH_RECREATION_BLOCKER: the retirement tombstone directory now blocks "
            f"legacy database recreation, but its marker could not be completed at {marker}: "
            f"{error}"
        ) from error
    _validate_retirement_tombstone(source, target, expected_archive=archive)


def _write_retirement_tombstone_marker(marker: Path, payload: dict[str, object]) -> None:
    with marker.open("x", encoding="utf-8", newline="\n") as marker_file:
        json.dump(payload, marker_file, sort_keys=True)
        marker_file.write("\n")
        marker_file.flush()
        os.fsync(marker_file.fileno())


def _validate_retirement_tombstone(
    source: Path,
    target: Path,
    *,
    expected_archive: Path | None = None,
) -> _RetirementTombstone:
    if source.is_symlink() or not source.is_dir():
        raise DatabaseRelocationError(
            f"Legacy retirement tombstone is not a real directory: {source}"
        )
    marker = source / _RETIREMENT_TOMBSTONE_MARKER_NAME
    if marker.is_symlink() or not marker.is_file():
        raise DatabaseRelocationError(
            f"Legacy retirement tombstone marker is missing or invalid: {marker}"
        )
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DatabaseRelocationError(
            f"Could not read legacy retirement tombstone marker {marker}: {error}"
        ) from error
    expected_keys = {"format", "version", "target", "archive"}
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise DatabaseRelocationError(
            f"Legacy retirement tombstone marker has an invalid structure: {marker}"
        )
    if (
        payload["format"] != _RETIREMENT_TOMBSTONE_FORMAT
        or payload["version"] != _RETIREMENT_TOMBSTONE_VERSION
        or not isinstance(payload["target"], str)
        or not isinstance(payload["archive"], str)
    ):
        raise DatabaseRelocationError(
            f"Legacy retirement tombstone marker has an unsupported identity: {marker}"
        )
    tombstone = _RetirementTombstone(
        Path(payload["target"]).resolve(strict=False),
        Path(payload["archive"]).resolve(strict=False),
    )
    resolved_target = target.resolve(strict=False)
    if tombstone.target != resolved_target:
        raise DatabaseRelocationError(
            "Legacy retirement tombstone target does not match the current default. "
            f"Marker: {tombstone.target}. Current default: {resolved_target}."
        )
    if expected_archive is not None and tombstone.archive != expected_archive.resolve(
        strict=False
    ):
        raise DatabaseRelocationError(
            "Legacy retirement tombstone archive does not match this relocation. "
            f"Marker: {tombstone.archive}. Relocation: {expected_archive.resolve(strict=False)}."
        )
    if not tombstone.archive.is_file():
        raise DatabaseRelocationError(
            f"Legacy retirement tombstone archive is missing or is not a file: {tombstone.archive}"
        )
    return tombstone


def _notify_test_hook(hook: Callable[[str], None] | None, stage: str) -> None:
    if hook is not None:
        hook(stage)


def _source_handle_blocks_retirement() -> bool:
    return os.name == "nt"


def _prepare_relocation_target(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    stale_paths = tuple(sorted(target.parent.glob(f"{target.name}.migrating-*")))
    if stale_paths:
        rendered = ", ".join(str(path) for path in stale_paths)
        raise DatabaseRelocationError(
            f"Recognized stale relocation temporary file(s) require cleanup: {rendered}"
        )


def _open_neutral_source_connection(source: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(source)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {_SOURCE_QUIESCENCE_BUSY_TIMEOUT_MS}")
    return connection


def _query_single_pragma_value(connection: sqlite3.Connection, sql: str) -> str:
    rows = connection.execute(sql).fetchall()
    if len(rows) != 1 or len(rows[0]) != 1 or not isinstance(rows[0][0], str):
        raise DatabaseRelocationError(
            "RELOCATION_JOURNAL_MODE_PREPARATION_FAILURE: SQLite returned an invalid "
            "PRAGMA result."
        )
    return str(rows[0][0])


def _query_exact_journal_mode(connection: sqlite3.Connection) -> str:
    mode = _query_single_pragma_value(connection, "PRAGMA journal_mode").casefold()
    if mode not in {*_PERSISTENT_ROLLBACK_JOURNAL_MODES, "memory", "off", "wal"}:
        raise DatabaseRelocationError(
            "RELOCATION_JOURNAL_MODE_PREPARATION_FAILURE: SQLite returned an unknown "
            "journal mode."
        )
    return mode


def _transition_journal_mode_to_wal(connection: sqlite3.Connection) -> str:
    if connection.in_transaction:
        raise DatabaseRelocationError(
            "RELOCATION_JOURNAL_MODE_PREPARATION_FAILURE: journal mode cannot be "
            "changed inside an active SQL transaction."
        )
    return _query_single_pragma_value(
        connection, "PRAGMA journal_mode=WAL"
    ).casefold()


def _observe_database_representation(
    source: Path,
) -> tuple[
    DatabaseFileObservation,
    DatabaseFileObservation,
    DatabaseFileObservation,
    DatabaseFileObservation,
]:
    return (
        _observe_database_file(source),
        _observe_database_file(Path(f"{source}-journal")),
        _observe_database_file(Path(f"{source}-wal")),
        _observe_database_file(Path(f"{source}-shm")),
    )


def _observe_database_sidecars(
    source: Path,
) -> tuple[
    DatabaseFileObservation,
    DatabaseFileObservation,
    DatabaseFileObservation,
]:
    _main, journal, wal, shm = _observe_database_representation(source)
    return journal, wal, shm


def _observe_sidecar_presence(path: Path) -> DatabaseSidecarObservation:
    if not path.exists():
        return DatabaseSidecarObservation(False, None)
    if not path.is_file():
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_BLOCKER: a sidecar path is not a regular file: "
            f"{path}"
        )
    try:
        size = path.stat().st_size
    except OSError as error:
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_BLOCKER: sidecar presence/size could not be "
            "observed."
        ) from error
    return DatabaseSidecarObservation(True, size)


def _compute_recovery_semantic_validation(
    connection: sqlite3.Connection,
    source: Path,
) -> tuple[
    DatabaseRelocationAuthorizationIdentity,
    tuple[str, ...],
    tuple[tuple[object, ...], ...],
]:
    if connection.in_transaction:
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_FAILURE: semantic validation cannot start inside "
            "an active transaction."
        )
    try:
        connection.execute("BEGIN IMMEDIATE")
        identity = _compute_relocation_authorization_identity(connection, source)
        _fingerprint, integrity_check, foreign_key_check = (
            _validate_held_database_with_evidence(connection, source)
        )
        return identity, integrity_check, foreign_key_check
    except DatabaseRelocationError:
        raise
    except sqlite3.Error as error:
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_FAILURE: semantic validation could not complete."
        ) from error
    finally:
        if connection.in_transaction:
            connection.rollback()


def _checkpoint_wal_truncate_on_owned_connection(
    connection: sqlite3.Connection,
) -> tuple[int, int, int]:
    if connection.in_transaction:
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_FAILURE: TRUNCATE checkpoint cannot run inside an "
            "active transaction."
        )
    try:
        row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    except sqlite3.Error as error:
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_FAILURE: TRUNCATE checkpoint failed."
        ) from error
    if not isinstance(row, (tuple, sqlite3.Row)) or len(row) != 3:
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_FAILURE: TRUNCATE checkpoint returned an invalid "
            "result."
        )
    try:
        checkpoint = tuple(int(value) for value in row)
    except (TypeError, ValueError) as error:
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_FAILURE: TRUNCATE checkpoint returned an invalid "
            "result."
        ) from error
    if checkpoint != (0, 0, 0):
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_FAILURE: TRUNCATE checkpoint did not return exact "
            "success (0, 0, 0)."
        )
    return 0, 0, 0


def _database_header_versions(source: Path) -> tuple[int, int]:
    try:
        with source.open("rb") as database_file:
            header = database_file.read(20)
    except OSError as error:
        raise DatabaseRelocationError(
            "RELOCATION_JOURNAL_MODE_PREPARATION_FAILURE: the SQLite main header "
            "could not be observed."
        ) from error
    if len(header) != 20 or header[:16] != b"SQLite format 3\x00":
        raise DatabaseRelocationError(
            "RELOCATION_JOURNAL_MODE_PREPARATION_FAILURE: the source does not have a "
            "valid SQLite main header."
        )
    return header[18], header[19]


def _acquire_source_quiescence(source: Path) -> sqlite3.Connection:
    from moa.database.sqlite import connect

    connection: sqlite3.Connection | None = None
    try:
        connection = connect(source)
        connection.execute(f"PRAGMA busy_timeout = {_SOURCE_QUIESCENCE_BUSY_TIMEOUT_MS}")
        connection.execute("BEGIN IMMEDIATE")
        return connection
    except sqlite3.Error as error:
        if connection is not None:
            try:
                connection.close()
            except BaseException as cleanup_error:
                error.add_note(
                    f"Could not close source quiescence connection: {cleanup_error}"
                )
        raise DatabaseRelocationError(
            "LEGACY_SOURCE_RETIREMENT_BLOCKER: could not acquire bounded SQLite source "
            "writer exclusion; stop every listener and source writer."
        ) from error


def _acquire_authorization_exclusion(source: Path) -> sqlite3.Connection:
    connection: sqlite3.Connection | None = None
    try:
        connection = _open_neutral_source_connection(source)
        connection.execute("BEGIN IMMEDIATE")
        return connection
    except sqlite3.Error as error:
        if connection is not None:
            try:
                connection.close()
            except BaseException as cleanup_error:
                error.add_note(
                    f"Could not close source quiescence connection: {cleanup_error}"
                )
        raise DatabaseRelocationError(
            "RELOCATION_AUTHORIZATION_BLOCKER: could not acquire bounded SQLite source "
            "writer exclusion; stop every listener and source writer."
        ) from error


def _checkpoint_source_truncate_neutral(source: Path) -> tuple[int, int, int]:
    connection: sqlite3.Connection | None = None
    primary_error: BaseException | None = None
    try:
        connection = _open_neutral_source_connection(source)
        journal_mode_row = connection.execute("PRAGMA journal_mode").fetchone()
        if (
            journal_mode_row is None
            or len(journal_mode_row) != 1
            or str(journal_mode_row[0]).casefold() != "wal"
        ):
            raise DatabaseRelocationError(
                "RELOCATION_AUTHORIZATION_BLOCKER: source journal mode must already be "
                "WAL; certification will not change it."
            )
        row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        checkpoint = _require_complete_checkpoint_result(row, mode="truncate")
        return checkpoint
    except DatabaseRelocationError as error:
        primary_error = error
        raise
    except (sqlite3.Error, TypeError, ValueError) as error:
        primary_error = error
        raise DatabaseRelocationError(
            "RELOCATION_AUTHORIZATION_BLOCKER: truncate checkpoint normalization failed."
        ) from error
    finally:
        if connection is not None:
            _close_checkpoint_connection(
                connection,
                mode="truncate",
                primary_error=primary_error,
            )


def _checkpoint_source_passive_under_exclusion(
    source: Path,
) -> tuple[int, int, int]:
    connection: sqlite3.Connection | None = None
    primary_error: BaseException | None = None
    try:
        connection = _open_neutral_source_connection(source)
        row = connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        return _require_complete_checkpoint_result(row, mode="passive")
    except DatabaseRelocationError as error:
        primary_error = error
        raise
    except (sqlite3.Error, TypeError, ValueError) as error:
        primary_error = error
        raise DatabaseRelocationError(
            "RELOCATION_AUTHORIZATION_BLOCKER: passive checkpoint normalization failed."
        ) from error
    finally:
        if connection is not None:
            _close_checkpoint_connection(
                connection,
                mode="passive",
                primary_error=primary_error,
            )


def _require_complete_checkpoint_result(
    row: object, *, mode: str
) -> tuple[int, int, int]:
    if not isinstance(row, (tuple, sqlite3.Row)) or len(row) != 3:
        raise DatabaseRelocationError(
            f"RELOCATION_AUTHORIZATION_BLOCKER: {mode} checkpoint returned no complete "
            "backfill result."
        )
    busy, log_frames, checkpointed_frames = (int(value) for value in row)
    if busy or log_frames != checkpointed_frames:
        raise DatabaseRelocationError(
            f"RELOCATION_AUTHORIZATION_BLOCKER: {mode} checkpoint could not completely "
            "backfill the normalized source."
        )
    return busy, log_frames, checkpointed_frames


def _close_checkpoint_connection(
    connection: sqlite3.Connection,
    *,
    mode: str,
    primary_error: BaseException | None,
) -> None:
    try:
        connection.close()
    except BaseException as cleanup_error:
        if primary_error is not None:
            primary_error.add_note(
                f"Could not close {mode} checkpoint connection: {cleanup_error}"
            )
        else:
            raise DatabaseRelocationError(
                f"RELOCATION_AUTHORIZATION_BLOCKER: could not close {mode} checkpoint "
                "connection."
            ) from cleanup_error


def _release_source_quiescence(
    connection: sqlite3.Connection,
    *,
    primary_error: BaseException | None = None,
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
    rendered = "; ".join(str(error) for error in cleanup_errors)
    if primary_error is not None:
        primary_error.add_note(f"Could not release source writer exclusion: {rendered}")
        return
    raise DatabaseRelocationError(
        f"LEGACY_SOURCE_RETIREMENT_BLOCKER: could not release source writer exclusion: {rendered}"
    )


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


def _validate_expected_authorization_identity(
    identity: DatabaseRelocationAuthorizationIdentity,
) -> None:
    if not isinstance(identity, DatabaseRelocationAuthorizationIdentity):
        raise DatabaseRelocationError(
            "Relocation authorization identity has an invalid typed representation."
        )
    if not _is_sha256(identity.normalized_sha256):
        raise DatabaseRelocationError(
            "Relocation authorization identity has an invalid normalized SHA-256."
        )
    if not _is_nonnegative_int(identity.normalized_size):
        raise DatabaseRelocationError(
            "Relocation authorization identity has an invalid normalized size."
        )
    if not isinstance(identity.migration_identity, tuple) or not all(
        isinstance(row, tuple)
        and len(row) == 2
        and isinstance(row[0], int)
        and not isinstance(row[0], bool)
        and row[0] > 0
        and isinstance(row[1], str)
        and bool(row[1])
        for row in identity.migration_identity
    ):
        raise DatabaseRelocationError(
            "Relocation authorization identity has invalid migration tuples."
        )
    if not isinstance(identity.generation_inventory, tuple) or not all(
        isinstance(row, tuple)
        and len(row) == 2
        and isinstance(row[0], int)
        and not isinstance(row[0], bool)
        and row[0] > 0
        and isinstance(row[1], bool)
        for row in identity.generation_inventory
    ):
        raise DatabaseRelocationError(
            "Relocation authorization identity has an invalid generation inventory."
        )
    if (
        not identity.generation_inventory
        or tuple(row[0] for row in identity.generation_inventory)
        != tuple(sorted({row[0] for row in identity.generation_inventory}))
        or sum(row[1] for row in identity.generation_inventory) != 1
    ):
        raise DatabaseRelocationError(
            "Relocation authorization identity requires one ordered current generation."
        )
    if not _is_nonnegative_int(identity.source_event_count) or not _is_nonnegative_int(
        identity.generation_1_projection_link_count
    ):
        raise DatabaseRelocationError(
            "Relocation authorization identity has an invalid durable row count."
        )
    if not isinstance(identity.projection_gaps, tuple) or not all(
        isinstance(finding, DataHealthFinding) for finding in identity.projection_gaps
    ):
        raise DatabaseRelocationError(
            "Relocation authorization identity has an invalid projection-gap result."
        )
    if not _is_sha256(identity.retained_source_preflight_fingerprint):
        raise DatabaseRelocationError(
            "Relocation authorization identity has an invalid retained-source fingerprint."
        )


def _validate_journal_mode_preparation_authority(
    authority: DatabaseRelocationJournalModePreparationAuthority,
) -> None:
    if not isinstance(authority, DatabaseRelocationJournalModePreparationAuthority):
        raise DatabaseRelocationError(
            "RELOCATION_JOURNAL_MODE_PREPARATION_MISMATCH: a typed preparation "
            "authority is required."
        )
    if not _is_git_checkpoint(authority.expected_moa_checkpoint):
        raise DatabaseRelocationError(
            "RELOCATION_JOURNAL_MODE_PREPARATION_MISMATCH: expected MOA checkpoint "
            "must be a lowercase 40-character hexadecimal commit identity."
        )
    if (
        authority.expected_journal_mode
        not in _PERSISTENT_ROLLBACK_JOURNAL_MODES
    ):
        raise DatabaseRelocationError(
            "RELOCATION_JOURNAL_MODE_PREPARATION_MISMATCH: expected journal mode "
            "must name one exact persistent rollback-journal mode."
        )
    if not isinstance(authority.authorization_id, str) or not authority.authorization_id:
        raise DatabaseRelocationError(
            "RELOCATION_JOURNAL_MODE_PREPARATION_MISMATCH: a non-empty authorization "
            "binding is required."
        )
    if not isinstance(authority.listener_known_writers_stopped_attested, bool):
        raise DatabaseRelocationError(
            "RELOCATION_JOURNAL_MODE_PREPARATION_MISMATCH: known-writer attestation "
            "must be an explicit boolean."
        )
    _validate_file_observation(authority.expected_main, label="main file")
    if not authority.expected_main.present:
        raise DatabaseRelocationError(
            "RELOCATION_JOURNAL_MODE_PREPARATION_MISMATCH: expected main-file "
            "authority must describe a present file."
        )
    _validate_file_observation(
        authority.expected_journal, label="rollback journal"
    )
    _validate_file_observation(authority.expected_wal, label="WAL sidecar")
    _validate_file_observation(authority.expected_shm, label="SHM sidecar")


def _validate_wal_recovery_authority(authority: DatabaseWalRecoveryAuthority) -> None:
    if not isinstance(authority, DatabaseWalRecoveryAuthority):
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_AUTHORIZATION_MISMATCH: a typed recovery-only "
            "authority is required."
        )
    exact_fields = (
        (
            "authorization format",
            authority.authorization_format,
            _WAL_RECOVERY_AUTHORIZATION_FORMAT,
        ),
        (
            "authorization version",
            authority.authorization_version,
            _WAL_RECOVERY_AUTHORIZATION_VERSION,
        ),
        ("action", authority.action, _WAL_RECOVERY_ACTION),
        ("max attempts", authority.max_attempts, _WAL_RECOVERY_MAX_ATTEMPTS),
        ("expected journal mode", authority.expected_journal_mode, "wal"),
    )
    mismatches = tuple(
        label for label, actual, expected in exact_fields if actual != expected
    )
    if mismatches:
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_AUTHORIZATION_MISMATCH: invalid "
            + ", ".join(mismatches)
            + "."
        )
    if not isinstance(authority.authorization_id, str) or not authority.authorization_id:
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_AUTHORIZATION_MISMATCH: a non-empty authorization "
            "ID is required."
        )
    if not isinstance(authority.source, Path) or not isinstance(
        authority.intended_destination, Path
    ):
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_AUTHORIZATION_MISMATCH: source and intended "
            "destination must be typed paths."
        )
    if not _is_git_checkpoint(authority.expected_moa_checkpoint):
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_AUTHORIZATION_MISMATCH: expected MOA checkpoint "
            "must be a lowercase 40-character hexadecimal commit identity."
        )
    if not isinstance(authority.listener_known_writers_stopped_attested, bool):
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_AUTHORIZATION_MISMATCH: known-writer attestation "
            "must be an explicit boolean."
        )
    _validate_recovery_file_observation(authority.expected_main, label="main file")
    if not authority.expected_main.present:
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_AUTHORIZATION_MISMATCH: expected main file must be "
            "present."
        )
    _validate_recovery_file_observation(authority.expected_wal, label="WAL sidecar")
    _validate_recovery_sidecar_observation(authority.expected_shm, label="SHM sidecar")
    if not _is_sha256(authority.authorization_record_sha256):
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_AUTHORIZATION_MISMATCH: authorization digest must "
            "be exactly 64 lowercase hexadecimal characters."
        )
    expected_digest = sha256(
        _canonical_json(_wal_recovery_authority_document(authority)).encode("ascii")
    ).hexdigest()
    if authority.authorization_record_sha256 != expected_digest:
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_AUTHORIZATION_MISMATCH: authorization digest does "
            "not bind the supplied recovery authority."
        )


def _validate_recovery_file_observation(
    observation: DatabaseFileObservation, *, label: str
) -> None:
    valid = isinstance(observation, DatabaseFileObservation)
    if valid and observation.present:
        valid = _is_nonnegative_int(observation.size) and _is_sha256(
            observation.sha256
        )
    elif valid:
        valid = observation.size is None and observation.sha256 is None
    if not valid:
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_AUTHORIZATION_MISMATCH: expected "
            f"{label} observation is incomplete or malformed."
        )


def _validate_recovery_sidecar_observation(
    observation: DatabaseSidecarObservation, *, label: str
) -> None:
    valid = isinstance(observation, DatabaseSidecarObservation)
    if valid and observation.present:
        valid = _is_nonnegative_int(observation.size)
    elif valid:
        valid = observation.size is None
    if not valid:
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_AUTHORIZATION_MISMATCH: expected "
            f"{label} presence/size is incomplete or malformed."
        )


def _require_matching_recovery_file_observation(
    expected: DatabaseFileObservation,
    actual: DatabaseFileObservation,
    *,
    label: str,
) -> None:
    if expected != actual:
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_AUTHORIZATION_MISMATCH: observed "
            f"{label} differs from the explicit recovery authority."
        )


def _require_matching_recovery_sidecar_observation(
    expected: DatabaseSidecarObservation,
    actual: DatabaseSidecarObservation,
    *,
    label: str,
) -> None:
    if expected != actual:
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_AUTHORIZATION_MISMATCH: observed "
            f"{label} differs from the explicit recovery authority."
        )


def _require_equivalent_locked_wal(
    expected: DatabaseFileObservation, actual: DatabaseFileObservation
) -> None:
    if expected == actual:
        return
    expected_empty = not expected.present or expected.size == 0
    actual_empty = not actual.present or actual.size == 0
    if expected_empty and actual_empty:
        return
    raise DatabaseRelocationError(
        "DATABASE_WAL_RECOVERY_AUTHORIZATION_MISMATCH: locked WAL representation "
        "differs from the authorized pre-recovery state."
    )


def _validate_file_observation(
    observation: DatabaseFileObservation, *, label: str
) -> None:
    valid = isinstance(observation, DatabaseFileObservation)
    if valid and observation.present:
        valid = _is_nonnegative_int(observation.size) and _is_sha256(
            observation.sha256
        )
    elif valid:
        valid = observation.size is None and observation.sha256 is None
    if not valid:
        raise DatabaseRelocationError(
            "RELOCATION_JOURNAL_MODE_PREPARATION_MISMATCH: expected "
            f"{label} authority is incomplete or malformed."
        )


def _require_matching_file_observation(
    expected: DatabaseFileObservation,
    actual: DatabaseFileObservation,
    *,
    label: str,
) -> None:
    if expected != actual:
        raise DatabaseRelocationError(
            "RELOCATION_JOURNAL_MODE_PREPARATION_MISMATCH: observed "
            f"{label} state differs from the explicit preparation authority."
        )


def _require_matching_journal_mode(expected: str, actual: str) -> None:
    if expected != actual:
        raise DatabaseRelocationError(
            "RELOCATION_JOURNAL_MODE_PREPARATION_MISMATCH: observed journal mode "
            "differs from the explicit preparation authority."
        )


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_git_checkpoint(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _observe_database_file(path: Path) -> DatabaseFileObservation:
    if not path.exists():
        return DatabaseFileObservation(False, None, None)
    if not path.is_file():
        raise DatabaseRelocationError(
            "RELOCATION_CERTIFICATION_BLOCKER: a database representation path is not "
            f"a regular file: {path}"
        )
    try:
        with path.open("rb") as observed_file:
            digest = file_digest(observed_file, "sha256").hexdigest()
            size = os.fstat(observed_file.fileno()).st_size
    except OSError as error:
        raise DatabaseRelocationError(
            "RELOCATION_CERTIFICATION_BLOCKER: a pre-normalization database "
            "representation could not be observed."
        ) from error
    return DatabaseFileObservation(True, size, digest)


def _canonical_json(document: dict[str, object]) -> str:
    return json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _file_observation_document(observation: DatabaseFileObservation) -> dict[str, object]:
    return {
        "present": observation.present,
        "sha256": observation.sha256,
        "size": observation.size,
    }


def _optional_file_observation_document(
    observation: DatabaseFileObservation | None,
) -> dict[str, object] | None:
    return _file_observation_document(observation) if observation is not None else None


def _authorization_identity_document(
    identity: DatabaseRelocationAuthorizationIdentity,
) -> dict[str, object]:
    return {
        "generation_1_projection_link_count": identity.generation_1_projection_link_count,
        "generation_inventory": [list(row) for row in identity.generation_inventory],
        "migration_identity": [list(row) for row in identity.migration_identity],
        "normalized_sha256": identity.normalized_sha256,
        "normalized_size": identity.normalized_size,
        "projection_gaps": [
            {
                "category": finding.category,
                "check_id": finding.check_id,
                "entity": finding.entity,
                "local_identifier": finding.local_identifier,
                "local_identifier_kind": (
                    "int" if isinstance(finding.local_identifier, int) else "str"
                ),
                "reason": finding.reason,
            }
            for finding in identity.projection_gaps
        ],
        "retained_source_preflight_fingerprint": (
            identity.retained_source_preflight_fingerprint
        ),
        "source_event_count": identity.source_event_count,
    }


def _journal_preparation_semantic_identity_document(
    identity: DatabaseRelocationAuthorizationIdentity,
) -> dict[str, object]:
    document = _authorization_identity_document(identity)
    del document["normalized_sha256"]
    del document["normalized_size"]
    return document


def _require_unchanged_journal_preparation_semantics(
    before: DatabaseRelocationAuthorizationIdentity,
    after: DatabaseRelocationAuthorizationIdentity,
) -> None:
    if _journal_preparation_semantic_identity_document(
        before
    ) != _journal_preparation_semantic_identity_document(after):
        raise DatabaseRelocationError(
            "RELOCATION_JOURNAL_MODE_PREPARATION_FAILURE: logical catalog identity "
            "changed across the journal-mode transition."
        )


def _require_unchanged_wal_recovery_semantics(
    before: DatabaseRelocationAuthorizationIdentity,
    after: DatabaseRelocationAuthorizationIdentity,
) -> None:
    if _journal_preparation_semantic_identity_document(
        before
    ) != _journal_preparation_semantic_identity_document(after):
        raise DatabaseRelocationError(
            "DATABASE_WAL_RECOVERY_FAILURE: logical catalog identity changed across "
            "TRUNCATE normalization."
        )


def _sidecar_observation_document(
    observation: DatabaseSidecarObservation,
) -> dict[str, object]:
    return {"present": observation.present, "size": observation.size}


def _wal_recovery_authority_document(
    authority: DatabaseWalRecoveryAuthority,
) -> dict[str, object]:
    return {
        "action": authority.action,
        "authorization_format": authority.authorization_format,
        "authorization_record_sha256_scope": (
            _WAL_RECOVERY_AUTHORIZATION_HASH_SCOPE
        ),
        "authorization_version": authority.authorization_version,
        "authorization_id": authority.authorization_id,
        "expected_journal_mode": authority.expected_journal_mode,
        "expected_main": _file_observation_document(authority.expected_main),
        "expected_moa_checkpoint": authority.expected_moa_checkpoint,
        "expected_shm": _sidecar_observation_document(authority.expected_shm),
        "expected_wal": _file_observation_document(authority.expected_wal),
        "intended_destination": str(authority.intended_destination),
        "listener_known_writers_stopped_attested": (
            authority.listener_known_writers_stopped_attested
        ),
        "max_attempts": authority.max_attempts,
        "source": str(authority.source),
    }


def _wal_recovery_evidence_document(
    *,
    status: str,
    run_id: str,
    started_at: str,
    completed_at: str,
    authority: DatabaseWalRecoveryAuthority,
    sqlite_version: str,
    listener_guard_acquired_at: str,
    listener_guard_released_at: str,
    sqlite_exclusive_acquired_at: str,
    sqlite_exclusive_released_at: str,
    pre_lock_main: DatabaseFileObservation,
    pre_lock_wal: DatabaseFileObservation,
    pre_lock_shm: DatabaseSidecarObservation,
    locked_main: DatabaseFileObservation,
    locked_wal: DatabaseFileObservation,
    locked_shm: DatabaseSidecarObservation,
    pre_normalization_journal_mode: str,
    pre_semantic_identity: DatabaseRelocationAuthorizationIdentity,
    pre_integrity_check: tuple[str, ...],
    pre_foreign_key_check: tuple[tuple[object, ...], ...],
    truncate_checkpoint: tuple[int, int, int] | None,
    post_semantic_identity: DatabaseRelocationAuthorizationIdentity,
    post_integrity_check: tuple[str, ...],
    post_foreign_key_check: tuple[tuple[object, ...], ...],
    normalized_main: DatabaseFileObservation,
    post_normalization_wal: DatabaseFileObservation,
    post_normalization_shm: DatabaseSidecarObservation,
    final_post_close_wal: DatabaseFileObservation,
    final_post_close_shm: DatabaseSidecarObservation,
) -> dict[str, object]:
    pre_semantic_document = _journal_preparation_semantic_identity_document(
        pre_semantic_identity
    )
    post_semantic_document = _journal_preparation_semantic_identity_document(
        post_semantic_identity
    )
    return {
        "authorization": authority.as_dict(),
        "authorization_consumption": {
            "durable_cross_process_enforcement": False,
            "host_operation_layer_must_consume_once": True,
            "max_attempts": authority.max_attempts,
        },
        "authorization_id": authority.authorization_id,
        "authorization_record_sha256": authority.authorization_record_sha256,
        "completed_at": completed_at,
        "evidence_format": _WAL_RECOVERY_EVIDENCE_FORMAT,
        "evidence_record_sha256_scope": _WAL_RECOVERY_EVIDENCE_HASH_SCOPE,
        "evidence_version": _WAL_RECOVERY_EVIDENCE_VERSION,
        "final_post_close_sidecars": {
            "shm": _sidecar_observation_document(final_post_close_shm),
            "wal": _file_observation_document(final_post_close_wal),
        },
        "intended_destination": str(authority.intended_destination),
        "listener_guard": {
            "acquired": True,
            "acquired_at": listener_guard_acquired_at,
            "released": True,
            "released_at": listener_guard_released_at,
        },
        "moa_checkpoint": authority.expected_moa_checkpoint,
        "normalization": {
            "checkpoint_mode": (
                "none-already-normalized"
                if truncate_checkpoint is None
                else "truncate"
            ),
            "truncate_checkpoint": (
                list(truncate_checkpoint)
                if truncate_checkpoint is not None
                else None
            ),
        },
        "post_normalization": {
            "main": _file_observation_document(normalized_main),
            "semantic_identity": post_semantic_document,
            "sidecars": {
                "shm": _sidecar_observation_document(post_normalization_shm),
                "wal": _file_observation_document(post_normalization_wal),
            },
        },
        "pre_lock": {
            "main": _file_observation_document(pre_lock_main),
            "sidecars": {
                "shm": _sidecar_observation_document(pre_lock_shm),
                "wal": _file_observation_document(pre_lock_wal),
            },
        },
        "pre_normalization_under_exclusive_ownership": {
            "journal_mode": pre_normalization_journal_mode,
            "main": _file_observation_document(locked_main),
            "semantic_identity": pre_semantic_document,
            "sidecars": {
                "shm": _sidecar_observation_document(locked_shm),
                "wal": _file_observation_document(locked_wal),
            },
        },
        "recovery_scope_declarations": {
            "activation_performed": False,
            "destination_created": False,
            "projection_performed": False,
            "relocation_performed": False,
            "reprojection_performed": False,
            "retention_performed": False,
            "source_retired_archived_or_tombstoned": False,
            "wal_or_shm_manually_unlinked": False,
        },
        "relocation_requirements_after_recovery": {
            "requires_fresh_bound_relocation_authorization": True,
            "requires_fresh_relocation_certification": True,
        },
        "run_id": run_id,
        "source": str(authority.source),
        "sqlite_runtime_version": sqlite_version,
        "sqlite_writer_exclusion": {
            "acquired": True,
            "acquired_at": sqlite_exclusive_acquired_at,
            "begin_mode": "exclusive",
            "busy_timeout_ms": _SOURCE_QUIESCENCE_BUSY_TIMEOUT_MS,
            "connection_locking_mode": "exclusive",
            "released": True,
            "released_at": sqlite_exclusive_released_at,
        },
        "started_at": started_at,
        "status": status,
        "validation": {
            "post_foreign_key_check": [list(row) for row in post_foreign_key_check],
            "post_integrity_check": list(post_integrity_check),
            "pre_foreign_key_check": [list(row) for row in pre_foreign_key_check],
            "pre_integrity_check": list(pre_integrity_check),
            "semantic_identity_unchanged": pre_semantic_document
            == post_semantic_document,
        },
    }


def _journal_mode_preparation_authority_document(
    authority: DatabaseRelocationJournalModePreparationAuthority,
) -> dict[str, object]:
    return {
        "authorization_id": authority.authorization_id,
        "expected_journal_mode": authority.expected_journal_mode,
        "expected_main": _file_observation_document(authority.expected_main),
        "expected_moa_checkpoint": authority.expected_moa_checkpoint,
        "expected_sidecars": {
            "journal": _file_observation_document(authority.expected_journal),
            "shm": _file_observation_document(authority.expected_shm),
            "wal": _file_observation_document(authority.expected_wal),
        },
        "intended_destination": str(authority.intended_destination),
        "listener_known_writers_stopped_attested": (
            authority.listener_known_writers_stopped_attested
        ),
        "source": str(authority.source),
    }


def _journal_mode_preparation_document(
    *,
    run_id: str,
    started_at: str,
    completed_at: str,
    authority: DatabaseRelocationJournalModePreparationAuthority,
    sqlite_version: str,
    exclusion_acquired_at: str,
    exclusion_released_at: str,
    pre_transition_main: DatabaseFileObservation,
    pre_transition_journal_mode: str,
    pre_transition_journal: DatabaseFileObservation,
    pre_transition_wal: DatabaseFileObservation,
    pre_transition_shm: DatabaseFileObservation,
    pre_semantic_identity: DatabaseRelocationAuthorizationIdentity,
    post_semantic_identity: DatabaseRelocationAuthorizationIdentity,
    pre_integrity_check: tuple[str, ...],
    pre_foreign_key_check: tuple[tuple[object, ...], ...],
    post_integrity_check: tuple[str, ...],
    post_foreign_key_check: tuple[tuple[object, ...], ...],
    returned_journal_mode: str,
    header_write_version: int,
    header_read_version: int,
    post_transition_main: DatabaseFileObservation,
    post_transition_journal: DatabaseFileObservation,
    post_transition_wal: DatabaseFileObservation,
    post_transition_shm: DatabaseFileObservation,
    final_post_close_journal: DatabaseFileObservation,
    final_post_close_wal: DatabaseFileObservation,
    final_post_close_shm: DatabaseFileObservation,
) -> dict[str, object]:
    pre_semantic_document = _journal_preparation_semantic_identity_document(
        pre_semantic_identity
    )
    post_semantic_document = _journal_preparation_semantic_identity_document(
        post_semantic_identity
    )
    return {
        "authorization_id": authority.authorization_id,
        "completed_at": completed_at,
        "evidence_format": _JOURNAL_MODE_PREPARATION_FORMAT,
        "evidence_record_sha256_scope": _JOURNAL_MODE_PREPARATION_HASH_SCOPE,
        "evidence_version": _JOURNAL_MODE_PREPARATION_VERSION,
        "expected_preparation_authority": (
            _journal_mode_preparation_authority_document(authority)
        ),
        "final_post_close_sidecars": {
            "journal": _file_observation_document(final_post_close_journal),
            "shm": _file_observation_document(final_post_close_shm),
            "wal": _file_observation_document(final_post_close_wal),
        },
        "intended_destination": str(authority.intended_destination),
        "listener_guard": {"acquired": True, "released": True},
        "listener_known_writers_stopped_attested": (
            authority.listener_known_writers_stopped_attested
        ),
        "moa_checkpoint": authority.expected_moa_checkpoint,
        "post_transition": {
            "header_read_version": header_read_version,
            "header_write_version": header_write_version,
            "main": _file_observation_document(post_transition_main),
            "semantic_identity": post_semantic_document,
            "sidecars": {
                "journal": _file_observation_document(post_transition_journal),
                "shm": _file_observation_document(post_transition_shm),
                "wal": _file_observation_document(post_transition_wal),
            },
        },
        "pre_transition": {
            "journal_mode": pre_transition_journal_mode,
            "main": _file_observation_document(pre_transition_main),
            "semantic_identity": pre_semantic_document,
            "sidecars": {
                "journal": _file_observation_document(pre_transition_journal),
                "shm": _file_observation_document(pre_transition_shm),
                "wal": _file_observation_document(pre_transition_wal),
            },
        },
        "run_id": run_id,
        "source": str(authority.source),
        "sqlite_runtime_version": sqlite_version,
        "sqlite_writer_exclusion": {
            "acquired": True,
            "acquired_at": exclusion_acquired_at,
            "begin_mode": "exclusive",
            "busy_timeout_ms": _SOURCE_QUIESCENCE_BUSY_TIMEOUT_MS,
            "connection_locking_mode": "exclusive",
            "released": True,
            "released_at": exclusion_released_at,
        },
        "started_at": started_at,
        "status": "completed",
        "transition": {
            "requested_journal_mode": "wal",
            "returned_journal_mode": returned_journal_mode,
        },
        "validation": {
            "post_foreign_key_check": [list(row) for row in post_foreign_key_check],
            "post_integrity_check": list(post_integrity_check),
            "pre_foreign_key_check": [list(row) for row in pre_foreign_key_check],
            "pre_integrity_check": list(pre_integrity_check),
            "semantic_identity_unchanged": pre_semantic_document
            == post_semantic_document,
        },
    }


def _journal_mode_preparation_failure_document(
    *,
    run_id: str,
    started_at: str,
    completed_at: str,
    authority: DatabaseRelocationJournalModePreparationAuthority,
    sqlite_version: str,
    exclusion_acquired_at: str | None,
    exclusion_released_at: str | None,
    returned_journal_mode: str | None,
    failure: BaseException,
    pre_transition_main: DatabaseFileObservation,
    pre_transition_journal_mode: str,
    pre_transition_journal: DatabaseFileObservation,
    pre_transition_wal: DatabaseFileObservation,
    pre_transition_shm: DatabaseFileObservation,
    pre_semantic_identity: DatabaseRelocationAuthorizationIdentity,
    post_semantic_identity: DatabaseRelocationAuthorizationIdentity | None,
    pre_integrity_check: tuple[str, ...],
    pre_foreign_key_check: tuple[tuple[object, ...], ...],
    post_integrity_check: tuple[str, ...] | None,
    post_foreign_key_check: tuple[tuple[object, ...], ...] | None,
    listener_guard_released: bool,
    final_journal: DatabaseFileObservation | None,
    final_wal: DatabaseFileObservation | None,
    final_shm: DatabaseFileObservation | None,
) -> dict[str, object]:
    final_main = _observe_database_file(authority.source)
    header_write_version, header_read_version = _database_header_versions(
        authority.source
    )
    pre_semantic_document = _journal_preparation_semantic_identity_document(
        pre_semantic_identity
    )
    post_semantic_document = (
        _journal_preparation_semantic_identity_document(post_semantic_identity)
        if post_semantic_identity is not None
        else None
    )
    return {
        "authorization_id": authority.authorization_id,
        "completed_at": completed_at,
        "evidence_format": _JOURNAL_MODE_PREPARATION_FORMAT,
        "evidence_record_sha256_scope": _JOURNAL_MODE_PREPARATION_HASH_SCOPE,
        "evidence_version": _JOURNAL_MODE_PREPARATION_VERSION,
        "expected_preparation_authority": (
            _journal_mode_preparation_authority_document(authority)
        ),
        "failure": {
            "reason": str(failure),
            "type": type(failure).__name__,
        },
        "final_observed_state": {
            "header_read_version": header_read_version,
            "header_write_version": header_write_version,
            "main": _file_observation_document(final_main),
            "sidecars": {
                "journal": _optional_file_observation_document(final_journal),
                "shm": _optional_file_observation_document(final_shm),
                "wal": _optional_file_observation_document(final_wal),
            },
        },
        "intended_destination": str(authority.intended_destination),
        "listener_guard": {"acquired": True, "released": listener_guard_released},
        "listener_known_writers_stopped_attested": (
            authority.listener_known_writers_stopped_attested
        ),
        "moa_checkpoint": authority.expected_moa_checkpoint,
        "pre_transition": {
            "journal_mode": pre_transition_journal_mode,
            "main": _file_observation_document(pre_transition_main),
            "semantic_identity": pre_semantic_document,
            "sidecars": {
                "journal": _file_observation_document(pre_transition_journal),
                "shm": _file_observation_document(pre_transition_shm),
                "wal": _file_observation_document(pre_transition_wal),
            },
        },
        "run_id": run_id,
        "source": str(authority.source),
        "sqlite_runtime_version": sqlite_version,
        "sqlite_writer_exclusion": {
            "acquired": exclusion_acquired_at is not None,
            "acquired_at": exclusion_acquired_at,
            "begin_mode": "exclusive",
            "busy_timeout_ms": _SOURCE_QUIESCENCE_BUSY_TIMEOUT_MS,
            "connection_locking_mode": "exclusive",
            "released": exclusion_released_at is not None,
            "released_at": exclusion_released_at,
        },
        "started_at": started_at,
        "status": "failed",
        "transition": {
            "automatic_reversal_attempted": False,
            "requested_journal_mode": "wal",
            "returned_journal_mode": returned_journal_mode,
        },
        "validation": {
            "post_foreign_key_check": (
                [list(row) for row in post_foreign_key_check]
                if post_foreign_key_check is not None
                else None
            ),
            "post_integrity_check": (
                list(post_integrity_check)
                if post_integrity_check is not None
                else None
            ),
            "post_semantic_identity": post_semantic_document,
            "pre_foreign_key_check": [list(row) for row in pre_foreign_key_check],
            "pre_integrity_check": list(pre_integrity_check),
            "semantic_identity_unchanged": (
                pre_semantic_document == post_semantic_document
                if post_semantic_document is not None
                else None
            ),
        },
    }


def _later_bound_relocation_argv(
    source: Path, identity: DatabaseRelocationAuthorizationIdentity
) -> list[str]:
    argv = [
        "catalog",
        "relocate-database",
        str(source),
        "--authorization-bound",
        "--expected-normalized-sha256",
        identity.normalized_sha256,
        "--expected-normalized-size",
        str(identity.normalized_size),
    ]
    for version, name in identity.migration_identity:
        argv.extend(("--expected-migration-version", str(version)))
        argv.extend(("--expected-migration-name", name))
    current_generation_id: int | None = None
    for generation_id, is_current in identity.generation_inventory:
        argv.extend(("--expected-generation-id", str(generation_id)))
        if is_current:
            current_generation_id = generation_id
    assert current_generation_id is not None
    argv.extend(
        (
            "--expected-current-generation-id",
            str(current_generation_id),
            "--expected-source-event-count",
            str(identity.source_event_count),
            "--expected-generation-1-projection-link-count",
            str(identity.generation_1_projection_link_count),
        )
    )
    projection_gap_fields = (
        ("--expected-projection-gap-check-id", "check_id"),
        ("--expected-projection-gap-category", "category"),
        ("--expected-projection-gap-entity", "entity"),
        ("--expected-projection-gap-local-identifier-kind", "local_identifier_kind"),
        ("--expected-projection-gap-local-identifier", "local_identifier"),
        ("--expected-projection-gap-reason", "reason"),
    )
    projection_gap_documents = _authorization_identity_document(identity)["projection_gaps"]
    assert isinstance(projection_gap_documents, list)
    for option, field in projection_gap_fields:
        for finding in projection_gap_documents:
            assert isinstance(finding, dict)
            argv.extend((option, str(finding[field])))
    argv.extend(
        (
            "--expected-retained-source-preflight-fingerprint",
            identity.retained_source_preflight_fingerprint,
            "--apply",
        )
    )
    return argv


def _certification_document(
    *,
    run_id: str,
    certified_at: str,
    source: Path,
    destination: Path,
    moa_checkpoint: str,
    identity: DatabaseRelocationAuthorizationIdentity,
    truncate_checkpoint: tuple[int, int, int],
    passive_checkpoint: tuple[int, int, int],
    exclusion_acquired_at: str,
    exclusion_released_at: str,
    integrity_check: tuple[str, ...],
    foreign_key_check: tuple[tuple[object, ...], ...],
    listener_known_writers_stopped_attested: bool,
    pre_normalization_main: DatabaseFileObservation,
    pre_normalization_wal: DatabaseFileObservation,
    pre_normalization_shm: DatabaseFileObservation,
) -> dict[str, object]:
    return {
        "authorization_identity": _authorization_identity_document(identity),
        "certification_format": _CERTIFICATION_FORMAT,
        "certification_record_sha256_scope": _CERTIFICATION_HASH_SCOPE,
        "certification_version": _CERTIFICATION_VERSION,
        "certified_at": certified_at,
        "destination": str(destination),
        "later_bound_relocation_argv": _later_bound_relocation_argv(source, identity),
        "listener_known_writers_stopped_attested": (
            listener_known_writers_stopped_attested
        ),
        "moa_checkpoint": moa_checkpoint,
        "normalization": {
            "passive_checkpoint": list(passive_checkpoint),
            "truncate_checkpoint": list(truncate_checkpoint),
        },
        "post_normalization_main": {
            "sha256": identity.normalized_sha256,
            "size": identity.normalized_size,
        },
        "pre_normalization": {
            "main": _file_observation_document(pre_normalization_main),
            "shm": _file_observation_document(pre_normalization_shm),
            "wal": _file_observation_document(pre_normalization_wal),
        },
        "run_id": run_id,
        "source": str(source),
        "sqlite_writer_exclusion": {
            "acquired": True,
            "acquired_at": exclusion_acquired_at,
            "released": True,
            "released_at": exclusion_released_at,
        },
        "validation": {
            "foreign_key_check": [list(row) for row in foreign_key_check],
            "integrity_check": list(integrity_check),
        },
    }


def _normalized_database_file_identity(path: Path) -> tuple[str, int]:
    with path.open("rb") as database_file:
        digest = file_digest(database_file, "sha256").hexdigest()
        size = os.fstat(database_file.fileno()).st_size
    return digest, size


def _compute_relocation_authorization_identity(
    connection: sqlite3.Connection,
    source: Path,
) -> DatabaseRelocationAuthorizationIdentity:
    if not connection.in_transaction:
        raise DatabaseRelocationError(
            "RELOCATION_AUTHORIZATION_BLOCKER: source writer exclusion is not held."
        )
    try:
        normalized_sha256, normalized_size = _normalized_database_file_identity(source)
        migration_identity = tuple(
            (int(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall()
        )
        generation_rows = connection.execute(
            "SELECT id, is_current FROM projection_generations ORDER BY id"
        ).fetchall()
        generation_inventory = tuple(
            (int(row[0]), bool(int(row[1]))) for row in generation_rows
        )
        if (
            not generation_inventory
            or any(int(row[1]) not in (0, 1) for row in generation_rows)
            or tuple(row[0] for row in generation_inventory)
            != tuple(sorted({row[0] for row in generation_inventory}))
            or sum(row[1] for row in generation_inventory) != 1
        ):
            raise ValueError("invalid projection-generation inventory")
        source_event_count = _table_count(connection, "discord_source_events")
        generation_1_projection_link_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM discord_projection_links WHERE generation_id = 1"
            ).fetchone()[0]
        )
        projection_gaps = DataHealthRepository(connection).scan_projection_gaps()
        preflight = RetainedSourceReprojectionPreflightService().preflight(connection)
        current_generation_ids = tuple(
            generation_id
            for generation_id, is_current in generation_inventory
            if is_current
        )
        if preflight.current_generation_id != current_generation_ids[0]:
            raise ValueError("preflight current generation does not match storage")
        return DatabaseRelocationAuthorizationIdentity(
            normalized_sha256=normalized_sha256,
            normalized_size=normalized_size,
            migration_identity=migration_identity,
            generation_inventory=generation_inventory,
            source_event_count=source_event_count,
            generation_1_projection_link_count=generation_1_projection_link_count,
            projection_gaps=projection_gaps,
            retained_source_preflight_fingerprint=preflight.inventory_fingerprint,
        )
    except DatabaseRelocationError:
        raise
    except (OSError, sqlite3.Error, TypeError, ValueError, RuntimeError, KeyError) as error:
        raise DatabaseRelocationError(
            "RELOCATION_AUTHORIZATION_BLOCKER: normalized source identity is unavailable "
            "or malformed."
        ) from error


def _require_matching_authorization_identity(
    expected: DatabaseRelocationAuthorizationIdentity,
    actual: DatabaseRelocationAuthorizationIdentity,
) -> None:
    fields = (
        ("normalized main-file SHA-256", expected.normalized_sha256, actual.normalized_sha256),
        ("normalized main-file size", expected.normalized_size, actual.normalized_size),
        ("migration tuples", expected.migration_identity, actual.migration_identity),
        ("projection-generation inventory", expected.generation_inventory, actual.generation_inventory),
        ("source-event count", expected.source_event_count, actual.source_event_count),
        (
            "generation-1 projection-link count",
            expected.generation_1_projection_link_count,
            actual.generation_1_projection_link_count,
        ),
        ("projection-gap result", expected.projection_gaps, actual.projection_gaps),
        (
            "retained-source readiness fingerprint",
            expected.retained_source_preflight_fingerprint,
            actual.retained_source_preflight_fingerprint,
        ),
    )
    mismatches = tuple(label for label, expected_value, actual_value in fields if expected_value != actual_value)
    if mismatches:
        raise DatabaseRelocationError(
            "RELOCATION_AUTHORIZATION_MISMATCH: " + ", ".join(mismatches) + "."
        )


def _validate_database(path: Path) -> _DatabaseFingerprint:
    try:
        connection = _open_read_only(path)
        try:
            return _validate_database_connection(connection, path)
        finally:
            connection.close()
    except DatabaseRelocationError:
        raise
    except sqlite3.Error as error:
        raise DatabaseRelocationError(
            f"Could not validate MOA SQLite database {path}: {error}"
        ) from error


def _validate_database_connection(
    connection: sqlite3.Connection,
    path: Path,
) -> _DatabaseFingerprint:
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
    table_counts = tuple((name, _table_count(connection, name)) for name in table_names)
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


def _validate_held_database(
    connection: sqlite3.Connection,
    path: Path,
) -> _DatabaseFingerprint:
    try:
        return _validate_database_connection(connection, path)
    except DatabaseRelocationError:
        raise
    except sqlite3.Error as error:
        raise DatabaseRelocationError(
            f"Could not validate MOA SQLite database {path}: {error}"
        ) from error


def _validate_held_database_with_evidence(
    connection: sqlite3.Connection,
    path: Path,
) -> tuple[_DatabaseFingerprint, tuple[str, ...], tuple[tuple[object, ...], ...]]:
    fingerprint = _validate_held_database(connection, path)
    try:
        foreign_key_rows = tuple(
            tuple(row) for row in connection.execute("PRAGMA foreign_key_check").fetchall()
        )
    except sqlite3.Error as error:
        raise DatabaseRelocationError(
            f"Could not validate MOA SQLite foreign keys for {path}: {error}"
        ) from error
    if foreign_key_rows:
        raise DatabaseRelocationError(
            f"SQLite foreign-key validation failed for {path}."
        )
    return fingerprint, ("ok",), foreign_key_rows


def _table_count(connection: sqlite3.Connection, table_name: str) -> int:
    quoted_name = table_name.replace('"', '""')
    return int(connection.execute(f'SELECT COUNT(*) FROM "{quoted_name}"').fetchone()[0])


def _database_file_digest(path: Path) -> bytes:
    with path.open("rb") as database_file:
        return file_digest(database_file, "sha256").digest()


def _backup_image_digest(source: Path) -> bytes:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{source.name}.certifying-",
        dir=source.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        _backup_database(source, temporary_path)
        _checkpoint_source_for_retirement(temporary_path)
        _remove_checkpointed_sidecars(temporary_path)
        digest = _database_file_digest(temporary_path)
    except BaseException as error:
        _cleanup_temporary_path(temporary_path, error)
        raise
    try:
        for cleanup_path in (
            temporary_path,
            Path(f"{temporary_path}-wal"),
            Path(f"{temporary_path}-shm"),
        ):
            cleanup_path.unlink(missing_ok=True)
    except OSError as error:
        raise DatabaseRelocationError(
            f"Could not remove source certification temporary file {cleanup_path}: {error}"
        ) from error
    return digest


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
    incomplete_marker = archive_directory / _INCOMPLETE_RETIREMENT_MARKER_NAME
    try:
        incomplete_marker.write_text(
            "Legacy database retirement did not complete.\n", encoding="utf-8"
        )
    except OSError as error:
        try:
            archive_directory.rmdir()
        except BaseException as cleanup_error:
            error.add_note(
                f"Could not remove incomplete archive directory {archive_directory}: "
                f"{cleanup_error}"
            )
        raise DatabaseRelocationError(
            f"Could not establish fail-closed source retirement marker {incomplete_marker}: "
            f"{error}"
        ) from error
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
        if all(original.exists() for original, _archived_path in moved_paths):
            try:
                incomplete_marker.unlink()
                archive_directory.rmdir()
            except BaseException as cleanup_error:
                error.add_note(
                    f"Could not remove incomplete archive directory {archive_directory}: "
                    f"{cleanup_error}"
                )
        raise
    return archive


def _complete_source_retirement(archive: Path) -> None:
    incomplete_marker = archive.parent / _INCOMPLETE_RETIREMENT_MARKER_NAME
    try:
        incomplete_marker.unlink()
    except OSError as error:
        raise DatabaseRelocationError(
            "Source files were archived, but the fail-closed incomplete-retirement marker "
            f"could not be cleared: {incomplete_marker}"
        ) from error


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
