from __future__ import annotations

from datetime import UTC, datetime, timedelta
import sqlite3

import pytest
from typer.testing import CliRunner

from moa.cli import main
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.repositories.projection_link_repository import ProjectionLinkRepository
from moa.repositories.retention_eligibility_repository import RetentionEligibilityDataError
from moa.services.retention_eligibility_service import RetentionEligibilityService
from moa.services.projection_expectations import (
    Expectedness,
    ExpectedProjectionIdentity,
    load_durable_projection_expectation_facts,
    resolve_expected_projections,
    server_account_projection_slot,
)


AS_OF = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
CUTOFF = AS_OF - timedelta(days=90)
OLD = CUTOFF - timedelta(minutes=1)
BOUNDARY = CUTOFF
YOUNG = CUTOFF + timedelta(minutes=1)


def _initialize(path) -> None:
    CatalogRepository(path)


def _insert_import(path, event_id: int, *, observed_at: datetime, raw: str = "raw import") -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO import_events (id, kind, source, observed_at, raw_message) "
            "VALUES (?, 'profile', 'test', ?, ?)",
            (event_id, observed_at.isoformat(), raw),
        )


def _new_source(path, suffix: str, *, raw: str = "raw discord") -> tuple[int, int]:
    repository = DiscordMessageRepository(path)
    aggregate_key = MessageAggregateKey(
        SourcePlatform.DISCORD, "guild", "channel", f"message-{suffix}"
    )
    received = repository.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(aggregate_key, f"payload-{suffix}", "v1"),
        event_key=f"event-{suffix}",
        event_kind="message_create",
        raw_text=raw,
        payload_json='{"content":"private"}',
        payload_capture_version="test",
        source_observed_at=AS_OF,
        received_at=AS_OF,
    )
    attempt = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OLD - timedelta(days=2),
    )
    return received.source_event_id, attempt.attempt_id


def _succeed_source(path, source_event_id: int, attempt_id: int, finished_at: datetime) -> None:
    DiscordMessageRepository(path).mark_processing_success(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        finished_at=finished_at,
    )


def _fail_source(path, source_event_id: int, attempt_id: int, *, detail: str = "private failure") -> None:
    DiscordMessageRepository(path).mark_processing_failure(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        status="failed",
        retryable=True,
        failure_code="TEST_FAILURE",
        failure_detail=detail,
        finished_at=OLD - timedelta(days=1),
    )


