import gc
import json
import multiprocessing
import os
import sqlite3
import sys
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from moa.cli.catalog_relocate_database_commands import (
    register_catalog_relocate_database_command,
)
from moa.database import legacy_database_relocation, sqlite
from moa.database.legacy_database_relocation import (
    DatabaseFileObservation,
    DatabaseRelocationAuthorizationIdentity,
    DatabaseRelocationError,
    DatabaseRelocationJournalModePreparationAuthority,
    DatabaseRelocationJournalModePreparationError,
    DatabaseSidecarObservation,
    DatabaseWalRecoveryAuthority,
    LegacyDatabaseAuthorityConflictError,
    LegacyDatabaseRelocationRequiredError,
    certify_database_relocation_identity,
    prepare_database_relocation_journal_mode,
    recover_database_wal,
    relocate_database,
    relocate_database_with_authorization,
)
from moa.models.data_health import DataHealthFinding
from moa.repositories.catalog_repository import CatalogRepository
from moa.services.listener_process_guard import ListenerProcessGuard


CHECKPOINT = "eabe16c0fcf0e61f6b1accf1cfa8afcd82955527"


def _commit_import_event_process(
    database: str,
    operation: str,
    message: str,
    start,
    connected,
    committed,
    allow_close,
    closed,
    result_queue,
) -> None:
    connection = None
    try:
        if not start.wait(10):
            raise RuntimeError("writer start event was not signaled")
        connection = sqlite3.connect(database, timeout=10)
        connection.execute("PRAGMA busy_timeout = 10000")
        connected.set()
        connection.execute("BEGIN IMMEDIATE")
        if operation == "insert":
            connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) "
                "VALUES ('command_observation', 'process-writer', "
                "'2026-08-12T00:00:00+00:00', ?)",
                (message,),
            )
        elif operation == "update":
            connection.execute(
                "UPDATE import_events SET raw_message = ? WHERE source = 'second-row'",
                (message,),
            )
        else:
            raise ValueError(f"unsupported writer operation: {operation}")
        connection.commit()
        committed.set()
        if not allow_close.wait(10):
            raise RuntimeError("writer close event was not signaled")
        result_queue.put(("committed", None))
    except BaseException as error:
        result_queue.put(("error", repr(error)))
    finally:
        if connection is not None:
            connection.close()
        closed.set()


def _hold_writer_transaction_process(database: str, holding, release, result_queue) -> None:
    connection = None
    try:
        connection = sqlite3.connect(database, timeout=10)
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) "
            "VALUES ('command_observation', 'holding-writer', "
            "'2026-08-12T00:00:00+00:00', 'pending')"
        )
        holding.set()
        if not release.wait(10):
            raise RuntimeError("holding writer release event was not signaled")
        connection.rollback()
        result_queue.put(("rolled-back", None))
    except BaseException as error:
        result_queue.put(("error", repr(error)))
    finally:
        if connection is not None:
            connection.close()


def _create_fresh_legacy_database_process(database: str, start, finished, result_queue) -> None:
    connection = None
    try:
        if not start.wait(10):
            raise RuntimeError("legacy writer start event was not signaled")
        connection = sqlite3.connect(database, timeout=10)
        connection.execute(
            "CREATE TABLE competing_legacy_write (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO competing_legacy_write (value) VALUES ('competing authority')"
        )
        connection.commit()
        result_queue.put(("committed", None))
    except BaseException as error:
        result_queue.put(("error", repr(error)))
    finally:
        if connection is not None:
            connection.close()
        finished.set()


def _leave_committed_wal_process(database: str, committed) -> None:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_autocheckpoint = 0")
    connection.execute(
        "INSERT INTO import_events (kind, source, observed_at, raw_message) "
        "VALUES ('command_observation', 'wal-recovery-fixture', "
        "'2026-09-04T00:00:00+00:00', 'committed only in WAL')"
    )
    connection.commit()
    committed.set()
    os._exit(0)


def _join_process(process: multiprocessing.Process) -> None:
    process.join(10)
    if process.is_alive():
        process.terminate()
        process.join(10)
        pytest.fail("test worker process did not stop")
    assert process.exitcode == 0


def _configure_checkout(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    checkout = tmp_path / "checkout"
    source_file = checkout / "src" / "moa" / "database" / "legacy_database_relocation.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("# synthetic source", encoding="utf-8")
    (source_file.parent / "sqlite.py").write_text("# synthetic sqlite", encoding="utf-8")
    (checkout / "pyproject.toml").write_text("[project]", encoding="utf-8")
    (checkout / ".git").mkdir()
    monkeypatch.setattr(legacy_database_relocation, "_source_file_path", lambda: source_file)
    monkeypatch.setattr(
        legacy_database_relocation,
        "_verified_moa_checkout_checkpoint",
        lambda: CHECKPOINT,
    )
    return checkout, checkout / "data" / "database" / "moa.db"


def _create_moa_database(path: Path, *, message: str = "representative content") -> None:
    CatalogRepository(path)
    connection = sqlite.connect(path)
    try:
        connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) "
            "VALUES (?, ?, ?, ?)",
            ("command_observation", "test", "2026-08-12T00:00:00+00:00", message),
        )
        connection.commit()
    finally:
        connection.close()
    gc.collect()


def _create_rollback_mode_moa_database(path: Path) -> None:
    _create_moa_database(path)
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (
            0,
            0,
            0,
        )
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == (
            "delete",
        )
    finally:
        connection.close()
    gc.collect()


def _preparation_authority(
    source: Path,
    destination: Path,
    *,
    attested: bool = True,
) -> DatabaseRelocationJournalModePreparationAuthority:
    connection = sqlite3.connect(source)
    try:
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
    finally:
        connection.close()
    return DatabaseRelocationJournalModePreparationAuthority(
        source=source.resolve(strict=False),
        intended_destination=destination.resolve(strict=False),
        expected_moa_checkpoint=CHECKPOINT,
        expected_main=legacy_database_relocation._observe_database_file(source),
        expected_journal_mode=journal_mode,
        expected_journal=legacy_database_relocation._observe_database_file(
            Path(f"{source}-journal")
        ),
        expected_wal=legacy_database_relocation._observe_database_file(
            Path(f"{source}-wal")
        ),
        expected_shm=legacy_database_relocation._observe_database_file(
            Path(f"{source}-shm")
        ),
        listener_known_writers_stopped_attested=attested,
        authorization_id="approved-preparation-run-001",
    )


def _recovery_authority(
    source: Path,
    destination: Path,
    *,
    authorization_id: str = "approved-wal-recovery-001",
) -> DatabaseWalRecoveryAuthority:
    authority = DatabaseWalRecoveryAuthority(
        authorization_format="moa-database-wal-recovery-authorization",
        authorization_version=1,
        action="AUTHORIZE_ONE_DATABASE_WAL_RECOVERY_ATTEMPT",
        authorization_id=authorization_id,
        source=source.resolve(strict=False),
        intended_destination=destination.resolve(strict=False),
        expected_moa_checkpoint=CHECKPOINT,
        expected_main=legacy_database_relocation._observe_database_file(source),
        expected_wal=legacy_database_relocation._observe_database_file(
            Path(f"{source}-wal")
        ),
        expected_shm=legacy_database_relocation._observe_sidecar_presence(
            Path(f"{source}-shm")
        ),
        expected_journal_mode="wal",
        listener_known_writers_stopped_attested=True,
        max_attempts=1,
        authorization_record_sha256="0" * 64,
    )
    digest = sha256(
        legacy_database_relocation._canonical_json(
            legacy_database_relocation._wal_recovery_authority_document(authority)
        ).encode("ascii")
    ).hexdigest()
    return replace(authority, authorization_record_sha256=digest)


def _create_crash_left_wal(source: Path) -> None:
    _create_moa_database(source)
    context = multiprocessing.get_context("spawn")
    committed = context.Event()
    process = context.Process(
        target=_leave_committed_wal_process,
        args=(str(source), committed),
    )
    process.start()
    assert committed.wait(10)
    _join_process(process)
    assert Path(f"{source}-wal").stat().st_size > 0


def _recovery_cli_arguments(
    authority: DatabaseWalRecoveryAuthority,
) -> list[str]:
    arguments = [
        "recover-database-wal",
        str(authority.source),
        "--expected-destination",
        str(authority.intended_destination),
        "--expected-moa-checkpoint",
        authority.expected_moa_checkpoint,
        "--authorization-format",
        authority.authorization_format,
        "--authorization-version",
        str(authority.authorization_version),
        "--authorization-action",
        authority.action,
        "--authorization-id",
        authority.authorization_id,
        "--authorization-sha256",
        authority.authorization_record_sha256,
        "--max-attempts",
        str(authority.max_attempts),
        "--expected-main-sha256",
        str(authority.expected_main.sha256),
        "--expected-main-size",
        str(authority.expected_main.size),
        "--expected-wal-state",
        "present" if authority.expected_wal.present else "absent",
        "--expected-shm-state",
        "present" if authority.expected_shm.present else "absent",
        "--expected-journal-mode",
        authority.expected_journal_mode,
        "--listener-known-writers-stopped",
        "--apply",
    ]
    if authority.expected_wal.present:
        arguments.extend(
            (
                "--expected-wal-size",
                str(authority.expected_wal.size),
                "--expected-wal-sha256",
                str(authority.expected_wal.sha256),
            )
        )
    if authority.expected_shm.present:
        arguments.extend(("--expected-shm-size", str(authority.expected_shm.size)))
    return arguments


def _authorization_identity(source: Path) -> DatabaseRelocationAuthorizationIdentity:
    legacy_database_relocation._checkpoint_source_for_retirement(source)
    connection = legacy_database_relocation._acquire_authorization_exclusion(source)
    primary_error = None
    try:
        legacy_database_relocation._checkpoint_source_passive_under_exclusion(source)
        return legacy_database_relocation._compute_relocation_authorization_identity(
            connection, source
        )
    except BaseException as error:
        primary_error = error
        raise
    finally:
        legacy_database_relocation._release_source_quiescence(
            connection, primary_error=primary_error
        )


def _assert_no_authorization_failure_output(source: Path, target: Path) -> None:
    assert source.is_file()
    assert not target.exists()
    assert not target.parent.exists()
    assert list(source.parent.glob(f"{source.name}.migrated-backup-*")) == []


