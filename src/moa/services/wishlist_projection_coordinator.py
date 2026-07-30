"""Transactional coordination for durable Discord `$wl` projections."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from moa.database.sqlite import DEFAULT_DATABASE_PATH, connect
from moa.models.character import WishlistSnapshot
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository


class WishlistProjectionCoordinatorError(RuntimeError):
    """Base error for a wishlist projection coordination failure."""


class WishlistProjectionStateError(WishlistProjectionCoordinatorError):
    """Raised when a source event or processing attempt is not usable."""


class WishlistProjectionIntegrityError(WishlistProjectionCoordinatorError):
    """Raised when durable wishlist projection state cannot be trusted."""


class WishlistProjectionTargetError(WishlistProjectionIntegrityError):
    """Raised when a wishlist projection target is missing or mismatched."""


class WishlistProjectionDatabasePathError(WishlistProjectionCoordinatorError, ValueError):
    """Raised when the repositories do not point at one database."""


@dataclass(frozen=True, slots=True)
class WishlistProjectionResult:
    """The durable outcome of one coordinated `$wl` wishlist projection."""

    imported_count: int
    import_event_id: int
    wishlist_observation_id: int
    replay_skipped: bool
    durable_success_recorded: bool
    projection_target: tuple[str, int]


class WishlistProjectionCoordinator:
    """Own one SQLite transaction for an account-scoped `$wl` projection."""

    _PROJECTION_KIND = "catalog.wishlist"
    _PROJECTION_TABLE = "wishlist_observations"
    _IMPORT_KIND = "wishlist"
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
            raise WishlistProjectionDatabasePathError(
                "catalog and Discord repositories must use the same database path"
            )
        self._database_path = catalog_path

    def coordinate_wishlist(
        self,
        *,
        source_event_id: int,
        attempt_id: int | None,
        state: WishlistSnapshot,
        server: str,
        account: str,
        raw: str,
        source: str,
        observed_at: datetime,
        finished_at: datetime,
    ) -> WishlistProjectionResult:
        """Coordinate one first-processing attempt or validate a completed replay."""
        self._validate_identity(source_event_id, "source_event_id")
        if attempt_id is not None:
            self._validate_identity(attempt_id, "attempt_id")
        observed_at = self._normalize_datetime(observed_at, "observed_at")
        finished_at = self._normalize_datetime(finished_at, "finished_at")
        projection_slot = self._wishlist_slot(server, account)

        connection = connect(self._database_path)
        try:
            connection.execute("BEGIN")
            event = self._load_source_event(connection, source_event_id)
            self._validate_attribution(
                connection,
                source_event_id=source_event_id,
                server=server,
                account=account,
            )
            self._validate_lifecycle(connection, event, source_event_id, attempt_id)
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
                raise WishlistProjectionIntegrityError(
                    f"source event {source_event_id} has unexpected projection links"
                )
            existing = links.get(expected_key)
            if existing is not None:
                if str(existing["state"]) == "claimed":
                    raise WishlistProjectionIntegrityError(
                        f"wishlist projection for source event {source_event_id} is still claimed"
                    )
                raise WishlistProjectionIntegrityError(
                    f"source event {source_event_id} already has a wishlist projection"
                )

            self._claim_projection_link(
                connection,
                source_event_id=source_event_id,
                projection_slot=projection_slot,
                claimed_at=observed_at,
            )
            imported = self._catalog._import_wishlist_with_connection(
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
                imported.wishlist_observation_id,
                "wishlist_observation_id",
            )
            target = (self._PROJECTION_TABLE, observation_id)
            self._validate_wishlist_target(
                connection,
                observation_id=observation_id,
                import_event_id=import_event_id,
                projection_slot=projection_slot,
                state=state,
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
                raise WishlistProjectionStateError(
                    f"processing success was not recorded for source event {source_event_id}"
                )
            connection.commit()
            return WishlistProjectionResult(
                imported_count=1,
                import_event_id=import_event_id,
                wishlist_observation_id=observation_id,
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
        state: WishlistSnapshot,
    ) -> WishlistProjectionResult:
        import_event_id = event["legacy_import_event_id"]
        if import_event_id is None:
            raise WishlistProjectionIntegrityError(
                f"succeeded source event {event['id']} has no legacy import event"
            )
        import_event_id = self._positive_id(import_event_id, "legacy_import_event_id")
        import_event = connection.execute(
            "SELECT id, kind FROM import_events WHERE id = ?", (import_event_id,)
        ).fetchone()
        if import_event is None or str(import_event["kind"]) != self._IMPORT_KIND:
            raise WishlistProjectionTargetError(
                f"legacy wishlist import event {import_event_id} for source event {event['id']} is missing or wrong"
            )

        links = self._load_links(connection, int(event["id"]))
        key = (self._PROJECTION_KIND, projection_slot)
        if set(links) != {key}:
            raise WishlistProjectionIntegrityError(
                f"succeeded source event {event['id']} has an inconsistent wishlist projection link"
            )
        link = links[key]
        if str(link["state"]) != "completed":
            raise WishlistProjectionIntegrityError(
                f"wishlist projection for source event {event['id']} is not completed"
            )
        if str(link["projection_table"]) != self._PROJECTION_TABLE:
            raise WishlistProjectionIntegrityError(
                f"succeeded source event {event['id']} has an inconsistent wishlist projection link"
            )
        projection_row_id = link["projection_row_id"]
        if projection_row_id is None:
            raise WishlistProjectionTargetError(
                f"wishlist projection for source event {event['id']} has no target"
            )
        observation_id = self._positive_id(projection_row_id, "projection_row_id")
        self._validate_wishlist_target(
            connection,
            observation_id=observation_id,
            import_event_id=import_event_id,
            projection_slot=projection_slot,
            state=state,
        )
        return WishlistProjectionResult(
            imported_count=0,
            import_event_id=import_event_id,
            wishlist_observation_id=observation_id,
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
            raise WishlistProjectionIntegrityError(
                f"source event {source_event_id} has no persisted server attribution"
            )
        if server_attribution.status != "resolved":
            raise WishlistProjectionIntegrityError(
                f"source event {source_event_id} has non-resolved server attribution"
            )
        if server_attribution.server_name is None or CatalogRepository._normalize(
            server_attribution.server_name
        ) != CatalogRepository._normalize(server):
            raise WishlistProjectionIntegrityError(
                f"source event {source_event_id} is attributed to another server"
            )

        account_attribution = self._discord._get_account_attribution_with_connection(
            connection, source_event_id
        )
        if account_attribution is None:
            raise WishlistProjectionIntegrityError(
                f"source event {source_event_id} has no persisted account attribution"
            )
        if account_attribution.status != "resolved":
            raise WishlistProjectionIntegrityError(
                f"source event {source_event_id} has non-resolved account attribution"
            )
        if account_attribution.server_name is None or CatalogRepository._normalize(
            account_attribution.server_name
        ) != CatalogRepository._normalize(server_attribution.server_name):
            raise WishlistProjectionIntegrityError(
                f"source event {source_event_id} has mismatched account attribution server"
            )
        if account_attribution.account_name is None or CatalogRepository._normalize(
            account_attribution.account_name
        ) != CatalogRepository._normalize(account):
            raise WishlistProjectionIntegrityError(
                f"source event {source_event_id} is attributed to another account"
            )

    @staticmethod
    def _load_source_event(connection: sqlite3.Connection, source_event_id: int) -> sqlite3.Row:
        event = connection.execute(
            "SELECT * FROM discord_source_events WHERE id = ?", (source_event_id,)
        ).fetchone()
        if event is None:
            raise WishlistProjectionStateError(
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
                raise WishlistProjectionStateError(
                    f"Discord source event {source_event_id} has already succeeded"
                )
            return
        if status != "processing":
            raise WishlistProjectionStateError(
                f"Discord source event {source_event_id} is not processing"
            )
        if attempt_id is None:
            raise WishlistProjectionStateError(
                f"Discord source event {source_event_id} has no active processing attempt"
            )
        attempt = connection.execute(
            "SELECT source_event_id, status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()
        if attempt is None:
            raise WishlistProjectionStateError(
                f"Discord processing attempt {attempt_id} was not found"
            )
        if int(attempt["source_event_id"]) != source_event_id:
            raise WishlistProjectionStateError(
                f"Discord processing attempt {attempt_id} belongs to another source event"
            )
        if str(attempt["status"]) != "processing":
            raise WishlistProjectionStateError(
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
                raise WishlistProjectionIntegrityError(
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
            raise WishlistProjectionIntegrityError(
                "wishlist import returned an invalid projection target"
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
            raise WishlistProjectionIntegrityError(
                "wishlist projection link could not be completed"
            )

    def _validate_wishlist_target(
        self,
        connection: sqlite3.Connection,
        *,
        observation_id: int,
        import_event_id: int,
        projection_slot: str,
        state: WishlistSnapshot,
    ) -> None:
        if self._PROJECTION_TABLE not in self._TARGET_TABLES:
            raise WishlistProjectionIntegrityError(
                "wishlist projection table is not allowlisted"
            )
        row = connection.execute(
            """
            SELECT wo.import_event_id, wo.wishlist_count, wo.wishlist_capacity,
                   wo.starwish_count, wo.starwish_capacity, wo.entries_json,
                   ac.normalized_name AS account, sc.normalized_name AS server
            FROM wishlist_observations AS wo
            JOIN account_contexts AS ac ON ac.id = wo.account_context_id
            JOIN server_contexts AS sc ON sc.id = ac.server_context_id
            WHERE wo.id = ?
            """,
            (observation_id,),
        ).fetchone()
        if row is None:
            raise WishlistProjectionTargetError(
                f"projection target wishlist_observations:{observation_id} is missing"
            )
        if int(row["import_event_id"]) != import_event_id:
            raise WishlistProjectionTargetError(
                f"projection target wishlist_observations:{observation_id} belongs to another import event"
            )
        import_event = connection.execute(
            "SELECT kind FROM import_events WHERE id = ?", (import_event_id,)
        ).fetchone()
        if import_event is None or str(import_event["kind"]) != self._IMPORT_KIND:
            raise WishlistProjectionTargetError(
                f"import event {import_event_id} is missing or has the wrong kind"
            )
        try:
            slot = json.loads(projection_slot)
        except (TypeError, ValueError) as exc:
            raise WishlistProjectionTargetError(
                "wishlist projection slot is not valid JSON"
            ) from exc
        if (
            not isinstance(slot, dict)
            or set(slot) != {"account", "server"}
            or not isinstance(slot["account"], str)
            or not isinstance(slot["server"], str)
        ):
            raise WishlistProjectionTargetError(
                "wishlist projection slot has an invalid shape"
            )
        if row["server"] != slot["server"] or row["account"] != slot["account"]:
            raise WishlistProjectionTargetError(
                f"projection target wishlist_observations:{observation_id} has mismatched wishlist scope"
            )
        if row["wishlist_count"] != state.wishlist_count:
            raise WishlistProjectionTargetError(
                f"projection target wishlist_observations:{observation_id} has mismatched wishlist_count"
            )
        if row["wishlist_capacity"] != state.wishlist_capacity:
            raise WishlistProjectionTargetError(
                f"projection target wishlist_observations:{observation_id} has mismatched wishlist_capacity"
            )
        if row["starwish_count"] != state.starwish_count:
            raise WishlistProjectionTargetError(
                f"projection target wishlist_observations:{observation_id} has mismatched starwish_count"
            )
        if row["starwish_capacity"] != state.starwish_capacity:
            raise WishlistProjectionTargetError(
                f"projection target wishlist_observations:{observation_id} has mismatched starwish_capacity"
            )
        if row["entries_json"] != json.dumps(
            [entry.model_dump() for entry in state.entries]
        ):
            raise WishlistProjectionTargetError(
                f"projection target wishlist_observations:{observation_id} has mismatched entries"
            )

    @staticmethod
    def _wishlist_slot(server: str, account: str) -> str:
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
            raise WishlistProjectionIntegrityError(
                f"wishlist import returned an invalid {field_name}"
            )
        return int(value)

    @staticmethod
    def _effective_database_path(repository: Any) -> Path:
        value = getattr(repository, "_database_path", None)
        return Path(value if value is not None else DEFAULT_DATABASE_PATH).resolve()
