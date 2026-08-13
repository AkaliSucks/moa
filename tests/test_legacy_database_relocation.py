import gc
import multiprocessing
import os
import sqlite3
from pathlib import Path

import pytest

from moa.database import legacy_database_relocation, sqlite
from moa.database.legacy_database_relocation import (
    DatabaseRelocationError,
    LegacyDatabaseAuthorityConflictError,
    relocate_database,
)
from moa.repositories.catalog_repository import CatalogRepository


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
    assert not source.exists()
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
            assert not source.exists()
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
    assert not source.exists()
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
