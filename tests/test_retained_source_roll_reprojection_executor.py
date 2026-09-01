from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.projection_link_repository import ProjectionLinkRepository
from moa.services.projection_authority import get_projection_authority
from moa.services.projection_expectations import (
    load_durable_projection_expectation_facts,
    resolve_expected_projections,
)
from moa.services.retained_source_reprojection_admission_service import (
    ReprojectionAdmissionRejection,
    RetainedSourceReprojectionAdmissionError,
    RetainedSourceReprojectionAdmissionService,
)
from moa.services.retained_source_roll_reprojection_executor import (
    RetainedSourceRollReprojectionError,
    RetainedSourceRollReprojectionExecutor,
)


NOW = "2026-08-31T12:00:00+00:00"
COMPLETED_AT = datetime(2026, 8, 31, 13, tzinfo=timezone.utc)


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "roll-reprojection.sqlite3"
    CatalogRepository(path)
    return path


def _fixture(
    connection: sqlite3.Connection,
    *,
    key: bool = True,
    rank: bool = True,
    kakera: bool = True,
    generations: int = 1,
    admit: bool = True,
):
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
    connection.execute(
        "INSERT INTO characters "
        "(id, name, series, normalized_name, normalized_series, created_at, updated_at) "
        "VALUES (1, 'Character', 'Series', 'character', 'series', ?, ?)",
        (NOW, NOW),
    )
    import_id = int(
        connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) "
            "VALUES ('roll', 'discord', ?, 'retained roll')",
            (NOW,),
        ).lastrowid
    )
    targets = {
        "catalog.roll": int(
            connection.execute(
                "INSERT INTO roll_observations "
                "(account_context_id, character_id, claim_rank, kakera_value, observed_at, import_event_id) "
                "VALUES (1, 1, ?, ?, ?, ?)",
                (1 if rank else None, 100 if kakera else None, NOW, import_id),
            ).lastrowid
        )
    }
    if key:
        targets["catalog.roll_key"] = int(
            connection.execute(
                "INSERT INTO harem_key_observations "
                "(account_context_id, character_id, character_name, normalized_character_name, "
                "key_type, key_count, kakera_value, observed_at, import_event_id) "
                "VALUES (1, 1, 'Character', 'character', 'bronze', 1, ?, ?, ?)",
                (100 if kakera else None, NOW, import_id),
            ).lastrowid
        )
    if rank:
        targets["catalog.roll_rank"] = int(
            connection.execute(
                "INSERT INTO rank_snapshots "
                "(character_id, claim_rank, like_rank, owner_name, observed_at, import_event_id) "
                "VALUES (1, 1, NULL, NULL, ?, ?)",
                (NOW, import_id),
            ).lastrowid
        )
    if kakera:
        targets["catalog.roll_server_character"] = int(
            connection.execute(
                "INSERT INTO server_character_observations "
                "(server_context_id, character_id, kakera_value, observed_at, import_event_id) "
                "VALUES (1, 1, 100, ?, ?)",
                (NOW, import_id),
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
            "VALUES ('roll-event', 1, 'MESSAGE_CREATE', 'succeeded', 'retained roll', "
            "?, ?, ?, ?, ?)",
            (NOW, NOW, import_id, NOW, NOW),
        ).lastrowid
    )
    connection.execute(
        "INSERT INTO discord_processing_attempts "
        "(source_event_id, attempt_number, status, retryable, parser_version, "
        "router_version, started_at, finished_at, created_at) "
        "VALUES (?, 1, 'succeeded', 0, 'p', 'r', ?, ?, ?)",
        (source_id, NOW, NOW, NOW),
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
    expected = resolve_expected_projections(
        load_durable_projection_expectation_facts(connection, source_id)
    ).known_expected_identities
    for generation_id in range(1, generations + 1):
        for identity in expected:
            authority = get_projection_authority(identity.projection_kind)
            connection.execute(
                "INSERT INTO discord_projection_links "
                "(source_event_id, generation_id, projection_kind, projection_slot, "
                "projection_table, projection_row_id, state, claimed_at, completed_at, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?)",
                (
                    source_id,
                    generation_id,
                    identity.projection_kind,
                    identity.projection_slot,
                    authority.target_table,
                    targets[identity.projection_kind],
                    NOW,
                    NOW,
                    NOW,
                    NOW,
                ),
            )
        assert ProjectionLinkRepository(connection).switch_current_generation() == generation_id + 1
    if not admit:
        return source_id
    return RetainedSourceReprojectionAdmissionService().admit(connection, source_id)


def _execute(connection, admission):
    return RetainedSourceRollReprojectionExecutor().execute(connection, admission, COMPLETED_AT)


def _dump(connection):
    return tuple(connection.iterdump())


@pytest.mark.parametrize(
    ("key", "rank", "kakera", "count"),
    (
        (True, False, False, 2),
        (True, True, False, 3),
        (True, False, True, 3),
        (True, True, True, 4),
    ),
)
def test_executes_every_admitted_identity_combination_atomically(
    database_path, key, rank, kakera, count
) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection, key=key, rank=rank, kakera=kakera)
        protected = {
            table: tuple(connection.execute(f"SELECT * FROM {table}").fetchall())
            for table in (
                "discord_source_events",
                "discord_processing_attempts",
                "discord_source_event_server_attributions",
                "discord_source_event_account_attributions",
                "import_events",
                "roll_observations",
                "harem_key_observations",
                "rank_snapshots",
                "server_character_observations",
                "projection_generations",
            )
        }

        result = _execute(connection, admission)

        assert result.linked_count == count
        assert result.replay_skipped is False
        current = ProjectionLinkRepository(connection).load_links(
            source_event_id=admission.source_event_id,
            generation_id=admission.current_generation_id,
        )
        assert len(current) == count
        assert all(row["state"] == "completed" for row in current)
        assert {(row["projection_kind"], row["projection_slot"]) for row in current} == {
            (item.projection_kind, item.projection_slot) for item in admission.expected_identities
        }
        for table, rows in protected.items():
            assert tuple(connection.execute(f"SELECT * FROM {table}").fetchall()) == rows


def test_rejects_base_only_evidence_without_key_proof_as_unknown_without_writes(
    database_path,
) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        source_event_id = _fixture(connection, key=False, rank=False, kakera=False, admit=False)
        before = _dump(connection)

        with pytest.raises(RetainedSourceReprojectionAdmissionError) as caught:
            RetainedSourceReprojectionAdmissionService().admit(connection, source_event_id)

        assert caught.value.reason is ReprojectionAdmissionRejection.EXPECTATIONS_UNKNOWN
        assert _dump(connection) == before
        assert connection.in_transaction


def test_exact_multi_link_replay_is_no_write_and_revalidates_every_generation(
    database_path,
) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection, generations=3)
        _execute(connection, admission)
        before = _dump(connection)
        replay = _execute(connection, admission)
        assert replay.linked_count == 0
        assert replay.replay_skipped is True
        assert _dump(connection) == before

        connection.execute(
            "UPDATE discord_projection_links SET projection_row_id = 999 "
            "WHERE source_event_id = ? AND generation_id = 1 AND projection_kind = 'catalog.roll'",
            (admission.source_event_id,),
        )
        corrupted = _dump(connection)
        with pytest.raises(RetainedSourceRollReprojectionError, match="historical evidence"):
            _execute(connection, admission)
        assert _dump(connection) == corrupted


@pytest.mark.parametrize(
    "forge",
    (
        lambda a: replace(a, source_family="timer_state"),
        lambda a: replace(a, current_generation_id=999),
        lambda a: replace(a, successful_attempt_id=999),
        lambda a: replace(a, import_event_id=999),
        lambda a: replace(a, expected_identities=()),
        lambda a: replace(a, payloads=a.payloads[:-1]),
        lambda a: replace(a, payloads=(a.payloads[0],) * len(a.payloads)),
        lambda a: replace(
            a, payloads=(replace(a.payloads[0], historical_target_id=999), *a.payloads[1:])
        ),
        lambda a: replace(
            a,
            payloads=(
                replace(a.payloads[0], target_table="wishlist_observations"),
                *a.payloads[1:],
            ),
        ),
    ),
    ids=(
        "family",
        "generation",
        "attempt",
        "import",
        "empty",
        "partial",
        "duplicate",
        "target",
        "table",
    ),
)
def test_rejects_stale_malformed_partial_duplicate_or_wrong_target_admission(
    database_path, forge
) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        before = _dump(connection)
        with pytest.raises(RetainedSourceRollReprojectionError):
            _execute(connection, forge(admission))
        assert _dump(connection) == before


