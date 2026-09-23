"""Durable replay coverage using disposable top-family source events."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from moa.database.sqlite import connect
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.services.automatic_import_service import (
    AutomaticImportService,
    DurableTopPageImportContext,
)
from moa.services.catalog_service import CatalogService
from moa.services.top_page_projection_coordinator import (
    TopPageProjectionCoordinator,
    TopPageProjectionError,
)


FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "discord" / "top_family_sanitized.v1.json").read_text(
        encoding="utf-8"
    )
)
OBSERVED = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
FINISHED = datetime(2026, 9, 23, 12, 1, tzinfo=timezone.utc)


def _services(path):
    catalog_repository = CatalogRepository(path)
    discord_repository = DiscordMessageRepository(path)
    importer = AutomaticImportService(
        CatalogService(catalog_repository),
        top_page_projection_coordinator=TopPageProjectionCoordinator(
            catalog_repository, discord_repository
        ),
    )
    return discord_repository, importer


def _receive(discord, raw, suffix, *, account=None):
    key = MessageAggregateKey(SourcePlatform.DISCORD, "guild", "channel", suffix)
    received = discord.receive_message(
        aggregate_key=key,
        revision_key=MessageRevisionKey.versioned(key, f"payload-{suffix}", "revision-1"),
        event_key=f"event-{suffix}",
        event_kind="message_create",
        raw_text=raw,
        payload_json=json.dumps({"content": raw}),
        payload_capture_version="capture-1",
        source_observed_at=OBSERVED,
        received_at=OBSERVED,
    )
    discord.record_server_attribution(
        received.source_event_id, status="resolved", server_name="Server", recorded_at=OBSERVED
    )
    if account is not None:
        discord.record_account_attribution(
            received.source_event_id,
            status="resolved",
            server_name="Server",
            account_name=account,
            recorded_at=OBSERVED,
        )
    attempt = discord.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OBSERVED,
    )
    return received.source_event_id, attempt.attempt_id


def _import(importer, raw, source_event_id, attempt_id, suffix, *, kind, account=None):
    source = f"discord:guild=guild:channel=channel:message={suffix}"
    return importer.import_message(
        raw,
        source,
        "Server",
        account or "Account",
        detected_kind=kind,
        durable_top_page_context=DurableTopPageImportContext(
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            server="Server",
            account=account,
            raw=raw,
            source=source,
            observed_at=OBSERVED,
            finished_at=FINISHED,
        ),
    )


def _counts(path):
    with connect(path) as connection:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "import_events",
                "rank_snapshots",
                "top_owner_observations",
                "unavailable_character_observations",
                "discord_projection_links",
            )
        }


@pytest.mark.parametrize(
    ("fixture", "kind", "import_kind", "projection_kind", "account", "evidence_table"),
    [
        ("top", "top", "top_page", "catalog.top_page", None, "top_owner_observations"),
        ("topo", "top", "top_page", "catalog.top_page", None, "top_owner_observations"),
        (
            "topx",
            "topx",
            "topx_page",
            "catalog.topx_page",
            "Account",
            "unavailable_character_observations",
        ),
    ],
)
def test_fresh_replay_and_different_source_identity(
    tmp_path, fixture, kind, import_kind, projection_kind, account, evidence_table
):
    path = tmp_path / "catalog.db"
    raw = FIXTURES[fixture]
    discord, importer = _services(path)
    source_event_id, attempt_id = _receive(discord, raw, "one", account=account)
    first = _import(importer, raw, source_event_id, attempt_id, "one", kind=kind, account=account)
    assert first.imported_count == 2
    assert first.durable_success_recorded and not first.replay_skipped
    with connect(path) as connection:
        source_event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()
        imported = connection.execute(
            "SELECT kind FROM import_events WHERE id = ?", (first.import_event_id,)
        ).fetchone()
        link = connection.execute(
            "SELECT projection_kind, projection_table, projection_row_id, state "
            "FROM discord_projection_links WHERE source_event_id = ?",
            (source_event_id,),
        ).fetchone()
        assert tuple(source_event) == ("succeeded", first.import_event_id)
        assert imported["kind"] == import_kind
        assert tuple(link) == (projection_kind, "import_events", first.import_event_id, "completed")
        if fixture == "topo":
            assert [
                row[0]
                for row in connection.execute(
                    "SELECT owner_name FROM top_owner_observations ORDER BY id"
                )
            ] == ["owner_a", "owner_b"]
    before = _counts(path)
    assert before["rank_snapshots"] == before[evidence_table] == 2

    # Recreate both repositories and importer to cross the process-memory boundary.
    discord, importer = _services(path)
    replay = _import(importer, raw, source_event_id, None, "one", kind=kind, account=account)
    assert replay.replay_skipped and replay.durable_success_recorded
    assert replay.imported_count == 0
    assert replay.import_event_id == first.import_event_id
    assert _counts(path) == before

    # Identical content from another Discord message is independent evidence.
    other_source_event_id, other_attempt_id = _receive(discord, raw, "two", account=account)
    second = _import(
        importer, raw, other_source_event_id, other_attempt_id, "two", kind=kind, account=account
    )
    assert second.imported_count == 2 and second.import_event_id != first.import_event_id
    after = _counts(path)
    assert after["import_events"] == 2
    assert after["rank_snapshots"] == after[evidence_table] == 4


@pytest.mark.parametrize(
    ("fixture", "kind", "account"),
    [
        ("top", "top", None),
        ("topx", "topx", "Account"),
    ],
)
def test_failed_retry_is_processable_and_success_failure_rolls_back(
    tmp_path, monkeypatch, fixture, kind, account
):
    path = tmp_path / "catalog.db"
    raw = FIXTURES[fixture]
    discord, importer = _services(path)
    source_event_id, attempt_id = _receive(discord, raw, "one", account=account)
    original_success = discord._mark_processing_success_with_connection

    def fail_success(*args, **kwargs):
        raise RuntimeError("simulated completion failure")

    monkeypatch.setattr(discord, "_mark_processing_success_with_connection", fail_success)
    with pytest.raises(RuntimeError, match="simulated completion failure"):
        _import(importer, raw, source_event_id, attempt_id, "one", kind=kind, account=account)
    assert all(count == 0 for count in _counts(path).values())
    monkeypatch.setattr(discord, "_mark_processing_success_with_connection", original_success)
    discord.mark_processing_failure(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        status="failed",
        retryable=True,
        failure_code="test_retry",
        failure_detail=None,
        finished_at=FINISHED,
    )
    retry = discord.begin_processing_attempt(
        source_event_id=source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=FINISHED,
    )
    result = _import(
        importer, raw, source_event_id, retry.attempt_id, "one", kind=kind, account=account
    )
    assert result.imported_count == 2 and result.durable_success_recorded
    assert _counts(path)["import_events"] == 1


def test_active_attempt_and_wrong_database_fail_closed(tmp_path):
    path = tmp_path / "catalog.db"
    discord, importer = _services(path)
    raw = FIXTURES["top"]
    source_event_id, attempt_id = _receive(discord, raw, "one")
    with pytest.raises(TopPageProjectionError, match="no active processing attempt"):
        _import(importer, raw, source_event_id, None, "one", kind="top")
    assert _counts(path)["import_events"] == 0
    assert attempt_id > 0
    with pytest.raises(ValueError, match="same database path"):
        TopPageProjectionCoordinator(
            CatalogRepository(path), DiscordMessageRepository(tmp_path / "other.db")
        )


def test_manual_top_and_topx_imports_keep_direct_catalog_behavior(tmp_path):
    path = tmp_path / "catalog.db"
    importer = AutomaticImportService(CatalogService(CatalogRepository(path)))
    top = importer.import_message(FIXTURES["top"], "manual-top", "Server", "Account")
    topx = importer.import_message(FIXTURES["topx"], "manual-topx", "Server", "Account")
    assert top.imported_count == topx.imported_count == 2
    assert not top.durable_success_recorded and not topx.durable_success_recorded
    assert _counts(path)["import_events"] == 2
    assert _counts(path)["discord_projection_links"] == 0
