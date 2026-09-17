from dataclasses import asdict
import json

import pytest
from typer.testing import CliRunner

from moa.database.sqlite import connect
from moa.services.roll_key_display_candidate_evaluator import (
    RollKeyDisplayCandidateEvaluator,
    RollKeyDisplayCandidateStatus as Status,
)
from moa.services.roll_key_display_candidate_inventory_service import (
    RollKeyDisplayCandidateInventoryService,
    RollKeyDisplayInventoryMode,
)
from moa.services.roll_key_display_presence_backfill_service import (
    RollKeyDisplayBackfillStatus,
    RollKeyDisplayPresenceBackfillService,
)
from test_roll_key_display_presence import DISPLAYED_KEY_ROLL, NO_KEY_ROLL, _seed_retained_roll


@pytest.mark.parametrize(
    ("raw", "expected"),
    ((NO_KEY_ROLL, False), (DISPLAYED_KEY_ROLL, True)),
)
def test_eligible_null_is_read_only_and_parser_backed(tmp_path, monkeypatch, raw, expected) -> None:
    path = tmp_path / "eligible.db"
    _, _, roll_id = _seed_retained_roll(path, raw, without_key=True)
    with connect(path) as connection:
        before_database = tuple(connection.iterdump())
        before_roll = tuple(connection.execute("SELECT * FROM roll_observations").fetchall())
        before_keys = tuple(connection.execute("SELECT * FROM harem_key_observations").fetchall())

    import moa.services.roll_key_display_candidate_inventory_service as inventory_module

    original_connect = inventory_module.connect_read_only
    opened = []

    class SpyConnection:
        def __init__(self, connection):
            self.connection = connection
            self.changes_at_close = None

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def close(self):
            self.changes_at_close = self.connection.total_changes
            self.connection.close()

    def spy(database_path):
        connection = SpyConnection(original_connect(database_path))
        opened.append(connection)
        return connection

    monkeypatch.setattr(inventory_module, "connect_read_only", spy)
    report = RollKeyDisplayCandidateInventoryService(path).scan(artifact_label="fixture-1")

    assert len(opened) == 1
    assert opened[0].changes_at_close == 0
    assert len(report.rows) == 1
    row = report.rows[0]
    assert row.roll_observation_id == roll_id
    assert row.candidate_status is Status.ELIGIBLE_NULL
    assert row.proposed_value is expected
    assert row.current_displayed_key_count_present is None
    assert row.parser_version == "p"
    assert report.summary.eligible_proposed_true == int(expected)
    assert report.summary.eligible_proposed_false == int(not expected)
    assert report.informational_only and report.requires_re_evaluation_for_mutation
    serialized = json.dumps(asdict(report), default=str)
    assert raw not in serialized
    assert "raw_text" not in serialized
    assert "payload_json" not in serialized
    assert "Character" not in serialized
    assert "Account" not in serialized
    with connect(path) as connection:
        assert tuple(connection.iterdump()) == before_database
        assert tuple(connection.execute("SELECT * FROM roll_observations").fetchall()) == before_roll
        assert tuple(connection.execute("SELECT * FROM harem_key_observations").fetchall()) == before_keys


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("UPDATE roll_observations SET displayed_key_count_present = 0", Status.ALREADY_ESTABLISHED_MATCHING),
        ("UPDATE roll_observations SET displayed_key_count_present = 1", Status.ALREADY_ESTABLISHED_CONFLICT),
        ("UPDATE discord_source_events SET legacy_import_event_id = NULL", Status.SOURCE_MISSING),
        ("UPDATE discord_source_events SET status = 'failed'", Status.SOURCE_NOT_SUCCEEDED),
        ("UPDATE discord_source_events SET raw_text = ''", Status.SOURCE_UNUSABLE),
        ("UPDATE discord_source_events SET raw_evidence_expired_at = 'now'", Status.SOURCE_EXPIRED),
        ("UPDATE import_events SET raw_message_expired_at = 'now'", Status.SOURCE_EXPIRED),
        ("UPDATE import_events SET kind = 'wishlist'", Status.NON_ROLL_SOURCE),
        ("UPDATE discord_source_events SET raw_text = 'not a roll'", Status.PARSE_FAILURE),
        ("UPDATE discord_processing_attempts SET status = 'failed'", Status.PARSER_PROVENANCE_MISSING_OR_AMBIGUOUS),
        ("UPDATE discord_source_event_account_attributions SET status = 'unresolved', server_name = NULL, account_name = NULL", Status.ATTRIBUTION_UNRESOLVED),
        ("UPDATE discord_source_event_account_attributions SET status = 'ambiguous', server_name = NULL, account_name = NULL", Status.ATTRIBUTION_AMBIGUOUS),
        ("UPDATE discord_source_event_account_attributions SET account_name = 'Other'", Status.ATTRIBUTION_CONFLICT),
        ("UPDATE discord_source_event_server_attributions SET status = 'unresolved', server_name = NULL", Status.ATTRIBUTION_UNRESOLVED),
        ("UPDATE characters SET normalized_name = 'other'", Status.IDENTITY_MISMATCH),
        ("UPDATE characters SET normalized_series = 'other'", Status.IDENTITY_MISMATCH),
        ("UPDATE roll_observations SET claim_rank = NULL", Status.IDENTITY_MISMATCH),
        ("UPDATE roll_observations SET kakera_value = 99", Status.IDENTITY_MISMATCH),
    ),
)
def test_inventory_taxonomy_on_disposable_fixture(tmp_path, mutation, expected) -> None:
    path = tmp_path / "status.db"
    _seed_retained_roll(path, NO_KEY_ROLL, without_key=True)
    with connect(path) as connection:
        connection.execute(mutation)
        connection.commit()

    report = RollKeyDisplayCandidateInventoryService(path).scan(mode=RollKeyDisplayInventoryMode.AUDIT)
    assert len(report.rows) == 1
    assert report.rows[0].candidate_status is expected
    assert report.rows[0].proposed_value is None
    assert report.summary.status_counts[expected.value] == 1


