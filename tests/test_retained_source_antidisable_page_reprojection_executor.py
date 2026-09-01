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
from moa.services.retained_source_antidisable_page_reprojection_executor import (
    RetainedSourceAntidisablePageReprojectionError,
    RetainedSourceAntidisablePageReprojectionExecutor,
)
from moa.services.retained_source_reprojection_admission_service import (
    RetainedSourceReprojectionAdmissionService,
)


NOW = "2026-08-31T12:00:00+00:00"
COMPLETED_AT = datetime(2026, 8, 31, 13, tzinfo=timezone.utc)


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "antidisable-page-reprojection.sqlite3"
    CatalogRepository(path)
    return path


def _fixture(connection: sqlite3.Connection):
    connection.execute(
        "INSERT INTO server_contexts "
        "(id, name, normalized_name, created_at, updated_at) "
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
            "VALUES ('antidisable', 'discord', ?, 'retained page')",
            (NOW,),
        ).lastrowid
    )
    connection.execute(
        "INSERT INTO harem_scans "
        "(id, account_context_id, expected_page_count, started_at, completed_at, scan_kind) "
        "VALUES (1, 1, 2, ?, NULL, 'antidisable')",
        (NOW,),
    )
    connection.execute(
        "INSERT INTO harem_scan_pages "
        "(harem_scan_id, page_number, import_event_id, slots_used, slots_capacity) "
        "VALUES (1, 1, ?, 2, 10)",
        (import_id,),
    )
    connection.executemany(
        "INSERT INTO antidisable_series_observations "
        "(account_context_id, series_name, normalized_series_name, "
        "antidisabled_character_count, observed_at, import_event_id, harem_scan_id) "
        "VALUES (1, ?, ?, 4, ?, ?, 1)",
        (
            ("First Series", "first series", NOW, import_id),
            ("Second Series", "second series", NOW, import_id),
        ),
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
            "VALUES ('antidisable-event', 1, 'MESSAGE_CREATE', 'succeeded', "
            "'retained page', ?, ?, ?, ?, ?)",
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
        "VALUES (?, 1, ?, ?, 'import_events', ?, 'completed', ?, ?, ?, ?)",
        (
            source_id,
            identity.projection_kind,
            identity.projection_slot,
            import_id,
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


def _execute(connection, admission):
    return RetainedSourceAntidisablePageReprojectionExecutor().execute(
        connection, admission, COMPLETED_AT
    )


def test_executes_only_the_missing_current_generation_link(database_path) -> None:
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
                "harem_scans",
                "harem_scan_pages",
                "antidisable_series_observations",
                "projection_generations",
            )
        }

        result = _execute(connection, admission)

        assert result.linked_count == 1
        assert result.replay_skipped is False
        assert result.projection_target == ("import_events", admission.import_event_id)
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
            "catalog.antidisable_page",
            admission.expected_identities[0].projection_slot,
            "import_events",
            admission.import_event_id,
            "completed",
            COMPLETED_AT.isoformat(),
        )
        for table, rows in protected.items():
            assert tuple(connection.execute(f"SELECT * FROM {table}").fetchall()) == rows


def test_exact_replay_is_no_write(database_path) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        _execute(connection, admission)
        before = _dump(connection)

        replay = _execute(connection, admission)

        assert replay.linked_count == 0
        assert replay.replay_skipped is True
        assert _dump(connection) == before


def test_replay_revalidates_every_historical_generation(database_path) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        first = _fixture(connection)
        _execute(connection, first)
        assert ProjectionLinkRepository(connection).switch_current_generation() == 3
        admission = RetainedSourceReprojectionAdmissionService().admit(
            connection, first.source_event_id
        )
        _execute(connection, admission)
        connection.execute(
            "UPDATE discord_projection_links SET projection_row_id = 999 "
            "WHERE source_event_id = ? AND generation_id = 1",
            (admission.source_event_id,),
        )
        before = _dump(connection)

        with pytest.raises(
            RetainedSourceAntidisablePageReprojectionError,
            match="historical evidence changed",
        ):
            _execute(connection, admission)

        assert _dump(connection) == before


