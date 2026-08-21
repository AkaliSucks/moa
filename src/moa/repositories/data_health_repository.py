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


class DataHealthSchemaError(RuntimeError):
    """Raised when a database is not a recognized current MOA catalog."""


class DataHealthRepository:
    """Own the SQL for the narrow DH-01 and DH-02 checks."""

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
