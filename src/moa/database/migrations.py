"""Small ordered migration runner for MOA's local SQLite database."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3


MigrationApply = Callable[[sqlite3.Connection], None]


class MigrationError(RuntimeError):
    """Raised when a database cannot be safely migrated."""


@dataclass(frozen=True)
class Migration:
    """One forward-only database migration."""

    version: int
    name: str
    apply: MigrationApply


CATALOG_TABLES = frozenset(
    {
        "characters",
        "import_events",
        "rank_snapshots",
        "server_contexts",
        "top_owner_observations",
        "server_character_observations",
        "account_contexts",
        "roll_observations",
        "claim_observations",
        "divorce_observations",
        "kakera_reaction_observations",
        "harem_key_observations",
        "owned_character_observations",
        "harem_scans",
        "harem_scan_pages",
        "antidisable_series_observations",
        "player_bonus_observations",
        "wishlist_observations",
        "disablelist_observations",
        "unavailable_character_observations",
        "kakera_state_observations",
        "personal_rare_observations",
        "tower_state_observations",
        "timer_state_observations",
        "sphere_result_observations",
        "kakeraloot_state_observations",
        "kakeraloot_settings_observations",
        "profile_observations",
        "mudapin_observations",
        "server_settings_observations",
    }
)


CATALOG_REQUIRED_COLUMNS = {
    "characters": frozenset(
        {
            "id",
            "name",
            "series",
            "normalized_name",
            "normalized_series",
            "created_at",
            "updated_at",
        }
    ),
    "import_events": frozenset({"id", "kind", "source", "observed_at", "raw_message"}),
    "rank_snapshots": frozenset(
        {"id", "character_id", "claim_rank", "like_rank", "observed_at", "import_event_id"}
    ),
    "server_contexts": frozenset(
        {"id", "name", "normalized_name", "created_at", "updated_at"}
    ),
    "account_contexts": frozenset(
        {"id", "server_context_id", "name", "normalized_name", "created_at", "updated_at"}
    ),
    "roll_observations": frozenset(
        {
            "id",
            "account_context_id",
            "character_id",
            "claim_rank",
            "kakera_value",
            "observed_at",
            "import_event_id",
        }
    ),
    "harem_key_observations": frozenset(
        {
            "id",
            "account_context_id",
            "character_id",
            "character_name",
            "normalized_character_name",
            "key_type",
            "key_count",
            "kakera_value",
            "harem_scan_id",
            "observed_at",
            "import_event_id",
        }
    ),
    "profile_observations": frozenset(
        {
            "id",
            "account_context_id",
            "profile_name",
            "collection_size",
            "kakera_balance",
            "bronze_keys",
            "silver_keys",
            "gold_keys",
            "sphere_stock",
            "observed_at",
            "import_event_id",
        }
    ),
    "sphere_result_observations": frozenset(
        {"id", "account_context_id", "snapshot_json", "total_gained", "stock", "observed_at", "import_event_id"}
    ),
}


def validate_catalog_schema(connection: sqlite3.Connection) -> None:
    """Confirm that a non-empty database is the known current catalog schema."""
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    actual_tables = {row[0] for row in rows if row[0] != "schema_migrations"}
    if actual_tables != CATALOG_TABLES:
        missing = sorted(CATALOG_TABLES - actual_tables)
        unexpected = sorted(actual_tables - CATALOG_TABLES)
        details = []
        if missing:
            details.append(f"missing tables: {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected tables: {', '.join(unexpected)}")
        raise MigrationError(
            "Unrecognized MOA catalog schema (" + "; ".join(details) + ")."
        )

    missing_columns = []
    for table, required in CATALOG_REQUIRED_COLUMNS.items():
        columns = {
            row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        missing = sorted(required - columns)
        if missing:
            missing_columns.append(f"{table}: {', '.join(missing)}")
    if missing_columns:
        raise MigrationError(
            "Unrecognized MOA catalog schema (missing columns: "
            + "; ".join(missing_columns)
            + ")."
        )


def _validate_migrations(migrations: Iterable[Migration]) -> tuple[Migration, ...]:
    definitions = tuple(migrations)
    versions = [migration.version for migration in definitions]
    if any(isinstance(version, bool) or not isinstance(version, int) or version <= 0 for version in versions):
        raise MigrationError("Migration versions must be positive integers.")
    if any(not migration.name.strip() for migration in definitions):
        raise MigrationError("Migration names must be nonblank.")
    if any(not callable(migration.apply) for migration in definitions):
        raise MigrationError("Migration apply operations must be callable.")
    if len(versions) != len(set(versions)):
        raise MigrationError("Migration versions must be unique.")
    if versions != sorted(versions):
        raise MigrationError("Migrations must be ordered by ascending version.")
    expected = list(range(1, len(versions) + 1))
    if versions != expected:
        raise MigrationError("Migration versions must be contiguous starting at version 1.")
    return definitions


def run_migrations(
    connection: sqlite3.Connection,
    migrations: Iterable[Migration],
) -> None:
    """Apply pending migrations in order, recording each successful migration."""
    definitions = _validate_migrations(migrations)
    known_versions = {migration.version for migration in definitions}
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    applied_rows = connection.execute(
        "SELECT version, name FROM schema_migrations ORDER BY version"
    ).fetchall()
    applied_versions = [row[0] for row in applied_rows]
    newer = sorted(set(applied_versions) - known_versions)
    if newer:
        raise MigrationError(
            "Database has unknown newer migration version(s): "
            + ", ".join(str(version) for version in newer)
            + "."
        )
    expected_applied = list(range(1, len(applied_versions) + 1))
    if applied_versions != expected_applied:
        raise MigrationError("Applied migrations must form a contiguous prefix.")

    for migration in definitions:
        if migration.version in applied_versions:
            continue
        try:
            connection.execute("BEGIN")
            migration.apply(connection)
            connection.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (migration.version, migration.name.strip(), datetime.now(timezone.utc).isoformat()),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def _apply_durable_discord_message_ingestion(
    connection: sqlite3.Connection,
) -> None:
    """Create the durable, storage-only Discord message ingestion schema."""
    statements = (
        """
        CREATE TABLE discord_message_aggregates (
            id INTEGER PRIMARY KEY,
            platform TEXT NOT NULL CHECK (platform = 'discord'),
            guild_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            first_received_at TEXT NOT NULL,
            last_received_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(platform, guild_id, channel_id, message_id)
        )
        """,

        """
        CREATE TABLE discord_message_revisions (
            id INTEGER PRIMARY KEY,
            aggregate_id INTEGER NOT NULL
                REFERENCES discord_message_aggregates(id),
            source_revision_marker TEXT NULL,
            normalized_payload_hash TEXT NOT NULL,
            revision_state TEXT NOT NULL CHECK (
                revision_state IN ('candidate', 'active', 'superseded', 'stale')
            ),
            selection_basis TEXT NULL,
            source_observed_at TEXT NULL,
            first_received_at TEXT NOT NULL,
            last_received_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,

        """
        CREATE UNIQUE INDEX uq_discord_revision_versioned
        ON discord_message_revisions(
            aggregate_id,
            source_revision_marker,
            normalized_payload_hash
        )
        WHERE source_revision_marker IS NOT NULL
        """,

        """
        CREATE UNIQUE INDEX uq_discord_revision_unversioned
        ON discord_message_revisions(aggregate_id, normalized_payload_hash)
        WHERE source_revision_marker IS NULL
        """,

        """
        CREATE UNIQUE INDEX uq_discord_active_revision
        ON discord_message_revisions(aggregate_id)
        WHERE revision_state = 'active'
        """,

        """
        CREATE TABLE discord_source_events (
            id INTEGER PRIMARY KEY,
            event_key TEXT NOT NULL CHECK (length(trim(event_key)) > 0) UNIQUE,
            revision_id INTEGER NOT NULL UNIQUE
                REFERENCES discord_message_revisions(id),
            event_kind TEXT NOT NULL CHECK (length(trim(event_kind)) > 0),
            status TEXT NOT NULL CHECK (
                status IN ('received', 'processing', 'succeeded', 'failed', 'unresolved_attribution')
            ),
            raw_text TEXT NOT NULL,
            payload_json TEXT NULL,
            payload_capture_version TEXT NULL,
            source_observed_at TEXT NULL,
            received_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            delivery_count INTEGER NOT NULL DEFAULT 1 CHECK (delivery_count >= 1),
            legacy_import_event_id INTEGER NULL REFERENCES import_events(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,

        """
        CREATE TABLE discord_processing_attempts (
            id INTEGER PRIMARY KEY,
            source_event_id INTEGER NOT NULL
                REFERENCES discord_source_events(id),
            attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
            status TEXT NOT NULL CHECK (
                status IN ('processing', 'succeeded', 'failed', 'unresolved_attribution')
            ),
            retryable INTEGER NOT NULL CHECK (retryable IN (0, 1)),
            parser_version TEXT NOT NULL CHECK (length(trim(parser_version)) > 0),
            router_version TEXT NOT NULL CHECK (length(trim(router_version)) > 0),
            started_at TEXT NOT NULL,
            finished_at TEXT NULL,
            lease_expires_at TEXT NULL,
            failure_code TEXT NULL,
            failure_detail TEXT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(source_event_id, attempt_number)
        )
        """,

        """
        CREATE UNIQUE INDEX uq_discord_processing_attempt
        ON discord_processing_attempts(source_event_id)
        WHERE status = 'processing'
        """,
    )
    for statement in statements:
        connection.execute(statement)


def _apply_durable_discord_projection_links(
    connection: sqlite3.Connection,
) -> None:
    """Create durable links between Discord source events and projections."""
    connection.execute(
        """
        CREATE TABLE discord_projection_links (
            id INTEGER PRIMARY KEY,
            source_event_id INTEGER NOT NULL
                REFERENCES discord_source_events(id)
                ON DELETE RESTRICT
                ON UPDATE RESTRICT,
            projection_kind TEXT NOT NULL,
            projection_slot TEXT NOT NULL,
            projection_table TEXT NULL,
            projection_row_id INTEGER NULL,
            state TEXT NOT NULL,
            claimed_at TEXT NOT NULL,
            completed_at TEXT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(source_event_id, projection_kind, projection_slot),
            CHECK(length(trim(projection_kind)) > 0),
            CHECK(length(trim(projection_slot)) > 0),
            CHECK(
                projection_table IS NULL
                OR length(trim(projection_table)) > 0
            ),
            CHECK(
                projection_row_id IS NULL
                OR projection_row_id > 0
            ),
            CHECK(
                (projection_table IS NULL AND projection_row_id IS NULL)
                OR
                (projection_table IS NOT NULL AND projection_row_id IS NOT NULL)
            ),
            CHECK(state IN ('claimed', 'completed')),
            CHECK(
                (
                    state = 'claimed'
                    AND completed_at IS NULL
                )
                OR
                (
                    state = 'completed'
                    AND projection_table IS NOT NULL
                    AND projection_row_id IS NOT NULL
                    AND completed_at IS NOT NULL
                )
            )
        )
        """
    )


def _apply_durable_discord_source_event_server_attributions(
    connection: sqlite3.Connection,
) -> None:
    """Create the durable, storage-only Discord server attribution schema."""
    connection.execute(
        """
        CREATE TABLE discord_source_event_server_attributions (
            source_event_id INTEGER PRIMARY KEY
                REFERENCES discord_source_events(id)
                ON DELETE CASCADE
                ON UPDATE RESTRICT,
            status TEXT NOT NULL CHECK (
                status IN ('resolved', 'unresolved', 'ambiguous')
            ),
            server_name TEXT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (
                (
                    status = 'resolved'
                    AND server_name IS NOT NULL
                    AND length(trim(server_name)) > 0
                )
                OR
                (
                    status IN ('unresolved', 'ambiguous')
                    AND server_name IS NULL
                )
            )
        )
        """
    )


def _apply_durable_discord_source_event_account_attributions(
    connection: sqlite3.Connection,
) -> None:
    """Create the durable, storage-only Discord account attribution schema."""
    connection.execute(
        """
        CREATE TABLE discord_source_event_account_attributions (
            source_event_id INTEGER PRIMARY KEY
                REFERENCES discord_source_events(id)
                ON DELETE CASCADE
                ON UPDATE RESTRICT,
            status TEXT NOT NULL CHECK (
                status IN ('resolved', 'unresolved', 'ambiguous')
            ),
            server_name TEXT NULL,
            account_name TEXT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (
                (
                    status = 'resolved'
                    AND server_name IS NOT NULL
                    AND length(trim(server_name)) > 0
                    AND account_name IS NOT NULL
                    AND length(trim(account_name)) > 0
                )
                OR
                (
                    status IN ('unresolved', 'ambiguous')
                    AND server_name IS NULL
                    AND account_name IS NULL
                )
            )
        )
        """
    )


def _apply_durable_discord_antidisable_workflow_bindings(
    connection: sqlite3.Connection,
) -> None:
    """Create durable Discord antidisable workflow and response bindings."""
    statements = (
        """
        CREATE TABLE discord_antidisable_workflows (
            harem_scan_id INTEGER PRIMARY KEY
                REFERENCES harem_scans(id)
                ON DELETE RESTRICT
                ON UPDATE RESTRICT,
            request_message_aggregate_id INTEGER NOT NULL UNIQUE
                REFERENCES discord_message_aggregates(id)
                ON DELETE RESTRICT
                ON UPDATE RESTRICT,
            requesting_user_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            CHECK(length(trim(requesting_user_id)) > 0),
            CHECK(expires_at > created_at)
        )
        """,
        """
        CREATE TABLE discord_antidisable_response_bindings (
            harem_scan_id INTEGER NOT NULL
                REFERENCES discord_antidisable_workflows(harem_scan_id)
                ON DELETE RESTRICT
                ON UPDATE RESTRICT,
            response_message_aggregate_id INTEGER NOT NULL UNIQUE
                REFERENCES discord_message_aggregates(id)
                ON DELETE RESTRICT
                ON UPDATE RESTRICT,
            bound_at TEXT NOT NULL,
            PRIMARY KEY (harem_scan_id, response_message_aggregate_id)
        )
        """,
        """
        CREATE INDEX ix_discord_antidisable_workflows_expires_at
        ON discord_antidisable_workflows(expires_at, harem_scan_id)
        """,
    )
    for statement in statements:
        connection.execute(statement)


CATALOG_MIGRATIONS = (
    Migration(
        version=1,
        name="catalog-schema-baseline",
        apply=validate_catalog_schema,
    ),
    Migration(
        version=2,
        name="durable-discord-message-ingestion",
        apply=_apply_durable_discord_message_ingestion,
    ),
    Migration(
        version=3,
        name="durable-discord-projection-links",
        apply=_apply_durable_discord_projection_links,
    ),
    Migration(
        version=4,
        name="durable-discord-source-event-server-attributions",
        apply=_apply_durable_discord_source_event_server_attributions,
    ),
    Migration(
        version=5,
        name="durable-discord-source-event-account-attributions",
        apply=_apply_durable_discord_source_event_account_attributions,
    ),
    Migration(
        version=6,
        name="durable-discord-antidisable-workflow-bindings",
        apply=_apply_durable_discord_antidisable_workflow_bindings,
    ),
)
