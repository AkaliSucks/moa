import sqlite3
from datetime import UTC, datetime
import re

import pytest
from typer.testing import CliRunner

from moa.cli import main
from moa.database.migrations import MigrationError, validate_current_catalog_schema
from moa.database.sqlite import connect_read_only
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.data_health_repository import DataHealthSchemaError
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.services.data_health_service import DataHealthService
from moa.services.projection_authority import PROJECTION_AUTHORITIES

OBSERVED_AT = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _initialize(path):
    CatalogRepository(path)


def _seed_base_rows(path):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO server_contexts "
            "(id, name, normalized_name, created_at, updated_at) "
            "VALUES (1, 'Server', 'server', 'now', 'now')"
        )
        connection.execute(
            "INSERT INTO account_contexts "
            "(id, server_context_id, name, normalized_name, created_at, updated_at) "
            "VALUES (1, 1, 'Account', 'account', 'now', 'now')"
        )
        connection.execute(
            "INSERT INTO import_events (id, kind, source, observed_at, raw_message) "
            "VALUES (1, 'test', 'test', 'now', 'test')"
        )


def _insert_reaction(path, observation_id, account_context_id, import_event_id):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO kakera_reaction_observations "
            "(id, account_context_id, reaction_label, kakera_earned, observed_at, import_event_id) "
            "VALUES (?, ?, 'heart', 1, 'now', ?)",
            (observation_id, account_context_id, import_event_id),
        )


def _seed_attribution(
    path,
    *,
    suffix="one",
    server_status="resolved",
    server_name="Server",
    account_status="resolved",
    account_server_name="Server",
    account_name="Account",
):
    repository = DiscordMessageRepository(path)
    aggregate_key = MessageAggregateKey(
        SourcePlatform.DISCORD, "guild", "channel", f"message-{suffix}"
    )
    received = repository.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(
            aggregate_key, f"payload-{suffix}", "revision-1"
        ),
        event_key=f"event-{suffix}",
        event_kind="message_create",
        raw_text="health test",
        payload_json='{"content":"health test"}',
        payload_capture_version="test",
        source_observed_at=OBSERVED_AT,
        received_at=OBSERVED_AT,
    )
    if server_status is not None:
        repository.record_server_attribution(
            received.source_event_id,
            status=server_status,
            server_name=server_name if server_status == "resolved" else None,
            recorded_at=OBSERVED_AT,
        )
    repository.record_account_attribution(
        received.source_event_id,
        status=account_status,
        server_name=account_server_name if account_status == "resolved" else None,
        account_name=account_name if account_status == "resolved" else None,
        recorded_at=OBSERVED_AT,
    )
    return received.source_event_id


def _insert_character(path, character_id, name, series, normalized_name, normalized_series):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO characters "
            "(id, name, series, normalized_name, normalized_series, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'now', 'now')",
            (character_id, name, series, normalized_name, normalized_series),
        )


def _insert_context(
    path,
    server_id,
    server_name,
    server_normalized,
    account_id,
    account_name,
    account_normalized,
):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO server_contexts "
            "(id, name, normalized_name, created_at, updated_at) "
            "VALUES (?, ?, ?, 'now', 'now')",
            (server_id, server_name, server_normalized),
        )
        connection.execute(
            "INSERT INTO account_contexts "
            "(id, server_context_id, name, normalized_name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'now', 'now')",
            (account_id, server_id, account_name, account_normalized),
        )


def _rebuild_without_singular_constraints(path, table):
    """TEST ONLY: rebuild one table after removing its singular constraints."""
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        create_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()[0]
        original_columns = [
            row[1] for row in connection.execute(f"PRAGMA table_info({table})")
        ]
        replacement = f"__dh03_{table}"
        rebuilt_sql = create_sql.replace(
            f"CREATE TABLE {table}", f"CREATE TABLE {replacement}", 1
        )
        rebuilt_sql = re.sub(r",\s*UNIQUE\s*\([^)]*\)", "", rebuilt_sql)
        rebuilt_sql = re.sub(r",\s*PRIMARY KEY\s*\([^)]*\)", "", rebuilt_sql)
        rebuilt_sql = re.sub(r"\s+UNIQUE(?=\s*(?:,|\n|$))", "", rebuilt_sql)
        if "id" not in original_columns:
            rebuilt_sql = re.sub(r"\s+PRIMARY KEY(?=\s|,|\n)", "", rebuilt_sql)
        connection.execute(rebuilt_sql)
        column_list = ", ".join(original_columns)
        connection.execute(
            f"INSERT INTO {replacement} ({column_list}) "
            f"SELECT {column_list} FROM {table}"
        )
        connection.execute(f"DROP TABLE {table}")
        connection.execute(f"ALTER TABLE {replacement} RENAME TO {table}")


def _drop_index(path, index):
    with sqlite3.connect(path) as connection:
        connection.execute(f"DROP INDEX {index}")


def _insert_aggregate(path, aggregate_id, message_id):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO discord_message_aggregates "
            "(id, platform, guild_id, channel_id, message_id, first_received_at, "
            "last_received_at, created_at, updated_at) "
            "VALUES (?, 'discord', 'guild', 'channel', ?, 'now', 'now', 'now', 'now')",
            (aggregate_id, message_id),
        )


def _insert_revision(
    path,
    revision_id,
    aggregate_id,
    payload_hash,
    *,
    marker=None,
    state="candidate",
):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO discord_message_revisions "
            "(id, aggregate_id, source_revision_marker, normalized_payload_hash, "
            "revision_state, first_received_at, last_received_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'now', 'now', 'now', 'now')",
            (revision_id, aggregate_id, marker, payload_hash, state),
        )


def _insert_source_event(
    path,
    event_id,
    event_key,
    revision_id,
    *,
    status="received",
    legacy_import_event_id=None,
):
    with sqlite3.connect(path) as connection:
        if legacy_import_event_id is not None:
            connection.execute(
                "INSERT INTO import_events (id, kind, source, observed_at, raw_message) "
                "VALUES (?, 'test', 'test', 'now', 'test')",
                (legacy_import_event_id,),
            )
        connection.execute(
            "INSERT INTO discord_source_events "
            "(id, event_key, revision_id, event_kind, status, raw_text, received_at, "
            "last_seen_at, legacy_import_event_id, created_at, updated_at) "
            "VALUES (?, ?, ?, 'message_create', ?, 'safe', 'now', 'now', ?, 'now', 'now')",
            (event_id, event_key, revision_id, status, legacy_import_event_id),
        )


def _insert_projection_link(
    path,
    source_event_id,
    projection_kind,
    projection_slot,
    *,
    state="completed",
    projection_table="missing_table",
    projection_row_id=999,
):
    with sqlite3.connect(path) as connection:
        if state == "completed":
            connection.execute(
                "INSERT INTO discord_projection_links "
                "(source_event_id, projection_kind, projection_slot, projection_table, "
                "projection_row_id, state, claimed_at, completed_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'completed', 'now', 'now', 'now', 'now')",
                (
                    source_event_id,
                    projection_kind,
                    projection_slot,
                    projection_table,
                    projection_row_id,
                ),
            )
        else:
            connection.execute(
                "INSERT INTO discord_projection_links "
                "(source_event_id, projection_kind, projection_slot, state, claimed_at, "
                "created_at, updated_at) VALUES (?, ?, ?, 'claimed', 'now', 'now', 'now')",
                (source_event_id, projection_kind, projection_slot),
            )


def _insert_profile_observation(path, observation_id=999):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO profile_observations "
            "(id, account_context_id, profile_name, collection_size, female_percent, "
            "male_percent, pokedex_json, kakera_reacts_json, bronze_keys, silver_keys, "
            "gold_keys, spheres_json, displayed_badges_json, observed_at, import_event_id) "
            "VALUES (?, 1, 'profile', 0, 0, 0, '{}', '[]', 0, 0, 0, '[]', '[]', 'now', 1)",
            (observation_id,),
        )


def _insert_import_event(path, import_event_id):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO import_events (id, kind, source, observed_at, raw_message) "
            "VALUES (?, 'test', 'test', 'now', 'test')",
            (import_event_id,),
        )


def _seed_generic_target_dependencies(path):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO characters "
            "(id, name, series, normalized_name, normalized_series, created_at, updated_at) "
            "VALUES (1, 'Character', 'Series', 'character', 'series', 'now', 'now')"
        )
        connection.execute(
            "INSERT OR IGNORE INTO harem_scans "
            "(id, account_context_id, expected_page_count, started_at, scan_kind) "
            "VALUES (1, 1, 1, 'now', 'keys')"
        )


