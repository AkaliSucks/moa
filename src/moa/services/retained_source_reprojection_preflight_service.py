"""Privacy-safe inventory for a hypothetical retained-source reprojection generation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from enum import Enum

from moa.database.migrations import MigrationError, validate_current_catalog_schema
from moa.repositories.projection_link_repository import (
    ProjectionLinkIntegrityError,
    ProjectionLinkRepository,
)
from moa.services.retained_source_reprojection_admission_service import (
    ReprojectionAdmissionRejection,
    RetainedSourceReprojectionAdmissionError,
    RetainedSourceReprojectionAdmissionService,
)


class ReprojectionPreflightEligibility(Enum):
    """Bounded eligibility states; unknown evidence is never treated as eligible."""

    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNKNOWN = "unknown"


class ReprojectionPreflightFailure(Enum):
    """Whole-inventory failures that prevent a trustworthy report."""

    TRANSACTION_REQUIRED = "transaction_required"
    SCHEMA_INVALID = "schema_invalid"
    CURRENT_GENERATION_INVALID = "current_generation_invalid"
    INVENTORY_NONDETERMINISTIC = "inventory_nondeterministic"


class RetainedSourceReprojectionPreflightError(RuntimeError):
    """A bounded fail-closed preflight failure without sensitive details."""

    def __init__(self, reason: ReprojectionPreflightFailure) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class RetainedSourceReprojectionPreflightRecord:
    """Privacy-safe result for one durable source."""

    source_event_id: int
    source_family: str
    eligibility: ReprojectionPreflightEligibility
    rejection_code: str | None
    expected_link_count: int | None


@dataclass(frozen=True, slots=True)
class ReprojectionPreflightFamilyTotal:
    source_family: str
    total: int
    eligible: int
    ineligible: int
    unknown: int


@dataclass(frozen=True, slots=True)
class ReprojectionPreflightReasonTotal:
    rejection_code: str
    total: int


@dataclass(frozen=True, slots=True)
class RetainedSourceReprojectionPreflight:
    """Deterministic inventory for an unpersisted exact next generation."""

    current_generation_id: int
    hypothetical_generation_id: int
    records: tuple[RetainedSourceReprojectionPreflightRecord, ...]
    family_totals: tuple[ReprojectionPreflightFamilyTotal, ...]
    reason_totals: tuple[ReprojectionPreflightReasonTotal, ...]
    inventory_fingerprint: str


_UNKNOWN_REJECTIONS = frozenset(
    {
        ReprojectionAdmissionRejection.ATTRIBUTION_UNRESOLVED,
        ReprojectionAdmissionRejection.EXPECTATIONS_UNKNOWN,
        ReprojectionAdmissionRejection.ANTIDISABLE_UNSUPPORTED,
        ReprojectionAdmissionRejection.PAYLOAD_INCOMPLETE,
    }
)
_UNKNOWN_FAMILY = "unknown"
_SAFE_FAMILIES = frozenset(
    {
        "antidisable",
        "claim",
        "disablelist",
        "kakera_state",
        "kakeraloot_settings",
        "kakeraloot_state",
        "mudapins",
        "player_bonus",
        "profile",
        "roll",
        "server_settings",
        "sphere_result",
        "timer_state",
        "tower_state",
        "wishlist",
    }
)


class RetainedSourceReprojectionPreflightService:
    """Inventory admissions inside one caller-owned read transaction without writes."""

    def __init__(
        self, admission_service: RetainedSourceReprojectionAdmissionService | None = None
    ) -> None:
        self._admission_service = admission_service or RetainedSourceReprojectionAdmissionService()

    def preflight(self, connection: sqlite3.Connection) -> RetainedSourceReprojectionPreflight:
        if not connection.in_transaction:
            raise RetainedSourceReprojectionPreflightError(
                ReprojectionPreflightFailure.TRANSACTION_REQUIRED
            )
        try:
            validate_current_catalog_schema(connection)
        except (MigrationError, sqlite3.DatabaseError):
            raise RetainedSourceReprojectionPreflightError(
                ReprojectionPreflightFailure.SCHEMA_INVALID
            ) from None

        try:
            current_generation_id = ProjectionLinkRepository(
                connection
            ).resolve_current_generation_id()
            generation_rows = connection.execute(
                "SELECT id, is_current FROM projection_generations ORDER BY id"
            ).fetchall()
            generation_ids = [int(row["id"]) for row in generation_rows]
            if (
                not generation_ids
                or any(generation_id <= 0 for generation_id in generation_ids)
                or generation_ids != sorted(set(generation_ids))
                or any(int(row["is_current"]) not in (0, 1) for row in generation_rows)
                or current_generation_id not in generation_ids
            ):
                raise ValueError("invalid generation inventory")
            hypothetical_generation_id = generation_ids[-1] + 1
            if hypothetical_generation_id <= current_generation_id:
                raise ValueError("next generation does not follow current generation")
        except (ProjectionLinkIntegrityError, sqlite3.DatabaseError, TypeError, ValueError):
            raise RetainedSourceReprojectionPreflightError(
                ReprojectionPreflightFailure.CURRENT_GENERATION_INVALID
            ) from None

        source_rows = connection.execute(
            """
            SELECT source.id, imported.kind
            FROM discord_source_events AS source
            LEFT JOIN import_events AS imported ON imported.id = source.legacy_import_event_id
            ORDER BY source.id
            """
        ).fetchall()
        source_ids = [int(row["id"]) for row in source_rows]
        if any(source_id <= 0 for source_id in source_ids) or source_ids != sorted(set(source_ids)):
            raise RetainedSourceReprojectionPreflightError(
                ReprojectionPreflightFailure.INVENTORY_NONDETERMINISTIC
            )

        records = tuple(
            self._inventory_source(
                connection,
                source_id=int(row["id"]),
                source_family=self._safe_family(row["kind"]),
                hypothetical_generation_id=hypothetical_generation_id,
            )
            for row in source_rows
        )
        family_totals = self._family_totals(records)
        reason_totals = self._reason_totals(records)
        fingerprint = self._fingerprint(
            current_generation_id,
            hypothetical_generation_id,
            records,
            family_totals,
            reason_totals,
        )
        return RetainedSourceReprojectionPreflight(
            current_generation_id,
            hypothetical_generation_id,
            records,
            family_totals,
            reason_totals,
            fingerprint,
        )

    def _inventory_source(
        self,
        connection: sqlite3.Connection,
        *,
        source_id: int,
        source_family: str,
        hypothetical_generation_id: int,
    ) -> RetainedSourceReprojectionPreflightRecord:
        try:
            admission = self._admission_service.admit(
                connection,
                source_id,
                hypothetical_generation_id=hypothetical_generation_id,
            )
        except RetainedSourceReprojectionAdmissionError as error:
            eligibility = (
                ReprojectionPreflightEligibility.UNKNOWN
                if error.reason in _UNKNOWN_REJECTIONS
                else ReprojectionPreflightEligibility.INELIGIBLE
            )
            return RetainedSourceReprojectionPreflightRecord(
                source_id, source_family, eligibility, error.reason.value, None
            )
        return RetainedSourceReprojectionPreflightRecord(
            source_id,
            admission.source_family,
            ReprojectionPreflightEligibility.ELIGIBLE,
            None,
            len(admission.expected_identities),
        )

    @staticmethod
    def _safe_family(raw_family: object) -> str:
        if not isinstance(raw_family, str) or raw_family not in _SAFE_FAMILIES:
            return _UNKNOWN_FAMILY
        return raw_family

    @staticmethod
    def _family_totals(
        records: tuple[RetainedSourceReprojectionPreflightRecord, ...],
    ) -> tuple[ReprojectionPreflightFamilyTotal, ...]:
        families: dict[str, list[int]] = {}
        for record in records:
            counts = families.setdefault(record.source_family, [0, 0, 0, 0])
            counts[0] += 1
            counts[
                {
                    ReprojectionPreflightEligibility.ELIGIBLE: 1,
                    ReprojectionPreflightEligibility.INELIGIBLE: 2,
                    ReprojectionPreflightEligibility.UNKNOWN: 3,
                }[record.eligibility]
            ] += 1
        return tuple(
            ReprojectionPreflightFamilyTotal(family, *families[family])
            for family in sorted(families)
        )

    @staticmethod
    def _reason_totals(
        records: tuple[RetainedSourceReprojectionPreflightRecord, ...],
    ) -> tuple[ReprojectionPreflightReasonTotal, ...]:
        counts: dict[str, int] = {}
        for record in records:
            if record.rejection_code is not None:
                counts[record.rejection_code] = counts.get(record.rejection_code, 0) + 1
        return tuple(
            ReprojectionPreflightReasonTotal(reason, counts[reason]) for reason in sorted(counts)
        )

    @staticmethod
    def _fingerprint(
        current_generation_id: int,
        hypothetical_generation_id: int,
        records: tuple[RetainedSourceReprojectionPreflightRecord, ...],
        family_totals: tuple[ReprojectionPreflightFamilyTotal, ...],
        reason_totals: tuple[ReprojectionPreflightReasonTotal, ...],
    ) -> str:
        document = {
            "current_generation_id": current_generation_id,
            "hypothetical_generation_id": hypothetical_generation_id,
            "records": [
                [
                    record.source_event_id,
                    record.source_family,
                    record.eligibility.value,
                    record.rejection_code,
                    record.expected_link_count,
                ]
                for record in records
            ],
            "family_totals": [
                [total.source_family, total.total, total.eligible, total.ineligible, total.unknown]
                for total in family_totals
            ],
            "reason_totals": [[total.rejection_code, total.total] for total in reason_totals],
        }
        encoded = json.dumps(
            document, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()
