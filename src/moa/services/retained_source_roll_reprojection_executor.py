"""Link-only Roll reprojection from admitted retained evidence."""

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
from moa.services.projection_authority import PROJECTION_AUTHORITY_BY_KIND
from moa.services.projection_expectations import roll_projection_slot
from moa.services.retained_source_reprojection_admission_service import (
    ReprojectionAdmissionRejection,
    RetainedSourceReprojectionAdmission,
    RetainedSourceReprojectionAdmissionError,
    RetainedSourceReprojectionAdmissionService,
)


class RetainedSourceRollReprojectionError(RuntimeError):
    """Raised when an admitted Roll reprojection cannot execute exactly."""


@dataclass(frozen=True, slots=True)
class RetainedSourceRollReprojectionResult:
    """The link-only outcome of one retained-source Roll execution."""

    source_event_id: int
    current_generation_id: int
    import_event_id: int
    linked_count: int
    replay_skipped: bool
    projection_targets: tuple[tuple[str, str, int], ...]


_ROLL_KINDS = (
    "catalog.roll",
    "catalog.roll_key",
    "catalog.roll_rank",
    "catalog.roll_server_character",
)
_REQUIRED_FIELDS = {
    "catalog.roll": frozenset(
        {
            "id",
            "account_context_id",
            "character_id",
            "claim_rank",
            "kakera_value",
            "observed_at",
            "import_event_id",
            "character",
            "series",
        }
    ),
    "catalog.roll_key": frozenset(
        {
            "id",
            "account_context_id",
            "character_id",
            "character_name",
            "normalized_character_name",
            "key_type",
            "key_count",
            "kakera_value",
            "harem_scan_id",
            "observed_at",
            "import_event_id",
            "character",
            "series",
        }
    ),
    "catalog.roll_rank": frozenset(
        {
            "id",
            "character_id",
            "claim_rank",
            "like_rank",
            "owner_name",
            "observed_at",
            "import_event_id",
            "character",
            "series",
        }
    ),
    "catalog.roll_server_character": frozenset(
        {
            "id",
            "server_context_id",
            "character_id",
            "kakera_value",
            "observed_at",
            "import_event_id",
            "character",
            "series",
        }
    ),
}