def _insert_generic_projection_target(path, table, row_id, import_event_id):
    _seed_generic_target_dependencies(path)
    with sqlite3.connect(path) as connection:
        columns = []
        values = []
        for column in connection.execute(f"PRAGMA table_info({table})"):
            name, column_type, not_null, default = column[1], column[2], column[3], column[4]
            if name in {"id", "import_event_id"} or not not_null or default is not None:
                continue
            columns.append(name)
            if name.endswith("_id"):
                values.append(1)
            elif "INT" in column_type.upper():
                values.append(0)
            elif "REAL" in column_type.upper() or "FLOA" in column_type.upper():
                values.append(0.0)
            else:
                values.append("test")
        columns = ["id", *columns, "import_event_id"]
        values = [row_id, *values, import_event_id]
        placeholders = ", ".join("?" for _ in values)
        connection.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )


def _seed_succeeded_projection_source(path, event_id=1):
    _insert_aggregate(path, event_id, f"message-{event_id}")
    _insert_revision(path, event_id, event_id, f"hash-{event_id}")
    _insert_source_event(
        path,
        event_id,
        f"event-{event_id}",
        event_id,
        status="succeeded",
        legacy_import_event_id=event_id,
    )


def _insert_harem_scan(path, scan_id, account_context_id=1, *, scan_kind="keys"):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO harem_scans "
            "(id, account_context_id, expected_page_count, started_at, scan_kind) "
            "VALUES (?, ?, 1, 'now', ?)",
            (scan_id, account_context_id, scan_kind),
        )


def test_healthy_catalog_has_no_findings_and_preserves_database_state(tmp_path):
    database_path = tmp_path / "catalog.db"
    _initialize(database_path)
    with sqlite3.connect(database_path) as connection:
        before = connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()

    assert DataHealthService(database_path).find_orphans() == ()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall() == before


def test_physical_foreign_key_orphan_is_reported_separately(tmp_path):
    database_path = tmp_path / "catalog.db"
    _initialize(database_path)
    _seed_base_rows(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            "INSERT INTO claim_observations "
            "(id, account_context_id, character_id, character_name, "
            "normalized_character_name, observed_at, import_event_id) "
            "VALUES (7, 999, NULL, 'unknown', 'unknown', 'now', 1)"
        )

    findings = DataHealthService(database_path).find_orphans()

    assert len(findings) == 1
    assert findings[0].check_id == "DH-ORPH-001"
    assert findings[0].entity == "claim_observations"
    assert findings[0].local_identifier.startswith("rowid=7;")


def test_kakera_account_orphan_is_reported_without_foreign_key_check(tmp_path):
    database_path = tmp_path / "catalog.db"
    _initialize(database_path)
    _seed_base_rows(database_path)
    _insert_reaction(database_path, 20, 999, 1)

    findings = DataHealthService(database_path).find_orphans()

    assert [(finding.check_id, finding.local_identifier) for finding in findings] == [
        ("DH-ORPH-002", 20)
    ]


def test_kakera_import_orphan_is_reported_without_foreign_key_check(tmp_path):
    database_path = tmp_path / "catalog.db"
    _initialize(database_path)
    _seed_base_rows(database_path)
    _insert_reaction(database_path, 30, 1, 999)

    findings = DataHealthService(database_path).find_orphans()

    assert [(finding.check_id, finding.local_identifier) for finding in findings] == [
        ("DH-ORPH-003", 30)
    ]


def test_multiple_findings_are_sorted_by_check_and_numeric_identifier(tmp_path):
    database_path = tmp_path / "catalog.db"
    _initialize(database_path)
    _seed_base_rows(database_path)
    _insert_reaction(database_path, 20, 999, 1)
    _insert_reaction(database_path, 3, 999, 1)
    _insert_reaction(database_path, 30, 1, 999)

    findings = DataHealthService(database_path).find_orphans()

    assert [(finding.check_id, finding.local_identifier) for finding in findings] == [
        ("DH-ORPH-002", 3),
        ("DH-ORPH-002", 20),
        ("DH-ORPH-003", 30),
    ]


def test_nullable_character_reference_is_not_an_orphan(tmp_path):
    database_path = tmp_path / "catalog.db"
    _initialize(database_path)
    _seed_base_rows(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO claim_observations "
            "(id, account_context_id, character_id, character_name, "
            "normalized_character_name, observed_at, import_event_id) "
            "VALUES (8, 1, NULL, 'historical name', 'historical name', 'now', 1)"
        )

    assert DataHealthService(database_path).find_orphans() == ()


def test_stale_migration_metadata_fails_schema_preflight_without_migration(tmp_path):
    database_path = tmp_path / "catalog.db"
    _initialize(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE schema_migrations SET name = 'stale' WHERE version = 11"
        )

    with pytest.raises(DataHealthSchemaError, match="migration metadata"):
        DataHealthService(database_path).find_orphans()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT name FROM schema_migrations WHERE version = 11"
        ).fetchone()[0] == "stale"


def test_unexpected_application_table_fails_dh01_schema_preflight_with_validator_parity(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "catalog.db"
    _initialize(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE unexpected_dh01_table (id INTEGER PRIMARY KEY)"
        )

    with sqlite3.connect(database_path) as connection:
        with pytest.raises(MigrationError, match="unexpected tables: unexpected_dh01_table"):
            validate_current_catalog_schema(connection)

    with pytest.raises(
        DataHealthSchemaError,
        match="unexpected tables: unexpected_dh01_table",
    ):
        DataHealthService(database_path).find_orphans()

    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)
    result = CliRunner().invoke(main.app, ["catalog", "data-health", "orphans"])

    assert result.exit_code == 1
    assert "unexpected_dh01_table" in result.stdout
    assert "DH-ORPH-" not in result.stdout


def test_read_only_opening_does_not_create_database_or_parent_or_enable_wal(tmp_path):
    database_path = tmp_path / "existing" / "catalog.db"
    database_path.parent.mkdir()
    with sqlite3.connect(database_path.parent / "seed.db") as connection:
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("CREATE TABLE marker (value INTEGER)")
    seed_path = database_path.parent / "seed.db"

    with connect_read_only(seed_path) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"

    missing_path = tmp_path / "not-created" / "catalog.db"
    with pytest.raises(sqlite3.OperationalError):
        connect_read_only(missing_path)
    assert not missing_path.exists()
    assert not missing_path.parent.exists()


def test_cli_healthy_database_succeeds(tmp_path, monkeypatch):
    database_path = tmp_path / "catalog.db"
    _initialize(database_path)
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    result = CliRunner().invoke(main.app, ["catalog", "data-health", "orphans"])

    assert result.exit_code == 0
    assert "No data-health findings." in result.stdout


def test_cli_findings_database_reports_findings_and_total(tmp_path, monkeypatch):
    database_path = tmp_path / "catalog.db"
    _initialize(database_path)
    _seed_base_rows(database_path)
    _insert_reaction(database_path, 20, 999, 1)
    _insert_reaction(database_path, 3, 1, 999)
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    result = CliRunner().invoke(main.app, ["catalog", "data-health", "orphans"])

    assert result.exit_code == 0
    assert result.stdout.index("DH-ORPH-002") < result.stdout.index("DH-ORPH-003")
    assert "Total findings: 2" in result.stdout
    assert "raw_message" not in result.stdout


def test_cli_unavailable_database_fails_without_creating_path(tmp_path, monkeypatch):
    database_path = tmp_path / "missing" / "catalog.db"
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    result = CliRunner().invoke(main.app, ["catalog", "data-health", "orphans"])

    assert result.exit_code == 1
    assert not database_path.exists()
    assert not database_path.parent.exists()
    assert "Traceback" not in result.stdout


def test_cli_unrecognized_database_fails_concisely(tmp_path, monkeypatch):
    database_path = tmp_path / "not-moa.db"
    database_path.touch()
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    result = CliRunner().invoke(main.app, ["catalog", "data-health", "orphans"])

    assert result.exit_code == 1
    assert "Unrecognized MOA catalog schema" in result.stdout
    assert "Traceback" not in result.stdout


def test_impossible_identities_healthy_database_has_no_findings(tmp_path):
    database_path = tmp_path / "catalog.db"
    _initialize(database_path)
    _seed_attribution(database_path)
    _insert_character(database_path, 1, "Character", "Series", "character", "series")
    _insert_context(database_path, 1, "Server", "server", 1, "Account", "account")

    assert DataHealthService(database_path).find_impossible_identities() == ()