def _complete_link(path, source_event_id: int) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO discord_projection_links (
                source_event_id, projection_kind, projection_slot,
                projection_table, projection_row_id, state,
                claimed_at, completed_at, created_at, updated_at
            ) VALUES (?, 'catalog.profile', 'slot', 'profile_observations', 1, 'completed', ?, ?, ?, ?)
            """,
            (source_event_id,) + (OLD.isoformat(),) * 4,
        )


def _report(path, *, clock=lambda: AS_OF):
    return RetentionEligibilityService(path, clock=clock).report()


def _new_linked_profile_source(
    path, suffix: str = "linked", *, account_resolved: bool = True
) -> int:
    _insert_import(path, 1, observed_at=OLD, raw="linked import")
    source_event_id, attempt_id = _new_source(path, suffix)
    _succeed_source(path, source_event_id, attempt_id, OLD)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE discord_source_events SET legacy_import_event_id = 1 WHERE id = ?",
            (source_event_id,),
        )
    repository = DiscordMessageRepository(path)
    repository.record_server_attribution(
        source_event_id,
        status="resolved",
        server_name="Server",
        recorded_at=AS_OF,
    )
    repository.record_account_attribution(
        source_event_id,
        status="resolved" if account_resolved else "unresolved",
        server_name="Server" if account_resolved else None,
        account_name="Account" if account_resolved else None,
        recorded_at=AS_OF,
    )
    return source_event_id


def _insert_projection_link(
    path,
    source_event_id: int,
    *,
    generation_id: int,
    projection_slot: str,
    state: str = "completed",
    projection_table: str | None = "profile_observations",
    projection_row_id: int | None = 999,
) -> None:
    with sqlite3.connect(path) as connection:
        if state == "completed" and (
            projection_table is None or projection_row_id is None
        ):
            connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "INSERT INTO discord_projection_links "
            "(source_event_id, generation_id, projection_kind, projection_slot, "
            "projection_table, projection_row_id, state, claimed_at, completed_at, "
            "created_at, updated_at) VALUES (?, ?, 'catalog.profile', ?, ?, ?, ?, "
            "'now', ?, 'now', 'now')",
            (
                source_event_id,
                generation_id,
                projection_slot,
                projection_table,
                projection_row_id,
                state,
                "now" if state == "completed" else None,
            ),
        )


def _activate_generation(path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("BEGIN")
        assert ProjectionLinkRepository(connection).switch_current_generation() == 2


def _profile_slot() -> str:
    return server_account_projection_slot("server", "account")


def test_clock_is_captured_once_and_boundary_is_inclusive(tmp_path) -> None:
    path = tmp_path / "catalog.db"
    _initialize(path)
    old_source, old_attempt = _new_source(path, "old")
    boundary_source, boundary_attempt = _new_source(path, "boundary")
    young_source, young_attempt = _new_source(path, "young")
    _succeed_source(path, old_source, old_attempt, OLD)
    _succeed_source(path, boundary_source, boundary_attempt, BOUNDARY)
    _succeed_source(path, young_source, young_attempt, YOUNG)

    calls: list[datetime] = []

    def clock() -> datetime:
        calls.append(AS_OF)
        return AS_OF

    report = _report(path, clock=clock)
    category = report.category("discord_source_raw_evidence")
    assert calls == [AS_OF]
    assert category.eligible_count == 2
    assert category.retained_blocked_count == 1
    assert category.blocked_reasons == {"not-old-enough": 1}
    assert category.oldest_eligible_anchor == OLD
    assert category.newest_eligible_anchor == BOUNDARY


def test_discord_source_requires_success_and_complete_links(tmp_path) -> None:
    path = tmp_path / "catalog.db"
    _initialize(path)
    succeeded, succeeded_attempt = _new_source(path, "succeeded")
    failed, failed_attempt = _new_source(path, "failed")
    active, _active_attempt = _new_source(path, "active")
    claimed, claimed_attempt = _new_source(path, "claimed")
    expired, expired_attempt = _new_source(path, "expired")
    _succeed_source(path, succeeded, succeeded_attempt, OLD)
    _complete_link(path, succeeded)
    _fail_source(path, failed, failed_attempt)
    _succeed_source(path, claimed, claimed_attempt, OLD)
    _succeed_source(path, expired, expired_attempt, OLD)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO discord_projection_links (
                source_event_id, projection_kind, projection_slot, state,
                claimed_at, created_at, updated_at
            ) VALUES (?, 'test', 'claimed', 'claimed', ?, ?, ?)
            """,
            (claimed, AS_OF.isoformat(), AS_OF.isoformat(), AS_OF.isoformat()),
        )
        connection.execute(
            "UPDATE discord_source_events SET raw_evidence_expired_at = ? WHERE id = ?",
            (AS_OF.isoformat(), expired),
        )

    category = _report(path).category("discord_source_raw_evidence")
    assert category.eligible_count == 1
    assert category.retained_blocked_count == 3
    assert category.already_expired_count == 1
    assert category.blocked_reasons == {
        "active-or-unfinished": 1,
        "incomplete-projection": 1,
        "not-successful": 1,
    }
    assert active > 0


def test_retry_failure_detail_uses_eventual_success_anchor_and_preserves_history(tmp_path) -> None:
    path = tmp_path / "catalog.db"
    _initialize(path)
    source_event_id, first_attempt_id = _new_source(path, "retry")
    _fail_source(path, source_event_id, first_attempt_id)
    second = DiscordMessageRepository(path).begin_processing_attempt(
        source_event_id=source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OLD - timedelta(hours=1),
    )
    _succeed_source(path, source_event_id, second.attempt_id, OLD)

    category = _report(path).category("processing_attempt_failure_detail")
    assert category.eligible_count == 1
    assert category.retained_blocked_count == 0
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT failure_detail FROM discord_processing_attempts WHERE id = ?",
            (first_attempt_id,),
        ).fetchone()[0] == "private failure"


