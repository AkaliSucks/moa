"""Run one retained-source reprojection behind the production operator boundary."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Literal

from moa.database.migrations import MigrationError
from moa.database.sqlite import connect_read_only
from moa.repositories.data_health_repository import (
    DataHealthRepository,
    DataHealthSchemaError,
)
from moa.repositories.projection_link_repository import (
    ProjectionLinkIntegrityError,
    ProjectionLinkRepository,
)
from moa.services.listener_process_guard import (
    ListenerAlreadyRunningError,
    ListenerProcessGuard,
    ListenerProcessGuardError,
    ListenerProcessGuardResourceError,
)
from moa.services.projection_authority import get_projection_authority
from moa.services.projection_expectations import (
    DurableProjectionExpectationFactsError,
    load_durable_projection_expectation_facts,
    resolve_expected_projections,
)
from moa.services.retained_source_reprojection_admission_service import (
    ReprojectionAdmissionRejection,
)
from moa.services.retained_source_reprojection_coordinator import (
    RetainedSourceReprojectionCoordinator,
    RetainedSourceReprojectionCoordinatorRejection,
    RetainedSourceReprojectionCoordinatorError,
    RetainedSourceReprojectionExecutionResult,
)


class RetainedSourceReprojectionOperatorFailure(Enum):
    """Bounded operator failure classifications."""

    INVALID_SOURCE_EVENT_ID = "invalid_source_event_id"
    INVALID_DATABASE_PATH = "invalid_database_path"
    SCHEMA_INVALID = "schema_invalid"
    LISTENER_OWNERSHIP_UNAVAILABLE = "listener_ownership_unavailable"
    ADMISSION_REJECTED = "admission_rejected"
    COORDINATOR_FAILED = "coordinator_failed"
    VERIFICATION_FAILED = "verification_failed"


class RetainedSourceReprojectionOperatorError(RuntimeError):
    """Raised when the operator cannot safely complete one source operation."""

    def __init__(
        self,
        reason: RetainedSourceReprojectionOperatorFailure,
        *,
        admission_reason: ReprojectionAdmissionRejection | None = None,
    ) -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.admission_reason = admission_reason


@dataclass(frozen=True, slots=True)
class RetainedSourceReprojectionVerification:
    """Bounded proof of the exact source's committed current-generation links."""

    source_event_id: int
    source_family: str
    import_event_id: int
    current_generation_id: int
    expected_link_count: int
    completed_link_count: int
    status: str = "passed"


@dataclass(frozen=True, slots=True)
class RetainedSourceReprojectionOperatorResult:
    """The coordinator result paired with its separate read-only verification."""

    execution: RetainedSourceReprojectionExecutionResult
    verification: RetainedSourceReprojectionVerification


CoordinatorFactory = Callable[[Path], RetainedSourceReprojectionCoordinator]


def _explicit_database_path(database_path: Path) -> Path:
    """Return one existing, canonical file-backed database authority."""
    if not isinstance(database_path, Path):
        raise RetainedSourceReprojectionOperatorError(
            RetainedSourceReprojectionOperatorFailure.INVALID_DATABASE_PATH
        )
    raw = str(database_path)
    if raw == ":memory:" or raw.startswith("file:"):
        raise RetainedSourceReprojectionOperatorError(
            RetainedSourceReprojectionOperatorFailure.INVALID_DATABASE_PATH
        )
    try:
        resolved = database_path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise RetainedSourceReprojectionOperatorError(
            RetainedSourceReprojectionOperatorFailure.INVALID_DATABASE_PATH
        ) from None
    if not resolved.is_file():
        raise RetainedSourceReprojectionOperatorError(
            RetainedSourceReprojectionOperatorFailure.INVALID_DATABASE_PATH
        )
    return resolved


def _read_only(database_path: Path) -> sqlite3.Connection:
    """Open one read-only transaction; the caller owns cleanup."""
    connection = connect_read_only(database_path)
    try:
        connection.execute("BEGIN")
    except BaseException:
        connection.close()
        raise
    return connection