@pytest.mark.parametrize("status", ["unresolved", "ambiguous"])
def test_non_resolved_account_attribution_is_not_impossible_identity(tmp_path, status):
    database_path = tmp_path / f"{status}.db"
    _initialize(database_path)
    _seed_attribution(database_path, account_status=status)

    assert DataHealthService(database_path).find_impossible_identities() == ()


@pytest.mark.parametrize("status", ["unresolved", "ambiguous"])
def test_resolved_account_with_non_resolved_server_is_dh_id_001(tmp_path, status):
    database_path = tmp_path / f"server-{status}.db"
    _initialize(database_path)
    _seed_attribution(database_path, server_status=status)

    findings = DataHealthService(database_path).find_impossible_identities()

    assert [(finding.check_id, finding.local_identifier) for finding in findings] == [
        ("DH-ID-001", 1)
    ]


def test_resolved_account_without_server_attribution_is_dh_id_001(tmp_path):
    database_path = tmp_path / "missing-server.db"
    _initialize(database_path)
    _seed_attribution(database_path, server_status=None)

    findings = DataHealthService(database_path).find_impossible_identities()

    assert [(finding.check_id, finding.local_identifier) for finding in findings] == [
        ("DH-ID-001", 1)
    ]


def test_resolved_attribution_server_identity_mismatch_is_dh_id_001(tmp_path):
    database_path = tmp_path / "attribution-mismatch.db"
    _initialize(database_path)
    _seed_attribution(database_path, account_server_name="Other Server")

    findings = DataHealthService(database_path).find_impossible_identities()

    assert [(finding.check_id, finding.local_identifier) for finding in findings] == [
        ("DH-ID-001", 1)
    ]


def test_character_normalization_and_series_scope(tmp_path):
    database_path = tmp_path / "characters.db"
    _initialize(database_path)
    _insert_character(database_path, 1, "Same Name", "Series A", "same name", "series a")
    _insert_character(database_path, 2, "Same Name", "Series B", "same name", "series b")
    _insert_character(database_path, 3, "Broken", "Series C", "wrong", "series c")

    findings = DataHealthService(database_path).find_impossible_identities()

    assert [(finding.entity, finding.local_identifier) for finding in findings] == [
        ("characters", 3)
    ]


def test_server_normalization_is_checked_per_row(tmp_path):
    database_path = tmp_path / "servers.db"
    _initialize(database_path)
    _insert_context(database_path, 1, "Server One", "server one", 1, "Account", "account")
    _insert_context(database_path, 2, "Server Two", "wrong", 2, "Account", "account")

    findings = DataHealthService(database_path).find_impossible_identities()

    assert [(finding.entity, finding.local_identifier) for finding in findings] == [
        ("server_contexts", 2)
    ]


def test_account_normalization_preserves_server_scoping(tmp_path):
    database_path = tmp_path / "accounts.db"
    _initialize(database_path)
    _insert_context(database_path, 1, "Server One", "server one", 1, "Shared", "shared")
    _insert_context(database_path, 2, "Server Two", "server two", 2, "Shared", "shared")
    _insert_context(database_path, 3, "Server Three", "server three", 3, "Shared", "wrong")

    findings = DataHealthService(database_path).find_impossible_identities()

    assert [(finding.entity, finding.local_identifier) for finding in findings] == [
        ("account_contexts", 3)
    ]


def test_nullable_character_observation_is_not_an_impossible_identity(tmp_path):
    database_path = tmp_path / "nullable-observation.db"
    _initialize(database_path)
    _seed_base_rows(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO claim_observations "
            "(id, account_context_id, character_id, character_name, "
            "normalized_character_name, observed_at, import_event_id) "
            "VALUES (8, 1, NULL, 'historical name', 'historical name', 'now', 1)"
        )

    assert DataHealthService(database_path).find_impossible_identities() == ()


def test_multiple_impossible_identity_findings_are_deterministically_sorted(tmp_path):
    database_path = tmp_path / "multiple.db"
    _initialize(database_path)
    _seed_attribution(database_path, suffix="one", account_server_name="Other")
    _seed_attribution(database_path, suffix="two", server_status=None)
    _insert_character(database_path, 2, "Broken Character", "Series", "wrong", "series")
    _insert_context(database_path, 2, "Broken Server", "wrong", 2, "Broken Account", "wrong")

    findings = DataHealthService(database_path).find_impossible_identities()

    assert [
        (finding.check_id, finding.entity, finding.local_identifier) for finding in findings
    ] == [
        ("DH-ID-001", "discord_source_event_account_attributions", 1),
        ("DH-ID-001", "discord_source_event_account_attributions", 2),
        ("DH-ID-002", "account_contexts", 2),
        ("DH-ID-002", "characters", 2),
        ("DH-ID-002", "server_contexts", 2),
    ]


def test_impossible_identity_scan_is_category_isolated_and_excludes_projection_links(tmp_path):
    database_path = tmp_path / "isolation.db"
    _initialize(database_path)
    _seed_attribution(database_path, account_server_name="Other")

    assert all(
        not finding.check_id.startswith("DH-ID-")
        for finding in DataHealthService(database_path).find_orphans()
    )
    assert all(
        not finding.check_id.startswith("DH-ORPH-")
        for finding in DataHealthService(database_path).find_impossible_identities()
    )
    assert all(
        not finding.check_id.startswith(("DH-ID-", "DH-DUP-"))
        for finding in DataHealthService(database_path).find_orphans()
    )


