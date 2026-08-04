import sqlite3

import pytest

from moa.database.sqlite import connect


def _pragma_value(connection: sqlite3.Connection, name: str):
    return connection.execute(f"PRAGMA {name}").fetchone()[0]


def test_connection_factory_current_effective_pragmas(tmp_path) -> None:
    database_path = tmp_path / "connection.db"

    with connect(database_path) as connection:
        values = {
            name: _pragma_value(connection, name)
            for name in ("foreign_keys", "journal_mode", "busy_timeout")
        }

    assert values == {
        "foreign_keys": 1,
        "journal_mode": "wal",
        "busy_timeout": 5000,
    }


def test_wal_writer_commit_preserves_active_reader_snapshot(tmp_path) -> None:
    database_path = tmp_path / "contention.db"

    with connect(database_path) as seed_connection:
        seed_connection.execute("CREATE TABLE values_table (value INTEGER NOT NULL)")
        seed_connection.execute("INSERT INTO values_table (value) VALUES (1)")

    reader = connect(database_path)
    writer = connect(database_path)
    try:
        assert _pragma_value(reader, "journal_mode") == "wal"
        assert _pragma_value(writer, "journal_mode") == "wal"
        assert _pragma_value(writer, "busy_timeout") == 5000

        reader.execute("BEGIN")
        assert reader.execute("SELECT value FROM values_table").fetchone()[0] == 1

        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE values_table SET value = 2")
        writer.commit()

        assert reader.execute("SELECT value FROM values_table").fetchone()[0] == 1
        reader.rollback()
        assert reader.execute("SELECT value FROM values_table").fetchone()[0] == 2
    finally:
        writer.rollback()
        reader.rollback()
        writer.close()
        reader.close()


def test_wal_second_writer_can_reuse_connection_after_contention(tmp_path) -> None:
    database_path = tmp_path / "writer-contention.db"

    with connect(database_path) as seed_connection:
        seed_connection.execute("CREATE TABLE values_table (value INTEGER NOT NULL)")
        seed_connection.execute("INSERT INTO values_table (value) VALUES (1)")

    writer_a = connect(database_path)
    writer_b = connect(database_path)
    try:
        assert _pragma_value(writer_a, "journal_mode") == "wal"
        assert _pragma_value(writer_b, "journal_mode") == "wal"
        assert _pragma_value(writer_b, "busy_timeout") == 5000

        writer_b.execute("PRAGMA busy_timeout = 0")
        assert _pragma_value(writer_b, "busy_timeout") == 0

        writer_a.execute("BEGIN IMMEDIATE")
        writer_a.execute("UPDATE values_table SET value = 2")
        assert writer_a.in_transaction is True

        with pytest.raises(sqlite3.OperationalError) as contention:
            writer_b.execute("BEGIN IMMEDIATE")

        assert contention.value.sqlite_errorcode == sqlite3.SQLITE_BUSY
        assert contention.value.sqlite_errorname == "SQLITE_BUSY"
        assert writer_b.in_transaction is False

        writer_a.commit()
        assert writer_a.in_transaction is False

        writer_b.execute("BEGIN IMMEDIATE")
        writer_b.execute("UPDATE values_table SET value = 3")
        writer_b.commit()
        assert writer_b.in_transaction is False
    finally:
        writer_b.rollback()
        writer_a.rollback()
        writer_b.close()
        writer_a.close()

    with connect(database_path) as verification_connection:
        assert verification_connection.execute(
            "SELECT value FROM values_table"
        ).fetchone()[0] == 3