class RetainedSourceReprojectionOperator:
    """Enforce quiescence, execute one coordinator operation, and verify it."""

    def __init__(
        self,
        database_path: Path,
        *,
        coordinator_factory: CoordinatorFactory = RetainedSourceReprojectionCoordinator,
        guard_factory: Callable[[Path], ListenerProcessGuard] = ListenerProcessGuard,
    ) -> None:
        self._database_path = _explicit_database_path(database_path)
        self._coordinator_factory = coordinator_factory
        self._guard_factory = guard_factory

    @property
    def database_path(self) -> Path:
        """Return the one canonical database authority used by every phase."""
        return self._database_path

    def execute(self, source_event_id: int) -> RetainedSourceReprojectionOperatorResult:
        """Execute exactly one source under the listener guard."""
        if (
            isinstance(source_event_id, bool)
            or not isinstance(source_event_id, int)
            or source_event_id <= 0
        ):
            raise RetainedSourceReprojectionOperatorError(
                RetainedSourceReprojectionOperatorFailure.INVALID_SOURCE_EVENT_ID
            )
        with self._acquired_guard():
            self._validate_schema()
            try:
                coordinator = self._coordinator_factory(self._database_path)
                execution = coordinator.execute(source_event_id)
            except RetainedSourceReprojectionCoordinatorError as error:
                if error.reason is RetainedSourceReprojectionCoordinatorRejection.ADMISSION_REJECTED:
                    raise RetainedSourceReprojectionOperatorError(
                        RetainedSourceReprojectionOperatorFailure.ADMISSION_REJECTED,
                        admission_reason=error.admission_reason,
                    ) from None
                raise RetainedSourceReprojectionOperatorError(
                    RetainedSourceReprojectionOperatorFailure.COORDINATOR_FAILED
                ) from None
            except Exception:
                raise RetainedSourceReprojectionOperatorError(
                    RetainedSourceReprojectionOperatorFailure.COORDINATOR_FAILED
                ) from None

            try:
                verification = self._verify(execution)
            except Exception:
                raise RetainedSourceReprojectionOperatorError(
                    RetainedSourceReprojectionOperatorFailure.VERIFICATION_FAILED
                ) from None
        return RetainedSourceReprojectionOperatorResult(execution, verification)

    class _GuardContext:
        def __init__(self, guard: ListenerProcessGuard) -> None:
            self.guard = guard

        def __enter__(self) -> ListenerProcessGuard:
            try:
                self.guard.acquire()
            except (ListenerAlreadyRunningError, ListenerProcessGuardResourceError):
                raise RetainedSourceReprojectionOperatorError(
                    RetainedSourceReprojectionOperatorFailure.LISTENER_OWNERSHIP_UNAVAILABLE
                ) from None
            except ListenerProcessGuardError:
                raise RetainedSourceReprojectionOperatorError(
                    RetainedSourceReprojectionOperatorFailure.LISTENER_OWNERSHIP_UNAVAILABLE
                ) from None
            return self.guard

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> Literal[False]:
            try:
                self.guard.release()
            except ListenerProcessGuardError:
                if exc is None:
                    raise RetainedSourceReprojectionOperatorError(
                        RetainedSourceReprojectionOperatorFailure.LISTENER_OWNERSHIP_UNAVAILABLE
                    ) from None
            return False

    def _acquired_guard(self) -> _GuardContext:
        return self._GuardContext(self._guard_factory(self._database_path))

    def _validate_schema(self) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = _read_only(self._database_path)
            DataHealthRepository(connection).validate_schema()
        except (DataHealthSchemaError, MigrationError, sqlite3.Error, OSError, ValueError):
            raise RetainedSourceReprojectionOperatorError(
                RetainedSourceReprojectionOperatorFailure.SCHEMA_INVALID
            ) from None
        except Exception:
            raise RetainedSourceReprojectionOperatorError(
                RetainedSourceReprojectionOperatorFailure.SCHEMA_INVALID
            ) from None
        finally:
            _close_read_only(connection)

    def _verify(
        self, execution: RetainedSourceReprojectionExecutionResult
    ) -> RetainedSourceReprojectionVerification:
        connection: sqlite3.Connection | None = None
        try:
            connection = _read_only(self._database_path)
            source = connection.execute(
                """
                SELECT id, status, legacy_import_event_id
                FROM discord_source_events
                WHERE id = ?
                """,
                (execution.source_event_id,),
            ).fetchone()
            if source is None or int(source["id"]) != execution.source_event_id:
                raise ValueError("source identity")
            if str(source["status"]) != "succeeded":
                raise ValueError("source status")
            import_event_id = _positive_int(source["legacy_import_event_id"])
            if import_event_id != execution.import_event_id:
                raise ValueError("import provenance")

            imported = connection.execute(
                "SELECT id, kind FROM import_events WHERE id = ?", (import_event_id,)
            ).fetchone()
            if imported is None or str(imported["kind"]) != execution.source_family:
                raise ValueError("import identity")
            references = connection.execute(
                "SELECT COUNT(*) FROM discord_source_events WHERE legacy_import_event_id = ?",
                (import_event_id,),
            ).fetchone()[0]
            if int(references) != 1:
                raise ValueError("import ownership")

            facts = load_durable_projection_expectation_facts(
                connection, execution.source_event_id
            )
            if facts.source_family != execution.source_family:
                raise ValueError("expectation family")
            expected = resolve_expected_projections(facts).known_expected_identities
            if not expected:
                raise ValueError("expected projections")

            current_generation_id = ProjectionLinkRepository(
                connection
            ).resolve_current_generation_id()
            if current_generation_id != execution.current_generation_id:
                raise ValueError("current generation")
            links = ProjectionLinkRepository(connection).load_links(
                source_event_id=execution.source_event_id,
                generation_id=current_generation_id,
            )
            if len(links) != len(expected) or len(links) != execution.linked_count:
                raise ValueError("link count")
            expected_keys = {
                (identity.projection_kind, identity.projection_slot) for identity in expected
            }
            actual_keys: set[tuple[str, str]] = set()
            for link in links:
                key = (str(link["projection_kind"]), str(link["projection_slot"]))
                if key in actual_keys or key not in expected_keys:
                    raise ValueError("link identity")
                actual_keys.add(key)
                if (
                    str(link["state"]) != "completed"
                    or link["completed_at"] is None
                    or link["projection_row_id"] is None
                ):
                    raise ValueError("link completion")
                authority = get_projection_authority(key[0])
                if str(link["projection_table"]) != authority.target_table:
                    raise ValueError("link authority")
                target_id = _positive_int(link["projection_row_id"])
                if authority.target_table == "import_events":
                    target = connection.execute(
                        "SELECT id, kind FROM import_events WHERE id = ?", (target_id,)
                    ).fetchone()
                    if (
                        target is None
                        or target_id != import_event_id
                        or str(target["kind"]) != "antidisable"
                    ):
                        raise ValueError("antidisable target")
                else:
                    target = connection.execute(
                        f"SELECT id, import_event_id FROM {authority.target_table} WHERE id = ?",
                        (target_id,),
                    ).fetchone()
                    if target is None or _positive_int(target["import_event_id"]) != import_event_id:
                        raise ValueError("projection target")
            if actual_keys != expected_keys:
                raise ValueError("projection set")
            return RetainedSourceReprojectionVerification(
                source_event_id=execution.source_event_id,
                source_family=execution.source_family,
                import_event_id=import_event_id,
                current_generation_id=current_generation_id,
                expected_link_count=len(expected),
                completed_link_count=len(links),
            )
        except (
            DurableProjectionExpectationFactsError,
            KeyError,
            ProjectionLinkIntegrityError,
            sqlite3.Error,
            TypeError,
            ValueError,
        ):
            raise
        finally:
            _close_read_only(connection)


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("expected positive integer")
    return value


def _close_read_only(connection: sqlite3.Connection | None) -> None:
    if connection is None:
        return
    try:
        if connection.in_transaction:
            connection.rollback()
    finally:
        connection.close()
