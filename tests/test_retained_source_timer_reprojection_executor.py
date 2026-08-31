from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import TimerStateSnapshot
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.projection_link_repository import ProjectionLinkRepository
from moa.services.projection_expectations import (
    load_durable_projection_expectation_facts,
    resolve_expected_projections,
)
from moa.services.retained_source_reprojection_admission_service import (
    RetainedSourceReprojectionAdmissionService,
)
from moa.services.retained_source_timer_reprojection_executor import (
    RetainedSourceTimerReprojectionError,
    RetainedSourceTimerReprojectionExecutor,
)


NOW = "2026-08-31T12:00:00+00:00"
COMPLETED_AT = datetime(2026, 8, 31, 13, tzinfo=timezone.utc)


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "timer-reprojection.sqlite3"
    CatalogRepository(path)
    return path


def _fixture(connection: sqlite3.Connection):
    connection.execute(
        "INSERT INTO server_contexts (id, name, normalized_name, created_at, updated_at) "
        "VALUES (1, 'Server', 'server', ?, ?)",
        (NOW, NOW),
    )
    connection.execute(
        "INSERT INTO account_contexts "
        "(id, server_context_id, name, normalized_name, created_at, updated_at) "
        "VALUES (1, 1, 'Account', 'account', ?, ?)",
        (NOW, NOW),
    )
    import_id = int(
        connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) "
            "VALUES ('timer_state', 'discord', ?, 'retained timer')",
            (NOW,),
        ).lastrowid
    )
    snapshot = TimerStateSnapshot(**{name: None for name in TimerStateSnapshot.model_fields})
    observation_id = int(
        connection.execute(
            "INSERT INTO timer_state_observations "
            "(account_context_id, snapshot_json, observed_at, import_event_id) "
            "VALUES (1, ?, ?, ?)",
            (snapshot.model_dump_json(), NOW, import_id),
        ).lastrowid
    )
    connection.execute(
        "INSERT INTO discord_message_aggregates "
        "(id, platform, guild_id, channel_id, message_id, first_received_at, "
        "last_received_at, created_at, updated_at) "
        "VALUES (1, 'discord', 'g', 'c', 'm', ?, ?, ?, ?)",
        (NOW, NOW, NOW, NOW),
    )
    connection.execute(
        "INSERT INTO discord_message_revisions "
        "(id, aggregate_id, normalized_payload_hash, revision_state, first_received_at, "
        "last_received_at, created_at, updated_at) "
        "VALUES (1, 1, 'hash', 'active', ?, ?, ?, ?)",
        (NOW, NOW, NOW, NOW),
    )
    source_id = int(
        connection.execute(
            "INSERT INTO discord_source_events "
            "(event_key, revision_id, event_kind, status, raw_text, received_at, "
            "last_seen_at, legacy_import_event_id, created_at, updated_at) "
            "VALUES ('timer-event', 1, 'MESSAGE_CREATE', 'succeeded', 'retained timer', "
            "?, ?, ?, ?, ?)",
            (NOW, NOW, import_id, NOW, NOW),
        ).lastrowid
    )
    attempt_id = int(
        connection.execute(
            "INSERT INTO discord_processing_attempts "
            "(source_event_id, attempt_number, status, retryable, parser_version, "
            "router_version, started_at, finished_at, created_at) "
            "VALUES (?, 1, 'succeeded', 0, 'p', 'r', ?, ?, ?)",
            (source_id, NOW, NOW, NOW),
        ).lastrowid
    )
    connection.execute(
        "INSERT INTO discord_source_event_server_attributions "
        "(source_event_id, status, server_name, created_at, updated_at) "
        "VALUES (?, 'resolved', 'Server', ?, ?)",
        (source_id, NOW, NOW),
    )
    connection.execute(
        "INSERT INTO discord_source_event_account_attributions "
        "(source_event_id, status, server_name, account_name, created_at, updated_at) "
        "VALUES (?, 'resolved', 'Server', 'Account', ?, ?)",
        (source_id, NOW, NOW),
    )
    identity = resolve_expected_projections(
        load_durable_projection_expectation_facts(connection, source_id)
    ).known_expected_identities[0]
    connection.execute(
        "INSERT INTO discord_projection_links "
        "(source_event_id, generation_id, projection_kind, projection_slot, "
        "projection_table, projection_row_id, state, claimed_at, completed_at, "
        "created_at, updated_at) "
        "VALUES (?, 1, ?, ?, 'timer_state_observations', ?, 'completed', ?, ?, ?, ?)",
        (
            source_id,
            identity.projection_kind,
            identity.projection_slot,
            observation_id,
            NOW,
            NOW,
            NOW,
            NOW,
        ),
    )
    assert ProjectionLinkRepository(connection).switch_current_generation() == 2
    admission = RetainedSourceReprojectionAdmissionService().admit(connection, source_id)
    assert admission.successful_attempt_id == attempt_id
    return admission