def test_unresolved_failure_detail_and_absent_and_expired_states_are_distinct(tmp_path) -> None:
    path = tmp_path / "catalog.db"
    _initialize(path)
    unresolved, unresolved_attempt = _new_source(path, "unresolved")
    _fail_source(path, unresolved, unresolved_attempt)
    absent, absent_attempt = _new_source(path, "absent")
    _succeed_source(path, absent, absent_attempt, OLD)
    expired, expired_attempt = _new_source(path, "expired")
    _succeed_source(path, expired, expired_attempt, OLD)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE discord_processing_attempts SET failure_detail = NULL WHERE id = ?",
            (absent_attempt,),
        )
        connection.execute(
            "UPDATE discord_processing_attempts SET failure_detail_expired_at = ? WHERE id = ?",
            (AS_OF.isoformat(), expired_attempt),
        )

    category = _report(path).category("processing_attempt_failure_detail")
    assert category.eligible_count == 0
    assert category.retained_blocked_count == 1
    assert category.already_expired_count == 1
    assert category.absent_count == 1
    assert category.blocked_reasons == {"not-successful": 1}


def test_import_anchors_and_reference_fail_closed(tmp_path) -> None:
    path = tmp_path / "catalog.db"
    _initialize(path)
    _insert_import(path, 1, observed_at=OLD, raw="legacy old")
    _insert_import(path, 2, observed_at=YOUNG, raw="legacy young")
    _insert_import(path, 5, observed_at=YOUNG, raw="retained young")
    referenced, referenced_attempt = _new_source(path, "referenced")
    _succeed_source(path, referenced, referenced_attempt, OLD)
    _insert_import(path, 3, observed_at=OLD, raw="referenced")
    incomplete, incomplete_attempt = _new_source(path, "incomplete")
    _succeed_source(path, incomplete, incomplete_attempt, OLD)
    _insert_import(path, 4, observed_at=OLD, raw="incomplete")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE discord_source_events SET legacy_import_event_id = ? WHERE id = ?",
            (3, referenced),
        )
        connection.execute(
            "UPDATE discord_source_events SET legacy_import_event_id = ? WHERE id = ?",
            (4, incomplete),
        )
        connection.execute(
            """
            INSERT INTO discord_projection_links (
                source_event_id, projection_kind, projection_slot, state,
                claimed_at, created_at, updated_at
            ) VALUES (?, 'test', 'claimed', 'claimed', ?, ?, ?)
            """,
            (incomplete, AS_OF.isoformat(), AS_OF.isoformat(), AS_OF.isoformat()),
        )
        connection.execute(
            "UPDATE import_events SET raw_message_expired_at = ? WHERE id = 2",
            (AS_OF.isoformat(),),
        )

    category = _report(path).category("import_raw_message")
    assert category.eligible_count == 2
    assert category.retained_blocked_count == 2
    assert category.already_expired_count == 1
    assert category.blocked_reasons == {"incomplete-projection": 1, "not-old-enough": 1}


@pytest.mark.parametrize(
    "current_state", ["missing", "completed", "claimed", "null-target", "conflicting"]
)
def test_linked_source_retention_uses_only_the_current_generation(
    tmp_path, current_state: str
) -> None:
    path = tmp_path / f"retention-generations-{current_state}.db"
    _initialize(path)
    source_event_id = _new_linked_profile_source(path, current_state)
    _insert_projection_link(
        path,
        source_event_id,
        generation_id=1,
        projection_slot=_profile_slot(),
    )
    _activate_generation(path)

    if current_state == "completed":
        _insert_projection_link(
            path,
            source_event_id,
            generation_id=2,
            projection_slot=_profile_slot(),
        )
    elif current_state == "claimed":
        _insert_projection_link(
            path,
            source_event_id,
            generation_id=2,
            projection_slot=_profile_slot(),
            state="claimed",
            projection_table=None,
            projection_row_id=None,
        )
    elif current_state == "null-target":
        _insert_projection_link(
            path,
            source_event_id,
            generation_id=2,
            projection_slot=_profile_slot(),
            projection_table=None,
            projection_row_id=None,
        )
    elif current_state == "conflicting":
        _insert_projection_link(
            path,
            source_event_id,
            generation_id=2,
            projection_slot='{"account":"other","server":"server"}',
        )

    category = _report(path).category("discord_source_raw_evidence")
    assert category.eligible_count == (1 if current_state == "completed" else 0)
    assert category.retained_blocked_count == (0 if current_state == "completed" else 1)
    assert category.blocked_reasons == (
        {} if current_state == "completed" else {"incomplete-projection": 1}
    )


