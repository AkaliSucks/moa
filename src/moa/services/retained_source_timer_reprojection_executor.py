"""Link-only Timer State reprojection from admitted retained evidence."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.projection_link_repository import (
    ProjectionLinkIntegrityError,
    ProjectionLinkRepository,
)
from moa.services.retained_source_reprojection_admission_service import (
    DurableReprojectionPayload,
    ReprojectionAdmissionRejection,
    RetainedSourceReprojectionAdmission,
    RetainedSourceReprojectionAdmissionError,
    RetainedSourceReprojectionAdmissionService,
)


class RetainedSourceTimerReprojectionError(RuntimeError):
    """Raised when an admitted Timer reprojection cannot execute exactly."""


@dataclass(frozen=True, slots=True)
class RetainedSourceTimerReprojectionResult:
    """The link-only outcome of one retained-source Timer execution."""

    source_event_id: int
    current_generation_id: int
    import_event_id: int
    timer_state_observation_id: int
    linked_count: int
    replay_skipped: bool
    projection_target: tuple[str, int]


class RetainedSourceTimerReprojectionExecutor:
    """Establish one missing current Timer link without importing or closing lifecycle."""

    _SOURCE_FAMILY = "timer_state"
    _PROJECTION_KIND = "catalog.timer_state"
    _PROJECTION_TABLE = "timer_state_observations"
    _SAVEPOINT = "retained_source_timer_reprojection"

    def __init__(
        self, admission_service: RetainedSourceReprojectionAdmissionService | None = None
    ) -> None:
        self._admission_service = admission_service or RetainedSourceReprojectionAdmissionService()

    def execute(
        self,
        connection: sqlite3.Connection,
        admission: RetainedSourceReprojectionAdmission,
        completed_at: datetime,
    ) -> RetainedSourceTimerReprojectionResult:
        """Validate and complete the admitted Timer link in a caller-owned transaction."""
        if not connection.in_transaction:
            raise RetainedSourceTimerReprojectionError(
                "Timer reprojection requires a caller-owned transaction"
            )
        completed_at = self._normalize_datetime(completed_at)
        identity, payload = self._validate_admission_shape(admission)

        links = ProjectionLinkRepository(connection)
        current_generation_id = self._resolve_current_generation(links)
        if current_generation_id != admission.current_generation_id:
            raise RetainedSourceTimerReprojectionError(
                "Timer reprojection admission is stale for the current generation"
            )

        current_links = links.load_links(
            source_event_id=admission.source_event_id,
            generation_id=current_generation_id,
        )
        if current_links:
            return self._validate_replay(
                connection,
                admission,
                identity.projection_slot,
                payload,
                current_links,
            )

        try:
            refreshed = self._admission_service.admit(connection, admission.source_event_id)
        except RetainedSourceReprojectionAdmissionError as error:
            raise RetainedSourceTimerReprojectionError(
                f"Timer reprojection admission no longer holds: {error.reason.value}"
            ) from error
        if refreshed != admission:
            raise RetainedSourceTimerReprojectionError(
                "Timer reprojection admission changed before execution"
            )
        if self._resolve_current_generation(links) != admission.current_generation_id:
            raise RetainedSourceTimerReprojectionError(
                "Timer reprojection generation changed before execution"
            )
        if links.load_links(
            source_event_id=admission.source_event_id,
            generation_id=admission.current_generation_id,
        ):
            raise RetainedSourceTimerReprojectionError(
                "Timer reprojection current links changed before execution"
            )

        connection.execute(f"SAVEPOINT {self._SAVEPOINT}")
        try:
            links.claim_link(
                source_event_id=admission.source_event_id,
                generation_id=admission.current_generation_id,
                projection_kind=self._PROJECTION_KIND,
                projection_slot=identity.projection_slot,
                claimed_at=completed_at,
            )
            claimed = links.load_links(
                source_event_id=admission.source_event_id,
                generation_id=admission.current_generation_id,
            )
            if len(claimed) != 1 or not self._is_exact_claimed_link(
                claimed[0], identity.projection_slot
            ):
                raise RetainedSourceTimerReprojectionError(
                    "Timer reprojection did not claim exactly the admitted link"
                )
            links.complete_claimed_link(
                source_event_id=admission.source_event_id,
                generation_id=admission.current_generation_id,
                projection_kind=self._PROJECTION_KIND,
                projection_slot=identity.projection_slot,
                projection_table=self._PROJECTION_TABLE,
                projection_row_id=payload.historical_target_id,
                completed_at=completed_at,
            )
            completed = links.load_links(
                source_event_id=admission.source_event_id,
                generation_id=admission.current_generation_id,
            )
            if len(completed) != 1 or not self._is_exact_completed_link(
                completed[0], identity.projection_slot, payload.historical_target_id
            ):
                raise RetainedSourceTimerReprojectionError(
                    "Timer reprojection did not complete exactly the admitted link"
                )
            connection.execute(f"RELEASE SAVEPOINT {self._SAVEPOINT}")
        except Exception:
            connection.execute(f"ROLLBACK TO SAVEPOINT {self._SAVEPOINT}")
            connection.execute(f"RELEASE SAVEPOINT {self._SAVEPOINT}")
            raise

        return self._result(admission, payload, linked_count=1, replay_skipped=False)

    def _validate_replay(
        self,
        connection: sqlite3.Connection,
        admission: RetainedSourceReprojectionAdmission,
        projection_slot: str,
        payload: DurableReprojectionPayload,
        current_links: tuple[sqlite3.Row, ...],
    ) -> RetainedSourceTimerReprojectionResult:
        try:
            self._admission_service.admit(connection, admission.source_event_id)
        except RetainedSourceReprojectionAdmissionError as error:
            if error.reason is not ReprojectionAdmissionRejection.CURRENT_LINKS_ALREADY_COMPLETE:
                raise RetainedSourceTimerReprojectionError(
                    f"Timer reprojection replay evidence is invalid: {error.reason.value}"
                ) from error
        else:
            raise RetainedSourceTimerReprojectionError(
                "Timer reprojection replay unexpectedly remained writable"
            )

        if len(current_links) != 1 or not self._is_exact_completed_link(
            current_links[0], projection_slot, payload.historical_target_id
        ):
            raise RetainedSourceTimerReprojectionError(
                "Timer reprojection replay does not exactly match the completed current link"
            )
        self._validate_admission_against_durable_replay(connection, admission)
        return self._result(admission, payload, linked_count=0, replay_skipped=True)

    def _validate_admission_against_durable_replay(
        self,
        connection: sqlite3.Connection,
        admission: RetainedSourceReprojectionAdmission,
    ) -> None:
        source = connection.execute(
            "SELECT legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (admission.source_event_id,),
        ).fetchone()
        if source is None or source["legacy_import_event_id"] != admission.import_event_id:
            raise RetainedSourceTimerReprojectionError(
                "Timer reprojection admission has forged import provenance"
            )
        attempt = connection.execute(
            "SELECT id FROM discord_processing_attempts WHERE source_event_id = ? AND status = 'succeeded'",
            (admission.source_event_id,),
        ).fetchall()
        if len(attempt) != 1 or int(attempt[0]["id"]) != admission.successful_attempt_id:
            raise RetainedSourceTimerReprojectionError(
                "Timer reprojection admission has forged attempt provenance"
            )
        imported = connection.execute(
            "SELECT kind FROM import_events WHERE id = ?", (admission.import_event_id,)
        ).fetchone()
        if imported is None or str(imported["kind"]) != admission.source_family:
            raise RetainedSourceTimerReprojectionError(
                "Timer reprojection admission has forged source family"
            )
        server = connection.execute(
            "SELECT server_name FROM discord_source_event_server_attributions WHERE source_event_id = ?",
            (admission.source_event_id,),
        ).fetchone()
        account = connection.execute(
            "SELECT server_name, account_name FROM discord_source_event_account_attributions WHERE source_event_id = ?",
            (admission.source_event_id,),
        ).fetchone()
        if (
            server is None
            or account is None
            or CatalogRepository._normalize(str(server["server_name"])) != admission.server
            or CatalogRepository._normalize(str(account["server_name"])) != admission.server
            or CatalogRepository._normalize(str(account["account_name"])) != admission.account
        ):
            raise RetainedSourceTimerReprojectionError(
                "Timer reprojection admission has forged attribution"
            )

        historical = connection.execute(
            "SELECT * FROM discord_projection_links WHERE source_event_id = ? AND generation_id != ? ORDER BY generation_id, id",
            (admission.source_event_id, admission.current_generation_id),
        ).fetchall()
        if not historical:
            raise RetainedSourceTimerReprojectionError(
                "Timer reprojection admission has no historical evidence"
            )
        groups: dict[int, list[sqlite3.Row]] = {}
        for row in historical:
            groups.setdefault(int(row["generation_id"]), []).append(row)
        historical_generation_id = max(groups)
        if historical_generation_id != admission.historical_generation_id:
            raise RetainedSourceTimerReprojectionError(
                "Timer reprojection admission has a forged historical generation"
            )
        for generation_links in groups.values():
            try:
                payloads = self._admission_service._validate_link_set(
                    connection,
                    tuple(generation_links),
                    admission.expected_identities,
                    admission.import_event_id,
                    admission.server,
                    admission.account,
                )
            except RetainedSourceReprojectionAdmissionError as error:
                raise RetainedSourceTimerReprojectionError(
                    f"Timer reprojection historical evidence changed: {error.reason.value}"
                ) from error
            if payloads != admission.payloads:
                raise RetainedSourceTimerReprojectionError(
                    "Timer reprojection admission payload changed before replay"
                )

    def _validate_admission_shape(self, admission):
        if not isinstance(admission, RetainedSourceReprojectionAdmission):
            raise TypeError("admission must be a RetainedSourceReprojectionAdmission")
        for field_name in (
            "source_event_id",
            "successful_attempt_id",
            "import_event_id",
            "current_generation_id",
            "historical_generation_id",
        ):
            value = getattr(admission, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise RetainedSourceTimerReprojectionError(
                    f"Timer reprojection admission has invalid {field_name}"
                )
        if admission.source_family != self._SOURCE_FAMILY or admission.account is None:
            raise RetainedSourceTimerReprojectionError(
                "Timer reprojection requires an account-scoped Timer admission"
            )
        if len(admission.expected_identities) != 1 or len(admission.payloads) != 1:
            raise RetainedSourceTimerReprojectionError(
                "Timer reprojection requires exactly one identity and payload"
            )
        identity = admission.expected_identities[0]
        payload = admission.payloads[0]
        if (
            identity.projection_kind != self._PROJECTION_KIND
            or not isinstance(identity.projection_slot, str)
            or not identity.projection_slot
            or payload.projection_kind != identity.projection_kind
            or payload.projection_slot != identity.projection_slot
            or payload.target_table != self._PROJECTION_TABLE
            or isinstance(payload.historical_target_id, bool)
            or not isinstance(payload.historical_target_id, int)
            or payload.historical_target_id <= 0
        ):
            raise RetainedSourceTimerReprojectionError(
                "Timer reprojection admission identity or target is malformed"
            )
        return identity, payload

    @classmethod
    def _is_exact_claimed_link(cls, row: sqlite3.Row, projection_slot: str) -> bool:
        return (
            str(row["projection_kind"]) == cls._PROJECTION_KIND
            and str(row["projection_slot"]) == projection_slot
            and str(row["state"]) == "claimed"
            and row["projection_table"] is None
            and row["projection_row_id"] is None
            and row["completed_at"] is None
        )

    @classmethod
    def _is_exact_completed_link(
        cls, row: sqlite3.Row, projection_slot: str, target_id: int
    ) -> bool:
        return (
            str(row["projection_kind"]) == cls._PROJECTION_KIND
            and str(row["projection_slot"]) == projection_slot
            and str(row["state"]) == "completed"
            and str(row["projection_table"]) == cls._PROJECTION_TABLE
            and row["projection_row_id"] == target_id
            and row["completed_at"] is not None
        )

    @staticmethod
    def _normalize_datetime(value: datetime) -> datetime:
        if not isinstance(value, datetime):
            raise TypeError("completed_at must be a datetime")
        if value.utcoffset() is None:
            raise ValueError("completed_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _resolve_current_generation(repository: ProjectionLinkRepository) -> int:
        try:
            return repository.resolve_current_generation_id()
        except (ProjectionLinkIntegrityError, TypeError, ValueError) as error:
            raise RetainedSourceTimerReprojectionError(
                "Timer reprojection requires one valid current generation"
            ) from error

    @staticmethod
    def _result(
        admission: RetainedSourceReprojectionAdmission,
        payload: DurableReprojectionPayload,
        *,
        linked_count: int,
        replay_skipped: bool,
    ) -> RetainedSourceTimerReprojectionResult:
        return RetainedSourceTimerReprojectionResult(
            source_event_id=admission.source_event_id,
            current_generation_id=admission.current_generation_id,
            import_event_id=admission.import_event_id,
            timer_state_observation_id=payload.historical_target_id,
            linked_count=linked_count,
            replay_skipped=replay_skipped,
            projection_target=(payload.target_table, payload.historical_target_id),
        )
