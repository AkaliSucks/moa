from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from moa.database.sqlite import run_write_transaction
from moa.services.retained_source_antidisable_page_reprojection_executor import (
    RetainedSourceAntidisablePageReprojectionExecutor,
)
from moa.services.retained_source_kakeraloot_settings_reprojection_executor import (
    RetainedSourceKakeralootSettingsReprojectionExecutor,
)
from moa.services.retained_source_reprojection_admission_service import (
    ReprojectionAdmissionRejection,
    RetainedSourceReprojectionAdmission,
    RetainedSourceReprojectionAdmissionError,
)
from moa.services.retained_source_reprojection_coordinator import (
    ADMITTED_REPROJECTION_FAMILIES,
    DEFAULT_REPROJECTION_EXECUTOR_REGISTRY,
    RetainedSourceReprojectionCoordinator,
    RetainedSourceReprojectionCoordinatorConfigurationError,
    RetainedSourceReprojectionCoordinatorError,
    RetainedSourceReprojectionCoordinatorRejection,
    RetainedSourceReprojectionExecutorRegistry,
)
from moa.services.retained_source_roll_reprojection_executor import (
    RetainedSourceRollReprojectionExecutor,
)
from moa.services.retained_source_server_settings_reprojection_executor import (
    RetainedSourceServerSettingsReprojectionExecutor,
)
from moa.services.retained_source_singleton_reprojection_executor import (
    RetainedSourceSingletonReprojectionExecutor,
)
from moa.services.retained_source_timer_reprojection_executor import (
    RetainedSourceTimerReprojectionExecutor,
)


COMPLETED_AT = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)


@dataclass(frozen=True)
class _Result:
    source_event_id: int
    current_generation_id: int
    import_event_id: int
    linked_count: int
    replay_skipped: bool


def _admission(family: str) -> RetainedSourceReprojectionAdmission:
    return RetainedSourceReprojectionAdmission(
        source_event_id=41,
        successful_attempt_id=7,
        import_event_id=9,
        source_family=family,
        current_generation_id=2,
        historical_generation_id=1,
        server="server",
        account=None if family in {"server_settings", "kakeraloot_settings"} else "account",
        expected_identities=(),
        payloads=(),
    )


class _AdmissionService:
    def __init__(self, admission: RetainedSourceReprojectionAdmission) -> None:
        self.admission = admission
        self.calls: list[tuple[int, sqlite3.Connection]] = []

    def admit(
        self, connection: sqlite3.Connection, source_event_id: int
    ) -> RetainedSourceReprojectionAdmission:
        self.calls.append((source_event_id, connection))
        return self.admission


class _RejectingAdmissionService:
    def __init__(self) -> None:
        self.calls = 0

    def admit(
        self, connection: sqlite3.Connection, source_event_id: int
    ) -> RetainedSourceReprojectionAdmission:
        self.calls += 1
        raise RetainedSourceReprojectionAdmissionError(
            ReprojectionAdmissionRejection.CURRENT_LINKS_CONFLICT,
            "fixture rejection detail must not become coordinator status",
        )


class _MalformedAdmissionService:
    def admit(
        self, connection: sqlite3.Connection, source_event_id: int
    ) -> RetainedSourceReprojectionAdmission:
        return object()  # type: ignore[return-value]


class _Executor:
    def __init__(
        self,
        result: _Result | None = None,
        *,
        fail: bool = False,
        write_count: int = 0,
    ) -> None:
        self.calls: list[tuple[sqlite3.Connection, RetainedSourceReprojectionAdmission]] = []
        self.transaction_states: list[bool] = []
        self.result = result
        self.fail = fail
        self.write_count = write_count

    def execute(
        self,
        connection: sqlite3.Connection,
        admission: RetainedSourceReprojectionAdmission,
        completed_at: datetime,
    ) -> _Result | None:
        self.calls.append((connection, admission))
        self.transaction_states.append(connection.in_transaction)
        for _ in range(self.write_count):
            connection.execute("INSERT INTO coordinator_writes (value) VALUES ('write')")
        if self.fail:
            raise RuntimeError("fixture executor failure")
        return self.result


def _database_path(tmp_path: Path) -> Path:
    path = tmp_path / "retained-source-coordinator.sqlite3"
    if path.exists():
        return path
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE coordinator_writes (value TEXT NOT NULL)")
    connection.commit()
    connection.close()
    return path


def _registry(family: str, executor: _Executor) -> RetainedSourceReprojectionExecutorRegistry:
    return RetainedSourceReprojectionExecutorRegistry(
        ((family, lambda admission_service: executor),)
    )


