"""Transactional coordination for durable Discord `$lk` projections."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from moa.database.sqlite import DEFAULT_DATABASE_PATH, connect
from moa.models.character import KakeralootStateSnapshot
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository


class KakeralootStateProjectionCoordinatorError(RuntimeError):
    """Base error for a Kakeraloot-state projection coordination failure."""


class KakeralootStateProjectionStateError(KakeralootStateProjectionCoordinatorError):
    """Raised when a source event or processing attempt is not usable."""


class KakeralootStateProjectionIntegrityError(KakeralootStateProjectionCoordinatorError):
    """Raised when durable Kakeraloot-state projection state cannot be trusted."""


class KakeralootStateProjectionTargetError(KakeralootStateProjectionIntegrityError):
    """Raised when a Kakeraloot-state projection target is missing or mismatched."""


class KakeralootStateProjectionDatabasePathError(
    KakeralootStateProjectionCoordinatorError, ValueError
):
    """Raised when the repositories do not point at one database."""


@dataclass(frozen=True, slots=True)
class KakeralootStateProjectionResult:
    """The durable outcome of one coordinated Kakeraloot-state projection."""

    imported_count: int
    import_event_id: int
    kakeraloot_state_observation_id: int
    replay_skipped: bool
    durable_success_recorded: bool
    projection_target: tuple[str, int]


class KakeralootStateProjectionCoordinator:
    """Own one SQLite transaction for an account-scoped `$lk` projection."""

    _PROJECTION_KIND = "catalog.kakeraloot_state"
    _PROJECTION_TABLE = "kakeraloot_state_observations"
    _IMPORT_KIND = "kakeraloot_state"
    _TARGET_TABLES = frozenset({_PROJECTION_TABLE})

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
            raise KakeralootStateProjectionDatabasePathError(
                "catalog and Discord repositories must use the same database path"
            )
        self._database_path = catalog_path

    def coordinate_kakeraloot_state(
        self,
        *,
        source_event_id: int,
        attempt_id: int | None,
        state: KakeralootStateSnapshot,
        server: str,
        account: str,
        raw: str,
        source: str,
        observed_at: datetime,
        finished_at: datetime,
    ) -> KakeralootStateProjectionResult:
        """Coordinate one first-processing attempt or validate a completed replay."""
        self._validate_identity(source_event_id, "source_event_id")
        if attempt_id is not None:
            self._validate_identity(attempt_id, "attempt_id")
        observed_at = self._normalize_datetime(observed_at, "observed_at")
        finished_at = self._normalize_datetime(finished_at, "finished_at")

        connection = connect(self._database_path)
        try:
            connection.execute("BEGIN")
            event = self._load_source_event(connection, source_event_id)
            self._validate_lifecycle(connection, event, source_event_id, attempt_id)
            self._validate_attribution(
                connection,
                source_event_id=source_event_id,
                server=server,
                account=account,
            )
            projection_slot = self._kakeraloot_state_slot(server, account)
            if str(event["status"]) == "succeeded":
                return self._coordinate_replay(
                    connection,
                    event,
                    projection_slot,
                    state=state,
                )

            links = self._load_links(connection, source_event_id)
            expected_key = (self._PROJECTION_KIND, projection_slot)
            if set(links) - {expected_key}:
                raise KakeralootStateProjectionIntegrityError(
                    f"source event {source_event_id} has unexpected projection links"
                )
            existing = links.get(expected_key)
            if existing is not None:
                if str(existing["state"]) == "claimed":
                    raise KakeralootStateProjectionIntegrityError(
                        f"Kakeraloot-state projection for source event {source_event_id} is still claimed"
                    )
                raise KakeralootStateProjectionIntegrityError(
                    f"source event {source_event_id} already has a Kakeraloot-state projection"
                )

            self._claim_projection_link(
                connection,
                source_event_id=source_event_id,
                projection_slot=projection_slot,
                claimed_at=observed_at,
            )
            imported = self._catalog._import_kakeraloot_state_with_connection(
                connection,
                state=state,
                server=server,
                account=account,
                raw=raw,
                source=source,
                observed_at=observed_at,
            )
            import_event_id = self._positive_id(imported.import_event_id, "import_event_id")
            observation_id = self._positive_id(
                imported.kakeraloot_state_observation_id,
                "kakeraloot_state_observation_id",
            )
            target = (self._PROJECTION_TABLE, observation_id)
            self._validate_kakeraloot_state_target(
                connection,
                observation_id=observation_id,
                import_event_id=import_event_id,
                projection_slot=projection_slot,
                state=state,
                observed_at=observed_at,
            )
            self._complete_projection_link(
                connection,
                source_event_id=source_event_id,
                projection_slot=projection_slot,
                target=target,
                completed_at=finished_at,
            )
            success = self._discord._mark_processing_success_with_connection(
                connection,
                source_event_id=source_event_id,
                attempt_id=attempt_id,
                finished_at=finished_at,
                legacy_import_event_id=import_event_id,
            )
            if success.attempt_status != "succeeded" or success.source_event_status != "succeeded":
                raise KakeralootStateProjectionStateError(
                    f"processing success was not recorded for source event {source_event_id}"
                )
            connection.commit()
            return KakeralootStateProjectionResult(
                imported_count=1,
                import_event_id=import_event_id,
                kakeraloot_state_observation_id=observation_id,
                replay_skipped=False,
                durable_success_recorded=True,
                projection_target=target,
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
        projection_slot: str,
        *,
        state: KakeralootStateSnapshot,
    ) -> KakeralootStateProjectionResult:
        import_event_id = event["legacy_import_event_id"]
        if import_event_id is None:
            raise KakeralootStateProjectionIntegrityError(
                f"succeeded source event {event['id']} has no legacy import event"
            )
        import_event_id = self._positive_id(import_event_id, "legacy_import_event_id")
        import_event = connection.execute(
            "SELECT id, kind FROM import_events WHERE id = ?", (import_event_id,)
        ).fetchone()
        if import_event is None or str(import_event["kind"]) != self._IMPORT_KIND:
            raise KakeralootStateProjectionTargetError(
                f"legacy Kakeraloot-state import event {import_event_id} for source event {event['id']} is missing or wrong"
            )

        links = self._load_links(connection, int(event["id"]))
        key = (self._PROJECTION_KIND, projection_slot)
        if set(links) != {key}:
            raise KakeralootStateProjectionIntegrityError(
                f"succeeded source event {event['id']} has an inconsistent Kakeraloot-state projection link"
            )
        link = links[key]
        if str(link["state"]) != "completed":
            raise KakeralootStateProjectionIntegrityError(
                f"Kakeraloot-state projection for source event {event['id']} is not completed"
            )
        if str(link["projection_table"]) != self._PROJECTION_TABLE:
            raise KakeralootStateProjectionIntegrityError(
                f"succeeded source event {event['id']} has an inconsistent Kakeraloot-state projection link"
            )
        projection_row_id = link["projection_row_id"]
        if projection_row_id is None:
            raise KakeralootStateProjectionTargetError(
                f"Kakeraloot-state projection for source event {event['id']} has no target"
            )
        observation_id = self._positive_id(projection_row_id, "projection_row_id")
        self._validate_kakeraloot_state_target(
            connection,
            observation_id=observation_id,
            import_event_id=import_event_id,
            projection_slot=projection_slot,
            state=state,
        )
        return KakeralootStateProjectionResult(
            imported_count=0,
            import_event_id=import_event_id,
            kakeraloot_state_observation_id=observation_id,
            replay_skipped=True,
            durable_success_recorded=True,
            projection_target=(self._PROJECTION_TABLE, observation_id),
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
            raise KakeralootStateProjectionIntegrityError(
                f"source event {source_event_id} has no persisted server attribution"
            )
        if server_attribution.status != "resolved":
            raise KakeralootStateProjectionIntegrityError(
                f"source event {source_event_id} has non-resolved server attribution"
            )
        if server_attribution.server_name is None or CatalogRepository._normalize(
            server_attribution.server_name
        ) != CatalogRepository._normalize(server):
            raise KakeralootStateProjectionIntegrityError(
                f"source event {source_event_id} is attributed to another server"
            )

        account_attribution = self._discord._get_account_attribution_with_connection(
            connection, source_event_id
        )
        if account_attribution is None:
            raise KakeralootStateProjectionIntegrityError(
                f"source event {source_event_id} has no persisted account attribution"
            )
        if account_attribution.status != "resolved":
            raise KakeralootStateProjectionIntegrityError(
                f"source event {source_event_id} has non-resolved account attribution"
            )
        if account_attribution.server_name is None or CatalogRepository._normalize(
            account_attribution.server_name
        ) != CatalogRepository._normalize(server_attribution.server_name):
            raise KakeralootStateProjectionIntegrityError(
                f"source event {source_event_id} has mismatched account attribution server"
            )
        if account_attribution.account_name is None or CatalogRepository._normalize(
            account_attribution.account_name
        ) != CatalogRepository._normalize(account):
            raise KakeralootStateProjectionIntegrityError(
                f"source event {source_event_id} is attributed to another account"
            )

    @staticmethod
    def _load_source_event(connection: sqlite3.Connection, source_event_id: int) -> sqlite3.Row:
        event = connection.execute(
            "SELECT * FROM discord_source_events WHERE id = ?", (source_event_id,)
        ).fetchone()
        if event is None:
            raise KakeralootStateProjectionStateError(
                f"Discord source event {source_event_id} was not found"
            )
        return event

    @staticmethod
    def _validate_lifecycle(
        connection: sqlite3.Connection,
        event: sqlite3.Row,
        source_event_id: int,
        attempt_id: int | None,
    ) -> None:
        status = str(event["status"])
        if status == "succeeded":
            if attempt_id is not None:
                raise KakeralootStateProjectionStateError(
                    f"Discord source event {source_event_id} has already succeeded"
                )
            return
        if status != "processing":
            raise KakeralootStateProjectionStateError(
                f"Discord source event {source_event_id} is not processing"
            )
        if attempt_id is None:
            raise KakeralootStateProjectionStateError(
                f"Discord source event {source_event_id} has no active processing attempt"
            )
        attempt = connection.execute(
            "SELECT source_event_id, status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()
        if attempt is None:
            raise KakeralootStateProjectionStateError(
                f"Discord processing attempt {attempt_id} was not found"
            )
        if int(attempt["source_event_id"]) != source_event_id:
            raise KakeralootStateProjectionStateError(
                f"Discord processing attempt {attempt_id} belongs to another source event"
            )
        if str(attempt["status"]) != "processing":
            raise KakeralootStateProjectionStateError(
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
                raise KakeralootStateProjectionIntegrityError(
                    f"duplicate projection link identity for source event {source_event_id}"
                )
            links[key] = link
        return links

    def _claim_projection_link(
        self,
        connection: sqlite3.Connection,
        *,
        source_event_id: int,
        projection_slot: str,
        claimed_at: datetime,
    ) -> None:
        value = claimed_at.isoformat()
        connection.execute(
            """
            INSERT INTO discord_projection_links (
                source_event_id, projection_kind, projection_slot,
                projection_table, projection_row_id, state,
                claimed_at, completed_at, created_at, updated_at
            ) VALUES (?, ?, ?, NULL, NULL, 'claimed', ?, NULL, ?, ?)
            """,
            (source_event_id, self._PROJECTION_KIND, projection_slot, value, value, value),
        )

    def _complete_projection_link(
        self,
        connection: sqlite3.Connection,
        *,
        source_event_id: int,
        projection_slot: str,
        target: tuple[str, int],
        completed_at: datetime,
    ) -> None:
        table, row_id = target
        if table not in self._TARGET_TABLES or isinstance(row_id, bool) or row_id <= 0:
            raise KakeralootStateProjectionIntegrityError(
                "Kakeraloot-state import returned an invalid projection target"
            )
        value = completed_at.isoformat()
        updated = connection.execute(
            """
            UPDATE discord_projection_links
            SET projection_table = ?, projection_row_id = ?, state = 'completed',
                completed_at = ?, updated_at = ?
            WHERE source_event_id = ? AND projection_kind = ? AND projection_slot = ?
              AND state = 'claimed'
            """,
            (
                table,
                row_id,
                value,
                value,
                source_event_id,
                self._PROJECTION_KIND,
                projection_slot,
            ),
        )
        if updated.rowcount != 1:
            raise KakeralootStateProjectionIntegrityError(
                "Kakeraloot-state projection link could not be completed"
            )

    def _validate_kakeraloot_state_target(
        self,
        connection: sqlite3.Connection,
        *,
        observation_id: int,
        import_event_id: int,
        projection_slot: str,
        state: KakeralootStateSnapshot | None = None,
        observed_at: datetime | None = None,
    ) -> None:
        if self._PROJECTION_TABLE not in self._TARGET_TABLES:
            raise KakeralootStateProjectionIntegrityError(
                "Kakeraloot-state projection table is not allowlisted"
            )
        row = connection.execute(
            """
            SELECT kso.import_event_id, kso.has_kakeraloots, kso.status_note,
                   kso.rolls_stacked, kso.disable_wa_ha_reduction,
                   kso.disable_wg_hg_reduction, kso.protected_wish_level,
                   kso.protected_wish_denominator, kso.mudapins,
                   kso.rt_cooldown_reduction_hours, kso.permanent_roll_bonus,
                   kso.star_branches, kso.starwish_slots_from_branches,
                   kso.quantity_level, kso.quality_level, kso.usage_count,
                   kso.kakera_balance, kso.observed_at,
                   ac.normalized_name AS account, sc.normalized_name AS server
            FROM kakeraloot_state_observations AS kso
            JOIN account_contexts AS ac ON ac.id = kso.account_context_id
            JOIN server_contexts AS sc ON sc.id = ac.server_context_id
            WHERE kso.id = ?
            """,
            (observation_id,),
        ).fetchone()
        if row is None:
            raise KakeralootStateProjectionTargetError(
                f"projection target kakeraloot_state_observations:{observation_id} is missing"
            )
        if int(row["import_event_id"]) != import_event_id:
            raise KakeralootStateProjectionTargetError(
                f"projection target kakeraloot_state_observations:{observation_id} belongs to another import event"
            )
        import_event = connection.execute(
            "SELECT kind FROM import_events WHERE id = ?", (import_event_id,)
        ).fetchone()
        if import_event is None or str(import_event["kind"]) != self._IMPORT_KIND:
            raise KakeralootStateProjectionTargetError(
                f"import event {import_event_id} is missing or has the wrong kind"
            )
        try:
            slot = json.loads(projection_slot)
        except (TypeError, json.JSONDecodeError) as error:
            raise KakeralootStateProjectionTargetError(
                "Kakeraloot-state projection slot is invalid"
            ) from error
        if (
            not isinstance(slot, dict)
            or set(slot) != {"account", "server"}
            or not isinstance(slot["account"], str)
            or not isinstance(slot["server"], str)
        ):
            raise KakeralootStateProjectionTargetError(
                "Kakeraloot-state projection slot is invalid"
            )
        if row["server"] != slot["server"] or row["account"] != slot["account"]:
            raise KakeralootStateProjectionTargetError(
                f"projection target kakeraloot_state_observations:{observation_id} has mismatched Kakeraloot-state scope"
            )
        if state is None:
            return
        expected_values = {
            "has_kakeraloots": int(state.has_kakeraloots),
            "status_note": state.status_note,
            "rolls_stacked": state.rolls_stacked or 0,
            "disable_wa_ha_reduction": state.disable_wa_ha_reduction or 0,
            "disable_wg_hg_reduction": state.disable_wg_hg_reduction or 0,
            "protected_wish_level": state.protected_wish_level or 0,
            "protected_wish_denominator": state.protected_wish_denominator or 0,
            "mudapins": state.mudapins or 0,
            "rt_cooldown_reduction_hours": state.rt_cooldown_reduction_hours or 0,
            "permanent_roll_bonus": state.permanent_roll_bonus or 0,
            "star_branches": state.star_branches or 0,
            "starwish_slots_from_branches": state.starwish_slots_from_branches or 0,
            "quantity_level": state.quantity_level or 0,
            "quality_level": state.quality_level or 0,
            "usage_count": state.usage_count or 0,
            "kakera_balance": state.kakera_balance or 0,
        }
        for field_name, expected in expected_values.items():
            if row[field_name] != expected:
                raise KakeralootStateProjectionTargetError(
                    f"projection target kakeraloot_state_observations:{observation_id} has mismatched {field_name}"
                )
        if observed_at is not None and row["observed_at"] != observed_at.isoformat():
            raise KakeralootStateProjectionTargetError(
                f"projection target kakeraloot_state_observations:{observation_id} has mismatched observation time"
            )

    @staticmethod
    def _kakeraloot_state_slot(server: str, account: str) -> str:
        values = {
            "account": CatalogRepository._normalize(account),
            "server": CatalogRepository._normalize(server),
        }
        return json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

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
    def _positive_id(value: Any, field_name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise KakeralootStateProjectionIntegrityError(
                f"Kakeraloot-state import returned an invalid {field_name}"
            )
        return int(value)

    @staticmethod
    def _effective_database_path(repository: Any) -> Path:
        value = getattr(repository, "_database_path", None)
        return Path(value if value is not None else DEFAULT_DATABASE_PATH).resolve()
