"""Small SQLite connection factory for MOA's local state database."""

import sqlite3
from pathlib import Path


DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[3] / "data" / "database" / "moa.db"


def connect(database_path: Path | None = None) -> sqlite3.Connection:
    """Open MOA's local SQLite database with the shared connection policy."""
    path = database_path or DEFAULT_DATABASE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection
