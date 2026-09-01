"""Link-only reprojection for retained Kakeraloot Settings sources."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from moa.services.projection_expectations import server_projection_slot
from moa.services.retained_source_reprojection_admission_service import (
    DurableReprojectionPayload,
    RetainedSourceReprojectionAdmission,
    RetainedSourceReprojectionAdmissionService,
)
from moa.services.retained_source_singleton_reprojection_executor import (
    RetainedSourceSingletonReprojectionError,
    RetainedSourceSingletonReprojectionExecutor,
)


class RetainedSourceKakeralootSettingsReprojectionError(RuntimeError):
    """Raised when an admitted Kakeraloot Settings reprojection cannot execute exactly."""


@dataclass(frozen=True, slots=True)
class RetainedSourceKakeralootSettingsReprojectionResult:
    """The link-only outcome for one retained Kakeraloot Settings source."""

    source_event_id: int
    source_family: str
    current_generation_id: int
    import_event_id: int
    linked_count: int
    replay_skipped: bool
    projection_target: tuple[str, int]


@dataclass(frozen=True, slots=True)
class _KakeralootSettingsAuthority:
    projection_kind: str
    target_table: str


_AUTHORITY = _KakeralootSettingsAuthority(
    projection_kind="catalog.kakeraloot_settings",
    target_table="kakeraloot_settings_observations",
)
_PAYLOAD_FIELDS = frozenset(
    {
        "id",
        "server_context_id",
        "loot_cost",
        "quantity_quality_base_cost",
        "quantity_quality_level_increment",
        "observed_at",
        "import_event_id",
    }
)
_SETTING_FIELDS = (
    "loot_cost",
    "quantity_quality_base_cost",
    "quantity_quality_level_increment",
)


class RetainedSourceKakeralootSettingsReprojectionExecutor(
    RetainedSourceSingletonReprojectionExecutor
):
    """Establish one missing Kakeraloot Settings link from admitted retained evidence."""

    _SAVEPOINT = "retained_source_kakeraloot_settings_reprojection"

    def __init__(
        self, admission_service: RetainedSourceReprojectionAdmissionService | None = None
    ) -> None:
        super().__init__(admission_service)

    def execute(
        self,
        connection: sqlite3.Connection,
        admission: RetainedSourceReprojectionAdmission,
        completed_at: datetime,
    ) -> RetainedSourceKakeralootSettingsReprojectionResult:
        """Validate and complete one admitted server-only link in the caller transaction."""
        try:
            result = super().execute(connection, admission, completed_at)
        except RetainedSourceSingletonReprojectionError as error:
            raise RetainedSourceKakeralootSettingsReprojectionError(
                str(error).replace("singleton reprojection", "Kakeraloot Settings reprojection")
            ) from error
        return RetainedSourceKakeralootSettingsReprojectionResult(
            source_event_id=result.source_event_id,
            source_family=result.source_family,
            current_generation_id=result.current_generation_id,
            import_event_id=result.import_event_id,
            linked_count=result.linked_count,
            replay_skipped=result.replay_skipped,
            projection_target=result.projection_target,
        )

    def _validate_admission_shape(
        self, admission: RetainedSourceReprojectionAdmission
    ) -> tuple[_KakeralootSettingsAuthority, str, DurableReprojectionPayload]:
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
                raise RetainedSourceKakeralootSettingsReprojectionError(
                    f"Kakeraloot Settings reprojection admission has invalid {field_name}"
                )
        if (
            admission.source_family != "kakeraloot_settings"
            or admission.account is not None
            or not isinstance(admission.server, str)
            or not admission.server
        ):
            raise RetainedSourceKakeralootSettingsReprojectionError(
                "source is outside the authorized server-only Kakeraloot Settings family"
            )
        if len(admission.expected_identities) != 1 or len(admission.payloads) != 1:
            raise RetainedSourceKakeralootSettingsReprojectionError(
                "Kakeraloot Settings reprojection requires exactly one identity and payload"
            )
        identity, payload = admission.expected_identities[0], admission.payloads[0]
        slot = server_projection_slot(admission.server)
        fields = dict(payload.fields)
        exact_integer_fields = (
            "id",
            "server_context_id",
            "import_event_id",
            *_SETTING_FIELDS,
        )
        if (
            identity.projection_kind != _AUTHORITY.projection_kind
            or identity.projection_slot != slot
            or payload.projection_kind != _AUTHORITY.projection_kind
            or payload.projection_slot != slot
            or payload.target_table != _AUTHORITY.target_table
            or isinstance(payload.historical_target_id, bool)
            or not isinstance(payload.historical_target_id, int)
            or payload.historical_target_id <= 0
            or frozenset(fields) != _PAYLOAD_FIELDS
            or any(
                isinstance(fields[name], bool) or not isinstance(fields[name], int)
                for name in exact_integer_fields
            )
            or fields["id"] != payload.historical_target_id
            or fields["import_event_id"] != admission.import_event_id
            or not isinstance(fields["observed_at"], str)
            or not fields["observed_at"]
        ):
            raise RetainedSourceKakeralootSettingsReprojectionError(
                "Kakeraloot Settings admission identity, slot, target, or payload is malformed"
            )
        return _AUTHORITY, slot, payload
