"""Read-only admission for reconstructing projections from retained durable sources."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TypeAlias

from moa.models.character import (
    DisableListSnapshot,
    KakeraStateSnapshot,
    KakeralootSettingsSnapshot,
    KakeralootStateSnapshot,
    MudapinSnapshot,
    PlayerBonusSnapshot,
    ServerSettingsSnapshot,
    SphereResultSnapshot,
    TimerStateSnapshot,
    TowerStateSnapshot,
    WishlistSnapshot,
)
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.disablelist_repository import DisableListRepository
from moa.repositories.kakeraloot_state_repository import KakeralootStateRepository
from moa.repositories.kakeraloot_state_repository import _KAKERALOOT_STATE_VALUE_FIELDS
from moa.repositories.profile_repository import ProfileRepository
from moa.repositories.projection_link_repository import (
    ProjectionLinkIntegrityError,
    ProjectionLinkRepository,
)
from moa.repositories.tower_state_repository import TowerStateRepository
from moa.services.projection_authority import get_projection_authority
from moa.services.projection_expectations import (
    DurableProjectionExpectationFactsError,
    Expectedness,
    ExpectedProjectionIdentity,
    load_durable_projection_expectation_facts,
    resolve_expected_projections,
)


class ReprojectionAdmissionRejection(Enum):
    """Fail-closed classifications produced by the admission gate."""

    INVALID_REQUEST = "invalid_request"
    TRANSACTION_REQUIRED = "transaction_required"
    CURRENT_GENERATION_INVALID = "current_generation_invalid"
    SOURCE_NOT_FOUND = "source_not_found"
    SOURCE_NOT_SUCCEEDED = "source_not_succeeded"
    SOURCE_EXPIRED = "source_expired"
    ATTEMPT_INCOHERENT = "attempt_incoherent"
    PROVENANCE_INCOHERENT = "provenance_incoherent"
    ATTRIBUTION_UNRESOLVED = "attribution_unresolved"
    EXPECTATIONS_UNKNOWN = "expectations_unknown"
    UNSUPPORTED_FAMILY = "unsupported_family"
    ANTIDISABLE_UNSUPPORTED = "antidisable_unsupported"
    CURRENT_LINKS_ALREADY_COMPLETE = "current_links_already_complete"
    CURRENT_LINKS_CONFLICT = "current_links_conflict"
    HISTORICAL_LINKS_INCOHERENT = "historical_links_incoherent"
    PAYLOAD_INCOMPLETE = "payload_incomplete"


class RetainedSourceReprojectionAdmissionError(RuntimeError):
    """A classified, expected rejection from retained-source admission."""

    def __init__(self, reason: ReprojectionAdmissionRejection, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


FrozenValue: TypeAlias = (
    None
    | bool
    | int
    | float
    | str
    | tuple["FrozenValue", ...]
    | tuple[tuple[str, "FrozenValue"], ...]
)


@dataclass(frozen=True, slots=True)
class DurableReprojectionPayload:
    """One immutable, fully checked historical target payload."""

    projection_kind: str
    projection_slot: str
    target_table: str
    historical_target_id: int
    fields: tuple[tuple[str, FrozenValue], ...]


@dataclass(frozen=True, slots=True)
class RetainedSourceReprojectionAdmission:
    """Read-only evidence sufficient for a later caller to perform projection."""

    source_event_id: int
    successful_attempt_id: int
    import_event_id: int
    source_family: str
    current_generation_id: int
    historical_generation_id: int
    server: str
    account: str | None
    expected_identities: tuple[ExpectedProjectionIdentity, ...]
    payloads: tuple[DurableReprojectionPayload, ...]


_SUPPORTED_FAMILIES = frozenset(
    {
        "claim",
        "server_settings",
        "kakeraloot_settings",
        "kakera_state",
        "mudapins",
        "player_bonus",
        "wishlist",
        "disablelist",
        "timer_state",
        "tower_state",
        "kakeraloot_state",
        "sphere_result",
        "profile",
        "roll",
    }
)
_SERVER_ONLY_FAMILIES = frozenset({"server_settings", "kakeraloot_settings"})
_JSON_COLUMNS: dict[str, dict[str, type]] = {
    "server_settings_observations": {"metrics_json": list},
    "kakera_state_observations": {"badges_json": list},
    "mudapin_observations": {"pin_markers_json": list},
    "player_bonus_observations": {"metrics_json": list},
    "wishlist_observations": {"entries_json": list},
    "disablelist_observations": {"entries_json": list},
    "timer_state_observations": {"snapshot_json": dict},
    "tower_state_observations": {"built_perk_ids_json": list},
    "sphere_result_observations": {"snapshot_json": dict},
    "profile_observations": {
        "pokedex_json": list,
        "kakera_reacts_json": dict,
        "spheres_json": dict,
        "displayed_badges_json": list,
    },
}


class RetainedSourceReprojectionAdmissionService:
    """Admit one succeeded source without taking transaction ownership or writing."""

    def admit(
        self, connection: sqlite3.Connection, source_event_id: int
    ) -> RetainedSourceReprojectionAdmission:
        if (
            isinstance(source_event_id, bool)
            or not isinstance(source_event_id, int)
            or source_event_id <= 0
        ):
            self._reject(
                ReprojectionAdmissionRejection.INVALID_REQUEST, "source_event_id must be positive"
            )
        if not connection.in_transaction:
            self._reject(
                ReprojectionAdmissionRejection.TRANSACTION_REQUIRED,
                "admission requires a caller-owned transaction",
            )

        try:
            current_generation_id = ProjectionLinkRepository(
                connection
            ).resolve_current_generation_id()
        except (ProjectionLinkIntegrityError, ValueError) as error:
            self._reject(ReprojectionAdmissionRejection.CURRENT_GENERATION_INVALID, str(error))

        source = connection.execute(
            "SELECT * FROM discord_source_events WHERE id = ?", (source_event_id,)
        ).fetchone()
        if source is None:
            self._reject(
                ReprojectionAdmissionRejection.SOURCE_NOT_FOUND, "source event was not found"
            )
        assert source is not None
        if str(source["status"]) != "succeeded":
            self._reject(
                ReprojectionAdmissionRejection.SOURCE_NOT_SUCCEEDED, "source event is not succeeded"
            )
        if source["raw_evidence_expired_at"] is not None:
            self._reject(
                ReprojectionAdmissionRejection.SOURCE_EXPIRED, "source evidence has expired"
            )

        attempt_id = self._validate_attempts(connection, source_event_id)
        import_event_id, family = self._validate_provenance(connection, source_event_id, source)
        if family == "antidisable":
            self._reject(
                ReprojectionAdmissionRejection.ANTIDISABLE_UNSUPPORTED,
                "Antidisable Page is not reconstructible",
            )
        if family not in _SUPPORTED_FAMILIES:
            self._reject(
                ReprojectionAdmissionRejection.UNSUPPORTED_FAMILY,
                f"unsupported source family {family!r}",
            )

        try:
            facts = load_durable_projection_expectation_facts(connection, source_event_id)
            expected = resolve_expected_projections(facts)
        except (DurableProjectionExpectationFactsError, KeyError, TypeError, ValueError) as error:
            self._reject(ReprojectionAdmissionRejection.PROVENANCE_INCOHERENT, str(error))
        if facts.source_family != family:
            self._reject(
                ReprojectionAdmissionRejection.PROVENANCE_INCOHERENT,
                "expectation family conflicts with provenance",
            )
        if any(item.expectedness is Expectedness.UNKNOWN for item in expected.assessments):
            if self._has_unresolved_required_attribution(connection, source_event_id, family):
                self._reject(
                    ReprojectionAdmissionRejection.ATTRIBUTION_UNRESOLVED,
                    "required attribution is not resolved",
                )
            self._reject(
                ReprojectionAdmissionRejection.EXPECTATIONS_UNKNOWN,
                "durable projection expectations are not explicitly known",
            )
        identities = expected.known_expected_identities
        if not identities:
            self._reject(
                ReprojectionAdmissionRejection.EXPECTATIONS_UNKNOWN,
                "no durable projections are explicitly expected",
            )
        server, account = self._validate_attribution(
            connection, source_event_id, family, facts.server, facts.account
        )

        current_links = ProjectionLinkRepository(connection).load_links(
            source_event_id=source_event_id, generation_id=current_generation_id
        )
        if current_links:
            try:
                self._validate_link_set(
                    connection, current_links, identities, import_event_id, server, account
                )
            except RetainedSourceReprojectionAdmissionError:
                self._reject(
                    ReprojectionAdmissionRejection.CURRENT_LINKS_CONFLICT,
                    "current-generation links conflict with admission",
                )
            self._reject(
                ReprojectionAdmissionRejection.CURRENT_LINKS_ALREADY_COMPLETE,
                "current generation is already complete",
            )

        rows = connection.execute(
            "SELECT * FROM discord_projection_links WHERE source_event_id = ? AND generation_id != ? ORDER BY generation_id, id",
            (source_event_id, current_generation_id),
        ).fetchall()
        groups: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            groups.setdefault(int(row["generation_id"]), []).append(row)
        if not groups:
            self._reject(
                ReprojectionAdmissionRejection.HISTORICAL_LINKS_INCOHERENT,
                "no historical completed projection set exists",
            )

        validated: list[tuple[int, tuple[DurableReprojectionPayload, ...]]] = []
        signatures: set[tuple[tuple[str, str, str, int], ...]] = set()
        for generation_id, links in groups.items():
            try:
                payloads = self._validate_link_set(
                    connection, tuple(links), identities, import_event_id, server, account
                )
            except RetainedSourceReprojectionAdmissionError as error:
                if error.reason is ReprojectionAdmissionRejection.PAYLOAD_INCOMPLETE:
                    raise
                self._reject(
                    ReprojectionAdmissionRejection.HISTORICAL_LINKS_INCOHERENT,
                    "historical projection evidence is incomplete or conflicting",
                )
            validated.append((generation_id, payloads))
            signatures.add(
                tuple(
                    (p.projection_kind, p.projection_slot, p.target_table, p.historical_target_id)
                    for p in payloads
                )
            )
        if len(signatures) != 1:
            self._reject(
                ReprojectionAdmissionRejection.HISTORICAL_LINKS_INCOHERENT,
                "historical generations disagree on target ownership",
            )
        historical_generation_id, payloads = max(validated, key=lambda item: item[0])
        return RetainedSourceReprojectionAdmission(
            source_event_id,
            attempt_id,
            import_event_id,
            family,
            current_generation_id,
            historical_generation_id,
            server,
            account,
            identities,
            payloads,
        )

    def _validate_attempts(self, connection: sqlite3.Connection, source_event_id: int) -> int:
        rows = connection.execute(
            "SELECT * FROM discord_processing_attempts WHERE source_event_id = ? ORDER BY id",
            (source_event_id,),
        ).fetchall()
        successes = [row for row in rows if str(row["status"]) == "succeeded"]
        if len(successes) != 1 or any(row["finished_at"] is None for row in rows):
            self._reject(
                ReprojectionAdmissionRejection.ATTEMPT_INCOHERENT,
                "source must own exactly one succeeded attempt and only finished attempts",
            )
        success = successes[0]
        try:
            started = datetime.fromisoformat(str(success["started_at"]))
            finished = datetime.fromisoformat(str(success["finished_at"]))
        except ValueError:
            self._reject(
                ReprojectionAdmissionRejection.ATTEMPT_INCOHERENT,
                "successful attempt timestamps are malformed",
            )
        if finished < started or int(success["retryable"]) != 0:
            self._reject(
                ReprojectionAdmissionRejection.ATTEMPT_INCOHERENT,
                "successful attempt lifecycle is inconsistent",
            )
        return int(success["id"])

    def _validate_provenance(
        self, connection: sqlite3.Connection, source_event_id: int, source: sqlite3.Row
    ) -> tuple[int, str]:
        raw_id = source["legacy_import_event_id"]
        if raw_id is None:
            self._reject(
                ReprojectionAdmissionRejection.PROVENANCE_INCOHERENT,
                "source has no durable import provenance",
            )
        try:
            import_event_id = int(raw_id)
        except (TypeError, ValueError):
            self._reject(
                ReprojectionAdmissionRejection.PROVENANCE_INCOHERENT,
                "source import provenance is malformed",
            )
        if import_event_id <= 0:
            self._reject(
                ReprojectionAdmissionRejection.PROVENANCE_INCOHERENT,
                "source import provenance is not positive",
            )
        imported = connection.execute(
            "SELECT * FROM import_events WHERE id = ?", (import_event_id,)
        ).fetchone()
        references = connection.execute(
            "SELECT COUNT(*) FROM discord_source_events WHERE legacy_import_event_id = ?",
            (import_event_id,),
        ).fetchone()[0]
        if imported is None or int(references) != 1:
            self._reject(
                ReprojectionAdmissionRejection.PROVENANCE_INCOHERENT,
                "import provenance is missing or multiply owned",
            )
        if imported["raw_message_expired_at"] is not None:
            self._reject(
                ReprojectionAdmissionRejection.SOURCE_EXPIRED, "import evidence has expired"
            )
        try:
            datetime.fromisoformat(str(imported["observed_at"]))
        except ValueError:
            self._reject(
                ReprojectionAdmissionRejection.PROVENANCE_INCOHERENT,
                "import provenance timestamp is malformed",
            )
        return import_event_id, str(imported["kind"])

    def _validate_attribution(
        self,
        connection: sqlite3.Connection,
        source_event_id: int,
        family: str,
        expected_server: str | None,
        expected_account: str | None,
    ) -> tuple[str, str | None]:
        server_row = connection.execute(
            "SELECT * FROM discord_source_event_server_attributions WHERE source_event_id = ?",
            (source_event_id,),
        ).fetchone()
        if (
            server_row is None
            or str(server_row["status"]) != "resolved"
            or server_row["server_name"] is None
        ):
            self._reject(
                ReprojectionAdmissionRejection.ATTRIBUTION_UNRESOLVED,
                "server attribution is not resolved",
            )
        server = CatalogRepository._normalize(str(server_row["server_name"]))
        if not server or server != expected_server:
            self._reject(
                ReprojectionAdmissionRejection.ATTRIBUTION_UNRESOLVED,
                "server attribution conflicts with durable provenance",
            )
        if family in _SERVER_ONLY_FAMILIES:
            return server, None
        account_row = connection.execute(
            "SELECT * FROM discord_source_event_account_attributions WHERE source_event_id = ?",
            (source_event_id,),
        ).fetchone()
        if (
            account_row is None
            or str(account_row["status"]) != "resolved"
            or account_row["server_name"] is None
            or account_row["account_name"] is None
        ):
            self._reject(
                ReprojectionAdmissionRejection.ATTRIBUTION_UNRESOLVED,
                "account attribution is not resolved",
            )
        account_server = CatalogRepository._normalize(str(account_row["server_name"]))
        account = CatalogRepository._normalize(str(account_row["account_name"]))
        if account_server != server or not account or account != expected_account:
            self._reject(
                ReprojectionAdmissionRejection.ATTRIBUTION_UNRESOLVED,
                "account attribution conflicts with durable provenance",
            )
        return server, account

    @staticmethod
    def _has_unresolved_required_attribution(
        connection: sqlite3.Connection, source_event_id: int, family: str
    ) -> bool:
        server = connection.execute(
            "SELECT status FROM discord_source_event_server_attributions WHERE source_event_id = ?",
            (source_event_id,),
        ).fetchone()
        if server is None or str(server["status"]) != "resolved":
            return True
        if family in _SERVER_ONLY_FAMILIES:
            return False
        account = connection.execute(
            "SELECT status FROM discord_source_event_account_attributions WHERE source_event_id = ?",
            (source_event_id,),
        ).fetchone()
        return account is None or str(account["status"]) != "resolved"

    def _validate_link_set(
        self,
        connection: sqlite3.Connection,
        links: tuple[sqlite3.Row, ...],
        identities: tuple[ExpectedProjectionIdentity, ...],
        import_event_id: int,
        server: str,
        account: str | None,
    ) -> tuple[DurableReprojectionPayload, ...]:
        expected_keys = {(item.projection_kind, item.projection_slot) for item in identities}
        actual_keys = {(str(row["projection_kind"]), str(row["projection_slot"])) for row in links}
        if len(actual_keys) != len(links) or actual_keys != expected_keys:
            self._reject(
                ReprojectionAdmissionRejection.HISTORICAL_LINKS_INCOHERENT,
                "projection identities do not exactly match expectations",
            )
        by_key = {(str(row["projection_kind"]), str(row["projection_slot"])): row for row in links}
        payloads = []
        for identity in identities:
            row = by_key[(identity.projection_kind, identity.projection_slot)]
            if str(row["state"]) != "completed" or row["completed_at"] is None:
                self._reject(
                    ReprojectionAdmissionRejection.HISTORICAL_LINKS_INCOHERENT,
                    "projection link is not completed",
                )
            try:
                datetime.fromisoformat(str(row["completed_at"]))
                authority = get_projection_authority(identity.projection_kind)
                target_id = int(row["projection_row_id"])
            except (KeyError, TypeError, ValueError):
                self._reject(
                    ReprojectionAdmissionRejection.HISTORICAL_LINKS_INCOHERENT,
                    "projection link metadata is malformed",
                )
            if str(row["projection_table"]) != authority.target_table or target_id <= 0:
                self._reject(
                    ReprojectionAdmissionRejection.HISTORICAL_LINKS_INCOHERENT,
                    "projection link violates target authority",
                )
            target = connection.execute(
                f"SELECT * FROM {authority.target_table} WHERE id = ?", (target_id,)
            ).fetchone()
            if target is None or int(target["import_event_id"]) != import_event_id:
                self._reject(
                    ReprojectionAdmissionRejection.PAYLOAD_INCOMPLETE,
                    "projection target is missing or owned by another import",
                )
            enriched = self._validate_and_enrich_target(
                connection, authority.target_table, target, server, account
            )
            payloads.append(
                DurableReprojectionPayload(
                    identity.projection_kind,
                    identity.projection_slot,
                    authority.target_table,
                    target_id,
                    self._freeze_row(enriched),
                )
            )
        return tuple(payloads)

    def _validate_and_enrich_target(
        self,
        connection: sqlite3.Connection,
        table: str,
        row: sqlite3.Row,
        server: str,
        account: str | None,
    ) -> dict[str, object]:
        values = dict(row)
        try:
            datetime.fromisoformat(str(row["observed_at"]))
            for column, expected_type in _JSON_COLUMNS.get(table, {}).items():
                decoded = json.loads(str(row[column]))
                if not isinstance(decoded, expected_type):
                    raise ValueError(column)
                values[column.removesuffix("_json")] = decoded
            if "account_context_id" in row.keys():
                owner = connection.execute(
                    "SELECT ac.normalized_name AS account, sc.normalized_name AS server FROM account_contexts ac JOIN server_contexts sc ON sc.id = ac.server_context_id WHERE ac.id = ?",
                    (int(row["account_context_id"]),),
                ).fetchone()
                if (
                    owner is None
                    or str(owner["server"]) != server
                    or account is None
                    or str(owner["account"]) != account
                ):
                    raise ValueError("account ownership")
            elif "server_context_id" in row.keys():
                owner = connection.execute(
                    "SELECT normalized_name FROM server_contexts WHERE id = ?",
                    (int(row["server_context_id"]),),
                ).fetchone()
                if owner is None or str(owner["normalized_name"]) != server:
                    raise ValueError("server ownership")
            if "character_id" in row.keys() and row["character_id"] is not None:
                character = connection.execute(
                    "SELECT normalized_name, normalized_series FROM characters WHERE id = ?",
                    (int(row["character_id"]),),
                ).fetchone()
                if (
                    character is None
                    or not str(character["normalized_name"]).strip()
                    or not str(character["normalized_series"]).strip()
                ):
                    raise ValueError("character ownership")
                values["character"] = str(character["normalized_name"])
                values["series"] = str(character["normalized_series"])
            if table == "mudapin_observations" and int(row["pin_count"]) != len(
                values["pin_markers"]
            ):
                raise ValueError("mudapin count")
            if table == "disablelist_observations":
                DisableListRepository._toggle_from_row(row, "western_disabled")
                DisableListRepository._toggle_from_row(row, "irl_disabled")
            if table == "tower_state_observations":
                if row["completed_towers_observed"] not in (0, 1):
                    raise ValueError("tower presence")
                TowerStateRepository._completed_towers_from_row(row)
            if table == "kakeraloot_state_observations":
                for field in _KAKERALOOT_STATE_VALUE_FIELDS:
                    if row[f"{field}_observed"] not in (0, 1):
                        raise ValueError("kakeraloot presence")
                    KakeralootStateRepository._value_from_row(row, field)
            if table == "profile_observations":
                ProfileRepository._snapshot_from_row(row)
                if any(
                    row[name] not in (0, 1)
                    for name in (
                        "pokedex_observed",
                        "reactions_observed",
                        "mudapins_observed",
                        "kakera_balance_observed",
                        "keys_observed",
                        "bronze_keys_observed",
                        "silver_keys_observed",
                        "gold_keys_observed",
                        "sphere_stock_observed",
                        "sphere_counts_observed",
                        "badges_observed",
                    )
                ):
                    raise ValueError("profile presence")
            self._validate_typed_payload(table, row, values)
        except (
            AttributeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
            sqlite3.IntegrityError,
        ) as error:
            self._reject(
                ReprojectionAdmissionRejection.PAYLOAD_INCOMPLETE,
                f"{table} payload is malformed or incomplete: {error}",
            )
        return values

    @staticmethod
    def _validate_typed_payload(table: str, row: sqlite3.Row, values: dict[str, object]) -> None:
        if table == "server_settings_observations":
            ServerSettingsSnapshot(
                server_premium=bool(row["server_premium"]),
                prefix=row["prefix"],
                language=row["language"],
                claim_reset_minutes=row["claim_reset_minutes"],
                reset_minute=row["reset_minute"],
                reset_shift_minutes=row["reset_shift_minutes"],
                rolls_per_hour=row["rolls_per_hour"],
                claim_reaction_expiry_seconds=row["claim_reaction_expiry_seconds"],
                claimed_character_rarity_multiplier=row["claimed_character_rarity_multiplier"],
                kakera_bonus_percent=row["kakera_bonus_percent"],
                sphere_bonus_percent=row["sphere_bonus_percent"],
                game_mode=row["game_mode"],
                channel_instance=row["channel_instance"],
                metrics=values["metrics"],
            )
        elif table == "kakeraloot_settings_observations":
            KakeralootSettingsSnapshot(
                loot_cost=row["loot_cost"],
                quantity_quality_base_cost=row["quantity_quality_base_cost"],
                quantity_quality_level_increment=row["quantity_quality_level_increment"],
            )
        elif table == "kakera_state_observations":
            KakeraStateSnapshot(kakera_balance=row["kakera_balance"], badges=values["badges"])
        elif table == "mudapin_observations":
            MudapinSnapshot(pin_markers=values["pin_markers"])
        elif table == "player_bonus_observations":
            PlayerBonusSnapshot(
                metrics=values["metrics"],
                **{
                    name: row[name]
                    for name in PlayerBonusSnapshot.model_fields
                    if name != "metrics"
                },
            )
        elif table == "wishlist_observations":
            WishlistSnapshot(
                wishlist_count=row["wishlist_count"],
                wishlist_capacity=row["wishlist_capacity"],
                starwish_count=row["starwish_count"],
                starwish_capacity=row["starwish_capacity"],
                entries=values["entries"],
            )
        elif table == "disablelist_observations":
            DisableListSnapshot(
                slots_used=row["slots_used"],
                slots_capacity=row["slots_capacity"],
                total_disabled=row["total_disabled"],
                disabled_wa=row["disabled_wa"],
                disabled_ha=row["disabled_ha"],
                disabled_wg=row["disabled_wg"],
                disabled_hg=row["disabled_hg"],
                wa_pool_limit=row["wa_pool_limit"],
                ha_pool_limit=row["ha_pool_limit"],
                western_disabled=DisableListRepository._toggle_from_row(row, "western_disabled"),
                irl_disabled=DisableListRepository._toggle_from_row(row, "irl_disabled"),
                entries=values["entries"],
            )
        elif table == "timer_state_observations":
            TimerStateSnapshot.model_validate(values["snapshot"])
        elif table == "tower_state_observations":
            TowerStateSnapshot(
                current_level=row["current_level"],
                completed_towers=TowerStateRepository._completed_towers_from_row(row),
                next_level_cost=row["next_level_cost"],
                kakera_balance=row["kakera_balance"],
                built_perk_ids=values["built_perk_ids"],
            )
        elif table == "kakeraloot_state_observations":
            KakeralootStateSnapshot(
                has_kakeraloots=bool(row["has_kakeraloots"]),
                status_note=row["status_note"],
                **{
                    name: KakeralootStateRepository._value_from_row(row, name)
                    for name in _KAKERALOOT_STATE_VALUE_FIELDS
                },
            )
        elif table == "sphere_result_observations":
            snapshot = SphereResultSnapshot.model_validate(values["snapshot"])
            if snapshot.total_gained != row["total_gained"] or snapshot.stock != row["stock"]:
                raise ValueError("sphere result scalar columns conflict with snapshot")

    @classmethod
    def _freeze(cls, value: object) -> FrozenValue:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, dict):
            return tuple((str(key), cls._freeze(item)) for key, item in sorted(value.items()))
        if isinstance(value, (list, tuple)):
            return tuple(cls._freeze(item) for item in value)
        raise TypeError(f"unsupported durable payload value {type(value).__name__}")

    @classmethod
    def _freeze_row(cls, values: dict[str, object]) -> tuple[tuple[str, FrozenValue], ...]:
        try:
            return tuple((key, cls._freeze(value)) for key, value in sorted(values.items()))
        except TypeError as error:
            cls._reject(ReprojectionAdmissionRejection.PAYLOAD_INCOMPLETE, str(error))

    @staticmethod
    def _reject(reason: ReprojectionAdmissionRejection, detail: str):
        raise RetainedSourceReprojectionAdmissionError(reason, detail)
