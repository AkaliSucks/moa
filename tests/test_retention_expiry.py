from __future__ import annotations

from datetime import UTC, datetime, timedelta
import sqlite3

import pytest
from typer.testing import CliRunner

from moa.cli import main
from moa.models.character import RollObservation
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.parser.mudae import MudaeTextParser
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.repositories.retention_expiry_repository import (
    RAW_EVIDENCE_EXPIRED_SENTINEL,
    RetentionExpiryError,
    RetentionExpiryRepository,
)
from moa.repositories.retention_eligibility_repository import RetentionEligibilityRepository
from moa.services.retention_eligibility_service import RetentionEligibilityService
from moa.services.retention_expiry_service import RetentionExpiryService
from moa.services.catalog_service import CatalogService


AS_OF = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
CUTOFF = AS_OF - timedelta(days=90)
OLD = CUTOFF - timedelta(minutes=1)
YOUNG = CUTOFF + timedelta(minutes=1)


def _initialize(path) -> None:
    CatalogRepository(path)


def _insert_import(path, event_id: int, observed_at: datetime, raw: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO import_events (id, kind, source, observed_at, raw_message) "
            "VALUES (?, 'test', 'test', ?, ?)",
            (event_id, observed_at.isoformat(), raw),
        )


def _new_source(path, suffix: str, *, raw: str = "private source") -> tuple[int, int]:
    repository = DiscordMessageRepository(path)
    aggregate_key = MessageAggregateKey(
        SourcePlatform.DISCORD, "private-guild", "private-channel", f"message-{suffix}"
    )
    received = repository.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(
            aggregate_key, f"payload-{suffix}", "revision-1"
        ),
        event_key=f"event-{suffix}",
        event_kind="message_create",
        raw_text=raw,
        payload_json='{"private":"payload"}',
        payload_capture_version="test",
        source_observed_at=AS_OF,
        received_at=AS_OF,
    )
    attempt = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OLD - timedelta(days=1),
    )
    return received.source_event_id, attempt.attempt_id


def _succeed(path, source_id: int, attempt_id: int, at: datetime) -> None:
    DiscordMessageRepository(path).mark_processing_success(
        source_event_id=source_id, attempt_id=attempt_id, finished_at=at
    )


def _fail(path, source_id: int, attempt_id: int) -> None:
    DiscordMessageRepository(path).mark_processing_failure(
        source_event_id=source_id,
        attempt_id=attempt_id,
        status="failed",
        retryable=True,
        failure_code="TEST_FAILURE",
        failure_detail="private failure detail",
        finished_at=OLD - timedelta(hours=1),
    )


def _event_rows(path):
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        return tuple(connection.execute("SELECT * FROM discord_source_events ORDER BY id"))


def test_apply_expires_all_categories_with_one_timestamp_and_private_result(tmp_path) -> None:
    path = tmp_path / "catalog.db"
    _initialize(path)
    _insert_import(path, 1, OLD, "private import")
    source_id, failed_attempt = _new_source(path, "happy")
    _fail(path, source_id, failed_attempt)
    retry = DiscordMessageRepository(path).begin_processing_attempt(
        source_event_id=source_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OLD,
    )
    _succeed(path, source_id, retry.attempt_id, OLD)

    calls: list[datetime] = []

    def clock() -> datetime:
        calls.append(AS_OF)
        return AS_OF

    result = RetentionExpiryService(path, clock=clock).apply()

    assert calls == [AS_OF]
    assert tuple(category.expired_count for category in result.categories) == (1, 1, 1)
    assert all(category.recomputed_eligible_count == 1 for category in result.categories)
    assert all(
        private not in repr(result)
        for private in (
            "private import",
            "private source",
            "private failure detail",
            "private-guild",
            "private-channel",
        )
    )
    with sqlite3.connect(path) as connection:
        import_row = connection.execute(
            "SELECT raw_message, raw_message_expired_at FROM import_events WHERE id = 1"
        ).fetchone()
        source_row = connection.execute(
            "SELECT raw_text, payload_json, raw_evidence_expired_at "
            "FROM discord_source_events WHERE id = ?",
            (source_id,),
        ).fetchone()
        attempt_row = connection.execute(
            "SELECT failure_detail, failure_detail_expired_at "
            "FROM discord_processing_attempts WHERE id = ?",
            (failed_attempt,),
        ).fetchone()
    timestamp = AS_OF.isoformat()
    assert import_row == (RAW_EVIDENCE_EXPIRED_SENTINEL, timestamp)
    assert source_row == (RAW_EVIDENCE_EXPIRED_SENTINEL, None, timestamp)
    assert attempt_row == (None, timestamp)