def test_source_and_roll_link_failures(tmp_path) -> None:
    path = tmp_path / "links.db"
    source_id, _, roll_id = _seed_retained_roll(path, NO_KEY_ROLL, without_key=True)
    with connect(path) as connection:
        connection.execute("UPDATE discord_source_events SET legacy_import_event_id = NULL")
        connection.commit()
        source_result = RollKeyDisplayCandidateEvaluator.evaluate(
            connection, source_event_id=source_id
        )
        assert source_result.status is Status.IMPORT_LINK_MISSING

    report = RollKeyDisplayCandidateInventoryService(path).scan()
    assert report.rows[0].roll_observation_id == roll_id
    assert report.rows[0].candidate_status is Status.SOURCE_MISSING
    with connect(path) as connection:
        assert RollKeyDisplayCandidateEvaluator.evaluate(
            connection, source_event_id=source_id + 1000
        ).status is Status.SOURCE_MISSING


def test_multiple_succeeded_attempts_fail_closed(tmp_path) -> None:
    path = tmp_path / "attempts.db"
    source_id, _, _ = _seed_retained_roll(path, NO_KEY_ROLL, without_key=True)
    with connect(path) as connection:
        connection.execute(
            "INSERT INTO discord_processing_attempts "
            "(source_event_id, attempt_number, status, retryable, parser_version, router_version, "
            "started_at, finished_at, created_at) "
            "SELECT source_event_id, 2, status, retryable, parser_version, router_version, "
            "started_at, finished_at, created_at "
            "FROM discord_processing_attempts WHERE source_event_id = ?",
            (source_id,),
        )
        connection.commit()
        result = RollKeyDisplayCandidateEvaluator.evaluate(
            connection, source_event_id=source_id
        )
    assert result.status is Status.PARSER_PROVENANCE_MISSING_OR_AMBIGUOUS