def test_cli_impossible_identities_healthy_database_succeeds(tmp_path, monkeypatch):
    database_path = tmp_path / "catalog.db"
    _initialize(database_path)
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    result = CliRunner().invoke(main.app, ["catalog", "data-health", "impossible-identities"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "No data-health findings."


def test_cli_impossible_identities_reports_findings_in_order(tmp_path, monkeypatch):
    database_path = tmp_path / "catalog.db"
    _initialize(database_path)
    _seed_attribution(database_path, account_server_name="Other")
    _insert_character(database_path, 2, "Broken Character", "Series", "wrong", "series")
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    result = CliRunner().invoke(main.app, ["catalog", "data-health", "impossible-identities"])

    assert result.exit_code == 0
    assert result.stdout.index("DH-ID-001") < result.stdout.index("DH-ID-002")
    assert "Total findings: 2" in result.stdout
    assert "raw_message" not in result.stdout


def test_cli_impossible_identities_invalid_schema_fails(tmp_path, monkeypatch):
    database_path = tmp_path / "not-moa.db"
    database_path.touch()
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    result = CliRunner().invoke(main.app, ["catalog", "data-health", "impossible-identities"])

    assert result.exit_code == 1
    assert "Unrecognized MOA catalog schema" in result.stdout


def test_cli_impossible_identities_unavailable_database_fails_without_creation(tmp_path, monkeypatch):
    database_path = tmp_path / "missing" / "catalog.db"
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    result = CliRunner().invoke(main.app, ["catalog", "data-health", "impossible-identities"])

    assert result.exit_code == 1
    assert not database_path.exists()
    assert not database_path.parent.exists()


def test_data_health_has_no_all_command(tmp_path, monkeypatch):
    database_path = tmp_path / "catalog.db"
    _initialize(database_path)
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    result = CliRunner().invoke(main.app, ["catalog", "data-health", "all"])

    assert result.exit_code != 0


def test_duplicates_cover_character_server_and_server_scoped_account_keys(tmp_path):
    database_path = tmp_path / "catalog.db"
    _initialize(database_path)
    _rebuild_without_singular_constraints(database_path, "characters")
    _rebuild_without_singular_constraints(database_path, "server_contexts")
    _rebuild_without_singular_constraints(database_path, "account_contexts")
    _insert_character(database_path, 1, "Same", "Series", "same", "series")
    _insert_character(database_path, 2, "Same", "Series", "same", "series")
    _insert_context(database_path, 1, "Server", "server", 1, "Account", "account")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO server_contexts "
            "(id, name, normalized_name, created_at, updated_at) "
            "VALUES (2, 'Other', 'server', 'now', 'now')"
        )
        connection.execute(
            "INSERT INTO account_contexts "
            "(id, server_context_id, name, normalized_name, created_at, updated_at) "
            "VALUES (2, 1, 'Account', 'account', 'now', 'now')"
        )

    findings = DataHealthService(database_path).find_duplicates()

    assert [(finding.check_id, finding.entity) for finding in findings] == [
        ("DH-DUP-001", "characters"),
        ("DH-DUP-002", "server_contexts"),
        ("DH-DUP-003", "account_contexts"),
    ]
    assert "2 rows" in findings[0].reason


def test_duplicate_aggregate_identity_excludes_delivery_count(tmp_path):
    database_path = tmp_path / "aggregate.db"
    _initialize(database_path)
    _rebuild_without_singular_constraints(database_path, "discord_message_aggregates")
    _insert_aggregate(database_path, 1, "message")
    _insert_aggregate(database_path, 2, "message")

    findings = DataHealthService(database_path).find_duplicates()

    assert [(finding.check_id, finding.entity) for finding in findings] == [
        ("DH-DUP-004", "discord_message_aggregates")
    ]


@pytest.mark.parametrize(
    ("index", "marker", "state", "reason"),
    [
        (
            "uq_discord_revision_versioned",
            "marker",
            "candidate",
            "marker-present revision identity",
        ),
        (
            "uq_discord_revision_unversioned",
            None,
            "candidate",
            "marker-absent revision identity",
        ),
        (
            "uq_discord_active_revision",
            "active-marker",
            "active",
            "single active revision identity",
        ),
    ],
)
def test_duplicate_revision_branches_reproduce_partial_index_predicates(
    tmp_path, index, marker, state, reason
):
    database_path = tmp_path / f"{index}.db"
    _initialize(database_path)
    _drop_index(database_path, index)
    _insert_aggregate(database_path, 1, "message")
    _insert_revision(database_path, 1, 1, "hash", marker=marker, state=state)
    second_marker = "active-marker-2" if state == "active" else marker
    second_hash = "hash-2" if state == "active" else "hash"
    _insert_revision(
        database_path,
        2,
        1,
        second_hash,
        marker=second_marker,
        state=state,
    )

    findings = DataHealthService(database_path).find_duplicates()

    assert len(findings) == 1
    assert findings[0].check_id == "DH-DUP-005"
    assert reason in findings[0].reason


def test_duplicate_source_event_keys_and_revision_ids_are_separate_identities(tmp_path):
    database_path = tmp_path / "source-events.db"
    _initialize(database_path)
    _rebuild_without_singular_constraints(database_path, "discord_source_events")
    for aggregate_id in range(1, 4):
        _insert_aggregate(database_path, aggregate_id, f"message-{aggregate_id}")
        _insert_revision(database_path, aggregate_id, aggregate_id, f"hash-{aggregate_id}")
    _insert_source_event(database_path, 1, "same-event-key", 1)
    _insert_source_event(database_path, 2, "same-event-key", 2)
    _insert_source_event(database_path, 3, "other-event-key", 3)
    _insert_source_event(database_path, 4, "third-event-key", 3)

    findings = DataHealthService(database_path).find_duplicates()

    assert len(findings) == 2
    assert all(finding.check_id == "DH-DUP-006" for finding in findings)
    assert {"event_key", "revision_id"} == {
        finding.local_identifier.split("=")[0] for finding in findings
    }


def test_duplicate_processing_attempt_identity_and_one_active_state(tmp_path):
    database_path = tmp_path / "attempts.db"
    _initialize(database_path)
    _rebuild_without_singular_constraints(database_path, "discord_processing_attempts")
    _insert_aggregate(database_path, 1, "message")
    _insert_revision(database_path, 1, 1, "hash")
    _insert_source_event(database_path, 1, "event", 1)
    with sqlite3.connect(database_path) as connection:
        rows = [
            (1, 1, 1, "succeeded"),
            (2, 1, 1, "failed"),
            (3, 1, 2, "processing"),
            (4, 1, 3, "processing"),
        ]
        connection.executemany(
            "INSERT INTO discord_processing_attempts "
            "(id, source_event_id, attempt_number, status, retryable, parser_version, "
            "router_version, started_at, created_at) "
            "VALUES (?, ?, ?, ?, 0, 'parser', 'router', 'now', 'now')",
            rows,
        )

    findings = DataHealthService(database_path).find_duplicates()

    assert len(findings) == 2
    assert all(finding.check_id == "DH-DUP-007" for finding in findings)
    assert any("attempt identity" in finding.reason for finding in findings)
    assert any("active processing" in finding.reason for finding in findings)


def test_duplicate_server_and_account_attribution_rows_are_independent(tmp_path):
    database_path = tmp_path / "attributions.db"
    _initialize(database_path)
    _rebuild_without_singular_constraints(
        database_path, "discord_source_event_server_attributions"
    )
    _rebuild_without_singular_constraints(
        database_path, "discord_source_event_account_attributions"
    )
    _insert_aggregate(database_path, 1, "message")
    _insert_revision(database_path, 1, 1, "hash")
    _insert_source_event(database_path, 1, "event", 1)
    with sqlite3.connect(database_path) as connection:
        connection.executemany(
            "INSERT INTO discord_source_event_server_attributions "
            "(source_event_id, status, server_name, created_at, updated_at) "
            "VALUES (?, 'resolved', 'Server', 'now', 'now')",
            [(1,), (1,)],
        )
        connection.executemany(
            "INSERT INTO discord_source_event_account_attributions "
            "(source_event_id, status, server_name, account_name, created_at, updated_at) "
            "VALUES (?, 'resolved', 'Server', 'Account', 'now', 'now')",
            [(1,), (1,)],
        )

    findings = DataHealthService(database_path).find_duplicates()

    assert [(finding.check_id, finding.entity) for finding in findings] == [
        ("DH-DUP-008", "discord_source_event_account_attributions"),
        ("DH-DUP-008", "discord_source_event_server_attributions"),
    ]


def test_duplicate_projection_link_identity_is_the_only_projection_check(tmp_path):
    database_path = tmp_path / "projection-links.db"
    _initialize(database_path)
    _rebuild_without_singular_constraints(database_path, "discord_projection_links")
    _insert_aggregate(database_path, 1, "message")
    _insert_revision(database_path, 1, 1, "hash")
    _insert_source_event(database_path, 1, "event", 1)
    with sqlite3.connect(database_path) as connection:
        connection.executemany(
            "INSERT INTO discord_projection_links "
            "(source_event_id, projection_kind, projection_slot, projection_table, "
            "projection_row_id, state, claimed_at, created_at, updated_at) "
            "VALUES (1, 'odd-kind', 'slot', NULL, NULL, 'claimed', 'now', 'now', 'now')",
            [(), ()],
        )

    findings = DataHealthService(database_path).find_duplicates()

    assert [(finding.check_id, finding.entity) for finding in findings] == [
        ("DH-DUP-009", "discord_projection_links")
    ]


def test_projection_gap_healthy_normal_source_lifecycle_has_no_findings(tmp_path):
    database_path = tmp_path / "projection-gap-healthy.db"
    _initialize(database_path)
    repository = DiscordMessageRepository(database_path)
    aggregate_key = MessageAggregateKey(
        SourcePlatform.DISCORD, "guild", "channel", "healthy-message"
    )
    received = repository.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(aggregate_key, "healthy-payload", "revision-1"),
        event_key="healthy-event",
        event_kind="message_create",
        raw_text="health test",
        payload_json='{"content":"health test"}',
        payload_capture_version="test",
        source_observed_at=OBSERVED_AT,
        received_at=OBSERVED_AT,
    )
    attempt = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser",
        router_version="router",
        started_at=OBSERVED_AT,
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO import_events (id, kind, source, observed_at, raw_message) "
            "VALUES (1, 'test', 'test', 'now', 'test')"
        )
    repository.mark_processing_success(
        source_event_id=received.source_event_id,
        attempt_id=attempt.attempt_id,
        finished_at=OBSERVED_AT,
        legacy_import_event_id=1,
    )
    _insert_profile_observation(database_path)
    _insert_projection_link(
        database_path,
        received.source_event_id,
        "catalog.profile",
        "slot",
        projection_table="profile_observations",
    )

    assert DataHealthService(database_path).find_projection_gaps() == ()


@pytest.mark.parametrize(
    "status", ["received", "processing", "succeeded", "failed", "unresolved_attribution"]
)
def test_projection_gap_reports_each_current_source_status_for_claimed_links(tmp_path, status):
    database_path = tmp_path / f"projection-gap-{status}.db"
    _initialize(database_path)
    _insert_aggregate(database_path, 1, "message")
    _insert_revision(database_path, 1, 1, "hash")
    _insert_source_event(database_path, 1, "event", 1, status=status)
    _insert_projection_link(database_path, 1, "kind", "slot", state="claimed")

    findings = DataHealthService(database_path).find_projection_gaps()

    assert [(finding.check_id, finding.local_identifier) for finding in findings] == [
        ("DH-PG-002", 1)
    ]
    assert "durably claimed projection link(s)" in findings[0].reason


