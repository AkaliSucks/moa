"""Small ordered migration runner for MOA's local SQLite database."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
import json
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


CATALOG_DURABLE_TABLES = frozenset(
    {
        "discord_message_aggregates",
        "discord_message_revisions",
        "discord_source_events",
        "discord_processing_attempts",
        "projection_generations",
        "discord_projection_links",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_antidisable_workflows",
        "discord_antidisable_response_bindings",
    }
)


CURRENT_CATALOG_TABLES = CATALOG_TABLES | CATALOG_DURABLE_TABLES


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
            "pokedex_observed",
            "reactions_observed",
            "mudapins_observed",
            "kakera_balance_observed",
            "keys_observed",
            "bronze_keys_observed",
            "silver_keys_observed",
            "gold_keys_observed",
            "sphere_stock_observed",
            "sphere_counts_observed",
            "badges_observed",
        }
    ),
    "sphere_result_observations": frozenset(
        {"id", "account_context_id", "snapshot_json", "total_gained", "stock", "observed_at", "import_event_id"}
    ),
}


CATALOG_CURRENT_REQUIRED_COLUMNS = {
    "import_events": frozenset({"raw_message_expired_at"}),
    "discord_source_events": frozenset({"raw_evidence_expired_at"}),
    "discord_processing_attempts": frozenset({"failure_detail_expired_at"}),
    "projection_generations": frozenset({"id", "is_current"}),
    "discord_projection_links": frozenset({"generation_id"}),
}


def _validate_catalog_schema_tables(
    connection: sqlite3.Connection,
    expected_tables: frozenset[str],
) -> None:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    actual_tables = {row[0] for row in rows if row[0] != "schema_migrations"}
    if actual_tables != expected_tables:
        missing = sorted(expected_tables - actual_tables)
        unexpected = sorted(actual_tables - expected_tables)
        details = []
        if missing:
            details.append(f"missing tables: {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected tables: {', '.join(unexpected)}")
        raise MigrationError(
            "Unrecognized MOA catalog schema (" + "; ".join(details) + ")."
        )

    missing_columns = []
    required_columns = dict(CATALOG_REQUIRED_COLUMNS)
    if expected_tables == CURRENT_CATALOG_TABLES:
        for table, required in CATALOG_CURRENT_REQUIRED_COLUMNS.items():
            required_columns[table] = required_columns.get(table, frozenset()) | required
    for table, required in required_columns.items():
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


def validate_catalog_schema(connection: sqlite3.Connection) -> None:
    """Confirm that a non-empty database has the baseline catalog schema."""
    _validate_catalog_schema_tables(connection, CATALOG_TABLES)


def validate_current_catalog_schema(connection: sqlite3.Connection) -> None:
    """Confirm that a database has the complete current catalog schema."""
    _validate_catalog_schema_tables(connection, CURRENT_CATALOG_TABLES)


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
    while True:
        try:
            connection.execute("BEGIN IMMEDIATE")
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
            if len(applied_versions) == len(definitions):
                connection.commit()
                return

            migration = definitions[len(applied_versions)]
            migration.apply(connection)
            connection.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (migration.version, migration.name.strip(), datetime.now(timezone.utc).isoformat()),
            )
            connection.commit()
        except BaseException:
            try:
                connection.rollback()
            except Exception:
                # Cleanup must not replace the migration or commit exception.
                pass
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


def _apply_tower_completed_towers_presence(connection: sqlite3.Connection) -> None:
    """Preserve exact Tower completed-count presence without rewriting legacy zeros."""
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(tower_state_observations)").fetchall()
    }
    if "completed_towers_observed" not in columns:
        connection.execute(
            """
            ALTER TABLE tower_state_observations
            ADD COLUMN completed_towers_observed INTEGER
                CHECK (completed_towers_observed IN (0, 1))
            """
        )
    connection.execute(
        """
        UPDATE tower_state_observations
        SET completed_towers_observed = 1
        WHERE completed_towers != 0
        """
    )


_KAKERALOOT_STATE_VALUE_FIELDS = (
    "rolls_stacked",
    "disable_wa_ha_reduction",
    "disable_wg_hg_reduction",
    "protected_wish_level",
    "protected_wish_denominator",
    "mudapins",
    "rt_cooldown_reduction_hours",
    "permanent_roll_bonus",
    "star_branches",
    "starwish_slots_from_branches",
    "quantity_level",
    "quality_level",
    "usage_count",
    "kakera_balance",
)


def _apply_kakeraloot_state_value_presence(connection: sqlite3.Connection) -> None:
    """Preserve exact Kakeraloot value presence without rewriting legacy zeros."""
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(kakeraloot_state_observations)").fetchall()
    }
    for field_name in _KAKERALOOT_STATE_VALUE_FIELDS:
        observed_column = f"{field_name}_observed"
        if observed_column not in columns:
            connection.execute(
                f"""
                ALTER TABLE kakeraloot_state_observations
                ADD COLUMN {observed_column} INTEGER
                    CHECK ({observed_column} IN (0, 1))
                """
            )
        connection.execute(
            f"""
            UPDATE kakeraloot_state_observations
            SET {observed_column} = 1
            WHERE {field_name} != 0
            """
        )


_PROFILE_PRESENCE_COLUMNS = (
    "pokedex_observed",
    "reactions_observed",
    "mudapins_observed",
    "kakera_balance_observed",
    "keys_observed",
    "bronze_keys_observed",
    "silver_keys_observed",
    "gold_keys_observed",
    "sphere_stock_observed",
    "sphere_counts_observed",
    "badges_observed",
)


def _load_profile_json(raw: str, expected_type: type, column_name: str):
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise MigrationError(
            f"Cannot migrate invalid profile_observations.{column_name} JSON."
        ) from error
    if not isinstance(value, expected_type):
        raise MigrationError(
            f"Cannot migrate invalid profile_observations.{column_name} shape."
        )
    return value


def _apply_profile_response_presence(connection: sqlite3.Connection) -> None:
    """Preserve Profile response presence without manufacturing historical certainty."""
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(profile_observations)").fetchall()
    }
    for column_name in _PROFILE_PRESENCE_COLUMNS:
        if column_name not in columns:
            connection.execute(
                f"""
                ALTER TABLE profile_observations
                ADD COLUMN {column_name} INTEGER
                    CHECK ({column_name} IN (0, 1))
                """
            )

    selected_columns = (
        "id",
        "pokedex_count",
        "pokedex_json",
        "kakera_reacts_json",
        "mudapins_collected",
        "mudapins_total",
        "kakera_balance",
        "bronze_keys",
        "silver_keys",
        "gold_keys",
        "sphere_stock",
        "spheres_json",
        "displayed_badges_json",
    )
    rows = connection.execute(
        f"SELECT {', '.join(selected_columns)} FROM profile_observations"
    ).fetchall()
    for row in rows:
        values = dict(zip(selected_columns, row, strict=True))
        pokedex_items = _load_profile_json(values["pokedex_json"], list, "pokedex_json")
        reactions = _load_profile_json(
            values["kakera_reacts_json"], dict, "kakera_reacts_json"
        )
        spheres = _load_profile_json(values["spheres_json"], dict, "spheres_json")
        badges = _load_profile_json(
            values["displayed_badges_json"], list, "displayed_badges_json"
        )
        count = values["pokedex_count"]
        safely_observed = {
            "pokedex_observed": count is not None and (bool(pokedex_items) or count == 0),
            "reactions_observed": bool(reactions),
            "mudapins_observed": (
                values["mudapins_collected"] is not None
                and values["mudapins_total"] is not None
            ),
            "kakera_balance_observed": values["kakera_balance"] is not None,
            "keys_observed": any(
                values[field_name] != 0
                for field_name in ("bronze_keys", "silver_keys", "gold_keys")
            ),
            "bronze_keys_observed": values["bronze_keys"] != 0,
            "silver_keys_observed": values["silver_keys"] != 0,
            "gold_keys_observed": values["gold_keys"] != 0,
            "sphere_stock_observed": values["sphere_stock"] is not None,
            "sphere_counts_observed": bool(spheres),
            "badges_observed": bool(badges),
        }
        for column_name, observed in safely_observed.items():
            if observed:
                connection.execute(
                    f"UPDATE profile_observations SET {column_name} = 1 "
                    f"WHERE id = ? AND {column_name} IS NULL",
                    (values["id"],),
                )


def _apply_ranked_harem_roulette_presence(connection: sqlite3.Connection) -> None:
    """Preserve ranked-harem roulette presence without resolving legacy empties."""
    columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(owned_character_observations)"
        ).fetchall()
    }
    if "roulette_types_observed" not in columns:
        connection.execute(
            """
            ALTER TABLE owned_character_observations
            ADD COLUMN roulette_types_observed INTEGER
                CHECK (roulette_types_observed IN (0, 1))
            """
        )

    rows = connection.execute(
        "SELECT id, roulette_types_json FROM owned_character_observations "
        "WHERE roulette_types_observed IS NULL"
    ).fetchall()
    for observation_id, raw_roulette_types in rows:
        try:
            roulette_types = json.loads(raw_roulette_types)
        except (TypeError, json.JSONDecodeError) as error:
            raise MigrationError(
                "Cannot migrate invalid owned_character_observations roulette JSON."
            ) from error
        if not isinstance(roulette_types, list) or any(
            not isinstance(roulette_type, str) for roulette_type in roulette_types
        ):
            raise MigrationError(
                "Cannot migrate invalid owned_character_observations roulette shape."
            )
        if roulette_types:
            connection.execute(
                "UPDATE owned_character_observations "
                "SET roulette_types_observed = 1 WHERE id = ?",
                (observation_id,),
            )


def _apply_disablelist_toggle_presence(connection: sqlite3.Connection) -> None:
    """Preserve independent disablelist toggle presence and legacy ambiguity."""
    columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(disablelist_observations)"
        ).fetchall()
    }
    for field_name in ("western_disabled", "irl_disabled"):
        observed_column = f"{field_name}_observed"
        if observed_column not in columns:
            connection.execute(
                f"""
                ALTER TABLE disablelist_observations
                ADD COLUMN {observed_column} INTEGER
                    CHECK ({observed_column} IN (0, 1))
                """
            )
        connection.execute(
            f"UPDATE disablelist_observations SET {observed_column} = 1 "
            f"WHERE {field_name} = 1 AND {observed_column} IS NULL"
        )


def _apply_raw_evidence_lifecycle_foundation(connection: sqlite3.Connection) -> None:
    """Add lifecycle markers without changing any retained evidence."""
    columns_by_table = {
        table: {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for table in (
            "import_events",
            "discord_source_events",
            "discord_processing_attempts",
        )
    }
    additions = (
        ("import_events", "raw_message_expired_at"),
        ("discord_source_events", "raw_evidence_expired_at"),
        ("discord_processing_attempts", "failure_detail_expired_at"),
    )
    for table, column in additions:
        if column not in columns_by_table[table]:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT NULL")

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_import_events_raw_message_expiry
        ON import_events(raw_message_expired_at, observed_at, id)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_discord_source_events_raw_evidence_expiry
        ON discord_source_events(status, raw_evidence_expired_at, id)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_discord_processing_attempts_source_status_finished
        ON discord_processing_attempts(source_event_id, status, finished_at)
        """
    )