def _assert_valid_tombstone(source: Path, target: Path, archive: Path) -> None:
    tombstone = legacy_database_relocation._validate_retirement_tombstone(
        source,
        target,
        expected_archive=archive,
    )
    assert source.is_dir()
    assert (source / ".moa-relocated").is_file()
    assert tombstone.version == 2
    assert tombstone.target == target.resolve()
    assert tombstone.archive == archive.resolve()
    assert tombstone.archive_sha256 == sha256(archive.read_bytes()).hexdigest()
    assert tombstone.archive_size == archive.stat().st_size


def _retirement_marker_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, dict[str, object]]:
    source = tmp_path / "checkout" / "data" / "database" / "moa.db"
    source.mkdir(parents=True)
    target = tmp_path / "user-data" / "moa.db"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"current destination")
    archive = source.with_name("moa.db.migrated-backup-test") / "moa.db"
    archive.parent.mkdir()
    archive.write_bytes(b"final archived database")
    marker = source / ".moa-relocated"
    payload: dict[str, object] = {
        "format": "moa-legacy-database-retirement",
        "version": 2,
        "target": str(target.resolve(strict=False)),
        "archive": str(archive.resolve(strict=False)),
        "archiveSha256": sha256(archive.read_bytes()).hexdigest(),
        "archiveSize": archive.stat().st_size,
    }
    marker.write_text(json.dumps(payload), encoding="utf-8")
    return source, target, archive, marker, payload


