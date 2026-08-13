import gc
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
    with sqlite3.connect(target) as connection:
        assert connection.execute(
            "SELECT raw_message FROM import_events WHERE source = 'test'"
        ).fetchone()[0] == "representative content"
    assert Path(sqlite.DEFAULT_DATABASE_PATH) == target


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
    active_writer = sqlite.connect(source)
    active_writer.execute("BEGIN IMMEDIATE")
    active_writer.execute(
        "INSERT INTO import_events (kind, source, observed_at, raw_message) "
        "VALUES ('command_observation', 'active', '2026-08-12T00:00:00+00:00', 'pending')"
    )

    try:
        with pytest.raises(DatabaseRelocationError, match="LEGACY_SOURCE_RETIREMENT_BLOCKER"):
            relocate_database(source, target)
    finally:
        active_writer.rollback()
        active_writer.close()

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
