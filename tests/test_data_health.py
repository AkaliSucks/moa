import sqlite3

import pytest
from typer.testing import CliRunner

from moa.cli import main
from moa.database.sqlite import connect_read_only
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.data_health_repository import DataHealthSchemaError
from moa.services.data_health_service import DataHealthService


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