def test_ambiguous_links_and_roll_target_fail_closed(tmp_path) -> None:
    path = tmp_path / "ambiguous.db"
    source_id, import_id, _ = _seed_retained_roll(path, NO_KEY_ROLL, without_key=True)
    with connect(path) as connection:
        connection.execute(
            "INSERT INTO roll_observations "
            "(account_context_id, character_id, claim_rank, kakera_value, observed_at, import_event_id) "
            "SELECT account_context_id, character_id, claim_rank, kakera_value, observed_at, import_event_id "
            "FROM roll_observations"
        )
        connection.commit()
        result = RollKeyDisplayCandidateEvaluator.evaluate(
            connection, source_event_id=source_id
        )
        assert result.status is Status.ROLL_TARGET_AMBIGUOUS
        report = RollKeyDisplayCandidateInventoryService(path).scan()
        assert len(report.rows) == 2
        assert all(row.candidate_status is Status.ROLL_TARGET_AMBIGUOUS for row in report.rows)
        connection.execute("DELETE FROM roll_observations WHERE import_event_id = ?", (import_id,))
        connection.commit()
        result = RollKeyDisplayCandidateEvaluator.evaluate(
            connection, source_event_id=source_id
        )
        assert result.status is Status.ROLL_TARGET_MISSING


def test_ambiguous_import_link_fails_closed(tmp_path) -> None:
    path = tmp_path / "duplicate-source.db"
    source_id, _, _ = _seed_retained_roll(path, NO_KEY_ROLL, without_key=True)
    with connect(path) as connection:
        connection.execute(
            "INSERT INTO discord_message_aggregates "
            "(id, platform, guild_id, channel_id, message_id, first_received_at, last_received_at, created_at, updated_at) "
            "SELECT 2, platform, guild_id, channel_id, 'other-message', first_received_at, last_received_at, created_at, updated_at "
            "FROM discord_message_aggregates WHERE id = 1"
        )
        connection.execute(
            "INSERT INTO discord_message_revisions "
            "(id, aggregate_id, normalized_payload_hash, revision_state, first_received_at, last_received_at, created_at, updated_at) "
            "SELECT 2, 2, normalized_payload_hash, revision_state, first_received_at, last_received_at, created_at, updated_at "
            "FROM discord_message_revisions WHERE id = 1"
        )
        connection.execute(
            "INSERT INTO discord_source_events "
            "(event_key, revision_id, event_kind, status, raw_text, received_at, last_seen_at, legacy_import_event_id, created_at, updated_at) "
            "SELECT 'other-event', 2, event_kind, status, raw_text, received_at, last_seen_at, legacy_import_event_id, created_at, updated_at "
            "FROM discord_source_events WHERE id = ?",
            (source_id,),
        )
        connection.commit()
        assert RollKeyDisplayCandidateEvaluator.evaluate(
            connection, source_event_id=source_id
        ).status is Status.IMPORT_LINK_AMBIGUOUS

    report = RollKeyDisplayCandidateInventoryService(path).scan()
    assert report.rows[0].candidate_status is Status.IMPORT_LINK_AMBIGUOUS
    assert report.summary.import_link_failures == 1


