"""Read-only SQL diagnostics for the local MOA catalog."""

from __future__ import annotations

import sqlite3

from moa.database.migrations import CATALOG_MIGRATIONS, CATALOG_REQUIRED_COLUMNS, CATALOG_TABLES
from moa.models.data_health import DataHealthFinding


class DataHealthSchemaError(RuntimeError):
    """Raised when a database is not a recognized current MOA catalog."""


class DataHealthRepository:
    """Own the SQL for the narrow DH-01 orphan checks."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def validate_schema(self) -> None:
        """Validate current catalog metadata without running migrations."""
        tables = {
            row["name"]
            for row in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        missing_tables = sorted(CATALOG_TABLES - tables)
        if missing_tables or "schema_migrations" not in tables:
            details = []
            if missing_tables:
                details.append("missing tables: " + ", ".join(missing_tables))
            if "schema_migrations" not in tables:
                details.append("missing table: schema_migrations")
            raise DataHealthSchemaError(
                "Unrecognized MOA catalog schema (" + "; ".join(details) + ")."
            )

        missing_columns = []
        for table, required in CATALOG_REQUIRED_COLUMNS.items():
            columns = {
                row["name"]
                for row in self._connection.execute(f"PRAGMA table_info({table})")
            }
            missing = sorted(required - columns)
            if missing:
                missing_columns.append(f"{table}: {', '.join(missing)}")
        if missing_columns:
            raise DataHealthSchemaError(
                "Unrecognized MOA catalog schema (missing columns: "
                + "; ".join(missing_columns)
                + ")."
            )

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
