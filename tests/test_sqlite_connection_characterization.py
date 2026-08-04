import sqlite3

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
        "journal_mode": "delete",
        "busy_timeout": 5000,
    }


def test_rollback_journal_writer_commit_is_blocked_by_active_reader(tmp_path) -> None:
    database_path = tmp_path / "contention.db"

    with connect(database_path) as seed_connection:
        seed_connection.execute("CREATE TABLE values_table (value INTEGER NOT NULL)")
        seed_connection.execute("INSERT INTO values_table (value) VALUES (1)")

    reader = connect(database_path)
    writer = connect(database_path)
    try:
        assert _pragma_value(reader, "journal_mode") == "delete"
        assert _pragma_value(writer, "journal_mode") == "delete"
        assert _pragma_value(writer, "busy_timeout") == 5000

        reader.execute("BEGIN")
        assert reader.execute("SELECT value FROM values_table").fetchone()[0] == 1

        writer.execute("PRAGMA busy_timeout = 0")
        assert _pragma_value(writer, "busy_timeout") == 0
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE values_table SET value = 2")

        try:
            writer.commit()
        except sqlite3.OperationalError as error:
            assert "locked" in str(error).casefold()
        else:
            raise AssertionError("writer commit unexpectedly succeeded while reader was active")
    finally:
        writer.rollback()
        reader.rollback()
        writer.close()
        reader.close()