def test_requires_caller_owned_transaction(database_path) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        connection.commit()
        before = _dump(connection)

        with pytest.raises(RetainedSourceAntidisablePageReprojectionError, match="caller-owned"):
            _execute(connection, admission)

        assert _dump(connection) == before


@pytest.mark.parametrize("completed_at", [datetime(2026, 8, 31, 13), "bad"])
def test_rejects_invalid_completion_timestamp(database_path, completed_at) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        before = _dump(connection)

        with pytest.raises((TypeError, ValueError)):
            RetainedSourceAntidisablePageReprojectionExecutor().execute(
                connection, admission, completed_at
            )

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
            payloads=(replace(admission.payloads[0], target_table="wishlist_observations"),),
        ),
        lambda admission: replace(
            admission,
            payloads=(replace(admission.payloads[0], historical_target_id=999),),
        ),
    ],
    ids=("family", "generation", "attempt", "import", "identity", "table", "target"),
)
def test_rejects_stale_forged_or_target_mismatched_admission(database_path, forge) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        before = _dump(connection)

        with pytest.raises(RetainedSourceAntidisablePageReprojectionError):
            _execute(connection, forge(admission))

        assert _dump(connection) == before


@pytest.mark.parametrize(
    "corrupt",
    [
        "UPDATE discord_source_event_server_attributions SET server_name = 'Other'",
        "UPDATE discord_source_event_account_attributions SET account_name = 'Other'",
        "UPDATE discord_source_events SET legacy_import_event_id = NULL",
        "UPDATE harem_scans SET scan_kind = 'keys'",
        "UPDATE harem_scans SET expected_page_count = 0",
        "UPDATE harem_scan_pages SET page_number = 3",
        "UPDATE harem_scan_pages SET slots_used = NULL",
        "UPDATE antidisable_series_observations SET account_context_id = 2",
        "UPDATE antidisable_series_observations SET normalized_series_name = 'wrong'",
        "UPDATE antidisable_series_observations SET harem_scan_id = 2",
    ],
    ids=(
        "server",
        "account",
        "provenance",
        "workflow",
        "page-count",
        "page-number",
        "pre-migration-slot-evidence",
        "series-context",
        "series-normalization",
        "series-scan",
    ),
)
def test_revalidates_complete_page_context_and_series_integrity(database_path, corrupt) -> None:
    with connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        if "account_context_id = 2" in corrupt:
            connection.execute(
                "INSERT INTO account_contexts "
                "(id, server_context_id, name, normalized_name, created_at, updated_at) "
                "VALUES (2, 1, 'Other', 'other', ?, ?)",
                (NOW, NOW),
            )
        connection.execute(corrupt)
        before = _dump(connection)

        with pytest.raises(RetainedSourceAntidisablePageReprojectionError):
            _execute(connection, admission)

        assert _dump(connection) == before


@pytest.mark.parametrize(
    ("state", "kind", "slot", "table", "target"),
    [
        ("claimed", "catalog.antidisable_page", "expected", None, None),
        ("completed", "catalog.antidisable_page", "wrong", "import_events", 1),
        ("completed", "catalog.antidisable_page", "expected", "wishlist_observations", 1),
        ("completed", "catalog.antidisable_page", "expected", "import_events", 999),
        ("completed", "catalog.wishlist", "expected", "import_events", 1),
    ],
    ids=("claimed", "slot", "table", "target", "kind"),
)
def test_rejects_every_incompatible_current_link(
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

        with pytest.raises(RetainedSourceAntidisablePageReprojectionError):
            _execute(connection, admission)

        assert _dump(connection) == before


def test_ordered_series_forgery_is_rejected(database_path) -> None:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        admission = _fixture(connection)
        fields = dict(admission.payloads[0].fields)
        fields["series"] = tuple(reversed(fields["series"]))
        forged_payload = replace(admission.payloads[0], fields=tuple(sorted(fields.items())))
        forged = replace(admission, payloads=(forged_payload,))
        before = _dump(connection)

        with pytest.raises(RetainedSourceAntidisablePageReprojectionError):
            _execute(connection, forged)

        assert _dump(connection) == before


def test_rolls_back_claim_when_completion_fails(database_path, monkeypatch) -> None:
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
