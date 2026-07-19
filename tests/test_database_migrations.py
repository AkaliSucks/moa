import sqlite3

import pytest

from moa.database.migrations import (
    Migration,
    MigrationError,
    run_migrations,
)
from moa.repositories.catalog_repository import CatalogRepository


def _migration_rows(database_path):
    with sqlite3.connect(database_path) as connection:
        return connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()


def test_fresh_catalog_database_records_baseline_once(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"

    CatalogRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "characters" in tables
    assert "schema_migrations" in tables
    assert _migration_rows(database_path) == [(1, "catalog-schema-baseline")]


def test_catalog_initialization_is_idempotent_and_preserves_data(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    CatalogRepository(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO characters "
            "(name, series, normalized_name, normalized_series, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("Miku", "VOCALOID", "miku", "vocaloid", "now", "now"),
        )

    CatalogRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT name FROM characters").fetchone()[0] == "Miku"
    assert _migration_rows(database_path) == [(1, "catalog-schema-baseline")]


def test_existing_current_schema_without_metadata_is_baselined(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    CatalogRepository(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO characters "
            "(name, series, normalized_name, normalized_series, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("Mai", "Seishun Buta Yarou", "mai", "seishun buta yarou", "now", "now"),
        )
        connection.execute("DROP TABLE schema_migrations")

    CatalogRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT name FROM characters").fetchone()[0] == "Mai"
    assert _migration_rows(database_path) == [(1, "catalog-schema-baseline")]


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
        assert connection.execute("SELECT * FROM schema_migrations").fetchall() == []
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


def test_unknown_newer_database_version_fails_safely(tmp_path) -> None:
    database_path = tmp_path / "newer.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations "
            "(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_migrations VALUES (2, 'future', 'now')"
        )

    with pytest.raises(MigrationError, match="unknown newer"):
        CatalogRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version FROM schema_migrations").fetchall() == [(2,)]
