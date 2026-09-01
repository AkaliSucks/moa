from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3
from unittest.mock import Mock

import pytest

import moa.database.sqlite as sqlite_module
import moa.services.projection_generation_activation_service as activation_module
from moa.database.projection_generation_backup import (
    ProjectionGenerationBackupResult,
    create_projection_generation_backup,
)
from moa.database.sqlite import connect
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.projection_link_repository import ProjectionLinkRepository
from moa.services.projection_generation_activation_service import (
    ProjectionGenerationActivationError,
    ProjectionGenerationActivationOutcomeUncertainError,
    ProjectionGenerationActivationService,
    activate_projection_generation,
)


def _certify(tmp_path: Path) -> tuple[Path, ProjectionGenerationBackupResult]:
    source = tmp_path / "isolated.sqlite3"
    outputs = tmp_path / "outputs"
    worktree = tmp_path / "worktree"
    outputs.mkdir()
    worktree.mkdir()
    CatalogRepository(source)
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    evidence = create_projection_generation_backup(
        source,
        outputs / "backup.sqlite3",
        outputs / "restore.sqlite3",
        worktree,
    )
    return source, evidence


def _activate(source: Path, evidence: ProjectionGenerationBackupResult):
    return ProjectionGenerationActivationService().activate(
        source, evidence, evidence.preflight_fingerprint
    )


def _generations(source: Path) -> list[tuple[int, int]]:
    with sqlite3.connect(source) as connection:
        return [
            (int(row[0]), int(row[1]))
            for row in connection.execute(
                "SELECT id, is_current FROM projection_generations ORDER BY id"
            )
        ]


def test_certified_isolated_database_activates_exactly_one_empty_generation(
    tmp_path: Path,
) -> None:
    source, evidence = _certify(tmp_path)

    result = _activate(source, evidence)

    assert result.database_path == source.resolve()
    assert result.historical_generation_ids == (1,)
    assert result.current_generation_id == 2
    assert result.mutation_count == 3
    assert result.preflight_fingerprint == evidence.preflight_fingerprint
    assert _generations(source) == [(1, 0), (2, 1)]
    with sqlite3.connect(source) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM discord_projection_links WHERE generation_id = 2"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("unsafe", ["default", "symlink", "memory", "implicit"])
def test_unsafe_database_path_is_refused_before_opening_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsafe: str
) -> None:
    source, evidence = _certify(tmp_path)
    writer = Mock(side_effect=AssertionError("writer was opened"))
    monkeypatch.setattr(activation_module, "run_write_transaction", writer)
    target: object = source
    if unsafe == "default":
        monkeypatch.setattr(activation_module, "default_database_path", lambda: source)
    elif unsafe == "symlink":
        target = tmp_path / "source-link.sqlite3"
        try:
            target.symlink_to(source)
        except OSError:
            pytest.skip("symlink creation is unavailable")
    elif unsafe == "memory":
        target = Path(":memory:")
    else:
        target = None

    with pytest.raises(ProjectionGenerationActivationError):
        activate_projection_generation(  # type: ignore[arg-type]
            target, evidence, evidence.preflight_fingerprint
        )

    writer.assert_not_called()
    assert _generations(source) == [(1, 1)]


def test_backup_and_review_evidence_are_required_before_opening_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, evidence = _certify(tmp_path)
    writer = Mock(side_effect=AssertionError("writer was opened"))
    monkeypatch.setattr(activation_module, "run_write_transaction", writer)

    with pytest.raises(ProjectionGenerationActivationError, match="backup evidence"):
        activate_projection_generation(  # type: ignore[arg-type]
            source, object(), evidence.preflight_fingerprint
        )
    with pytest.raises(ProjectionGenerationActivationError, match="Reviewed preflight"):
        activate_projection_generation(source, evidence, "f" * 64)

    writer.assert_not_called()


@pytest.mark.parametrize("stale", ["source", "backup", "fingerprint"])
def test_stale_file_or_fingerprint_evidence_rolls_back_without_generation(
    tmp_path: Path, stale: str
) -> None:
    source, evidence = _certify(tmp_path)
    if stale == "source":
        with source.open("ab") as file:
            file.write(b"stale-source")
    elif stale == "backup":
        with evidence.backup_path.open("ab") as file:
            file.write(b"stale-backup")
    else:
        evidence = replace(evidence, preflight_fingerprint="f" * 64)

    with pytest.raises(ProjectionGenerationActivationError):
        _activate(source, evidence)

    assert _generations(source) == [(1, 1)]


def test_source_changed_after_validation_is_rejected_before_generation_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, evidence = _certify(tmp_path)
    real_runner = activation_module.run_write_transaction
    switch = Mock(side_effect=AssertionError("generation switch was reached"))

    def race_runner(path: Path, callback):
        with source.open("ab") as file:
            file.write(b"post-validation-source-change")
        return real_runner(path, callback)

    monkeypatch.setattr(activation_module, "run_write_transaction", race_runner)
    monkeypatch.setattr(ProjectionLinkRepository, "switch_current_generation", switch)

    with pytest.raises(ProjectionGenerationActivationError, match="source digest or size"):
        _activate(source, evidence)

    switch.assert_not_called()
    assert _generations(source) == [(1, 1)]