def _coordinator(
    tmp_path: Path,
    family: str,
    executor: _Executor,
    admission_service: object | None = None,
    *,
    transaction_runner=run_write_transaction,
) -> RetainedSourceReprojectionCoordinator:
    return RetainedSourceReprojectionCoordinator(
        _database_path(tmp_path),
        admission_service=admission_service
        or _AdmissionService(_admission(family)),
        executor_registry=_registry(family, executor),
        transaction_runner=transaction_runner,
    )


def test_default_dispatch_is_complete_and_uses_required_specializations() -> None:
    expected = {
        **{
            family: RetainedSourceSingletonReprojectionExecutor
            for family in (
                "claim",
                "kakera_state",
                "mudapins",
                "player_bonus",
                "wishlist",
                "disablelist",
                "tower_state",
                "kakeraloot_state",
                "sphere_result",
                "profile",
            )
        },
        "server_settings": RetainedSourceServerSettingsReprojectionExecutor,
        "kakeraloot_settings": RetainedSourceKakeralootSettingsReprojectionExecutor,
        "timer_state": RetainedSourceTimerReprojectionExecutor,
        "roll": RetainedSourceRollReprojectionExecutor,
        "antidisable": RetainedSourceAntidisablePageReprojectionExecutor,
    }
    assert ADMITTED_REPROJECTION_FAMILIES == set(expected)
    assert DEFAULT_REPROJECTION_EXECUTOR_REGISTRY.families == set(expected)
    admission_service = object()
    for family, expected_type in expected.items():
        executor = DEFAULT_REPROJECTION_EXECUTOR_REGISTRY.create(family, admission_service)
        assert type(executor) is expected_type


def test_singleton_dispatch_uses_one_runner_and_connection(tmp_path: Path) -> None:
    executor = _Executor(_Result(41, 2, 9, 1, False), write_count=1)
    runner_calls = 0

    def runner(path, callback):
        nonlocal runner_calls
        runner_calls += 1
        return run_write_transaction(path, callback)

    coordinator = _coordinator(tmp_path, "claim", executor, transaction_runner=runner)
    result = coordinator.execute(41, completed_at=COMPLETED_AT)

    assert runner_calls == 1
    assert len(executor.calls) == 1
    assert executor.transaction_states == [True]
    assert result.source_event_id == 41
    assert result.source_family == "claim"
    assert result.executor_name == "_Executor"
    assert result.terminal_status == "success"
    assert result.linked_count == 1
    connection = sqlite3.connect(_database_path(tmp_path))
    assert connection.execute("SELECT COUNT(*) FROM coordinator_writes").fetchone()[0] == 1
    connection.close()


def test_each_specialized_family_can_be_selected_by_the_registry() -> None:
    assert type(
        DEFAULT_REPROJECTION_EXECUTOR_REGISTRY.create("timer_state", object())
    ) is RetainedSourceTimerReprojectionExecutor
    assert type(
        DEFAULT_REPROJECTION_EXECUTOR_REGISTRY.create("roll", object())
    ) is RetainedSourceRollReprojectionExecutor
    assert type(
        DEFAULT_REPROJECTION_EXECUTOR_REGISTRY.create("antidisable", object())
    ) is RetainedSourceAntidisablePageReprojectionExecutor
    assert type(
        DEFAULT_REPROJECTION_EXECUTOR_REGISTRY.create("server_settings", object())
    ) is RetainedSourceServerSettingsReprojectionExecutor
    assert type(
        DEFAULT_REPROJECTION_EXECUTOR_REGISTRY.create("kakeraloot_settings", object())
    ) is RetainedSourceKakeralootSettingsReprojectionExecutor


def test_rejected_admission_never_reaches_executor(tmp_path: Path) -> None:
    executor = _Executor(_Result(41, 2, 9, 1, False))
    admission_service = _RejectingAdmissionService()
    coordinator = _coordinator(tmp_path, "claim", executor, admission_service)

    with pytest.raises(RetainedSourceReprojectionCoordinatorError) as caught:
        coordinator.execute(41, completed_at=COMPLETED_AT)

    assert caught.value.reason is RetainedSourceReprojectionCoordinatorRejection.ADMISSION_REJECTED
    assert caught.value.admission_reason is ReprojectionAdmissionRejection.CURRENT_LINKS_CONFLICT
    assert admission_service.calls == 1
    assert executor.calls == []


def test_unknown_admitted_family_fails_closed_before_dispatch(tmp_path: Path) -> None:
    executor = _Executor(_Result(41, 2, 9, 1, False))
    admission_service = _AdmissionService(_admission("not-admitted"))
    coordinator = _coordinator(tmp_path, "claim", executor, admission_service)

    with pytest.raises(RetainedSourceReprojectionCoordinatorError) as caught:
        coordinator.execute(41, completed_at=COMPLETED_AT)

    assert caught.value.reason is RetainedSourceReprojectionCoordinatorRejection.UNKNOWN_FAMILY
    assert executor.calls == []


