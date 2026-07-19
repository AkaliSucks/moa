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


CATALOG_MIGRATIONS = (
    Migration(
        version=1,
        name="catalog-schema-baseline",
        apply=validate_catalog_schema,
    ),
)
