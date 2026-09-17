"""Coordinate one admitted retained-source reprojection in one write transaction."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, TypeAlias, cast

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
    RetainedSourceReprojectionAdmissionService,
    _SUPPORTED_FAMILIES,
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


class RetainedSourceReprojectionCoordinatorRejection(Enum):
    """Fail-closed coordinator classifications."""

    INVALID_REQUEST = "invalid_request"
    TRANSACTION_REQUIRED = "transaction_required"
    ADMISSION_REJECTED = "admission_rejected"
    UNKNOWN_FAMILY = "unknown_family"
    MISSING_EXECUTOR = "missing_executor"
    DUPLICATE_REGISTRATION = "duplicate_registration"
    MALFORMED_EXECUTOR_RESULT = "malformed_executor_result"
    EXECUTOR_FAILED = "executor_failed"


class RetainedSourceReprojectionCoordinatorError(RuntimeError):
    """Raised when one retained-source execution cannot complete safely."""

    def __init__(
        self,
        reason: RetainedSourceReprojectionCoordinatorRejection,
        detail: str,
        *,
        admission_reason: ReprojectionAdmissionRejection | None = None,
    ) -> None:
        super().__init__(detail)
        self.reason = reason
        self.admission_reason = admission_reason


class RetainedSourceReprojectionCoordinatorConfigurationError(
    RetainedSourceReprojectionCoordinatorError
):
    """Raised when the static executor dispatch configuration is invalid."""


class RetainedSourceReprojectionExecutorResult(Protocol):
    """The bounded result contract shared by all retained-source executors."""

    source_event_id: int
    current_generation_id: int
    import_event_id: int
    linked_count: int
    replay_skipped: bool


class RetainedSourceReprojectionExecutor(Protocol):
    """One family-specific caller-transaction executor."""

    def execute(
        self,
        connection: sqlite3.Connection,
        admission: RetainedSourceReprojectionAdmission,
        completed_at: datetime,
    ) -> object: ...


ExecutorFactory: TypeAlias = Callable[
    [RetainedSourceReprojectionAdmissionService], RetainedSourceReprojectionExecutor
]
TransactionRunner: TypeAlias = Callable[
    [Path, Callable[[sqlite3.Connection], "RetainedSourceReprojectionExecutionResult"]],
    "RetainedSourceReprojectionExecutionResult",
]


@dataclass(frozen=True, slots=True)
class RetainedSourceReprojectionExecutionResult:
    """Bounded metadata around one family executor result."""

    source_event_id: int
    source_family: str
    executor_name: str
    terminal_status: str
    executor_result: RetainedSourceReprojectionExecutorResult

    @property
    def current_generation_id(self) -> int:
        return self.executor_result.current_generation_id

    @property
    def import_event_id(self) -> int:
        return self.executor_result.import_event_id

    @property
    def linked_count(self) -> int:
        return self.executor_result.linked_count

    @property
    def replay_skipped(self) -> bool:
        return self.executor_result.replay_skipped


ADMITTED_REPROJECTION_FAMILIES = frozenset(_SUPPORTED_FAMILIES)


class RetainedSourceReprojectionExecutorRegistry:
    """Immutable, duplicate-resistant family-to-executor factory registry."""

    def __init__(self, entries: Iterable[tuple[str, ExecutorFactory]]) -> None:
        factories: dict[str, ExecutorFactory] = {}
        for family, factory in entries:
            if not isinstance(family, str) or not family:
                raise RetainedSourceReprojectionCoordinatorConfigurationError(
                    RetainedSourceReprojectionCoordinatorRejection.UNKNOWN_FAMILY,
                    "executor family identity must be a non-empty string",
                )
            if family not in ADMITTED_REPROJECTION_FAMILIES:
                raise RetainedSourceReprojectionCoordinatorConfigurationError(
                    RetainedSourceReprojectionCoordinatorRejection.UNKNOWN_FAMILY,
                    "executor registry contains an unadmitted family",
                )
            if family in factories:
                raise RetainedSourceReprojectionCoordinatorConfigurationError(
                    RetainedSourceReprojectionCoordinatorRejection.DUPLICATE_REGISTRATION,
                    "executor registry contains duplicate family registration",
                )
            factories[family] = factory
        self._factories = MappingProxyType(factories)

    @property
    def families(self) -> frozenset[str]:
        return frozenset(self._factories)

    def create(
        self,
        family: str,
        admission_service: RetainedSourceReprojectionAdmissionService,
    ) -> RetainedSourceReprojectionExecutor | None:
        factory = self._factories.get(family)
        return None if factory is None else factory(admission_service)


def _singleton_factory(
    admission_service: RetainedSourceReprojectionAdmissionService,
) -> RetainedSourceReprojectionExecutor:
    return RetainedSourceSingletonReprojectionExecutor(admission_service)


def _server_settings_factory(
    admission_service: RetainedSourceReprojectionAdmissionService,
) -> RetainedSourceReprojectionExecutor:
    return RetainedSourceServerSettingsReprojectionExecutor(admission_service)


def _kakeraloot_settings_factory(
    admission_service: RetainedSourceReprojectionAdmissionService,
) -> RetainedSourceReprojectionExecutor:
    return RetainedSourceKakeralootSettingsReprojectionExecutor(admission_service)


def _timer_factory(
    admission_service: RetainedSourceReprojectionAdmissionService,
) -> RetainedSourceReprojectionExecutor:
    return RetainedSourceTimerReprojectionExecutor(admission_service)


def _roll_factory(
    admission_service: RetainedSourceReprojectionAdmissionService,
) -> RetainedSourceReprojectionExecutor:
    return RetainedSourceRollReprojectionExecutor(admission_service)


def _antidisable_factory(
    admission_service: RetainedSourceReprojectionAdmissionService,
) -> RetainedSourceReprojectionExecutor:
    return RetainedSourceAntidisablePageReprojectionExecutor(admission_service)


_DEFAULT_DISPATCH_ENTRIES: tuple[tuple[str, ExecutorFactory], ...] = (
    ("claim", _singleton_factory),
    ("server_settings", _server_settings_factory),
    ("kakeraloot_settings", _kakeraloot_settings_factory),
    ("kakera_state", _singleton_factory),
    ("mudapins", _singleton_factory),
    ("player_bonus", _singleton_factory),
    ("wishlist", _singleton_factory),
    ("disablelist", _singleton_factory),
    ("timer_state", _timer_factory),
    ("tower_state", _singleton_factory),
    ("kakeraloot_state", _singleton_factory),
    ("sphere_result", _singleton_factory),
    ("profile", _singleton_factory),
    ("roll", _roll_factory),
    ("antidisable", _antidisable_factory),
)

DEFAULT_REPROJECTION_EXECUTOR_REGISTRY = RetainedSourceReprojectionExecutorRegistry(
    _DEFAULT_DISPATCH_ENTRIES
)


class RetainedSourceReprojectionCoordinator:
    """Admit and execute exactly one retained source inside one write transaction."""

    def __init__(
        self,
        database_path: Path,
        *,
        admission_service: RetainedSourceReprojectionAdmissionService | None = None,
        executor_registry: RetainedSourceReprojectionExecutorRegistry
        | None = None,
        transaction_runner: TransactionRunner = run_write_transaction,
    ) -> None:
        self._database_path = Path(database_path)
        self._admission_service = admission_service or RetainedSourceReprojectionAdmissionService()
        self._executor_registry = executor_registry or DEFAULT_REPROJECTION_EXECUTOR_REGISTRY
        self._transaction_runner = transaction_runner

    def execute(
        self,
        source_event_id: int,
        *,
        completed_at: datetime | None = None,
    ) -> RetainedSourceReprojectionExecutionResult:
        """Admit and execute one source event; the transaction runner owns its lifecycle."""
        if isinstance(source_event_id, bool) or not isinstance(source_event_id, int):
            raise RetainedSourceReprojectionCoordinatorError(
                RetainedSourceReprojectionCoordinatorRejection.INVALID_REQUEST,
                "source_event_id must be a positive integer",
            )
        if source_event_id <= 0:
            raise RetainedSourceReprojectionCoordinatorError(
                RetainedSourceReprojectionCoordinatorRejection.INVALID_REQUEST,
                "source_event_id must be a positive integer",
            )
        execution_time = completed_at or datetime.now(timezone.utc)

        def execute_with_connection(
            connection: sqlite3.Connection,
        ) -> RetainedSourceReprojectionExecutionResult:
            if not connection.in_transaction:
                raise RetainedSourceReprojectionCoordinatorError(
                    RetainedSourceReprojectionCoordinatorRejection.TRANSACTION_REQUIRED,
                    "coordinator requires a runner-owned transaction",
                )
            try:
                admission = self._admission_service.admit(connection, source_event_id)
            except RetainedSourceReprojectionAdmissionError as error:
                raise RetainedSourceReprojectionCoordinatorError(
                    RetainedSourceReprojectionCoordinatorRejection.ADMISSION_REJECTED,
                    "retained-source admission rejected the execution request",
                    admission_reason=error.reason,
                ) from error
            return self._execute_admitted(connection, admission, execution_time)

        return self._transaction_runner(self._database_path, execute_with_connection)

    def _execute_admitted(
        self,
        connection: sqlite3.Connection,
        admission: RetainedSourceReprojectionAdmission,
        completed_at: datetime,
    ) -> RetainedSourceReprojectionExecutionResult:
        if not isinstance(admission, RetainedSourceReprojectionAdmission):
            raise RetainedSourceReprojectionCoordinatorError(
                RetainedSourceReprojectionCoordinatorRejection.INVALID_REQUEST,
                "admission must be a RetainedSourceReprojectionAdmission",
            )
        if (
            isinstance(admission.source_event_id, bool)
            or not isinstance(admission.source_event_id, int)
            or admission.source_event_id <= 0
        ):
            raise RetainedSourceReprojectionCoordinatorError(
                RetainedSourceReprojectionCoordinatorRejection.INVALID_REQUEST,
                "admission source identity is malformed",
            )
        if admission.source_family not in ADMITTED_REPROJECTION_FAMILIES:
            raise RetainedSourceReprojectionCoordinatorError(
                RetainedSourceReprojectionCoordinatorRejection.UNKNOWN_FAMILY,
                "admission family is not admitted",
            )
        executor = self._executor_registry.create(admission.source_family, self._admission_service)
        if executor is None:
            raise RetainedSourceReprojectionCoordinatorError(
                RetainedSourceReprojectionCoordinatorRejection.MISSING_EXECUTOR,
                "no executor is registered for the admitted family",
            )
        try:
            executor_result = cast(
                RetainedSourceReprojectionExecutorResult,
                executor.execute(connection, admission, completed_at),
            )
        except RetainedSourceReprojectionCoordinatorError:
            raise
        except Exception as error:
            raise RetainedSourceReprojectionCoordinatorError(
                RetainedSourceReprojectionCoordinatorRejection.EXECUTOR_FAILED,
                "retained-source executor failed; transaction must roll back",
            ) from error
        self._validate_executor_result(admission, executor_result)
        return RetainedSourceReprojectionExecutionResult(
            source_event_id=admission.source_event_id,
            source_family=admission.source_family,
            executor_name=type(executor).__name__,
            terminal_status="success",
            executor_result=executor_result,
        )

    @staticmethod
    def _validate_executor_result(
        admission: RetainedSourceReprojectionAdmission,
        result: RetainedSourceReprojectionExecutorResult,
    ) -> None:
        try:
            valid = (
                isinstance(result.source_event_id, int)
                and not isinstance(result.source_event_id, bool)
                and result.source_event_id == admission.source_event_id
                and isinstance(result.current_generation_id, int)
                and not isinstance(result.current_generation_id, bool)
                and result.current_generation_id == admission.current_generation_id
                and isinstance(result.import_event_id, int)
                and not isinstance(result.import_event_id, bool)
                and result.import_event_id == admission.import_event_id
                and isinstance(result.linked_count, int)
                and not isinstance(result.linked_count, bool)
                and result.linked_count >= 0
                and isinstance(result.replay_skipped, bool)
            )
        except (AttributeError, TypeError):
            valid = False
        if not valid:
            raise RetainedSourceReprojectionCoordinatorError(
                RetainedSourceReprojectionCoordinatorRejection.MALFORMED_EXECUTOR_RESULT,
                "retained-source executor returned an incoherent result",
            )