@pytest.mark.parametrize(
    "corrupt",
    (
        "UPDATE discord_source_event_account_attributions SET account_name = 'Other'",
        "UPDATE discord_source_events SET legacy_import_event_id = NULL",
        "UPDATE roll_observations SET import_event_id = 999",
        "UPDATE roll_observations SET claim_rank = 2",
        "UPDATE harem_key_observations SET key_type = 'gold'",
        "UPDATE harem_key_observations SET kakera_value = 999",
        "UPDATE rank_snapshots SET claim_rank = 999",
        "UPDATE server_character_observations SET kakera_value = 999",
    ),
    ids=(
        "attribution",
        "provenance",
        "import-owner",
        "base-rank",
        "key-slot",
        "key-kakera",
        "rank",
        "server-kakera",
    ),
)
def test_rejects_divergent_durable_scope_ownership_and_cross_target_coherence(
    database_path, corrupt
) -> None:
    with connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        connection.execute(corrupt)
        before = _dump(connection)
        with pytest.raises(RetainedSourceRollReprojectionError):
            _execute(connection, admission)
        assert _dump(connection) == before


@pytest.mark.parametrize(
    ("state", "kind", "slot", "table", "target"),
    (
        ("claimed", "catalog.roll", "expected", None, None),
        ("completed", "catalog.roll", "wrong", "roll_observations", 1),
        ("completed", "catalog.roll", "expected", "wishlist_observations", 1),
        ("completed", "catalog.roll", "expected", "roll_observations", 999),
        ("completed", "catalog.timer_state", "expected", "roll_observations", 1),
    ),
)
def test_rejects_current_link_conflicts_without_writing(
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
        with pytest.raises(RetainedSourceRollReprojectionError):
            _execute(connection, admission)
        assert _dump(connection) == before


@pytest.mark.parametrize("failure_call", range(1, 9))
def test_savepoint_rolls_back_each_claim_and_completion_stage(
    database_path, monkeypatch, failure_call
) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        before = _dump(connection)
        claim = ProjectionLinkRepository.claim_link
        complete = ProjectionLinkRepository.complete_claimed_link
        calls = 0

        def maybe_fail_claim(repository, **kwargs):
            nonlocal calls
            calls += 1
            if calls == failure_call:
                raise RuntimeError("stage failed")
            return claim(repository, **kwargs)

        def maybe_fail_complete(repository, **kwargs):
            nonlocal calls
            calls += 1
            if calls == failure_call:
                raise RuntimeError("stage failed")
            return complete(repository, **kwargs)

        monkeypatch.setattr(ProjectionLinkRepository, "claim_link", maybe_fail_claim)
        monkeypatch.setattr(ProjectionLinkRepository, "complete_claimed_link", maybe_fail_complete)
        with pytest.raises(RuntimeError, match="stage failed"):
            _execute(connection, admission)
        assert _dump(connection) == before
        assert connection.in_transaction


def test_requires_transaction_and_valid_timestamp(database_path) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        connection.commit()
        before = _dump(connection)
        with pytest.raises(RetainedSourceRollReprojectionError, match="caller-owned"):
            _execute(connection, admission)
        assert _dump(connection) == before

    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = RetainedSourceReprojectionAdmissionService().admit(
            connection, admission.source_event_id
        )
        before = _dump(connection)
        with pytest.raises((TypeError, ValueError)):
            RetainedSourceRollReprojectionExecutor().execute(
                connection, admission, datetime(2026, 8, 31, 13)
            )
        assert _dump(connection) == before