def test_linked_source_ignores_corrupt_historical_links_when_current_set_completes(
    tmp_path,
) -> None:
    path = tmp_path / "retention-generations-historical-corruption.db"
    _initialize(path)
    source_event_id = _new_linked_profile_source(path, "historical-corruption")
    _insert_projection_link(
        path,
        source_event_id,
        generation_id=1,
        projection_slot=_profile_slot(),
        state="claimed",
        projection_table=None,
        projection_row_id=None,
    )
    _activate_generation(path)
    _insert_projection_link(
        path,
        source_event_id,
        generation_id=2,
        projection_slot=_profile_slot(),
    )

    category = _report(path).category("discord_source_raw_evidence")
    assert category.eligible_count == 1
    with sqlite3.connect(path) as connection:
        historical = connection.execute(
            "SELECT state, projection_table, projection_row_id "
            "FROM discord_projection_links WHERE source_event_id = ? AND generation_id = 1",
            (source_event_id,),
        ).fetchone()
    assert historical == ("claimed", None, None)


def test_unknown_expectation_blocks_completed_current_link(tmp_path) -> None:
    path = tmp_path / "retention-generations-unknown-expectation.db"
    _initialize(path)
    source_event_id = _new_linked_profile_source(
        path, "unknown-expectation", account_resolved=False
    )
    _activate_generation(path)
    _insert_projection_link(
        path,
        source_event_id,
        generation_id=2,
        projection_slot=_profile_slot(),
    )

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        expected_set = resolve_expected_projections(
            load_durable_projection_expectation_facts(connection, source_event_id)
        )
    assert expected_set.expectedness_for(
        ExpectedProjectionIdentity("catalog.profile", _profile_slot())
    ) is Expectedness.UNKNOWN

    category = _report(path).category("discord_source_raw_evidence")
    assert category.eligible_count == 0
    assert category.retained_blocked_count == 1
    assert category.blocked_reasons == {"incomplete-projection": 1}


@pytest.mark.parametrize("invalid_state", ["zero", "multiple", "invalid"])
def test_retention_fails_closed_for_invalid_current_generation(tmp_path, invalid_state: str) -> None:
    path = tmp_path / f"retention-invalid-generation-{invalid_state}.db"
    _initialize(path)
    with sqlite3.connect(path) as connection:
        if invalid_state == "zero":
            connection.execute("UPDATE projection_generations SET is_current = 0")
        elif invalid_state == "invalid":
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute("UPDATE projection_generations SET id = 0 WHERE id = 1")
        else:
            connection.execute("DROP INDEX uq_projection_generations_one_current")
            connection.execute(
                "INSERT INTO projection_generations (id, is_current) VALUES (2, 1)"
            )

    with pytest.raises(RetentionEligibilityDataError, match="generation"):
        _report(path)


def test_generation_aware_eligibility_read_is_byte_for_byte_read_only(tmp_path) -> None:
    path = tmp_path / "retention-generations-read-only.db"
    _initialize(path)
    source_event_id = _new_linked_profile_source(path, "read-only")
    _activate_generation(path)
    _insert_projection_link(
        path,
        source_event_id,
        generation_id=2,
        projection_slot=_profile_slot(),
    )
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = path.read_bytes()

    category = _report(path).category("discord_source_raw_evidence")

    assert category.eligible_count == 1
    assert path.read_bytes() == before


def test_report_is_aggregate_private_and_read_only_and_cli_zero_noop_succeeds(tmp_path, monkeypatch) -> None:
    path = tmp_path / "catalog.db"
    _initialize(path)
    before = path.read_bytes()
    report = _report(path)
    assert path.read_bytes() == before
    assert all(
        private not in repr(report)
        for private in ("raw discord", '{"content"', "private failure", "guild", "channel")
    )

    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", path)
    result = CliRunner().invoke(main.app, ["catalog", "data-health", "retention"])
    assert result.exit_code == 0
    assert (
        "Read-only classification; no evidence was changed and this report does not authorize "
        "expiry or deletion."
    ) in result.stdout
    assert "No eligible" not in result.stdout
    assert "raw import" not in result.stdout
    assert "private" not in result.stdout
    assert "guild" not in result.stdout
    assert "payload" not in result.stdout
    assert "failure" not in result.stdout
    assert "0" in result.stdout