def test_missing_executor_for_admitted_family_fails_closed(tmp_path: Path) -> None:
    admission_service = _AdmissionService(_admission("claim"))
    coordinator = RetainedSourceReprojectionCoordinator(
        _database_path(tmp_path),
        admission_service=admission_service,
        executor_registry=RetainedSourceReprojectionExecutorRegistry(()),
    )

    with pytest.raises(RetainedSourceReprojectionCoordinatorError) as caught:
        coordinator.execute(41, completed_at=COMPLETED_AT)

    assert caught.value.reason is RetainedSourceReprojectionCoordinatorRejection.MISSING_EXECUTOR


def test_malformed_admission_fails_closed_before_dispatch(tmp_path: Path) -> None:
    executor = _Executor(_Result(41, 2, 9, 1, False))
    coordinator = _coordinator(tmp_path, "claim", executor, _MalformedAdmissionService())

    with pytest.raises(RetainedSourceReprojectionCoordinatorError) as caught:
        coordinator.execute(41, completed_at=COMPLETED_AT)

    assert caught.value.reason is RetainedSourceReprojectionCoordinatorRejection.INVALID_REQUEST
    assert executor.calls == []


def test_duplicate_dispatch_registration_is_rejected() -> None:
    with pytest.raises(RetainedSourceReprojectionCoordinatorConfigurationError) as caught:
        RetainedSourceReprojectionExecutorRegistry(
            (
                ("claim", lambda admission_service: _Executor()),
                ("claim", lambda admission_service: _Executor()),
            )
        )

    assert (
        caught.value.reason
        is RetainedSourceReprojectionCoordinatorRejection.DUPLICATE_REGISTRATION
    )


def test_executor_exception_rolls_back_all_writes(tmp_path: Path) -> None:
    executor = _Executor(fail=True, write_count=2)
    coordinator = _coordinator(tmp_path, "claim", executor)

    with pytest.raises(RetainedSourceReprojectionCoordinatorError) as caught:
        coordinator.execute(41, completed_at=COMPLETED_AT)

    assert caught.value.reason is RetainedSourceReprojectionCoordinatorRejection.EXECUTOR_FAILED
    connection = sqlite3.connect(_database_path(tmp_path))
    assert connection.execute("SELECT COUNT(*) FROM coordinator_writes").fetchone()[0] == 0
    connection.close()


def test_transaction_runner_failure_is_propagated_without_dispatch(tmp_path: Path) -> None:
    executor = _Executor(_Result(41, 2, 9, 1, False))

    def failing_runner(path, callback):
        raise RuntimeError("fixture transaction runner failure")

    coordinator = _coordinator(
        tmp_path,
        "claim",
        executor,
        transaction_runner=failing_runner,
    )

    with pytest.raises(RuntimeError, match="fixture transaction runner failure"):
        coordinator.execute(41, completed_at=COMPLETED_AT)
    assert executor.calls == []


def test_incoherent_executor_result_rolls_back_all_writes(tmp_path: Path) -> None:
    executor = _Executor(_Result(41, 999, 9, 1, False), write_count=2)
    coordinator = _coordinator(tmp_path, "claim", executor)

    with pytest.raises(RetainedSourceReprojectionCoordinatorError) as caught:
        coordinator.execute(41, completed_at=COMPLETED_AT)

    assert (
        caught.value.reason
        is RetainedSourceReprojectionCoordinatorRejection.MALFORMED_EXECUTOR_RESULT
    )
    connection = sqlite3.connect(_database_path(tmp_path))
    assert connection.execute("SELECT COUNT(*) FROM coordinator_writes").fetchone()[0] == 0
    connection.close()


def test_repeated_execution_leaves_replay_semantics_to_executor(tmp_path: Path) -> None:
    executor = _Executor(_Result(41, 2, 9, 0, True))
    coordinator = _coordinator(tmp_path, "claim", executor)

    first = coordinator.execute(41, completed_at=COMPLETED_AT)
    second = coordinator.execute(41, completed_at=COMPLETED_AT)

    assert len(executor.calls) == 2
    assert first.replay_skipped is True
    assert second.replay_skipped is True


def test_invalid_request_does_not_start_a_transaction(tmp_path: Path) -> None:
    executor = _Executor(_Result(41, 2, 9, 1, False))
    runner_calls = 0

    def runner(path, callback):
        nonlocal runner_calls
        runner_calls += 1
        return run_write_transaction(path, callback)

    coordinator = _coordinator(tmp_path, "claim", executor, transaction_runner=runner)
    with pytest.raises(RetainedSourceReprojectionCoordinatorError) as caught:
        coordinator.execute(0, completed_at=COMPLETED_AT)

    assert caught.value.reason is RetainedSourceReprojectionCoordinatorRejection.INVALID_REQUEST
    assert runner_calls == 0