@pytest.mark.parametrize("stale", ["schema", "migration", "generation"])
def test_stale_schema_migration_or_generation_evidence_rolls_back(
    tmp_path: Path, stale: str
) -> None:
    source, evidence = _certify(tmp_path)
    with connect(source) as connection:
        if stale == "schema":
            connection.execute("DROP TABLE wishlist_observations")
        elif stale == "migration":
            connection.execute("UPDATE schema_migrations SET name = 'stale' WHERE version = 15")
        else:
            connection.execute("BEGIN IMMEDIATE")
            ProjectionLinkRepository(connection).switch_current_generation()
        connection.commit()
    digest, size = activation_module._file_identity(source)
    evidence = replace(evidence, source_sha256=digest, source_size=size)

    with pytest.raises(ProjectionGenerationActivationError):
        _activate(source, evidence)

    expected = [(1, 0), (2, 1)] if stale == "generation" else [(1, 1)]
    assert _generations(source) == expected


def test_stale_preflight_inventory_fingerprint_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, evidence = _certify(tmp_path)
    real_preflight = activation_module.RetainedSourceReprojectionPreflightService.preflight

    def stale_preflight(service, connection):
        report = real_preflight(service, connection)
        return replace(report, inventory_fingerprint="f" * 64)

    monkeypatch.setattr(
        activation_module.RetainedSourceReprojectionPreflightService,
        "preflight",
        stale_preflight,
    )

    with pytest.raises(ProjectionGenerationActivationError, match="preflight is stale"):
        _activate(source, evidence)

    assert _generations(source) == [(1, 1)]


def test_writer_contention_fails_before_switch_and_is_safe_to_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, evidence = _certify(tmp_path)
    real_connect = sqlite_module.connect

    def short_timeout_connect(path=None):
        connection = real_connect(path)
        connection.execute("PRAGMA busy_timeout = 1")
        return connection

    monkeypatch.setattr(sqlite_module, "connect", short_timeout_connect)
    writer = sqlite3.connect(source)
    writer.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(ProjectionGenerationActivationError) as caught:
            _activate(source, evidence)
    finally:
        writer.rollback()
        writer.close()

    assert caught.value.retry_safe is True
    assert _generations(source) == [(1, 1)]
    assert _activate(source, evidence).current_generation_id == 2


def test_forced_switch_failure_rolls_back_and_same_evidence_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, evidence = _certify(tmp_path)
    real_switch = ProjectionLinkRepository.switch_current_generation
    calls = 0

    def fail_once(repository: ProjectionLinkRepository) -> int:
        nonlocal calls
        calls += 1
        generation_id = real_switch(repository)
        if calls == 1:
            raise RuntimeError("forced switch failure")
        return generation_id

    monkeypatch.setattr(ProjectionLinkRepository, "switch_current_generation", fail_once)

    with pytest.raises(ProjectionGenerationActivationError) as caught:
        _activate(source, evidence)
    assert caught.value.retry_safe is True
    assert _generations(source) == [(1, 1)]

    assert _activate(source, evidence).current_generation_id == 2


def test_forced_postcondition_failure_rolls_back(tmp_path: Path, monkeypatch) -> None:
    source, evidence = _certify(tmp_path)
    real_inventory = activation_module._generation_inventory
    calls = 0

    def fail_after_switch(connection: sqlite3.Connection):
        nonlocal calls
        calls += 1
        inventory = real_inventory(connection)
        return ((1, 0),) if calls == 2 else inventory

    monkeypatch.setattr(activation_module, "_generation_inventory", fail_after_switch)

    with pytest.raises(ProjectionGenerationActivationError, match="postconditions"):
        _activate(source, evidence)

    assert _generations(source) == [(1, 1)]


def test_unexpected_mutation_accounting_rolls_back(tmp_path: Path, monkeypatch) -> None:
    source, evidence = _certify(tmp_path)
    real_switch = ProjectionLinkRepository.switch_current_generation

    def switch_with_extra_mutation(repository: ProjectionLinkRepository) -> int:
        generation_id = real_switch(repository)
        repository._connection.execute(  # noqa: SLF001 - forced corruption seam
            "UPDATE projection_generations SET is_current = 0 WHERE id = 1"
        )
        return generation_id

    monkeypatch.setattr(
        ProjectionLinkRepository, "switch_current_generation", switch_with_extra_mutation
    )

    with pytest.raises(ProjectionGenerationActivationError, match="mutation accounting"):
        _activate(source, evidence)

    assert _generations(source) == [(1, 1)]


def test_stale_evidence_after_success_refuses_second_generation(tmp_path: Path) -> None:
    source, evidence = _certify(tmp_path)
    _activate(source, evidence)

    with pytest.raises(ProjectionGenerationActivationError):
        _activate(source, evidence)

    assert _generations(source) == [(1, 0), (2, 1)]


def test_callback_success_followed_by_runner_failure_is_uncertain_and_not_retry_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, evidence = _certify(tmp_path)

    def uncertain_runner(path: Path, callback):
        connection = connect(path)
        connection.execute("BEGIN IMMEDIATE")
        try:
            callback(connection)
        finally:
            connection.rollback()
            connection.close()
        raise sqlite3.OperationalError("commit result unavailable")

    monkeypatch.setattr(activation_module, "run_write_transaction", uncertain_runner)

    with pytest.raises(ProjectionGenerationActivationOutcomeUncertainError) as caught:
        _activate(source, evidence)

    assert caught.value.retry_safe is False
    assert caught.value.outcome_uncertain is True
    assert _generations(source) == [(1, 1)]