def _apply_projection_generation_foundation(connection: sqlite3.Connection) -> None:
    """Qualify durable projection links with the immutable initial generation."""
    connection.execute(
        """
        CREATE TABLE projection_generations (
            id INTEGER PRIMARY KEY CHECK(id > 0),
            is_current INTEGER NOT NULL CHECK(is_current IN (0, 1))
        )
        """
    )
    connection.execute(
        "INSERT INTO projection_generations (id, is_current) VALUES (1, 1)"
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX uq_projection_generations_current
        ON projection_generations(is_current)
        WHERE is_current = 1
        """
    )
    connection.execute(
        """
        CREATE TRIGGER projection_generations_keep_current_on_update
        BEFORE UPDATE OF is_current ON projection_generations
        WHEN OLD.is_current = 1 AND NEW.is_current != 1
        BEGIN
            SELECT RAISE(ABORT, 'projection generations require exactly one current row');
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER projection_generations_keep_initial_id
        BEFORE UPDATE OF id ON projection_generations
        WHEN OLD.id = 1 AND NEW.id != 1
        BEGIN
            SELECT RAISE(ABORT, 'initial projection generation is immutable');
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER projection_generations_keep_current_on_delete
        BEFORE DELETE ON projection_generations
        WHEN OLD.is_current = 1
        BEGIN
            SELECT RAISE(ABORT, 'projection generations require exactly one current row');
        END
        """
    )

    legacy_count = connection.execute(
        "SELECT COUNT(*) FROM discord_projection_links"
    ).fetchone()[0]
    connection.execute(
        """
        CREATE TABLE discord_projection_links_generation_13 (
            id INTEGER PRIMARY KEY,
            source_event_id INTEGER NOT NULL
                REFERENCES discord_source_events(id)
                ON DELETE RESTRICT
                ON UPDATE RESTRICT,
            generation_id INTEGER NOT NULL DEFAULT 1
                REFERENCES projection_generations(id)
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
            UNIQUE(source_event_id, generation_id, projection_kind, projection_slot),
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
    connection.execute(
        """
        INSERT INTO discord_projection_links_generation_13 (
            id, source_event_id, generation_id, projection_kind, projection_slot,
            projection_table, projection_row_id, state, claimed_at, completed_at,
            created_at, updated_at
        )
        SELECT
            id, source_event_id, 1, projection_kind, projection_slot,
            projection_table, projection_row_id, state, claimed_at, completed_at,
            created_at, updated_at
        FROM discord_projection_links
        """
    )

    copied_count = connection.execute(
        "SELECT COUNT(*) FROM discord_projection_links_generation_13"
    ).fetchone()[0]
    invalid_generation_count = connection.execute(
        """
        SELECT COUNT(*)
        FROM discord_projection_links_generation_13 AS links
        LEFT JOIN projection_generations AS generations
            ON generations.id = links.generation_id
        WHERE links.generation_id != 1 OR generations.id IS NULL
        """
    ).fetchone()[0]
    distinct_key_count = connection.execute(
        """
        SELECT COUNT(*)
        FROM (
            SELECT source_event_id, generation_id, projection_kind, projection_slot
            FROM discord_projection_links_generation_13
            GROUP BY source_event_id, generation_id, projection_kind, projection_slot
        )
        """
    ).fetchone()[0]
    current_generation_count = connection.execute(
        "SELECT COUNT(*) FROM projection_generations WHERE is_current = 1"
    ).fetchone()[0]
    if (
        copied_count != legacy_count
        or invalid_generation_count != 0
        or distinct_key_count != copied_count
        or current_generation_count != 1
    ):
        raise MigrationError("Projection generation migration integrity validation failed.")

    connection.execute("DROP TABLE discord_projection_links")
    connection.execute(
        "ALTER TABLE discord_projection_links_generation_13 RENAME TO discord_projection_links"
    )


def _apply_projection_generation_switchover(connection: sqlite3.Connection) -> None:
    """Permit transactional generation switches while retaining one current row."""
    generation_count = connection.execute(
        "SELECT COUNT(*) FROM projection_generations"
    ).fetchone()[0]
    positive_generation_count = connection.execute(
        "SELECT COUNT(*) FROM projection_generations WHERE id > 0"
    ).fetchone()[0]
    current_rows = connection.execute(
        "SELECT id FROM projection_generations WHERE is_current = 1"
    ).fetchall()
    if (
        generation_count != positive_generation_count
        or len(current_rows) != 1
        or int(current_rows[0][0]) <= 0
    ):
        raise MigrationError(
            "Projection generation switchover requires exactly one positive current generation."
        )

    connection.execute("DROP TRIGGER projection_generations_keep_current_on_update")
    connection.execute("DROP TRIGGER projection_generations_keep_initial_id")
    connection.execute("DROP TRIGGER projection_generations_keep_current_on_delete")
    connection.execute("DROP INDEX uq_projection_generations_current")
    connection.execute(
        """
        CREATE UNIQUE INDEX uq_projection_generations_one_current
        ON projection_generations(is_current)
        WHERE is_current = 1
        """
    )


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
    Migration(
        version=7,
        name="tower-completed-towers-presence",
        apply=_apply_tower_completed_towers_presence,
    ),
    Migration(
        version=8,
        name="kakeraloot-state-value-presence",
        apply=_apply_kakeraloot_state_value_presence,
    ),
    Migration(
        version=9,
        name="profile-response-presence",
        apply=_apply_profile_response_presence,
    ),
    Migration(
        version=10,
        name="ranked-harem-roulette-presence",
        apply=_apply_ranked_harem_roulette_presence,
    ),
    Migration(
        version=11,
        name="disablelist-toggle-presence",
        apply=_apply_disablelist_toggle_presence,
    ),
    Migration(
        version=12,
        name="raw-evidence-lifecycle-foundation",
        apply=_apply_raw_evidence_lifecycle_foundation,
    ),
    Migration(
        version=13,
        name="projection-generation-foundation",
        apply=_apply_projection_generation_foundation,
    ),
    Migration(
        version=14,
        name="projection-generation-switchover",
        apply=_apply_projection_generation_switchover,
    ),
)