class RetainedSourceRollReprojectionExecutor:
    """Establish the exact missing Roll link set without recreating projections."""

    _SAVEPOINT = "retained_source_roll_reprojection"

    def __init__(
        self, admission_service: RetainedSourceReprojectionAdmissionService | None = None
    ) -> None:
        self._admission_service = admission_service or RetainedSourceReprojectionAdmissionService()

    def execute(
        self,
        connection: sqlite3.Connection,
        admission: RetainedSourceReprojectionAdmission,
        completed_at: datetime,
    ) -> RetainedSourceRollReprojectionResult:
        """Validate, claim, and complete all admitted links in the caller transaction."""
        if not connection.in_transaction:
            raise RetainedSourceRollReprojectionError(
                "Roll reprojection requires a caller-owned transaction"
            )
        completed_at = self._normalize_datetime(completed_at)
        pairs = self._validate_admission_shape(admission)
        links = ProjectionLinkRepository(connection)
        current_generation_id = self._resolve_current_generation(links)
        if current_generation_id != admission.current_generation_id:
            raise RetainedSourceRollReprojectionError(
                "Roll reprojection admission is stale for the current generation"
            )

        current = links.load_links(
            source_event_id=admission.source_event_id,
            generation_id=current_generation_id,
        )
        if current:
            return self._validate_replay(connection, admission, pairs, current)

        try:
            refreshed = self._admission_service.admit(connection, admission.source_event_id)
        except RetainedSourceReprojectionAdmissionError as error:
            raise RetainedSourceRollReprojectionError(
                f"Roll reprojection admission no longer holds: {error.reason.value}"
            ) from error
        if refreshed != admission:
            raise RetainedSourceRollReprojectionError(
                "Roll reprojection admission changed before execution"
            )
        if self._resolve_current_generation(links) != admission.current_generation_id:
            raise RetainedSourceRollReprojectionError(
                "Roll reprojection generation changed before execution"
            )
        if links.load_links(
            source_event_id=admission.source_event_id,
            generation_id=admission.current_generation_id,
        ):
            raise RetainedSourceRollReprojectionError(
                "Roll reprojection current links changed before execution"
            )

        connection.execute(f"SAVEPOINT {self._SAVEPOINT}")
        try:
            for identity, _payload in pairs:
                links.claim_link(
                    source_event_id=admission.source_event_id,
                    generation_id=admission.current_generation_id,
                    projection_kind=identity.projection_kind,
                    projection_slot=identity.projection_slot,
                    claimed_at=completed_at,
                )
                self._validate_link_set(
                    links.load_links(
                        source_event_id=admission.source_event_id,
                        generation_id=admission.current_generation_id,
                    ),
                    pairs[: pairs.index((identity, _payload)) + 1],
                    claimed=True,
                )
            for identity, payload in pairs:
                links.complete_claimed_link(
                    source_event_id=admission.source_event_id,
                    generation_id=admission.current_generation_id,
                    projection_kind=identity.projection_kind,
                    projection_slot=identity.projection_slot,
                    projection_table=payload.target_table,
                    projection_row_id=payload.historical_target_id,
                    completed_at=completed_at,
                )
                completed_pairs = pairs[: pairs.index((identity, payload)) + 1]
                self._validate_completion_progress(
                    links.load_links(
                        source_event_id=admission.source_event_id,
                        generation_id=admission.current_generation_id,
                    ),
                    pairs,
                    completed_pairs,
                )
            connection.execute(f"RELEASE SAVEPOINT {self._SAVEPOINT}")
        except Exception:
            connection.execute(f"ROLLBACK TO SAVEPOINT {self._SAVEPOINT}")
            connection.execute(f"RELEASE SAVEPOINT {self._SAVEPOINT}")
            raise
        return self._result(admission, pairs, len(pairs), False)

    def _validate_replay(self, connection, admission, pairs, current):
        try:
            self._admission_service.admit(connection, admission.source_event_id)
        except RetainedSourceReprojectionAdmissionError as error:
            if error.reason is not ReprojectionAdmissionRejection.CURRENT_LINKS_ALREADY_COMPLETE:
                raise RetainedSourceRollReprojectionError(
                    f"Roll reprojection replay evidence is invalid: {error.reason.value}"
                ) from error
        else:
            raise RetainedSourceRollReprojectionError(
                "Roll reprojection replay unexpectedly remained writable"
            )
        self._validate_link_set(current, pairs, claimed=False)
        self._validate_admission_against_durable_replay(connection, admission)
        return self._result(admission, pairs, 0, True)

    def _validate_admission_shape(self, admission):
        if not isinstance(admission, RetainedSourceReprojectionAdmission):
            raise TypeError("admission must be a RetainedSourceReprojectionAdmission")
        for name in (
            "source_event_id",
            "successful_attempt_id",
            "import_event_id",
            "current_generation_id",
            "historical_generation_id",
        ):
            value = getattr(admission, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise RetainedSourceRollReprojectionError(
                    f"Roll reprojection admission has invalid {name}"
                )
        if (
            admission.source_family != "roll"
            or not isinstance(admission.server, str)
            or not admission.server
            or not isinstance(admission.account, str)
            or not admission.account
            or not 1 <= len(admission.expected_identities) <= 4
            or len(admission.payloads) != len(admission.expected_identities)
        ):
            raise RetainedSourceRollReprojectionError(
                "Roll reprojection requires one-to-four account-scoped identities and payloads"
            )
        identities = {
            (item.projection_kind, item.projection_slot): item
            for item in admission.expected_identities
        }
        payloads = {
            (item.projection_kind, item.projection_slot): item for item in admission.payloads
        }
        if (
            len(identities) != len(admission.expected_identities)
            or len(payloads) != len(admission.payloads)
            or set(identities) != set(payloads)
        ):
            raise RetainedSourceRollReprojectionError(
                "Roll reprojection identity-to-payload mapping is not bijective"
            )
        pairs = tuple((identity, payloads[key]) for key, identity in identities.items())
        if {identity.projection_kind for identity, _ in pairs} - set(_ROLL_KINDS):
            raise RetainedSourceRollReprojectionError("Roll reprojection has an unauthorized kind")
        if "catalog.roll" not in {identity.projection_kind for identity, _ in pairs}:
            raise RetainedSourceRollReprojectionError("Roll reprojection has no base Roll identity")
        scopes = []
        fields_by_kind = {}
        for identity, payload in pairs:
            authority = PROJECTION_AUTHORITY_BY_KIND[identity.projection_kind]
            fields = dict(payload.fields)
            if (
                payload.target_table != authority.target_table
                or payload.projection_kind != identity.projection_kind
                or payload.projection_slot != identity.projection_slot
                or isinstance(payload.historical_target_id, bool)
                or not isinstance(payload.historical_target_id, int)
                or payload.historical_target_id <= 0
                or frozenset(fields) != _REQUIRED_FIELDS[identity.projection_kind]
                or fields["id"] != payload.historical_target_id
                or fields["import_event_id"] != admission.import_event_id
                or not isinstance(fields["observed_at"], str)
                or not fields["observed_at"]
            ):
                raise RetainedSourceRollReprojectionError(
                    "Roll admission identity, slot, target, or payload is malformed"
                )
            try:
                slot = json.loads(identity.projection_slot)
            except (TypeError, json.JSONDecodeError) as error:
                raise RetainedSourceRollReprojectionError(
                    "Roll projection slot is malformed"
                ) from error
            required_slot = {"server", "account", "character", "series"}
            if set(slot) not in (required_slot, required_slot | {"key_type"}) or any(
                not isinstance(slot[name], str) or not slot[name] for name in required_slot
            ):
                raise RetainedSourceRollReprojectionError("Roll projection slot is malformed")
            if (
                slot["server"] != admission.server
                or slot["account"] != admission.account
                or fields["character"] != slot["character"]
                or fields["series"] != slot["series"]
            ):
                raise RetainedSourceRollReprojectionError("Roll projection scope is divergent")
            expected_slot = roll_projection_slot(
                admission.server,
                admission.account,
                slot["character"],
                slot["series"],
                key_type=slot.get("key_type"),
            )
            if identity.projection_slot != expected_slot:
                raise RetainedSourceRollReprojectionError("Roll projection slot is not canonical")
            if identity.projection_kind == "catalog.roll_key":
                if (
                    set(slot) != required_slot | {"key_type"}
                    or CatalogRepository._normalize(str(fields["key_type"])) != slot["key_type"]
                ):
                    raise RetainedSourceRollReprojectionError("Roll key type and slot disagree")
            elif set(slot) != required_slot:
                raise RetainedSourceRollReprojectionError(
                    "only the Roll key slot may contain key_type"
                )
            scopes.append(
                tuple(slot[name] for name in ("server", "account", "character", "series"))
            )
            fields_by_kind[identity.projection_kind] = fields
        if len(set(scopes)) != 1:
            raise RetainedSourceRollReprojectionError(
                "Roll projection targets have divergent scope"
            )
        self._validate_cross_target_coherence(fields_by_kind)
        return pairs

    @staticmethod
    def _validate_cross_target_coherence(fields):
        base = fields["catalog.roll"]
        if (
            "catalog.roll_rank" in fields
            and fields["catalog.roll_rank"]["claim_rank"] != base["claim_rank"]
        ):
            raise RetainedSourceRollReprojectionError("Roll claim-rank targets disagree")
        for kind in ("catalog.roll_key", "catalog.roll_server_character"):
            if kind in fields and fields[kind]["kakera_value"] != base["kakera_value"]:
                raise RetainedSourceRollReprojectionError("Roll Kakera targets disagree")
        if "catalog.roll_key" in fields:
            key = fields["catalog.roll_key"]
            if (
                isinstance(key["key_count"], bool)
                or not isinstance(key["key_count"], int)
                or key["key_count"] < 0
                or CatalogRepository._normalize(str(key["normalized_character_name"]))
                != key["character"]
            ):
                raise RetainedSourceRollReprojectionError("Roll key payload is incoherent")

    def _validate_admission_against_durable_replay(self, connection, admission):
        source = connection.execute(
            "SELECT legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (admission.source_event_id,),
        ).fetchone()
        attempts = connection.execute(
            "SELECT id FROM discord_processing_attempts WHERE source_event_id = ? AND status = 'succeeded'",
            (admission.source_event_id,),
        ).fetchall()
        imported = connection.execute(
            "SELECT kind FROM import_events WHERE id = ?", (admission.import_event_id,)
        ).fetchone()
        if (
            source is None
            or source[0] != admission.import_event_id
            or len(attempts) != 1
            or int(attempts[0][0]) != admission.successful_attempt_id
            or imported is None
            or imported[0] != "roll"
        ):
            raise RetainedSourceRollReprojectionError("Roll admission has forged provenance")
        historical = connection.execute(
            "SELECT * FROM discord_projection_links WHERE source_event_id = ? AND generation_id != ? ORDER BY generation_id, id",
            (admission.source_event_id, admission.current_generation_id),
        ).fetchall()
        groups = {}
        for row in historical:
            groups.setdefault(int(row["generation_id"]), []).append(row)
        if not groups or max(groups) != admission.historical_generation_id:
            raise RetainedSourceRollReprojectionError("Roll historical generation changed")
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
                raise RetainedSourceRollReprojectionError(
                    f"Roll historical evidence changed: {error.reason.value}"
                ) from error
            if payloads != admission.payloads:
                raise RetainedSourceRollReprojectionError(
                    "Roll admission payload changed before replay"
                )

    @staticmethod
    def _validate_link_set(rows, pairs, *, claimed):
        if len(rows) != len(pairs):
            raise RetainedSourceRollReprojectionError("Roll link set is partial or additional")
        expected = {
            (identity.projection_kind, identity.projection_slot): payload
            for identity, payload in pairs
        }
        actual = {(str(row["projection_kind"]), str(row["projection_slot"])): row for row in rows}
        if len(actual) != len(rows) or set(actual) != set(expected):
            raise RetainedSourceRollReprojectionError(
                "Roll link identities are duplicate or divergent"
            )
        for key, row in actual.items():
            payload = expected[key]
            if claimed:
                valid = (
                    row["state"] == "claimed"
                    and row["projection_table"] is None
                    and row["projection_row_id"] is None
                    and row["completed_at"] is None
                )
            else:
                valid = (
                    row["state"] == "completed"
                    and row["projection_table"] == payload.target_table
                    and row["projection_row_id"] == payload.historical_target_id
                    and row["completed_at"] is not None
                )
            if not valid:
                raise RetainedSourceRollReprojectionError(
                    "Roll link state or target is contradictory"
                )

    def _validate_completion_progress(self, rows, pairs, completed_pairs):
        if len(rows) != len(pairs):
            raise RetainedSourceRollReprojectionError("Roll completion changed the link set")
        completed_keys = {(i.projection_kind, i.projection_slot) for i, _ in completed_pairs}
        for row in rows:
            key = (str(row["projection_kind"]), str(row["projection_slot"]))
            pair = next(
                (
                    pair
                    for pair in pairs
                    if (pair[0].projection_kind, pair[0].projection_slot) == key
                ),
                None,
            )
            if pair is None:
                raise RetainedSourceRollReprojectionError(
                    "Roll completion introduced a divergent link"
                )
            payload = pair[1]
            valid = (
                row["state"] == "completed"
                and row["projection_table"] == payload.target_table
                and row["projection_row_id"] == payload.historical_target_id
                and row["completed_at"] is not None
                if key in completed_keys
                else row["state"] == "claimed"
                and row["projection_table"] is None
                and row["projection_row_id"] is None
                and row["completed_at"] is None
            )
            if not valid:
                raise RetainedSourceRollReprojectionError(
                    "Roll completion progress is contradictory"
                )

    @staticmethod
    def _normalize_datetime(value):
        if not isinstance(value, datetime):
            raise TypeError("completed_at must be a datetime")
        if value.utcoffset() is None:
            raise ValueError("completed_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _resolve_current_generation(repository):
        try:
            return repository.resolve_current_generation_id()
        except (ProjectionLinkIntegrityError, ValueError) as error:
            raise RetainedSourceRollReprojectionError(
                "Roll reprojection current generation is invalid"
            ) from error

    @staticmethod
    def _result(admission, pairs, linked_count, replay_skipped):
        return RetainedSourceRollReprojectionResult(
            admission.source_event_id,
            admission.current_generation_id,
            admission.import_event_id,
            linked_count,
            replay_skipped,
            tuple((p.projection_kind, p.target_table, p.historical_target_id) for _, p in pairs),
        )
