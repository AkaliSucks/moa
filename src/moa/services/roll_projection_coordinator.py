"""Transactional coordination for durable Discord roll projections."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from moa.database.sqlite import DEFAULT_DATABASE_PATH, connect
from moa.models.character import RollObservation
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import (
    DiscordMessageProcessingConflictError,
    DiscordMessageProcessingNotFoundError,
    DiscordMessageRepository,
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
    kind: str
    slot: str
    table: str
    result_attribute: str


class RollProjectionCoordinator:
    """Own one SQLite transaction for a Discord roll and its projections."""

    _TARGET_TABLES = frozenset(
        {
            "roll_observations",
            "harem_key_observations",
            "rank_snapshots",
            "server_character_observations",
        }
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
        expected = self._expected_projections(roll, server, account)

        connection = connect(self._database_path)
        try:
            connection.execute("BEGIN")
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
                return self._coordinate_replay(connection, event, expected)

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
                roll=roll,
                server=server,
                account=account,
                raw=raw,
                source=source,
                observed_at=observed_at,
            )
            targets = self._targets_from_import(expected, imported)
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
            connection.commit()
            return RollProjectionResult(
                imported_count=1,
                import_event_id=int(imported.import_event_id),
                replay_skipped=False,
                durable_success_recorded=True,
                projection_targets=targets,
            )
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _coordinate_replay(
        self,
        connection: sqlite3.Connection,
        event: sqlite3.Row,
        expected: tuple[_ProjectionSpec, ...],
    ) -> RollProjectionResult:
        import_event_id = event["legacy_import_event_id"]
        if import_event_id is None:
            raise RollProjectionIntegrityError(
                f"succeeded source event {event['id']} has no legacy import event"
            )
        import_event = connection.execute(
            "SELECT id FROM import_events WHERE id = ?", (int(import_event_id),)
        ).fetchone()
        if import_event is None:
            raise RollProjectionIntegrityError(
                f"legacy import event {import_event_id} for source event {event['id']} is missing"
            )
        links = self._load_links(connection, int(event["id"]))
        self._validate_existing_links(
            connection,
            event,
            links,
            expected,
            allow_claimed=False,
        )
        targets = tuple(
            (spec.table, int(links[(spec.kind, spec.slot)]["projection_row_id"]))
            for spec in expected
        )
        return RollProjectionResult(
            imported_count=0,
            import_event_id=int(import_event_id),
            replay_skipped=True,
            durable_success_recorded=True,
            projection_targets=targets,
        )

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
        elif spec.table == "rank_snapshots":
            context = connection.execute(
                "SELECT normalized_name AS character, normalized_series AS series FROM characters WHERE id = ?",
                (int(row["character_id"]),),
            ).fetchone()
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

    def _expected_projections(
        self, roll: RollObservation, server: str, account: str
    ) -> tuple[_ProjectionSpec, ...]:
        slot_values = {
            "account": self._normalize(account),
            "character": self._normalize(roll.name),
            "series": self._normalize(roll.series),
            "server": self._normalize(server),
        }

        def slot(**extra: str) -> str:
            values = {**slot_values, **extra}
            return json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

        expected = [
            _ProjectionSpec(
                "catalog.roll",
                slot(),
                "roll_observations",
                "roll_observation_id",
            )
        ]
        if roll.displayed_key_count is not None and roll.displayed_key_type is not None:
            expected.append(
                _ProjectionSpec(
                    "catalog.roll_key",
                    slot(key_type=self._normalize(roll.displayed_key_type)),
                    "harem_key_observations",
                    "harem_key_observation_id",
                )
            )
        if roll.claim_rank is not None:
            expected.append(
                _ProjectionSpec(
                    "catalog.roll_rank",
                    slot(),
                    "rank_snapshots",
                    "rank_snapshot_id",
                )
            )
        if roll.kakera_value is not None:
            expected.append(
                _ProjectionSpec(
                    "catalog.roll_server_character",
                    slot(),
                    "server_character_observations",
                    "server_character_observation_id",
                )
            )
        return tuple(expected)

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
