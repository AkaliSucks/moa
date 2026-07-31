"""Transactional coordination for durable Discord `$adl` page projections."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from moa.database.sqlite import DEFAULT_DATABASE_PATH, connect
from moa.models.character import AntidisablePage
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository


class AntidisablePageProjectionCoordinatorError(RuntimeError):
    """Base error for an antidisable page projection coordination failure."""


class AntidisablePageProjectionStateError(AntidisablePageProjectionCoordinatorError):
    """Raised when a source event, attempt, or scan is not usable."""


class AntidisablePageProjectionIntegrityError(AntidisablePageProjectionCoordinatorError):
    """Raised when durable antidisable page state cannot be trusted."""


class AntidisablePageProjectionTargetError(AntidisablePageProjectionIntegrityError):
    """Raised when a page projection target is missing or mismatched."""


class AntidisablePageProjectionDatabasePathError(
    AntidisablePageProjectionCoordinatorError, ValueError
):
    """Raised when the repositories do not point at one database."""


@dataclass(frozen=True, slots=True)
class AntidisablePageProjectionResult:
    """The durable outcome of one coordinated antidisable page projection."""

    imported_count: int
    import_event_id: int
    scan_id: int
    page_number: int
    page_count: int
    replay_skipped: bool
    durable_success_recorded: bool
    projection_target: tuple[str, int]


class AntidisablePageProjectionCoordinator:
    """Own one SQLite transaction for an account-scoped `$adl` page."""

    _PROJECTION_KIND = "catalog.antidisable_page"
    _PROJECTION_TABLE = "import_events"
    _IMPORT_KIND = "antidisable"
    _SCAN_KIND = "antidisable"
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
            raise AntidisablePageProjectionDatabasePathError(
                "catalog and Discord repositories must use the same database path"
            )
        self._database_path = catalog_path

    def coordinate_antidisable_page(
        self,
        *,
        source_event_id: int,
        attempt_id: int | None,
        page: AntidisablePage,
        scan_id: int,
        server: str,
        account: str,
        raw: str,
        source: str,
        observed_at: datetime,
        finished_at: datetime,
    ) -> AntidisablePageProjectionResult:
        """Coordinate one first-processing attempt or validate a completed replay."""
        self._validate_identity(source_event_id, "source_event_id")
        if attempt_id is not None:
            self._validate_identity(attempt_id, "attempt_id")
        self._validate_identity(scan_id, "scan_id")
        if not isinstance(page, AntidisablePage):
            raise TypeError("page must be an AntidisablePage")
        page_number, page_count = self._validate_page_metadata(page)
        observed_at = self._normalize_datetime(observed_at, "observed_at")
        finished_at = self._normalize_datetime(finished_at, "finished_at")

        connection = connect(self._database_path)
        try:
            connection.execute("BEGIN")
            event = self._load_source_event(connection, source_event_id)
            self._validate_lifecycle(connection, event, source_event_id, attempt_id)
            self._validate_source_payload(event, raw)
            self._validate_attribution(
                connection,
                source_event_id=source_event_id,
                server=server,
                account=account,
            )
            projection_slot = self._antidisable_page_slot(
                server, account, scan_id, page_number
            )
            if str(event["status"]) == "succeeded":
                return self._coordinate_replay(
                    connection,
                    event,
                    page=page,
                    scan_id=scan_id,
                    server=server,
                    account=account,
                    raw=raw,
                    source=source,
                    observed_at=observed_at,
                    projection_slot=projection_slot,
                )

            self._validate_scan_for_import(
                connection,
                scan_id=scan_id,
                server=server,
                account=account,
                page_count=page_count,
                page_number=page_number,
            )
            self._require_new_projection_slot(
                connection,
                source_event_id=source_event_id,
                projection_slot=projection_slot,
            )
            self._claim_projection_link(
                connection,
                source_event_id=source_event_id,
                projection_slot=projection_slot,
                claimed_at=observed_at,
            )
            imported = self._catalog._import_antidisable_page_with_connection(
                connection,
                page=page,
                scan_id=scan_id,
                server=server,
                account=account,
                raw=raw,
                source=source,
                observed_at=observed_at,
            )
            import_event_id = self._positive_id(imported.import_event_id, "import_event_id")
            if imported.scan_id != scan_id:
                raise AntidisablePageProjectionIntegrityError(
                    "antidisable page import returned the wrong scan_id"
                )
            self._validate_page_target(
                connection,
                import_event_id=import_event_id,
                scan_id=scan_id,
                page=page,
                server=server,
                account=account,
                raw=raw,
                source=source,
                observed_at=observed_at,
                projection_slot=projection_slot,
                require_incomplete_scan=True,
            )
            target = (self._PROJECTION_TABLE, import_event_id)
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
                raise AntidisablePageProjectionStateError(
                    f"processing success was not recorded for source event {source_event_id}"
                )
            connection.commit()
            return AntidisablePageProjectionResult(
                imported_count=1,
                import_event_id=import_event_id,
                scan_id=scan_id,
                page_number=page_number,
                page_count=page_count,
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
        *,
        page: AntidisablePage,
        scan_id: int,
        server: str,
        account: str,
        raw: str,
        source: str,
        observed_at: datetime,
        projection_slot: str,
    ) -> AntidisablePageProjectionResult:
        import_event_id = event["legacy_import_event_id"]
        if import_event_id is None:
            raise AntidisablePageProjectionIntegrityError(
                f"succeeded source event {event['id']} has no legacy import event"
            )
        import_event_id = self._positive_id(import_event_id, "legacy_import_event_id")
        links = self._load_links(connection, int(event["id"]))
        key = (self._PROJECTION_KIND, projection_slot)
        if set(links) != {key}:
            raise AntidisablePageProjectionIntegrityError(
                f"succeeded source event {event['id']} has an inconsistent antidisable projection link"
            )
        link = links[key]
        if str(link["state"]) != "completed":
            raise AntidisablePageProjectionIntegrityError(
                f"antidisable projection for source event {event['id']} is not completed"
            )
        if str(link["projection_table"]) != self._PROJECTION_TABLE:
            raise AntidisablePageProjectionIntegrityError(
                f"succeeded source event {event['id']} has an inconsistent antidisable projection link"
            )
        projection_row_id = link["projection_row_id"]
        if projection_row_id is None:
            raise AntidisablePageProjectionTargetError(
                f"antidisable projection for source event {event['id']} has no target"
            )
        projection_row_id = self._positive_id(projection_row_id, "projection_row_id")
        if projection_row_id != import_event_id:
            raise AntidisablePageProjectionTargetError(
                "antidisable projection target does not match the legacy import event"
            )
        self._validate_page_target(
            connection,
            import_event_id=import_event_id,
            scan_id=scan_id,
            page=page,
            server=server,
            account=account,
            raw=raw,
            source=source,
            observed_at=observed_at,
            projection_slot=projection_slot,
            require_incomplete_scan=False,
        )
        return AntidisablePageProjectionResult(
            imported_count=0,
            import_event_id=import_event_id,
            scan_id=scan_id,
            page_number=self._validate_page_metadata(page)[0],
            page_count=self._validate_page_metadata(page)[1],
            replay_skipped=True,
            durable_success_recorded=True,
            projection_target=(self._PROJECTION_TABLE, import_event_id),
        )

    def _validate_page_target(
        self,
        connection: sqlite3.Connection,
        *,
        import_event_id: int,
        scan_id: int,
        page: AntidisablePage,
        server: str,
        account: str,
        raw: str,
        source: str,
        observed_at: datetime,
        projection_slot: str,
        require_incomplete_scan: bool,
    ) -> None:
        page_number, page_count = self._validate_page_metadata(page)
        scan = self._load_scan(
            connection,
            scan_id=scan_id,
            server=server,
            account=account,
        )
        if require_incomplete_scan and scan["completed_at"] is not None:
            raise AntidisablePageProjectionIntegrityError(
                f"antidisable scan {scan_id} completed during page import"
            )
        if scan["expected_page_count"] != page_count:
            raise AntidisablePageProjectionTargetError(
                f"antidisable scan {scan_id} has a mismatched page count"
            )
        self._validate_projection_slot(
            projection_slot,
            server=server,
            account=account,
            scan_id=scan_id,
            page_number=page_number,
        )
        import_event = connection.execute(
            "SELECT id, kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (import_event_id,),
        ).fetchone()
        if import_event is None:
            raise AntidisablePageProjectionTargetError(
                f"projection target import_events:{import_event_id} is missing"
            )
        if str(import_event["kind"]) != self._IMPORT_KIND:
            raise AntidisablePageProjectionTargetError(
                f"import event {import_event_id} is missing or has the wrong kind"
            )
        if (
            str(import_event["source"]) != source
            or str(import_event["observed_at"]) != observed_at.isoformat()
            or str(import_event["raw_message"]) != raw
        ):
            raise AntidisablePageProjectionTargetError(
                f"import event {import_event_id} does not match the supplied page"
            )

        page_rows = connection.execute(
            """
            SELECT harem_scan_id, page_number, import_event_id
            FROM harem_scan_pages
            WHERE import_event_id = ?
            """,
            (import_event_id,),
        ).fetchall()
        if len(page_rows) != 1 or (
            int(page_rows[0]["harem_scan_id"]) != scan_id
            or int(page_rows[0]["page_number"]) != page_number
            or int(page_rows[0]["import_event_id"]) != import_event_id
        ):
            raise AntidisablePageProjectionTargetError(
                f"import event {import_event_id} has an invalid antidisable page association"
            )

        series_rows = connection.execute(
            """
            SELECT aso.account_context_id, aso.series_name, aso.normalized_series_name,
                   aso.antidisabled_character_count, aso.observed_at, aso.import_event_id,
                   aso.harem_scan_id, sc.normalized_name AS server_name,
                   ac.normalized_name AS account_name
            FROM antidisable_series_observations AS aso
            JOIN account_contexts AS ac ON ac.id = aso.account_context_id
            JOIN server_contexts AS sc ON sc.id = ac.server_context_id
            WHERE aso.import_event_id = ?
            ORDER BY aso.id
            """,
            (import_event_id,),
        ).fetchall()
        if len(series_rows) != len(page.series_names):
            raise AntidisablePageProjectionTargetError(
                f"import event {import_event_id} has the wrong antidisable series count"
            )
        expected_account_context_id = int(scan["account_context_id"])
        for row, expected_series_name in zip(series_rows, page.series_names, strict=True):
            if (
                int(row["account_context_id"]) != expected_account_context_id
                or str(row["server_name"]) != CatalogRepository._normalize(server)
                or str(row["account_name"]) != CatalogRepository._normalize(account)
                or str(row["series_name"]) != expected_series_name
                or str(row["normalized_series_name"])
                != CatalogRepository._normalize(expected_series_name)
                or row["antidisabled_character_count"]
                != page.antidisabled_character_count
                or str(row["observed_at"]) != observed_at.isoformat()
                or int(row["import_event_id"]) != import_event_id
                or int(row["harem_scan_id"]) != scan_id
            ):
                raise AntidisablePageProjectionTargetError(
                    f"import event {import_event_id} has mismatched antidisable series data"
                )

    def _validate_scan_for_import(
        self,
        connection: sqlite3.Connection,
        *,
        scan_id: int,
        server: str,
        account: str,
        page_count: int,
        page_number: int,
    ) -> None:
        scan = self._load_scan(
            connection,
            scan_id=scan_id,
            server=server,
            account=account,
        )
        if scan["completed_at"] is not None:
            raise AntidisablePageProjectionStateError(
                f"antidisable scan {scan_id} is already complete"
            )
        if scan["expected_page_count"] not in (None, page_count):
            raise AntidisablePageProjectionStateError(
                f"antidisable scan {scan_id} has a mismatched page count"
            )
        duplicate = connection.execute(
            "SELECT 1 FROM harem_scan_pages WHERE harem_scan_id = ? AND page_number = ?",
            (scan_id, page_number),
        ).fetchone()
        if duplicate is not None:
            raise AntidisablePageProjectionStateError(
                f"antidisable scan {scan_id} already contains page {page_number}"
            )

    def _load_scan(
        self,
        connection: sqlite3.Connection,
        *,
        scan_id: int,
        server: str,
        account: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT hs.id, hs.account_context_id, hs.expected_page_count,
                   hs.completed_at, hs.scan_kind, sc.normalized_name AS server_name,
                   ac.normalized_name AS account_name
            FROM harem_scans AS hs
            JOIN account_contexts AS ac ON ac.id = hs.account_context_id
            JOIN server_contexts AS sc ON sc.id = ac.server_context_id
            WHERE hs.id = ?
            """,
            (scan_id,),
        ).fetchone()
        if row is None:
            raise AntidisablePageProjectionStateError(
                f"antidisable scan {scan_id} was not found"
            )
        if str(row["scan_kind"]) != self._SCAN_KIND:
            raise AntidisablePageProjectionStateError(
                f"scan {scan_id} is not an antidisable scan"
            )
        if (
            str(row["server_name"]) != CatalogRepository._normalize(server)
            or str(row["account_name"]) != CatalogRepository._normalize(account)
        ):
            raise AntidisablePageProjectionStateError(
                f"antidisable scan {scan_id} belongs to a different server or account"
            )
        return row

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
        if server_attribution is None or server_attribution.status != "resolved":
            raise AntidisablePageProjectionIntegrityError(
                f"source event {source_event_id} has no resolved server attribution"
            )
        if server_attribution.server_name is None or CatalogRepository._normalize(
            server_attribution.server_name
        ) != CatalogRepository._normalize(server):
            raise AntidisablePageProjectionIntegrityError(
                f"source event {source_event_id} is attributed to another server"
            )

        account_attribution = self._discord._get_account_attribution_with_connection(
            connection, source_event_id
        )
        if account_attribution is None or account_attribution.status != "resolved":
            raise AntidisablePageProjectionIntegrityError(
                f"source event {source_event_id} has no resolved account attribution"
            )
        if account_attribution.server_name is None or CatalogRepository._normalize(
            account_attribution.server_name
        ) != CatalogRepository._normalize(server_attribution.server_name):
            raise AntidisablePageProjectionIntegrityError(
                f"source event {source_event_id} has mismatched account attribution server"
            )
        if account_attribution.account_name is None or CatalogRepository._normalize(
            account_attribution.account_name
        ) != CatalogRepository._normalize(account):
            raise AntidisablePageProjectionIntegrityError(
                f"source event {source_event_id} is attributed to another account"
            )

    @staticmethod
    def _load_source_event(
        connection: sqlite3.Connection, source_event_id: int
    ) -> sqlite3.Row:
        event = connection.execute(
            "SELECT * FROM discord_source_events WHERE id = ?", (source_event_id,)
        ).fetchone()
        if event is None:
            raise AntidisablePageProjectionStateError(
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
                raise AntidisablePageProjectionStateError(
                    f"Discord source event {source_event_id} has already succeeded"
                )
            return
        if status != "processing":
            raise AntidisablePageProjectionStateError(
                f"Discord source event {source_event_id} is not processing"
            )
        if attempt_id is None:
            raise AntidisablePageProjectionStateError(
                f"Discord source event {source_event_id} has no active processing attempt"
            )
        attempt = connection.execute(
            "SELECT source_event_id, status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()
        if attempt is None:
            raise AntidisablePageProjectionStateError(
                f"Discord processing attempt {attempt_id} was not found"
            )
        if int(attempt["source_event_id"]) != source_event_id:
            raise AntidisablePageProjectionStateError(
                f"Discord processing attempt {attempt_id} belongs to another source event"
            )
        if str(attempt["status"]) != "processing":
            raise AntidisablePageProjectionStateError(
                f"Discord processing attempt {attempt_id} is not processing"
            )

    @staticmethod
    def _validate_source_payload(event: sqlite3.Row, raw: str) -> None:
        if str(event["raw_text"]) != raw:
            raise AntidisablePageProjectionIntegrityError(
                f"source event {event['id']} raw message does not match the supplied page"
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
                raise AntidisablePageProjectionIntegrityError(
                    f"duplicate projection link identity for source event {source_event_id}"
                )
            links[key] = link
        return links

    def _require_new_projection_slot(
        self,
        connection: sqlite3.Connection,
        *,
        source_event_id: int,
        projection_slot: str,
    ) -> None:
        links = self._load_links(connection, source_event_id)
        expected_key = (self._PROJECTION_KIND, projection_slot)
        if set(links) - {expected_key}:
            raise AntidisablePageProjectionIntegrityError(
                f"source event {source_event_id} has unexpected projection links"
            )
        existing = links.get(expected_key)
        if existing is not None:
            raise AntidisablePageProjectionIntegrityError(
                f"source event {source_event_id} already has an antidisable page projection"
            )

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
            raise AntidisablePageProjectionIntegrityError(
                "antidisable page import returned an invalid projection target"
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
            raise AntidisablePageProjectionIntegrityError(
                "antidisable page projection link could not be completed"
            )

    @staticmethod
    def _antidisable_page_slot(
        server: str, account: str, scan_id: int, page_number: int
    ) -> str:
        values = {
            "account": CatalogRepository._normalize(account),
            "page_number": page_number,
            "scan_id": scan_id,
            "server": CatalogRepository._normalize(server),
        }
        return json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @classmethod
    def _validate_projection_slot(
        cls,
        projection_slot: str,
        *,
        server: str,
        account: str,
        scan_id: int,
        page_number: int,
    ) -> None:
        expected = cls._antidisable_page_slot(server, account, scan_id, page_number)
        if projection_slot != expected:
            raise AntidisablePageProjectionTargetError(
                "antidisable page projection slot is invalid"
            )

    @staticmethod
    def _validate_page_metadata(page: AntidisablePage) -> tuple[int, int]:
        if page.page_number is None or page.page_count is None:
            raise AntidisablePageProjectionStateError(
                "A scanned antidisable page must include its Page X / Y indicator."
            )
        return page.page_number, page.page_count

    @staticmethod
    def _normalize_datetime(value: datetime, field_name: str) -> datetime:
        if not isinstance(value, datetime):
            raise TypeError(f"{field_name} must be a datetime")
        if value.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _validate_identity(value: int, field_name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{field_name} must be a positive integer")

    @staticmethod
    def _positive_id(value: Any, field_name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise AntidisablePageProjectionIntegrityError(
                f"antidisable page import returned an invalid {field_name}"
            )
        return int(value)

    @staticmethod
    def _effective_database_path(repository: Any) -> Path:
        value = getattr(repository, "_database_path", None)
        return Path(value if value is not None else DEFAULT_DATABASE_PATH).resolve()
