"""Link-only reprojection for the authorized retained-source singleton cohort."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Final, Mapping

from moa.repositories.projection_link_repository import (
    ProjectionLinkIntegrityError,
    ProjectionLinkRepository,
)
from moa.services.projection_expectations import (
    claim_projection_slot,
    server_account_projection_slot,
)
from moa.services.retained_source_reprojection_admission_service import (
    DurableReprojectionPayload,
    ReprojectionAdmissionRejection,
    RetainedSourceReprojectionAdmission,
    RetainedSourceReprojectionAdmissionError,
    RetainedSourceReprojectionAdmissionService,
)


class RetainedSourceSingletonReprojectionError(RuntimeError):
    """Raised when an admitted singleton reprojection cannot execute exactly."""


@dataclass(frozen=True, slots=True)
class RetainedSourceSingletonReprojectionResult:
    """The link-only outcome for one authorized singleton source family."""

    source_event_id: int
    source_family: str
    current_generation_id: int
    import_event_id: int
    linked_count: int
    replay_skipped: bool
    projection_target: tuple[str, int]


@dataclass(frozen=True, slots=True)
class _SingletonAuthority:
    projection_kind: str
    target_table: str


_AUTHORITIES: Final[Mapping[str, _SingletonAuthority]] = MappingProxyType(
    {
        "claim": _SingletonAuthority("catalog.claim", "claim_observations"),
        "kakera_state": _SingletonAuthority("catalog.kakera_state", "kakera_state_observations"),
        "mudapins": _SingletonAuthority("catalog.mudapins", "mudapin_observations"),
        "player_bonus": _SingletonAuthority("catalog.player_bonus", "player_bonus_observations"),
        "wishlist": _SingletonAuthority("catalog.wishlist", "wishlist_observations"),
        "disablelist": _SingletonAuthority("catalog.disablelist", "disablelist_observations"),
        "tower_state": _SingletonAuthority("catalog.tower_state", "tower_state_observations"),
        "kakeraloot_state": _SingletonAuthority(
            "catalog.kakeraloot_state", "kakeraloot_state_observations"
        ),
        "sphere_result": _SingletonAuthority("catalog.sphere_result", "sphere_result_observations"),
        "profile": _SingletonAuthority("catalog.profile", "profile_observations"),
    }
)


class RetainedSourceSingletonReprojectionExecutor:
    """Establish one missing singleton link without importing or closing lifecycle."""

    _SAVEPOINT = "retained_source_singleton_reprojection"

    def __init__(
        self, admission_service: RetainedSourceReprojectionAdmissionService | None = None
    ) -> None:
        self._admission_service = admission_service or RetainedSourceReprojectionAdmissionService()

    def execute(
        self,
        connection: sqlite3.Connection,
        admission: RetainedSourceReprojectionAdmission,
        completed_at: datetime,
    ) -> RetainedSourceSingletonReprojectionResult:
        """Validate and complete one admitted link in a caller-owned transaction."""
        if not connection.in_transaction:
            raise RetainedSourceSingletonReprojectionError(
                "singleton reprojection requires a caller-owned transaction"
            )
        completed_at = self._normalize_datetime(completed_at)
        authority, slot, payload = self._validate_admission_shape(admission)
        links = ProjectionLinkRepository(connection)
        current_generation_id = self._resolve_current_generation(links)
        if current_generation_id != admission.current_generation_id:
            raise RetainedSourceSingletonReprojectionError(
                "singleton reprojection admission is stale for the current generation"
            )

        current_links = links.load_links(
            source_event_id=admission.source_event_id,
            generation_id=current_generation_id,
        )
        if current_links:
            return self._validate_replay(
                connection, admission, authority, slot, payload, current_links
            )

        try:
            refreshed = self._admission_service.admit(connection, admission.source_event_id)
        except RetainedSourceReprojectionAdmissionError as error:
            raise RetainedSourceSingletonReprojectionError(
                f"singleton reprojection admission no longer holds: {error.reason.value}"
            ) from error
        if refreshed != admission:
            raise RetainedSourceSingletonReprojectionError(
                "singleton reprojection admission changed before execution"
            )
        if self._resolve_current_generation(links) != admission.current_generation_id:
            raise RetainedSourceSingletonReprojectionError(
                "singleton reprojection generation changed before execution"
            )
        if links.load_links(
            source_event_id=admission.source_event_id,
            generation_id=admission.current_generation_id,
        ):
            raise RetainedSourceSingletonReprojectionError(
                "singleton reprojection current links changed before execution"
            )

        connection.execute(f"SAVEPOINT {self._SAVEPOINT}")
        try:
            links.claim_link(
                source_event_id=admission.source_event_id,
                generation_id=admission.current_generation_id,
                projection_kind=authority.projection_kind,
                projection_slot=slot,
                claimed_at=completed_at,
            )
            claimed = links.load_links(
                source_event_id=admission.source_event_id,
                generation_id=admission.current_generation_id,
            )
            if len(claimed) != 1 or not self._is_exact_claimed_link(claimed[0], authority, slot):
                raise RetainedSourceSingletonReprojectionError(
                    "singleton reprojection did not claim exactly the admitted link"
                )
            links.complete_claimed_link(
                source_event_id=admission.source_event_id,
                generation_id=admission.current_generation_id,
                projection_kind=authority.projection_kind,
                projection_slot=slot,
                projection_table=authority.target_table,
                projection_row_id=payload.historical_target_id,
                completed_at=completed_at,
            )
            completed = links.load_links(
                source_event_id=admission.source_event_id,
                generation_id=admission.current_generation_id,
            )
            if len(completed) != 1 or not self._is_exact_completed_link(
                completed[0], authority, slot, payload.historical_target_id
            ):
                raise RetainedSourceSingletonReprojectionError(
                    "singleton reprojection did not complete exactly the admitted link"
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
        authority: _SingletonAuthority,
        slot: str,
        payload: DurableReprojectionPayload,
        current_links: tuple[sqlite3.Row, ...],
    ) -> RetainedSourceSingletonReprojectionResult:
        try:
            self._admission_service.admit(connection, admission.source_event_id)
        except RetainedSourceReprojectionAdmissionError as error:
            if error.reason is not ReprojectionAdmissionRejection.CURRENT_LINKS_ALREADY_COMPLETE:
                raise RetainedSourceSingletonReprojectionError(
                    f"singleton reprojection replay evidence is invalid: {error.reason.value}"
                ) from error
        else:
            raise RetainedSourceSingletonReprojectionError(
                "singleton reprojection replay unexpectedly remained writable"
            )
        if len(current_links) != 1 or not self._is_exact_completed_link(
            current_links[0], authority, slot, payload.historical_target_id
        ):
            raise RetainedSourceSingletonReprojectionError(
                "singleton reprojection replay does not exactly match the completed current link"
            )
        self._validate_admission_against_durable_replay(connection, admission)
        return self._result(admission, payload, linked_count=0, replay_skipped=True)

    def _validate_admission_against_durable_replay(
        self, connection: sqlite3.Connection, admission: RetainedSourceReprojectionAdmission
    ) -> None:
        source = connection.execute(
            "SELECT * FROM discord_source_events WHERE id = ?", (admission.source_event_id,)
        ).fetchone()
        if (
            source is None
            or str(source["status"]) != "succeeded"
            or source["raw_evidence_expired_at"] is not None
            or source["legacy_import_event_id"] != admission.import_event_id
        ):
            raise RetainedSourceSingletonReprojectionError(
                "singleton reprojection admission has forged source provenance"
            )
        attempts = connection.execute(
            "SELECT * FROM discord_processing_attempts WHERE source_event_id = ? ORDER BY id",
            (admission.source_event_id,),
        ).fetchall()
        try:
            successful_attempt_id = self._admission_service._validate_attempts(
                connection, admission.source_event_id
            )
        except RetainedSourceReprojectionAdmissionError as error:
            raise RetainedSourceSingletonReprojectionError(
                f"singleton reprojection attempt evidence changed: {error.reason.value}"
            ) from error
        if not attempts or successful_attempt_id != admission.successful_attempt_id:
            raise RetainedSourceSingletonReprojectionError(
                "singleton reprojection admission has forged attempt provenance"
            )
        try:
            import_event_id, family = self._admission_service._validate_provenance(
                connection, admission.source_event_id, source
            )
            server, account = self._admission_service._validate_attribution(
                connection,
                admission.source_event_id,
                family,
                admission.server,
                admission.account,
            )
        except RetainedSourceReprojectionAdmissionError as error:
            raise RetainedSourceSingletonReprojectionError(
                f"singleton reprojection durable evidence changed: {error.reason.value}"
            ) from error
        if (
            import_event_id != admission.import_event_id
            or family != admission.source_family
            or server != admission.server
            or account != admission.account
        ):
            raise RetainedSourceSingletonReprojectionError(
                "singleton reprojection admission conflicts with durable evidence"
            )
        historical = connection.execute(
            "SELECT * FROM discord_projection_links WHERE source_event_id = ? "
            "AND generation_id != ? ORDER BY generation_id, id",
            (admission.source_event_id, admission.current_generation_id),
        ).fetchall()
        groups: dict[int, list[sqlite3.Row]] = {}
        for row in historical:
            groups.setdefault(int(row["generation_id"]), []).append(row)
        if not groups or max(groups) != admission.historical_generation_id:
            raise RetainedSourceSingletonReprojectionError(
                "singleton reprojection historical generation changed"
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
                raise RetainedSourceSingletonReprojectionError(
                    f"singleton reprojection historical evidence changed: {error.reason.value}"
                ) from error
            if payloads != admission.payloads:
                raise RetainedSourceSingletonReprojectionError(
                    "singleton reprojection payload changed before replay"
                )

    def _validate_admission_shape(
        self, admission: RetainedSourceReprojectionAdmission
    ) -> tuple[_SingletonAuthority, str, DurableReprojectionPayload]:
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
                raise RetainedSourceSingletonReprojectionError(
                    f"singleton reprojection admission has invalid {field_name}"
                )
        authority = _AUTHORITIES.get(admission.source_family)
        if authority is None or admission.account is None:
            raise RetainedSourceSingletonReprojectionError(
                "source family is outside the authorized account-scoped singleton cohort"
            )
        if len(admission.expected_identities) != 1 or len(admission.payloads) != 1:
            raise RetainedSourceSingletonReprojectionError(
                "singleton reprojection requires exactly one identity and payload"
            )
        identity, payload = admission.expected_identities[0], admission.payloads[0]
        fields = dict(payload.fields)
        if admission.source_family == "claim":
            character = fields.get("normalized_character_name")
            if not isinstance(character, str) or not character:
                raise RetainedSourceSingletonReprojectionError(
                    "Claim reprojection requires exact retained character identity"
                )
            expected_slot = claim_projection_slot(admission.server, admission.account, character)
        else:
            expected_slot = server_account_projection_slot(admission.server, admission.account)
        if (
            identity.projection_kind != authority.projection_kind
            or identity.projection_slot != expected_slot
            or payload.projection_kind != authority.projection_kind
            or payload.projection_slot != expected_slot
            or payload.target_table != authority.target_table
            or isinstance(payload.historical_target_id, bool)
            or not isinstance(payload.historical_target_id, int)
            or payload.historical_target_id <= 0
            or not payload.fields
        ):
            raise RetainedSourceSingletonReprojectionError(
                "singleton reprojection admission identity, slot, target, or payload is malformed"
            )
        return authority, expected_slot, payload

    @staticmethod
    def _is_exact_claimed_link(row: sqlite3.Row, authority: _SingletonAuthority, slot: str) -> bool:
        return (
            str(row["projection_kind"]) == authority.projection_kind
            and str(row["projection_slot"]) == slot
            and str(row["state"]) == "claimed"
            and row["projection_table"] is None
            and row["projection_row_id"] is None
            and row["completed_at"] is None
        )

    @staticmethod
    def _is_exact_completed_link(
        row: sqlite3.Row, authority: _SingletonAuthority, slot: str, target_id: int
    ) -> bool:
        return (
            str(row["projection_kind"]) == authority.projection_kind
            and str(row["projection_slot"]) == slot
            and str(row["state"]) == "completed"
            and str(row["projection_table"]) == authority.target_table
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
            raise RetainedSourceSingletonReprojectionError(
                "singleton reprojection requires one valid current generation"
            ) from error

    @staticmethod
    def _result(
        admission: RetainedSourceReprojectionAdmission,
        payload: DurableReprojectionPayload,
        *,
        linked_count: int,
        replay_skipped: bool,
    ) -> RetainedSourceSingletonReprojectionResult:
        return RetainedSourceSingletonReprojectionResult(
            source_event_id=admission.source_event_id,
            source_family=admission.source_family,
            current_generation_id=admission.current_generation_id,
            import_event_id=admission.import_event_id,
            linked_count=linked_count,
            replay_skipped=replay_skipped,
            projection_target=(payload.target_table, payload.historical_target_id),
        )
