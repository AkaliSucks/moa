import gc
import json
import multiprocessing
import os
import sqlite3
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from moa.database import legacy_database_relocation, sqlite
from moa.database.legacy_database_relocation import (
    DatabaseRelocationAuthorizationIdentity,
    DatabaseRelocationError,
    LegacyDatabaseAuthorityConflictError,
    LegacyDatabaseRelocationRequiredError,
    certify_database_relocation_identity,
    relocate_database,
    relocate_database_with_authorization,
)
from moa.models.data_health import DataHealthFinding
from moa.repositories.catalog_repository import CatalogRepository


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
    assert tombstone.target == target.resolve()
    assert tombstone.archive == archive.resolve()


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
