"""Transactional coordination for durable Discord top and topx pages."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from moa.database.sqlite import DEFAULT_DATABASE_PATH, run_write_transaction
from moa.models.character import TopPage, UnavailableCharacterPage
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.repositories.projection_link_repository import ProjectionLinkRepository
from moa.services.projection_authority import TOP_PAGE_PROJECTION, TOPX_PAGE_PROJECTION
from moa.services.projection_expectations import (
    build_projection_expectation_facts,
    load_durable_projection_expectation_facts,
    resolve_expected_projections,
)


class TopPageProjectionError(RuntimeError):
    """A top-family source event or projection is inconsistent."""


@dataclass(frozen=True, slots=True)
class TopPageProjectionResult:
    imported_count: int
    import_event_id: int
    replay_skipped: bool
    durable_success_recorded: bool


class TopPageProjectionCoordinator:
    """Commit one top-family page and its source lifecycle in one transaction."""

    def __init__(
        self, catalog_repository: CatalogRepository, discord_repository: DiscordMessageRepository
    ) -> None:
        self._catalog = catalog_repository
        self._discord = discord_repository
        catalog_path = Path(
            getattr(catalog_repository, "_database_path", DEFAULT_DATABASE_PATH)
        ).resolve()
        discord_path = Path(
            getattr(discord_repository, "_database_path", DEFAULT_DATABASE_PATH)
        ).resolve()
        if catalog_path != discord_path:
            raise ValueError("catalog and Discord repositories must use the same database path")
        self._database_path = catalog_path

    def coordinate_top_page(
        self,
        *,
        source_event_id: int,
        attempt_id: int | None,
        page: TopPage,
        server: str,
        raw: str,
        source: str,
        observed_at: datetime,
        finished_at: datetime,
    ) -> TopPageProjectionResult:
        return self._coordinate(
            kind="top_page",
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            page=page,
            server=server,
            account=None,
            raw=raw,
            source=source,
            observed_at=observed_at,
            finished_at=finished_at,
        )

    def coordinate_topx_page(
        self,
        *,
        source_event_id: int,
        attempt_id: int | None,
        page: UnavailableCharacterPage,
        server: str,
        account: str,
        raw: str,
        source: str,
        observed_at: datetime,
        finished_at: datetime,
    ) -> TopPageProjectionResult:
        return self._coordinate(
            kind="topx_page",
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            page=page,
            server=server,
            account=account,
            raw=raw,
            source=source,
            observed_at=observed_at,
            finished_at=finished_at,
        )

    def _coordinate(
        self,
        *,
        kind: str,
        source_event_id: int,
        attempt_id: int | None,
        page: TopPage | UnavailableCharacterPage,
        server: str,
        account: str | None,
        raw: str,
        source: str,
        observed_at: datetime,
        finished_at: datetime,
    ) -> TopPageProjectionResult:
        if isinstance(source_event_id, bool) or source_event_id <= 0:
            raise ValueError("source_event_id must be positive")
        if attempt_id is not None and (isinstance(attempt_id, bool) or attempt_id <= 0):
            raise ValueError("attempt_id must be positive")
        if (kind == "top_page" and not isinstance(page, TopPage)) or (
            kind == "topx_page" and not isinstance(page, UnavailableCharacterPage)
        ):
            raise TypeError("page does not match top-family kind")
        if observed_at.utcoffset() is None or finished_at.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        observed_at = observed_at.astimezone(timezone.utc)
        finished_at = finished_at.astimezone(timezone.utc)
        identity = resolve_expected_projections(
            build_projection_expectation_facts(kind, server=server, account=account)
        ).known_expected_identities
        if len(identity) != 1:
            raise TopPageProjectionError("top-family projection scope is unresolved")
        projection_kind = identity[0].projection_kind
        projection_slot = identity[0].projection_slot
        authority = TOP_PAGE_PROJECTION if kind == "top_page" else TOPX_PAGE_PROJECTION
        if projection_kind != authority.projection_kind:
            raise TopPageProjectionError("top-family projection authority is inconsistent")

        def coordinate_with_connection(connection: sqlite3.Connection) -> TopPageProjectionResult:
            links = ProjectionLinkRepository(connection)
            generation_id = links.resolve_current_generation_id()
            event = connection.execute(
                "SELECT * FROM discord_source_events WHERE id = ?", (source_event_id,)
            ).fetchone()
            if event is None:
                raise TopPageProjectionError("source event is missing")
            attributed_server = self._discord._get_server_attribution_with_connection(
                connection, source_event_id
            )
            if (
                attributed_server is None
                or attributed_server.status != "resolved"
                or attributed_server.server_name is None
                or CatalogRepository._normalize(attributed_server.server_name)
                != CatalogRepository._normalize(server)
            ):
                raise TopPageProjectionError(
                    "source event server attribution is unresolved or mismatched"
                )
            if kind == "topx_page":
                attributed_account = self._discord._get_account_attribution_with_connection(
                    connection, source_event_id
                )
                if (
                    attributed_account is None
                    or attributed_account.status != "resolved"
                    or attributed_account.server_name is None
                    or CatalogRepository._normalize(attributed_account.server_name)
                    != CatalogRepository._normalize(server)
                    or attributed_account.account_name is None
                    or CatalogRepository._normalize(attributed_account.account_name)
                    != CatalogRepository._normalize(account or "")
                ):
                    raise TopPageProjectionError(
                        "source event account attribution is unresolved or mismatched"
                    )

            current_links = links.load_links(
                source_event_id=source_event_id, generation_id=generation_id
            )
            if event["status"] == "succeeded":
                if attempt_id is not None:
                    raise TopPageProjectionError("succeeded source event received an attempt")
                durable = resolve_expected_projections(
                    load_durable_projection_expectation_facts(connection, source_event_id)
                ).known_expected_identities
                if durable != identity:
                    raise TopPageProjectionError("durable top-family projection identity differs")
                import_event_id = event["legacy_import_event_id"]
                if not isinstance(import_event_id, int) or import_event_id <= 0:
                    raise TopPageProjectionError("succeeded source event has no import event")
                self._validate_link(
                    current_links, projection_kind, projection_slot, import_event_id
                )
                self._validate_import(
                    connection, import_event_id, kind, page, server, account, raw, source
                )
                return TopPageProjectionResult(0, import_event_id, True, True)

            if event["status"] != "processing" or attempt_id is None:
                raise TopPageProjectionError("source event has no active processing attempt")
            attempt = connection.execute(
                "SELECT source_event_id, status FROM discord_processing_attempts WHERE id = ?",
                (attempt_id,),
            ).fetchone()
            if (
                attempt is None
                or attempt["source_event_id"] != source_event_id
                or attempt["status"] != "processing"
            ):
                raise TopPageProjectionError("processing attempt is not active for source event")
            if current_links or event["legacy_import_event_id"] is not None:
                raise TopPageProjectionError(
                    "processing source event already has projection evidence"
                )
            links.claim_link(
                source_event_id=source_event_id,
                generation_id=generation_id,
                projection_kind=projection_kind,
                projection_slot=projection_slot,
                claimed_at=observed_at,
            )
            if kind == "top_page":
                assert isinstance(page, TopPage)
                top_imported = self._catalog._import_top_page_with_connection(
                    connection, page, raw, source, server, observed_at
                )
                import_event_id = top_imported.import_event_id
                imported_count = top_imported.characters_imported
            else:
                assert isinstance(page, UnavailableCharacterPage) and account is not None
                topx_imported = self._catalog._import_unavailable_characters_with_connection(
                    connection, page, server, account, raw, source, observed_at
                )
                import_event_id = topx_imported.import_event_id
                imported_count = topx_imported.characters_imported
            self._validate_import(
                connection, import_event_id, kind, page, server, account, raw, source
            )
            links.complete_claimed_link(
                source_event_id=source_event_id,
                generation_id=generation_id,
                projection_kind=projection_kind,
                projection_slot=projection_slot,
                projection_table=authority.target_table,
                projection_row_id=import_event_id,
                completed_at=finished_at,
            )
            success = self._discord._mark_processing_success_with_connection(
                connection,
                source_event_id=source_event_id,
                attempt_id=attempt_id,
                finished_at=finished_at,
                legacy_import_event_id=import_event_id,
            )
            if success.source_event_status != "succeeded" or success.attempt_status != "succeeded":
                raise TopPageProjectionError("source event success was not recorded")
            return TopPageProjectionResult(imported_count, import_event_id, False, True)

        return run_write_transaction(self._database_path, coordinate_with_connection)

    @staticmethod
    def _validate_link(
        links: tuple[sqlite3.Row, ...], kind: str, slot: str, import_event_id: int
    ) -> None:
        if len(links) != 1:
            raise TopPageProjectionError("succeeded source event has no unique projection link")
        link = links[0]
        if (
            link["projection_kind"] != kind
            or link["projection_slot"] != slot
            or link["state"] != "completed"
            or link["projection_table"] != "import_events"
            or link["projection_row_id"] != import_event_id
        ):
            raise TopPageProjectionError("succeeded source event has mismatched projection link")

    @staticmethod
    def _validate_import(
        connection: sqlite3.Connection,
        import_event_id: int,
        kind: str,
        page: TopPage | UnavailableCharacterPage,
        server: str,
        account: str | None,
        raw: str,
        source: str,
    ) -> None:
        imported = connection.execute(
            "SELECT kind, raw_message, source FROM import_events WHERE id = ?",
            (import_event_id,),
        ).fetchone()
        if (
            imported is None
            or imported["kind"] != kind
            or imported["raw_message"] != raw
            or imported["source"] != source
        ):
            raise TopPageProjectionError("top-family import event is missing or mismatched")
        ranks = connection.execute(
            """SELECT c.name, c.series, r.claim_rank, r.owner_name
               FROM rank_snapshots AS r JOIN characters AS c ON c.id = r.character_id
               WHERE r.import_event_id = ? ORDER BY r.id""",
            (import_event_id,),
        ).fetchall()
        expected_ranks = [
            (
                character.name,
                character.series,
                character.claim_rank,
                getattr(character, "owner_name", None) if kind == "top_page" else None,
            )
            for character in page.characters
        ]
        if [tuple(row) for row in ranks] != expected_ranks:
            raise TopPageProjectionError("top-family rank rows are missing or mismatched")
        if kind == "top_page":
            assert isinstance(page, TopPage)
            owners = connection.execute(
                """SELECT c.name, c.series, o.owner_name, s.normalized_name
                   FROM top_owner_observations AS o
                   JOIN characters AS c ON c.id = o.character_id
                   JOIN server_contexts AS s ON s.id = o.server_context_id
                   WHERE o.import_event_id = ? ORDER BY o.id""",
                (import_event_id,),
            ).fetchall()
            expected_owners = [
                (
                    character.name,
                    character.series,
                    character.owner_name,
                    CatalogRepository._normalize(server),
                )
                for character in page.characters
            ]
            if [tuple(row) for row in owners] != expected_owners:
                raise TopPageProjectionError("top owner rows are missing or mismatched")
        else:
            assert isinstance(page, UnavailableCharacterPage) and account is not None
            unavailable = connection.execute(
                """SELECT c.name, c.series, u.reason, a.normalized_name, s.normalized_name
                   FROM unavailable_character_observations AS u
                   JOIN characters AS c ON c.id = u.character_id
                   JOIN account_contexts AS a ON a.id = u.account_context_id
                   JOIN server_contexts AS s ON s.id = a.server_context_id
                   WHERE u.import_event_id = ? ORDER BY u.id""",
                (import_event_id,),
            ).fetchall()
            expected_unavailable = [
                (
                    character.name,
                    character.series,
                    character.reason,
                    CatalogRepository._normalize(account),
                    CatalogRepository._normalize(server),
                )
                for character in page.characters
            ]
            if [tuple(row) for row in unavailable] != expected_unavailable:
                raise TopPageProjectionError("topx unavailable rows are missing or mismatched")
