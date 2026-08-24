"""Read-only SQL diagnostics for the local MOA catalog."""

from __future__ import annotations

import sqlite3

from moa.database.migrations import (
    CATALOG_MIGRATIONS,
    MigrationError,
    validate_current_catalog_schema,
)
from moa.models.data_health import DataHealthFinding
from moa.repositories._catalog_identity import normalize
from moa.services.projection_authority import get_projection_authority
from moa.services.projection_expectations import (
    DurableProjectionExpectationFactsError,
    Expectedness,
    ExpectedProjectionIdentity,
    load_durable_projection_expectation_facts,
    resolve_expected_projections,
)


class DataHealthSchemaError(RuntimeError):
    """Raised when a database is not a recognized current MOA catalog."""


class DataHealthRepository:
    """Own the SQL for the narrow data-health checks."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def validate_schema(self) -> None:
        """Validate current catalog metadata without running migrations."""
        try:
            validate_current_catalog_schema(self._connection)
        except MigrationError as error:
            raise DataHealthSchemaError(str(error)) from error

        applied = tuple(
            (row["version"], row["name"])
            for row in self._connection.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            )
        )
        expected = tuple((migration.version, migration.name) for migration in CATALOG_MIGRATIONS)
        if applied != expected:
            raise DataHealthSchemaError(
                "Unrecognized MOA catalog schema (migration metadata is not current)."
            )

    def find_orphans(self) -> tuple[DataHealthFinding, ...]:
        """Return exactly the three audited DH-01 orphan check results."""
        findings = [*self._foreign_key_findings()]
        findings.extend(self._kakera_account_findings())
        findings.extend(self._kakera_import_findings())
        return tuple(findings)

    def scan_impossible_identities(self) -> tuple[DataHealthFinding, ...]:
        """Return only the narrowed DH-02 impossible-identity findings."""
        findings = [*self._resolved_attribution_findings()]
        findings.extend(self._stored_normalization_findings())
        return tuple(findings)

    def scan_duplicates(self) -> tuple[DataHealthFinding, ...]:
        """Return only the audited DH-03 duplicate-invariant findings."""
        findings = [*self._character_duplicate_findings()]
        findings.extend(self._server_duplicate_findings())
        findings.extend(self._account_duplicate_findings())
        findings.extend(self._aggregate_duplicate_findings())
        findings.extend(self._revision_duplicate_findings())
        findings.extend(self._source_event_duplicate_findings())
        findings.extend(self._processing_attempt_duplicate_findings())
        findings.extend(self._attribution_duplicate_findings())
        findings.extend(self._projection_link_duplicate_findings())
        findings.extend(self._harem_scan_page_duplicate_findings())
        findings.extend(self._antidisable_duplicate_findings())
        return tuple(findings)

    def scan_projection_gaps(self) -> tuple[DataHealthFinding, ...]:
        """Return projection-gap findings for completed and claimed links."""
        completed_rows = self._connection.execute(
            """
            SELECT source.id AS source_event_id,
                   source.status AS source_status,
                   COUNT(link.id) AS completed_link_count
            FROM discord_projection_links AS link
            JOIN discord_source_events AS source
                ON source.id = link.source_event_id
            WHERE link.state = 'completed'
              AND source.status != 'succeeded'
            GROUP BY source.id, source.status
            ORDER BY source.id
            """
        )
        findings = [
            DataHealthFinding(
                check_id="DH-PG-001",
                category="projection-gap",
                entity="discord_source_events",
                local_identifier=int(row["source_event_id"]),
                reason=(
                    f"source event status {row['source_status']!r} owns "
                    f"{int(row['completed_link_count'])} completed projection link(s)"
                ),
            )
            for row in completed_rows
        ]
        claimed_rows = self._connection.execute(
            """
            SELECT source.id AS source_event_id,
                   COUNT(link.id) AS claimed_link_count
            FROM discord_projection_links AS link
            JOIN discord_source_events AS source
                ON source.id = link.source_event_id
            WHERE link.state = 'claimed'
            GROUP BY source.id
            ORDER BY source.id
            """
        )
        findings.extend(
            DataHealthFinding(
                check_id="DH-PG-002",
                category="projection-gap",
                entity="discord_source_events",
                local_identifier=int(row["source_event_id"]),
                reason=(
                    f"source event owns {int(row['claimed_link_count'])} "
                    "durably claimed projection link(s)"
                ),
            )
            for row in claimed_rows
        )
        provenance_rows = self._connection.execute(
            """
            SELECT source.id AS source_event_id,
                   COUNT(link.id) AS completed_link_count
            FROM discord_projection_links AS link
            JOIN discord_source_events AS source
                ON source.id = link.source_event_id
            WHERE source.status = 'succeeded'
              AND source.legacy_import_event_id IS NULL
              AND link.state = 'completed'
            GROUP BY source.id
            ORDER BY source.id
            """
        )
        findings.extend(
            DataHealthFinding(
                check_id="DH-PG-003",
                category="projection-gap",
                entity="discord_source_events",
                local_identifier=int(row["source_event_id"]),
                reason=(
                    "succeeded source event owns "
                    f"{int(row['completed_link_count'])} completed projection link(s) "
                    "but has no recorded import event provenance"
                ),
            )
            for row in provenance_rows
        )
        findings.extend(self._projection_authority_findings())
        findings.extend(self._projection_target_findings())
        findings.extend(self._projection_ownership_findings())
        findings.extend(self._projection_context_findings())
        findings.extend(self._projection_expectation_findings())
        return tuple(findings)

    def _projection_expectation_findings(self) -> tuple[DataHealthFinding, ...]:
        """Compare authoritative expected identities with completed link identities."""
        sources = self._connection.execute(
            """
            SELECT id AS source_event_id
            FROM discord_source_events
            WHERE status = 'succeeded'
              AND legacy_import_event_id IS NOT NULL
            ORDER BY id
            """
        )
        findings = []
        for source in sources:
            source_event_id = int(source["source_event_id"])
            links = tuple(
                self._connection.execute(
                    """
                    SELECT projection_kind, projection_slot, projection_table, state
                    FROM discord_projection_links
                    WHERE source_event_id = ?
                    ORDER BY projection_kind, projection_slot
                    """,
                    (source_event_id,),
                )
            )
            if any(link["state"] != "completed" for link in links):
                continue

            observed = set()
            structurally_valid = True
            for link in links:
                projection_kind = link["projection_kind"]
                try:
                    authority = get_projection_authority(projection_kind)
                except KeyError:
                    structurally_valid = False
                    break
                if link["projection_table"] != authority.target_table:
                    structurally_valid = False
                    break
                observed.add(
                    ExpectedProjectionIdentity(
                        projection_kind=str(projection_kind),
                        projection_slot=str(link["projection_slot"]),
                    )
                )
            if not structurally_valid:
                continue

            try:
                facts = load_durable_projection_expectation_facts(
                    self._connection, source_event_id
                )
            except DurableProjectionExpectationFactsError:
                continue
            expected_set = resolve_expected_projections(facts)

            for identity in expected_set.known_expected_identities:
                if identity in observed:
                    continue
                local_identifier = self._projection_identity_identifier(
                    source_event_id, identity
                )
                findings.append(
                    DataHealthFinding(
                        check_id="DH-PG-008",
                        category="projection-gaps",
                        entity="discord_source_events",
                        local_identifier=local_identifier,
                        reason=(
                            "expected projection identity is missing: "
                            f"projection kind {identity.projection_kind!r}, "
                            f"projection slot {identity.projection_slot!r}"
                        ),
                    )
                )

            for identity in sorted(
                observed,
                key=lambda value: (value.projection_kind, value.projection_slot),
            ):
                if expected_set.expectedness_for(identity) is not Expectedness.NOT_EXPECTED:
                    continue
                local_identifier = self._projection_identity_identifier(
                    source_event_id, identity
                )
                findings.append(
                    DataHealthFinding(
                        check_id="DH-PG-008",
                        category="projection-gaps",
                        entity="discord_projection_links",
                        local_identifier=local_identifier,
                        reason=(
                            "observed projection identity is not expected: "
                            f"projection kind {identity.projection_kind!r}, "
                            f"projection slot {identity.projection_slot!r}"
                        ),
                    )
                )
        return tuple(findings)

    @staticmethod
    def _projection_identity_identifier(
        source_event_id: int, identity: ExpectedProjectionIdentity
    ) -> str:
        return (
            f"source_event_id={source_event_id}; "
            f"projection_kind={identity.projection_kind!r}; "
            f"projection_slot={identity.projection_slot!r}"
        )

    def _projection_authority_findings(self) -> tuple[DataHealthFinding, ...]:
        rows = self._connection.execute(
            """
            SELECT source.id AS source_event_id,
                   link.projection_kind,
                   link.projection_slot,
                   link.projection_table
            FROM discord_projection_links AS link
            JOIN discord_source_events AS source
                ON source.id = link.source_event_id
            WHERE link.state = 'completed'
              AND source.status = 'succeeded'
              AND source.legacy_import_event_id IS NOT NULL
            ORDER BY source.id, link.projection_kind, link.projection_slot
            """
        )
        findings = []
        for row in rows:
            projection_kind = row["projection_kind"]
            local_identifier = (
                f"source_event_id={row['source_event_id']}; "
                f"projection_kind={projection_kind!r}; "
                f"projection_slot={row['projection_slot']!r}"
            )
            try:
                authority = get_projection_authority(projection_kind)
            except KeyError:
                findings.append(
                    DataHealthFinding(
                        check_id="DH-PG-004",
                        category="projection-gap",
                        entity="discord_projection_links",
                        local_identifier=local_identifier,
                        reason=(
                            f"projection kind {projection_kind!r} is unknown to the "
                            "shared projection authority"
                        ),
                    )
                )
                continue

            stored_table = row["projection_table"]
            if stored_table != authority.target_table:
                findings.append(
                    DataHealthFinding(
                        check_id="DH-PG-004",
                        category="projection-gap",
                        entity="discord_projection_links",
                        local_identifier=local_identifier,
                        reason=(
                            f"projection kind {projection_kind!r} authorizes target "
                            f"table {authority.target_table!r}, but stored projection "
                            f"table is {stored_table!r}"
                        ),
                    )
                )
        return tuple(findings)

    def _projection_target_findings(self) -> tuple[DataHealthFinding, ...]:
        rows = self._connection.execute(
            """
            SELECT source.id AS source_event_id,
                   link.projection_kind,
                   link.projection_slot,
                   link.projection_table,
                   link.projection_row_id
            FROM discord_projection_links AS link
            JOIN discord_source_events AS source
                ON source.id = link.source_event_id
            WHERE link.state = 'completed'
              AND source.status = 'succeeded'
              AND source.legacy_import_event_id IS NOT NULL
            ORDER BY source.id, link.projection_kind, link.projection_slot
            """
        )
        findings = []
        for row in rows:
            projection_kind = row["projection_kind"]
            try:
                authority = get_projection_authority(projection_kind)
            except KeyError:
                continue
            if row["projection_table"] != authority.target_table:
                continue

            quoted_target_table = '"' + authority.target_table.replace('"', '""') + '"'
            target_row = self._connection.execute(
                f"SELECT 1 FROM {quoted_target_table} WHERE id = ? LIMIT 1",
                (row["projection_row_id"],),
            ).fetchone()
            if target_row is not None:
                continue

            local_identifier = (
                f"source_event_id={row['source_event_id']}; "
                f"projection_kind={projection_kind!r}; "
                f"projection_slot={row['projection_slot']!r}"
            )
            findings.append(
                DataHealthFinding(
                    check_id="DH-PG-005",
                    category="projection-gap",
                    entity="discord_projection_links",
                    local_identifier=local_identifier,
                    reason=(
                        "completed projection references missing authorized target "
                        f"table {authority.target_table!r} row "
                        f"{row['projection_row_id']}"
                    ),
                )
            )
        return tuple(findings)

    def _projection_ownership_findings(self) -> tuple[DataHealthFinding, ...]:
        rows = self._connection.execute(
            """
            SELECT source.id AS source_event_id,
                   source.legacy_import_event_id,
                   link.projection_kind,
                   link.projection_slot,
                   link.projection_table,
                   link.projection_row_id
            FROM discord_projection_links AS link
            JOIN discord_source_events AS source
                ON source.id = link.source_event_id
            WHERE link.state = 'completed'
              AND source.status = 'succeeded'
              AND source.legacy_import_event_id IS NOT NULL
            ORDER BY source.id, link.projection_kind, link.projection_slot
            """
        )
        findings = []
        for row in rows:
            projection_kind = row["projection_kind"]
            try:
                authority = get_projection_authority(projection_kind)
            except KeyError:
                continue
            if row["projection_table"] != authority.target_table:
                continue

            quoted_target_table = '"' + authority.target_table.replace('"', '""') + '"'
            target_row = self._connection.execute(
                f"SELECT 1 FROM {quoted_target_table} WHERE id = ? LIMIT 1",
                (row["projection_row_id"],),
            ).fetchone()
            if target_row is None:
                continue

            if authority.target_table == "import_events":
                target_import_event_id = row["projection_row_id"]
            else:
                ownership_row = self._connection.execute(
                    f"SELECT import_event_id FROM {quoted_target_table} WHERE id = ? LIMIT 1",
                    (row["projection_row_id"],),
                ).fetchone()
                if ownership_row is None:
                    continue
                target_import_event_id = ownership_row["import_event_id"]

            if target_import_event_id == row["legacy_import_event_id"]:
                continue

            local_identifier = (
                f"source_event_id={row['source_event_id']}; "
                f"projection_kind={projection_kind!r}; "
                f"projection_slot={row['projection_slot']!r}"
            )
            if authority.target_table == "import_events":
                reason = (
                    "completed Antidisable target import-event row "
                    f"{row['projection_row_id']} does not match source import event "
                    f"{row['legacy_import_event_id']}"
                )
            else:
                reason = (
                    "completed projection target table "
                    f"{authority.target_table!r} row {row['projection_row_id']} "
                    f"is owned by import event {target_import_event_id}, but source "
                    f"event records import event {row['legacy_import_event_id']}"
                )
            findings.append(
                DataHealthFinding(
                    check_id="DH-PG-006",
                    category="projection-gap",
                    entity="discord_projection_links",
                    local_identifier=local_identifier,
                    reason=reason,
                )
            )
        return tuple(findings)

    def _projection_context_findings(self) -> tuple[DataHealthFinding, ...]:
        """Report account-context mismatches after the preceding projection gates."""
        rows = self._connection.execute(
            """
            SELECT source.id AS source_event_id,
                   source.legacy_import_event_id,
                   link.projection_kind,
                   link.projection_slot,
                   link.projection_table,
                   link.projection_row_id,
                   account.status AS account_status,
                   account.server_name AS source_account_server_name,
                   account.account_name AS source_account_name,
                   server.status AS server_status,
                   server.server_name AS source_server_name
            FROM discord_projection_links AS link
            JOIN discord_source_events AS source
                ON source.id = link.source_event_id
            LEFT JOIN discord_source_event_account_attributions AS account
                ON account.source_event_id = source.id
            LEFT JOIN discord_source_event_server_attributions AS server
                ON server.source_event_id = source.id
            WHERE link.state = 'completed'
              AND source.status = 'succeeded'
              AND source.legacy_import_event_id IS NOT NULL
            ORDER BY source.id, link.projection_kind, link.projection_slot
            """
        )
        findings = []
        for row in rows:
            try:
                authority = get_projection_authority(row["projection_kind"])
            except KeyError:
                continue
            if row["projection_table"] != authority.target_table:
                continue
            if row["account_status"] != "resolved" or row["server_status"] != "resolved":
                continue
            source_server = normalize(str(row["source_server_name"]))
            if source_server != normalize(str(row["source_account_server_name"])):
                continue
            source_account = normalize(str(row["source_account_name"]))

            quoted_target_table = '"' + authority.target_table.replace('"', '""') + '"'
            target_columns = {
                column["name"]
                for column in self._connection.execute(
                    f"PRAGMA table_info({quoted_target_table})"
                )
            }
            if "account_context_id" not in target_columns:
                continue

            target_row = self._connection.execute(
                f"""
                SELECT target.import_event_id,
                       ac.normalized_name AS target_account_name,
                       sc.normalized_name AS target_server_name
                FROM {quoted_target_table} AS target
                JOIN account_contexts AS ac ON ac.id = target.account_context_id
                JOIN server_contexts AS sc ON sc.id = ac.server_context_id
                WHERE target.id = ?
                """,
                (row["projection_row_id"],),
            ).fetchone()
            if target_row is None or target_row["import_event_id"] != row["legacy_import_event_id"]:
                continue

            target_pair = (
                normalize(str(target_row["target_server_name"])),
                normalize(str(target_row["target_account_name"])),
            )
            source_pair = (source_server, source_account)
            if target_pair == source_pair:
                continue
            local_identifier = (
                f"source_event_id={row['source_event_id']}; "
                f"projection_kind={row['projection_kind']!r}; "
                f"projection_slot={row['projection_slot']!r}"
            )
            findings.append(
                DataHealthFinding(
                    check_id="DH-PG-007",
                    category="projection-gap",
                    entity="discord_projection_links",
                    local_identifier=local_identifier,
                    reason=(
                        "completed projection target account context differs from "
                        f"resolved source attribution: expected server/account "
                        f"{source_pair!r}, actual {target_pair!r}"
                    ),
                )
            )
        return tuple(findings)

    def _character_duplicate_findings(self) -> tuple[DataHealthFinding, ...]:
        return self._grouped_duplicate_findings(
            "DH-DUP-001",
            "characters",
            ("normalized_name", "normalized_series"),
            """
            SELECT normalized_name, normalized_series, COUNT(*) AS duplicate_count
            FROM characters
            GROUP BY normalized_name, normalized_series
            HAVING COUNT(*) > 1
            ORDER BY normalized_name, normalized_series
            """,
            "character canonical identity",
        )

    def _server_duplicate_findings(self) -> tuple[DataHealthFinding, ...]:
        return self._grouped_duplicate_findings(
            "DH-DUP-002",
            "server_contexts",
            ("normalized_name",),
            """
            SELECT normalized_name, COUNT(*) AS duplicate_count
            FROM server_contexts
            GROUP BY normalized_name
            HAVING COUNT(*) > 1
            ORDER BY normalized_name
            """,
            "server normalized identity",
        )

    def _account_duplicate_findings(self) -> tuple[DataHealthFinding, ...]:
        return self._grouped_duplicate_findings(
            "DH-DUP-003",
            "account_contexts",
            ("server_context_id", "normalized_name"),
            """
            SELECT server_context_id, normalized_name, COUNT(*) AS duplicate_count
            FROM account_contexts
            GROUP BY server_context_id, normalized_name
            HAVING COUNT(*) > 1
            ORDER BY server_context_id, normalized_name
            """,
            "server-scoped account normalized identity",
        )

    def _aggregate_duplicate_findings(self) -> tuple[DataHealthFinding, ...]:
        return self._grouped_duplicate_findings(
            "DH-DUP-004",
            "discord_message_aggregates",
            ("platform", "guild_id", "channel_id", "message_id"),
            """
            SELECT platform, guild_id, channel_id, message_id,
                   COUNT(*) AS duplicate_count
            FROM discord_message_aggregates
            GROUP BY platform, guild_id, channel_id, message_id
            HAVING COUNT(*) > 1
            ORDER BY platform, guild_id, channel_id, message_id
            """,
            "Discord message aggregate identity",
        )

    def _revision_duplicate_findings(self) -> tuple[DataHealthFinding, ...]:
        findings = [
            *self._grouped_duplicate_findings(
                "DH-DUP-005",
                "discord_message_revisions",
                ("aggregate_id", "source_revision_marker", "normalized_payload_hash"),
                """
                SELECT aggregate_id, source_revision_marker, normalized_payload_hash,
                       COUNT(*) AS duplicate_count
                FROM discord_message_revisions
                WHERE source_revision_marker IS NOT NULL
                GROUP BY aggregate_id, source_revision_marker, normalized_payload_hash
                HAVING COUNT(*) > 1
                ORDER BY aggregate_id, source_revision_marker, normalized_payload_hash
                """,
                "marker-present revision identity",
            )
        ]
        findings.extend(
            self._grouped_duplicate_findings(
                "DH-DUP-005",
                "discord_message_revisions",
                ("aggregate_id", "normalized_payload_hash"),
                """
                SELECT aggregate_id, normalized_payload_hash, COUNT(*) AS duplicate_count
                FROM discord_message_revisions
                WHERE source_revision_marker IS NULL
                GROUP BY aggregate_id, normalized_payload_hash
                HAVING COUNT(*) > 1
                ORDER BY aggregate_id, normalized_payload_hash
                """,
                "marker-absent revision identity",
            )
        )
        findings.extend(
            self._grouped_duplicate_findings(
                "DH-DUP-005",
                "discord_message_revisions",
                ("aggregate_id",),
                """
                SELECT aggregate_id, COUNT(*) AS duplicate_count
                FROM discord_message_revisions
                WHERE revision_state = 'active'
                GROUP BY aggregate_id
                HAVING COUNT(*) > 1
                ORDER BY aggregate_id
                """,
                "single active revision identity",
            )
        )
        return tuple(findings)

    def _source_event_duplicate_findings(self) -> tuple[DataHealthFinding, ...]:
        findings = [
            *self._grouped_duplicate_findings(
                "DH-DUP-006",
                "discord_source_events",
                ("event_key",),
                """
                SELECT event_key, COUNT(*) AS duplicate_count
                FROM discord_source_events
                GROUP BY event_key
                HAVING COUNT(*) > 1
                ORDER BY event_key
                """,
                "event_key source-event identity",
            )
        ]
        findings.extend(
            self._grouped_duplicate_findings(
                "DH-DUP-006",
                "discord_source_events",
                ("revision_id",),
                """
                SELECT revision_id, COUNT(*) AS duplicate_count
                FROM discord_source_events
                GROUP BY revision_id
                HAVING COUNT(*) > 1
                ORDER BY revision_id
                """,
                "revision_id source-event identity",
            )
        )
        return tuple(findings)

    def _processing_attempt_duplicate_findings(self) -> tuple[DataHealthFinding, ...]:
        findings = [
            *self._grouped_duplicate_findings(
                "DH-DUP-007",
                "discord_processing_attempts",
                ("source_event_id", "attempt_number"),
                """
                SELECT source_event_id, attempt_number, COUNT(*) AS duplicate_count
                FROM discord_processing_attempts
                GROUP BY source_event_id, attempt_number
                HAVING COUNT(*) > 1
                ORDER BY source_event_id, attempt_number
                """,
                "source-event attempt identity",
            )
        ]
        findings.extend(
            self._grouped_duplicate_findings(
                "DH-DUP-007",
                "discord_processing_attempts",
                ("source_event_id",),
                """
                SELECT source_event_id, COUNT(*) AS duplicate_count
                FROM discord_processing_attempts
                WHERE status = 'processing'
                GROUP BY source_event_id
                HAVING COUNT(*) > 1
                ORDER BY source_event_id
                """,
                "single active processing attempt identity",
            )
        )
        return tuple(findings)

    def _attribution_duplicate_findings(self) -> tuple[DataHealthFinding, ...]:
        findings = [
            *self._grouped_duplicate_findings(
                "DH-DUP-008",
                "discord_source_event_server_attributions",
                ("source_event_id",),
                """
                SELECT source_event_id, COUNT(*) AS duplicate_count
                FROM discord_source_event_server_attributions
                GROUP BY source_event_id
                HAVING COUNT(*) > 1
                ORDER BY source_event_id
                """,
                "server attribution row identity",
            )
        ]
        findings.extend(
            self._grouped_duplicate_findings(
                "DH-DUP-008",
                "discord_source_event_account_attributions",
                ("source_event_id",),
                """
                SELECT source_event_id, COUNT(*) AS duplicate_count
                FROM discord_source_event_account_attributions
                GROUP BY source_event_id
                HAVING COUNT(*) > 1
                ORDER BY source_event_id
                """,
                "account attribution row identity",
            )
        )
        return tuple(findings)

    def _projection_link_duplicate_findings(self) -> tuple[DataHealthFinding, ...]:
        return self._grouped_duplicate_findings(
            "DH-DUP-009",
            "discord_projection_links",
            ("source_event_id", "projection_kind", "projection_slot"),
            """
            SELECT source_event_id, projection_kind, projection_slot,
                   COUNT(*) AS duplicate_count
            FROM discord_projection_links
            GROUP BY source_event_id, projection_kind, projection_slot
            HAVING COUNT(*) > 1
            ORDER BY source_event_id, projection_kind, projection_slot
            """,
            "projection-link identity",
        )

    def _harem_scan_page_duplicate_findings(self) -> tuple[DataHealthFinding, ...]:
        return self._grouped_duplicate_findings(
            "DH-DUP-010",
            "harem_scan_pages",
            ("harem_scan_id", "page_number"),
            """
            SELECT harem_scan_id, page_number, COUNT(*) AS duplicate_count
            FROM harem_scan_pages
            GROUP BY harem_scan_id, page_number
            HAVING COUNT(*) > 1
            ORDER BY harem_scan_id, page_number
            """,
            "scan-scoped harem page identity",
        )

    def _antidisable_duplicate_findings(self) -> tuple[DataHealthFinding, ...]:
        findings = [
            *self._grouped_duplicate_findings(
                "DH-DUP-011",
                "discord_antidisable_workflows",
                ("harem_scan_id",),
                """
                SELECT harem_scan_id, COUNT(*) AS duplicate_count
                FROM discord_antidisable_workflows
                GROUP BY harem_scan_id
                HAVING COUNT(*) > 1
                ORDER BY harem_scan_id
                """,
                "antidisable workflow per harem scan identity",
            )
        ]
        findings.extend(
            self._grouped_duplicate_findings(
                "DH-DUP-011",
                "discord_antidisable_workflows",
                ("request_message_aggregate_id",),
                """
                SELECT request_message_aggregate_id, COUNT(*) AS duplicate_count
                FROM discord_antidisable_workflows
                GROUP BY request_message_aggregate_id
                HAVING COUNT(*) > 1
                ORDER BY request_message_aggregate_id
                """,
                "antidisable request aggregate identity",
            )
        )
        findings.extend(
            self._grouped_duplicate_findings(
                "DH-DUP-011",
                "discord_antidisable_response_bindings",
                ("harem_scan_id", "response_message_aggregate_id"),
                """
                SELECT harem_scan_id, response_message_aggregate_id,
                       COUNT(*) AS duplicate_count
                FROM discord_antidisable_response_bindings
                GROUP BY harem_scan_id, response_message_aggregate_id
                HAVING COUNT(*) > 1
                ORDER BY harem_scan_id, response_message_aggregate_id
                """,
                "antidisable response binding identity",
            )
        )
        findings.extend(
            self._grouped_duplicate_findings(
                "DH-DUP-011",
                "discord_antidisable_response_bindings",
                ("response_message_aggregate_id",),
                """
                SELECT response_message_aggregate_id, COUNT(*) AS duplicate_count
                FROM discord_antidisable_response_bindings
                GROUP BY response_message_aggregate_id
                HAVING COUNT(*) > 1
                ORDER BY response_message_aggregate_id
                """,
                "antidisable response aggregate identity",
            )
        )
        return tuple(findings)

    def _grouped_duplicate_findings(
        self,
        check_id: str,
        entity: str,
        key_columns: tuple[str, ...],
        query: str,
        invariant: str,
    ) -> tuple[DataHealthFinding, ...]:
        rows = self._connection.execute(query)
        return tuple(
            DataHealthFinding(
                check_id=check_id,
                category="duplicate",
                entity=entity,
                local_identifier=self._format_duplicate_key(row, key_columns),
                reason=f"{invariant} is occupied by {int(row['duplicate_count'])} rows",
            )
            for row in rows
        )

    @staticmethod
    def _format_duplicate_key(row: sqlite3.Row, key_columns: tuple[str, ...]) -> str:
        return ", ".join(f"{column}={row[column]!r}" for column in key_columns)

    def _resolved_attribution_findings(self) -> tuple[DataHealthFinding, ...]:
        rows = self._connection.execute(
            """
            SELECT account.source_event_id,
                   account.server_name AS account_server_name,
                   server.status AS server_status,
                   server.server_name AS server_server_name
            FROM discord_source_event_account_attributions AS account
            LEFT JOIN discord_source_event_server_attributions AS server
                ON server.source_event_id = account.source_event_id
            WHERE account.status = 'resolved'
            ORDER BY account.source_event_id
            """
        )
        findings = []
        for row in rows:
            source_event_id = int(row["source_event_id"])
            if row["server_status"] != "resolved":
                reason = (
                    "resolved account attribution requires a resolved server attribution; "
                    "the corresponding server attribution is absent or non-resolved"
                )
            elif normalize(str(row["account_server_name"])) != normalize(
                str(row["server_server_name"])
            ):
                reason = (
                    "resolved account attribution server identity disagrees with the "
                    "resolved server attribution"
                )
            else:
                continue
            findings.append(
                DataHealthFinding(
                    check_id="DH-ID-001",
                    category="impossible-identity",
                    entity="discord_source_event_account_attributions",
                    local_identifier=source_event_id,
                    reason=reason,
                )
            )
        return tuple(findings)

    def _stored_normalization_findings(self) -> tuple[DataHealthFinding, ...]:
        findings = [*self._character_normalization_findings()]
        findings.extend(self._server_normalization_findings())
        findings.extend(self._account_normalization_findings())
        return tuple(findings)

    def _character_normalization_findings(self) -> tuple[DataHealthFinding, ...]:
        rows = self._connection.execute(
            """
            SELECT id, name, series, normalized_name, normalized_series
            FROM characters
            ORDER BY id
            """
        )
        return tuple(
            DataHealthFinding(
                check_id="DH-ID-002",
                category="impossible-identity",
                entity="characters",
                local_identifier=int(row["id"]),
                reason=(
                    "stored normalized identity does not match authoritative normalization "
                    f"for: {', '.join(fields)}"
                ),
            )
            for row in rows
            if (fields := self._mismatched_character_fields(row))
        )

    def _server_normalization_findings(self) -> tuple[DataHealthFinding, ...]:
        rows = self._connection.execute(
            """
            SELECT id, name, normalized_name
            FROM server_contexts
            ORDER BY id
            """
        )
        return tuple(
            DataHealthFinding(
                check_id="DH-ID-002",
                category="impossible-identity",
                entity="server_contexts",
                local_identifier=int(row["id"]),
                reason=(
                    "stored normalized identity does not match authoritative normalization "
                    "for: normalized_name"
                ),
            )
            for row in rows
            if normalize(str(row["name"])) != str(row["normalized_name"])
        )

    def _account_normalization_findings(self) -> tuple[DataHealthFinding, ...]:
        rows = self._connection.execute(
            """
            SELECT id, name, normalized_name
            FROM account_contexts
            ORDER BY id
            """
        )
        return tuple(
            DataHealthFinding(
                check_id="DH-ID-002",
                category="impossible-identity",
                entity="account_contexts",
                local_identifier=int(row["id"]),
                reason=(
                    "stored normalized identity does not match authoritative normalization "
                    "for: normalized_name"
                ),
            )
            for row in rows
            if normalize(str(row["name"])) != str(row["normalized_name"])
        )

    @staticmethod
    def _mismatched_character_fields(row: sqlite3.Row) -> tuple[str, ...]:
        fields = []
        if normalize(str(row["name"])) != str(row["normalized_name"]):
            fields.append("normalized_name")
        if normalize(str(row["series"])) != str(row["normalized_series"]):
            fields.append("normalized_series")
        return tuple(fields)

    def _foreign_key_findings(self) -> tuple[DataHealthFinding, ...]:
        findings = []
        for row in self._connection.execute("PRAGMA foreign_key_check"):
            table = str(row["table"])
            row_identifier = "?" if row["rowid"] is None else str(row["rowid"])
            parent = "?" if row["parent"] is None else str(row["parent"])
            foreign_key_id = "?" if row["fkid"] is None else str(row["fkid"])
            findings.append(
                DataHealthFinding(
                    check_id="DH-ORPH-001",
                    category="orphan",
                    entity=table,
                    local_identifier=f"rowid={row_identifier};fkid={foreign_key_id}",
                    reason=f"foreign key parent does not resolve: {parent}",
                )
            )
        return tuple(findings)

    def _kakera_account_findings(self) -> tuple[DataHealthFinding, ...]:
        rows = self._connection.execute(
            """
            SELECT observation.id
            FROM kakera_reaction_observations AS observation
            WHERE NOT EXISTS (
                SELECT 1
                FROM account_contexts AS account
                WHERE account.id = observation.account_context_id
            )
            ORDER BY observation.id
            """
        )
        return tuple(
            DataHealthFinding(
                check_id="DH-ORPH-002",
                category="orphan",
                entity="kakera_reaction_observations",
                local_identifier=row["id"],
                reason="account_context_id does not resolve to account_contexts",
            )
            for row in rows
        )

    def _kakera_import_findings(self) -> tuple[DataHealthFinding, ...]:
        rows = self._connection.execute(
            """
            SELECT observation.id
            FROM kakera_reaction_observations AS observation
            WHERE NOT EXISTS (
                SELECT 1
                FROM import_events AS event
                WHERE event.id = observation.import_event_id
            )
            ORDER BY observation.id
            """
        )
        return tuple(
            DataHealthFinding(
                check_id="DH-ORPH-003",
                category="orphan",
                entity="kakera_reaction_observations",
                local_identifier=row["id"],
                reason="import_event_id does not resolve to import_events",
            )
            for row in rows
        )
