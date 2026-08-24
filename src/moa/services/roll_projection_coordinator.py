"""Transactional coordination for durable Discord roll projections."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from moa.database.sqlite import DEFAULT_DATABASE_PATH, run_write_transaction
from moa.models.character import RollObservation
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import (
    DiscordMessageProcessingConflictError,
    DiscordMessageProcessingNotFoundError,
    DiscordMessageRepository,
)
from moa.services.projection_authority import (
    ROLL_KEY_PROJECTION,
    ROLL_PROJECTION,
    ROLL_RANK_PROJECTION,
    ROLL_SERVER_CHARACTER_PROJECTION,
    ProjectionKindAuthority,
    get_projection_authority,
)
from moa.services.projection_expectations import (
    DurableProjectionExpectationFactsError,
    Expectedness,
    ExpectedProjectionSet,
    build_projection_expectation_facts,
    load_durable_projection_expectation_facts,
    resolve_expected_projections,
)


class RollProjectionCoordinatorError(RuntimeError):
    """Base error for a roll projection coordination failure."""


class RollProjectionIntegrityError(RollProjectionCoordinatorError):
    """Raised when durable projection state cannot be trusted or completed safely."""


@dataclass(frozen=True, slots=True)
class RollProjectionResult:
    """The durable outcome of one coordinated roll."""

    imported_count: int
    import_event_id: int
    replay_skipped: bool
    durable_success_recorded: bool
    projection_targets: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class _ProjectionSpec:
    authority: ProjectionKindAuthority
    slot: str
    result_attribute: str

    @property
    def kind(self) -> str:
        return self.authority.projection_kind

    @property
    def table(self) -> str:
        return self.authority.target_table


class RollProjectionCoordinator:
    """Own one SQLite transaction for a Discord roll and its projections."""

    _TARGET_TABLES = frozenset(
        authority.target_table
        for authority in (
            ROLL_PROJECTION,
            ROLL_KEY_PROJECTION,
            ROLL_RANK_PROJECTION,
            ROLL_SERVER_CHARACTER_PROJECTION,
        )
    )

    def __init__(
        self,
        catalog_repository: CatalogRepository,
        discord_message_repository: DiscordMessageRepository,
    ) -> None:
        self._catalog = catalog_repository
        self._discord = discord_message_repository
        catalog_path = self._effective_database_path(catalog_repository)
        discord_path = self._effective_database_path(discord_message_repository)
        if catalog_path != discord_path:
            raise ValueError(
                "catalog and Discord repositories must use the same database path"
            )
        self._database_path = catalog_path

    def coordinate_roll(
        self,
        *,
        source_event_id: int,
        attempt_id: int | None,
        roll: RollObservation,
        server: str,
        account: str,
        raw: str,
        source: str,
        observed_at: datetime,
        finished_at: datetime,
    ) -> RollProjectionResult:
        """Coordinate one first-processing attempt or validate a completed replay."""
        self._validate_identity(source_event_id, "source_event_id")
        if attempt_id is not None:
            self._validate_identity(attempt_id, "attempt_id")
        observed_at = self._normalize_datetime(observed_at, "observed_at")
        finished_at = self._normalize_datetime(finished_at, "finished_at")
        expected_set = resolve_expected_projections(
            build_projection_expectation_facts(
                "roll",
                server=server,
                account=account,
                character=roll.name,
                series=roll.series,
                roll_key_count_present=roll.displayed_key_count is not None,
                roll_key_type=roll.displayed_key_type,
                roll_rank_present=roll.claim_rank is not None,
                roll_kakera_value_present=roll.kakera_value is not None,
            )
        )
        expected = self._projection_specs(expected_set)
        persistence_roll = (
            roll
            if any(spec.kind == "catalog.roll_key" for spec in expected)
            else roll.model_copy(
                update={"displayed_key_count": None, "displayed_key_type": None}
            )
        )

        def coordinate_with_connection(
            connection: sqlite3.Connection,
        ) -> RollProjectionResult:
            event = self._load_source_event(connection, source_event_id)
            if str(event["status"]) == "succeeded":
                if attempt_id is not None:
                    raise DiscordMessageProcessingConflictError(
                        f"Discord source event {source_event_id} has already succeeded"
                    )
                self._validate_attribution(
                    connection,
                    source_event_id=source_event_id,
                    server=server,
                    account=account,
                )
                return self._coordinate_replay(connection, event, roll)

            if attempt_id is None:
                raise DiscordMessageProcessingConflictError(
                    f"Discord source event {source_event_id} has no active processing attempt"
                )
            self._validate_active_attempt(connection, event, source_event_id, attempt_id)
            self._validate_attribution(
                connection,
                source_event_id=source_event_id,
                server=server,
                account=account,
            )
            links = self._load_links(connection, source_event_id)
            self._validate_existing_links(
                connection,
                event,
                links,
                expected,
                allow_claimed=False,
                roll=roll,
            )
            if any(str(link["state"]) == "completed" for link in links.values()):
                raise RollProjectionIntegrityError(
                    f"source event {source_event_id} has completed projection links while processing"
                )
            self._claim_missing_links(
                connection,
                source_event_id=source_event_id,
                expected=expected,
                claimed_at=observed_at,
            )

            imported = self._catalog._import_roll_with_connection(
                connection,
                roll=persistence_roll,
                server=server,
                account=account,
                raw=raw,
                source=source,
                observed_at=observed_at,
            )
            targets = self._targets_from_import(expected, imported)
            for spec, (table, projection_row_id) in zip(expected, targets):
                if table != spec.table:
                    raise RollProjectionIntegrityError(
                        f"projection target for {spec.kind} has the wrong table"
                    )
                self._validate_target(
                    connection,
                    event,
                    spec,
                    projection_row_id,
                    int(imported.import_event_id),
                    roll,
                )
            self._complete_projection_links(
                connection,
                source_event_id=source_event_id,
                expected=expected,
                targets=targets,
                completed_at=finished_at,
            )
            success = self._discord._mark_processing_success_with_connection(
                connection,
                source_event_id=source_event_id,
                attempt_id=attempt_id,
                finished_at=finished_at,
                legacy_import_event_id=int(imported.import_event_id),
            )
            if success.attempt_status != "succeeded" or success.source_event_status != "succeeded":
                raise RollProjectionCoordinatorError(
                    f"processing success was not recorded for source event {source_event_id}"
                )
            return RollProjectionResult(
                imported_count=1,
                import_event_id=int(imported.import_event_id),
                replay_skipped=False,
                durable_success_recorded=True,
                projection_targets=targets,
            )

        return run_write_transaction(self._database_path, coordinate_with_connection)

    def _coordinate_replay(
        self,
        connection: sqlite3.Connection,
        event: sqlite3.Row,
        roll: RollObservation,
    ) -> RollProjectionResult:
        import_event_id = event["legacy_import_event_id"]
        if import_event_id is None:
            raise RollProjectionIntegrityError(
                f"succeeded source event {event['id']} has no legacy import event"
            )
        import_event = connection.execute(
            "SELECT id, kind FROM import_events WHERE id = ?", (int(import_event_id),)
        ).fetchone()
        if import_event is None:
            raise RollProjectionIntegrityError(
                f"legacy import event {import_event_id} for source event {event['id']} is missing"
            )
        if str(import_event["kind"]) != "roll":
            raise RollProjectionIntegrityError(
                f"legacy import event {import_event_id} for source event {event['id']} is not a roll import"
            )
        try:
            durable_facts = load_durable_projection_expectation_facts(
                connection, int(event["id"])
            )
        except DurableProjectionExpectationFactsError as error:
            raise RollProjectionIntegrityError(
                "durable Roll expected set is unavailable"
            ) from error
        if durable_facts.source_family != "roll":
            raise RollProjectionIntegrityError(
                "durable Roll expected set has the wrong source family"
            )
        expected_set = resolve_expected_projections(durable_facts)
        links = self._load_links(connection, int(event["id"]))
        actual = self._validate_replay_links(
            connection,
            event,
            links,
            expected_set,
            roll=roll,
        )
        targets = tuple(
            (spec.table, int(links[(spec.kind, spec.slot)]["projection_row_id"]))
            for spec in actual
        )
        return RollProjectionResult(
            imported_count=0,
            import_event_id=int(import_event_id),
            replay_skipped=True,
            durable_success_recorded=True,
            projection_targets=targets,
        )

    def _validate_replay_links(
        self,
        connection: sqlite3.Connection,
        event: sqlite3.Row,
        links: dict[tuple[str, str], sqlite3.Row],
        expected_set: ExpectedProjectionSet,
        *,
        roll: RollObservation,
    ) -> tuple[_ProjectionSpec, ...]:
        possible_kinds = {
            assessment.projection_kind for assessment in expected_set.assessments
        }
        if any(kind not in possible_kinds for kind, _slot in links):
            raise RollProjectionIntegrityError(
                f"source event {event['id']} has unexpected projection links"
            )
        import_event_id = event["legacy_import_event_id"]
        actual: list[_ProjectionSpec] = []
        result_attributes = {
            "catalog.roll": "roll_observation_id",
            "catalog.roll_key": "harem_key_observation_id",
            "catalog.roll_rank": "rank_snapshot_id",
            "catalog.roll_server_character": "server_character_observation_id",
        }
        for assessment in expected_set.assessments:
            kind_keys = [key for key in links if key[0] == assessment.projection_kind]
            if assessment.expectedness is Expectedness.EXPECTED:
                assert assessment.identity is not None
                expected_key = (
                    assessment.identity.projection_kind,
                    assessment.identity.projection_slot,
                )
                if kind_keys != [expected_key]:
                    raise RollProjectionIntegrityError(
                        f"succeeded source event {event['id']} has an inconsistent projection set"
                    )
                spec = _ProjectionSpec(
                    get_projection_authority(assessment.projection_kind),
                    assessment.identity.projection_slot,
                    result_attributes[assessment.projection_kind],
                )
            elif assessment.expectedness is Expectedness.NOT_EXPECTED:
                if kind_keys:
                    raise RollProjectionIntegrityError(
                        f"source event {event['id']} has unexpected projection links"
                    )
                continue
            else:
                if len(kind_keys) > 1:
                    raise RollProjectionIntegrityError(
                        f"source event {event['id']} has ambiguous projection links"
                    )
                if not kind_keys:
                    continue
                spec = _ProjectionSpec(
                    get_projection_authority(assessment.projection_kind),
                    kind_keys[0][1],
                    result_attributes[assessment.projection_kind],
                )

            key = (spec.kind, spec.slot)
            link = links[key]
            if str(link["state"]) != "completed":
                raise RollProjectionIntegrityError(
                    f"projection link {spec.kind} for source event {event['id']} is not completed"
                )
            if import_event_id is None:
                raise RollProjectionIntegrityError(
                    f"completed projection link for source event {event['id']} has no import event"
                )
            if str(link["projection_table"]) != spec.table:
                raise RollProjectionIntegrityError(
                    f"projection link {spec.kind} points to a disallowed table"
                )
            if link["projection_row_id"] is None:
                raise RollProjectionIntegrityError(
                    f"projection link {spec.kind} has no target"
                )
            self._validate_target(
                connection,
                event,
                spec,
                int(link["projection_row_id"]),
                int(import_event_id),
                roll,
            )
            actual.append(spec)
        return tuple(actual)

    def _validate_attribution(
        self,
        connection: sqlite3.Connection,
        *,
        source_event_id: int,
        server: str,
        account: str,
    ) -> None:
        server_attribution = self._discord._get_server_attribution_with_connection(
            connection, source_event_id
        )
        if server_attribution is None:
            raise RollProjectionIntegrityError(
                f"source event {source_event_id} has no persisted server attribution"
            )
        if server_attribution.status != "resolved":
            raise RollProjectionIntegrityError(
                f"source event {source_event_id} has non-resolved server attribution"
            )
        if server_attribution.server_name is None or self._normalize(
            server_attribution.server_name
        ) != self._normalize(server):
            raise RollProjectionIntegrityError(
                f"source event {source_event_id} is attributed to another server"
            )

        account_attribution = self._discord._get_account_attribution_with_connection(
            connection, source_event_id
        )
        if account_attribution is None:
            raise RollProjectionIntegrityError(
                f"source event {source_event_id} has no persisted account attribution"
            )
        if account_attribution.status != "resolved":
            raise RollProjectionIntegrityError(
                f"source event {source_event_id} has non-resolved account attribution"
            )
        if account_attribution.server_name is None or self._normalize(
            account_attribution.server_name
        ) != self._normalize(server_attribution.server_name):
            raise RollProjectionIntegrityError(
                f"source event {source_event_id} has mismatched account attribution server"
            )
        if account_attribution.account_name is None or self._normalize(
            account_attribution.account_name
        ) != self._normalize(account):
            raise RollProjectionIntegrityError(
                f"source event {source_event_id} is attributed to another account"
            )

    @staticmethod
    def _load_source_event(connection: sqlite3.Connection, source_event_id: int) -> sqlite3.Row:
        event = connection.execute(
            "SELECT * FROM discord_source_events WHERE id = ?", (source_event_id,)
        ).fetchone()
        if event is None:
            raise DiscordMessageProcessingNotFoundError(
                f"Discord source event {source_event_id} was not found"
            )
        return event

    @staticmethod
    def _validate_active_attempt(
        connection: sqlite3.Connection,
        event: sqlite3.Row,
        source_event_id: int,
        attempt_id: int,
    ) -> None:
        if str(event["status"]) != "processing":
            raise DiscordMessageProcessingConflictError(
                f"Discord source event {source_event_id} is not processing"
            )
        attempt = connection.execute(
            "SELECT * FROM discord_processing_attempts WHERE id = ? AND source_event_id = ?",
            (attempt_id, source_event_id),
        ).fetchone()
        if attempt is None:
            ownership = connection.execute(
                "SELECT source_event_id FROM discord_processing_attempts WHERE id = ?",
                (attempt_id,),
            ).fetchone()
            if ownership is not None:
                raise DiscordMessageProcessingConflictError(
                    f"Discord processing attempt {attempt_id} belongs to another source event"
                )
            raise DiscordMessageProcessingNotFoundError(
                f"Discord processing attempt {attempt_id} was not found"
            )
        if str(attempt["status"]) != "processing":
            raise DiscordMessageProcessingConflictError(
                f"Discord processing attempt {attempt_id} is not processing"
            )

    @staticmethod
    def _load_links(
        connection: sqlite3.Connection, source_event_id: int
    ) -> dict[tuple[str, str], sqlite3.Row]:
        links: dict[tuple[str, str], sqlite3.Row] = {}
        for link in connection.execute(
            "SELECT * FROM discord_projection_links WHERE source_event_id = ?",
            (source_event_id,),
        ).fetchall():
            key = (str(link["projection_kind"]), str(link["projection_slot"]))
            if key in links:
                raise RollProjectionIntegrityError(
                    f"duplicate projection link identity for source event {source_event_id}"
                )
            links[key] = link
        return links

    def _validate_existing_links(
        self,
        connection: sqlite3.Connection,
        event: sqlite3.Row,
        links: dict[tuple[str, str], sqlite3.Row],
        expected: tuple[_ProjectionSpec, ...],
        *,
        allow_claimed: bool,
        roll: RollObservation,
    ) -> None:
        expected_keys = {(spec.kind, spec.slot) for spec in expected}
        actual_keys = set(links)
        if actual_keys - expected_keys:
            raise RollProjectionIntegrityError(
                f"source event {event['id']} has unexpected projection links"
            )
        import_event_id = event["legacy_import_event_id"]
        for key, link in links.items():
            state = str(link["state"])
            if state == "claimed":
                if allow_claimed:
                    continue
                raise RollProjectionIntegrityError(
                    f"projection link {key[0]} for source event {event['id']} is still claimed"
                )
            if state != "completed":
                raise RollProjectionIntegrityError(
                    f"projection link {key[0]} for source event {event['id']} has invalid state"
                )
            if import_event_id is None:
                raise RollProjectionIntegrityError(
                    f"completed projection link for source event {event['id']} has no import event"
                )
            spec = next(spec for spec in expected if (spec.kind, spec.slot) == key)
            if str(link["projection_table"]) != spec.table:
                raise RollProjectionIntegrityError(
                    f"projection link {spec.kind} points to a disallowed table"
                )
            self._validate_target(
                connection,
                event,
                spec,
                int(link["projection_row_id"]),
                int(import_event_id),
                roll,
            )
        if str(event["status"]) == "succeeded" and actual_keys != expected_keys:
            raise RollProjectionIntegrityError(
                f"succeeded source event {event['id']} has an incomplete projection set"
            )

    def _validate_target(
        self,
        connection: sqlite3.Connection,
        event: sqlite3.Row,
        spec: _ProjectionSpec,
        projection_row_id: int,
        import_event_id: int,
        roll: RollObservation,
    ) -> None:
        if spec.table not in self._TARGET_TABLES:
            raise RollProjectionIntegrityError(
                f"projection kind {spec.kind} is mapped to a disallowed table"
            )
        row = connection.execute(
            f"SELECT * FROM {spec.table} WHERE id = ?", (projection_row_id,)
        ).fetchone()
        if row is None:
            raise RollProjectionIntegrityError(
                f"projection target {spec.table}:{projection_row_id} is missing"
            )
        if int(row["import_event_id"]) != import_event_id:
            raise RollProjectionIntegrityError(
                f"projection target {spec.table}:{projection_row_id} belongs to another import event"
            )

        slot = json.loads(spec.slot)
        normalized_name = slot["character"]
        normalized_series = slot["series"]
        normalized_server = slot["server"]
        normalized_account = slot["account"]
        if spec.table == "roll_observations":
            context = connection.execute(
                """
                SELECT ac.normalized_name AS account, sc.normalized_name AS server,
                       c.normalized_name AS character, c.normalized_series AS series
                FROM account_contexts AS ac
                JOIN server_contexts AS sc ON sc.id = ac.server_context_id
                JOIN characters AS c ON c.id = ?
                WHERE ac.id = ?
                """,
                (int(row["character_id"]), int(row["account_context_id"])),
            ).fetchone()
            if row["claim_rank"] != roll.claim_rank:
                raise RollProjectionIntegrityError(
                    f"projection target {spec.table}:{projection_row_id} has mismatched claim rank"
                )
            if row["kakera_value"] != roll.kakera_value:
                raise RollProjectionIntegrityError(
                    f"projection target {spec.table}:{projection_row_id} has mismatched Kakera value"
                )
        elif spec.table == "harem_key_observations":
            context = connection.execute(
                """
                SELECT ac.normalized_name AS account, sc.normalized_name AS server,
                       c.normalized_name AS character, c.normalized_series AS series
                FROM account_contexts AS ac
                JOIN server_contexts AS sc ON sc.id = ac.server_context_id
                JOIN characters AS c ON c.id = ?
                WHERE ac.id = ?
                """,
                (int(row["character_id"]), int(row["account_context_id"])),
            ).fetchone()
            if context is not None and self._normalize(str(row["key_type"])) != slot["key_type"]:
                raise RollProjectionIntegrityError(
                    f"projection target {spec.table}:{projection_row_id} has the wrong key type"
                )
            if row["key_count"] != roll.displayed_key_count:
                raise RollProjectionIntegrityError(
                    f"projection target {spec.table}:{projection_row_id} has mismatched key count"
                )
            if row["kakera_value"] != roll.kakera_value:
                raise RollProjectionIntegrityError(
                    f"projection target {spec.table}:{projection_row_id} has mismatched Kakera value"
                )
        elif spec.table == "rank_snapshots":
            context = connection.execute(
                "SELECT normalized_name AS character, normalized_series AS series FROM characters WHERE id = ?",
                (int(row["character_id"]),),
            ).fetchone()
            if row["claim_rank"] != roll.claim_rank:
                raise RollProjectionIntegrityError(
                    f"projection target {spec.table}:{projection_row_id} has mismatched claim rank"
                )
        else:
            context = connection.execute(
                """
                SELECT sc.normalized_name AS server, c.normalized_name AS character,
                       c.normalized_series AS series
                FROM server_contexts AS sc
                JOIN characters AS c ON c.id = ?
                WHERE sc.id = ?
                """,
                (int(row["character_id"]), int(row["server_context_id"])),
            ).fetchone()
            if row["kakera_value"] != roll.kakera_value:
                raise RollProjectionIntegrityError(
                    f"projection target {spec.table}:{projection_row_id} has mismatched Kakera value"
                )

        if context is None:
            raise RollProjectionIntegrityError(
                f"projection target {spec.table}:{projection_row_id} has missing context"
            )
        for field in ("character", "series"):
            if context[field] != (normalized_name if field == "character" else normalized_series):
                raise RollProjectionIntegrityError(
                    f"projection target {spec.table}:{projection_row_id} has mismatched character context"
                )
        if "server" in context and context["server"] != normalized_server:
            raise RollProjectionIntegrityError(
                f"projection target {spec.table}:{projection_row_id} has mismatched server context"
            )
        if "account" in context and context["account"] != normalized_account:
            raise RollProjectionIntegrityError(
                f"projection target {spec.table}:{projection_row_id} has mismatched account context"
            )

    def _claim_missing_links(
        self,
        connection: sqlite3.Connection,
        *,
        source_event_id: int,
        expected: tuple[_ProjectionSpec, ...],
        claimed_at: datetime,
    ) -> None:
        value = claimed_at.isoformat()
        for spec in expected:
            connection.execute(
                """
                INSERT INTO discord_projection_links (
                    source_event_id, projection_kind, projection_slot,
                    projection_table, projection_row_id, state,
                    claimed_at, completed_at, created_at, updated_at
                ) VALUES (?, ?, ?, NULL, NULL, 'claimed', ?, NULL, ?, ?)
                """,
                (source_event_id, spec.kind, spec.slot, value, value, value),
            )

    def _complete_projection_links(
        self,
        connection: sqlite3.Connection,
        *,
        source_event_id: int,
        expected: tuple[_ProjectionSpec, ...],
        targets: tuple[tuple[str, int], ...],
        completed_at: datetime,
    ) -> None:
        value = completed_at.isoformat()
        for spec, (table, row_id) in zip(expected, targets, strict=True):
            if table != spec.table or row_id <= 0:
                raise RollProjectionIntegrityError(
                    f"import result did not provide the expected target for {spec.kind}"
                )
            updated = connection.execute(
                """
                UPDATE discord_projection_links
                SET projection_table = ?, projection_row_id = ?, state = 'completed',
                    completed_at = ?, updated_at = ?
                WHERE source_event_id = ? AND projection_kind = ? AND projection_slot = ?
                  AND state = 'claimed'
                """,
                (table, row_id, value, value, source_event_id, spec.kind, spec.slot),
            )
            if updated.rowcount != 1:
                raise RollProjectionIntegrityError(
                    f"projection link {spec.kind} could not be completed"
                )

    @staticmethod
    def _targets_from_import(
        expected: tuple[_ProjectionSpec, ...], imported: Any
    ) -> tuple[tuple[str, int], ...]:
        targets: list[tuple[str, int]] = []
        for spec in expected:
            row_id = getattr(imported, spec.result_attribute)
            if row_id is None:
                raise RollProjectionIntegrityError(
                    f"import result omitted the expected target for {spec.kind}"
                )
            targets.append((spec.table, int(row_id)))
        return tuple(targets)

    @staticmethod
    def _projection_specs(expected: ExpectedProjectionSet) -> tuple[_ProjectionSpec, ...]:
        result_attributes = {
            "catalog.roll": "roll_observation_id",
            "catalog.roll_key": "harem_key_observation_id",
            "catalog.roll_rank": "rank_snapshot_id",
            "catalog.roll_server_character": "server_character_observation_id",
        }
        return tuple(
            _ProjectionSpec(
                get_projection_authority(identity.projection_kind),
                identity.projection_slot,
                result_attributes[identity.projection_kind],
            )
            for identity in expected.known_expected_identities
        )

    @staticmethod
    def _normalize_datetime(value: datetime, field_name: str) -> datetime:
        if not isinstance(value, datetime):
            raise TypeError(f"{field_name} must be a datetime")
        if value.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _validate_identity(value: int, field_name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{field_name} must be an integer")

    @staticmethod
    def _effective_database_path(repository: Any) -> Path:
        value = getattr(repository, "_database_path", None)
        return Path(value if value is not None else DEFAULT_DATABASE_PATH).resolve()

    @staticmethod
    def _normalize(value: str) -> str:
        return CatalogRepository._normalize(value)
