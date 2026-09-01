from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.projection_link_repository import ProjectionLinkRepository
from moa.services.projection_expectations import (
    load_durable_projection_expectation_facts,
    resolve_expected_projections,
)
from moa.services.retained_source_reprojection_admission_service import (
    RetainedSourceReprojectionAdmissionService,
)
from moa.services.retained_source_server_settings_reprojection_executor import (
    RetainedSourceServerSettingsReprojectionError,
    RetainedSourceServerSettingsReprojectionExecutor,
)


NOW = "2026-08-31T12:00:00+00:00"
COMPLETED_AT = datetime(2026, 8, 31, 13, tzinfo=timezone.utc)


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "server-settings-reprojection.sqlite3"
    CatalogRepository(path)
    return path


def _fixture(connection: sqlite3.Connection, historical_generations: int = 2):
    connection.execute(
        "INSERT INTO server_contexts (id, name, normalized_name, created_at, updated_at) "
        "VALUES (1, 'Server', 'server', ?, ?)",
        (NOW, NOW),
    )
    import_id = int(
        connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) "
            "VALUES ('server_settings', 'discord', ?, 'retained settings')",
            (NOW,),
        ).lastrowid
    )
    observation_id = int(
        connection.execute(
            "INSERT INTO server_settings_observations (server_context_id, server_premium, "
            "prefix, language, claim_reset_minutes, reset_minute, reset_shift_minutes, "
            "rolls_per_hour, claim_reaction_expiry_seconds, "
            "claimed_character_rarity_multiplier, kakera_bonus_percent, "
            "sphere_bonus_percent, game_mode, channel_instance, metrics_json, observed_at, "
            "import_event_id) VALUES (1, 1, '$', 'en', 180, '00', 0, 10, 30, 1, 0, 0, "
            "1, 1, '[]', ?, ?)",
            (NOW, import_id),
        ).lastrowid
    )
    connection.execute(
        "INSERT INTO discord_message_aggregates (id, platform, guild_id, channel_id, "
        "message_id, first_received_at, last_received_at, created_at, updated_at) "
        "VALUES (1, 'discord', 'g', 'c', 'm', ?, ?, ?, ?)",
        (NOW, NOW, NOW, NOW),
    )
    connection.execute(
        "INSERT INTO discord_message_revisions (id, aggregate_id, normalized_payload_hash, "
        "revision_state, first_received_at, last_received_at, created_at, updated_at) "
        "VALUES (1, 1, 'hash', 'active', ?, ?, ?, ?)",
        (NOW, NOW, NOW, NOW),
    )
    source_id = int(
        connection.execute(
            "INSERT INTO discord_source_events (event_key, revision_id, event_kind, status, "
            "raw_text, received_at, last_seen_at, legacy_import_event_id, created_at, "
            "updated_at) VALUES ('settings-event', 1, 'MESSAGE_CREATE', 'succeeded', "
            "'retained settings', ?, ?, ?, ?, ?)",
            (NOW, NOW, import_id, NOW, NOW),
        ).lastrowid
    )
    connection.execute(
        "INSERT INTO discord_processing_attempts (source_event_id, attempt_number, status, "
        "retryable, parser_version, router_version, started_at, finished_at, created_at) "
        "VALUES (?, 1, 'succeeded', 0, 'p', 'r', ?, ?, ?)",
        (source_id, NOW, NOW, NOW),
    )
    connection.execute(
        "INSERT INTO discord_source_event_server_attributions (source_event_id, status, "
        "server_name, created_at, updated_at) VALUES (?, 'resolved', 'Server', ?, ?)",
        (source_id, NOW, NOW),
    )
    identity = resolve_expected_projections(
        load_durable_projection_expectation_facts(connection, source_id)
    ).known_expected_identities[0]
    for generation_id in range(1, historical_generations + 1):
        if generation_id > 1:
            assert ProjectionLinkRepository(connection).switch_current_generation() == generation_id
        connection.execute(
            "INSERT INTO discord_projection_links (source_event_id, generation_id, "
            "projection_kind, projection_slot, projection_table, projection_row_id, state, "
            "claimed_at, completed_at, created_at, updated_at) VALUES "
            "(?, ?, ?, ?, 'server_settings_observations', ?, 'completed', ?, ?, ?, ?)",
            (
                source_id,
                generation_id,
                identity.projection_kind,
                identity.projection_slot,
                observation_id,
                NOW,
                NOW,
                NOW,
                NOW,
            ),
        )
    assert ProjectionLinkRepository(connection).switch_current_generation() == (
        historical_generations + 1
    )
    admission = RetainedSourceReprojectionAdmissionService().admit(connection, source_id)
    assert admission.account is None
    return admission


