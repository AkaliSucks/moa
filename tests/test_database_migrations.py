import sqlite3
import threading

import pytest

from moa.database.migrations import (
    CATALOG_MIGRATIONS,
    CATALOG_TABLES,
    Migration,
    MigrationError,
    validate_current_catalog_schema,
    run_migrations,
)
from moa.database.sqlite import connect
from moa.models.character import (
    DisableListSnapshot,
    KakeralootStateSnapshot,
    ProfileSnapshot,
    RankedHaremEntry,
    RankedHaremPage,
    TowerStateSnapshot,
)
from moa.repositories.catalog_repository import CatalogRepository


KAKERALOOT_VALUE_FIELDS = (
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

PROFILE_PRESENCE_FIELDS = (
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

MINIMAL_PROFILE = ProfileSnapshot(
    profile_name="Account",
    collection_size=0,
    female_percent=0,
    male_percent=0,
    pokedex_count=None,
    pokedex_pokemon=None,
    kakera_reacts=None,
    mudapins_collected=None,
    mudapins_total=None,
    kakera_balance=None,
    bronze_keys=None,
    silver_keys=None,
    gold_keys=None,
    sphere_stock=None,
    spheres=None,
    displayed_badges=None,
    **{field_name: False for field_name in PROFILE_PRESENCE_FIELDS},
)


TOWER_STATE = TowerStateSnapshot(
    current_level=2,
    completed_towers=3,
    next_level_cost=75_000,
    kakera_balance=7_673,
    built_perk_ids=(2, 7),
)


def _migration_rows(database_path):
    with sqlite3.connect(database_path) as connection:
        return connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()


def _open_database(database_path):
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _restore_generationless_projection_links(connection):
    """Return a current test database to the migration-12 link schema."""
    connection.execute(
        "ALTER TABLE discord_projection_links RENAME TO discord_projection_links_generation_13"
    )
    CATALOG_MIGRATIONS[2].apply(connection)
    connection.execute(
        """
        INSERT INTO discord_projection_links (
            id, source_event_id, projection_kind, projection_slot,
            projection_table, projection_row_id, state, claimed_at, completed_at,
            created_at, updated_at
        )
        SELECT
            id, source_event_id, projection_kind, projection_slot,
            projection_table, projection_row_id, state, claimed_at, completed_at,
            created_at, updated_at
        FROM discord_projection_links_generation_13
        """
    )
    connection.execute("DROP TABLE discord_projection_links_generation_13")
    connection.execute("DROP TABLE projection_generations")
    connection.execute("DELETE FROM schema_migrations WHERE version = 13")


def _make_baseline_database(database_path):
    """Create a version-1 database to exercise the version-2 upgrade path."""
    CatalogRepository(database_path)
    with _open_database(database_path) as connection:
        connection.execute("DROP TABLE discord_antidisable_response_bindings")
        connection.execute("DROP TABLE discord_antidisable_workflows")
        connection.execute("DROP TABLE discord_source_event_server_attributions")
        connection.execute("DROP TABLE discord_source_event_account_attributions")
        connection.execute("DROP TABLE discord_projection_links")
        connection.execute("DROP TABLE projection_generations")
        connection.execute("DROP TABLE discord_processing_attempts")
        connection.execute("DROP TABLE discord_source_events")
        connection.execute("DROP TABLE discord_message_revisions")
        connection.execute("DROP TABLE discord_message_aggregates")
        connection.execute("DELETE FROM schema_migrations WHERE version = 3")
        connection.execute("DELETE FROM schema_migrations WHERE version = 2")
        connection.execute("DELETE FROM schema_migrations WHERE version = 4")
        connection.execute("DELETE FROM schema_migrations WHERE version = 5")
        connection.execute("DELETE FROM schema_migrations WHERE version = 6")
        connection.execute("DELETE FROM schema_migrations WHERE version = 7")
        connection.execute("DELETE FROM schema_migrations WHERE version = 8")
        connection.execute("DELETE FROM schema_migrations WHERE version = 9")
        connection.execute("DELETE FROM schema_migrations WHERE version = 10")
        connection.execute("DELETE FROM schema_migrations WHERE version = 11")
        connection.execute("DELETE FROM schema_migrations WHERE version = 12")
        connection.execute("DELETE FROM schema_migrations WHERE version = 13")


def _make_version_5_database(database_path):
    CatalogRepository(database_path)
    with _open_database(database_path) as connection:
        _restore_generationless_projection_links(connection)
        connection.execute("DROP TABLE discord_antidisable_response_bindings")
        connection.execute("DROP TABLE discord_antidisable_workflows")
        connection.execute("DELETE FROM schema_migrations WHERE version = 6")
        connection.execute("DELETE FROM schema_migrations WHERE version = 7")
        connection.execute("DELETE FROM schema_migrations WHERE version = 8")
        connection.execute("DELETE FROM schema_migrations WHERE version = 9")
        connection.execute("DELETE FROM schema_migrations WHERE version = 10")
        connection.execute("DELETE FROM schema_migrations WHERE version = 11")
        connection.execute("DELETE FROM schema_migrations WHERE version = 12")


def _make_version_6_database(database_path, completed_towers_by_account):
    catalog = CatalogRepository(database_path)
    for account, completed_towers in completed_towers_by_account.items():
        catalog.import_tower_state(
            TOWER_STATE.model_copy(update={"completed_towers": completed_towers}),
            "Server",
            account,
            f"legacy {account}",
            "test",
        )
    with _open_database(database_path) as connection:
        _restore_generationless_projection_links(connection)
        connection.execute(
            "ALTER TABLE tower_state_observations DROP COLUMN completed_towers_observed"
        )
        connection.execute("DELETE FROM schema_migrations WHERE version = 7")
        connection.execute("DELETE FROM schema_migrations WHERE version = 8")
        connection.execute("DELETE FROM schema_migrations WHERE version = 9")
        connection.execute("DELETE FROM schema_migrations WHERE version = 10")
        connection.execute("DELETE FROM schema_migrations WHERE version = 11")
        connection.execute("DELETE FROM schema_migrations WHERE version = 12")


def _make_version_7_kakeraloot_database(database_path, states_by_account):
    catalog = CatalogRepository(database_path)
    for account, state in states_by_account.items():
        catalog.import_kakeraloot_state(
            state,
            "Server",
            account,
            f"legacy {account}",
            "test",
        )
    with _open_database(database_path) as connection:
        _restore_generationless_projection_links(connection)
        for field_name in KAKERALOOT_VALUE_FIELDS:
            connection.execute(
                f"ALTER TABLE kakeraloot_state_observations DROP COLUMN {field_name}_observed"
            )
        connection.execute("DELETE FROM schema_migrations WHERE version = 8")
        connection.execute("DELETE FROM schema_migrations WHERE version = 9")
        connection.execute("DELETE FROM schema_migrations WHERE version = 10")
        connection.execute("DELETE FROM schema_migrations WHERE version = 11")
        connection.execute("DELETE FROM schema_migrations WHERE version = 12")


def _make_version_8_profile_database(database_path, profiles_by_account):
    catalog = CatalogRepository(database_path)
    for account, profile in profiles_by_account.items():
        catalog.import_profile(profile, "Server", account, f"legacy {account}", "test")
    with _open_database(database_path) as connection:
        _restore_generationless_projection_links(connection)
        for field_name in PROFILE_PRESENCE_FIELDS:
            connection.execute(
                f"ALTER TABLE profile_observations DROP COLUMN {field_name}"
            )
        connection.execute("DELETE FROM schema_migrations WHERE version = 9")
        connection.execute("DELETE FROM schema_migrations WHERE version = 10")
        connection.execute("DELETE FROM schema_migrations WHERE version = 11")
        connection.execute("DELETE FROM schema_migrations WHERE version = 12")


def _make_version_9_ranked_harem_database(database_path, roulette_by_account):
    catalog = CatalogRepository(database_path)
    for account, roulette_types in roulette_by_account.items():
        catalog.import_ranked_harem_page(
            RankedHaremPage(
                page_number=None,
                page_count=None,
                entries=(
                    RankedHaremEntry(
                        name=account,
                        claim_rank=1,
                        roulette_types=roulette_types,
                    ),
                ),
            ),
            "Server",
            account,
            f"legacy {account}",
            "test",
        )
    with _open_database(database_path) as connection:
        _restore_generationless_projection_links(connection)
        connection.execute(
            "ALTER TABLE owned_character_observations "
            "DROP COLUMN roulette_types_observed"
        )
        connection.execute("DELETE FROM schema_migrations WHERE version = 10")
        connection.execute("DELETE FROM schema_migrations WHERE version = 11")
        connection.execute("DELETE FROM schema_migrations WHERE version = 12")


def _make_version_10_disablelist_database(database_path, toggles_by_account):
    catalog = CatalogRepository(database_path)
    for account, (western_disabled, irl_disabled) in toggles_by_account.items():
        catalog.import_disablelist(
            DisableListSnapshot(
                slots_used=0,
                slots_capacity=16,
                total_disabled=0,
                disabled_wa=0,
                disabled_ha=0,
                disabled_wg=0,
                disabled_hg=0,
                wa_pool_limit=None,
                ha_pool_limit=None,
                western_disabled=western_disabled,
                irl_disabled=irl_disabled,
                entries=(),
            ),
            "Server",
            account,
            f"legacy {account} with opposite raw anchors: "
            "Western animanga series are completely disabled; "
            "IRL series are completely disabled",
            "test",
        )
    with _open_database(database_path) as connection:
        _restore_generationless_projection_links(connection)
        connection.execute(
            "ALTER TABLE disablelist_observations "
            "DROP COLUMN western_disabled_observed"
        )
        connection.execute(
            "ALTER TABLE disablelist_observations "
            "DROP COLUMN irl_disabled_observed"
        )
        connection.execute("DELETE FROM schema_migrations WHERE version = 11")
        connection.execute("DELETE FROM schema_migrations WHERE version = 12")


def _insert_aggregate(
    connection,
    *,
    guild_id="guild-1",
    channel_id="channel-1",
    message_id="message-1",
):
    return connection.execute(
        """
        INSERT INTO discord_message_aggregates (
            platform, guild_id, channel_id, message_id,
            first_received_at, last_received_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "discord",
            guild_id,
            channel_id,
            message_id,
            "2026-07-18T00:00:00+00:00",
            "2026-07-18T00:00:00+00:00",
            "2026-07-18T00:00:00+00:00",
            "2026-07-18T00:00:00+00:00",
        ),
    ).lastrowid


def _insert_revision(
    connection,
    aggregate_id,
    *,
    source_revision_marker="revision-1",
    normalized_payload_hash="hash-1",
    revision_state="candidate",
):
    return connection.execute(
        """
        INSERT INTO discord_message_revisions (
            aggregate_id, source_revision_marker, normalized_payload_hash,
            revision_state, first_received_at, last_received_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            aggregate_id,
            source_revision_marker,
            normalized_payload_hash,
            revision_state,
            "2026-07-18T00:00:00+00:00",
            "2026-07-18T00:00:00+00:00",
            "2026-07-18T00:00:00+00:00",
            "2026-07-18T00:00:00+00:00",
        ),
    ).lastrowid


def _insert_source_event(
    connection,
    revision_id,
    *,
    event_key="event-1",
    status="received",
    delivery_count=1,
    legacy_import_event_id=None,
):
    return connection.execute(
        """
        INSERT INTO discord_source_events (
            event_key, revision_id, event_kind, status, raw_text,
            source_observed_at, received_at, last_seen_at, delivery_count,
            legacy_import_event_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_key,
            revision_id,
            "message_create",
            status,
            "raw message",
            "2026-07-18T00:00:00+00:00",
            "2026-07-18T00:00:00+00:00",
            "2026-07-18T00:00:00+00:00",
            delivery_count,
            legacy_import_event_id,
            "2026-07-18T00:00:00+00:00",
            "2026-07-18T00:00:00+00:00",
        ),
    ).lastrowid


def _insert_attempt(
    connection,
    source_event_id,
    *,
    attempt_number=1,
    status="processing",
    retryable=1,
    parser_version="parser-1",
    router_version="router-1",
):
    return connection.execute(
        """
        INSERT INTO discord_processing_attempts (
            source_event_id, attempt_number, status, retryable,
            parser_version, router_version, started_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_event_id,
            attempt_number,
            status,
            retryable,
            parser_version,
            router_version,
            "2026-07-18T00:00:00+00:00",
            "2026-07-18T00:00:00+00:00",
        ),
    ).lastrowid


def _insert_account_context(connection, *, suffix="1"):
    server_context_id = connection.execute(
        """
        INSERT INTO server_contexts (
            name, normalized_name, created_at, updated_at
        ) VALUES (?, ?, ?, ?)
        """,
        (f"Server {suffix}", f"server-{suffix}", "now", "now"),
    ).lastrowid
    return connection.execute(
        """
        INSERT INTO account_contexts (
            server_context_id, name, normalized_name, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (server_context_id, f"Account {suffix}", f"account-{suffix}", "now", "now"),
    ).lastrowid


def _insert_harem_scan(connection, account_context_id, *, scan_kind="antidisable"):
    return connection.execute(
        """
        INSERT INTO harem_scans (
            account_context_id, expected_page_count, started_at, scan_kind
        ) VALUES (?, ?, ?, ?)
        """,
        (account_context_id, 2, "2026-07-18T00:00:00+00:00", scan_kind),
    ).lastrowid


def _insert_antidisable_workflow(
    connection,
    harem_scan_id,
    request_message_aggregate_id,
    *,
    requesting_user_id="user-1",
    created_at="2026-07-18T00:00:00+00:00",
    expires_at="2026-07-18T00:05:00+00:00",
):
    connection.execute(
        """
        INSERT INTO discord_antidisable_workflows (
            harem_scan_id, request_message_aggregate_id, requesting_user_id,
            created_at, expires_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            harem_scan_id,
            request_message_aggregate_id,
            requesting_user_id,
            created_at,
            expires_at,
        ),
    )


def _insert_antidisable_response_binding(
    connection,
    harem_scan_id,
    response_message_aggregate_id,
):
    connection.execute(
        """
        INSERT INTO discord_antidisable_response_bindings (
            harem_scan_id, response_message_aggregate_id, bound_at
        ) VALUES (?, ?, ?)
        """,
        (harem_scan_id, response_message_aggregate_id, "2026-07-18T00:01:00+00:00"),
    )


def _insert_projection_link(
    connection,
    source_event_id,
    *,
    generation_id=None,
    projection_kind="roll",
    projection_slot="account:1",
    projection_table=None,
    projection_row_id=None,
    state="claimed",
    claimed_at="2026-07-18T00:00:00+00:00",
    completed_at=None,
    created_at="2026-07-18T00:00:00+00:00",
    updated_at="2026-07-18T00:00:00+00:00",
):
    generation_columns = ""
    generation_placeholder = ""
    generation_values = ()
    if generation_id is not None:
        generation_columns = "generation_id, "
        generation_placeholder = "?, "
        generation_values = (generation_id,)
    return connection.execute(
        f"""
        INSERT INTO discord_projection_links (
            source_event_id, {generation_columns}projection_kind, projection_slot,
            projection_table, projection_row_id, state,
            claimed_at, completed_at, created_at, updated_at
        ) VALUES (?, {generation_placeholder}?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_event_id,
            *generation_values,
            projection_kind,
            projection_slot,
            projection_table,
            projection_row_id,
            state,
            claimed_at,
            completed_at,
            created_at,
            updated_at,
        ),
    ).lastrowid


def test_fresh_catalog_database_records_migrations_and_ingestion_schema(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"

    CatalogRepository(database_path)

    with _open_database(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "characters" in tables
    assert "schema_migrations" in tables
    assert {
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
    } <= tables
    assert _migration_rows(database_path) == [
        (1, "catalog-schema-baseline"),
        (2, "durable-discord-message-ingestion"),
        (3, "durable-discord-projection-links"),
        (4, "durable-discord-source-event-server-attributions"),
        (5, "durable-discord-source-event-account-attributions"),
        (6, "durable-discord-antidisable-workflow-bindings"),
        (7, "tower-completed-towers-presence"),
        (8, "kakeraloot-state-value-presence"),
        (9, "profile-response-presence"),
        (10, "ranked-harem-roulette-presence"),
        (11, "disablelist-toggle-presence"),
        (12, "raw-evidence-lifecycle-foundation"),
        (13, "projection-generation-foundation"),
    ]
    with _open_database(database_path) as connection:
        assert connection.execute(
            "SELECT id, is_current FROM projection_generations"
        ).fetchall() == [(1, 1)]
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
    assert {
        "uq_discord_revision_versioned",
        "uq_discord_revision_unversioned",
        "uq_discord_active_revision",
        "uq_discord_processing_attempt",
        "ix_discord_antidisable_workflows_expires_at",
        "ix_import_events_raw_message_expiry",
        "ix_discord_source_events_raw_evidence_expiry",
        "ix_discord_processing_attempts_source_status_finished",
    } <= indexes

    expected_columns = {
        "import_events": {
            "id",
            "kind",
            "source",
            "observed_at",
            "raw_message",
            "raw_message_expired_at",
        },
        "discord_message_aggregates": {
            "id",
            "platform",
            "guild_id",
            "channel_id",
            "message_id",
            "first_received_at",
            "last_received_at",
            "created_at",
            "updated_at",
        },
        "discord_message_revisions": {
            "id",
            "aggregate_id",
            "source_revision_marker",
            "normalized_payload_hash",
            "revision_state",
            "selection_basis",
            "source_observed_at",
            "first_received_at",
            "last_received_at",
            "created_at",
            "updated_at",
        },
        "discord_source_events": {
            "id",
            "event_key",
            "revision_id",
            "event_kind",
            "status",
            "raw_text",
            "payload_json",
            "payload_capture_version",
            "raw_evidence_expired_at",
            "source_observed_at",
            "received_at",
            "last_seen_at",
            "delivery_count",
            "legacy_import_event_id",
            "created_at",
            "updated_at",
        },
        "discord_processing_attempts": {
            "id",
            "source_event_id",
            "attempt_number",
            "status",
            "retryable",
            "parser_version",
            "router_version",
            "started_at",
            "finished_at",
            "lease_expires_at",
            "failure_code",
            "failure_detail",
            "failure_detail_expired_at",
            "created_at",
        },
        "projection_generations": {
            "id",
            "is_current",
        },
        "discord_projection_links": {
            "id",
            "source_event_id",
            "generation_id",
            "projection_kind",
            "projection_slot",
            "projection_table",
            "projection_row_id",
            "state",
            "claimed_at",
            "completed_at",
            "created_at",
            "updated_at",
        },
        "discord_source_event_server_attributions": {
            "source_event_id",
            "status",
            "server_name",
            "created_at",
            "updated_at",
        },
        "discord_source_event_account_attributions": {
            "source_event_id",
            "status",
            "server_name",
            "account_name",
            "created_at",
            "updated_at",
        },
        "discord_antidisable_workflows": {
            "harem_scan_id",
            "request_message_aggregate_id",
            "requesting_user_id",
            "created_at",
            "expires_at",
        },
        "discord_antidisable_response_bindings": {
            "harem_scan_id",
            "response_message_aggregate_id",
            "bound_at",
        },
    }
    with _open_database(database_path) as connection:
        for table, columns in expected_columns.items():
            actual = {
                row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            assert actual == columns


def test_raw_evidence_lifecycle_upgrade_preserves_existing_text_and_defaults_markers_null(
    tmp_path,
) -> None:
    database_path = tmp_path / "catalog.db"
    _make_version_10_disablelist_database(
        database_path, {"legacy": (True, False)}
    )

    with _open_database(database_path) as connection:
        connection.execute("DROP INDEX ix_import_events_raw_message_expiry")
        connection.execute("DROP INDEX ix_discord_source_events_raw_evidence_expiry")
        connection.execute(
            "DROP INDEX ix_discord_processing_attempts_source_status_finished"
        )
        raw_before = connection.execute(
            "SELECT id, raw_message FROM import_events ORDER BY id"
        ).fetchall()
        connection.execute("ALTER TABLE import_events DROP COLUMN raw_message_expired_at")
        connection.execute(
            "ALTER TABLE discord_source_events DROP COLUMN raw_evidence_expired_at"
        )
        connection.execute(
            "ALTER TABLE discord_processing_attempts DROP COLUMN failure_detail_expired_at"
        )
        run_migrations(connection, CATALOG_MIGRATIONS)
        assert connection.execute(
            "SELECT id, raw_message FROM import_events ORDER BY id"
        ).fetchall() == raw_before
        for table, column in (
            ("import_events", "raw_message_expired_at"),
            ("discord_source_events", "raw_evidence_expired_at"),
            ("discord_processing_attempts", "failure_detail_expired_at"),
        ):
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} IS NOT NULL"
            ).fetchone()[0] == 0


def test_current_schema_validation_requires_baseline_import_event_columns_with_lifecycle(
    tmp_path,
) -> None:
    database_path = tmp_path / "catalog.db"
    CatalogRepository(database_path)

    with _open_database(database_path) as connection:
        connection.execute("ALTER TABLE import_events DROP COLUMN raw_message")

        with pytest.raises(MigrationError, match="import_events: raw_message"):
            validate_current_catalog_schema(connection)


def test_failed_legacy_schema_script_rolls_back_and_same_database_retry_succeeds(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "catalog.db"

    class FaultingScriptConnection:
        def __init__(self, connection):
            self._connection = connection

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return self._connection.__exit__(exc_type, exc_value, traceback)

        def executescript(self, script):
            next_table = "CREATE TABLE IF NOT EXISTS import_events"
            faulting_script = script.replace(
                next_table,
                "SELECT * FROM injected_missing_bootstrap_table;\n" + next_table,
                1,
            )
            return self._connection.executescript(faulting_script)

    def faulting_connection(repository):
        return FaultingScriptConnection(connect(repository._database_path))

    with monkeypatch.context() as fault:
        fault.setattr(CatalogRepository, "_connection", faulting_connection)
        with pytest.raises(
            sqlite3.OperationalError,
            match="no such table: injected_missing_bootstrap_table",
        ):
            CatalogRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall() == []

    CatalogRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert CATALOG_TABLES <= tables
        assert {
            "discord_message_aggregates",
            "discord_message_revisions",
            "discord_source_events",
            "discord_processing_attempts",
            "discord_projection_links",
            "discord_source_event_server_attributions",
            "discord_source_event_account_attributions",
            "discord_antidisable_workflows",
            "discord_antidisable_response_bindings",
        } <= tables
        assert "schema_migrations" in tables
        assert not any(name.endswith("_legacy") for name in tables)
    assert _migration_rows(database_path) == [
        (1, "catalog-schema-baseline"),
        (2, "durable-discord-message-ingestion"),
        (3, "durable-discord-projection-links"),
        (4, "durable-discord-source-event-server-attributions"),
        (5, "durable-discord-source-event-account-attributions"),
        (6, "durable-discord-antidisable-workflow-bindings"),
        (7, "tower-completed-towers-presence"),
        (8, "kakeraloot-state-value-presence"),
        (9, "profile-response-presence"),
        (10, "ranked-harem-roulette-presence"),
        (11, "disablelist-toggle-presence"),
        (12, "raw-evidence-lifecycle-foundation"),
        (13, "projection-generation-foundation"),
    ]


def test_competing_legacy_schema_bootstraps_serialize_their_mutation_boundary(
    tmp_path,
) -> None:
    database_path = tmp_path / "catalog.db"
    with connect(database_path):
        pass

    owner_ready = threading.Event()
    competitor_ready = threading.Event()
    start_owner = threading.Event()
    start_competitor = threading.Event()
    owner_holding_transaction = threading.Event()
    competitor_attempted_script = threading.Event()
    release_owner = threading.Event()
    competitor_finished = threading.Event()
    failures = []

    class ObservedScriptConnection:
        def __init__(self, connection, role):
            self._connection = connection
            self._role = role

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return self._connection.__exit__(exc_type, exc_value, traceback)

        def executescript(self, script):
            if self._role == "owner":
                result = self._connection.executescript(script)
                assert self._connection.in_transaction is True
                owner_holding_transaction.set()
                assert release_owner.wait(5), "schema transaction was not released"
                return result
            competitor_attempted_script.set()
            return self._connection.executescript(script)

    def run_bootstrap(role, ready, start):
        connection = connect(database_path)
        repository = object.__new__(CatalogRepository)
        repository._database_path = database_path
        repository._connection = lambda: ObservedScriptConnection(connection, role)
        ready.set()
        try:
            assert start.wait(5), f"{role} bootstrap was not started"
            repository._create_schema()
            if role == "competitor":
                competitor_finished.set()
        except BaseException as error:
            failures.append(error)
        finally:
            connection.close()

    owner_thread = threading.Thread(
        target=run_bootstrap,
        args=("owner", owner_ready, start_owner),
    )
    competitor_thread = threading.Thread(
        target=run_bootstrap,
        args=("competitor", competitor_ready, start_competitor),
    )
    owner_thread.start()
    competitor_thread.start()
    assert owner_ready.wait(5), "owner connection was not ready"
    assert competitor_ready.wait(5), "competitor connection was not ready"

    start_owner.set()
    assert owner_holding_transaction.wait(5), "owner did not acquire schema transaction"
    start_competitor.set()
    assert competitor_attempted_script.wait(5), "competitor did not attempt schema script"
    assert not competitor_finished.is_set()

    release_owner.set()
    owner_thread.join(timeout=5)
    competitor_thread.join(timeout=5)
    assert not owner_thread.is_alive(), "owner bootstrap did not terminate"
    assert not competitor_thread.is_alive(), "competitor bootstrap did not terminate"
    assert failures == []
    assert competitor_finished.is_set()

    CatalogRepository(database_path)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert CATALOG_TABLES <= tables
        assert not any(name.endswith("_legacy") for name in tables)
    assert [row[0] for row in _migration_rows(database_path)] == list(range(1, 14))


def test_antidisable_workflow_schema_has_required_keys_and_nullability(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    CatalogRepository(database_path)

    with _open_database(database_path) as connection:
        workflow_columns = {
            row[1]: row for row in connection.execute(
                "PRAGMA table_info(discord_antidisable_workflows)"
            ).fetchall()
        }
        response_columns = {
            row[1]: row for row in connection.execute(
                "PRAGMA table_info(discord_antidisable_response_bindings)"
            ).fetchall()
        }
        assert workflow_columns["harem_scan_id"][5] == 1
        assert {
            name: workflow_columns[name][3]
            for name in (
                "request_message_aggregate_id",
                "requesting_user_id",
                "created_at",
                "expires_at",
            )
        } == {
            "request_message_aggregate_id": 1,
            "requesting_user_id": 1,
            "created_at": 1,
            "expires_at": 1,
        }
        assert {
            name: response_columns[name][5]
            for name in ("harem_scan_id", "response_message_aggregate_id")
        } == {"harem_scan_id": 1, "response_message_aggregate_id": 2}
        assert all(row[3] == 1 for row in response_columns.values())

        workflow_foreign_keys = {
            (row[2], row[3], row[4], row[5], row[6])
            for row in connection.execute(
                "PRAGMA foreign_key_list(discord_antidisable_workflows)"
            ).fetchall()
        }
        assert workflow_foreign_keys == {
            (
                "harem_scans",
                "harem_scan_id",
                "id",
                "RESTRICT",
                "RESTRICT",
            ),
            (
                "discord_message_aggregates",
                "request_message_aggregate_id",
                "id",
                "RESTRICT",
                "RESTRICT",
            ),
        }
        response_foreign_keys = {
            (row[2], row[3], row[4], row[5], row[6])
            for row in connection.execute(
                "PRAGMA foreign_key_list(discord_antidisable_response_bindings)"
            ).fetchall()
        }
        assert response_foreign_keys == {
            (
                "discord_antidisable_workflows",
                "harem_scan_id",
                "harem_scan_id",
                "RESTRICT",
                "RESTRICT",
            ),
            (
                "discord_message_aggregates",
                "response_message_aggregate_id",
                "id",
                "RESTRICT",
                "RESTRICT",
            ),
        }
        assert connection.execute(
            "PRAGMA index_info(ix_discord_antidisable_workflows_expires_at)"
        ).fetchall() == [
            (0, 4, "expires_at"),
            (1, 0, "harem_scan_id"),
        ]


def test_upgrade_from_version_5_preserves_catalog_and_discord_rows(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    _make_version_5_database(database_path)

    with _open_database(database_path) as connection:
        connection.execute(
            "INSERT INTO characters "
            "(name, series, normalized_name, normalized_series, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("Asuna", "Sword Art Online", "asuna", "sword art online", "now", "now"),
        )
        aggregate_id = _insert_aggregate(connection)
        revision_id = _insert_revision(connection, aggregate_id)
        source_event_id = _insert_source_event(connection, revision_id)
        account_context_id = _insert_account_context(connection)
        scan_id = _insert_harem_scan(connection, account_context_id)
        before = {
            "characters": connection.execute(
                "SELECT id, name, series FROM characters"
            ).fetchall(),
            "aggregates": connection.execute(
                "SELECT id, guild_id, channel_id, message_id "
                "FROM discord_message_aggregates"
            ).fetchall(),
            "source_events": connection.execute(
                "SELECT id, event_key, revision_id FROM discord_source_events"
            ).fetchall(),
            "scans": connection.execute(
                "SELECT id, account_context_id, scan_kind FROM harem_scans"
            ).fetchall(),
        }
        assert source_event_id == before["source_events"][0][0]
        assert scan_id == before["scans"][0][0]
        assert _migration_rows(database_path)[-1] == (
            5,
            "durable-discord-source-event-account-attributions",
        )
        connection.commit()

        run_migrations(connection, CATALOG_MIGRATIONS)

        assert connection.execute(
            "SELECT id, name, series FROM characters"
        ).fetchall() == before["characters"]
        assert connection.execute(
            "SELECT id, guild_id, channel_id, message_id "
            "FROM discord_message_aggregates"
        ).fetchall() == before["aggregates"]
        assert connection.execute(
            "SELECT id, event_key, revision_id FROM discord_source_events"
        ).fetchall() == before["source_events"]
        assert connection.execute(
            "SELECT id, account_context_id, scan_kind FROM harem_scans"
        ).fetchall() == before["scans"]
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_workflows"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_response_bindings"
        ).fetchone()[0] == 0
        assert _migration_rows(database_path)[-1] == (
            13,
            "projection-generation-foundation",
        )


def test_failed_antidisable_workflow_migration_rolls_back_schema_and_metadata(
    tmp_path,
) -> None:
    database_path = tmp_path / "catalog.db"
    _make_version_5_database(database_path)

    with _open_database(database_path) as connection:

        def fail_after_schema(migration_connection):
            CATALOG_MIGRATIONS[5].apply(migration_connection)
            raise RuntimeError("migration 6 failed")

        with pytest.raises(RuntimeError, match="migration 6 failed"):
            run_migrations(
                connection,
                CATALOG_MIGRATIONS[:5]
                + (Migration(6, "failing-antidisable-bindings", fail_after_schema),),
            )
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,), (5,)]
        assert connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE name IN (
                'discord_antidisable_workflows',
                'discord_antidisable_response_bindings',
                'ix_discord_antidisable_workflows_expires_at'
            )
            """
        ).fetchall() == []


def test_antidisable_workflow_and_response_foreign_keys_require_parents(
    tmp_path,
) -> None:
    database_path = tmp_path / "catalog.db"
    CatalogRepository(database_path)

    with _open_database(database_path) as connection:
        account_context_id = _insert_account_context(connection)
        scan_id = _insert_harem_scan(connection, account_context_id)
        request_aggregate_id = _insert_aggregate(connection)
        response_aggregate_id = _insert_aggregate(
            connection, message_id="response-message-1"
        )

        with pytest.raises(sqlite3.IntegrityError):
            _insert_antidisable_workflow(connection, 999, request_aggregate_id)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_antidisable_workflow(connection, scan_id, 999)

        _insert_antidisable_workflow(connection, scan_id, request_aggregate_id)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_antidisable_response_binding(connection, 999, response_aggregate_id)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_antidisable_response_binding(connection, scan_id, 999)
        _insert_antidisable_response_binding(
            connection, scan_id, response_aggregate_id
        )


def test_antidisable_bindings_enforce_uniqueness_and_allow_concurrent_shape(
    tmp_path,
) -> None:
    database_path = tmp_path / "catalog.db"
    CatalogRepository(database_path)

    with _open_database(database_path) as connection:
        account_context_id = _insert_account_context(connection)
        first_scan_id = _insert_harem_scan(connection, account_context_id)
        second_scan_id = _insert_harem_scan(connection, account_context_id)
        third_scan_id = _insert_harem_scan(connection, account_context_id)
        request_ids = [
            _insert_aggregate(connection, message_id=f"request-message-{number}")
            for number in range(1, 4)
        ]
        response_ids = [
            _insert_aggregate(connection, message_id=f"response-message-{number}")
            for number in range(1, 3)
        ]

        _insert_antidisable_workflow(
            connection,
            first_scan_id,
            request_ids[0],
            requesting_user_id="same-user",
        )
        _insert_antidisable_workflow(
            connection,
            second_scan_id,
            request_ids[1],
            requesting_user_id="same-user",
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_antidisable_workflow(connection, first_scan_id, request_ids[2])
        with pytest.raises(sqlite3.IntegrityError):
            _insert_antidisable_workflow(connection, third_scan_id, request_ids[0])

        _insert_antidisable_response_binding(connection, first_scan_id, response_ids[0])
        _insert_antidisable_response_binding(connection, first_scan_id, response_ids[1])
        with pytest.raises(sqlite3.IntegrityError):
            _insert_antidisable_response_binding(connection, first_scan_id, response_ids[0])
        with pytest.raises(sqlite3.IntegrityError):
            _insert_antidisable_response_binding(connection, second_scan_id, response_ids[0])

        assert connection.execute(
            "SELECT harem_scan_id, requesting_user_id "
            "FROM discord_antidisable_workflows ORDER BY harem_scan_id"
        ).fetchall() == [
            (first_scan_id, "same-user"),
            (second_scan_id, "same-user"),
        ]
        assert connection.execute(
            "SELECT response_message_aggregate_id "
            "FROM discord_antidisable_response_bindings "
            "WHERE harem_scan_id = ? ORDER BY response_message_aggregate_id",
            (first_scan_id,),
        ).fetchall() == [(response_ids[0],), (response_ids[1],)]
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_response_bindings "
            "WHERE harem_scan_id = ?",
            (second_scan_id,),
        ).fetchone()[0] == 0


def test_antidisable_workflow_rejects_blank_user_and_nonfuture_expiry(
    tmp_path,
) -> None:
    database_path = tmp_path / "catalog.db"
    CatalogRepository(database_path)

    with _open_database(database_path) as connection:
        account_context_id = _insert_account_context(connection)
        scan_id = _insert_harem_scan(connection, account_context_id)
        request_aggregate_id = _insert_aggregate(connection)

        for blank_user_id in ("", "   "):
            with pytest.raises(sqlite3.IntegrityError):
                _insert_antidisable_workflow(
                    connection,
                    scan_id,
                    request_aggregate_id,
                    requesting_user_id=blank_user_id,
                )
        for invalid_expiry in (
            "2026-07-18T00:00:00+00:00",
            "2026-07-17T23:59:59+00:00",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                _insert_antidisable_workflow(
                    connection,
                    scan_id,
                    request_aggregate_id,
                    expires_at=invalid_expiry,
                )

        _insert_antidisable_workflow(
            connection,
            scan_id,
            request_aggregate_id,
            expires_at="2026-07-18T00:00:01+00:00",
        )


def test_upgrade_from_baseline_preserves_catalog_data_and_records_version_once(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    _make_baseline_database(database_path)
    with _open_database(database_path) as connection:
        connection.execute(
            "INSERT INTO characters "
            "(name, series, normalized_name, normalized_series, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("Asuna", "Sword Art Online", "asuna", "sword art online", "now", "now"),
        )
        assert _migration_rows(database_path) == [(1, "catalog-schema-baseline")]

    CatalogRepository(database_path)
    CatalogRepository(database_path)

    with _open_database(database_path) as connection:
        assert connection.execute(
            "SELECT name, series FROM characters"
        ).fetchall() == [("Asuna", "Sword Art Online")]
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 2"
        ).fetchone()[0] == 1
    assert _migration_rows(database_path) == [
        (1, "catalog-schema-baseline"),
        (2, "durable-discord-message-ingestion"),
        (3, "durable-discord-projection-links"),
        (4, "durable-discord-source-event-server-attributions"),
        (5, "durable-discord-source-event-account-attributions"),
        (6, "durable-discord-antidisable-workflow-bindings"),
        (7, "tower-completed-towers-presence"),
        (8, "kakeraloot-state-value-presence"),
        (9, "profile-response-presence"),
        (10, "ranked-harem-roulette-presence"),
        (11, "disablelist-toggle-presence"),
        (12, "raw-evidence-lifecycle-foundation"),
        (13, "projection-generation-foundation"),
    ]


def test_upgrade_from_version_2_preserves_discord_and_catalog_data(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    _make_baseline_database(database_path)

    with _open_database(database_path) as connection:
        run_migrations(connection, CATALOG_MIGRATIONS[:2])
        connection.execute(
            "INSERT INTO characters "
            "(name, series, normalized_name, normalized_series, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("Asuna", "Sword Art Online", "asuna", "sword art online", "now", "now"),
        )
        import_event_id = connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) "
            "VALUES (?, ?, ?, ?)",
            ("roll", "discord", "now", "legacy roll"),
        ).lastrowid
        aggregate_id = _insert_aggregate(connection)
        revision_id = _insert_revision(connection, aggregate_id)
        source_event_id = _insert_source_event(
            connection, revision_id, legacy_import_event_id=import_event_id
        )
        catalog_rows = connection.execute(
            "SELECT name, series FROM characters"
        ).fetchall()
        import_rows = connection.execute(
            "SELECT kind, source, raw_message FROM import_events"
        ).fetchall()
        source_event_rows = connection.execute(
            "SELECT id, event_key, revision_id, legacy_import_event_id "
            "FROM discord_source_events"
        ).fetchall()
        assert source_event_id == source_event_rows[0][0]
        assert _migration_rows(database_path) == [
            (1, "catalog-schema-baseline"),
            (2, "durable-discord-message-ingestion"),
        ]

        connection.commit()
        run_migrations(connection, CATALOG_MIGRATIONS)

        assert connection.execute(
            "SELECT name, series FROM characters"
        ).fetchall() == catalog_rows
        assert connection.execute(
            "SELECT kind, source, raw_message FROM import_events"
        ).fetchall() == import_rows
        assert connection.execute(
            "SELECT id, event_key, revision_id, legacy_import_event_id "
            "FROM discord_source_events"
        ).fetchall() == source_event_rows
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 3"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 4"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 5"
        ).fetchone()[0] == 1


def _current_database_with_source_event(database_path):
    CatalogRepository(database_path)
    with _open_database(database_path) as connection:
        aggregate_id = _insert_aggregate(connection)
        revision_id = _insert_revision(connection, aggregate_id)
        return _insert_source_event(connection, revision_id)


def test_upgrade_from_version_3_preserves_discord_source_event_rows(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    CatalogRepository(database_path)
    with _open_database(database_path) as connection:
        _restore_generationless_projection_links(connection)
        connection.execute("DROP TABLE discord_antidisable_response_bindings")
        connection.execute("DROP TABLE discord_antidisable_workflows")
        connection.execute("DROP TABLE discord_source_event_server_attributions")
        connection.execute("DROP TABLE discord_source_event_account_attributions")
        connection.execute("DELETE FROM schema_migrations WHERE version = 4")
        connection.execute("DELETE FROM schema_migrations WHERE version = 5")
        connection.execute("DELETE FROM schema_migrations WHERE version = 6")
        connection.execute("DELETE FROM schema_migrations WHERE version = 7")
        connection.execute("DELETE FROM schema_migrations WHERE version = 8")
        connection.execute("DELETE FROM schema_migrations WHERE version = 9")
        connection.execute("DELETE FROM schema_migrations WHERE version = 10")
        connection.execute("DELETE FROM schema_migrations WHERE version = 11")
        connection.execute("DELETE FROM schema_migrations WHERE version = 12")
        source_event_id = _insert_source_event(
            connection,
            _insert_revision(connection, _insert_aggregate(connection)),
        )
        before = connection.execute(
            "SELECT id, event_key, revision_id, status FROM discord_source_events"
        ).fetchall()
        connection.commit()
        assert _migration_rows(database_path) == [
            (1, "catalog-schema-baseline"),
            (2, "durable-discord-message-ingestion"),
            (3, "durable-discord-projection-links"),
        ]

    CatalogRepository(database_path)

    with _open_database(database_path) as connection:
        assert connection.execute(
            "SELECT id, event_key, revision_id, status FROM discord_source_events"
        ).fetchall() == before
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'discord_source_event_server_attributions'"
        ).fetchone()[0] == "discord_source_event_server_attributions"
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 4"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 5"
        ).fetchone()[0] == 1
    assert source_event_id == before[0][0]


