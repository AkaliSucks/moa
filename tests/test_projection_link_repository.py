from __future__ import annotations

from datetime import UTC, datetime
import sqlite3

import pytest

from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.projection_link_repository import (
    ProjectionLinkIntegrityError,
    ProjectionLinkRepository,
)


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def _open_database(path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _make_source_event(connection: sqlite3.Connection, suffix: str = "1") -> int:
    value = NOW.isoformat()
    aggregate_id = connection.execute(
        """
        INSERT INTO discord_message_aggregates (
            platform, guild_id, channel_id, message_id,
            first_received_at, last_received_at, created_at, updated_at
        ) VALUES ('discord', ?, ?, ?, ?, ?, ?, ?)
        """,
        (f"guild-{suffix}", f"channel-{suffix}", f"message-{suffix}", value, value, value, value),
    ).lastrowid
    revision_id = connection.execute(
        """
        INSERT INTO discord_message_revisions (
            aggregate_id, source_revision_marker, normalized_payload_hash,
            revision_state, first_received_at, last_received_at, created_at, updated_at
        ) VALUES (?, ?, ?, 'candidate', ?, ?, ?, ?)
        """,
        (aggregate_id, f"revision-{suffix}", f"hash-{suffix}", value, value, value, value),
    ).lastrowid
    return int(
        connection.execute(
            """
            INSERT INTO discord_source_events (
                event_key, revision_id, event_kind, status, raw_text,
                source_observed_at, received_at, last_seen_at, delivery_count,
                created_at, updated_at
            ) VALUES (?, ?, 'message_create', 'received', 'sanitized fixture', ?, ?, ?, 1, ?, ?)
            """,
            (f"event-{suffix}", revision_id, value, value, value, value, value),
        ).lastrowid
    )


def _new_database(tmp_path):
    path = tmp_path / "catalog.db"
    CatalogRepository(path)
    return path


def _claim(
    repository: ProjectionLinkRepository,
    source_event_id: int,
    generation_id: int,
    *,
    kind: str = "catalog.profile",
    slot: str = '{"account":"account","server":"server"}',
) -> int:
    return repository.claim_link(
        source_event_id=source_event_id,
        generation_id=generation_id,
        projection_kind=kind,
        projection_slot=slot,
        claimed_at=NOW,
    )


def test_generation_one_compatibility_and_caller_owned_rollback(tmp_path) -> None:
    path = _new_database(tmp_path)
    with _open_database(path) as connection:
        repository = ProjectionLinkRepository(connection)
        source_event_id = _make_source_event(connection)

        assert repository.resolve_current_generation_id() == 1
        link_id = _claim(repository, source_event_id, 1)
        link = repository.load_links(source_event_id=source_event_id, generation_id=1)[0]
        assert (link["id"], link["generation_id"], link["state"]) == (link_id, 1, "claimed")

        connection.rollback()
        assert (
            connection.execute("SELECT COUNT(*) FROM discord_projection_links").fetchone()[0] == 0
        )
        assert connection.execute("SELECT COUNT(*) FROM discord_source_events").fetchone()[0] == 0


@pytest.mark.parametrize("current_state", ["zero", "multiple", "invalid"])
def test_current_generation_resolution_fails_closed(tmp_path, current_state: str) -> None:
    path = _new_database(tmp_path)
    with _open_database(path) as connection:
        connection.execute("DROP TRIGGER projection_generations_keep_current_on_update")
        if current_state == "zero":
            connection.execute("UPDATE projection_generations SET is_current = 0")
        elif current_state == "multiple":
            connection.execute("DROP INDEX uq_projection_generations_current")
            connection.execute("INSERT INTO projection_generations (id, is_current) VALUES (2, 1)")
        else:
            connection.execute("DROP TRIGGER projection_generations_keep_initial_id")
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute("UPDATE projection_generations SET id = -1 WHERE id = 1")

        with pytest.raises(ProjectionLinkIntegrityError):
            ProjectionLinkRepository(connection).resolve_current_generation_id()


def test_loads_are_isolated_by_source_event_and_generation(tmp_path) -> None:
    path = _new_database(tmp_path)
    with _open_database(path) as connection:
        repository = ProjectionLinkRepository(connection)
        source_event_id = _make_source_event(connection, "current")
        other_source_event_id = _make_source_event(connection, "other")
        connection.execute("INSERT INTO projection_generations (id, is_current) VALUES (2, 0)")
        current_link_id = _claim(repository, source_event_id, 1, kind="current")
        _claim(repository, source_event_id, 2, kind="historical")
        _claim(repository, other_source_event_id, 1, kind="other-source")

        links = repository.load_links(
            source_event_id=source_event_id,
            generation_id=repository.resolve_current_generation_id(),
        )

        assert [link["id"] for link in links] == [current_link_id]
        assert {(link["source_event_id"], link["generation_id"]) for link in links} == {
            (source_event_id, 1)
        }


def test_claim_uses_explicit_generation_and_propagates_constraints(tmp_path) -> None:
    path = _new_database(tmp_path)
    with _open_database(path) as connection:
        repository = ProjectionLinkRepository(connection)
        source_event_id = _make_source_event(connection)
        connection.execute("INSERT INTO projection_generations (id, is_current) VALUES (2, 0)")

        _claim(repository, source_event_id, 2)
        assert (
            connection.execute("SELECT generation_id FROM discord_projection_links").fetchone()[0]
            == 2
        )

        with pytest.raises(sqlite3.IntegrityError):
            _claim(repository, source_event_id, 999, kind="missing-generation")


def test_completion_is_generation_isolated_and_claimed_only(tmp_path) -> None:
    path = _new_database(tmp_path)
    with _open_database(path) as connection:
        repository = ProjectionLinkRepository(connection)
        source_event_id = _make_source_event(connection)
        connection.execute("INSERT INTO projection_generations (id, is_current) VALUES (2, 0)")
        _claim(repository, source_event_id, 1)
        _claim(repository, source_event_id, 2)

        repository.complete_claimed_link(
            source_event_id=source_event_id,
            generation_id=2,
            projection_kind="catalog.profile",
            projection_slot='{"account":"account","server":"server"}',
            projection_table="profile_observations",
            projection_row_id=41,
            completed_at=NOW,
        )

        rows = connection.execute(
            "SELECT generation_id, state, projection_row_id FROM discord_projection_links ORDER BY generation_id"
        ).fetchall()
        assert [tuple(row) for row in rows] == [(1, "claimed", None), (2, "completed", 41)]
        with pytest.raises(ProjectionLinkIntegrityError):
            repository.complete_claimed_link(
                source_event_id=source_event_id,
                generation_id=2,
                projection_kind="catalog.profile",
                projection_slot='{"account":"account","server":"server"}',
                projection_table="profile_observations",
                projection_row_id=42,
                completed_at=NOW,
            )


def test_historical_completed_link_does_not_suppress_another_generation_claim(tmp_path) -> None:
    path = _new_database(tmp_path)
    with _open_database(path) as connection:
        repository = ProjectionLinkRepository(connection)
        source_event_id = _make_source_event(connection)
        connection.execute("INSERT INTO projection_generations (id, is_current) VALUES (2, 0)")
        _claim(repository, source_event_id, 1)
        repository.complete_claimed_link(
            source_event_id=source_event_id,
            generation_id=1,
            projection_kind="catalog.profile",
            projection_slot='{"account":"account","server":"server"}',
            projection_table="profile_observations",
            projection_row_id=10,
            completed_at=NOW,
        )

        second_link_id = _claim(repository, source_event_id, 2)

        assert [
            (row["id"], row["generation_id"], row["state"])
            for row in connection.execute(
                "SELECT id, generation_id, state FROM discord_projection_links ORDER BY generation_id"
            )
        ] == [(1, 1, "completed"), (second_link_id, 2, "claimed")]