@pytest.mark.parametrize(
    ("anchor", "expired"), [(OLD, True), (CUTOFF, True), (YOUNG, False)]
)
def test_apply_threshold_is_inclusive(tmp_path, anchor: datetime, expired: bool) -> None:
    path = tmp_path / "catalog.db"
    _initialize(path)
    source_id, attempt_id = _new_source(path, anchor.isoformat())
    _succeed(path, source_id, attempt_id, anchor)

    RetentionExpiryService(path, clock=lambda: AS_OF).apply()

    row = _event_rows(path)[0]
    assert (row["raw_evidence_expired_at"] is not None) is expired


def test_apply_preserves_unresolved_active_incomplete_and_absent_evidence(tmp_path) -> None:
    path = tmp_path / "catalog.db"
    _initialize(path)
    failed, failed_attempt = _new_source(path, "failed")
    _fail(path, failed, failed_attempt)
    active, _active_attempt = _new_source(path, "active")
    incomplete, incomplete_attempt = _new_source(path, "incomplete")
    _succeed(path, incomplete, incomplete_attempt, OLD)
    absent_payload, absent_attempt = _new_source(path, "absent")
    _succeed(path, absent_payload, absent_attempt, YOUNG)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO discord_projection_links (
                source_event_id, projection_kind, projection_slot, state,
                claimed_at, created_at, updated_at
            ) VALUES (?, 'test', 'slot', 'claimed', ?, ?, ?)
            """,
            (incomplete, AS_OF.isoformat(), AS_OF.isoformat(), AS_OF.isoformat()),
        )
        connection.execute(
            "UPDATE discord_source_events SET payload_json = NULL WHERE id = ?",
            (absent_payload,),
        )
        connection.execute(
            "UPDATE discord_processing_attempts SET failure_detail = NULL WHERE id = ?",
            (absent_attempt,),
        )

    result = RetentionExpiryService(path, clock=lambda: AS_OF).apply()

    assert result.category("discord_source_raw_evidence").expired_count == 0
    assert result.category("processing_attempt_failure_detail").expired_count == 0
    with sqlite3.connect(path) as connection:
        rows = tuple(
            connection.execute(
                "SELECT id, raw_text, raw_evidence_expired_at FROM discord_source_events ORDER BY id"
            )
        )
        absent_attempt_row = connection.execute(
            "SELECT failure_detail, failure_detail_expired_at "
            "FROM discord_processing_attempts WHERE id = ?",
            (absent_attempt,),
        ).fetchone()
    assert rows[0][1:] == ("private source", None)
    assert rows[1][1:] == ("private source", None)
    assert rows[2][1:] == ("private source", None)
    assert rows[3][1:] == ("private source", None)
    assert absent_attempt_row == (None, None)
    assert active > 0


def test_linked_imports_use_source_lifecycle_without_observed_at_fallback(tmp_path) -> None:
    path = tmp_path / "catalog.db"
    _initialize(path)
    _insert_import(path, 1, OLD, "unlinked")
    _insert_import(path, 2, OLD, "linked young")
    _insert_import(path, 3, OLD, "linked incomplete")
    _insert_import(path, 4, OLD, "linked ambiguous")
    young_source, young_attempt = _new_source(path, "linked-young")
    _succeed(path, young_source, young_attempt, YOUNG)
    incomplete_source, incomplete_attempt = _new_source(path, "linked-incomplete")
    _succeed(path, incomplete_source, incomplete_attempt, OLD)
    ambiguous_one, ambiguous_one_attempt = _new_source(path, "ambiguous-one")
    ambiguous_two, ambiguous_two_attempt = _new_source(path, "ambiguous-two")
    _succeed(path, ambiguous_one, ambiguous_one_attempt, OLD)
    _succeed(path, ambiguous_two, ambiguous_two_attempt, OLD)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE discord_source_events SET legacy_import_event_id = 2 WHERE id = ?",
            (young_source,),
        )
        connection.execute(
            "UPDATE discord_source_events SET legacy_import_event_id = 3 WHERE id = ?",
            (incomplete_source,),
        )
        connection.executemany(
            "UPDATE discord_source_events SET legacy_import_event_id = 4 WHERE id = ?",
            ((ambiguous_one,), (ambiguous_two,)),
        )
        connection.execute(
            """
            INSERT INTO discord_projection_links (
                source_event_id, projection_kind, projection_slot, state,
                claimed_at, created_at, updated_at
            ) VALUES (?, 'test', 'slot', 'claimed', ?, ?, ?)
            """,
            (incomplete_source,) + (AS_OF.isoformat(),) * 3,
        )

    RetentionExpiryService(path, clock=lambda: AS_OF).apply()

    with sqlite3.connect(path) as connection:
        rows = tuple(
            connection.execute(
                "SELECT id, raw_message, raw_message_expired_at FROM import_events ORDER BY id"
            )
        )
    assert rows[0][1:] == (RAW_EVIDENCE_EXPIRED_SENTINEL, AS_OF.isoformat())
    assert rows[1][1:] == ("linked young", None)
    assert rows[2][1:] == ("linked incomplete", None)
    assert rows[3][1:] == ("linked ambiguous", None)


def test_marker_authority_already_expired_absence_and_idempotency(tmp_path) -> None:
    path = tmp_path / "catalog.db"
    _initialize(path)
    _insert_import(path, 1, OLD, RAW_EVIDENCE_EXPIRED_SENTINEL)
    _insert_import(path, 2, OLD, "already expired")
    old_marker = (AS_OF - timedelta(days=1)).isoformat()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE import_events SET raw_message_expired_at = ? WHERE id = 2", (old_marker,)
        )

    first = RetentionExpiryService(path, clock=lambda: AS_OF).apply()
    second = RetentionExpiryService(
        path, clock=lambda: AS_OF + timedelta(days=1)
    ).apply()

    assert first.category("import_raw_message").expired_count == 1
    assert second.category("import_raw_message").expired_count == 0
    with sqlite3.connect(path) as connection:
        rows = tuple(
            connection.execute(
                "SELECT raw_message, raw_message_expired_at FROM import_events ORDER BY id"
            )
        )
    assert rows == (
        (RAW_EVIDENCE_EXPIRED_SENTINEL, AS_OF.isoformat()),
        ("already expired", old_marker),
    )


def test_apply_recomputes_after_prior_report_and_cannot_use_stale_eligibility(tmp_path) -> None:
    path = tmp_path / "catalog.db"
    _initialize(path)
    source_id, attempt_id = _new_source(path, "stale")
    _succeed(path, source_id, attempt_id, OLD)
    prior = RetentionEligibilityService(path, clock=lambda: AS_OF).report()
    assert prior.category("discord_source_raw_evidence").eligible_count == 1
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE discord_source_events SET status = 'failed' WHERE id = ?", (source_id,)
        )

    result = RetentionExpiryService(path, clock=lambda: AS_OF).apply()

    assert result.category("discord_source_raw_evidence").expired_count == 0
    assert _event_rows(path)[0]["raw_evidence_expired_at"] is None


def test_eligibility_selection_runs_after_writer_transaction_begins(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "catalog.db"
    _initialize(path)
    _insert_import(path, 1, OLD, "private import")
    original = RetentionEligibilityRepository._select_for_expiry
    observed_in_transaction: list[bool] = []

    def observe_transaction(self, as_of):
        observed_in_transaction.append(self._connection.in_transaction)
        return original(self, as_of)

    monkeypatch.setattr(
        RetentionEligibilityRepository, "_select_for_expiry", observe_transaction
    )

    result = RetentionExpiryService(path, clock=lambda: AS_OF).apply()

    assert observed_in_transaction == [True]
    assert result.category("import_raw_message").expired_count == 1


def test_rowcount_mismatch_rolls_back_every_category(tmp_path, monkeypatch) -> None:
    path = tmp_path / "catalog.db"
    _initialize(path)
    _insert_import(path, 1, OLD, "private import")
    source_id, attempt_id = _new_source(path, "mismatch")
    _succeed(path, source_id, attempt_id, OLD)
    original = RetentionExpiryRepository._expire_source_evidence

    def mismatch(self, row_ids, timestamp, cutoff):
        original(self, row_ids, timestamp, cutoff)
        return 0

    monkeypatch.setattr(RetentionExpiryRepository, "_expire_source_evidence", mismatch)

    with pytest.raises(RetentionExpiryError, match="source-rowcount-mismatch"):
        RetentionExpiryService(path, clock=lambda: AS_OF).apply()

    with sqlite3.connect(path) as connection:
        import_row = connection.execute(
            "SELECT raw_message, raw_message_expired_at FROM import_events WHERE id = 1"
        ).fetchone()
        source_row = connection.execute(
            "SELECT raw_text, raw_evidence_expired_at FROM discord_source_events WHERE id = ?",
            (source_id,),
        ).fetchone()
    assert import_row == ("private import", None)
    assert source_row == ("private source", None)


def test_failure_after_prior_updates_rolls_back_cross_category(tmp_path, monkeypatch) -> None:
    path = tmp_path / "catalog.db"
    _initialize(path)
    _insert_import(path, 1, OLD, "private import")
    source_id, failed_attempt = _new_source(path, "rollback")
    _fail(path, source_id, failed_attempt)
    retry = DiscordMessageRepository(path).begin_processing_attempt(
        source_event_id=source_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OLD,
    )
    _succeed(path, source_id, retry.attempt_id, OLD)

    def fail_after_prior_categories(self, row_ids, timestamp, cutoff):
        raise RetentionExpiryError("controlled-failure")

    monkeypatch.setattr(RetentionExpiryRepository, "_expire_failure_details", fail_after_prior_categories)

    with pytest.raises(RetentionExpiryError, match="controlled-failure"):
        RetentionExpiryService(path, clock=lambda: AS_OF).apply()

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT raw_message_expired_at FROM import_events WHERE id = 1"
        ).fetchone()[0] is None
        assert connection.execute(
            "SELECT raw_evidence_expired_at FROM discord_source_events WHERE id = ?",
            (source_id,),
        ).fetchone()[0] is None
        assert connection.execute(
            "SELECT failure_detail, failure_detail_expired_at "
            "FROM discord_processing_attempts WHERE id = ?",
            (failed_attempt,),
        ).fetchone() == ("private failure detail", None)


def test_cli_apply_is_explicit_private_and_render_failure_does_not_reapply(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "catalog.db"
    _initialize(path)
    _insert_import(path, 1, OLD, "private import")
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", path)

    report = CliRunner().invoke(main.app, ["catalog", "data-health", "retention"])
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT raw_message_expired_at FROM import_events WHERE id = 1"
        ).fetchone()[0] is None
    assert report.exit_code == 0

    applied = CliRunner().invoke(
        main.app, ["catalog", "data-health", "retention", "--apply"]
    )
    assert applied.exit_code == 0
    assert "committed" in applied.stdout
    assert "private import" not in applied.stdout
    assert "private" not in applied.stdout

    _insert_import(path, 2, OLD, "second private import")
    calls = 0
    original_apply = RetentionExpiryService.apply

    def counted_apply(self, apply_as_of=None):
        nonlocal calls
        calls += 1
        return original_apply(self, apply_as_of)

    monkeypatch.setattr(RetentionExpiryService, "apply", counted_apply)
    monkeypatch.setattr(
        main, "_render_retention_expiry_result", lambda _result: (_ for _ in ()).throw(RuntimeError())
    )
    failed_render = CliRunner().invoke(
        main.app, ["catalog", "data-health", "retention", "--apply"]
    )
    assert failed_render.exit_code == 1
    assert "committed, but presentation failed" in failed_render.stderr
    assert calls == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT raw_message_expired_at FROM import_events WHERE id = 2"
        ).fetchone()[0] is not None


def test_discord_duplicate_reader_accepts_real_apply_marker(tmp_path) -> None:
    path = tmp_path / "catalog.db"
    _initialize(path)
    source_id, attempt_id = _new_source(path, "duplicate", raw="original raw")
    _succeed(path, source_id, attempt_id, OLD)
    RetentionExpiryService(path, clock=lambda: AS_OF).apply()

    repository = DiscordMessageRepository(path)
    aggregate_key = MessageAggregateKey(
        SourcePlatform.DISCORD, "private-guild", "private-channel", "message-duplicate"
    )
    replay = repository.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(
            aggregate_key, "payload-duplicate", "revision-1"
        ),
        event_key="event-duplicate",
        event_kind="message_create",
        raw_text="original raw",
        payload_json='{"private":"payload"}',
        payload_capture_version="test",
        source_observed_at=AS_OF,
        received_at=AS_OF + timedelta(minutes=1),
    )

    assert replay.source_event_id == source_id
    assert replay.delivery_count == 2


def test_catalog_repair_reader_treats_real_apply_as_policy_expired(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "catalog.db"
    service = CatalogService(CatalogRepository(path))
    raw_message = (
        "Each kakera button consumes 100% of your reaction power.\n"
        "Your characters with 10+ keys consume half the power (50%)"
    )
    service.import_roll(
        RollObservation(
            name="Each kakera button consumes 100% of your reaction power.",
            series="Your characters with 10+ keys consume half the power (50%)",
            claim_rank=None,
            kakera_value=0,
        ),
        "Server",
        "account",
        raw_message,
        "discord",
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE import_events SET observed_at = ?", (OLD.isoformat(),)
        )
    RetentionExpiryService(path, clock=lambda: AS_OF).apply()

    def fail_parse(*_args, **_kwargs):
        raise AssertionError("expired raw evidence must not be parsed")

    monkeypatch.setattr(MudaeTextParser, "parse_roll", fail_parse)
    inspection = service.inspect_bugged_imports_with_lifecycle()

    assert inspection.expired_imports == 1
    assert service.repair_bugged_imports() == (0, 0)