def test_wal_recovery_authority_is_strict_bound_and_not_relocation_authority(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    destination = tmp_path / "absent" / "moa.db"
    authority = _recovery_authority(source, destination)

    assert authority.as_dict()["action"] == (
        "AUTHORIZE_ONE_DATABASE_WAL_RECOVERY_ATTEMPT"
    )
    assert authority.as_dict()["max_attempts"] == 1
    with pytest.raises(DatabaseRelocationError, match="invalid action"):
        invalid_action = replace(
            authority,
            action="AUTHORIZE_ONE_DATABASE_RELOCATION_ATTEMPT",
            authorization_record_sha256="0" * 64,
        )
        recover_database_wal(invalid_action)
    with pytest.raises(DatabaseRelocationError, match="typed recovery-only"):
        recover_database_wal(_authorization_identity(source))  # type: ignore[arg-type]
    with pytest.raises(DatabaseRelocationError, match="invalid typed representation"):
        relocate_database_with_authorization(
            source,
            destination,
            authority,  # type: ignore[arg-type]
        )


def test_wal_recovery_rejects_digest_path_and_destination_mismatches_before_guard(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    destination = tmp_path / "absent" / "moa.db"
    authority = _recovery_authority(source, destination)

    with pytest.raises(DatabaseRelocationError, match="authorization digest"):
        recover_database_wal(
            replace(authority, authorization_record_sha256="1" * 64)
        )

    (source.parent / "nested").mkdir()
    noncanonical = replace(
        authority,
        source=source.parent / "nested" / ".." / source.name,
        authorization_record_sha256="0" * 64,
    )
    noncanonical = replace(
        noncanonical,
        authorization_record_sha256=sha256(
            legacy_database_relocation._canonical_json(
                legacy_database_relocation._wal_recovery_authority_document(
                    noncanonical
                )
            ).encode("ascii")
        ).hexdigest(),
    )
    with pytest.raises(DatabaseRelocationError, match="canonical paths"):
        recover_database_wal(noncanonical)

    destination.parent.mkdir()
    destination.write_bytes(b"must remain untouched")
    with pytest.raises(DatabaseRelocationError, match="must remain absent"):
        recover_database_wal(authority)
    assert destination.read_bytes() == b"must remain untouched"


def test_wal_recovery_normalizes_committed_wal_visible_data_and_emits_evidence(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_crash_left_wal(source)
    destination = tmp_path / "destination-never-created" / "moa.db"
    authority = _recovery_authority(source, destination)
    pre_main = authority.expected_main
    stages: list[str] = []
    original_unlink = Path.unlink

    def reject_manual_sidecar_unlink(path: Path, *args, **kwargs):
        if path in (Path(f"{source}-wal"), Path(f"{source}-shm")):
            raise AssertionError("recovery must never manually unlink source sidecars")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", reject_manual_sidecar_unlink)

    result = recover_database_wal(authority, _test_hook=stages.append)

    assert result.status == "RECOVERY_COMPLETED"
    assert result.truncate_checkpoint == (0, 0, 0)
    assert result.normalized_main.present
    assert (
        result.normalized_main.sha256 != pre_main.sha256
        or result.normalized_main.size != pre_main.size
    )
    assert stages == [
        "LISTENER_GUARD_ACQUIRED",
        "SQLITE_EXCLUSIVE_ACQUIRED",
        "PRE_SEMANTIC_VALIDATED",
        "TRUNCATE_CHECKPOINT_COMPLETED",
        "RECOVERY_VALIDATED",
    ]
    assert not destination.exists()
    assert not destination.parent.exists()
    assert list(source.parent.glob(f"{source.name}.migrated-backup-*")) == []
    assert source.is_file()
    with sqlite3.connect(source) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM import_events WHERE raw_message = ?",
            ("committed only in WAL",),
        ).fetchone()[0] == 1

    document = result.as_dict()
    digest = document.pop("evidence_record_sha256")
    assert digest == sha256(
        legacy_database_relocation._canonical_json(document).encode("ascii")
    ).hexdigest()
    assert result.to_json() == json.dumps(
        result.as_dict(), ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    assert document["validation"]["semantic_identity_unchanged"] is True
    assert document["pre_normalization_under_exclusive_ownership"][
        "semantic_identity"
    ] == document["post_normalization"]["semantic_identity"]
    assert "sha256" not in document["pre_lock"]["sidecars"]["shm"]
    assert "sha256" not in document["post_normalization"]["sidecars"]["shm"]
    assert "sha256" not in document["final_post_close_sidecars"]["shm"]
    assert document["recovery_scope_declarations"] == {
        "activation_performed": False,
        "destination_created": False,
        "projection_performed": False,
        "relocation_performed": False,
        "reprojection_performed": False,
        "retention_performed": False,
        "source_retired_archived_or_tombstoned": False,
        "wal_or_shm_manually_unlinked": False,
    }
    assert document["relocation_requirements_after_recovery"] == {
        "requires_fresh_bound_relocation_authorization": True,
        "requires_fresh_relocation_certification": True,
    }
    assert document["authorization_consumption"] == {
        "durable_cross_process_enforcement": False,
        "host_operation_layer_must_consume_once": True,
        "max_attempts": 1,
    }


def test_wal_recovery_already_normalized_is_explicit_and_skips_checkpoint(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    destination = tmp_path / "absent" / "moa.db"
    authority = _recovery_authority(source, destination)

    def forbidden_checkpoint(_connection):
        raise AssertionError("already-normalized recovery must not checkpoint")

    monkeypatch.setattr(
        legacy_database_relocation,
        "_checkpoint_wal_truncate_on_owned_connection",
        forbidden_checkpoint,
    )
    result = recover_database_wal(authority)

    assert result.status == "NO_ACTION_ALREADY_NORMALIZED"
    assert result.truncate_checkpoint is None
    assert result.as_dict()["normalization"] == {
        "checkpoint_mode": "none-already-normalized",
        "truncate_checkpoint": None,
    }
    assert not destination.exists()


def test_wal_recovery_cli_requires_bound_authority_and_renders_canonical_evidence(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_crash_left_wal(source)
    destination = tmp_path / "absent" / "moa.db"
    authority = _recovery_authority(source, destination)
    app = typer.Typer()
    register_catalog_relocate_database_command(
        app, Console(), lambda: destination
    )

    result = CliRunner().invoke(app, _recovery_cli_arguments(authority))

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert result.stdout.strip() == json.dumps(
        document, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    assert document["status"] == "RECOVERY_COMPLETED"
    assert document["authorization"]["action"] == (
        "AUTHORIZE_ONE_DATABASE_WAL_RECOVERY_ATTEMPT"
    )
    assert not destination.exists()


def test_wal_recovery_listener_guard_precedes_sqlite_and_exclusive_blocks_access(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_crash_left_wal(source)
    destination = tmp_path / "absent" / "moa.db"
    authority = _recovery_authority(source, destination)
    real_open = legacy_database_relocation._open_neutral_source_connection

    def checked_open(path: Path):
        competing_guard = ListenerProcessGuard(path)
        with pytest.raises(Exception, match="already running|owns database|lock"):
            competing_guard.acquire()
        return real_open(path)

    def assert_exclusive(stage: str) -> None:
        if stage != "SQLITE_EXCLUSIVE_ACQUIRED":
            return
        reader = sqlite3.connect(source, timeout=0.05)
        reader.execute("PRAGMA busy_timeout = 50")
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                reader.execute("SELECT COUNT(*) FROM import_events").fetchone()
        finally:
            reader.close()
        writer = sqlite3.connect(source, timeout=0.05)
        writer.execute("PRAGMA busy_timeout = 50")
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                writer.execute("BEGIN IMMEDIATE")
        finally:
            writer.close()

    monkeypatch.setattr(
        legacy_database_relocation, "_open_neutral_source_connection", checked_open
    )
    recover_database_wal(authority, _test_hook=assert_exclusive)


def test_wal_recovery_semantic_drift_and_checkpoint_failure_fail_closed_and_release(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_crash_left_wal(source)
    destination = tmp_path / "absent" / "moa.db"
    authority = _recovery_authority(source, destination)
    real_validation = legacy_database_relocation._compute_recovery_semantic_validation
    calls = 0

    def drift_on_post(connection, path):
        nonlocal calls
        calls += 1
        identity, integrity, foreign_keys = real_validation(connection, path)
        if calls == 2:
            identity = replace(identity, source_event_count=identity.source_event_count + 1)
        return identity, integrity, foreign_keys

    monkeypatch.setattr(
        legacy_database_relocation,
        "_compute_recovery_semantic_validation",
        drift_on_post,
    )
    with pytest.raises(DatabaseRelocationError, match="logical catalog identity changed"):
        recover_database_wal(authority)
    guard = ListenerProcessGuard(source)
    guard.acquire()
    guard.release()
    with sqlite3.connect(source, timeout=0.1) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.rollback()
    assert not destination.exists()


@pytest.mark.parametrize(
    "failure_message",
    ["integrity failure", "foreign key failure", "checkpoint incomplete"],
)
def test_wal_recovery_validation_failures_release_resources(
    monkeypatch, tmp_path, failure_message: str
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_crash_left_wal(source)
    destination = tmp_path / "absent" / "moa.db"
    authority = _recovery_authority(source, destination)

    if failure_message == "checkpoint incomplete":
        def fail_checkpoint(_connection):
            raise DatabaseRelocationError(
                "DATABASE_WAL_RECOVERY_FAILURE: checkpoint incomplete"
            )

        monkeypatch.setattr(
            legacy_database_relocation,
            "_checkpoint_wal_truncate_on_owned_connection",
            fail_checkpoint,
        )
    else:
        def fail_validation(_connection, _source):
            raise DatabaseRelocationError(failure_message)

        monkeypatch.setattr(
            legacy_database_relocation,
            "_compute_recovery_semantic_validation",
            fail_validation,
        )

    with pytest.raises(DatabaseRelocationError, match=failure_message):
        recover_database_wal(authority)
    guard = ListenerProcessGuard(source)
    guard.acquire()
    guard.release()
    assert not destination.exists()


@pytest.mark.parametrize("row", [(1, 0, 0), (0, 1, 1), (0, 1, 0)])
def test_wal_recovery_checkpoint_requires_exact_zero_result(row) -> None:
    class _Cursor:
        def fetchone(self):
            return row

    class _Connection:
        in_transaction = False

        def execute(self, sql: str):
            assert sql == "PRAGMA wal_checkpoint(TRUNCATE)"
            return _Cursor()

    with pytest.raises(DatabaseRelocationError, match="exact success"):
        legacy_database_relocation._checkpoint_wal_truncate_on_owned_connection(
            _Connection()
        )


def test_wal_recovery_evidence_failure_occurs_after_resource_release(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_crash_left_wal(source)
    authority = _recovery_authority(source, tmp_path / "absent" / "moa.db")

    def fail_evidence(**_kwargs):
        guard = ListenerProcessGuard(source)
        guard.acquire()
        guard.release()
        with sqlite3.connect(source, timeout=0.1) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        raise RuntimeError("injected evidence failure")

    monkeypatch.setattr(
        legacy_database_relocation, "_wal_recovery_evidence_document", fail_evidence
    )
    with pytest.raises(RuntimeError, match="injected evidence failure"):
        recover_database_wal(authority)


def test_successful_relocation_validates_promotes_and_retires_source(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    platform_root = tmp_path / "user-data" / "moa"
    target = platform_root / "moa.db"
    monkeypatch.setattr(sqlite, "user_data_path", lambda **_kwargs: platform_root)

    result = relocate_database(source, target)

    assert result.target == target.resolve()
    assert target.is_file()
    _assert_valid_tombstone(source, target, result.source_archive)
    assert result.source_archive.is_file()
    assert result.source_archive.name == "moa.db"
    assert result.source_archive.parent.name.startswith("moa.db.migrated-backup-")
    marker_document = json.loads((source / ".moa-relocated").read_text(encoding="utf-8"))
    assert set(marker_document) == {
        "format",
        "version",
        "target",
        "archive",
        "archiveSha256",
        "archiveSize",
    }
    assert marker_document == {
        "format": "moa-legacy-database-retirement",
        "version": 2,
        "target": str(target.resolve(strict=False)),
        "archive": str(result.source_archive.resolve(strict=False)),
        "archiveSha256": sha256(result.source_archive.read_bytes()).hexdigest(),
        "archiveSize": result.source_archive.stat().st_size,
    }
    assert not Path(f"{source}-wal").exists()
    assert not Path(f"{source}-shm").exists()
    assert list(target.parent.glob("moa.db.migrating-*")) == []
    assert list(result.source_archive.parent.glob("moa.db.certifying-*")) == []
    with sqlite3.connect(target) as connection:
        assert connection.execute(
            "SELECT raw_message FROM import_events WHERE source = 'test'"
        ).fetchone()[0] == "representative content"
    assert Path(sqlite.DEFAULT_DATABASE_PATH) == target


def test_authorization_bound_relocation_matches_normalized_identity_and_relocates(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    expected = _authorization_identity(source)
    target = tmp_path / "authorized-target" / "moa.db"

    assert expected.generation_inventory == ((1, True),)
    assert expected.source_event_count == 0
    assert expected.generation_1_projection_link_count == 0
    assert expected.projection_gaps == ()
    result = relocate_database_with_authorization(source, target, expected)

    assert result.target == target.resolve()
    assert target.is_file()
    _assert_valid_tombstone(source, target, result.source_archive)


def test_journal_mode_preparation_transitions_exact_authority_and_preserves_catalog(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    destination = tmp_path / "app-data" / "moa.db"
    _create_rollback_mode_moa_database(source)
    authority = _preparation_authority(source, destination)
    pre_sha256 = authority.expected_main.sha256
    timestamps = iter(
        (
            "2026-09-03T01:00:00+00:00",
            "2026-09-03T01:00:01+00:00",
            "2026-09-03T01:00:02+00:00",
            "2026-09-03T01:00:03+00:00",
        )
    )
    monkeypatch.setattr(legacy_database_relocation, "_utc_timestamp", lambda: next(timestamps))
    monkeypatch.setattr(
        legacy_database_relocation,
        "uuid4",
        lambda: "00000000-0000-0000-0000-000000000001",
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("preparation invoked forbidden relocation/certification output")

    for name in (
        "certify_database_relocation_identity",
        "relocate_database",
        "relocate_database_with_authorization",
        "_prepare_relocation_target",
        "_backup_database",
        "_promote_target",
        "_retire_source",
        "_install_retirement_tombstone",
    ):
        monkeypatch.setattr(legacy_database_relocation, name, forbidden)

    evidence = prepare_database_relocation_journal_mode(authority)

    connection = sqlite3.connect(source)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    finally:
        connection.close()
    document = json.loads(evidence.to_json())
    digest = document.pop("evidence_record_sha256")
    assert digest == sha256(
        json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
            "ascii"
        )
    ).hexdigest()
    assert evidence.to_json() == json.dumps(
        evidence.as_dict(), ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    assert document["evidence_format"] == (
        "moa-database-relocation-journal-mode-preparation-evidence"
    )
    assert document["status"] == "completed"
    assert document["transition"] == {
        "requested_journal_mode": "wal",
        "returned_journal_mode": "wal",
    }
    assert document["post_transition"]["header_write_version"] == 2
    assert document["post_transition"]["header_read_version"] == 2
    assert document["post_transition"]["main"]["sha256"] != pre_sha256
    assert document["validation"]["semantic_identity_unchanged"] is True
    assert document["pre_transition"]["semantic_identity"] == document[
        "post_transition"
    ]["semantic_identity"]
    assert document["expected_preparation_authority"]["authorization_id"] == (
        "approved-preparation-run-001"
    )
    assert not destination.exists()


@pytest.mark.parametrize(
    ("mismatch", "match"),
    (
        ("sha256", "main file"),
        ("size", "main file"),
        ("mode", "journal mode"),
        ("journal", "rollback journal"),
        ("wal", "WAL sidecar"),
        ("shm", "SHM sidecar"),
    ),
)
def test_journal_mode_preparation_rejects_exact_preflight_mismatch_before_transition(
    monkeypatch, tmp_path, mismatch, match
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    destination = tmp_path / "app-data" / "moa.db"
    _create_rollback_mode_moa_database(source)
    authority = _preparation_authority(source, destination)
    unexpected_file = DatabaseFileObservation(
        True, 1, sha256(mismatch.encode("ascii")).hexdigest()
    )
    if mismatch == "sha256":
        replacement = {
            "expected_main": replace(authority.expected_main, sha256="0" * 64)
        }
    elif mismatch == "size":
        assert authority.expected_main.size is not None
        replacement = {
            "expected_main": replace(
                authority.expected_main, size=authority.expected_main.size + 1
            )
        }
    elif mismatch == "mode":
        replacement = {"expected_journal_mode": "persist"}
    else:
        replacement = {f"expected_{mismatch}": unexpected_file}
    authority = replace(authority, **replacement)
    invoked = False

    def unexpected_transition(_connection):
        nonlocal invoked
        invoked = True
        raise AssertionError("journal transition must not be attempted")

    monkeypatch.setattr(
        legacy_database_relocation,
        "_transition_journal_mode_to_wal",
        unexpected_transition,
    )
    with pytest.raises(DatabaseRelocationError, match=match):
        prepare_database_relocation_journal_mode(authority)
    assert invoked is False
    connection = sqlite3.connect(source)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "obstruction", ("destination", "relocation", "certification", "retirement")
)
def test_journal_mode_preparation_rejects_context_obstructions_before_transition(
    monkeypatch, tmp_path, obstruction
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    destination = tmp_path / "app-data" / "moa.db"
    _create_rollback_mode_moa_database(source)
    authority = _preparation_authority(source, destination)
    if obstruction == "destination":
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"occupied")
    elif obstruction == "relocation":
        destination.parent.mkdir(parents=True)
        destination.with_name(f"{destination.name}.migrating-stale").write_bytes(b"stale")
    elif obstruction == "certification":
        source.with_name(f"{source.name}.certifying-stale").write_bytes(b"stale")
    else:
        marker = source.parent / f"{source.name}.migrated-backup-stale"
        marker.mkdir()
        (marker / ".moa-relocation-incomplete").write_text("stale", encoding="utf-8")

    with pytest.raises(DatabaseRelocationError):
        prepare_database_relocation_journal_mode(authority)
    connection = sqlite3.connect(source)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    finally:
        connection.close()


def test_journal_mode_preparation_requires_attestation_and_listener_guard(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    destination = tmp_path / "app-data" / "moa.db"
    _create_rollback_mode_moa_database(source)
    authority = _preparation_authority(source, destination)
    with pytest.raises(DatabaseRelocationError, match="attestation"):
        prepare_database_relocation_journal_mode(
            replace(authority, listener_known_writers_stopped_attested=False)
        )

    guard = ListenerProcessGuard(source)
    guard.acquire()
    try:
        with pytest.raises(DatabaseRelocationError, match="listener guard"):
            prepare_database_relocation_journal_mode(authority)
    finally:
        guard.release()


def test_journal_mode_preparation_begin_exclusive_contention_fails_before_transition(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    destination = tmp_path / "app-data" / "moa.db"
    _create_rollback_mode_moa_database(source)
    authority = _preparation_authority(source, destination)
    monkeypatch.setattr(
        legacy_database_relocation, "_SOURCE_QUIESCENCE_BUSY_TIMEOUT_MS", 100
    )
    writer = sqlite3.connect(source)
    writer.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(DatabaseRelocationError, match="exclusive source ownership"):
            prepare_database_relocation_journal_mode(authority)
    finally:
        writer.rollback()
        writer.close()


@pytest.mark.parametrize("behavior", ("non-wal", "sqlite-error"))
def test_journal_mode_preparation_transition_failure_preserves_actual_evidence(
    monkeypatch, tmp_path, behavior
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    destination = tmp_path / "app-data" / "moa.db"
    _create_rollback_mode_moa_database(source)
    authority = _preparation_authority(source, destination)
    if behavior == "non-wal":
        monkeypatch.setattr(
            legacy_database_relocation,
            "_transition_journal_mode_to_wal",
            lambda _connection: "delete",
        )
    else:

        def fail_transition(_connection):
            raise sqlite3.OperationalError("synthetic transition failure")

        monkeypatch.setattr(
            legacy_database_relocation,
            "_transition_journal_mode_to_wal",
            fail_transition,
        )

    with pytest.raises(DatabaseRelocationJournalModePreparationError) as raised:
        prepare_database_relocation_journal_mode(authority)
    document = json.loads(raised.value.to_json())
    digest = document.pop("evidence_record_sha256")
    assert digest == sha256(
        json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
            "ascii"
        )
    ).hexdigest()
    assert document["status"] == "failed"
    assert document["transition"]["automatic_reversal_attempted"] is False
    assert document["final_observed_state"]["header_write_version"] == 1
    assert document["listener_guard"] == {"acquired": True, "released": True}


@pytest.mark.parametrize("failure", ("integrity", "foreign-key", "semantic"))
def test_journal_mode_preparation_post_validation_failure_never_reverses_wal(
    monkeypatch, tmp_path, failure
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    destination = tmp_path / "app-data" / "moa.db"
    _create_rollback_mode_moa_database(source)
    authority = _preparation_authority(source, destination)
    if failure in {"integrity", "foreign-key"}:
        real_validate = legacy_database_relocation._validate_held_database_with_evidence
        calls = 0

        def fail_second_validation(connection, path):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise DatabaseRelocationError(f"synthetic {failure} failure")
            return real_validate(connection, path)

        monkeypatch.setattr(
            legacy_database_relocation,
            "_validate_held_database_with_evidence",
            fail_second_validation,
        )
    else:
        real_compute = legacy_database_relocation._compute_relocation_authorization_identity
        calls = 0

        def drift_second_identity(connection, path):
            nonlocal calls
            calls += 1
            identity = real_compute(connection, path)
            if calls == 2:
                return replace(identity, source_event_count=identity.source_event_count + 1)
            return identity

        monkeypatch.setattr(
            legacy_database_relocation,
            "_compute_relocation_authorization_identity",
            drift_second_identity,
        )

    with pytest.raises(DatabaseRelocationJournalModePreparationError) as raised:
        prepare_database_relocation_journal_mode(authority)
    assert raised.value.evidence["status"] == "failed"
    assert raised.value.evidence["final_observed_state"]["header_write_version"] == 2
    connection = sqlite3.connect(source)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    finally:
        connection.close()


def test_journal_mode_preparation_rejects_already_wal_under_non_wal_authority(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    destination = tmp_path / "app-data" / "moa.db"
    _create_moa_database(source)
    authority = replace(
        _preparation_authority(source, destination), expected_journal_mode="delete"
    )
    with pytest.raises(DatabaseRelocationError, match="observed journal mode"):
        prepare_database_relocation_journal_mode(authority)
    connection = sqlite3.connect(source)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    finally:
        connection.close()


def test_certification_returns_canonical_evidence_without_relocation_output(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    destination = tmp_path / "absent-destination" / "moa.db"
    with sqlite3.connect(source) as connection:
        original_rows = tuple(
            connection.execute(
            "SELECT id, kind, source, observed_at, raw_message FROM import_events ORDER BY id"
            )
        )
    timestamps = iter(
        (
            "2026-09-03T10:00:00+00:00",
            "2026-09-03T10:00:01+00:00",
            "2026-09-03T10:00:02+00:00",
        )
    )
    monkeypatch.setattr(legacy_database_relocation, "_utc_timestamp", lambda: next(timestamps))
    monkeypatch.setattr(
        legacy_database_relocation,
        "uuid4",
        lambda: "00000000-0000-4000-8000-000000000001",
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("certification invoked relocation output")

    for name in (
        "_prepare_relocation_target",
        "_backup_database",
        "_promote_target",
        "_retire_source",
        "_install_retirement_tombstone",
        "_complete_source_retirement",
    ):
        monkeypatch.setattr(legacy_database_relocation, name, forbidden)
    monkeypatch.setattr(legacy_database_relocation.tempfile, "mkstemp", forbidden)

    certification = certify_database_relocation_identity(
        source,
        destination,
        CHECKPOINT,
        listener_known_writers_stopped_attested=True,
    )

    document = json.loads(certification.to_json())
    digest = document.pop("certification_record_sha256")
    assert digest == sha256(
        json.dumps(
            document,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    ).hexdigest()
    assert certification.to_json() == json.dumps(
        certification.as_dict(),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert document["certification_format"] == (
        "moa-database-relocation-identity-certification"
    )
    assert document["certification_version"] == 1
    assert document["moa_checkpoint"] == CHECKPOINT
    assert document["source"] == str(source.resolve())
    assert document["destination"] == str(destination.resolve())
    assert document["normalization"]["truncate_checkpoint"][0] == 0
    assert document["normalization"]["passive_checkpoint"][0] == 0
    assert document["validation"] == {
        "foreign_key_check": [],
        "integrity_check": ["ok"],
    }
    assert document["listener_known_writers_stopped_attested"] is True
    assert document["sqlite_writer_exclusion"]["acquired"] is True
    assert document["sqlite_writer_exclusion"]["released"] is True
    assert document["post_normalization_main"] == {
        "sha256": certification.identity.normalized_sha256,
        "size": certification.identity.normalized_size,
    }
    assert source.is_file()
    assert not destination.parent.exists()
    assert list(source.parent.glob(f"{source.name}.migrated-backup-*")) == []
    with sqlite3.connect(source) as connection:
        assert tuple(
            connection.execute(
                "SELECT id, kind, source, observed_at, raw_message "
                "FROM import_events ORDER BY id"
            )
        ) == original_rows
        connection.execute("BEGIN IMMEDIATE")
        connection.rollback()


def test_certification_preserves_order_and_typed_projection_gap_identifiers(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    destination = tmp_path / "destination" / "moa.db"
    real_compute = legacy_database_relocation._compute_relocation_authorization_identity

    def compute_with_findings(connection, path):
        identity = real_compute(connection, path)
        return replace(
            identity,
            projection_gaps=(
                DataHealthFinding("DH-PG-001", "first", "entity", 7, "first"),
                DataHealthFinding("DH-PG-002", "second", "entity", "007", "second"),
            ),
        )

    monkeypatch.setattr(
        legacy_database_relocation,
        "_compute_relocation_authorization_identity",
        compute_with_findings,
    )

    certification = certify_database_relocation_identity(source, destination, CHECKPOINT)
    identity_document = certification.as_dict()["authorization_identity"]
    assert identity_document["migration_identity"] == [
        list(row) for row in certification.identity.migration_identity
    ]
    assert identity_document["generation_inventory"] == [
        list(row) for row in certification.identity.generation_inventory
    ]
    assert [
        (finding["local_identifier_kind"], finding["local_identifier"])
        for finding in identity_document["projection_gaps"]
    ] == [("int", 7), ("str", "007")]
    argv = certification.as_dict()["later_bound_relocation_argv"]
    assert isinstance(argv, list)
    assert argv[:4] == [
        "catalog",
        "relocate-database",
        str(source.resolve()),
        "--authorization-bound",
    ]
    assert argv[-1] == "--apply"


def test_certification_identity_is_reused_by_unchanged_bound_relocation(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    destination = tmp_path / "destination" / "moa.db"

    certification = certify_database_relocation_identity(source, destination, CHECKPOINT)
    result = relocate_database_with_authorization(
        source, destination, certification.identity
    )

    assert result.target == destination.resolve()
    assert destination.is_file()


def test_certification_identity_rejects_later_logical_drift_before_output(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    destination = tmp_path / "destination" / "moa.db"
    certification = certify_database_relocation_identity(source, destination, CHECKPOINT)
    with sqlite.connect(source) as connection:
        connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) "
            "VALUES ('command_observation', 'drift', "
            "'2026-09-03T00:00:00+00:00', 'later state')"
        )
        connection.commit()

    with pytest.raises(DatabaseRelocationError, match="RELOCATION_AUTHORIZATION_MISMATCH"):
        relocate_database_with_authorization(
            source, destination, certification.identity
        )

    _assert_no_authorization_failure_output(source, destination)


def test_certification_rejects_incomplete_retirement_and_stale_target_state(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    destination = tmp_path / "destination" / "moa.db"
    incomplete = source.with_name(f"{source.name}.migrated-backup-test")
    incomplete.mkdir()
    (incomplete / ".moa-relocation-incomplete").write_text("incomplete", encoding="utf-8")

    with pytest.raises(DatabaseRelocationError, match="incomplete source retirement"):
        certify_database_relocation_identity(source, destination, CHECKPOINT)

    (incomplete / ".moa-relocation-incomplete").unlink()
    incomplete.rmdir()
    destination.parent.mkdir()
    stale = destination.parent / f"{destination.name}.migrating-stale"
    stale.write_text("stale", encoding="utf-8")
    with pytest.raises(DatabaseRelocationError, match="stale relocation temporary"):
        certify_database_relocation_identity(source, destination, CHECKPOINT)
    assert not destination.exists()


def test_certification_rejects_path_checkpoint_and_database_admission_failures(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    destination = tmp_path / "destination" / "moa.db"

    with pytest.raises(DatabaseRelocationError, match="distinct paths"):
        certify_database_relocation_identity(source, source, CHECKPOINT)

    destination.parent.mkdir()
    destination.write_bytes(b"existing destination")
    with pytest.raises(DatabaseRelocationError, match="will not be overwritten"):
        certify_database_relocation_identity(source, destination, CHECKPOINT)
    assert destination.read_bytes() == b"existing destination"
    destination.unlink()
    destination.parent.rmdir()

    monkeypatch.setattr(
        legacy_database_relocation,
        "_verified_moa_checkout_checkpoint",
        lambda: "0" * 40,
    )
    with pytest.raises(DatabaseRelocationError, match="CHECKPOINT_MISMATCH"):
        certify_database_relocation_identity(source, destination, CHECKPOINT)
    assert not destination.parent.exists()

    monkeypatch.setattr(
        legacy_database_relocation,
        "_verified_moa_checkout_checkpoint",
        lambda: CHECKPOINT,
    )
    monkeypatch.setattr(
        legacy_database_relocation, "verified_legacy_database_path", lambda: None
    )
    invalid_source = tmp_path / "invalid" / "moa.db"
    invalid_source.parent.mkdir()
    invalid_source.write_bytes(b"not sqlite")
    with pytest.raises(DatabaseRelocationError, match="SQLite database"):
        certify_database_relocation_identity(invalid_source, destination, CHECKPOINT)
    assert not destination.parent.exists()


def test_certification_rejects_non_wal_source_without_changing_journal_mode(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    destination = tmp_path / "destination" / "moa.db"
    with sqlite3.connect(source) as connection:
        assert connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0] == "delete"

    with pytest.raises(DatabaseRelocationError, match="must already be WAL"):
        certify_database_relocation_identity(source, destination, CHECKPOINT)

    with sqlite3.connect(source) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert not destination.parent.exists()


def test_certification_semantic_identity_failure_releases_exclusion(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    destination = tmp_path / "destination" / "moa.db"

    def fail_identity(*_args, **_kwargs):
        raise DatabaseRelocationError(
            "RELOCATION_AUTHORIZATION_BLOCKER: normalized source identity is unavailable."
        )

    monkeypatch.setattr(
        legacy_database_relocation,
        "_compute_relocation_authorization_identity",
        fail_identity,
    )
    with pytest.raises(DatabaseRelocationError, match="identity is unavailable"):
        certify_database_relocation_identity(source, destination, CHECKPOINT)
    assert not destination.parent.exists()
    with sqlite3.connect(source) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.rollback()


def test_certification_foreign_key_failure_releases_exclusion_and_creates_no_output(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    destination = tmp_path / "destination" / "moa.db"
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            "INSERT INTO rank_snapshots "
            "(character_id, claim_rank, like_rank, observed_at, import_event_id) "
            "VALUES (999999, NULL, NULL, '2026-09-03T00:00:00+00:00', 1)"
        )
        connection.commit()

    with pytest.raises(DatabaseRelocationError, match="foreign-key validation failed"):
        certify_database_relocation_identity(source, destination, CHECKPOINT)

    assert not destination.parent.exists()
    with sqlite3.connect(source) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.rollback()


def test_certification_begin_immediate_contention_fails_before_output(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    destination = tmp_path / "destination" / "moa.db"
    monkeypatch.setattr(
        legacy_database_relocation, "_SOURCE_QUIESCENCE_BUSY_TIMEOUT_MS", 100
    )
    context = multiprocessing.get_context("spawn")
    holding = context.Event()
    release = context.Event()
    result_queue = context.Queue()
    process = context.Process(
        target=_hold_writer_transaction_process,
        args=(str(source), holding, release, result_queue),
    )
    real_checkpoint = legacy_database_relocation._checkpoint_source_truncate_neutral

    def checkpoint_then_start_writer(path):
        result = real_checkpoint(path)
        process.start()
        assert holding.wait(10)
        return result

    monkeypatch.setattr(
        legacy_database_relocation,
        "_checkpoint_source_truncate_neutral",
        checkpoint_then_start_writer,
    )
    try:
        with pytest.raises(DatabaseRelocationError, match="writer exclusion"):
            certify_database_relocation_identity(source, destination, CHECKPOINT)
    finally:
        release.set()
        _join_process(process)

    assert result_queue.get(timeout=1) == ("rolled-back", None)
    assert not destination.parent.exists()


@pytest.mark.parametrize("checkpoint_mode", ["truncate", "passive"])
def test_certification_incomplete_checkpoint_fails_before_output(
    monkeypatch, tmp_path, checkpoint_mode
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    destination = tmp_path / "destination" / "moa.db"

    def incomplete(_path):
        raise DatabaseRelocationError(
            f"RELOCATION_AUTHORIZATION_BLOCKER: {checkpoint_mode} checkpoint could not "
            "completely backfill the normalized source."
        )

    monkeypatch.setattr(
        legacy_database_relocation,
        f"_checkpoint_source_{checkpoint_mode}_neutral"
        if checkpoint_mode == "truncate"
        else "_checkpoint_source_passive_under_exclusion",
        incomplete,
    )
    with pytest.raises(DatabaseRelocationError, match=f"{checkpoint_mode} checkpoint"):
        certify_database_relocation_identity(source, destination, CHECKPOINT)
    assert not destination.parent.exists()


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("normalized_sha256", lambda value: "0" * 64 if value != "0" * 64 else "1" * 64, "SHA-256"),
        ("normalized_size", lambda value: value + 1, "size"),
        ("migration_identity", lambda value: value[:-1], "migration tuples"),
        ("generation_inventory", lambda _value: ((2, True),), "generation"),
        ("source_event_count", lambda value: value + 1, "source-event count"),
        (
            "generation_1_projection_link_count",
            lambda value: value + 1,
            "generation-1 projection-link count",
        ),
        (
            "projection_gaps",
            lambda value: value
            + (
                DataHealthFinding(
                    "DH-PG-TEST", "projection-gap", "test", "test", "mismatch"
                ),
            ),
            "projection-gap result",
        ),
        (
            "retained_source_preflight_fingerprint",
            lambda value: "0" * 64 if value != "0" * 64 else "1" * 64,
            "readiness fingerprint",
        ),
    ],
    ids=[
        "sha",
        "size",
        "migrations",
        "generations",
        "source-events",
        "generation-links",
        "projection-gaps",
        "readiness-fingerprint",
    ],
)
def test_authorization_identity_mismatch_fails_before_output(
    monkeypatch, tmp_path, field, replacement, message
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    expected = _authorization_identity(source)
    target = tmp_path / "absent-target-parent" / "moa.db"
    mismatched = replace(expected, **{field: replacement(getattr(expected, field))})

    with pytest.raises(DatabaseRelocationError, match=message):
        relocate_database_with_authorization(source, target, mismatched)

    _assert_no_authorization_failure_output(source, target)


def test_bound_relocation_writer_contention_fails_before_output(monkeypatch, tmp_path) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    expected = _authorization_identity(source)
    target = tmp_path / "absent-target-parent" / "moa.db"
    monkeypatch.setattr(
        legacy_database_relocation, "_SOURCE_QUIESCENCE_BUSY_TIMEOUT_MS", 100
    )
    context = multiprocessing.get_context("spawn")
    holding = context.Event()
    release = context.Event()
    result_queue = context.Queue()
    process = context.Process(
        target=_hold_writer_transaction_process,
        args=(str(source), holding, release, result_queue),
    )
    real_checkpoint = legacy_database_relocation._checkpoint_source_truncate_neutral

    def checkpoint_then_start_writer(path):
        real_checkpoint(path)
        process.start()
        assert holding.wait(10)

    monkeypatch.setattr(
        legacy_database_relocation,
        "_checkpoint_source_truncate_neutral",
        checkpoint_then_start_writer,
    )

    try:
        with pytest.raises(DatabaseRelocationError, match="writer exclusion"):
            relocate_database_with_authorization(source, target, expected)
    finally:
        release.set()
        _join_process(process)

    assert result_queue.get(timeout=1) == ("rolled-back", None)
    _assert_no_authorization_failure_output(source, target)


def test_bound_relocation_incomplete_passive_checkpoint_fails_before_output(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    expected = _authorization_identity(source)
    target = tmp_path / "absent-target-parent" / "moa.db"
    original_open = legacy_database_relocation._open_neutral_source_connection
    opened = 0
    passive_closed = False

    class IncompletePassiveConnection:
        def execute(self, statement):
            assert statement == "PRAGMA wal_checkpoint(PASSIVE)"
            return self

        def fetchone(self):
            return (1, 2, 1)

        def close(self):
            nonlocal passive_closed
            passive_closed = True

    def open_with_incomplete_passive(path):
        nonlocal opened
        opened += 1
        if opened <= 2:
            return original_open(path)
        return IncompletePassiveConnection()

    monkeypatch.setattr(
        legacy_database_relocation,
        "_open_neutral_source_connection",
        open_with_incomplete_passive,
    )

    with pytest.raises(DatabaseRelocationError, match="completely backfill"):
        relocate_database_with_authorization(source, target, expected)

    assert passive_closed
    _assert_no_authorization_failure_output(source, target)


def test_commit_before_writer_exclusion_invalidates_earlier_normalized_identity(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    expected = _authorization_identity(source)
    target = tmp_path / "absent-target-parent" / "moa.db"
    real_checkpoint = legacy_database_relocation._checkpoint_source_truncate_neutral

    def checkpoint_then_commit(path):
        real_checkpoint(path)
        with sqlite.connect(path) as connection:
            connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) "
                "VALUES ('command_observation', 'later-write', "
                "'2026-09-02T00:00:00+00:00', 'committed before exclusion')"
            )
            connection.commit()

    monkeypatch.setattr(
        legacy_database_relocation,
        "_checkpoint_source_truncate_neutral",
        checkpoint_then_commit,
    )

    with pytest.raises(DatabaseRelocationError, match="normalized main-file SHA-256"):
        relocate_database_with_authorization(source, target, expected)

    _assert_no_authorization_failure_output(source, target)


def test_semantic_identity_uses_the_held_begin_immediate_connection(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    expected = _authorization_identity(source)
    target = tmp_path / "authorized-target" / "moa.db"
    real_acquire = legacy_database_relocation._acquire_authorization_exclusion
    real_compute = legacy_database_relocation._compute_relocation_authorization_identity
    real_prepare = legacy_database_relocation._prepare_relocation_target
    held_connection = None
    identity_checked = False
    continuation_checked = False

    def acquire(path):
        nonlocal held_connection
        held_connection = real_acquire(path)
        return held_connection

    def compute(connection, path):
        nonlocal identity_checked
        assert connection is held_connection
        assert connection.in_transaction
        identity_checked = True
        return real_compute(connection, path)

    def prepare(path):
        nonlocal continuation_checked
        assert held_connection is not None
        assert held_connection.in_transaction
        continuation_checked = True
        real_prepare(path)

    monkeypatch.setattr(
        legacy_database_relocation, "_acquire_authorization_exclusion", acquire
    )
    monkeypatch.setattr(
        legacy_database_relocation, "_compute_relocation_authorization_identity", compute
    )
    monkeypatch.setattr(legacy_database_relocation, "_prepare_relocation_target", prepare)

    relocate_database_with_authorization(source, target, expected)

    assert identity_checked
    assert continuation_checked


def test_bound_relocation_preflight_uses_caller_owned_transaction(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    expected = _authorization_identity(source)
    target = tmp_path / "authorized-target" / "moa.db"
    real_service = legacy_database_relocation.RetainedSourceReprojectionPreflightService()
    checked = False

    class CheckingPreflightService:
        def preflight(self, connection):
            nonlocal checked
            assert connection.in_transaction
            checked = True
            return real_service.preflight(connection)

    monkeypatch.setattr(
        legacy_database_relocation,
        "RetainedSourceReprojectionPreflightService",
        CheckingPreflightService,
    )

    relocate_database_with_authorization(source, target, expected)

    assert checked


def test_competing_writer_winning_post_retirement_race_fails_relocation(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    target = tmp_path / "user-data" / "moa.db"
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    finished = context.Event()
    result_queue = context.Queue()
    process = context.Process(
        target=_create_fresh_legacy_database_process,
        args=(str(source), start, finished, result_queue),
    )
    process.start()

    def let_writer_win(stage: str) -> None:
        if stage != "SOURCE_RETIRED":
            return
        start.set()
        assert finished.wait(10)

    try:
        with pytest.raises(DatabaseRelocationError, match="dual-authority") as error:
            relocate_database(source, target, _test_hook=let_writer_win)
    finally:
        _join_process(process)

    assert result_queue.get(timeout=1) == ("committed", None)
    assert "LEGACY_PATH_RECREATION_BLOCKER" in str(error.value.__cause__)
    assert source.is_file()
    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT value FROM competing_legacy_write").fetchone()[0] == (
            "competing authority"
        )
    assert target.is_file()
    archives = list(source.parent.glob("moa.db.migrated-backup-*"))
    assert len(archives) == 1
    assert (archives[0] / "moa.db").is_file()
    assert (archives[0] / ".moa-relocation-incomplete").is_file()
    with pytest.raises(LegacyDatabaseAuthorityConflictError, match="incomplete"):
        legacy_database_relocation.ensure_default_database_authority(target)


def test_retirement_tombstone_winning_race_blocks_fresh_legacy_database(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    target = tmp_path / "user-data" / "moa.db"
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    finished = context.Event()
    result_queue = context.Queue()
    process = context.Process(
        target=_create_fresh_legacy_database_process,
        args=(str(source), start, finished, result_queue),
    )
    process.start()

    def let_tombstone_win(stage: str) -> None:
        if stage != "RETIREMENT_TOMBSTONE_INSTALLED":
            return
        start.set()
        assert finished.wait(10)

    try:
        result = relocate_database(source, target, _test_hook=let_tombstone_win)
    finally:
        _join_process(process)

    outcome, detail = result_queue.get(timeout=1)
    assert outcome == "error"
    assert "OperationalError" in detail
    assert target.is_file()
    _assert_valid_tombstone(source, target, result.source_archive)


@pytest.mark.skipif(os.name != "nt", reason="Windows final-success recreation boundary")
def test_windows_tombstone_blocks_recreation_after_archive_certification(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    target = tmp_path / "user-data" / "moa.db"
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    finished = context.Event()
    result_queue = context.Queue()
    process = context.Process(
        target=_create_fresh_legacy_database_process,
        args=(str(source), start, finished, result_queue),
    )
    process.start()

    def recreate_in_prior_final_check_window(stage: str) -> None:
        if stage != "ARCHIVED_SOURCE_CERTIFIED":
            return
        start.set()
        assert finished.wait(10)

    try:
        result = relocate_database(
            source,
            target,
            _test_hook=recreate_in_prior_final_check_window,
        )
    finally:
        _join_process(process)

    outcome, detail = result_queue.get(timeout=1)
    assert outcome == "error"
    assert "OperationalError" in detail
    assert target.is_file()
    _assert_valid_tombstone(source, target, result.source_archive)


def test_valid_tombstone_without_target_fails_closed(monkeypatch, tmp_path) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    target = tmp_path / "user-data" / "moa.db"
    result = relocate_database(source, target)
    target.unlink()

    with pytest.raises(LegacyDatabaseAuthorityConflictError, match="target is missing"):
        legacy_database_relocation.ensure_default_database_authority(target)

    assert not target.exists()
    _assert_valid_tombstone(source, target, result.source_archive)


def test_valid_v1_retirement_marker_remains_accepted_and_is_not_upgraded(tmp_path) -> None:
    source, target, archive, marker, _payload = _retirement_marker_fixture(tmp_path)
    v1_payload = {
        "format": "moa-legacy-database-retirement",
        "version": 1,
        "target": str(target.resolve(strict=False)),
        "archive": str(archive.resolve(strict=False)),
    }
    original_marker_text = json.dumps(v1_payload)
    marker.write_text(original_marker_text, encoding="utf-8")

    tombstone = legacy_database_relocation._validate_retirement_tombstone(
        source, target, expected_archive=archive
    )

    assert tombstone.version == 1
    assert tombstone.archive_sha256 is None
    assert tombstone.archive_size is None
    assert marker.read_text(encoding="utf-8") == original_marker_text


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("format", "wrong-format", "unsupported identity"),
        ("version", 3, "unsupported identity"),
        ("archiveSha256", "A" * 64, "unsupported v2 identity"),
        ("archiveSha256", "0" * 63, "unsupported v2 identity"),
        ("archiveSha256", "g" * 64, "unsupported v2 identity"),
        ("archiveSize", 0, "unsupported v2 identity"),
        ("archiveSize", -1, "unsupported v2 identity"),
        ("archiveSize", 1.5, "unsupported v2 identity"),
        ("archiveSize", True, "unsupported v2 identity"),
        ("archiveSize", sys.maxsize + 1, "unsupported v2 identity"),
    ],
)
def test_v2_retirement_marker_rejects_invalid_identity_fields(
    tmp_path, field: str, value: object, message: str
) -> None:
    source, target, archive, marker, payload = _retirement_marker_fixture(tmp_path)
    payload[field] = value
    marker.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DatabaseRelocationError, match=message):
        legacy_database_relocation._validate_retirement_tombstone(
            source, target, expected_archive=archive
        )


@pytest.mark.parametrize("change", ["unknown", "missing"])
def test_v2_retirement_marker_requires_exact_six_field_schema(
    tmp_path, change: str
) -> None:
    source, target, archive, marker, payload = _retirement_marker_fixture(tmp_path)
    if change == "unknown":
        payload["unexpected"] = "rejected"
    else:
        del payload["archiveSize"]
    marker.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DatabaseRelocationError, match="invalid v2 structure"):
        legacy_database_relocation._validate_retirement_tombstone(
            source, target, expected_archive=archive
        )


@pytest.mark.parametrize("field", ["target", "archive"])
def test_v2_retirement_marker_rejects_noncanonical_serialized_paths(
    tmp_path, field: str
) -> None:
    source, target, archive, marker, payload = _retirement_marker_fixture(tmp_path)
    path = target if field == "target" else archive
    payload[field] = str(path.parent) + os.sep + "." + os.sep + path.name
    marker.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DatabaseRelocationError, match="noncanonical v2 path"):
        legacy_database_relocation._validate_retirement_tombstone(
            source, target, expected_archive=archive
        )


def test_v2_retirement_marker_rejects_archive_directory(tmp_path) -> None:
    source, target, archive, marker, payload = _retirement_marker_fixture(tmp_path)
    archive.unlink()
    archive.mkdir()
    marker.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DatabaseRelocationError, match="regular file"):
        legacy_database_relocation._validate_retirement_tombstone(
            source, target, expected_archive=archive
        )


def test_v2_retirement_marker_rejects_missing_archive(tmp_path) -> None:
    source, target, archive, _marker, _payload = _retirement_marker_fixture(tmp_path)
    archive.unlink()

    with pytest.raises(DatabaseRelocationError, match="missing or unavailable"):
        legacy_database_relocation._validate_retirement_tombstone(
            source, target, expected_archive=archive
        )


def test_v2_retirement_marker_rejects_symlinked_archive_leaf(tmp_path) -> None:
    source, target, archive, marker, payload = _retirement_marker_fixture(tmp_path)
    archive.unlink()
    real_archive = archive.with_name("real.db")
    real_archive.write_bytes(b"final archived database")
    try:
        archive.symlink_to(real_archive)
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable: {error}")
    payload["archive"] = str(archive)
    marker.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DatabaseRelocationError, match="noncanonical|symlink"):
        legacy_database_relocation._validate_retirement_tombstone(
            source, target, expected_archive=archive
        )
    with pytest.raises(DatabaseRelocationError, match="symlink or reparse point"):
        legacy_database_relocation._safe_archive_leaf_metadata(archive)


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse attribute semantics")
def test_v2_archive_leaf_rejects_windows_reparse_attribute(
    monkeypatch, tmp_path
) -> None:
    archive = tmp_path / "archive.db"
    archive.write_bytes(b"archive")
    real_metadata = archive.lstat()
    reparse_metadata = SimpleNamespace(
        st_mode=real_metadata.st_mode,
        st_size=real_metadata.st_size,
        st_file_attributes=getattr(os.stat(archive), "st_file_attributes", 0) | 0x400,
    )
    original_lstat = Path.lstat

    def marked_reparse(path: Path):
        return reparse_metadata if path == archive else original_lstat(path)

    monkeypatch.setattr(Path, "lstat", marked_reparse)

    with pytest.raises(DatabaseRelocationError, match="reparse point"):
        legacy_database_relocation._safe_archive_leaf_metadata(archive)


@pytest.mark.parametrize("replacement", [b"FINAL ARCHIVED DATABASE", b"short"])
def test_v2_retirement_marker_rejects_archive_content_or_size_change(
    tmp_path, replacement: bytes
) -> None:
    source, target, archive, _marker, _payload = _retirement_marker_fixture(tmp_path)
    archive.write_bytes(replacement)

    with pytest.raises(DatabaseRelocationError, match="does not match"):
        legacy_database_relocation._validate_retirement_tombstone(
            source, target, expected_archive=archive
        )


@pytest.mark.parametrize("mismatch", ["target", "archive"])
def test_v2_retirement_marker_rejects_expected_path_mismatch(
    tmp_path, mismatch: str
) -> None:
    source, target, archive, _marker, _payload = _retirement_marker_fixture(tmp_path)
    expected_target = target.with_name("different.db") if mismatch == "target" else target
    expected_archive = (
        archive.with_name("different.db") if mismatch == "archive" else archive
    )

    with pytest.raises(DatabaseRelocationError, match=f"{mismatch} does not match"):
        legacy_database_relocation._validate_retirement_tombstone(
            source, expected_target, expected_archive=expected_archive
        )


@pytest.mark.parametrize("failure_function", ["_archive_file_sha256", "_archive_file_size"])
def test_archive_identity_collection_failure_retains_incomplete_marker(
    monkeypatch, tmp_path, failure_function: str
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    target = tmp_path / "user-data" / "moa.db"

    def fail_identity(_archive: Path):
        raise OSError(f"injected {failure_function} failure")

    monkeypatch.setattr(legacy_database_relocation, failure_function, fail_identity)

    with pytest.raises(DatabaseRelocationError, match="dual-authority") as error:
        relocate_database(source, target)

    assert "final archive identity could not be collected" in str(error.value.__cause__)
    assert source.is_dir()
    assert not (source / ".moa-relocated").exists()
    incomplete_markers = list(
        source.parent.glob("moa.db.migrated-backup-*/.moa-relocation-incomplete")
    )
    assert len(incomplete_markers) == 1
    assert (incomplete_markers[0].parent / "moa.db").is_file()


def test_invalid_newly_written_v2_marker_retains_incomplete_marker(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    target = tmp_path / "user-data" / "moa.db"
    original_writer = legacy_database_relocation._write_retirement_tombstone_marker

    def write_invalid_marker(marker: Path, payload: dict[str, object]) -> None:
        invalid_payload = dict(payload)
        invalid_payload["archiveSha256"] = "A" * 64
        original_writer(marker, invalid_payload)

    monkeypatch.setattr(
        legacy_database_relocation,
        "_write_retirement_tombstone_marker",
        write_invalid_marker,
    )

    with pytest.raises(DatabaseRelocationError, match="dual-authority"):
        relocate_database(source, target)

    assert source.is_dir()
    assert (source / ".moa-relocated").is_file()
    assert len(
        list(source.parent.glob("moa.db.migrated-backup-*/.moa-relocation-incomplete"))
    ) == 1


def test_post_write_archive_identity_mismatch_retains_incomplete_marker(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    target = tmp_path / "user-data" / "moa.db"
    original_identity = legacy_database_relocation._archive_file_identity
    observations = 0

    def mismatch_after_write(archive: Path) -> tuple[str, int]:
        nonlocal observations
        observations += 1
        digest, size = original_identity(archive)
        if observations == 2:
            digest = "0" * 64 if digest != "0" * 64 else "1" * 64
        return digest, size

    monkeypatch.setattr(
        legacy_database_relocation, "_archive_file_identity", mismatch_after_write
    )

    with pytest.raises(DatabaseRelocationError, match="dual-authority"):
        relocate_database(source, target)

    assert observations == 2
    assert len(
        list(source.parent.glob("moa.db.migrated-backup-*/.moa-relocation-incomplete"))
    ) == 1


def test_successful_v2_validation_precedes_incomplete_marker_removal(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    target = tmp_path / "user-data" / "moa.db"
    original_validator = legacy_database_relocation._validate_retirement_tombstone
    original_completion = legacy_database_relocation._complete_source_retirement
    validation_barriers: list[bool] = []

    def validate_with_barrier(*args, **kwargs):
        expected_archive = kwargs.get("expected_archive")
        if expected_archive is not None:
            validation_barriers.append(
                (Path(expected_archive).parent / ".moa-relocation-incomplete").is_file()
            )
        return original_validator(*args, **kwargs)

    def complete_after_validation(archive: Path) -> None:
        assert len(validation_barriers) >= 2
        assert all(validation_barriers)
        original_completion(archive)

    monkeypatch.setattr(
        legacy_database_relocation,
        "_validate_retirement_tombstone",
        validate_with_barrier,
    )
    monkeypatch.setattr(
        legacy_database_relocation,
        "_complete_source_retirement",
        complete_after_validation,
    )

    result = relocate_database(source, target)

    assert len(validation_barriers) >= 2
    assert all(validation_barriers)
    assert not (
        result.source_archive.parent / ".moa-relocation-incomplete"
    ).exists()


@pytest.mark.parametrize("marker_content", [None, "{"])
def test_invalid_retirement_tombstone_fails_closed(
    monkeypatch, tmp_path, marker_content: str | None
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    source.mkdir(parents=True)
    if marker_content is not None:
        (source / ".moa-relocated").write_text(marker_content, encoding="utf-8")
    target = tmp_path / "user-data" / "moa.db"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"preserved target")

    with pytest.raises(LegacyDatabaseAuthorityConflictError, match="invalid or unrecognized"):
        legacy_database_relocation.ensure_default_database_authority(target)

    assert target.read_bytes() == b"preserved target"


def test_ordinary_legacy_file_and_new_target_remain_dual_authority(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    target = tmp_path / "user-data" / "moa.db"
    _create_moa_database(target, message="separate target authority")

    with pytest.raises(LegacyDatabaseAuthorityConflictError, match="Both the legacy"):
        legacy_database_relocation.ensure_default_database_authority(target)

    assert source.is_file()
    assert target.is_file()


def test_ordinary_legacy_file_without_target_still_requires_relocation(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    target = tmp_path / "user-data" / "moa.db"

    with pytest.raises(LegacyDatabaseRelocationRequiredError, match="relocate-database"):
        legacy_database_relocation.ensure_default_database_authority(target)

    assert source.is_file()
    assert not target.exists()


def test_tombstone_marker_failure_preserves_barrier_and_incomplete_state(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    target = tmp_path / "user-data" / "moa.db"

    def fail_marker_write(marker: Path, _payload: dict[str, object]) -> None:
        marker.write_text("{", encoding="utf-8")
        raise OSError("injected marker write failure")

    monkeypatch.setattr(
        legacy_database_relocation,
        "_write_retirement_tombstone_marker",
        fail_marker_write,
    )

    with pytest.raises(DatabaseRelocationError, match="dual-authority") as error:
        relocate_database(source, target)

    assert "marker could not be completed" in str(error.value.__cause__)
    assert source.is_dir()
    assert (source / ".moa-relocated").read_text(encoding="utf-8") == "{"
    assert target.is_file()
    incomplete_markers = list(
        source.parent.glob("moa.db.migrated-backup-*/.moa-relocation-incomplete")
    )
    assert len(incomplete_markers) == 1
    assert (incomplete_markers[0].parent / "moa.db").is_file()
    with pytest.raises(LegacyDatabaseAuthorityConflictError, match="incomplete"):
        legacy_database_relocation.ensure_default_database_authority(target)


def test_custom_source_relocation_does_not_install_legacy_tombstone(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(legacy_database_relocation, "verified_legacy_database_path", lambda: None)
    source = tmp_path / "custom" / "custom.db"
    _create_moa_database(source)
    target = tmp_path / "target" / "moa.db"

    result = relocate_database(source, target)

    assert not source.exists()
    assert target.is_file()
    assert result.source_archive.is_file()


def test_writer_committed_before_source_quiescence_is_included(monkeypatch, tmp_path) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    target = tmp_path / "target" / "moa.db"
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    connected = context.Event()
    committed = context.Event()
    allow_close = context.Event()
    closed = context.Event()
    result_queue = context.Queue()
    start.set()
    allow_close.set()
    process = context.Process(
        target=_commit_import_event_process,
        args=(
            str(source),
            "insert",
            "committed before quiescence",
            start,
            connected,
            committed,
            allow_close,
            closed,
            result_queue,
        ),
    )
    process.start()
    assert committed.wait(10)
    assert closed.wait(10)
    _join_process(process)
    assert result_queue.get(timeout=1) == ("committed", None)

    relocate_database(source, target)

    with sqlite3.connect(target) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM import_events WHERE raw_message = ?",
            ("committed before quiescence",),
        ).fetchone()[0] == 1


def test_writer_after_final_snapshot_cannot_cross_successful_retirement(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    target = tmp_path / "target" / "moa.db"
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    connected = context.Event()
    committed = context.Event()
    allow_close = context.Event()
    closed = context.Event()
    result_queue = context.Queue()
    process = context.Process(
        target=_commit_import_event_process,
        args=(
            str(source),
            "insert",
            "attempted after final snapshot",
            start,
            connected,
            committed,
            allow_close,
            closed,
            result_queue,
        ),
    )
    process.start()

    def coordinate_writer(stage: str) -> None:
        if stage != "FINAL_SOURCE_SNAPSHOT_ESTABLISHED":
            return
        start.set()
        assert connected.wait(10)
        assert not committed.is_set()

    try:
        if os.name == "nt":
            with pytest.raises(DatabaseRelocationError, match="dual-authority"):
                relocate_database(source, target, _test_hook=coordinate_writer)
            assert source.is_file()
            assert target.is_file()
            with pytest.raises(LegacyDatabaseAuthorityConflictError):
                legacy_database_relocation.ensure_default_database_authority(target)
        else:
            result = relocate_database(source, target, _test_hook=coordinate_writer)
            assert result.source_archive.is_file()
            _assert_valid_tombstone(source, target, result.source_archive)
        assert committed.wait(10)
    finally:
        allow_close.set()
        assert closed.wait(10)
        _join_process(process)
    assert result_queue.get(timeout=1) == ("committed", None)
    with sqlite3.connect(target) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM import_events WHERE raw_message = ?",
            ("attempted after final snapshot",),
        ).fetchone()[0] == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows closed-handle certification path")
@pytest.mark.parametrize(
    ("operation", "message"),
    [
        ("insert", "committed in Windows close gap"),
        ("update", "updated in Windows close gap"),
    ],
)
def test_windows_writer_commit_in_close_gap_is_detected_before_success(
    monkeypatch, tmp_path, operation: str, message: str
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    connection = sqlite.connect(source)
    try:
        connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) "
            "VALUES ('command_observation', 'second-row', "
            "'2026-08-12T00:00:00+00:00', 'unchanged')"
        )
        connection.commit()
    finally:
        connection.close()
    target = tmp_path / "target" / "moa.db"
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    connected = context.Event()
    committed = context.Event()
    allow_close = context.Event()
    closed = context.Event()
    result_queue = context.Queue()
    allow_close.set()
    process = context.Process(
        target=_commit_import_event_process,
        args=(
            str(source),
            operation,
            message,
            start,
            connected,
            committed,
            allow_close,
            closed,
            result_queue,
        ),
    )
    process.start()

    def coordinate_writer(stage: str) -> None:
        if stage == "FINAL_SOURCE_SNAPSHOT_ESTABLISHED":
            start.set()
            assert connected.wait(10)
            assert not committed.is_set()
        elif stage == "SOURCE_QUIESCENCE_RELEASED":
            assert committed.wait(10)
            assert closed.wait(10)

    with pytest.raises(DatabaseRelocationError, match="dual-authority") as error:
        relocate_database(source, target, _test_hook=coordinate_writer)

    _join_process(process)
    assert result_queue.get(timeout=1) == ("committed", None)
    cause_message = str(error.value.__cause__)
    if operation == "update":
        assert "archived source image changed" in cause_message
    else:
        assert "archived source changed" in cause_message
    assert target.is_file()
    assert source.is_dir()
    assert (source / ".moa-relocated").is_file()
    assert len(
        list(
            source.parent.glob(
                "moa.db.migrated-backup-*/.moa-relocation-incomplete"
            )
        )
    ) == 1
    with pytest.raises(LegacyDatabaseAuthorityConflictError, match="incomplete"):
        legacy_database_relocation.ensure_default_database_authority(target)


def test_relocation_refuses_existing_target_without_overwrite(monkeypatch, tmp_path) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    target = tmp_path / "target" / "moa.db"
    target.parent.mkdir()
    target.write_bytes(b"existing target")

    with pytest.raises(DatabaseRelocationError, match="will not be overwritten"):
        relocate_database(source, target)

    assert source.is_file()
    assert target.read_bytes() == b"existing target"


def test_backup_or_promotion_failure_preserves_source_and_removes_temp(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    target = tmp_path / "target" / "moa.db"

    def fail_promotion(_temporary, _target) -> None:
        raise DatabaseRelocationError("injected promotion failure")

    monkeypatch.setattr(legacy_database_relocation, "_promote_target", fail_promotion)
    with pytest.raises(DatabaseRelocationError, match="injected promotion failure"):
        relocate_database(source, target)

    assert source.is_file()
    assert not target.exists()
    assert list(target.parent.glob("moa.db.migrating-*")) == []


def test_target_validation_failure_prevents_promotion(monkeypatch, tmp_path) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    target = tmp_path / "target" / "moa.db"
    original_validate = legacy_database_relocation._validate_database

    def fail_temporary_validation(path: Path):
        if path != source.resolve():
            raise DatabaseRelocationError("injected target validation failure")
        return original_validate(path)

    monkeypatch.setattr(
        legacy_database_relocation,
        "_validate_database",
        fail_temporary_validation,
    )
    with pytest.raises(DatabaseRelocationError, match="target validation failure"):
        relocate_database(source, target)

    assert source.is_file()
    assert not target.exists()
    assert list(target.parent.glob("moa.db.migrating-*")) == []


def test_source_retirement_failure_leaves_visible_dual_authority(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    platform_root = tmp_path / "user-data" / "moa"
    target = platform_root / "moa.db"
    monkeypatch.setattr(sqlite, "user_data_path", lambda **_kwargs: platform_root)

    def fail_retirement(_source: Path) -> Path:
        raise OSError("injected retirement failure")

    monkeypatch.setattr(legacy_database_relocation, "_retire_source", fail_retirement)
    with pytest.raises(DatabaseRelocationError, match="dual-authority"):
        relocate_database(source, target)

    assert source.is_file()
    assert target.is_file()
    with pytest.raises(LegacyDatabaseAuthorityConflictError):
        Path(sqlite.DEFAULT_DATABASE_PATH)


def test_partial_retirement_rollback_failure_leaves_startup_blocker(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source database material")
    wal = Path(f"{source}-wal")
    wal.write_bytes(b"wal material")
    target = tmp_path / "target" / "moa.db"
    target.parent.mkdir()
    target.write_bytes(b"promoted target")
    original_rename = Path.rename

    def fail_sidecar_and_database_rollback(path: Path, destination: Path):
        destination_path = Path(destination)
        if path == wal:
            raise PermissionError("injected WAL retirement failure")
        if (
            path.name == source.name
            and path.parent.name.startswith("moa.db.migrated-backup-")
            and destination_path == source
        ):
            raise PermissionError("injected database rollback failure")
        return original_rename(path, destination_path)

    monkeypatch.setattr(Path, "rename", fail_sidecar_and_database_rollback)

    with pytest.raises(PermissionError, match="WAL retirement failure"):
        legacy_database_relocation._retire_source(source)

    assert not source.exists()
    assert wal.is_file()
    assert len(
        list(
            source.parent.glob(
                "moa.db.migrated-backup-*/.moa-relocation-incomplete"
            )
        )
    ) == 1
    with pytest.raises(LegacyDatabaseAuthorityConflictError, match="incomplete"):
        legacy_database_relocation.ensure_default_database_authority(target)


def test_source_retirement_preserves_present_wal_and_shm(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(legacy_database_relocation, "verified_legacy_database_path", lambda: None)
    source = tmp_path / "moa.db"
    source.write_bytes(b"database")
    wal = Path(f"{source}-wal")
    shm = Path(f"{source}-shm")
    wal.write_bytes(b"wal")
    shm.write_bytes(b"shm")

    archive = legacy_database_relocation._retire_source(source)
    legacy_database_relocation._complete_source_retirement(archive)

    assert archive.read_bytes() == b"database"
    assert (archive.parent / wal.name).read_bytes() == b"wal"
    assert (archive.parent / shm.name).read_bytes() == b"shm"
    assert not (archive.parent / ".moa-relocation-incomplete").exists()


def test_post_promotion_temp_cleanup_failure_reports_dual_authority(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    target = tmp_path / "user-data" / "moa.db"
    original_unlink = Path.unlink

    def fail_promoted_temp_unlink(path: Path, *args, **kwargs) -> None:
        if ".migrating-" in path.name and target.exists():
            raise PermissionError("injected promoted-temp cleanup failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_promoted_temp_unlink)
    with pytest.raises(DatabaseRelocationError, match="temporary path could not be removed") as error:
        relocate_database(source, target)

    assert str(source.resolve()) in str(error.value)
    assert str(target.resolve()) in str(error.value)
    assert source.is_file()
    assert target.is_file()
    assert len(list(target.parent.glob("moa.db.migrating-*"))) == 1


def test_recognized_stale_temporary_file_requires_explicit_cleanup(
    monkeypatch, tmp_path
) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    target = tmp_path / "target" / "moa.db"
    target.parent.mkdir()
    stale = target.parent / "moa.db.migrating-stale"
    stale.write_bytes(b"partial")

    with pytest.raises(DatabaseRelocationError, match="stale relocation temporary"):
        relocate_database(source, target)

    assert stale.read_bytes() == b"partial"
    assert source.is_file()
    assert not target.exists()


def test_active_source_writer_blocks_before_target_creation(monkeypatch, tmp_path) -> None:
    _checkout, source = _configure_checkout(monkeypatch, tmp_path)
    _create_moa_database(source)
    target = tmp_path / "target" / "moa.db"
    monkeypatch.setattr(
        legacy_database_relocation, "_SOURCE_QUIESCENCE_BUSY_TIMEOUT_MS", 100
    )
    context = multiprocessing.get_context("spawn")
    holding = context.Event()
    release = context.Event()
    result_queue = context.Queue()
    process = context.Process(
        target=_hold_writer_transaction_process,
        args=(str(source), holding, release, result_queue),
    )
    process.start()
    assert holding.wait(10)

    try:
        with pytest.raises(DatabaseRelocationError, match="LEGACY_SOURCE_RETIREMENT_BLOCKER"):
            relocate_database(source, target)
    finally:
        release.set()
        _join_process(process)

    assert result_queue.get(timeout=1) == ("rolled-back", None)
    assert source.is_file()
    assert not target.exists()


@pytest.mark.parametrize("path", [Path(":memory:"), Path("file:moa.db")])
def test_relocation_rejects_non_file_backed_paths(path, tmp_path) -> None:
    with pytest.raises(DatabaseRelocationError, match="ordinary file-backed"):
        relocate_database(path, tmp_path / "target.db")


def test_relocation_rejects_same_source_and_target(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(legacy_database_relocation, "verified_legacy_database_path", lambda: None)
    source = tmp_path / "moa.db"
    source.write_bytes(b"not opened")

    with pytest.raises(DatabaseRelocationError, match="must be distinct"):
        relocate_database(source, source)


def test_unrecognized_sqlite_source_is_rejected_before_checkpoint(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(legacy_database_relocation, "verified_legacy_database_path", lambda: None)
    source = tmp_path / "unrelated.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")

    def unexpected_checkpoint(_source: Path) -> None:
        pytest.fail("unrecognized source must not be checkpointed")

    monkeypatch.setattr(
        legacy_database_relocation,
        "_checkpoint_source_for_retirement",
        unexpected_checkpoint,
    )

    with pytest.raises(DatabaseRelocationError, match="not a recognized MOA catalog"):
        relocate_database(source, tmp_path / "target.db")
