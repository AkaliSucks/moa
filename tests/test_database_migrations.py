import sqlite3

import pytest

from moa.database.migrations import (
    CATALOG_MIGRATIONS,
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


def _open_database(database_path):
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _make_baseline_database(database_path):
    """Create a version-1 database to exercise the version-2 upgrade path."""
    CatalogRepository(database_path)
    with _open_database(database_path) as connection:
        connection.execute("DROP TABLE discord_antidisable_response_bindings")
        connection.execute("DROP TABLE discord_antidisable_workflows")
        connection.execute("DROP TABLE discord_source_event_server_attributions")
        connection.execute("DROP TABLE discord_source_event_account_attributions")
        connection.execute("DROP TABLE discord_projection_links")
        connection.execute("DROP TABLE discord_processing_attempts")
        connection.execute("DROP TABLE discord_source_events")
        connection.execute("DROP TABLE discord_message_revisions")
        connection.execute("DROP TABLE discord_message_aggregates")
        connection.execute("DELETE FROM schema_migrations WHERE version = 3")
        connection.execute("DELETE FROM schema_migrations WHERE version = 2")
        connection.execute("DELETE FROM schema_migrations WHERE version = 4")
        connection.execute("DELETE FROM schema_migrations WHERE version = 5")
        connection.execute("DELETE FROM schema_migrations WHERE version = 6")


def _make_version_5_database(database_path):
    CatalogRepository(database_path)
    with _open_database(database_path) as connection:
        connection.execute("DROP TABLE discord_antidisable_response_bindings")
        connection.execute("DROP TABLE discord_antidisable_workflows")
        connection.execute("DELETE FROM schema_migrations WHERE version = 6")


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
    return connection.execute(
        """
        INSERT INTO discord_projection_links (
            source_event_id, projection_kind, projection_slot,
            projection_table, projection_row_id, state,
            claimed_at, completed_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_event_id,
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
    ]
    with _open_database(database_path) as connection:
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
    } <= indexes

    expected_columns = {
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
            "created_at",
        },
        "discord_projection_links": {
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
            6,
            "durable-discord-antidisable-workflow-bindings",
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
        connection.execute("DROP TABLE discord_antidisable_response_bindings")
        connection.execute("DROP TABLE discord_antidisable_workflows")
        connection.execute("DROP TABLE discord_source_event_server_attributions")
        connection.execute("DROP TABLE discord_source_event_account_attributions")
        connection.execute("DELETE FROM schema_migrations WHERE version = 4")
        connection.execute("DELETE FROM schema_migrations WHERE version = 5")
        connection.execute("DELETE FROM schema_migrations WHERE version = 6")
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
            "INSERT INTO schema_migrations VALUES (7, 'future', 'now')"
        )

    with pytest.raises(MigrationError, match="unknown newer"):
        CatalogRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version FROM schema_migrations").fetchall() == [(7,)]
