from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import moa.cli.retained_source_reprojection_commands as command_module
import moa.services.retained_source_reprojection_operator as operator_module
from moa.cli.main import app
from moa.database.migrations import MigrationError
from moa.repositories.catalog_repository import CatalogRepository
from moa.services.retained_source_reprojection_admission_service import (
    ReprojectionAdmissionRejection,
)
from moa.services.retained_source_reprojection_coordinator import (
    RetainedSourceReprojectionCoordinatorError,
    RetainedSourceReprojectionCoordinatorRejection,
)
from moa.services.retained_source_reprojection_operator import (
    RetainedSourceReprojectionOperator,
    RetainedSourceReprojectionOperatorError,
    RetainedSourceReprojectionOperatorFailure,
    RetainedSourceReprojectionVerification,
)


@dataclass
class _Guard:
    path: Path
    events: list[str]

    def acquire(self) -> None:
        self.events.append(f"acquire:{self.path}")

    def release(self) -> None:
        self.events.append("release")


class _Coordinator:
    def __init__(self, path: Path, events: list[tuple[str, object]]) -> None:
        self.path = path
        self.events = events

    def execute(self, source_event_id: int) -> object:
        self.events.append(("coordinator", source_event_id))
        return SimpleNamespace(
            source_event_id=source_event_id,
            source_family="claim",
            executor_name="FakeExecutor",
            terminal_status="success",
            current_generation_id=1,
            import_event_id=3,
            linked_count=1,
        )


def _database_path(tmp_path: Path) -> Path:
    path = tmp_path / "operator.sqlite3"
    CatalogRepository(path)
    return path


def _verification(source_event_id: int = 17) -> RetainedSourceReprojectionVerification:
    return RetainedSourceReprojectionVerification(
        source_event_id=source_event_id,
        source_family="claim",
        import_event_id=3,
        current_generation_id=1,
        expected_link_count=1,
        completed_link_count=1,
    )


def test_command_is_registered_with_explicit_arguments_and_no_escape_hatches() -> None:
    result = CliRunner().invoke(app, ["catalog", "retained-source-reproject", "--help"])

    assert result.exit_code == 0
    assert "SOURCE_EVENT_ID" in result.stdout
    assert "DATABASE_PATH" in result.output
    assert "--confirm" in result.stdout
    for flag in ("--all", "--batch", "--force", "--skip-preflight", "--ignore-family"):
        assert flag not in result.stdout


def test_missing_database_argument_has_no_production_default() -> None:
    result = CliRunner().invoke(app, ["catalog", "retained-source-reproject", "17"])

    assert result.exit_code == 2
    assert "DATABASE_PATH" in result.output