def test_projection_gap_groups_completed_links_per_source_event(tmp_path):
    database_path = tmp_path / "projection-gap-grouping.db"
    _initialize(database_path)
    _insert_aggregate(database_path, 1, "message")
    _insert_revision(database_path, 1, 1, "hash")
    _insert_source_event(database_path, 1, "event", 1, status="failed")
    _insert_projection_link(database_path, 1, "kind-a", "slot-a")
    _insert_projection_link(database_path, 1, "kind-b", "slot-b")

    findings = DataHealthService(database_path).find_projection_gaps()

    assert len(findings) == 1
    assert findings[0].check_id == "DH-PG-001"
    assert findings[0].local_identifier == 1
    assert "2 completed projection link(s)" in findings[0].reason


def test_projection_gap_preserves_retry_history_for_current_success(tmp_path):
    database_path = tmp_path / "projection-gap-retry.db"
    _initialize(database_path)
    _insert_aggregate(database_path, 1, "message")
    _insert_revision(database_path, 1, 1, "hash")
    _insert_source_event(
        database_path, 1, "event", 1, status="succeeded", legacy_import_event_id=1
    )
    with sqlite3.connect(database_path) as connection:
        connection.executemany(
            "INSERT INTO discord_processing_attempts "
            "(source_event_id, attempt_number, status, retryable, parser_version, "
            "router_version, started_at, finished_at, created_at) "
            "VALUES (1, ?, ?, ?, 'parser', 'router', 'now', 'now', 'now')",
            [(1, "failed", 1), (2, "succeeded", 0)],
        )
    _insert_projection_link(
        database_path, 1, "catalog.profile", "slot", projection_table="profile_observations"
    )
    _insert_profile_observation(database_path)

    assert DataHealthService(database_path).find_projection_gaps() == ()


def test_projection_gap_reports_claimed_link_with_active_processing_attempt(tmp_path):
    database_path = tmp_path / "projection-gap-claimed-active-attempt.db"
    _initialize(database_path)
    _insert_aggregate(database_path, 1, "message")
    _insert_revision(database_path, 1, 1, "hash")
    _insert_source_event(database_path, 1, "event", 1, status="processing")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO discord_processing_attempts "
            "(source_event_id, attempt_number, status, retryable, parser_version, "
            "router_version, started_at, created_at) "
            "VALUES (1, 1, 'processing', 0, 'parser', 'router', 'now', 'now')"
        )
    _insert_projection_link(database_path, 1, "kind", "slot", state="claimed")

    findings = DataHealthService(database_path).find_projection_gaps()

    assert [(finding.check_id, finding.local_identifier) for finding in findings] == [
        ("DH-PG-002", 1)
    ]


def test_projection_gap_groups_claimed_links_per_source_event(tmp_path):
    database_path = tmp_path / "projection-gap-claimed-grouping.db"
    _initialize(database_path)
    _insert_aggregate(database_path, 1, "message-1")
    _insert_revision(database_path, 1, 1, "hash-1")
    _insert_source_event(database_path, 1, "event-1", 1, status="succeeded")
    _insert_projection_link(database_path, 1, "kind-a", "slot-a", state="claimed")
    _insert_projection_link(database_path, 1, "kind-b", "slot-b", state="claimed")

    findings = DataHealthService(database_path).find_projection_gaps()

    assert len(findings) == 1
    assert findings[0].check_id == "DH-PG-002"
    assert findings[0].local_identifier == 1
    assert "2 durably claimed projection link(s)" in findings[0].reason


def test_projection_gap_reports_claimed_links_per_source_in_deterministic_order(tmp_path):
    database_path = tmp_path / "projection-gap-claimed-sources.db"
    _initialize(database_path)
    for event_id in (1, 2):
        _insert_aggregate(database_path, event_id, f"message-{event_id}")
        _insert_revision(database_path, event_id, event_id, f"hash-{event_id}")
        _insert_source_event(database_path, event_id, f"event-{event_id}", event_id, status="failed")
        _insert_projection_link(database_path, event_id, "kind", "slot", state="claimed")

    findings = DataHealthService(database_path).find_projection_gaps()

    assert [(finding.check_id, finding.local_identifier) for finding in findings] == [
        ("DH-PG-002", 1),
        ("DH-PG-002", 2),
    ]


def test_projection_gap_reports_succeeded_completed_source_without_import_provenance(tmp_path):
    database_path = tmp_path / "projection-gap-null-legacy-import.db"
    _initialize(database_path)
    _insert_aggregate(database_path, 1, "message")
    _insert_revision(database_path, 1, 1, "hash")
    _insert_source_event(database_path, 1, "event", 1, status="succeeded")
    _insert_projection_link(database_path, 1, "kind", "slot", state="completed")

    findings = DataHealthService(database_path).find_projection_gaps()

    assert [(finding.check_id, finding.local_identifier) for finding in findings] == [
        ("DH-PG-003", 1)
    ]
    assert "1 completed projection link(s)" in findings[0].reason


def test_projection_gap_groups_null_import_provenance_per_source_event(tmp_path):
    database_path = tmp_path / "projection-gap-null-legacy-import-grouping.db"
    _initialize(database_path)
    _insert_aggregate(database_path, 1, "message")
    _insert_revision(database_path, 1, 1, "hash")
    _insert_source_event(database_path, 1, "event", 1, status="succeeded")
    _insert_projection_link(database_path, 1, "kind-a", "slot-a")
    _insert_projection_link(database_path, 1, "kind-b", "slot-b")

    findings = DataHealthService(database_path).find_projection_gaps()

    assert len(findings) == 1
    assert findings[0].check_id == "DH-PG-003"
    assert findings[0].local_identifier == 1
    assert "2 completed projection link(s)" in findings[0].reason


def test_projection_gap_excludes_succeeded_source_with_zero_links(tmp_path):
    database_path = tmp_path / "projection-gap-zero-links.db"
    _initialize(database_path)
    _insert_aggregate(database_path, 1, "message")
    _insert_revision(database_path, 1, 1, "hash")
    _insert_source_event(database_path, 1, "event", 1, status="succeeded")

    assert DataHealthService(database_path).find_projection_gaps() == ()


def test_projection_gap_reports_missing_authorized_target_for_succeeded_source(tmp_path):
    database_path = tmp_path / "projection-gap-missing-target.db"
    _initialize(database_path)
    _insert_aggregate(database_path, 1, "message")
    _insert_revision(database_path, 1, 1, "hash")
    _insert_source_event(
        database_path, 1, "event", 1, status="succeeded", legacy_import_event_id=1
    )
    _insert_projection_link(
        database_path,
        1,
        "catalog.profile",
        "slot",
        projection_table="profile_observations",
    )

    findings = DataHealthService(database_path).find_projection_gaps()

    assert len(findings) == 1
    assert findings[0].check_id == "DH-PG-005"
    assert findings[0].entity == "discord_projection_links"
    assert "profile_observations" in findings[0].reason
    assert "999" in findings[0].reason


def test_projection_gap_reports_unknown_projection_kind(tmp_path):
    database_path = tmp_path / "projection-gap-unknown-kind.db"
    _initialize(database_path)
    _seed_succeeded_projection_source(database_path)
    _insert_projection_link(
        database_path,
        1,
        "catalog.unknown",
        "slot",
        projection_table="arbitrary_table",
    )

    findings = DataHealthService(database_path).find_projection_gaps()

    assert len(findings) == 1
    assert findings[0].check_id == "DH-PG-004"
    assert findings[0].entity == "discord_projection_links"
    assert "unknown to the shared projection authority" in findings[0].reason
    assert "arbitrary_table" not in findings[0].reason


def test_projection_gap_reports_known_kind_with_wrong_authorized_table(tmp_path):
    database_path = tmp_path / "projection-gap-wrong-table.db"
    _initialize(database_path)
    _seed_succeeded_projection_source(database_path)
    _insert_projection_link(
        database_path,
        1,
        "catalog.profile",
        "slot",
        projection_table="other_table",
    )

    findings = DataHealthService(database_path).find_projection_gaps()

    assert len(findings) == 1
    assert findings[0].check_id == "DH-PG-004"
    assert "profile_observations" in findings[0].reason
    assert "other_table" in findings[0].reason


def test_projection_gap_unknown_kind_takes_precedence_over_wrong_table(tmp_path):
    database_path = tmp_path / "projection-gap-unknown-precedence.db"
    _initialize(database_path)
    _seed_succeeded_projection_source(database_path)
    _insert_projection_link(
        database_path,
        1,
        "catalog.unknown",
        "slot",
        projection_table="other_table",
    )

    findings = DataHealthService(database_path).find_projection_gaps()

    assert len(findings) == 1
    assert findings[0].check_id == "DH-PG-004"
    assert "unknown to the shared projection authority" in findings[0].reason
    assert "authorizes target table" not in findings[0].reason


def test_projection_gap_reports_one_finding_per_bad_link_deterministically(tmp_path):
    database_path = tmp_path / "projection-gap-multiple-bad-links.db"
    _initialize(database_path)
    _seed_succeeded_projection_source(database_path)
    _insert_projection_link(
        database_path,
        1,
        "catalog.unknown",
        "unknown-slot",
        projection_table="other_table",
    )
    _insert_projection_link(
        database_path,
        1,
        "catalog.profile",
        "wrong-table-slot",
        projection_table="other_table",
    )

    findings = DataHealthService(database_path).find_projection_gaps()

    assert len(findings) == 2
    assert all(finding.check_id == "DH-PG-004" for finding in findings)
    assert findings == DataHealthService(database_path).find_projection_gaps()
    assert any("unknown to the shared projection authority" in finding.reason for finding in findings)
    assert any("authorizes target table" in finding.reason for finding in findings)


def test_projection_gap_reports_one_finding_per_missing_target_link(tmp_path):
    database_path = tmp_path / "projection-gap-multiple-missing-targets.db"
    _initialize(database_path)
    _seed_succeeded_projection_source(database_path)
    _insert_projection_link(
        database_path,
        1,
        "catalog.profile",
        "profile-slot",
        projection_table="profile_observations",
        projection_row_id=100,
    )
    _insert_projection_link(
        database_path,
        1,
        "catalog.roll",
        "roll-slot",
        projection_table="roll_observations",
        projection_row_id=101,
    )

    findings = DataHealthService(database_path).find_projection_gaps()

    assert [finding.check_id for finding in findings] == ["DH-PG-005", "DH-PG-005"]
    assert [finding.local_identifier for finding in findings] == [
        "source_event_id=1; projection_kind='catalog.profile'; projection_slot='profile-slot'",
        "source_event_id=1; projection_kind='catalog.roll'; projection_slot='roll-slot'",
    ]


@pytest.mark.parametrize(
    ("projection_kind", "projection_table"),
    [
        (authority.projection_kind, authority.target_table)
        for authority in PROJECTION_AUTHORITIES
    ],
)
def test_projection_gap_dereferences_all_authorized_targets_by_id(
    tmp_path, projection_kind, projection_table
):
    database_path = tmp_path / "projection-gap-all-authorized-targets.db"
    _initialize(database_path)
    _seed_succeeded_projection_source(database_path)
    _insert_projection_link(
        database_path,
        1,
        projection_kind,
        "slot",
        projection_table=projection_table,
    )

    findings = DataHealthService(database_path).find_projection_gaps()

    assert len(findings) == 1
    assert findings[0].check_id == "DH-PG-005"


@pytest.mark.parametrize(
    ("projection_kind", "projection_table"),
    [
        ("catalog.profile", "profile_observations"),
        ("catalog.roll", "roll_observations"),
        ("catalog.roll_key", "harem_key_observations"),
        ("catalog.roll_rank", "rank_snapshots"),
        ("catalog.roll_server_character", "server_character_observations"),
        ("catalog.antidisable_page", "import_events"),
    ],
)
def test_projection_gap_reports_missing_roll_and_antidisable_targets(
    tmp_path, projection_kind, projection_table
):
    database_path = tmp_path / "projection-gap-healthy-authority.db"
    _initialize(database_path)
    _seed_succeeded_projection_source(database_path)
    _insert_projection_link(
        database_path,
        1,
        projection_kind,
        "slot",
        projection_table=projection_table,
    )

    findings = DataHealthService(database_path).find_projection_gaps()

    assert len(findings) == 1
    assert findings[0].check_id == "DH-PG-005"


def test_projection_gap_healthy_existing_target_has_no_finding(tmp_path):
    database_path = tmp_path / "projection-gap-healthy-target.db"
    _initialize(database_path)
    _seed_succeeded_projection_source(database_path)
    _insert_projection_link(
        database_path,
        1,
        "catalog.antidisable_page",
        "slot",
        projection_table="import_events",
        projection_row_id=1,
    )

    assert DataHealthService(database_path).find_projection_gaps() == ()


def test_projection_gap_reports_existing_target_with_other_provenance(tmp_path):
    database_path = tmp_path / "projection-gap-target-provenance-deferred.db"
    _initialize(database_path)
    _seed_succeeded_projection_source(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO import_events (id, kind, source, observed_at, raw_message) "
            "VALUES (2, 'other', 'other', 'now', 'test')"
        )
    _insert_projection_link(
        database_path,
        1,
        "catalog.antidisable_page",
        "slot",
        projection_table="import_events",
        projection_row_id=2,
    )

    findings = DataHealthService(database_path).find_projection_gaps()

    assert [finding.check_id for finding in findings] == ["DH-PG-006"]
    assert "row 2" in findings[0].reason
    assert "import event 1" in findings[0].reason


def test_projection_gap_reports_wrong_valid_generic_target_import(tmp_path):
    database_path = tmp_path / "projection-gap-wrong-generic-import.db"
    _initialize(database_path)
    _seed_succeeded_projection_source(database_path)
    _insert_import_event(database_path, 2)
    _insert_generic_projection_target(database_path, "profile_observations", 100, 2)
    _insert_projection_link(
        database_path,
        1,
        "catalog.profile",
        "slot",
        projection_table="profile_observations",
        projection_row_id=100,
    )

    findings = DataHealthService(database_path).find_projection_gaps()

    assert [finding.check_id for finding in findings] == ["DH-PG-006"]
    assert "profile_observations" in findings[0].reason
    assert "row 100" in findings[0].reason
    assert "import event 2" in findings[0].reason
    assert "import event 1" in findings[0].reason


@pytest.mark.parametrize(
    ("projection_kind", "projection_table"),
    [
        (authority.projection_kind, authority.target_table)
        for authority in PROJECTION_AUTHORITIES
        if authority.target_table != "import_events"
    ],
)
def test_projection_gap_detects_wrong_valid_import_for_all_generic_targets(
    tmp_path, projection_kind, projection_table
):
    database_path = tmp_path / "projection-gap-all-generic-imports.db"
    _initialize(database_path)
    _seed_succeeded_projection_source(database_path)
    _insert_import_event(database_path, 2)
    _insert_generic_projection_target(database_path, projection_table, 100, 2)
    _insert_projection_link(
        database_path,
        1,
        projection_kind,
        "slot",
        projection_table=projection_table,
        projection_row_id=100,
    )

    findings = DataHealthService(database_path).find_projection_gaps()

    assert [finding.check_id for finding in findings] == ["DH-PG-006"]


@pytest.mark.parametrize(
    ("projection_kind", "projection_table"),
    [
        ("catalog.roll", "roll_observations"),
        ("catalog.roll_key", "harem_key_observations"),
        ("catalog.roll_rank", "rank_snapshots"),
        ("catalog.roll_server_character", "server_character_observations"),
    ],
)
def test_projection_gap_roll_targets_use_only_import_ownership(
    tmp_path, projection_kind, projection_table
):
    database_path = tmp_path / "projection-gap-roll-imports.db"
    _initialize(database_path)
    _seed_succeeded_projection_source(database_path)
    _insert_import_event(database_path, 2)
    _insert_generic_projection_target(database_path, projection_table, 100, 2)
    _insert_projection_link(
        database_path,
        1,
        projection_kind,
        "slot",
        projection_table=projection_table,
        projection_row_id=100,
    )

    findings = DataHealthService(database_path).find_projection_gaps()

    assert [finding.check_id for finding in findings] == ["DH-PG-006"]


def test_projection_gap_reports_wrong_valid_antidisable_import(tmp_path):
    database_path = tmp_path / "projection-gap-wrong-antidisable-import.db"
    _initialize(database_path)
    _seed_succeeded_projection_source(database_path)
    _insert_import_event(database_path, 2)
    _insert_projection_link(
        database_path,
        1,
        "catalog.antidisable_page",
        "slot",
        projection_table="import_events",
        projection_row_id=2,
    )

    findings = DataHealthService(database_path).find_projection_gaps()

    assert [finding.check_id for finding in findings] == ["DH-PG-006"]
    assert "row 2" in findings[0].reason
    assert "import event 1" in findings[0].reason


