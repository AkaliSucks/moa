import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from moa.cli import main
from moa.database.migrations import MigrationError, validate_current_catalog_schema
from moa.database.sqlite import connect_read_only
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories import data_health_repository
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.data_health_repository import DataHealthSchemaError
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.services.data_health_service import DataHealthService

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
    repository_source = Path(data_health_repository.__file__).read_text(encoding="utf-8")
    assert "discord_projection_links" not in repository_source


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
