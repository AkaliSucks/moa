"""Shared SQLite identity helpers for catalog-backed repositories."""

import sqlite3
from datetime import datetime


def normalize(value: str) -> str:
    """Return the canonical normalized text used by catalog identities."""
    return " ".join(value.casefold().split())


def upsert_server(
    connection: sqlite3.Connection, server_name: str, observed_at: datetime
) -> int:
    """Upsert one server identity using a caller-owned connection."""
    normalized_name = normalize(server_name)
    connection.execute(
        """
        INSERT INTO server_contexts (name, normalized_name, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(normalized_name) DO UPDATE SET
            name = excluded.name,
            updated_at = excluded.updated_at
        """,
        (
            server_name.strip(),
            normalized_name,
            observed_at.isoformat(),
            observed_at.isoformat(),
        ),
    )
    return connection.execute(
        "SELECT id FROM server_contexts WHERE normalized_name = ?",
        (normalized_name,),
    ).fetchone()["id"]


def upsert_account(
    connection: sqlite3.Connection,
    server_id: int,
    account_name: str,
    observed_at: datetime,
) -> int:
    """Upsert one account identity using a caller-owned connection."""
    normalized_name = normalize(account_name)
    connection.execute(
        """
        INSERT INTO account_contexts (
            server_context_id, name, normalized_name, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(server_context_id, normalized_name) DO UPDATE SET
            name = excluded.name,
            updated_at = excluded.updated_at
        """,
        (
            server_id,
            account_name.strip(),
            normalized_name,
            observed_at.isoformat(),
            observed_at.isoformat(),
        ),
    )
    return connection.execute(
        """
        SELECT id FROM account_contexts
        WHERE server_context_id = ? AND normalized_name = ?
        """,
        (server_id, normalized_name),
    ).fetchone()["id"]