def test_projection_gap_reports_each_wrong_owned_link_deterministically(tmp_path):
    database_path = tmp_path / "projection-gap-multiple-wrong-imports.db"
    _initialize(database_path)
    _seed_succeeded_projection_source(database_path)
    _insert_import_event(database_path, 2)
    _insert_generic_projection_target(database_path, "profile_observations", 100, 2)
    _insert_generic_projection_target(database_path, "timer_state_observations", 101, 2)
    _insert_projection_link(
        database_path,
        1,
        "catalog.profile",
        "profile-slot",
        projection_table="profile_observations",
        projection_row_id=100,
    )
    _insert_projection_link(
        database_path,
        1,
        "catalog.timer_state",
        "timer-slot",
        projection_table="timer_state_observations",
        projection_row_id=101,
    )

    findings = DataHealthService(database_path).find_projection_gaps()

    assert [finding.check_id for finding in findings] == ["DH-PG-006", "DH-PG-006"]
    assert [finding.local_identifier for finding in findings] == [
        "source_event_id=1; projection_kind='catalog.profile'; projection_slot='profile-slot'",
        "source_event_id=1; projection_kind='catalog.timer_state'; projection_slot='timer-slot'",
    ]
    assert findings == DataHealthService(database_path).find_projection_gaps()


def test_projection_gap_matching_generic_ownership_is_healthy(tmp_path):
    database_path = tmp_path / "projection-gap-matching-generic-import.db"
    _initialize(database_path)
    _seed_succeeded_projection_source(database_path)
    _insert_generic_projection_target(database_path, "profile_observations", 100, 1)
    _insert_projection_link(
        database_path,
        1,
        "catalog.profile",
        "slot",
        projection_table="profile_observations",
        projection_row_id=100,
    )

    assert DataHealthService(database_path).find_projection_gaps() == ()


@pytest.mark.parametrize(
    ("projection_kind", "expected_table"),
    [
        ("catalog.roll", "roll_observations"),
        ("catalog.antidisable_page", "import_events"),
    ],
)
def test_projection_gap_reports_wrong_roll_or_antidisable_table(
    tmp_path, projection_kind, expected_table
):
    database_path = tmp_path / "projection-gap-special-table.db"
    _initialize(database_path)
    _seed_succeeded_projection_source(database_path)
    _insert_projection_link(
        database_path,
        1,
        projection_kind,
        "slot",
        projection_table="other_table",
    )

    findings = DataHealthService(database_path).find_projection_gaps()

    assert len(findings) == 1
    assert findings[0].check_id == "DH-PG-004"
    assert expected_table in findings[0].reason


@pytest.mark.parametrize(
    (
        "source_status",
        "legacy_import_event_id",
        "link_state",
        "projection_kind",
        "projection_table",
        "expected_check",
    ),
    [
        ("failed", 1, "completed", "catalog.profile", "profile_observations", "DH-PG-001"),
        ("succeeded", 1, "claimed", "catalog.profile", "profile_observations", "DH-PG-002"),
        ("succeeded", None, "completed", "catalog.unknown", "other_table", "DH-PG-003"),
        ("succeeded", None, "completed", "catalog.profile", "profile_observations", "DH-PG-003"),
        ("succeeded", 1, "completed", "catalog.unknown", "other_table", "DH-PG-004"),
        ("succeeded", 1, "completed", "catalog.profile", "other_table", "DH-PG-004"),
    ],
)
def test_projection_gap_root_cause_states_suppress_authority_findings(
    tmp_path,
    source_status,
    legacy_import_event_id,
    link_state,
    projection_kind,
    projection_table,
    expected_check,
):
    database_path = tmp_path / "projection-gap-root-cause-boundary.db"
    _initialize(database_path)
    _insert_aggregate(database_path, 1, "message")
    _insert_revision(database_path, 1, 1, "hash")
    _insert_source_event(
        database_path,
        1,
        "event",
        1,
        status=source_status,
        legacy_import_event_id=legacy_import_event_id,
    )
    _insert_projection_link(
        database_path,
        1,
        projection_kind,
        "slot",
        state=link_state,
        projection_table=projection_table,
    )

    findings = DataHealthService(database_path).find_projection_gaps()

    assert [finding.check_id for finding in findings] == [expected_check]


def test_projection_gap_does_not_duplicate_dh03_projection_link_findings(tmp_path):
    database_path = tmp_path / "projection-gap-duplicate-links.db"
    _initialize(database_path)
    _rebuild_without_singular_constraints(database_path, "discord_projection_links")
    _insert_aggregate(database_path, 1, "message")
    _insert_revision(database_path, 1, 1, "hash")
    _insert_source_event(
        database_path, 1, "event", 1, status="succeeded", legacy_import_event_id=1
    )
    _insert_projection_link(
        database_path,
        1,
        "catalog.antidisable_page",
        "slot",
        projection_table="import_events",
        projection_row_id=1,
    )
    _insert_projection_link(
        database_path,
        1,
        "catalog.antidisable_page",
        "slot",
        projection_table="import_events",
        projection_row_id=1,
    )

    assert DataHealthService(database_path).find_projection_gaps() == ()
    assert [finding.check_id for finding in DataHealthService(database_path).find_duplicates()] == [
        "DH-DUP-009"
    ]


def test_projection_gap_category_does_not_report_dh01_or_dh02_findings(tmp_path):
    database_path = tmp_path / "projection-gap-category-isolation.db"
    _initialize(database_path)
    _seed_base_rows(database_path)
    _insert_character(database_path, 1, "Broken", "Series", "wrong", "series")
    _insert_reaction(database_path, 1, 999, 1)

    assert DataHealthService(database_path).find_projection_gaps() == ()


def test_duplicate_harem_scan_page_identity_is_scan_scoped(tmp_path):
    database_path = tmp_path / "harem-pages.db"
    _initialize(database_path)
    _rebuild_without_singular_constraints(database_path, "harem_scan_pages")
    _seed_base_rows(database_path)
    _insert_harem_scan(database_path, 1)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO harem_scan_pages (harem_scan_id, page_number, import_event_id) "
            "VALUES (1, 1, 1)"
        )
        connection.execute(
            "INSERT INTO harem_scan_pages (harem_scan_id, page_number, import_event_id) "
            "VALUES (1, 1, 1)"
        )

    findings = DataHealthService(database_path).find_duplicates()

    assert [(finding.check_id, finding.entity) for finding in findings] == [
        ("DH-DUP-010", "harem_scan_pages")
    ]


def test_duplicate_antidisable_workflow_and_binding_identities(tmp_path):
    database_path = tmp_path / "antidisable.db"
    _initialize(database_path)
    _rebuild_without_singular_constraints(database_path, "discord_antidisable_workflows")
    _rebuild_without_singular_constraints(
        database_path, "discord_antidisable_response_bindings"
    )
    _seed_base_rows(database_path)
    for scan_id in range(1, 5):
        _insert_harem_scan(database_path, scan_id, scan_kind="antidisable")
    for aggregate_id in (10, 11, 12, 13, 30, 40):
        _insert_aggregate(database_path, aggregate_id, f"message-{aggregate_id}")
    with sqlite3.connect(database_path) as connection:
        connection.executemany(
            "INSERT INTO discord_antidisable_workflows "
            "(harem_scan_id, request_message_aggregate_id, requesting_user_id, "
            "created_at, expires_at) VALUES (?, ?, 'user', '2026-01-01', '2026-01-02')",
            [(1, 10), (1, 11), (2, 10), (3, 12), (4, 13)],
        )
        connection.executemany(
            "INSERT INTO discord_antidisable_response_bindings "
            "(harem_scan_id, response_message_aggregate_id, bound_at) "
            "VALUES (?, ?, 'now')",
            [(3, 30), (3, 30), (3, 40), (4, 40)],
        )

    findings = DataHealthService(database_path).find_duplicates()

    assert len(findings) == 5
    assert all(finding.check_id == "DH-DUP-011" for finding in findings)
    assert any("workflow" in finding.reason for finding in findings)
    assert any("request aggregate" in finding.reason for finding in findings)
    assert any("response binding" in finding.reason for finding in findings)
    assert any("response aggregate" in finding.reason for finding in findings)