def test_paging_summary_and_writer_revalidation(tmp_path, monkeypatch) -> None:
    path = tmp_path / "page.db"
    source_id, import_id, roll_id = _seed_retained_roll(path, NO_KEY_ROLL, without_key=True)
    with connect(path) as connection:
        new_import_id = connection.execute(
            "INSERT INTO import_events (kind, source, observed_at, raw_message) "
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (import_id,),
        ).lastrowid
        connection.execute(
            "INSERT INTO roll_observations "
            "(account_context_id, character_id, claim_rank, kakera_value, observed_at, import_event_id) "
            "SELECT account_context_id, character_id, claim_rank, kakera_value, observed_at, ? "
            "FROM roll_observations",
            (new_import_id,),
        )
        connection.commit()

    original = RollKeyDisplayCandidateEvaluator.evaluate
    calls = []

    def spy(connection, **kwargs):
        calls.append(kwargs)
        return original(connection, **kwargs)

    monkeypatch.setattr(RollKeyDisplayCandidateEvaluator, "evaluate", staticmethod(spy))
    inventory = RollKeyDisplayCandidateInventoryService(path)
    first = inventory.scan(limit=1)
    second = inventory.scan(limit=1, after_roll_observation_id=first.next_cursor)
    assert first.next_cursor == roll_id
    assert first.truncated and first.summary.more_available
    assert second.next_cursor > first.next_cursor
    assert not second.truncated
    assert second.rows[0].candidate_status is Status.SOURCE_MISSING
    assert first.summary.total_evaluated == second.summary.total_evaluated == 1

    outcome = RollKeyDisplayPresenceBackfillService(path).backfill(source_id)
    assert outcome.status is RollKeyDisplayBackfillStatus.UPDATED
    assert len(calls) == 3
    assert calls[-1] == {"source_event_id": source_id}

    with connect(path) as connection:
        assert connection.execute(
            "SELECT displayed_key_count_present FROM roll_observations WHERE id = ?",
            (roll_id,),
        ).fetchone()[0] == 0
    with pytest.raises(ValueError):
        inventory.scan(limit=1001)
    with pytest.raises(ValueError):
        inventory.scan(artifact_label="C:\\private\\file.db")


def test_writer_rechecks_after_inventory_and_never_overwrites_established_value(tmp_path) -> None:
    path = tmp_path / "race.db"
    source_id, _, roll_id = _seed_retained_roll(path, NO_KEY_ROLL, without_key=True)
    report = RollKeyDisplayCandidateInventoryService(path).scan()
    assert report.rows[0].candidate_status is Status.ELIGIBLE_NULL
    with connect(path) as connection:
        connection.execute(
            "UPDATE roll_observations SET displayed_key_count_present = 1 WHERE id = ?",
            (roll_id,),
        )
        connection.commit()
    result = RollKeyDisplayPresenceBackfillService(path).backfill(source_id)
    assert result.status is RollKeyDisplayBackfillStatus.CONFLICT
    with connect(path) as connection:
        assert connection.execute(
            "SELECT displayed_key_count_present FROM roll_observations WHERE id = ?",
            (roll_id,),
        ).fetchone()[0] == 1


def test_cli_help_is_lazy_and_output_is_bounded_and_private_safe(tmp_path) -> None:
    import moa.cli.main as main

    runner = CliRunner()
    help_result = runner.invoke(
        main.app, ["catalog", "roll-key-display-candidates", "--help"]
    )
    assert help_result.exit_code == 0
    assert "--limit" in help_result.stdout
    assert "DATABASE_PATH" in help_result.stdout

    path = tmp_path / "cli.db"
    source_id, _, _ = _seed_retained_roll(path, NO_KEY_ROLL, without_key=True)
    result = runner.invoke(
        main.app,
        ["catalog", "roll-key-display-candidates", str(path), "--limit", "1"],
    )
    assert result.exit_code == 0
    assert "Roll key-display candidate inventory" in result.stdout
    assert str(source_id) in result.stdout
    assert "ELIGIBLE_NULL" in result.stdout
    assert NO_KEY_ROLL not in result.stdout
    assert "Character" not in result.stdout
    assert "Account" not in result.stdout
    assert "--apply" not in result.stdout


def test_cli_absent_path_does_not_create_database(tmp_path) -> None:
    import moa.cli.main as main

    database_path = tmp_path / "missing" / "catalog.sqlite3"
    result = CliRunner().invoke(
        main.app,
        ["catalog", "roll-key-display-candidates", str(database_path)],
    )
    assert result.exit_code == 1
    assert not database_path.exists()
    assert not database_path.parent.exists()
    assert str(database_path) not in result.stdout
