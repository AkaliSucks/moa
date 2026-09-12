from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest

import moa.database.projection_generation_backup as backup_module
from moa.database.projection_generation_backup import (
    ProjectionGenerationBackupError,
    create_projection_generation_backup,
)
from moa.database.sqlite import connect
from moa.database.writer_lease import try_acquire_exclusive_database_quiescence
from moa.repositories.catalog_repository import CatalogRepository


def _paths(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    worktree = tmp_path / "worktree"
    outputs = tmp_path / "outputs"
    worktree.mkdir()
    outputs.mkdir()
    source = tmp_path / "source.sqlite3"
    CatalogRepository(source)
    return source, outputs / "backup.sqlite3", outputs / "restore.sqlite3", worktree


def test_backup_records_identity_and_proves_exact_restore_without_changing_source(
    tmp_path: Path,
) -> None:
    source, backup, restore, worktree = _paths(tmp_path)
    source_before = source.read_bytes()
    journal_mode_before: str
    with sqlite3.connect(source) as connection:
        journal_mode_before = str(connection.execute("PRAGMA journal_mode").fetchone()[0])

    result = create_projection_generation_backup(source, backup, restore, worktree)

    assert result.source_path == source.resolve()
    assert result.backup_path == backup.resolve()
    assert result.restore_probe_path == restore.resolve()
    assert result.source_sha256 != ""
    assert result.source_size == len(source_before)
    assert result.backup_sha256 == result.restore_probe_sha256
    assert result.backup_size == result.restore_probe_size == backup.stat().st_size
    assert result.generation_ids == (1,)
    assert result.current_generation_id == 1
    assert result.hypothetical_generation_id == 2
    assert len(result.preflight_fingerprint) == 64
    assert result.migration_identity[-1] == (
        15,
        "antidisable-reconstructibility-foundation",
    )
    assert backup.read_bytes() == restore.read_bytes()
    assert source.read_bytes() == source_before
    with sqlite3.connect(source) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == journal_mode_before


def test_canonical_default_database_is_refused_before_backup_work(
    tmp_path: Path, monkeypatch
) -> None:
    source, backup, restore, worktree = _paths(tmp_path)
    monkeypatch.setattr(backup_module, "default_database_path", lambda: source)
    writer_exclusion = Mock(side_effect=AssertionError("writer exclusion was reached"))
    file_identity = Mock(side_effect=AssertionError("file identity was reached"))
    backup_promotion = Mock(side_effect=AssertionError("backup promotion was reached"))
    monkeypatch.setattr(backup_module, "_acquire_writer_exclusion", writer_exclusion)
    monkeypatch.setattr(backup_module, "_file_identity", file_identity)
    monkeypatch.setattr(backup_module, "_backup_and_promote", backup_promotion)

    with pytest.raises(ProjectionGenerationBackupError, match="canonical MOA default database"):
        create_projection_generation_backup(source, backup, restore, worktree)

    writer_exclusion.assert_not_called()
    file_identity.assert_not_called()
    backup_promotion.assert_not_called()
    assert not backup.exists()
    assert not restore.exists()


def test_canonical_default_database_cannot_be_an_isolated_output(
    tmp_path: Path, monkeypatch
) -> None:
    source, backup, restore, worktree = _paths(tmp_path)
    monkeypatch.setattr(backup_module, "default_database_path", lambda: backup)

    with pytest.raises(ProjectionGenerationBackupError, match="canonical MOA default"):
        create_projection_generation_backup(source, backup, restore, worktree)

    assert not backup.exists()
    assert not restore.exists()


def test_verified_legacy_database_cannot_use_isolated_backup_bypass(
    tmp_path: Path, monkeypatch
) -> None:
    import moa.database.legacy_database_relocation as relocation_module

    source, backup, restore, worktree = _paths(tmp_path)
    monkeypatch.setattr(backup_module, "default_database_path", lambda: tmp_path / "other.db")
    monkeypatch.setattr(
        relocation_module,
        "verified_legacy_database_path",
        lambda: source,
    )

    with pytest.raises(ProjectionGenerationBackupError, match="verified legacy"):
        create_projection_generation_backup(source, backup, restore, worktree)

    assert not backup.exists()
    assert not restore.exists()


def test_isolated_backup_remains_independent_of_operational_quiescence(
    tmp_path: Path,
) -> None:
    source, backup, restore, worktree = _paths(tmp_path)
    attempt = try_acquire_exclusive_database_quiescence()
    assert attempt.lease is not None
    try:
        result = create_projection_generation_backup(source, backup, restore, worktree)
    finally:
        attempt.lease.release()

    assert result.backup_path == backup.resolve()
    assert result.restore_probe_path == restore.resolve()


@pytest.mark.parametrize("target_name", ["backup", "restore"])
def test_outputs_must_be_absent(tmp_path: Path, target_name: str) -> None:
    source, backup, restore, worktree = _paths(tmp_path)
    target = backup if target_name == "backup" else restore
    target.write_text("collision", encoding="utf-8")

    with pytest.raises(ProjectionGenerationBackupError, match="must be absent"):
        create_projection_generation_backup(source, backup, restore, worktree)

    assert target.read_text(encoding="utf-8") == "collision"


@pytest.mark.parametrize("target_name", ["backup", "restore"])
def test_outputs_must_be_outside_worktree(tmp_path: Path, target_name: str) -> None:
    source, backup, restore, worktree = _paths(tmp_path)
    target = worktree / f"{target_name}.sqlite3"
    if target_name == "backup":
        backup = target
    else:
        restore = target

    with pytest.raises(ProjectionGenerationBackupError, match="outside the worktree"):
        create_projection_generation_backup(source, backup, restore, worktree)


def test_source_must_be_regular_and_not_a_symlink(tmp_path: Path) -> None:
    source, backup, restore, worktree = _paths(tmp_path)
    link = tmp_path / "source-link.sqlite3"
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(ProjectionGenerationBackupError, match="non-symlink"):
        create_projection_generation_backup(link, backup, restore, worktree)


def test_destination_parent_must_exist_and_not_be_a_symlink(tmp_path: Path) -> None:
    source, backup, restore, worktree = _paths(tmp_path)
    missing_backup = tmp_path / "missing" / "backup.sqlite3"

    with pytest.raises(ProjectionGenerationBackupError, match="parent must be"):
        create_projection_generation_backup(source, missing_backup, restore, worktree)


@pytest.mark.parametrize("same_target", ["source", "backup"])
def test_all_database_paths_must_be_distinct(tmp_path: Path, same_target: str) -> None:
    source, backup, restore, worktree = _paths(tmp_path)
    if same_target == "source":
        restore = source
    else:
        restore = backup

    with pytest.raises(ProjectionGenerationBackupError):
        create_projection_generation_backup(source, backup, restore, worktree)


def test_busy_writer_fails_closed_without_outputs(tmp_path: Path, monkeypatch) -> None:
    source, backup, restore, worktree = _paths(tmp_path)
    monkeypatch.setattr(backup_module, "_WRITER_EXCLUSION_TIMEOUT_MS", 1)
    writer = sqlite3.connect(source)
    writer.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(ProjectionGenerationBackupError, match="writer exclusion"):
            create_projection_generation_backup(source, backup, restore, worktree)
    finally:
        writer.rollback()
        writer.close()

    assert not backup.exists()
    assert not restore.exists()


@pytest.mark.parametrize("invalid_state", ["foreign_key", "schema", "migration", "generation"])
def test_database_validation_failures_leave_no_outputs(tmp_path: Path, invalid_state: str) -> None:
    source, backup, restore, worktree = _paths(tmp_path)
    with connect(source) as connection:
        if invalid_state == "foreign_key":
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(
                "INSERT INTO discord_projection_links (source_event_id, generation_id, projection_kind, projection_slot, state, claimed_at, created_at, updated_at) VALUES (999, 1, 'catalog.roll', 'slot', 'claimed', 'now', 'now', 'now')"
            )
        elif invalid_state == "schema":
            connection.execute("DROP TABLE wishlist_observations")
        elif invalid_state == "migration":
            connection.execute("UPDATE schema_migrations SET name = 'wrong' WHERE version = 15")
        else:
            connection.execute("DROP INDEX uq_projection_generations_one_current")
            connection.execute("INSERT INTO projection_generations (id, is_current) VALUES (2, 1)")
        connection.commit()

    with pytest.raises(ProjectionGenerationBackupError):
        create_projection_generation_backup(source, backup, restore, worktree)

    assert not backup.exists()
    assert not restore.exists()


def test_backup_fingerprint_mismatch_cleans_promoted_outputs(tmp_path: Path, monkeypatch) -> None:
    source, backup, restore, worktree = _paths(tmp_path)
    real_validate = backup_module._validate_path

    def mismatching_validation(path: Path):
        result = real_validate(path)
        return backup_module._DatabaseValidation(
            result.migration_identity,
            result.generation_ids,
            result.current_generation_id,
            result.hypothetical_generation_id,
            "f" * 64,
        )

    monkeypatch.setattr(backup_module, "_validate_path", mismatching_validation)

    with pytest.raises(ProjectionGenerationBackupError, match="does not match"):
        create_projection_generation_backup(source, backup, restore, worktree)

    assert not backup.exists()
    assert not restore.exists()


def test_restore_failure_removes_promoted_backup(tmp_path: Path, monkeypatch) -> None:
    source, backup, restore, worktree = _paths(tmp_path)
    real_backup = backup_module._backup_and_promote
    calls = 0

    def fail_restore(source_path: Path, target_path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ProjectionGenerationBackupError("restore failed")
        real_backup(source_path, target_path)

    monkeypatch.setattr(backup_module, "_backup_and_promote", fail_restore)

    with pytest.raises(ProjectionGenerationBackupError, match="restore failed"):
        create_projection_generation_backup(source, backup, restore, worktree)

    assert not backup.exists()
    assert not restore.exists()