def test_valid_history_repeats_and_distinct_scopes_are_not_duplicates(tmp_path):
    database_path = tmp_path / "valid-repeats.db"
    _initialize(database_path)
    _insert_character(database_path, 1, "Same", "Series A", "same", "series a")
    _insert_character(database_path, 2, "Same", "Series B", "same", "series b")
    _insert_context(database_path, 1, "Server One", "server one", 1, "Shared", "shared")
    _insert_context(database_path, 2, "Server Two", "server two", 2, "Shared", "shared")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO import_events (id, kind, source, observed_at, raw_message) "
            "VALUES (1, 'test', 'test', 'now', 'repeat')"
        )
    _insert_aggregate(database_path, 1, "message")
    _insert_revision(database_path, 1, 1, "hash-1", marker="one")
    _insert_revision(database_path, 2, 1, "hash-2", marker="two")
    _insert_source_event(database_path, 1, "event", 1)
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE discord_source_events SET delivery_count = 2 WHERE id = 1")
        connection.executemany(
            "INSERT INTO discord_processing_attempts "
            "(source_event_id, attempt_number, status, retryable, parser_version, "
            "router_version, started_at, created_at) "
            "VALUES (1, ?, 'failed', 1, 'parser', 'router', 'now', 'now')",
            [(1,), (2,)],
        )
        connection.executemany(
            "INSERT INTO discord_projection_links "
            "(source_event_id, projection_kind, projection_slot, state, claimed_at, "
            "created_at, updated_at) VALUES (1, ?, ?, 'claimed', 'now', 'now', 'now')",
            [("kind-a", "slot-a"), ("kind-b", "slot-b")],
        )
        connection.executemany(
            "INSERT INTO import_events (id, kind, source, observed_at, raw_message) "
            "VALUES (?, 'test', 'test', 'now', 'repeat')",
            [(2,), (3,)],
        )
        connection.executemany(
            "INSERT INTO claim_observations "
            "(account_context_id, character_id, character_name, normalized_character_name, "
            "observed_at, import_event_id) VALUES (1, 1, 'Same', 'same', 'now', ?)",
            [(2,), (3,)],
        )
        connection.executemany(
            "INSERT INTO harem_scans "
            "(id, account_context_id, expected_page_count, started_at, scan_kind) "
            "VALUES (?, 1, 1, 'now', 'keys')",
            [(1,), (2,)],
        )
        connection.executemany(
            "INSERT INTO harem_scan_pages (harem_scan_id, page_number, import_event_id) "
            "VALUES (?, 1, ?)",
            [(1, 2), (2, 3)],
        )
        connection.executemany(
            "INSERT INTO discord_antidisable_workflows "
            "(harem_scan_id, request_message_aggregate_id, requesting_user_id, "
            "created_at, expires_at) VALUES (?, ?, 'user', '2026-01-01', '2026-01-02')",
            [(1, 1), (2, 2)],
        )

    assert DataHealthService(database_path).find_duplicates() == ()


def test_duplicate_scan_preserves_dh02_and_dh04_category_boundaries(tmp_path):
    database_path = tmp_path / "boundaries.db"
    _initialize(database_path)
    _insert_character(database_path, 1, "Broken", "Series", "wrong", "series")
    _insert_aggregate(database_path, 1, "message")
    _insert_revision(database_path, 1, 1, "hash")
    _insert_source_event(database_path, 1, "event", 1)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO discord_projection_links "
            "(source_event_id, projection_kind, projection_slot, projection_table, "
            "projection_row_id, state, claimed_at, completed_at, created_at, updated_at) "
            "VALUES (1, 'weird', 'target', 'missing_table', 999, 'completed', "
            "'now', 'now', 'now', 'now')"
        )

    assert DataHealthService(database_path).find_duplicates() == ()
    assert [finding.check_id for finding in DataHealthService(database_path).find_impossible_identities()] == [
        "DH-ID-002"
    ]


def test_cli_duplicates_healthy_database_succeeds(tmp_path, monkeypatch):
    database_path = tmp_path / "catalog.db"
    _initialize(database_path)
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    result = CliRunner().invoke(main.app, ["catalog", "data-health", "duplicates"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "No data-health findings."


def test_cli_duplicates_reports_deterministic_findings_and_total(tmp_path, monkeypatch):
    database_path = tmp_path / "catalog.db"
    _initialize(database_path)
    _rebuild_without_singular_constraints(database_path, "characters")
    _insert_character(database_path, 1, "Same", "Series", "same", "series")
    _insert_character(database_path, 2, "Same", "Series", "same", "series")
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    result = CliRunner().invoke(main.app, ["catalog", "data-health", "duplicates"])

    assert result.exit_code == 0
    assert "DH-DUP-001" in result.stdout
    assert "Total findings: 1" in result.stdout
    assert "raw_text" not in result.stdout


def test_cli_duplicates_invalid_schema_fails(tmp_path, monkeypatch):
    database_path = tmp_path / "not-moa.db"
    database_path.touch()
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    result = CliRunner().invoke(main.app, ["catalog", "data-health", "duplicates"])

    assert result.exit_code == 1
    assert "Unrecognized MOA catalog schema" in result.stdout


def test_cli_duplicates_unavailable_database_fails_without_creation(tmp_path, monkeypatch):
    database_path = tmp_path / "missing" / "catalog.db"
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    result = CliRunner().invoke(main.app, ["catalog", "data-health", "duplicates"])

    assert result.exit_code == 1
    assert not database_path.exists()
    assert not database_path.parent.exists()


def test_cli_projection_gaps_healthy_database_succeeds(tmp_path, monkeypatch):
    database_path = tmp_path / "catalog.db"
    _initialize(database_path)
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    result = CliRunner().invoke(main.app, ["catalog", "data-health", "projection-gaps"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "No data-health findings."


def test_cli_projection_gaps_reports_source_event_findings(tmp_path, monkeypatch):
    database_path = tmp_path / "catalog.db"
    _initialize(database_path)
    _insert_aggregate(database_path, 1, "message")
    _insert_revision(database_path, 1, 1, "hash")
    _insert_source_event(database_path, 1, "event", 1, status="failed")
    _insert_projection_link(database_path, 1, "kind", "slot")
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    result = CliRunner().invoke(main.app, ["catalog", "data-health", "projection-gaps"])

    assert result.exit_code == 0
    assert "DH-PG-001" in result.stdout
    assert "projection-gap" in result.stdout
    assert "Total findings: 1" in result.stdout
    assert "raw_text" not in result.stdout


def test_cli_projection_gaps_reports_claimed_link_findings(tmp_path, monkeypatch):
    database_path = tmp_path / "catalog.db"
    _initialize(database_path)
    _insert_aggregate(database_path, 1, "message")
    _insert_revision(database_path, 1, 1, "hash")
    _insert_source_event(database_path, 1, "event", 1, status="succeeded")
    _insert_projection_link(database_path, 1, "kind", "slot", state="claimed")
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    result = CliRunner().invoke(main.app, ["catalog", "data-health", "projection-gaps"])

    assert result.exit_code == 0
    assert "DH-PG-002" in result.stdout
    assert "1" in result.stdout
    assert "Total findings: 1" in result.stdout


def test_data_health_command_inventory_has_only_the_four_audited_commands(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "catalog.db"
    _initialize(database_path)
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    result = CliRunner().invoke(main.app, ["catalog", "data-health", "--help"])

    assert result.exit_code == 0
    assert "orphans" in result.stdout
    assert "impossible-identities" in result.stdout
    assert "duplicates" in result.stdout
    assert "projection-gaps" in result.stdout
    assert " all " not in f" {result.stdout} "


def test_each_data_health_command_is_category_isolated(tmp_path):
    database_path = tmp_path / "category-isolation.db"
    _initialize(database_path)
    _rebuild_without_singular_constraints(database_path, "characters")
    _seed_base_rows(database_path)
    _insert_character(database_path, 1, "Broken", "Series", "wrong", "series")
    _insert_character(database_path, 2, "Broken", "Series", "wrong", "series")
    _insert_reaction(database_path, 1, 999, 1)

    orphan_ids = {finding.check_id for finding in DataHealthService(database_path).find_orphans()}
    identity_ids = {
        finding.check_id
        for finding in DataHealthService(database_path).find_impossible_identities()
    }
    duplicate_ids = {finding.check_id for finding in DataHealthService(database_path).find_duplicates()}

    assert orphan_ids == {"DH-ORPH-002"}
    assert identity_ids == {"DH-ID-002"}
    assert duplicate_ids == {"DH-DUP-001"}