def test_projection_links_are_idempotent_across_restart(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    source_event_id = _current_database_with_source_event(database_path)
    with _open_database(database_path) as connection:
        _insert_projection_link(connection, source_event_id)

    CatalogRepository(database_path)
    CatalogRepository(database_path)

    with _open_database(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_projection_links"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 3"
        ).fetchone()[0] == 1


def test_server_attribution_schema_enforces_identity_and_status_consistency(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    source_event_id = _current_database_with_source_event(database_path)

    with _open_database(database_path) as connection:
        connection.execute(
            """
            INSERT INTO discord_source_event_server_attributions (
                source_event_id, status, server_name, created_at, updated_at
            ) VALUES (?, 'resolved', ?, ?, ?)
            """,
            (source_event_id, "Server A", "now", "now"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO discord_source_event_server_attributions (
                    source_event_id, status, server_name, created_at, updated_at
                ) VALUES (?, 'resolved', ?, ?, ?)
                """,
                (source_event_id, "Server B", "now", "now"),
            )
        connection.execute(
            "DELETE FROM discord_source_event_server_attributions WHERE source_event_id = ?",
            (source_event_id,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO discord_source_event_server_attributions (
                    source_event_id, status, server_name, created_at, updated_at
                ) VALUES (?, 'unresolved', ?, ?, ?)
                """,
                (source_event_id, "Server A", "now", "now"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO discord_source_event_server_attributions (
                    source_event_id, status, server_name, created_at, updated_at
                ) VALUES (?, 'resolved', ?, ?, ?)
                """,
                (source_event_id, "", "now", "now"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO discord_source_event_server_attributions (
                    source_event_id, status, server_name, created_at, updated_at
                ) VALUES (999, 'unresolved', NULL, ?, ?)
                """,
                ("now", "now"),
            )
        connection.execute(
            """
            INSERT INTO discord_source_event_server_attributions (
                source_event_id, status, server_name, created_at, updated_at
            ) VALUES (?, 'resolved', ?, ?, ?)
            """,
            (source_event_id, "Server A", "now", "now"),
        )
        connection.execute(
            "DELETE FROM discord_source_events WHERE id = ?", (source_event_id,)
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_source_event_server_attributions"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    "status, server_name, account_name",
    [
        ("resolved", "Server A", "Account A"),
        ("unresolved", None, None),
        ("ambiguous", None, None),
    ],
)
def test_account_attribution_schema_accepts_valid_status_identity_combinations(
    tmp_path, status: str, server_name: str | None, account_name: str | None
) -> None:
    database_path = tmp_path / "catalog.db"
    source_event_id = _current_database_with_source_event(database_path)

    with _open_database(database_path) as connection:
        connection.execute(
            """
            INSERT INTO discord_source_event_account_attributions (
                source_event_id, status, server_name, account_name, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                source_event_id,
                status,
                server_name,
                account_name,
                "now",
                "now",
            ),
        )
        assert connection.execute(
            "SELECT status, server_name, account_name "
            "FROM discord_source_event_account_attributions"
        ).fetchone() == (status, server_name, account_name)


@pytest.mark.parametrize(
    "status, server_name, account_name",
    [
        ("resolved", None, "Account A"),
        ("resolved", "Server A", None),
        ("resolved", "   ", "Account A"),
        ("resolved", "Server A", "   "),
        ("unresolved", "Server A", None),
        ("unresolved", None, "Account A"),
        ("ambiguous", "Server A", None),
        ("ambiguous", None, "Account A"),
    ],
)
def test_account_attribution_schema_rejects_invalid_status_identity_combinations(
    tmp_path, status: str, server_name: str | None, account_name: str | None
) -> None:
    database_path = tmp_path / "catalog.db"
    source_event_id = _current_database_with_source_event(database_path)

    with _open_database(database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO discord_source_event_account_attributions (
                    source_event_id, status, server_name, account_name, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    source_event_id,
                    status,
                    server_name,
                    account_name,
                    "now",
                    "now",
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO discord_source_event_account_attributions (
                    source_event_id, status, server_name, account_name, created_at, updated_at
                ) VALUES (999, 'unresolved', NULL, NULL, 'now', 'now')
                """
            )
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_source_event_account_attributions"
        ).fetchone()[0] == 0


def test_account_attribution_schema_cascades_source_event_deletion(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    source_event_id = _current_database_with_source_event(database_path)

    with _open_database(database_path) as connection:
        connection.execute(
            """
            INSERT INTO discord_source_event_account_attributions (
                source_event_id, status, server_name, account_name, created_at, updated_at
            ) VALUES (?, 'resolved', 'Server A', 'Account A', 'now', 'now')
            """,
            (source_event_id,),
        )
        connection.execute("DELETE FROM discord_source_events WHERE id = ?", (source_event_id,))
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_source_event_account_attributions"
        ).fetchone()[0] == 0


def test_failed_account_attribution_migration_rolls_back_schema_and_metadata(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    _make_baseline_database(database_path)

    with _open_database(database_path) as connection:
        run_migrations(connection, CATALOG_MIGRATIONS[:4])

        def fail_after_schema(migration_connection):
            CATALOG_MIGRATIONS[4].apply(migration_connection)
            raise RuntimeError("migration 5 failed")

        with pytest.raises(RuntimeError, match="migration 5 failed"):
            run_migrations(
                connection,
                CATALOG_MIGRATIONS[:4]
                + (Migration(5, "failing-account-attribution", fail_after_schema),),
            )
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,)]
        assert connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = "
            "'discord_source_event_account_attributions'"
        ).fetchone() is None


def test_failed_server_attribution_migration_rolls_back_schema_and_metadata(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    _make_baseline_database(database_path)

    with _open_database(database_path) as connection:
        run_migrations(connection, CATALOG_MIGRATIONS[:3])

        def fail_after_schema(migration_connection):
            CATALOG_MIGRATIONS[3].apply(migration_connection)
            raise RuntimeError("migration 4 failed")

        with pytest.raises(RuntimeError, match="migration 4 failed"):
            run_migrations(
                connection,
                CATALOG_MIGRATIONS[:3]
                + (Migration(4, "failing-server-attribution", fail_after_schema),),
            )
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,)]
        assert connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = "
            "'discord_source_event_server_attributions'"
        ).fetchone() is None