def _dump(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(connection.iterdump())


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _execute(connection, admission):
    return RetainedSourceTimerReprojectionExecutor().execute(connection, admission, COMPLETED_AT)


def test_executes_one_link_only_without_mutating_retained_evidence(database_path) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        protected = {
            table: tuple(connection.execute(f"SELECT * FROM {table}").fetchall())
            for table in (
                "discord_source_events",
                "discord_processing_attempts",
                "discord_source_event_server_attributions",
                "discord_source_event_account_attributions",
                "import_events",
                "timer_state_observations",
                "projection_generations",
            )
        }

        result = _execute(connection, admission)

        assert result.linked_count == 1
        assert result.replay_skipped is False
        assert result.projection_target == (
            "timer_state_observations",
            admission.payloads[0].historical_target_id,
        )
        current = ProjectionLinkRepository(connection).load_links(
            source_event_id=admission.source_event_id,
            generation_id=admission.current_generation_id,
        )
        assert len(current) == 1
        assert (
            current[0]["projection_kind"],
            current[0]["projection_slot"],
            current[0]["projection_table"],
            current[0]["projection_row_id"],
            current[0]["state"],
            current[0]["completed_at"],
        ) == (
            "catalog.timer_state",
            admission.expected_identities[0].projection_slot,
            "timer_state_observations",
            admission.payloads[0].historical_target_id,
            "completed",
            COMPLETED_AT.isoformat(),
        )
        for table, rows in protected.items():
            assert tuple(connection.execute(f"SELECT * FROM {table}").fetchall()) == rows
        assert _count(connection, "import_events") == 1
        assert _count(connection, "timer_state_observations") == 1


def test_exact_retry_is_a_no_write_replay(database_path) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        _execute(connection, admission)
        before = _dump(connection)

        replay = _execute(connection, admission)

        assert replay.linked_count == 0
        assert replay.replay_skipped is True
        assert _dump(connection) == before


def test_requires_a_caller_owned_transaction(database_path) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        connection.commit()
        before = _dump(connection)

        with pytest.raises(RetainedSourceTimerReprojectionError, match="caller-owned"):
            _execute(connection, admission)

        assert _dump(connection) == before


@pytest.mark.parametrize("completed_at", [datetime(2026, 8, 31, 13), "not-a-datetime"])
def test_rejects_invalid_completion_timestamp_without_writing(database_path, completed_at) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        before = _dump(connection)

        with pytest.raises((TypeError, ValueError)):
            RetainedSourceTimerReprojectionExecutor().execute(connection, admission, completed_at)

        assert _dump(connection) == before


@pytest.mark.parametrize(
    "forge",
    [
        lambda admission: replace(admission, source_family="wishlist"),
        lambda admission: replace(admission, current_generation_id=999),
        lambda admission: replace(admission, successful_attempt_id=999),
        lambda admission: replace(admission, import_event_id=999),
        lambda admission: replace(admission, expected_identities=()),
        lambda admission: replace(
            admission,
            payloads=(replace(admission.payloads[0], historical_target_id=999),),
        ),
    ],
    ids=("family", "generation", "attempt", "import", "identity", "target"),
)
def test_rejects_stale_or_forged_admission_without_writing(database_path, forge) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        forged = forge(admission)
        before = _dump(connection)

        with pytest.raises(RetainedSourceTimerReprojectionError):
            _execute(connection, forged)

        assert _dump(connection) == before


def test_rejects_generation_change_without_writing(database_path) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        assert ProjectionLinkRepository(connection).switch_current_generation() == 3
        before = _dump(connection)

        with pytest.raises(RetainedSourceTimerReprojectionError, match="stale"):
            _execute(connection, admission)

        assert _dump(connection) == before


@pytest.mark.parametrize(
    "corrupt",
    [
        "UPDATE discord_source_event_server_attributions "
        "SET status = 'unresolved', server_name = NULL",
        "UPDATE discord_source_event_account_attributions SET account_name = 'Other'",
        "UPDATE discord_source_events SET legacy_import_event_id = NULL",
        "UPDATE timer_state_observations SET snapshot_json = '{}'",
        "UPDATE timer_state_observations SET import_event_id = 999",
    ],
    ids=("server", "account", "provenance", "payload", "target-ownership"),
)
def test_revalidates_and_rejects_corrupted_durable_evidence_without_writing(
    database_path, corrupt
) -> None:
    with connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        connection.execute(corrupt)
        before = _dump(connection)

        with pytest.raises(RetainedSourceTimerReprojectionError):
            _execute(connection, admission)

        assert _dump(connection) == before


@pytest.mark.parametrize(
    ("state", "kind", "slot", "table", "target"),
    [
        ("claimed", "catalog.timer_state", "expected", None, None),
        ("completed", "catalog.timer_state", "wrong-slot", "timer_state_observations", 1),
        ("completed", "catalog.timer_state", "expected", "wishlist_observations", 1),
        ("completed", "catalog.timer_state", "expected", "timer_state_observations", 999),
        ("completed", "catalog.wishlist", "expected", "timer_state_observations", 1),
    ],
    ids=("claimed", "wrong-slot", "wrong-table", "wrong-target", "wrong-kind"),
)
def test_rejects_every_conflicting_current_link_without_writing(
    database_path, state, kind, slot, table, target
) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        projection_slot = (
            admission.expected_identities[0].projection_slot if slot == "expected" else slot
        )
        connection.execute(
            "INSERT INTO discord_projection_links "
            "(source_event_id, generation_id, projection_kind, projection_slot, "
            "projection_table, projection_row_id, state, claimed_at, completed_at, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                admission.source_event_id,
                admission.current_generation_id,
                kind,
                projection_slot,
                table,
                target,
                state,
                NOW,
                NOW if state == "completed" else None,
                NOW,
                NOW,
            ),
        )
        before = _dump(connection)

        with pytest.raises(RetainedSourceTimerReprojectionError):
            _execute(connection, admission)

        assert _dump(connection) == before


def test_rejects_an_additional_current_link_on_retry_without_writing(database_path) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        _execute(connection, admission)
        connection.execute(
            "INSERT INTO discord_projection_links "
            "(source_event_id, generation_id, projection_kind, projection_slot, state, "
            "claimed_at, created_at, updated_at) VALUES (?, ?, 'catalog.timer_state', "
            "'additional', 'claimed', ?, ?, ?)",
            (admission.source_event_id, admission.current_generation_id, NOW, NOW, NOW),
        )
        before = _dump(connection)

        with pytest.raises(RetainedSourceTimerReprojectionError):
            _execute(connection, admission)

        assert _dump(connection) == before


def test_rolls_back_a_partial_link_if_completion_fails(database_path, monkeypatch) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        before = _dump(connection)

        def fail_completion(*args, **kwargs):
            raise RuntimeError("completion failed")

        monkeypatch.setattr(ProjectionLinkRepository, "complete_claimed_link", fail_completion)
        with pytest.raises(RuntimeError, match="completion failed"):
            _execute(connection, admission)

        assert _dump(connection) == before
        assert (
            ProjectionLinkRepository(connection).load_links(
                source_event_id=admission.source_event_id,
                generation_id=admission.current_generation_id,
            )
            == ()
        )