def _dump(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(connection.iterdump())


def _execute(connection, admission):
    return RetainedSourceServerSettingsReprojectionExecutor().execute(
        connection, admission, COMPLETED_AT
    )


def test_creates_only_the_missing_current_link_and_exact_replay_is_no_write(
    database_path,
) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        protected = {
            table: tuple(connection.execute(f"SELECT * FROM {table}").fetchall())
            for table in (
                "discord_source_events",
                "discord_processing_attempts",
                "discord_source_event_server_attributions",
                "import_events",
                "server_settings_observations",
                "projection_generations",
            )
        }

        result = _execute(connection, admission)

        assert result.source_family == "server_settings"
        assert result.linked_count == 1 and result.replay_skipped is False
        assert result.projection_target == (
            "server_settings_observations",
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
        ) == (
            "catalog.server_settings",
            admission.expected_identities[0].projection_slot,
            "server_settings_observations",
            admission.payloads[0].historical_target_id,
            "completed",
        )
        for table, rows in protected.items():
            assert tuple(connection.execute(f"SELECT * FROM {table}").fetchall()) == rows
        before = _dump(connection)
        replay = _execute(connection, admission)
        assert replay.linked_count == 0 and replay.replay_skipped is True
        assert _dump(connection) == before


def test_requires_caller_owned_transaction(database_path) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        connection.commit()
        before = _dump(connection)
        with pytest.raises(RetainedSourceServerSettingsReprojectionError, match="caller-owned"):
            _execute(connection, admission)
        assert _dump(connection) == before


@pytest.mark.parametrize(
    "forge",
    (
        lambda admission: replace(admission, source_family="kakeraloot_settings"),
        lambda admission: replace(admission, account="inferred-account"),
        lambda admission: replace(admission, current_generation_id=999),
        lambda admission: replace(admission, successful_attempt_id=999),
        lambda admission: replace(admission, import_event_id=999),
        lambda admission: replace(admission, expected_identities=()),
        lambda admission: replace(
            admission,
            payloads=(replace(admission.payloads[0], historical_target_id=999),),
        ),
    ),
    ids=("family", "account-inference", "generation", "attempt", "import", "identity", "target"),
)
def test_rejects_stale_or_forged_admission_without_writing(database_path, forge) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        before = _dump(connection)
        with pytest.raises(RetainedSourceServerSettingsReprojectionError):
            _execute(connection, forge(admission))
        assert _dump(connection) == before


@pytest.mark.parametrize(
    "corrupt",
    (
        "UPDATE discord_source_event_server_attributions SET status = 'unresolved', "
        "server_name = NULL",
        "UPDATE discord_source_events SET legacy_import_event_id = NULL",
        "UPDATE server_settings_observations SET metrics_json = 'not-json'",
        "UPDATE server_settings_observations SET import_event_id = 999",
        "UPDATE server_settings_observations SET server_context_id = 999",
    ),
    ids=("unknown-server", "provenance", "payload", "target-ownership", "server-scope"),
)
def test_rejects_corrupted_evidence_scope_or_provenance_without_writing(
    database_path, corrupt
) -> None:
    with connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        connection.execute(corrupt)
        before = _dump(connection)
        with pytest.raises(RetainedSourceServerSettingsReprojectionError):
            _execute(connection, admission)
        assert _dump(connection) == before


def test_revalidates_every_historical_generation_on_replay(database_path) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection, historical_generations=3)
        _execute(connection, admission)
        connection.execute(
            "UPDATE discord_projection_links SET projection_row_id = 999 "
            "WHERE source_event_id = ? AND generation_id = 1",
            (admission.source_event_id,),
        )
        before = _dump(connection)
        with pytest.raises(
            RetainedSourceServerSettingsReprojectionError, match="historical evidence"
        ):
            _execute(connection, admission)
        assert _dump(connection) == before


@pytest.mark.parametrize(
    ("state", "kind", "slot", "table", "target"),
    (
        ("claimed", "catalog.server_settings", "expected", None, None),
        (
            "completed",
            "catalog.server_settings",
            "wrong-slot",
            "server_settings_observations",
            1,
        ),
        ("completed", "catalog.server_settings", "expected", "wishlist_observations", 1),
        (
            "completed",
            "catalog.server_settings",
            "expected",
            "server_settings_observations",
            999,
        ),
        (
            "completed",
            "catalog.kakeraloot_settings",
            "expected",
            "server_settings_observations",
            1,
        ),
    ),
)
def test_rejects_incompatible_current_links_without_writing(
    database_path, state, kind, slot, table, target
) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        projection_slot = (
            admission.expected_identities[0].projection_slot if slot == "expected" else slot
        )
        connection.execute(
            "INSERT INTO discord_projection_links (source_event_id, generation_id, "
            "projection_kind, projection_slot, projection_table, projection_row_id, state, "
            "claimed_at, completed_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        with pytest.raises(RetainedSourceServerSettingsReprojectionError):
            _execute(connection, admission)
        assert _dump(connection) == before


def test_completion_failure_rolls_back_only_the_new_link(database_path, monkeypatch) -> None:
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
        assert connection.in_transaction
