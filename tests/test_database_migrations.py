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
        connection.execute("DROP TABLE discord_projection_links")
        connection.execute("DROP TABLE discord_processing_attempts")
        connection.execute("DROP TABLE discord_source_events")
        connection.execute("DROP TABLE discord_message_revisions")
        connection.execute("DROP TABLE discord_message_aggregates")
        connection.execute("DELETE FROM schema_migrations WHERE version = 3")
        connection.execute("DELETE FROM schema_migrations WHERE version = 2")


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
    } <= tables
    assert _migration_rows(database_path) == [
        (1, "catalog-schema-baseline"),
        (2, "durable-discord-message-ingestion"),
        (3, "durable-discord-projection-links"),
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
    }
    with _open_database(database_path) as connection:
        for table, columns in expected_columns.items():
            actual = {
                row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            assert actual == columns


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


def _current_database_with_source_event(database_path):
    CatalogRepository(database_path)
    with _open_database(database_path) as connection:
        aggregate_id = _insert_aggregate(connection)
        revision_id = _insert_revision(connection, aggregate_id)
        return _insert_source_event(connection, revision_id)


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
            "INSERT INTO schema_migrations VALUES (4, 'future', 'now')"
        )

    with pytest.raises(MigrationError, match="unknown newer"):
        CatalogRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version FROM schema_migrations").fetchall() == [(4,)]