def test_missing_confirmation_performs_no_operator_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _database_path(tmp_path)
    called = False

    def fail_if_constructed(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True
        raise AssertionError("operator must not be constructed without confirmation")

    monkeypatch.setattr(command_module, "RetainedSourceReprojectionOperator", fail_if_constructed)
    result = CliRunner().invoke(
        app, ["catalog", "retained-source-reproject", "17", str(path)]
    )

    assert result.exit_code == 1
    assert "No changes made" in result.stdout
    assert called is False


@pytest.mark.parametrize("source_event_id", [0, -1])
def test_nonpositive_source_id_is_rejected_before_guard(
    tmp_path: Path, source_event_id: int
) -> None:
    path = _database_path(tmp_path)
    events: list[str] = []
    operator = RetainedSourceReprojectionOperator(
        path,
        guard_factory=lambda resolved: _Guard(resolved, events),
    )

    with pytest.raises(RetainedSourceReprojectionOperatorError) as caught:
        operator.execute(source_event_id)

    assert caught.value.reason is RetainedSourceReprojectionOperatorFailure.INVALID_SOURCE_EVENT_ID
    assert events == []


def test_malformed_source_id_is_rejected_by_cli_parsing(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["catalog", "retained-source-reproject", "not-an-id", str(_database_path(tmp_path)), "--confirm"],
    )

    assert result.exit_code == 2
    assert "SOURCE_EVENT_ID" in result.output


@pytest.mark.parametrize("database_path", [Path(":memory:"), Path("does-not-exist.sqlite3")])
def test_invalid_database_path_is_rejected_without_creation(
    tmp_path: Path, database_path: Path
) -> None:
    supplied = database_path if database_path.name == database_path.as_posix() else tmp_path / database_path
    if database_path.name == "does-not-exist.sqlite3":
        supplied = tmp_path / "missing" / database_path.name

    with pytest.raises(RetainedSourceReprojectionOperatorError) as caught:
        RetainedSourceReprojectionOperator(supplied)

    assert caught.value.reason is RetainedSourceReprojectionOperatorFailure.INVALID_DATABASE_PATH
    assert not supplied.exists()
    assert not supplied.parent.exists() if supplied.parent.name == "missing" else True


def test_schema_and_migration_identity_are_checked_read_only_before_coordinator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _database_path(tmp_path)
    events: list[str] = []
    coordinator_calls = 0

    class InvalidSchema:
        def __init__(self, connection: sqlite3.Connection) -> None:
            pass

        def validate_schema(self) -> None:
            raise MigrationError("fixture schema mismatch")

    def factory(_path: Path) -> _Coordinator:
        nonlocal coordinator_calls
        coordinator_calls += 1
        return _Coordinator(path, [])

    monkeypatch.setattr(operator_module, "DataHealthRepository", InvalidSchema)
    operator = RetainedSourceReprojectionOperator(
        path,
        coordinator_factory=factory,
        guard_factory=lambda resolved: _Guard(resolved, events),
    )

    with pytest.raises(RetainedSourceReprojectionOperatorError) as caught:
        operator.execute(17)

    assert caught.value.reason is RetainedSourceReprojectionOperatorFailure.SCHEMA_INVALID
    assert coordinator_calls == 0
    assert events == [f"acquire:{path.resolve()}", "release"]


def test_guard_and_coordinator_use_the_same_canonical_database_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _database_path(tmp_path)
    supplied = path.parent / "." / path.name
    guard_paths: list[Path] = []
    coordinator_paths: list[Path] = []

    def guard_factory(resolved: Path) -> _Guard:
        guard_paths.append(resolved)
        return _Guard(resolved, [])

    def coordinator_factory(resolved: Path) -> _Coordinator:
        coordinator_paths.append(resolved)
        return _Coordinator(resolved, [])

    operator = RetainedSourceReprojectionOperator(
        supplied,
        coordinator_factory=coordinator_factory,
        guard_factory=guard_factory,
    )
    monkeypatch.setattr(operator, "_verify", lambda execution: _verification())
    operator.execute(17)

    assert guard_paths == [path.resolve()]
    assert coordinator_paths == [path.resolve()]


def test_guard_contention_rejects_before_coordinator(tmp_path: Path) -> None:
    path = _database_path(tmp_path)
    held = operator_module.ListenerProcessGuard(path)
    held.acquire()
    calls = 0

    def factory(_path: Path) -> _Coordinator:
        nonlocal calls
        calls += 1
        return _Coordinator(path, [])

    try:
        operator = RetainedSourceReprojectionOperator(path, coordinator_factory=factory)
        with pytest.raises(RetainedSourceReprojectionOperatorError) as caught:
            operator.execute(17)
    finally:
        held.release()

    assert caught.value.reason is RetainedSourceReprojectionOperatorFailure.LISTENER_OWNERSHIP_UNAVAILABLE
    assert calls == 0


def test_coordinator_is_called_once_and_guard_is_released_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _database_path(tmp_path)
    events: list[str] = []
    coordinator_events: list[tuple[str, object]] = []

    def factory(resolved: Path) -> _Coordinator:
        return _Coordinator(resolved, coordinator_events)

    operator = RetainedSourceReprojectionOperator(
        path,
        coordinator_factory=factory,
        guard_factory=lambda resolved: _Guard(resolved, events),
    )
    monkeypatch.setattr(operator, "_verify", lambda execution: _verification())
    result = operator.execute(17)

    assert coordinator_events == [("coordinator", 17)]
    assert events == [f"acquire:{path.resolve()}", "release"]
    assert result.verification.status == "passed"


def test_guard_is_released_after_coordinator_failure(tmp_path: Path) -> None:
    path = _database_path(tmp_path)
    events: list[str] = []

    class FailingCoordinator:
        def execute(self, source_event_id: int) -> object:
            raise RuntimeError("private executor failure")

    operator = RetainedSourceReprojectionOperator(
        path,
        coordinator_factory=lambda _path: FailingCoordinator(),  # type: ignore[arg-type]
        guard_factory=lambda resolved: _Guard(resolved, events),
    )

    with pytest.raises(RetainedSourceReprojectionOperatorError) as caught:
        operator.execute(17)

    assert caught.value.reason is RetainedSourceReprojectionOperatorFailure.COORDINATOR_FAILED
    assert events == [f"acquire:{path.resolve()}", "release"]


def test_admission_rejection_is_bounded_and_has_no_secondary_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _database_path(tmp_path)
    calls = 0

    class RejectingCoordinator:
        def execute(self, source_event_id: int) -> object:
            nonlocal calls
            calls += 1
            raise RetainedSourceReprojectionCoordinatorError(
                RetainedSourceReprojectionCoordinatorRejection.ADMISSION_REJECTED,
                "private fixture detail",
                admission_reason=ReprojectionAdmissionRejection.CURRENT_LINKS_ALREADY_COMPLETE,
            )

    operator = RetainedSourceReprojectionOperator(
        path,
        coordinator_factory=lambda _path: RejectingCoordinator(),  # type: ignore[arg-type]
    )
    with pytest.raises(RetainedSourceReprojectionOperatorError) as caught:
        operator.execute(17)

    assert caught.value.reason is RetainedSourceReprojectionOperatorFailure.ADMISSION_REJECTED
    assert caught.value.admission_reason is ReprojectionAdmissionRejection.CURRENT_LINKS_ALREADY_COMPLETE
    assert calls == 1


def test_cli_admission_failure_does_not_render_private_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _database_path(tmp_path)

    class FailingOperator:
        def __init__(self, database_path: Path) -> None:
            pass

        def execute(self, source_event_id: int) -> object:
            raise RetainedSourceReprojectionOperatorError(
                RetainedSourceReprojectionOperatorFailure.ADMISSION_REJECTED,
                admission_reason=ReprojectionAdmissionRejection.CURRENT_LINKS_ALREADY_COMPLETE,
            )

    monkeypatch.setattr(command_module, "RetainedSourceReprojectionOperator", FailingOperator)
    result = CliRunner().invoke(
        app,
        ["catalog", "retained-source-reproject", "17", str(path), "--confirm"],
    )

    assert result.exit_code == 1
    assert "current_links_already_complete" in result.stdout
    assert "private" not in result.stdout.lower()
    assert "token" not in result.stdout.lower()


def test_verification_failure_reports_commit_may_be_durable_and_does_not_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _database_path(tmp_path)
    guard_events: list[str] = []
    coordinator_events: list[tuple[str, object]] = []
    operator = RetainedSourceReprojectionOperator(
        path,
        coordinator_factory=lambda resolved: _Coordinator(resolved, coordinator_events),
        guard_factory=lambda resolved: _Guard(resolved, guard_events),
    )

    def fail_verification(_execution: object) -> object:
        raise ValueError("private verification detail")

    monkeypatch.setattr(operator, "_verify", fail_verification)
    with pytest.raises(RetainedSourceReprojectionOperatorError) as caught:
        operator.execute(17)

    assert caught.value.reason is RetainedSourceReprojectionOperatorFailure.VERIFICATION_FAILED
    assert coordinator_events == [("coordinator", 17)]
    assert guard_events == [f"acquire:{path.resolve()}", "release"]


def test_real_singleton_operator_path_commits_and_verifies(tmp_path: Path) -> None:
    from test_retained_source_singleton_reprojection_executor import _fixture
    from moa.database.sqlite import connect

    path = _database_path(tmp_path)
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection, "claim")
    result = CliRunner().invoke(
        app,
        [
            "catalog",
            "retained-source-reproject",
            str(admission.source_event_id),
            str(path),
            "--confirm",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Derived family: claim" in result.stdout
    assert "Expected link count: 1" in result.stdout
    assert "Post-write verification: passed" in result.stdout
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_projection_links WHERE source_event_id = ? "
            "AND generation_id = 3 AND state = 'completed'",
            (admission.source_event_id,),
        ).fetchone()[0] == 1
    replay = CliRunner().invoke(
        app,
        [
            "catalog",
            "retained-source-reproject",
            str(admission.source_event_id),
            str(path),
            "--confirm",
        ],
    )
    assert replay.exit_code == 1
    assert "current_links_already_complete" in replay.stdout


def test_real_roll_multi_link_operator_path_commits_and_verifies(tmp_path: Path) -> None:
    from test_retained_source_roll_reprojection_executor import _fixture
    from moa.database.sqlite import connect

    path = _database_path(tmp_path)
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection, generations=1)
    result = CliRunner().invoke(
        app,
        [
            "catalog",
            "retained-source-reproject",
            str(admission.source_event_id),
            str(path),
            "--confirm",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Derived family: roll" in result.stdout
    assert "Expected link count: 4" in result.stdout
    assert "Completed link count: 4" in result.stdout
    assert "Post-write verification: passed" in result.stdout
