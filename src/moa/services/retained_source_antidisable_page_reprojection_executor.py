"""Link-only Antidisable Page reprojection from admitted retained evidence."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.projection_link_repository import (
    ProjectionLinkIntegrityError,
    ProjectionLinkRepository,
)
from moa.services.projection_expectations import (
    ExpectedProjectionIdentity,
    antidisable_page_projection_slot,
)
from moa.services.retained_source_reprojection_admission_service import (
    DurableReprojectionPayload,
    ReprojectionAdmissionRejection,
    RetainedSourceReprojectionAdmission,
    RetainedSourceReprojectionAdmissionError,
    RetainedSourceReprojectionAdmissionService,
)


class RetainedSourceAntidisablePageReprojectionError(RuntimeError):
    """Raised when an admitted Antidisable Page link cannot execute exactly."""


@dataclass(frozen=True, slots=True)
class RetainedSourceAntidisablePageReprojectionResult:
    """The link-only outcome of one retained Antidisable Page execution."""

    source_event_id: int
    current_generation_id: int
    import_event_id: int
    linked_count: int
    replay_skipped: bool
    projection_target: tuple[str, int]


class RetainedSourceAntidisablePageReprojectionExecutor:
    """Establish one missing current Antidisable Page link without re-importing."""

    _SOURCE_FAMILY = "antidisable"
    _PROJECTION_KIND = "catalog.antidisable_page"
    _PROJECTION_TABLE = "import_events"
    _SAVEPOINT = "retained_source_antidisable_page_reprojection"
    _PAYLOAD_FIELDS = frozenset(
        {
            "id",
            "kind",
            "source",
            "observed_at",
            "raw_message",
            "raw_message_expired_at",
            "scan_id",
            "page_number",
            "page_count",
            "slots_used",
            "slots_capacity",
            "series",
        }
    )

    def __init__(
        self, admission_service: RetainedSourceReprojectionAdmissionService | None = None
    ) -> None:
        self._admission_service = admission_service or RetainedSourceReprojectionAdmissionService()

    def execute(
        self,
        connection: sqlite3.Connection,
        admission: RetainedSourceReprojectionAdmission,
        completed_at: datetime,
    ) -> RetainedSourceAntidisablePageReprojectionResult:
        """Validate and complete the admitted link in a caller-owned transaction."""
        if not connection.in_transaction:
            raise RetainedSourceAntidisablePageReprojectionError(
                "Antidisable Page reprojection requires a caller-owned transaction"
            )
        completed_at = self._normalize_datetime(completed_at)
        identity, payload = self._validate_admission_shape(admission)

        links = ProjectionLinkRepository(connection)
        current_generation_id = self._resolve_current_generation(links)
        if current_generation_id != admission.current_generation_id:
            raise RetainedSourceAntidisablePageReprojectionError(
                "Antidisable Page admission is stale for the current generation"
            )

        current_links = links.load_links(
            source_event_id=admission.source_event_id,
            generation_id=current_generation_id,
        )
        if current_links:
            return self._validate_replay(
                connection, admission, identity.projection_slot, current_links
            )

        try:
            refreshed = self._admission_service.admit(connection, admission.source_event_id)
        except RetainedSourceReprojectionAdmissionError as error:
            raise RetainedSourceAntidisablePageReprojectionError(
                f"Antidisable Page admission no longer holds: {error.reason.value}"
            ) from error
        if refreshed != admission:
            raise RetainedSourceAntidisablePageReprojectionError(
                "Antidisable Page admission changed before execution"
            )
        if self._resolve_current_generation(links) != admission.current_generation_id:
            raise RetainedSourceAntidisablePageReprojectionError(
                "Antidisable Page generation changed before execution"
            )
        if links.load_links(
            source_event_id=admission.source_event_id,
            generation_id=admission.current_generation_id,
        ):
            raise RetainedSourceAntidisablePageReprojectionError(
                "Antidisable Page current links changed before execution"
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
                raise RetainedSourceAntidisablePageReprojectionError(
                    "Antidisable Page reprojection did not claim exactly the admitted link"
                )
            links.complete_claimed_link(
                source_event_id=admission.source_event_id,
                generation_id=admission.current_generation_id,
                projection_kind=self._PROJECTION_KIND,
                projection_slot=identity.projection_slot,
                projection_table=self._PROJECTION_TABLE,
                projection_row_id=admission.import_event_id,
                completed_at=completed_at,
            )
            completed = links.load_links(
                source_event_id=admission.source_event_id,
                generation_id=admission.current_generation_id,
            )
            if len(completed) != 1 or not self._is_exact_completed_link(
                completed[0], identity.projection_slot, admission.import_event_id
            ):
                raise RetainedSourceAntidisablePageReprojectionError(
                    "Antidisable Page reprojection did not complete exactly the admitted link"
                )
            connection.execute(f"RELEASE SAVEPOINT {self._SAVEPOINT}")
        except Exception:
            connection.execute(f"ROLLBACK TO SAVEPOINT {self._SAVEPOINT}")
            connection.execute(f"RELEASE SAVEPOINT {self._SAVEPOINT}")
            raise

        return self._result(admission, linked_count=1, replay_skipped=False)

    def _validate_replay(
        self,
        connection: sqlite3.Connection,
        admission: RetainedSourceReprojectionAdmission,
        projection_slot: str,
        current_links: tuple[sqlite3.Row, ...],
    ) -> RetainedSourceAntidisablePageReprojectionResult:
        try:
            self._admission_service.admit(connection, admission.source_event_id)
        except RetainedSourceReprojectionAdmissionError as error:
            if error.reason is not ReprojectionAdmissionRejection.CURRENT_LINKS_ALREADY_COMPLETE:
                raise RetainedSourceAntidisablePageReprojectionError(
                    f"Antidisable Page replay evidence is invalid: {error.reason.value}"
                ) from error
        else:
            raise RetainedSourceAntidisablePageReprojectionError(
                "Antidisable Page replay unexpectedly remained writable"
            )
        if len(current_links) != 1 or not self._is_exact_completed_link(
            current_links[0], projection_slot, admission.import_event_id
        ):
            raise RetainedSourceAntidisablePageReprojectionError(
                "Antidisable Page replay does not exactly match the admitted current link"
            )
        self._validate_admission_against_durable_replay(connection, admission)
        return self._result(admission, linked_count=0, replay_skipped=True)

    def _validate_admission_against_durable_replay(
        self,
        connection: sqlite3.Connection,
        admission: RetainedSourceReprojectionAdmission,
    ) -> None:
        source = connection.execute(
            "SELECT legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (admission.source_event_id,),
        ).fetchone()
        attempts = connection.execute(
            "SELECT id FROM discord_processing_attempts "
            "WHERE source_event_id = ? AND status = 'succeeded'",
            (admission.source_event_id,),
        ).fetchall()
        imported = connection.execute(
            "SELECT kind FROM import_events WHERE id = ?", (admission.import_event_id,)
        ).fetchone()
        if (
            source is None
            or source["legacy_import_event_id"] != admission.import_event_id
            or len(attempts) != 1
            or int(attempts[0]["id"]) != admission.successful_attempt_id
            or imported is None
            or str(imported["kind"]) != self._SOURCE_FAMILY
        ):
            raise RetainedSourceAntidisablePageReprojectionError(
                "Antidisable Page admission has forged provenance"
            )
        server = connection.execute(
            "SELECT server_name FROM discord_source_event_server_attributions "
            "WHERE source_event_id = ?",
            (admission.source_event_id,),
        ).fetchone()
        account = connection.execute(
            "SELECT server_name, account_name "
            "FROM discord_source_event_account_attributions WHERE source_event_id = ?",
            (admission.source_event_id,),
        ).fetchone()
        if (
            server is None
            or account is None
            or CatalogRepository._normalize(str(server["server_name"])) != admission.server
            or CatalogRepository._normalize(str(account["server_name"])) != admission.server
            or CatalogRepository._normalize(str(account["account_name"])) != admission.account
        ):
            raise RetainedSourceAntidisablePageReprojectionError(
                "Antidisable Page admission has forged attribution"
            )

        historical = connection.execute(
            "SELECT * FROM discord_projection_links "
            "WHERE source_event_id = ? AND generation_id != ? ORDER BY generation_id, id",
            (admission.source_event_id, admission.current_generation_id),
        ).fetchall()
        groups: dict[int, list[sqlite3.Row]] = {}
        for row in historical:
            groups.setdefault(int(row["generation_id"]), []).append(row)
        if not groups or max(groups) != admission.historical_generation_id:
            raise RetainedSourceAntidisablePageReprojectionError(
                "Antidisable Page historical generation changed"
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
                raise RetainedSourceAntidisablePageReprojectionError(
                    f"Antidisable Page historical evidence changed: {error.reason.value}"
                ) from error
            if payloads != admission.payloads:
                raise RetainedSourceAntidisablePageReprojectionError(
                    "Antidisable Page admission payload changed before replay"
                )

    def _validate_admission_shape(
        self, admission: RetainedSourceReprojectionAdmission
    ) -> tuple[ExpectedProjectionIdentity, DurableReprojectionPayload]:
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
                raise RetainedSourceAntidisablePageReprojectionError(
                    f"Antidisable Page admission has invalid {field_name}"
                )
        if (
            admission.source_family != self._SOURCE_FAMILY
            or not isinstance(admission.server, str)
            or not admission.server
            or not isinstance(admission.account, str)
            or not admission.account
            or len(admission.expected_identities) != 1
            or len(admission.payloads) != 1
        ):
            raise RetainedSourceAntidisablePageReprojectionError(
                "Antidisable Page reprojection requires one account-scoped identity and payload"
            )
        identity = admission.expected_identities[0]
        payload = admission.payloads[0]
        fields = dict(payload.fields)
        if (
            identity.projection_kind != self._PROJECTION_KIND
            or payload.projection_kind != identity.projection_kind
            or payload.projection_slot != identity.projection_slot
            or payload.target_table != self._PROJECTION_TABLE
            or payload.historical_target_id != admission.import_event_id
            or frozenset(fields) != self._PAYLOAD_FIELDS
            or fields["id"] != admission.import_event_id
            or fields["kind"] != self._SOURCE_FAMILY
        ):
            raise RetainedSourceAntidisablePageReprojectionError(
                "Antidisable Page identity, exceptional target, or payload is malformed"
            )
        scan_id = self._positive_int(fields["scan_id"], "scan_id")
        page_number = self._positive_int(fields["page_number"], "page_number")
        page_count = self._positive_int(fields["page_count"], "page_count")
        slots_used = self._nonnegative_int(fields["slots_used"], "slots_used")
        slots_capacity = self._nonnegative_int(fields["slots_capacity"], "slots_capacity")
        if page_number > page_count or slots_used > slots_capacity:
            raise RetainedSourceAntidisablePageReprojectionError(
                "Antidisable Page page or slot evidence is incomplete"
            )
        if identity.projection_slot != antidisable_page_projection_slot(
            admission.server, admission.account, scan_id, page_number
        ):
            raise RetainedSourceAntidisablePageReprojectionError(
                "Antidisable Page projection slot is not canonical"
            )
        try:
            slot = json.loads(identity.projection_slot)
        except (TypeError, json.JSONDecodeError) as error:
            raise RetainedSourceAntidisablePageReprojectionError(
                "Antidisable Page projection slot is malformed"
            ) from error
        if set(slot) != {"server", "account", "scan_id", "page_number"}:
            raise RetainedSourceAntidisablePageReprojectionError(
                "Antidisable Page projection slot is malformed"
            )
        series = fields["series"]
        if not isinstance(series, tuple):
            raise RetainedSourceAntidisablePageReprojectionError(
                "Antidisable Page ordered series evidence is malformed"
            )
        character_count: int | None = None
        for index, item in enumerate(series):
            if not isinstance(item, tuple):
                raise RetainedSourceAntidisablePageReprojectionError(
                    "Antidisable Page ordered series evidence is malformed"
                )
            values = dict(item)
            if set(values) != {
                "series_name",
                "normalized_series_name",
                "antidisabled_character_count",
            }:
                raise RetainedSourceAntidisablePageReprojectionError(
                    "Antidisable Page ordered series evidence is malformed"
                )
            count = values["antidisabled_character_count"]
            if count is not None:
                count = self._nonnegative_int(count, "antidisabled_character_count")
            if index == 0:
                character_count = count
            elif count != character_count:
                raise RetainedSourceAntidisablePageReprojectionError(
                    "Antidisable Page ordered series counts diverge"
                )
            name = values["series_name"]
            normalized = values["normalized_series_name"]
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(normalized, str)
                or normalized != CatalogRepository._normalize(name)
            ):
                raise RetainedSourceAntidisablePageReprojectionError(
                    "Antidisable Page ordered series names diverge"
                )
        if page_number == 1 and (not series or character_count is None):
            raise RetainedSourceAntidisablePageReprojectionError(
                "Antidisable Page first-page series evidence is incomplete"
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
        cls, row: sqlite3.Row, projection_slot: str, import_event_id: int
    ) -> bool:
        return (
            str(row["projection_kind"]) == cls._PROJECTION_KIND
            and str(row["projection_slot"]) == projection_slot
            and str(row["state"]) == "completed"
            and str(row["projection_table"]) == cls._PROJECTION_TABLE
            and row["projection_row_id"] == import_event_id
            and row["completed_at"] is not None
        )

    @staticmethod
    def _positive_int(value: object, field_name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise RetainedSourceAntidisablePageReprojectionError(
                f"Antidisable Page admission has invalid {field_name}"
            )
        return value

    @staticmethod
    def _nonnegative_int(value: object, field_name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RetainedSourceAntidisablePageReprojectionError(
                f"Antidisable Page admission has invalid {field_name}"
            )
        return value

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
            raise RetainedSourceAntidisablePageReprojectionError(
                "Antidisable Page reprojection requires one valid current generation"
            ) from error

    @staticmethod
    def _result(
        admission: RetainedSourceReprojectionAdmission,
        *,
        linked_count: int,
        replay_skipped: bool,
    ) -> RetainedSourceAntidisablePageReprojectionResult:
        return RetainedSourceAntidisablePageReprojectionResult(
            source_event_id=admission.source_event_id,
            current_generation_id=admission.current_generation_id,
            import_event_id=admission.import_event_id,
            linked_count=linked_count,
            replay_skipped=replay_skipped,
            projection_target=("import_events", admission.import_event_id),
        )