def test_projection_link_source_event_foreign_key_requires_parent(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    source_event_id = _current_database_with_source_event(database_path)

    with _open_database(database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_projection_link(connection, 999)
        _insert_projection_link(connection, source_event_id)


def test_projection_link_identity_is_unique_per_source_event(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    first_source_event_id = _current_database_with_source_event(database_path)

    with _open_database(database_path) as connection:
        second_aggregate_id = _insert_aggregate(connection, guild_id="guild-2")
        second_revision_id = _insert_revision(
            connection,
            second_aggregate_id,
            source_revision_marker="revision-2",
            normalized_payload_hash="hash-2",
        )
        second_source_event_id = _insert_source_event(
            connection, second_revision_id, event_key="event-2"
        )
        _insert_projection_link(connection, first_source_event_id)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_projection_link(connection, first_source_event_id)
        _insert_projection_link(connection, second_source_event_id)
        _insert_projection_link(
            connection, first_source_event_id, projection_slot="account:2"
        )
        _insert_projection_link(
            connection, first_source_event_id, projection_kind="profile"
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_projection_links"
        ).fetchone()[0] == 4


def test_projection_generation_upgrade_backfills_and_preserves_source_and_link_rows(
    tmp_path,
) -> None:
    database_path = tmp_path / "catalog.db"
    _make_baseline_database(database_path)

    with _open_database(database_path) as connection:
        run_migrations(connection, CATALOG_MIGRATIONS[:12])
        aggregate_id = _insert_aggregate(connection)
        revision_id = _insert_revision(connection, aggregate_id)
        source_event_id = _insert_source_event(connection, revision_id)
        link_id = _insert_projection_link(
            connection,
            source_event_id,
            state="completed",
            projection_table="roll_observations",
            projection_row_id=42,
            completed_at="2026-07-18T00:01:00+00:00",
        )
        source_before = connection.execute(
            "SELECT * FROM discord_source_events WHERE id = ?", (source_event_id,)
        ).fetchone()
        link_before = connection.execute(
            "SELECT * FROM discord_projection_links WHERE id = ?", (link_id,)
        ).fetchone()
        connection.commit()

        run_migrations(connection, CATALOG_MIGRATIONS)

        assert connection.execute(
            "SELECT * FROM discord_source_events WHERE id = ?", (source_event_id,)
        ).fetchone() == source_before
        link_after = connection.execute(
            """
            SELECT id, source_event_id, projection_kind, projection_slot,
                   projection_table, projection_row_id, state, claimed_at,
                   completed_at, created_at, updated_at
            FROM discord_projection_links
            WHERE id = ?
            """,
            (link_id,),
        ).fetchone()
        assert link_after == link_before
        assert connection.execute(
            "SELECT generation_id FROM discord_projection_links WHERE id = ?",
            (link_id,),
        ).fetchone() == (1,)
        assert _migration_rows(database_path)[-1] == (
            13,
            "projection-generation-foundation",
        )


def test_projection_link_identity_is_generation_qualified(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    source_event_id = _current_database_with_source_event(database_path)

    with _open_database(database_path) as connection:
        _insert_projection_link(connection, source_event_id)
        connection.execute(
            "INSERT INTO projection_generations (id, is_current) VALUES (2, 0)"
        )
        _insert_projection_link(connection, source_event_id, generation_id=2)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_projection_link(connection, source_event_id, generation_id=2)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_projection_link(
                connection,
                source_event_id,
                generation_id=999,
                projection_slot="missing-generation",
            )
        assert connection.execute(
            "SELECT generation_id FROM discord_projection_links ORDER BY generation_id"
        ).fetchall() == [(1,), (2,)]


def test_projection_generations_require_positive_ids_and_exactly_one_current(
    tmp_path,
) -> None:
    database_path = tmp_path / "catalog.db"
    CatalogRepository(database_path)

    with _open_database(database_path) as connection:
        for generation_id in (0, -1):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO projection_generations (id, is_current) "
                    "VALUES (?, 0)",
                    (generation_id,),
                )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO projection_generations (id, is_current) VALUES (2, 1)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE projection_generations SET is_current = 0 WHERE id = 1"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE projection_generations SET id = 2 WHERE id = 1"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM projection_generations WHERE id = 1"
            )
        assert connection.execute(
            "SELECT id, is_current FROM projection_generations"
        ).fetchall() == [(1, 1)]


def test_failed_projection_generation_migration_rolls_back_schema_data_and_metadata(
    tmp_path,
) -> None:
    database_path = tmp_path / "catalog.db"
    _make_baseline_database(database_path)

    with _open_database(database_path) as connection:
        run_migrations(connection, CATALOG_MIGRATIONS[:12])
        source_event_id = _insert_source_event(
            connection,
            _insert_revision(connection, _insert_aggregate(connection)),
        )
        link_id = _insert_projection_link(connection, source_event_id)
        connection.commit()

        def fail_after_schema(migration_connection):
            CATALOG_MIGRATIONS[12].apply(migration_connection)
            raise RuntimeError("migration 13 failed")

        with pytest.raises(RuntimeError, match="migration 13 failed"):
            run_migrations(
                connection,
                CATALOG_MIGRATIONS[:12]
                + (Migration(13, "failing-projection-generation", fail_after_schema),),
            )

        assert connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'projection_generations'"
        ).fetchone() is None
        assert {
            row[1] for row in connection.execute("PRAGMA table_info(discord_projection_links)")
        } == {
            "id",
            "source_event_id",
            "projection_kind",
            "projection_slot",
            "projection_table",
            "projection_row_id",
            "state",
            "claimed_at",
            "completed_at",
            "created_at",
            "updated_at",
        }
        assert connection.execute(
            "SELECT id, source_event_id FROM discord_projection_links"
        ).fetchall() == [(link_id, source_event_id)]
        assert connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone() == (12,)

        run_migrations(connection, CATALOG_MIGRATIONS)
        assert connection.execute(
            "SELECT id, source_event_id, generation_id FROM discord_projection_links"
        ).fetchall() == [(link_id, source_event_id, 1)]


@pytest.mark.parametrize(
    "overrides",
    [
        {"projection_kind": ""},
        {"projection_kind": "   "},
        {"projection_slot": " "},
        {"projection_table": " "},
    ],
)
def test_projection_link_rejects_blank_semantic_identity_and_target(
    tmp_path, overrides
) -> None:
    database_path = tmp_path / "catalog.db"
    source_event_id = _current_database_with_source_event(database_path)

    with _open_database(database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_projection_link(connection, source_event_id, **overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"projection_table": "roll_observations"},
        {"projection_row_id": 1},
        {"projection_table": "roll_observations", "projection_row_id": 0},
        {"projection_table": "roll_observations", "projection_row_id": -1},
    ],
)
def test_projection_link_rejects_invalid_projection_targets(tmp_path, overrides) -> None:
    database_path = tmp_path / "catalog.db"
    source_event_id = _current_database_with_source_event(database_path)

    with _open_database(database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_projection_link(connection, source_event_id, **overrides)


def test_projection_link_claimed_state_requires_no_completion(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    source_event_id = _current_database_with_source_event(database_path)

    with _open_database(database_path) as connection:
        _insert_projection_link(connection, source_event_id)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_projection_link(
                connection,
                source_event_id,
                projection_slot="account:2",
                completed_at="2026-07-18T00:01:00+00:00",
            )


@pytest.mark.parametrize(
    "overrides",
    [
        {"projection_table": None},
        {"projection_row_id": None},
        {"completed_at": None},
    ],
)
def test_projection_link_completed_state_requires_target_and_completion(
    tmp_path, overrides
) -> None:
    database_path = tmp_path / "catalog.db"
    source_event_id = _current_database_with_source_event(database_path)

    with _open_database(database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            values = {
                "state": "completed",
                "projection_table": "roll_observations",
                "projection_row_id": 1,
                "completed_at": "2026-07-18T00:01:00+00:00",
            }
            values.update(overrides)
            _insert_projection_link(
                connection,
                source_event_id,
                **values,
            )
        _insert_projection_link(
            connection,
            source_event_id,
            state="completed",
            projection_table="roll_observations",
            projection_row_id=1,
            completed_at="2026-07-18T00:01:00+00:00",
        )


def test_projection_link_rejects_invalid_state(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    source_event_id = _current_database_with_source_event(database_path)

    with _open_database(database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_projection_link(connection, source_event_id, state="failed")


def test_failed_projection_link_migration_rolls_back_schema_and_metadata(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    _make_baseline_database(database_path)

    with _open_database(database_path) as connection:
        run_migrations(connection, CATALOG_MIGRATIONS[:2])

        def fail_after_schema(migration_connection):
            CATALOG_MIGRATIONS[2].apply(migration_connection)
            raise RuntimeError("migration 3 failed")

        with pytest.raises(RuntimeError, match="migration 3 failed"):
            run_migrations(
                connection,
                CATALOG_MIGRATIONS[:2]
                + (Migration(3, "failing-projection-links", fail_after_schema),),
            )
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,)]
        assert connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'discord_projection_links'"
        ).fetchone() is None


def test_aggregate_identity_is_unique_without_using_payload_hash(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    CatalogRepository(database_path)
    with _open_database(database_path) as connection:
        _insert_aggregate(connection)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_aggregate(connection)
        _insert_aggregate(connection, channel_id="channel-2")
        _insert_aggregate(connection, guild_id="guild-2")
        _insert_aggregate(connection, message_id="message-2")

        assert connection.execute(
            "SELECT COUNT(*) FROM discord_message_aggregates"
        ).fetchone()[0] == 4


def test_revision_partial_uniqueness_and_active_state_constraints(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    CatalogRepository(database_path)
    with _open_database(database_path) as connection:
        aggregate_id = _insert_aggregate(connection)
        _insert_revision(connection, aggregate_id)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_revision(connection, aggregate_id)

        _insert_revision(connection, aggregate_id, source_revision_marker="revision-2")
        _insert_revision(
            connection,
            aggregate_id,
            source_revision_marker=None,
            normalized_payload_hash="hash-1",
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_revision(
                connection,
                aggregate_id,
                source_revision_marker=None,
                normalized_payload_hash="hash-1",
            )

        _insert_revision(
            connection,
            aggregate_id,
            source_revision_marker="revision-3",
            normalized_payload_hash="hash-3",
            revision_state="active",
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_revision(
                connection,
                aggregate_id,
                source_revision_marker="revision-4",
                normalized_payload_hash="hash-4",
                revision_state="active",
            )
        for state in ("candidate", "superseded", "stale"):
            _insert_revision(
                connection,
                aggregate_id,
                source_revision_marker=f"{state}-revision",
                normalized_payload_hash=f"{state}-hash",
                revision_state=state,
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_revision(
                connection,
                aggregate_id,
                source_revision_marker="invalid",
                normalized_payload_hash="invalid",
                revision_state="invalid",
            )


def test_source_event_constraints_and_legacy_import_event_foreign_key(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    CatalogRepository(database_path)
    with _open_database(database_path) as connection:
        aggregate_id = _insert_aggregate(connection)
        revision_id = _insert_revision(connection, aggregate_id)
        connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) "
            "VALUES (?, ?, ?, ?)",
            ("roll", "test", "now", "legacy"),
        )
        legacy_event_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        _insert_source_event(connection, revision_id, legacy_import_event_id=legacy_event_id)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_source_event(connection, revision_id, event_key="event-2")

        second_revision_id = _insert_revision(
            connection,
            aggregate_id,
            source_revision_marker="revision-2",
            normalized_payload_hash="hash-2",
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_source_event(connection, second_revision_id, event_key="event-1")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_source_event(connection, second_revision_id, event_key="event-3", status="invalid")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_source_event(connection, second_revision_id, event_key="event-4", delivery_count=0)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_source_event(connection, second_revision_id, event_key=" ")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO discord_source_events (
                    event_key, revision_id, event_kind, status, raw_text,
                    received_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("event-5", second_revision_id, " ", "received", "", "now", "now"),
            )


def test_processing_attempt_constraints_and_processing_lease_uniqueness(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    CatalogRepository(database_path)
    with _open_database(database_path) as connection:
        first_aggregate_id = _insert_aggregate(connection)
        first_revision_id = _insert_revision(connection, first_aggregate_id)
        first_event_id = _insert_source_event(connection, first_revision_id)
        second_aggregate_id = _insert_aggregate(connection, guild_id="guild-2")
        second_revision_id = _insert_revision(
            connection,
            second_aggregate_id,
            source_revision_marker="revision-2",
            normalized_payload_hash="hash-2",
        )
        second_event_id = _insert_source_event(
            connection, second_revision_id, event_key="event-2"
        )

        _insert_attempt(connection, first_event_id, status="succeeded", retryable=0)
        _insert_attempt(connection, first_event_id, attempt_number=2, status="failed")
        _insert_attempt(connection, first_event_id, attempt_number=3, status="processing")
        _insert_attempt(connection, second_event_id, status="processing")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_attempt(connection, first_event_id, attempt_number=2, status="failed")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_attempt(connection, first_event_id, attempt_number=4, status="processing")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_attempt(connection, first_event_id, attempt_number=5, status="invalid")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_attempt(connection, first_event_id, attempt_number=5, retryable=2)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_attempt(connection, first_event_id, attempt_number=0)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_attempt(connection, first_event_id, attempt_number=5, parser_version=" ")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_attempt(connection, first_event_id, attempt_number=5, router_version=" ")


def test_ingestion_foreign_keys_require_parent_rows(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    CatalogRepository(database_path)
    with _open_database(database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_revision(connection, 999)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_source_event(connection, 999)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_attempt(connection, 999)
        aggregate_id = _insert_aggregate(connection)
        revision_id = _insert_revision(connection, aggregate_id)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_source_event(connection, revision_id, legacy_import_event_id=999)


def test_failed_discord_ingestion_migration_rolls_back_schema_and_metadata(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    _make_baseline_database(database_path)

    def fail_after_schema(connection):
        CATALOG_MIGRATIONS[1].apply(connection)
        raise RuntimeError("migration 2 failed")

    with _open_database(database_path) as connection:
        with pytest.raises(RuntimeError, match="migration 2 failed"):
            run_migrations(
                connection,
                (CATALOG_MIGRATIONS[0], Migration(2, "failing-ingestion", fail_after_schema)),
            )
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,)]
        assert connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE name IN (
                'discord_message_aggregates', 'discord_message_revisions',
                'discord_source_events', 'discord_processing_attempts',
                'uq_discord_revision_versioned', 'uq_discord_revision_unversioned',
                'uq_discord_active_revision', 'uq_discord_processing_attempt'
            )
            """
        ).fetchall() == []


def test_catalog_initialization_is_idempotent_and_preserves_data(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    CatalogRepository(database_path)
    with _open_database(database_path) as connection:
        connection.execute(
            "INSERT INTO characters "
            "(name, series, normalized_name, normalized_series, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("Miku", "VOCALOID", "miku", "vocaloid", "now", "now"),
        )

    CatalogRepository(database_path)

    with _open_database(database_path) as connection:
        assert connection.execute("SELECT name FROM characters").fetchone()[0] == "Miku"
    assert _migration_rows(database_path) == [
        (1, "catalog-schema-baseline"),
        (2, "durable-discord-message-ingestion"),
        (3, "durable-discord-projection-links"),
        (4, "durable-discord-source-event-server-attributions"),
        (5, "durable-discord-source-event-account-attributions"),
        (6, "durable-discord-antidisable-workflow-bindings"),
        (7, "tower-completed-towers-presence"),
        (8, "kakeraloot-state-value-presence"),
        (9, "profile-response-presence"),
        (10, "ranked-harem-roulette-presence"),
        (11, "disablelist-toggle-presence"),
        (12, "raw-evidence-lifecycle-foundation"),
        (13, "projection-generation-foundation"),
    ]


def test_existing_current_schema_without_metadata_is_baselined(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    _make_baseline_database(database_path)
    with _open_database(database_path) as connection:
        connection.execute(
            "INSERT INTO characters "
            "(name, series, normalized_name, normalized_series, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("Mai", "Seishun Buta Yarou", "mai", "seishun buta yarou", "now", "now"),
        )
        connection.execute("DROP TABLE schema_migrations")

    CatalogRepository(database_path)

    with _open_database(database_path) as connection:
        assert connection.execute("SELECT name FROM characters").fetchone()[0] == "Mai"
    assert _migration_rows(database_path) == [
        (1, "catalog-schema-baseline"),
        (2, "durable-discord-message-ingestion"),
        (3, "durable-discord-projection-links"),
        (4, "durable-discord-source-event-server-attributions"),
        (5, "durable-discord-source-event-account-attributions"),
        (6, "durable-discord-antidisable-workflow-bindings"),
        (7, "tower-completed-towers-presence"),
        (8, "kakeraloot-state-value-presence"),
        (9, "profile-response-presence"),
        (10, "ranked-harem-roulette-presence"),
        (11, "disablelist-toggle-presence"),
        (12, "raw-evidence-lifecycle-foundation"),
        (13, "projection-generation-foundation"),
    ]


def test_unknown_partial_schema_fails_without_baselining_or_repairing(tmp_path) -> None:
    database_path = tmp_path / "partial.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO unrelated (value) VALUES ('keep me')")

    with pytest.raises(MigrationError, match="Unrecognized MOA catalog schema"):
        CatalogRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT value FROM unrelated").fetchone()[0] == "keep me"
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone() is None


def test_ordered_migrations_run_once_in_ascending_order(tmp_path) -> None:
    database_path = tmp_path / "ordered.db"
    applied = []
    migrations = (
        Migration(1, "first", lambda connection: applied.append("first")),
        Migration(2, "second", lambda connection: applied.append("second")),
    )
    with sqlite3.connect(database_path) as connection:
        run_migrations(connection, migrations)
        run_migrations(connection, migrations)

    assert applied == ["first", "second"]
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,)]


def test_competing_migrators_recheck_versions_after_immediate_ownership(tmp_path) -> None:
    database_path = tmp_path / "concurrent-migration.db"
    _make_version_5_database(database_path)

    migration_a_applied = threading.Event()
    release_migration_a = threading.Event()
    migration_b_ready = threading.Event()
    start_migration_b = threading.Event()
    migration_b_begin_attempted = threading.Event()
    migration_b_read_versions = threading.Event()
    migration_b_body_executed = threading.Event()
    migration_b_finished = threading.Event()
    failures = []
    execution_count = 0
    execution_count_lock = threading.Lock()

    class ObservedConnection:
        def __init__(self, connection, role):
            self._connection = connection
            self._role = role

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def execute(self, sql, parameters=()):
            if self._role == "b" and sql == "BEGIN IMMEDIATE":
                migration_b_begin_attempted.set()
            result = self._connection.execute(sql, parameters)
            if (
                self._role == "b"
                and sql == "SELECT version, name FROM schema_migrations ORDER BY version"
            ):
                migration_b_read_versions.set()
            return result

    def apply_migration_a(connection):
        nonlocal execution_count
        CATALOG_MIGRATIONS[5].apply(connection)
        with execution_count_lock:
            execution_count += 1
        migration_a_applied.set()
        assert release_migration_a.wait(5), "migration A was not released"

    def apply_migration_b(connection):
        nonlocal execution_count
        migration_b_body_executed.set()
        CATALOG_MIGRATIONS[5].apply(connection)
        with execution_count_lock:
            execution_count += 1

    migrations_a = CATALOG_MIGRATIONS[:5] + (
        Migration(6, CATALOG_MIGRATIONS[5].name, apply_migration_a),
    )
    migrations_b = CATALOG_MIGRATIONS[:5] + (
        Migration(6, CATALOG_MIGRATIONS[5].name, apply_migration_b),
    )

    with connect(database_path) as observer_a, connect(database_path) as observer_b:
        assert [
            row[0]
            for row in observer_a.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ] == [1, 2, 3, 4, 5]
        assert [
            row[0]
            for row in observer_b.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ] == [1, 2, 3, 4, 5]

    def run_migrator(role, migrations):
        connection = connect(database_path)
        try:
            if role == "b":
                migration_b_ready.set()
                assert start_migration_b.wait(5), "migration B was not started"
            run_migrations(ObservedConnection(connection, role), migrations)
            if role == "b":
                migration_b_finished.set()
        except BaseException as error:
            failures.append(error)
        finally:
            connection.close()

    migrator_a = threading.Thread(target=run_migrator, args=("a", migrations_a))
    migrator_b = threading.Thread(target=run_migrator, args=("b", migrations_b))
    migrator_b.start()
    assert migration_b_ready.wait(5), "migration B connection was not ready"
    migrator_a.start()
    assert migration_a_applied.wait(5), "migration A did not apply migration 6"

    start_migration_b.set()
    assert migration_b_begin_attempted.wait(5), "migration B did not attempt ownership"
    assert not migration_b_read_versions.is_set()
    assert not migration_b_body_executed.is_set()
    assert not migration_b_finished.is_set()

    release_migration_a.set()
    migrator_a.join(timeout=5)
    migrator_b.join(timeout=5)
    assert not migrator_a.is_alive(), "migration A did not terminate"
    assert not migrator_b.is_alive(), "migration B did not terminate"
    assert failures == []
    assert migration_b_read_versions.is_set()
    assert not migration_b_body_executed.is_set()
    assert migration_b_finished.is_set()
    assert execution_count == 1

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 6"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name LIKE 'discord_antidisable_%' ORDER BY name"
        ).fetchall() == [
            ("discord_antidisable_response_bindings",),
            ("discord_antidisable_workflows",),
        ]


def test_version_row_failure_rolls_back_and_same_database_retry_succeeds(tmp_path) -> None:
    database_path = tmp_path / "migration-retry.db"
    later_ran = False

    def first(connection):
        connection.execute("CREATE TABLE first_committed (value TEXT)")

    def second(connection):
        connection.execute("CREATE TABLE second_retried (value TEXT)")
        connection.execute("INSERT INTO second_retried VALUES ('kept after retry')")

    def third(connection):
        nonlocal later_ran
        later_ran = True
        connection.execute("CREATE TABLE third_committed (value TEXT)")

    migrations = (
        Migration(1, "first", first),
        Migration(2, "second", second),
        Migration(3, "third", third),
    )

    with sqlite3.connect(database_path) as connection:
        run_migrations(connection, migrations[:1])
        connection.execute(
            """
            CREATE TRIGGER fail_second_version_row
            BEFORE INSERT ON schema_migrations
            WHEN NEW.version = 2
            BEGIN
                SELECT RAISE(FAIL, 'forced version-row failure');
            END
            """
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError, match="forced version-row failure"):
            run_migrations(connection, migrations)

        assert connection.in_transaction is False
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,)]
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'second_retried'"
        ).fetchone() is None
        assert later_ran is False

        connection.execute("DROP TRIGGER fail_second_version_row")
        connection.commit()
        run_migrations(connection, migrations)

        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,)]
        assert connection.execute("SELECT value FROM second_retried").fetchall() == [
            ("kept after retry",)
        ]
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'third_committed'"
        ).fetchone()[0] == "third_committed"
        assert later_ran is True


def test_failed_migration_rolls_back_and_stops_later_migrations(tmp_path) -> None:
    database_path = tmp_path / "failed.db"
    later_ran = False

    def fail(connection):
        connection.execute("CREATE TABLE rolled_back (value TEXT)")
        connection.execute("INSERT INTO rolled_back VALUES ('not kept')")
        raise RuntimeError("migration failed")

    def later(connection):
        nonlocal later_ran
        later_ran = True

    with sqlite3.connect(database_path) as connection:
        with pytest.raises(RuntimeError, match="migration failed"):
            run_migrations(connection, (Migration(1, "fail", fail), Migration(2, "later", later)))
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'rolled_back'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone() is None
    assert later_ran is False


@pytest.mark.parametrize(
    "migrations, message",
    [
        ((Migration(1, "one", lambda connection: None), Migration(1, "again", lambda connection: None)), "unique"),
        ((Migration(2, "two", lambda connection: None),), "contiguous"),
        ((Migration(2, "two", lambda connection: None), Migration(1, "one", lambda connection: None)), "ordered"),
    ],
)
def test_invalid_migration_definitions_are_rejected(tmp_path, migrations, message) -> None:
    with sqlite3.connect(tmp_path / "invalid.db") as connection:
        with pytest.raises(MigrationError, match=message):
            run_migrations(connection, migrations)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone() is None


def test_tower_presence_migration_preserves_legacy_ambiguity_and_nonzero_values(
    tmp_path,
) -> None:
    database_path = tmp_path / "catalog.db"
    historical_values = {
        "legacy-none": None,
        "legacy-zero": 0,
        "legacy-positive": 11,
        "legacy-negative": -1,
    }
    _make_version_6_database(database_path, historical_values)

    with _open_database(database_path) as connection:
        assert "completed_towers_observed" not in {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(tower_state_observations)"
            ).fetchall()
        }
        run_migrations(connection, CATALOG_MIGRATIONS)
        rows = connection.execute(
            """
            SELECT account_contexts.name, completed_towers, completed_towers_observed
            FROM tower_state_observations
            JOIN account_contexts
              ON account_contexts.id = tower_state_observations.account_context_id
            ORDER BY tower_state_observations.id
            """
        ).fetchall()
        assert rows == [
            ("legacy-none", 0, None),
            ("legacy-zero", 0, None),
            ("legacy-positive", 11, 1),
            ("legacy-negative", -1, 1),
        ]
        presence_column = next(
            row
            for row in connection.execute(
                "PRAGMA table_info(tower_state_observations)"
            ).fetchall()
            if row[1] == "completed_towers_observed"
        )
        assert tuple(presence_column[1:5]) == (
            "completed_towers_observed",
            "INTEGER",
            0,
            None,
        )

    catalog = CatalogRepository(database_path)
    assert catalog.tower_state("Server", "legacy-none").completed_towers is None
    assert catalog.tower_state("Server", "legacy-zero").completed_towers is None
    assert catalog.tower_state("Server", "legacy-positive").completed_towers == 11
    assert catalog.tower_state("Server", "legacy-negative").completed_towers == -1
    CatalogRepository(database_path)
    assert _migration_rows(database_path).count((7, "tower-completed-towers-presence")) == 1
    with _open_database(database_path) as connection:
        assert connection.execute(
            "SELECT completed_towers, completed_towers_observed "
            "FROM tower_state_observations ORDER BY id"
        ).fetchall() == [(0, None), (0, None), (11, 1), (-1, 1)]


def test_failed_tower_presence_migration_rolls_back_and_retries_cleanly(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    _make_version_6_database(
        database_path,
        {"legacy-zero": 0, "legacy-positive": 11, "legacy-negative": -1},
    )

    def fail_after_tower_presence(connection):
        CATALOG_MIGRATIONS[6].apply(connection)
        raise RuntimeError("stop after tower presence")

    failing_migrations = CATALOG_MIGRATIONS[:6] + (
        Migration(7, "failing-tower-presence", fail_after_tower_presence),
    )
    with _open_database(database_path) as connection:
        with pytest.raises(RuntimeError, match="stop after tower presence"):
            run_migrations(connection, failing_migrations)
        assert "completed_towers_observed" not in {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(tower_state_observations)"
            ).fetchall()
        }
        assert connection.execute(
            "SELECT completed_towers FROM tower_state_observations ORDER BY id"
        ).fetchall() == [(0,), (11,), (-1,)]
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,), (5,), (6,)]

        run_migrations(connection, CATALOG_MIGRATIONS)
        assert connection.execute(
            "SELECT completed_towers, completed_towers_observed "
            "FROM tower_state_observations ORDER BY id"
        ).fetchall() == [(0, None), (11, 1), (-1, 1)]
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 7"
        ).fetchone()[0] == 1


def test_fresh_and_migrated_tower_presence_columns_are_equivalent_and_constrained(
    tmp_path,
) -> None:
    migrated_path = tmp_path / "migrated.db"
    _make_version_6_database(migrated_path, {"Account": 3})
    with _open_database(migrated_path) as connection:
        run_migrations(connection, CATALOG_MIGRATIONS)
        migrated_column = next(
            tuple(row[1:5])
            for row in connection.execute(
                "PRAGMA table_info(tower_state_observations)"
            ).fetchall()
            if row[1] == "completed_towers_observed"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE tower_state_observations SET completed_towers_observed = 2"
            )

    fresh_path = tmp_path / "fresh.db"
    fresh_catalog = CatalogRepository(fresh_path)
    result = fresh_catalog.import_tower_state(
        TOWER_STATE, "Server", "Account", "fresh", "test"
    )
    with _open_database(fresh_path) as connection:
        fresh_column = next(
            tuple(row[1:5])
            for row in connection.execute(
                "PRAGMA table_info(tower_state_observations)"
            ).fetchall()
            if row[1] == "completed_towers_observed"
        )
        assert fresh_column == migrated_column == (
            "completed_towers_observed",
            "INTEGER",
            0,
            None,
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE tower_state_observations SET completed_towers_observed = 2 "
                "WHERE import_event_id = ?",
                (result.import_event_id,),
            )


def test_kakeraloot_presence_migration_preserves_all_legacy_value_classes(
    tmp_path,
) -> None:
    database_path = tmp_path / "catalog.db"
    zero_values = {field_name: 0 for field_name in KAKERALOOT_VALUE_FIELDS}
    positive_values = {
        field_name: index for index, field_name in enumerate(KAKERALOOT_VALUE_FIELDS, 1)
    }
    negative_values = {
        field_name: -index for index, field_name in enumerate(KAKERALOOT_VALUE_FIELDS, 1)
    }
    states = {
        "legacy-none": KakeralootStateSnapshot(),
        "legacy-zero": KakeralootStateSnapshot(**zero_values),
        "legacy-positive": KakeralootStateSnapshot(**positive_values),
        "legacy-negative": KakeralootStateSnapshot(**negative_values),
        "false-zero": KakeralootStateSnapshot(has_kakeraloots=False, **zero_values),
        "false-nonzero": KakeralootStateSnapshot(has_kakeraloots=False, **positive_values),
    }
    _make_version_7_kakeraloot_database(database_path, states)

    with _open_database(database_path) as connection:
        assert not {f"{field_name}_observed" for field_name in KAKERALOOT_VALUE_FIELDS} & {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(kakeraloot_state_observations)"
            ).fetchall()
        }
        run_migrations(connection, CATALOG_MIGRATIONS)
        selected_columns = (
            *KAKERALOOT_VALUE_FIELDS,
            *(f"{field_name}_observed" for field_name in KAKERALOOT_VALUE_FIELDS),
        )
        rows = connection.execute(
            f"""
            SELECT account_contexts.name, {", ".join(selected_columns)}
            FROM kakeraloot_state_observations
            JOIN account_contexts
              ON account_contexts.id = kakeraloot_state_observations.account_context_id
            ORDER BY kakeraloot_state_observations.id
            """
        ).fetchall()

    by_account = {row[0]: row[1:] for row in rows}
    field_count = len(KAKERALOOT_VALUE_FIELDS)
    for account in ("legacy-none", "legacy-zero", "false-zero"):
        assert by_account[account][:field_count] == tuple(0 for _ in range(field_count))
        assert by_account[account][field_count:] == tuple(None for _ in range(field_count))
    for account, expected_values in (
        ("legacy-positive", positive_values),
        ("false-nonzero", positive_values),
        ("legacy-negative", negative_values),
    ):
        assert by_account[account][:field_count] == tuple(
            expected_values[field_name] for field_name in KAKERALOOT_VALUE_FIELDS
        )
        assert by_account[account][field_count:] == tuple(1 for _ in range(field_count))

    catalog = CatalogRepository(database_path)
    assert catalog.kakeraloot_state("Server", "legacy-zero").rolls_stacked is None
    assert catalog.kakeraloot_state("Server", "legacy-positive").rolls_stacked == 1
    assert catalog.kakeraloot_state("Server", "legacy-negative").rolls_stacked == -1
    assert catalog.kakeraloot_state("Server", "false-nonzero").rolls_stacked is None
    CatalogRepository(database_path)
    assert _migration_rows(database_path).count((8, "kakeraloot-state-value-presence")) == 1


def test_failed_kakeraloot_presence_migration_rolls_back_and_retries_cleanly(
    tmp_path,
) -> None:
    database_path = tmp_path / "catalog.db"
    _make_version_7_kakeraloot_database(
        database_path,
        {
            "legacy-zero": KakeralootStateSnapshot(rolls_stacked=0),
            "legacy-nonzero": KakeralootStateSnapshot(rolls_stacked=-1),
        },
    )

    def fail_after_kakeraloot_presence(connection):
        CATALOG_MIGRATIONS[7].apply(connection)
        raise RuntimeError("stop after Kakeraloot presence")

    failing_migrations = CATALOG_MIGRATIONS[:7] + (
        Migration(8, "failing-kakeraloot-presence", fail_after_kakeraloot_presence),
    )
    with _open_database(database_path) as connection:
        with pytest.raises(RuntimeError, match="stop after Kakeraloot presence"):
            run_migrations(connection, failing_migrations)
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(kakeraloot_state_observations)"
            ).fetchall()
        }
        assert not {f"{field_name}_observed" for field_name in KAKERALOOT_VALUE_FIELDS} & columns
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(version,) for version in range(1, 8)]

        run_migrations(connection, CATALOG_MIGRATIONS)
        assert connection.execute(
            "SELECT rolls_stacked, rolls_stacked_observed "
            "FROM kakeraloot_state_observations ORDER BY id"
        ).fetchall() == [(0, None), (-1, 1)]
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version = 8"
            ).fetchone()[0]
            == 1
        )


def test_fresh_and_migrated_kakeraloot_presence_columns_are_equivalent_and_constrained(
    tmp_path,
) -> None:
    migrated_path = tmp_path / "migrated.db"
    _make_version_7_kakeraloot_database(
        migrated_path,
        {"Account": KakeralootStateSnapshot(rolls_stacked=3)},
    )
    with _open_database(migrated_path) as connection:
        run_migrations(connection, CATALOG_MIGRATIONS)

    fresh_path = tmp_path / "fresh.db"
    fresh_catalog = CatalogRepository(fresh_path)
    fresh_catalog.import_kakeraloot_state(
        KakeralootStateSnapshot(), "Server", "Account", "fresh", "test"
    )

    schema_by_path = {}
    for database_path in (migrated_path, fresh_path):
        with _open_database(database_path) as connection:
            schema_by_path[database_path] = {
                row[1]: tuple(row[1:5])
                for row in connection.execute(
                    "PRAGMA table_info(kakeraloot_state_observations)"
                ).fetchall()
                if row[1].endswith("_observed")
            }
            for field_name in KAKERALOOT_VALUE_FIELDS:
                with pytest.raises(sqlite3.IntegrityError):
                    connection.execute(
                        f"UPDATE kakeraloot_state_observations SET {field_name}_observed = 2"
                    )

    expected_schema = {
        f"{field_name}_observed": (
            f"{field_name}_observed",
            "INTEGER",
            0,
            None,
        )
        for field_name in KAKERALOOT_VALUE_FIELDS
    }
    assert schema_by_path[migrated_path] == schema_by_path[fresh_path] == expected_schema


def test_profile_presence_migration_preserves_safe_evidence_and_legacy_ambiguity(
    tmp_path,
) -> None:
    database_path = tmp_path / "catalog.db"
    rich_profile = MINIMAL_PROFILE.model_copy(
        update={
            "profile_name": "rich",
            "pokedex_count": 2,
            "pokedex_pokemon": ("gulpin", "piloswine"),
            "kakera_reacts": {":kakeraY:": 497},
            "mudapins_collected": 4,
            "mudapins_total": 12,
            "kakera_balance": 812,
            "bronze_keys": 3,
            "silver_keys": 2,
            "gold_keys": 1,
            "sphere_stock": 0,
            "spheres": {":spP:": 2},
            "displayed_badges": (":silvmudae:",),
            **{field_name: True for field_name in PROFILE_PRESENCE_FIELDS},
        }
    )
    zero_profile = MINIMAL_PROFILE.model_copy(
        update={
            "profile_name": "zero",
            "kakera_balance": 0,
            "keys_observed": True,
            "bronze_keys": 0,
            "bronze_keys_observed": True,
            "sphere_stock": 0,
            "kakera_balance_observed": True,
            "sphere_stock_observed": True,
        }
    )
    profiles = {
        "minimal": MINIMAL_PROFILE.model_copy(update={"profile_name": "minimal"}),
        "rich": rich_profile,
        "zero": zero_profile,
        "partial": MINIMAL_PROFILE.model_copy(update={"profile_name": "partial"}),
        "partial-items": MINIMAL_PROFILE.model_copy(
            update={"profile_name": "partial-items"}
        ),
    }
    _make_version_8_profile_database(database_path, profiles)
    with _open_database(database_path) as connection:
        connection.execute(
            "UPDATE profile_observations SET pokedex_count = 5, "
            "mudapins_collected = 7 WHERE profile_name = 'partial'"
        )
        connection.execute(
            "UPDATE profile_observations SET pokedex_json = '[\"piplup\"]' "
            "WHERE profile_name = 'partial-items'"
        )
        original_values = connection.execute(
            "SELECT profile_name, pokedex_count, pokedex_json, kakera_reacts_json, "
            "mudapins_collected, mudapins_total, kakera_balance, bronze_keys, "
            "silver_keys, gold_keys, sphere_stock, spheres_json, displayed_badges_json "
            "FROM profile_observations ORDER BY profile_name"
        ).fetchall()
        connection.commit()

        run_migrations(connection, CATALOG_MIGRATIONS)

        rows = connection.execute(
            f"SELECT profile_name, {', '.join(PROFILE_PRESENCE_FIELDS)} "
            "FROM profile_observations ORDER BY profile_name"
        ).fetchall()
        assert rows == [
            ("minimal", *([None] * len(PROFILE_PRESENCE_FIELDS))),
            ("partial", *([None] * len(PROFILE_PRESENCE_FIELDS))),
            ("partial-items", *([None] * len(PROFILE_PRESENCE_FIELDS))),
            ("rich", *([1] * len(PROFILE_PRESENCE_FIELDS))),
            (
                "zero",
                None,
                None,
                None,
                1,
                None,
                None,
                None,
                None,
                1,
                None,
                None,
            ),
        ]
        assert connection.execute(
            "SELECT profile_name, pokedex_count, pokedex_json, kakera_reacts_json, "
            "mudapins_collected, mudapins_total, kakera_balance, bronze_keys, "
            "silver_keys, gold_keys, sphere_stock, spheres_json, displayed_badges_json "
            "FROM profile_observations ORDER BY profile_name"
        ).fetchall() == original_values

    catalog = CatalogRepository(database_path)
    assert catalog.profile("Server", "minimal").snapshot == profiles["minimal"].model_copy(
        update={field_name: None for field_name in PROFILE_PRESENCE_FIELDS}
    )
    partial = catalog.profile("Server", "partial").snapshot
    assert (partial.pokedex_count, partial.pokedex_pokemon, partial.pokedex_observed) == (
        5,
        None,
        None,
    )
    assert (
        partial.mudapins_collected,
        partial.mudapins_total,
        partial.mudapins_observed,
    ) == (7, None, None)
    partial_items = catalog.profile("Server", "partial-items").snapshot
    assert (
        partial_items.pokedex_count,
        partial_items.pokedex_pokemon,
        partial_items.pokedex_observed,
    ) == (None, ("piplup",), None)
    zero = catalog.profile("Server", "zero").snapshot
    assert (zero.kakera_balance, zero.kakera_balance_observed) == (0, True)
    assert (zero.sphere_stock, zero.sphere_stock_observed) == (0, True)
    assert (zero.bronze_keys, zero.bronze_keys_observed, zero.keys_observed) == (
        None,
        None,
        None,
    )


def test_failed_profile_presence_migration_rolls_back_and_retries_cleanly(
    tmp_path,
) -> None:
    database_path = tmp_path / "catalog.db"
    _make_version_8_profile_database(database_path, {"Account": MINIMAL_PROFILE})

    def fail_after_profile_presence(connection):
        CATALOG_MIGRATIONS[8].apply(connection)
        raise RuntimeError("stop after Profile presence")

    failing_migrations = CATALOG_MIGRATIONS[:8] + (
        Migration(9, "failing-profile-presence", fail_after_profile_presence),
    )
    with _open_database(database_path) as connection:
        with pytest.raises(RuntimeError, match="stop after Profile presence"):
            run_migrations(connection, failing_migrations)
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(profile_observations)")
        }
        assert not set(PROFILE_PRESENCE_FIELDS) & columns
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(version,) for version in range(1, 9)]

        run_migrations(connection, CATALOG_MIGRATIONS)
        assert set(PROFILE_PRESENCE_FIELDS) <= {
            row[1]
            for row in connection.execute("PRAGMA table_info(profile_observations)")
        }
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 9"
        ).fetchone()[0] == 1


def test_fresh_and_migrated_profile_presence_schema_are_equivalent_and_idempotent(
    tmp_path,
) -> None:
    migrated_path = tmp_path / "migrated.db"
    _make_version_8_profile_database(migrated_path, {"Account": MINIMAL_PROFILE})
    CatalogRepository(migrated_path)
    with _open_database(migrated_path) as connection:
        before = connection.execute("SELECT * FROM profile_observations").fetchall()

    fresh_path = tmp_path / "fresh.db"
    CatalogRepository(fresh_path).import_profile(
        MINIMAL_PROFILE, "Server", "Account", "fresh", "test"
    )

    schema_by_path = {}
    for database_path in (migrated_path, fresh_path):
        with _open_database(database_path) as connection:
            schema_by_path[database_path] = {
                row[1]: tuple(row[1:5])
                for row in connection.execute(
                    "PRAGMA table_info(profile_observations)"
                ).fetchall()
            }
            for field_name in PROFILE_PRESENCE_FIELDS:
                with pytest.raises(sqlite3.IntegrityError):
                    connection.execute(
                        f"UPDATE profile_observations SET {field_name} = 2"
                    )

    assert schema_by_path[migrated_path] == schema_by_path[fresh_path]
    CatalogRepository(migrated_path)
    with _open_database(migrated_path) as connection:
        assert connection.execute("SELECT * FROM profile_observations").fetchall() == before
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 9"
        ).fetchone()[0] == 1
        assert all(
            sum(row[1] == field_name for row in connection.execute(
                "PRAGMA table_info(profile_observations)"
            )) == 1
            for field_name in PROFILE_PRESENCE_FIELDS
        )


def test_ranked_harem_roulette_presence_migration_preserves_legacy_ambiguity(
    tmp_path,
) -> None:
    database_path = tmp_path / "catalog.db"
    _make_version_9_ranked_harem_database(
        database_path,
        {
            "legacy-absent": None,
            "legacy-empty": (),
            "legacy-observed": ("wa", "ha"),
        },
    )

    with _open_database(database_path) as connection:
        assert "roulette_types_observed" not in {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(owned_character_observations)"
            )
        }
        run_migrations(connection, CATALOG_MIGRATIONS)
        assert connection.execute(
            """
            SELECT account_contexts.name, roulette_types_json,
                   roulette_types_observed
            FROM owned_character_observations
            JOIN account_contexts
              ON account_contexts.id =
                 owned_character_observations.account_context_id
            ORDER BY owned_character_observations.id
            """
        ).fetchall() == [
            ("legacy-absent", "[]", None),
            ("legacy-empty", "[]", None),
            ("legacy-observed", '["wa", "ha"]', 1),
        ]
        presence_column = next(
            row
            for row in connection.execute(
                "PRAGMA table_info(owned_character_observations)"
            )
            if row[1] == "roulette_types_observed"
        )
        assert tuple(presence_column[1:5]) == (
            "roulette_types_observed",
            "INTEGER",
            0,
            None,
        )

    catalog = CatalogRepository(database_path)
    assert catalog.owned_characters("Server", "legacy-absent")[0].roulette_types is None
    assert catalog.owned_characters("Server", "legacy-empty")[0].roulette_types is None
    assert catalog.owned_characters("Server", "legacy-observed")[0].roulette_types == (
        "wa",
        "ha",
    )
    CatalogRepository(database_path)
    assert _migration_rows(database_path).count(
        (10, "ranked-harem-roulette-presence")
    ) == 1


def test_failed_ranked_harem_roulette_presence_migration_rolls_back_and_retries(
    tmp_path,
) -> None:
    database_path = tmp_path / "catalog.db"
    _make_version_9_ranked_harem_database(
        database_path,
        {"legacy-empty": (), "legacy-observed": ("wa",)},
    )

    def fail_after_ranked_harem_presence(connection):
        CATALOG_MIGRATIONS[9].apply(connection)
        raise RuntimeError("stop after ranked-harem presence")

    failing_migrations = CATALOG_MIGRATIONS[:9] + (
        Migration(
            10,
            "failing-ranked-harem-presence",
            fail_after_ranked_harem_presence,
        ),
    )
    with _open_database(database_path) as connection:
        with pytest.raises(RuntimeError, match="stop after ranked-harem presence"):
            run_migrations(connection, failing_migrations)
        assert "roulette_types_observed" not in {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(owned_character_observations)"
            )
        }
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(version,) for version in range(1, 10)]

        run_migrations(connection, CATALOG_MIGRATIONS)
        assert connection.execute(
            "SELECT roulette_types_json, roulette_types_observed "
            "FROM owned_character_observations ORDER BY id"
        ).fetchall() == [("[]", None), ('["wa"]', 1)]
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 10"
        ).fetchone()[0] == 1


def test_fresh_and_migrated_ranked_harem_presence_schema_are_equivalent(
    tmp_path,
) -> None:
    migrated_path = tmp_path / "migrated.db"
    _make_version_9_ranked_harem_database(
        migrated_path,
        {"legacy-observed": ("wa",)},
    )
    CatalogRepository(migrated_path)

    fresh_path = tmp_path / "fresh.db"
    fresh_catalog = CatalogRepository(fresh_path)
    fresh_catalog.import_ranked_harem_page(
        RankedHaremPage(
            page_number=None,
            page_count=None,
            entries=(
                RankedHaremEntry(
                    name="fresh-absent",
                    claim_rank=1,
                    roulette_types=None,
                ),
                RankedHaremEntry(
                    name="fresh-observed",
                    claim_rank=2,
                    roulette_types=("ha",),
                ),
            ),
        ),
        "Server",
        "Account",
        "fresh ranked harem",
        "test",
    )

    columns_by_path = {}
    for database_path in (migrated_path, fresh_path):
        with _open_database(database_path) as connection:
            columns_by_path[database_path] = next(
                tuple(row[1:5])
                for row in connection.execute(
                    "PRAGMA table_info(owned_character_observations)"
                )
                if row[1] == "roulette_types_observed"
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE owned_character_observations "
                    "SET roulette_types_observed = 2"
                )

    assert columns_by_path[migrated_path] == columns_by_path[fresh_path] == (
        "roulette_types_observed",
        "INTEGER",
        0,
        None,
    )
    with _open_database(fresh_path) as connection:
        before = connection.execute(
            "SELECT roulette_types_json, roulette_types_observed "
            "FROM owned_character_observations ORDER BY id"
        ).fetchall()
        CATALOG_MIGRATIONS[9].apply(connection)
        assert connection.execute(
            "SELECT roulette_types_json, roulette_types_observed "
            "FROM owned_character_observations ORDER BY id"
        ).fetchall() == before == [("[]", 0), ('["ha"]', 1)]


def test_disablelist_toggle_presence_migration_preserves_legacy_ambiguity(
    tmp_path,
) -> None:
    database_path = tmp_path / "catalog.db"
    _make_version_10_disablelist_database(
        database_path,
        {
            "western-true": (True, False),
            "western-false": (False, True),
            "irl-true": (False, True),
            "irl-false": (True, False),
            "mixed": (True, False),
        },
    )

    with _open_database(database_path) as connection:
        before_values = connection.execute(
            "SELECT western_disabled, irl_disabled "
            "FROM disablelist_observations ORDER BY id"
        ).fetchall()
        assert {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(disablelist_observations)"
            )
        }.isdisjoint(
            {"western_disabled_observed", "irl_disabled_observed"}
        )

        run_migrations(connection, CATALOG_MIGRATIONS)

        rows = connection.execute(
            """
            SELECT account_contexts.name, western_disabled,
                   western_disabled_observed, irl_disabled,
                   irl_disabled_observed
            FROM disablelist_observations
            JOIN account_contexts
              ON account_contexts.id =
                 disablelist_observations.account_context_id
            ORDER BY disablelist_observations.id
            """
        ).fetchall()
        assert rows == [
            ("western-true", 1, 1, 0, None),
            ("western-false", 0, None, 1, 1),
            ("irl-true", 0, None, 1, 1),
            ("irl-false", 1, 1, 0, None),
            ("mixed", 1, 1, 0, None),
        ]
        assert [(row[1], row[3]) for row in rows] == before_values
        assert connection.execute(
            "SELECT COUNT(*) FROM disablelist_observations "
            "WHERE western_disabled_observed = 0 "
            "OR irl_disabled_observed = 0"
        ).fetchone()[0] == 0

    catalog = CatalogRepository(database_path)
    assert catalog.disablelist("Server", "western-true").western_disabled is True
    assert catalog.disablelist("Server", "western-false").western_disabled is None
    assert catalog.disablelist("Server", "irl-true").irl_disabled is True
    assert catalog.disablelist("Server", "irl-false").irl_disabled is None


def test_failed_disablelist_toggle_presence_migration_rolls_back_and_retries(
    tmp_path,
) -> None:
    database_path = tmp_path / "catalog.db"
    _make_version_10_disablelist_database(
        database_path,
        {"historical-false": (False, False), "historical-true": (True, True)},
    )

    def fail_after_disablelist_presence(connection):
        CATALOG_MIGRATIONS[10].apply(connection)
        raise RuntimeError("stop after disablelist presence")

    failing_migrations = CATALOG_MIGRATIONS[:10] + (
        Migration(
            11,
            "failing-disablelist-presence",
            fail_after_disablelist_presence,
        ),
    )
    with _open_database(database_path) as connection:
        before_values = connection.execute(
            "SELECT western_disabled, irl_disabled "
            "FROM disablelist_observations ORDER BY id"
        ).fetchall()
        with pytest.raises(RuntimeError, match="stop after disablelist presence"):
            run_migrations(connection, failing_migrations)
        assert {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(disablelist_observations)"
            )
        }.isdisjoint(
            {"western_disabled_observed", "irl_disabled_observed"}
        )
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(version,) for version in range(1, 11)]
        assert connection.execute(
            "SELECT western_disabled, irl_disabled "
            "FROM disablelist_observations ORDER BY id"
        ).fetchall() == before_values

        run_migrations(connection, CATALOG_MIGRATIONS)
        assert connection.execute(
            "SELECT western_disabled, western_disabled_observed, "
            "irl_disabled, irl_disabled_observed "
            "FROM disablelist_observations ORDER BY id"
        ).fetchall() == [(0, None, 0, None), (1, 1, 1, 1)]
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 11"
        ).fetchone()[0] == 1


def test_fresh_and_migrated_disablelist_presence_schema_are_equivalent_and_idempotent(
    tmp_path,
) -> None:
    migrated_path = tmp_path / "migrated.db"
    _make_version_10_disablelist_database(
        migrated_path,
        {"legacy-false": (False, False), "legacy-true": (True, True)},
    )
    CatalogRepository(migrated_path)

    fresh_path = tmp_path / "fresh.db"
    fresh_catalog = CatalogRepository(fresh_path)
    for account, toggles in {
        "fresh-none": (None, None),
        "fresh-false": (False, False),
        "fresh-true": (True, True),
    }.items():
        fresh_catalog.import_disablelist(
            DisableListSnapshot(
                slots_used=0,
                slots_capacity=16,
                total_disabled=0,
                disabled_wa=0,
                disabled_ha=0,
                disabled_wg=0,
                disabled_hg=0,
                wa_pool_limit=None,
                ha_pool_limit=None,
                western_disabled=toggles[0],
                irl_disabled=toggles[1],
                entries=(),
            ),
            "Server",
            account,
            "fresh disablelist",
            "test",
        )

    columns_by_path = {}
    for database_path in (migrated_path, fresh_path):
        with _open_database(database_path) as connection:
            columns_by_path[database_path] = {
                row[1]: tuple(row[1:5])
                for row in connection.execute(
                    "PRAGMA table_info(disablelist_observations)"
                )
                if row[1] in {
                    "western_disabled_observed",
                    "irl_disabled_observed",
                }
            }
            for field_name in (
                "western_disabled_observed",
                "irl_disabled_observed",
            ):
                with pytest.raises(sqlite3.IntegrityError):
                    connection.execute(
                        f"UPDATE disablelist_observations SET {field_name} = 2"
                    )

    expected_columns = {
        "western_disabled_observed": (
            "western_disabled_observed",
            "INTEGER",
            0,
            None,
        ),
        "irl_disabled_observed": (
            "irl_disabled_observed",
            "INTEGER",
            0,
            None,
        ),
    }
    assert columns_by_path[migrated_path] == columns_by_path[fresh_path] == expected_columns

    with _open_database(fresh_path) as connection:
        before = connection.execute(
            "SELECT western_disabled, western_disabled_observed, "
            "irl_disabled, irl_disabled_observed "
            "FROM disablelist_observations ORDER BY id"
        ).fetchall()
        CATALOG_MIGRATIONS[10].apply(connection)
        assert connection.execute(
            "SELECT western_disabled, western_disabled_observed, "
            "irl_disabled, irl_disabled_observed "
            "FROM disablelist_observations ORDER BY id"
        ).fetchall() == before == [(0, 0, 0, 0), (0, 1, 0, 1), (1, 1, 1, 1)]

    before_reinitialize = {}
    for database_path in (migrated_path, fresh_path):
        with _open_database(database_path) as connection:
            before_reinitialize[database_path] = connection.execute(
                "SELECT western_disabled, western_disabled_observed, "
                "irl_disabled, irl_disabled_observed "
                "FROM disablelist_observations ORDER BY id"
            ).fetchall()

    CatalogRepository(migrated_path)
    CatalogRepository(fresh_path)
    for database_path in (migrated_path, fresh_path):
        assert _migration_rows(database_path).count(
            (11, "disablelist-toggle-presence")
        ) == 1
        with _open_database(database_path) as connection:
            assert connection.execute(
                "SELECT western_disabled, western_disabled_observed, "
                "irl_disabled, irl_disabled_observed "
                "FROM disablelist_observations ORDER BY id"
            ).fetchall() == before_reinitialize[database_path]
            column_names = [
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(disablelist_observations)"
                )
            ]
            assert column_names.count("western_disabled_observed") == 1
            assert column_names.count("irl_disabled_observed") == 1


def test_unknown_newer_database_version_fails_safely(tmp_path) -> None:
    database_path = tmp_path / "newer.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations "
            "(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_migrations VALUES (14, 'future', 'now')"
        )

    with pytest.raises(MigrationError, match="unknown newer"):
        CatalogRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version FROM schema_migrations").fetchall() == [(14,)]
