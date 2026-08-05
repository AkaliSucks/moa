import json
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import AntidisablePage
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.services.antidisable_page_projection_coordinator import (
    AntidisablePageProjectionCoordinator,
    AntidisablePageProjectionDatabasePathError,
    AntidisablePageProjectionIntegrityError,
    AntidisablePageProjectionResult,
    AntidisablePageProjectionStateError,
)


OBSERVED_AT = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
FINISHED_AT = datetime(2026, 7, 30, 12, 1, tzinfo=timezone.utc)


def _repositories(tmp_path):
    database_path = tmp_path / "antidisable-coordinator.db"
    catalog = CatalogRepository(database_path)
    discord = DiscordMessageRepository(database_path)
    return database_path, catalog, discord, AntidisablePageProjectionCoordinator(catalog, discord)


def _receive_and_begin(discord, *, suffix="one", raw="adl payload"):
    aggregate_key = MessageAggregateKey(
        SourcePlatform.DISCORD, "guild", "channel", f"message-{suffix}"
    )
    received = discord.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(
            aggregate_key, f"payload-{suffix}", "revision-1"
        ),
        event_key=f"event-{suffix}",
        event_kind="message_create",
        raw_text=raw,
        payload_json=json.dumps({"content": raw}),
        payload_capture_version="capture-1",
        source_observed_at=OBSERVED_AT,
        received_at=OBSERVED_AT,
    )
    attempt = discord.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OBSERVED_AT,
    )
    return received.source_event_id, attempt.attempt_id


def _record_attribution(discord, source_event_id, *, server="Server", account="Account"):
    discord.record_server_attribution(
        source_event_id,
        status="resolved",
        server_name=server,
        recorded_at=OBSERVED_AT,
    )
    discord.record_account_attribution(
        source_event_id,
        status="resolved",
        server_name=server,
        account_name=account,
        recorded_at=OBSERVED_AT,
    )


def _page(
    *,
    number=1,
    count=2,
    character_count=12,
    series=("Series A", "Series B"),
):
    return AntidisablePage(
        page_number=number,
        page_count=count,
        slots_used=2,
        slots_capacity=10,
        antidisabled_character_count=character_count,
        series_names=tuple(series),
    )


def _begin_scan(catalog, *, server="Server", account="Account"):
    return catalog.begin_antidisable_scan(server, account)


def _coordinate(
    coordinator,
    source_event_id,
    attempt_id,
    *,
    page=None,
    scan_id,
    server=" Server ",
    account=" Account ",
    raw="adl payload",
    source="discord",
):
    return coordinator.coordinate_antidisable_page(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        page=page or _page(),
        scan_id=scan_id,
        server=server,
        account=account,
        raw=raw,
        source=source,
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )


def _setup(tmp_path, *, page=None, server="Server", account="Account", suffix="one"):
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord, suffix=suffix)
    _record_attribution(discord, source_event_id, server=server, account=account)
    scan = _begin_scan(catalog, server=server, account=account)
    result = _coordinate(
        coordinator,
        source_event_id,
        attempt_id,
        page=page or _page(),
        scan_id=scan.id,
        server=server,
        account=account,
    )
    return database_path, catalog, discord, coordinator, source_event_id, result


def _new_processing(tmp_path, *, suffix="one", raw="adl payload"):
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord, suffix=suffix, raw=raw)
    _record_attribution(discord, source_event_id)
    scan = _begin_scan(catalog)
    return database_path, catalog, discord, coordinator, source_event_id, attempt_id, scan.id


def _counts(database_path):
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "harem_scans",
        "harem_scan_pages",
        "antidisable_series_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_processing_attempts",
    )
    with connect(database_path) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }


def _assert_replay_failure_without_writes(
    database_path,
    coordinator,
    source_event_id,
    result,
    *,
    page=None,
):
    before = _counts(database_path)
    with pytest.raises(AntidisablePageProjectionIntegrityError):
        _coordinate(
            coordinator,
            source_event_id,
            None,
            page=page,
            scan_id=result.scan_id,
        )
    assert _counts(database_path) == before


def _insert_projection_link(
    database_path,
    *,
    source_event_id,
    projection_kind,
    projection_slot,
    state,
    projection_table=None,
    projection_row_id=None,
    completed_at=None,
):
    with connect(database_path) as connection:
        connection.execute(
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
                OBSERVED_AT.isoformat(),
                completed_at,
                OBSERVED_AT.isoformat(),
                OBSERVED_AT.isoformat(),
            ),
        )
        connection.commit()


def _swap_series_observation_positions(connection, import_event_id, left_index, right_index):
    rows = connection.execute(
        "SELECT id FROM antidisable_series_observations "
        "WHERE import_event_id = ? ORDER BY id",
        (import_event_id,),
    ).fetchall()
    left_id = int(rows[left_index]["id"])
    right_id = int(rows[right_index]["id"])
    connection.execute(
        "UPDATE antidisable_series_observations SET id = -id "
        "WHERE id IN (?, ?)",
        (left_id, right_id),
    )
    connection.execute(
        "UPDATE antidisable_series_observations SET id = ? WHERE id = ?",
        (right_id, -left_id),
    )
    connection.execute(
        "UPDATE antidisable_series_observations SET id = ? WHERE id = ?",
        (left_id, -right_id),
    )


def test_successful_first_page_and_durable_target(tmp_path):
    database_path, catalog, discord, coordinator, source_event_id, result = _setup(tmp_path)

    assert isinstance(result, AntidisablePageProjectionResult)
    assert result.imported_count == 1
    assert result.projection_target == ("import_events", result.import_event_id)
    assert result.scan_id > 0
    assert result.page_number == 1
    assert result.page_count == 2
    assert result.replay_skipped is False
    assert result.durable_success_recorded is True
    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        page_row = connection.execute(
            "SELECT harem_scan_id, page_number, import_event_id FROM harem_scan_pages"
        ).fetchone()
        link = connection.execute(
            "SELECT projection_kind, projection_slot, projection_table, projection_row_id, state "
            "FROM discord_projection_links WHERE source_event_id = ?",
            (source_event_id,),
        ).fetchone()
        lifecycle = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()
    assert tuple(event) == ("antidisable", "discord", OBSERVED_AT.isoformat(), "adl payload")
    assert tuple(page_row) == (result.scan_id, 1, result.import_event_id)
    assert tuple(link)[0] == "catalog.antidisable_page"
    assert tuple(link)[2:] == ("import_events", result.import_event_id, "completed")
    slot = json.loads(link[1])
    assert slot == {
        "account": "account",
        "page_number": 1,
        "scan_id": result.scan_id,
        "server": "server",
    }
    assert tuple(lifecycle) == ("succeeded", result.import_event_id)
    progress = catalog.harem_scan_progress(result.scan_id)
    assert progress is not None and progress.completed_at is None
    assert discord.get_server_attribution(source_event_id).status == "resolved"


def test_continuation_and_out_of_order_pages_preserve_page_identity(tmp_path):
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    scan = _begin_scan(catalog)
    first_event, first_attempt = _receive_and_begin(discord, suffix="first")
    _record_attribution(discord, first_event)
    first = _coordinate(
        coordinator, first_event, first_attempt, page=_page(number=2), scan_id=scan.id
    )

    second_event, second_attempt = _receive_and_begin(discord, suffix="second")
    _record_attribution(discord, second_event)
    second = _coordinate(
        coordinator, second_event, second_attempt, page=_page(number=1), scan_id=scan.id
    )

    assert first.page_number == 2 and second.page_number == 1
    assert first.import_event_id != second.import_event_id
    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT page_number, import_event_id FROM harem_scan_pages "
            "WHERE harem_scan_id = ? ORDER BY page_number",
            (scan.id,),
        ).fetchall()
    assert [tuple(row) for row in rows] == [(1, second.import_event_id), (2, first.import_event_id)]


def test_page_count_is_validation_data_not_slot_identity(tmp_path):
    page = _page(number=1, count=3)
    database_path, catalog, discord, coordinator, source_event_id, result = _setup(
        tmp_path, page=page
    )
    with connect(database_path) as connection:
        slot = connection.execute(
            "SELECT projection_slot FROM discord_projection_links WHERE source_event_id = ?",
            (source_event_id,),
        ).fetchone()[0]
    assert json.loads(slot) == {
        "account": "account",
        "page_number": 1,
        "scan_id": result.scan_id,
        "server": "server",
    }
    assert "page_count" not in json.loads(slot)
    assert catalog.harem_scan_progress(result.scan_id).expected_page_count == 3


def test_same_page_number_in_different_scans_is_distinct(tmp_path):
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    results = []
    for suffix in ("one", "two"):
        source_event_id, attempt_id = _receive_and_begin(discord, suffix=suffix)
        _record_attribution(discord, source_event_id)
        scan = _begin_scan(catalog)
        results.append(
            _coordinate(
                coordinator,
                source_event_id,
                attempt_id,
                scan_id=scan.id,
                page=_page(number=1),
            )
        )
    assert results[0].scan_id != results[1].scan_id
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_projection_links WHERE projection_kind = ?",
            ("catalog.antidisable_page",),
        ).fetchone()[0] == 2


def test_exact_order_duplicates_nullable_count_and_empty_page(tmp_path):
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    pages = (
        _page(number=1, count=3, character_count=0, series=("B", "A", "B")),
        _page(number=2, count=3, character_count=None, series=("C",)),
        _page(number=3, count=3, character_count=None, series=()),
    )
    scan = _begin_scan(catalog)
    for index, page in enumerate(pages):
        source_event_id, attempt_id = _receive_and_begin(discord, suffix=str(index))
        _record_attribution(discord, source_event_id)
        result = _coordinate(
            coordinator, source_event_id, attempt_id, page=page, scan_id=scan.id
        )
        with connect(database_path) as connection:
            rows = connection.execute(
                "SELECT series_name, normalized_series_name, antidisabled_character_count "
                "FROM antidisable_series_observations WHERE import_event_id = ? ORDER BY id",
                (result.import_event_id,),
            ).fetchall()
        assert [tuple(row) for row in rows] == [
            (name, CatalogRepository._normalize(name), page.antidisabled_character_count)
            for name in page.series_names
        ]
    assert _counts(database_path)["antidisable_series_observations"] == 4


@pytest.mark.parametrize(
    ("mutator", "error_type"),
    [
        ("missing_scan", AntidisablePageProjectionStateError),
        ("wrong_kind", AntidisablePageProjectionStateError),
        ("completed_scan", AntidisablePageProjectionStateError),
        ("wrong_server", AntidisablePageProjectionStateError),
        ("wrong_account", AntidisablePageProjectionStateError),
        ("bad_page", AntidisablePageProjectionStateError),
        ("wrong_count", AntidisablePageProjectionStateError),
        ("duplicate_page", AntidisablePageProjectionStateError),
    ],
)
def test_first_processing_rejects_invalid_scan_or_page_state(tmp_path, mutator, error_type):
    database_path, catalog, discord, coordinator, source_event_id, attempt_id, scan_id = _new_processing(
        tmp_path
    )
    page = _page()
    if mutator == "missing_scan":
        scan_id = 9999
    elif mutator == "wrong_kind":
        with connect(database_path) as connection:
            connection.execute("UPDATE harem_scans SET scan_kind = 'keys' WHERE id = ?", (scan_id,))
            connection.commit()
    elif mutator == "completed_scan":
        with connect(database_path) as connection:
            connection.execute(
                "UPDATE harem_scans SET completed_at = ? WHERE id = ?",
                (FINISHED_AT.isoformat(), scan_id),
            )
            connection.commit()
    elif mutator == "wrong_server":
        with connect(database_path) as connection:
            connection.execute(
                "UPDATE server_contexts SET normalized_name = 'other' WHERE normalized_name = 'server'"
            )
            connection.commit()
    elif mutator == "wrong_account":
        with connect(database_path) as connection:
            connection.execute(
                "UPDATE account_contexts SET normalized_name = 'other' WHERE normalized_name = 'account'"
            )
            connection.commit()
    elif mutator == "bad_page":
        page = _page(number=None)
    elif mutator == "wrong_count":
        with connect(database_path) as connection:
            connection.execute(
                "UPDATE harem_scans SET expected_page_count = 9 WHERE id = ?", (scan_id,)
            )
            connection.commit()
    elif mutator == "duplicate_page":
        _coordinate(coordinator, source_event_id, attempt_id, scan_id=scan_id)
        source_event_id, attempt_id = _receive_and_begin(discord, suffix="duplicate")
        _record_attribution(discord, source_event_id)

    with pytest.raises(error_type):
        _coordinate(coordinator, source_event_id, attempt_id, page=page, scan_id=scan_id)
    assert _counts(database_path)["import_events"] == (1 if mutator == "duplicate_page" else 0)


def test_wrong_page_count_after_first_page_is_rejected(tmp_path):
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    scan = _begin_scan(catalog)
    first_event, first_attempt = _receive_and_begin(discord, suffix="first")
    _record_attribution(discord, first_event)
    _coordinate(coordinator, first_event, first_attempt, scan_id=scan.id)
    second_event, second_attempt = _receive_and_begin(discord, suffix="second")
    _record_attribution(discord, second_event)
    with pytest.raises(AntidisablePageProjectionStateError):
        _coordinate(
            coordinator,
            second_event,
            second_attempt,
            page=_page(number=2, count=3),
            scan_id=scan.id,
        )
    assert _counts(database_path)["import_events"] == 1


def test_injected_failure_rolls_back_and_retry_has_one_projection(tmp_path, monkeypatch):
    database_path, catalog, discord, coordinator, source_event_id, attempt_id, scan_id = _new_processing(
        tmp_path
    )
    original = catalog._import_antidisable_page_with_connection

    def failing(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected after page insert")

    monkeypatch.setattr(catalog, "_import_antidisable_page_with_connection", failing)
    with pytest.raises(RuntimeError, match="injected"):
        _coordinate(coordinator, source_event_id, attempt_id, scan_id=scan_id)
    assert _counts(database_path) == {
        "import_events": 0,
        "server_contexts": 1,
        "account_contexts": 1,
        "harem_scans": 1,
        "harem_scan_pages": 0,
        "antidisable_series_observations": 0,
        "discord_projection_links": 0,
        "discord_source_events": 1,
        "discord_processing_attempts": 1,
    }
    monkeypatch.setattr(catalog, "_import_antidisable_page_with_connection", original)
    result = _coordinate(coordinator, source_event_id, attempt_id, scan_id=scan_id)
    assert result.imported_count == 1
    with connect(database_path) as connection:
        import_events = connection.execute(
            "SELECT id FROM import_events WHERE kind = 'antidisable'"
        ).fetchall()
        page_rows = connection.execute(
            "SELECT harem_scan_id, page_number, import_event_id FROM harem_scan_pages"
        ).fetchall()
        series_rows = connection.execute(
            "SELECT id FROM antidisable_series_observations WHERE import_event_id = ?",
            (result.import_event_id,),
        ).fetchall()
        links = connection.execute(
            "SELECT projection_kind, projection_slot, projection_table, projection_row_id, state "
            "FROM discord_projection_links WHERE source_event_id = ?",
            (source_event_id,),
        ).fetchall()
    assert [row["id"] for row in import_events] == [result.import_event_id]
    assert [tuple(row) for row in page_rows] == [(scan_id, 1, result.import_event_id)]
    assert len(series_rows) == 2
    assert len(links) == 1
    assert tuple(links[0])[0] == "catalog.antidisable_page"
    assert json.loads(links[0][1]) == {
        "account": "account",
        "page_number": 1,
        "scan_id": scan_id,
        "server": "server",
    }
    assert tuple(links[0])[2:] == ("import_events", result.import_event_id, "completed")


@pytest.mark.parametrize(
    ("link_state", "projection_table", "projection_row_id", "completed_at"),
    [
        pytest.param("claimed", None, None, None, id="claimed-incomplete-link"),
        pytest.param("completed", "import_events", 9999, FINISHED_AT.isoformat(), id="completed-link"),
        pytest.param("completed", "wrong_table", 9999, FINISHED_AT.isoformat(), id="incompatible-completed-target"),
    ],
)
def test_first_processing_rejects_preexisting_projection_link(
    tmp_path, link_state, projection_table, projection_row_id, completed_at
):
    database_path, catalog, discord, coordinator, source_event_id, attempt_id, scan_id = _new_processing(
        tmp_path
    )
    slot = coordinator._antidisable_page_slot("Server", "Account", scan_id, 1)
    _insert_projection_link(
        database_path,
        source_event_id=source_event_id,
        projection_kind="catalog.antidisable_page",
        projection_slot=slot,
        state=link_state,
        projection_table=projection_table,
        projection_row_id=projection_row_id,
        completed_at=completed_at,
    )
    with connect(database_path) as connection:
        before_link = tuple(
            connection.execute(
                "SELECT projection_kind, projection_slot, projection_table, projection_row_id, "
                "state, completed_at FROM discord_projection_links WHERE source_event_id = ?",
                (source_event_id,),
            ).fetchone()
        )
    with pytest.raises(AntidisablePageProjectionIntegrityError):
        _coordinate(coordinator, source_event_id, attempt_id, scan_id=scan_id)
    with connect(database_path) as connection:
        after_link = tuple(
            connection.execute(
                "SELECT projection_kind, projection_slot, projection_table, projection_row_id, "
                "state, completed_at FROM discord_projection_links WHERE source_event_id = ?",
                (source_event_id,),
            ).fetchone()
        )
    assert after_link == before_link
    assert _counts(database_path)["import_events"] == 0
    assert _counts(database_path)["harem_scan_pages"] == 0
    assert _counts(database_path)["discord_projection_links"] == 1


def test_replay_after_reconstructing_coordinator_writes_nothing(tmp_path):
    database_path, catalog, discord, coordinator, source_event_id, result = _setup(tmp_path)
    before = _counts(database_path)
    replay = AntidisablePageProjectionCoordinator(
        CatalogRepository(database_path), DiscordMessageRepository(database_path)
    )
    page = _page()
    output = _coordinate(
        replay,
        source_event_id,
        None,
        page=page,
        scan_id=result.scan_id,
    )
    assert output == AntidisablePageProjectionResult(
        imported_count=0,
        import_event_id=result.import_event_id,
        scan_id=result.scan_id,
        page_number=1,
        page_count=2,
        replay_skipped=True,
        durable_success_recorded=True,
        projection_target=("import_events", result.import_event_id),
    )
    assert _counts(database_path) == before


def test_explicit_scan_coordination_requires_no_listener_memory_state(tmp_path):
    database_path, catalog, discord, coordinator, source_event_id, attempt_id, scan_id = _new_processing(
        tmp_path
    )
    assert not hasattr(coordinator, "_scan_ids")
    assert not hasattr(coordinator, "_scan_contexts")
    first = _coordinate(coordinator, source_event_id, attempt_id, scan_id=scan_id)
    assert first.scan_id == scan_id
    reconstructed = AntidisablePageProjectionCoordinator(
        CatalogRepository(database_path), DiscordMessageRepository(database_path)
    )
    assert not hasattr(reconstructed, "_scan_ids")
    replay = _coordinate(reconstructed, source_event_id, None, scan_id=scan_id)
    assert replay.replay_skipped is True
    assert replay.import_event_id == first.import_event_id


@pytest.mark.parametrize("attempt_case", ["missing", "wrong_event", "finished", "none"])
def test_processing_attempt_is_required_and_active(tmp_path, attempt_case):
    database_path, catalog, discord, coordinator, source_event_id, attempt_id, scan_id = _new_processing(
        tmp_path
    )
    supplied_attempt = attempt_id
    if attempt_case == "missing":
        supplied_attempt = 9999
    elif attempt_case == "wrong_event":
        other_event, other_attempt = _receive_and_begin(discord, suffix="other")
        supplied_attempt = other_attempt
    elif attempt_case == "finished":
        discord.mark_processing_failure(
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            status="failed",
            retryable=True,
            failure_code="test",
            failure_detail="test",
            finished_at=FINISHED_AT,
        )
    elif attempt_case == "none":
        supplied_attempt = None
    with pytest.raises(AntidisablePageProjectionStateError):
        _coordinate(coordinator, source_event_id, supplied_attempt, scan_id=scan_id)
    assert _counts(database_path)["import_events"] == 0


@pytest.mark.parametrize("which", ["missing", "unresolved", "ambiguous", "mismatch", "account_server"])
def test_attribution_must_be_resolved_and_match(tmp_path, which):
    database_path, catalog, discord, coordinator, source_event_id, attempt_id, scan_id = _new_processing(
        tmp_path
    )
    if which == "missing":
        with connect(database_path) as connection:
            connection.execute(
                "DELETE FROM discord_source_event_server_attributions WHERE source_event_id = ?",
                (source_event_id,),
            )
            connection.execute(
                "DELETE FROM discord_source_event_account_attributions WHERE source_event_id = ?",
                (source_event_id,),
            )
            connection.commit()
    elif which == "unresolved":
        with connect(database_path) as connection:
            connection.execute(
                "UPDATE discord_source_event_server_attributions SET status = 'unresolved', server_name = NULL "
                "WHERE source_event_id = ?",
                (source_event_id,),
            )
            connection.execute(
                "UPDATE discord_source_event_account_attributions SET status = 'unresolved', "
                "server_name = NULL, account_name = NULL WHERE source_event_id = ?",
                (source_event_id,),
            )
            connection.commit()
    elif which == "ambiguous":
        with connect(database_path) as connection:
            connection.execute(
                "UPDATE discord_source_event_server_attributions SET status = 'ambiguous', server_name = NULL "
                "WHERE source_event_id = ?",
                (source_event_id,),
            )
            connection.execute(
                "UPDATE discord_source_event_account_attributions SET status = 'ambiguous', "
                "server_name = NULL, account_name = NULL WHERE source_event_id = ?",
                (source_event_id,),
            )
            connection.commit()
    elif which == "mismatch":
        with connect(database_path) as connection:
            connection.execute(
                "UPDATE discord_source_event_server_attributions SET server_name = 'Other' "
                "WHERE source_event_id = ?",
                (source_event_id,),
            )
            connection.commit()
    else:
        with connect(database_path) as connection:
            connection.execute(
                "UPDATE discord_source_event_account_attributions SET server_name = 'Other' "
                "WHERE source_event_id = ?",
                (source_event_id,),
            )
            connection.commit()
    with pytest.raises(AntidisablePageProjectionIntegrityError):
        _coordinate(coordinator, source_event_id, attempt_id, scan_id=scan_id)
    assert _counts(database_path)["import_events"] == 0


def test_database_path_mismatch_is_rejected(tmp_path):
    catalog = CatalogRepository(tmp_path / "catalog.db")
    discord = DiscordMessageRepository(tmp_path / "discord.db")
    with pytest.raises(AntidisablePageProjectionDatabasePathError):
        AntidisablePageProjectionCoordinator(catalog, discord)


def test_projection_slot_and_target_tampering_fail_closed(tmp_path):
    database_path, catalog, discord, coordinator, source_event_id, result = _setup(tmp_path)
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE discord_projection_links SET projection_table = 'wrong' WHERE source_event_id = ?",
            (source_event_id,),
        )
        connection.commit()
    _assert_replay_failure_without_writes(
        database_path, coordinator, source_event_id, result
    )


@pytest.mark.parametrize("tamper", ["wrong_kind", "wrong_target", "null_target", "claimed", "wrong_slot"])
def test_replay_link_integrity_fail_closed(tmp_path, tamper):
    database_path, catalog, discord, coordinator, source_event_id, result = _setup(tmp_path)
    with connect(database_path) as connection:
        if tamper == "wrong_kind":
            connection.execute(
                "UPDATE discord_projection_links SET projection_kind = 'catalog.other' WHERE source_event_id = ?",
                (source_event_id,),
            )
        elif tamper == "wrong_target":
            connection.execute(
                "UPDATE discord_projection_links SET projection_row_id = ? WHERE source_event_id = ?",
                (result.import_event_id + 99, source_event_id),
            )
        elif tamper == "null_target":
            connection.execute(
                "UPDATE discord_projection_links SET state = 'claimed', projection_table = NULL, "
                "projection_row_id = NULL, completed_at = NULL WHERE source_event_id = ?",
                (source_event_id,),
            )
        elif tamper == "claimed":
            connection.execute(
                "UPDATE discord_projection_links SET state = 'claimed', projection_table = NULL, "
                "projection_row_id = NULL, completed_at = NULL WHERE source_event_id = ?",
                (source_event_id,),
            )
        else:
            connection.execute(
                "UPDATE discord_projection_links SET projection_slot = '{}' WHERE source_event_id = ?",
                (source_event_id,),
            )
        connection.commit()
    _assert_replay_failure_without_writes(
        database_path, coordinator, source_event_id, result
    )


def test_replay_rejects_completed_link_with_independent_null_target(tmp_path):
    database_path, catalog, discord, coordinator, source_event_id, result = _setup(tmp_path)
    with connect(database_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE discord_projection_links SET projection_row_id = NULL "
            "WHERE source_event_id = ?",
            (source_event_id,),
        )
        connection.commit()
        corrupted = connection.execute(
            "SELECT state, projection_kind, projection_slot, projection_table, projection_row_id "
            "FROM discord_projection_links WHERE source_event_id = ?",
            (source_event_id,),
        ).fetchone()
    assert tuple(corrupted)[0:4] == (
        "completed",
        "catalog.antidisable_page",
        coordinator._antidisable_page_slot("Server", "Account", result.scan_id, 1),
        "import_events",
    )
    assert corrupted["projection_row_id"] is None
    _assert_replay_failure_without_writes(
        database_path, coordinator, source_event_id, result
    )


@pytest.mark.parametrize("tamper", ["missing_event", "wrong_kind", "wrong_raw", "wrong_source", "wrong_time"])
def test_replay_import_event_integrity_fail_closed(tmp_path, tamper):
    database_path, catalog, discord, coordinator, source_event_id, result = _setup(tmp_path)
    with connect(database_path) as connection:
        if tamper == "missing_event":
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("DELETE FROM import_events WHERE id = ?", (result.import_event_id,))
        elif tamper == "wrong_kind":
            connection.execute(
                "UPDATE import_events SET kind = 'tower_state' WHERE id = ?", (result.import_event_id,)
            )
        elif tamper == "wrong_raw":
            connection.execute(
                "UPDATE import_events SET raw_message = 'tampered' WHERE id = ?", (result.import_event_id,)
            )
        elif tamper == "wrong_source":
            connection.execute(
                "UPDATE import_events SET source = 'cli' WHERE id = ?", (result.import_event_id,)
            )
        else:
            connection.execute(
                "UPDATE import_events SET observed_at = ? WHERE id = ?",
                (FINISHED_AT.isoformat(), result.import_event_id),
            )
        connection.commit()
    _assert_replay_failure_without_writes(
        database_path, coordinator, source_event_id, result
    )


@pytest.mark.parametrize("tamper", ["page_missing", "page_scan", "page_number", "count", "character", "series"])
def test_replay_page_target_integrity_fail_closed(tmp_path, tamper):
    database_path, catalog, discord, coordinator, source_event_id, result = _setup(tmp_path)
    with connect(database_path) as connection:
        if tamper == "page_missing":
            connection.execute("DELETE FROM harem_scan_pages WHERE import_event_id = ?", (result.import_event_id,))
        elif tamper == "page_scan":
            other_scan = catalog.begin_antidisable_scan("Other", "Other").id
            connection.execute(
                "UPDATE harem_scan_pages SET harem_scan_id = ? WHERE import_event_id = ?",
                (other_scan, result.import_event_id),
            )
        elif tamper == "page_number":
            connection.execute(
                "UPDATE harem_scan_pages SET page_number = 2 WHERE import_event_id = ?",
                (result.import_event_id,),
            )
        elif tamper == "count":
            connection.execute(
                "UPDATE harem_scans SET expected_page_count = 9 WHERE id = ?", (result.scan_id,)
            )
        elif tamper == "character":
            connection.execute(
                "UPDATE antidisable_series_observations SET antidisabled_character_count = 99 "
                "WHERE import_event_id = ?",
                (result.import_event_id,),
            )
        else:
            connection.execute(
                "UPDATE antidisable_series_observations SET series_name = 'Tampered' "
                "WHERE import_event_id = ? AND id = (SELECT MIN(id) FROM antidisable_series_observations WHERE import_event_id = ?)",
                (result.import_event_id, result.import_event_id),
            )
        connection.commit()
    _assert_replay_failure_without_writes(
        database_path, coordinator, source_event_id, result
    )


def test_replay_rejects_persisted_series_order_tampering_without_writes(tmp_path):
    page = _page(count=1, series=("Series A", "Series B"))
    database_path, catalog, discord, coordinator, source_event_id, result = _setup(
        tmp_path, page=page
    )
    with connect(database_path) as connection:
        _swap_series_observation_positions(connection, result.import_event_id, 0, 1)
        connection.commit()
        persisted_names = [
            row["series_name"]
            for row in connection.execute(
                "SELECT series_name FROM antidisable_series_observations "
                "WHERE import_event_id = ? ORDER BY id",
                (result.import_event_id,),
            ).fetchall()
        ]
    assert persisted_names == ["Series B", "Series A"]
    _assert_replay_failure_without_writes(
        database_path, coordinator, source_event_id, result, page=page
    )


@pytest.mark.parametrize(
    "tamper",
    [
        pytest.param("remove_duplicate", id="remove-duplicate"),
        pytest.param("add_duplicate", id="add-duplicate"),
        pytest.param("move_duplicate", id="move-duplicate-position"),
    ],
)
def test_replay_rejects_duplicate_multiplicity_or_position_tampering(tmp_path, tamper):
    page = _page(count=1, series=("Series A", "Series B", "Series A"))
    database_path, catalog, discord, coordinator, source_event_id, result = _setup(
        tmp_path, page=page
    )
    with connect(database_path) as connection:
        if tamper == "remove_duplicate":
            duplicate = connection.execute(
                "SELECT id FROM antidisable_series_observations "
                "WHERE import_event_id = ? AND series_name = ? ORDER BY id DESC LIMIT 1",
                (result.import_event_id, "Series A"),
            ).fetchone()
            connection.execute(
                "DELETE FROM antidisable_series_observations WHERE id = ?",
                (duplicate["id"],),
            )
        elif tamper == "add_duplicate":
            row = connection.execute(
                "SELECT account_context_id, observed_at, harem_scan_id, "
                "antidisabled_character_count FROM antidisable_series_observations "
                "WHERE import_event_id = ? LIMIT 1",
                (result.import_event_id,),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO antidisable_series_observations (
                    account_context_id, series_name, normalized_series_name,
                    antidisabled_character_count, observed_at, import_event_id, harem_scan_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["account_context_id"],
                    "Series A",
                    CatalogRepository._normalize("Series A"),
                    row["antidisabled_character_count"],
                    row["observed_at"],
                    result.import_event_id,
                    row["harem_scan_id"],
                ),
            )
        else:
            _swap_series_observation_positions(connection, result.import_event_id, 1, 2)
        connection.commit()
    _assert_replay_failure_without_writes(
        database_path, coordinator, source_event_id, result, page=page
    )


def test_replay_rejects_unexpected_series_on_empty_page_without_repair(tmp_path):
    page = _page(count=1, series=())
    database_path, catalog, discord, coordinator, source_event_id, result = _setup(
        tmp_path, page=page
    )
    with connect(database_path) as connection:
        account_context_id = connection.execute(
            "SELECT account_context_id FROM harem_scans WHERE id = ?",
            (result.scan_id,),
        ).fetchone()["account_context_id"]
        connection.execute(
            """
            INSERT INTO antidisable_series_observations (
                account_context_id, series_name, normalized_series_name,
                antidisabled_character_count, observed_at, import_event_id, harem_scan_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_context_id,
                "Unexpected",
                CatalogRepository._normalize("Unexpected"),
                page.antidisabled_character_count,
                OBSERVED_AT.isoformat(),
                result.import_event_id,
                result.scan_id,
            ),
        )
        connection.commit()
    _assert_replay_failure_without_writes(
        database_path, coordinator, source_event_id, result, page=page
    )
    with connect(database_path) as connection:
        unexpected = connection.execute(
            "SELECT series_name FROM antidisable_series_observations "
            "WHERE import_event_id = ?",
            (result.import_event_id,),
        ).fetchall()
    assert [row["series_name"] for row in unexpected] == ["Unexpected"]


@pytest.mark.parametrize(
    "tamper",
    [
        pytest.param("display_name", id="display-series-name"),
        pytest.param("normalized_name", id="normalized-series-name"),
        pytest.param("character_count", id="character-count"),
        pytest.param("account_context_id", id="account-context-ownership"),
        pytest.param("observed_at", id="observed-at"),
        pytest.param("import_event_id", id="import-event-ownership"),
        pytest.param("harem_scan_id", id="scan-ownership"),
    ],
)
def test_replay_rejects_each_persisted_series_property_tampering(tmp_path, tamper):
    page = _page(count=1, series=("Series A", "Series B"))
    database_path, catalog, discord, coordinator, source_event_id, result = _setup(
        tmp_path, page=page
    )
    other_scan_id = None
    if tamper in {"account_context_id", "harem_scan_id"}:
        other_scan = (
            catalog.begin_antidisable_scan("Other", "Other")
            if tamper == "account_context_id"
            else catalog.begin_antidisable_scan("Server", "Account")
        )
        other_scan_id = other_scan.id
    with connect(database_path) as connection:
        observation = connection.execute(
            "SELECT * FROM antidisable_series_observations "
            "WHERE import_event_id = ? ORDER BY id LIMIT 1",
            (result.import_event_id,),
        ).fetchone()
        if tamper == "display_name":
            connection.execute(
                "UPDATE antidisable_series_observations SET series_name = ? WHERE id = ?",
                ("Tampered", observation["id"]),
            )
        elif tamper == "normalized_name":
            connection.execute(
                "UPDATE antidisable_series_observations SET normalized_series_name = ? WHERE id = ?",
                ("tampered", observation["id"]),
            )
        elif tamper == "character_count":
            connection.execute(
                "UPDATE antidisable_series_observations SET antidisabled_character_count = ? WHERE id = ?",
                (99, observation["id"]),
            )
        elif tamper == "account_context_id":
            other_context = connection.execute(
                "SELECT account_context_id FROM harem_scans WHERE id = ?",
                (other_scan_id,),
            ).fetchone()["account_context_id"]
            connection.execute(
                "UPDATE antidisable_series_observations SET account_context_id = ? WHERE id = ?",
                (other_context, observation["id"]),
            )
        elif tamper == "observed_at":
            connection.execute(
                "UPDATE antidisable_series_observations SET observed_at = ? WHERE id = ?",
                (FINISHED_AT.isoformat(), observation["id"]),
            )
        elif tamper == "import_event_id":
            cursor = connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) "
                "VALUES ('antidisable', 'discord', ?, 'adl payload')",
                (OBSERVED_AT.isoformat(),),
            )
            other_import_event_id = int(cursor.lastrowid)
            connection.execute(
                "UPDATE antidisable_series_observations SET import_event_id = ? WHERE id = ?",
                (other_import_event_id, observation["id"]),
            )
        else:
            connection.execute(
                "UPDATE antidisable_series_observations SET harem_scan_id = ? WHERE id = ?",
                (other_scan_id, observation["id"]),
            )
        connection.commit()
    _assert_replay_failure_without_writes(
        database_path, coordinator, source_event_id, result, page=page
    )


def test_replay_after_scan_completion_is_valid_and_does_not_complete_during_import(tmp_path):
    database_path, catalog, discord, coordinator, source_event_id, result = _setup(
        tmp_path, page=_page(count=1)
    )
    catalog.complete_antidisable_scan(result.scan_id)
    replay = _coordinate(
        coordinator,
        source_event_id,
        None,
        page=_page(count=1),
        scan_id=result.scan_id,
    )
    assert replay.replay_skipped is True and replay.imported_count == 0


def test_scan_completion_requires_existing_separate_operation(tmp_path):
    database_path, catalog, discord, coordinator, source_event_id, result = _setup(tmp_path)
    progress = catalog.harem_scan_progress(result.scan_id)
    assert progress is not None and progress.completed_at is None
    with pytest.raises(ValueError, match="incomplete"):
        catalog.complete_antidisable_scan(result.scan_id)


def test_prior_page_workflow_and_bindings_survive_later_page_rollback(
    tmp_path, monkeypatch
):
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    _receive_and_begin(discord, suffix="request", raw="adl request")
    scan = _begin_scan(catalog)
    discord.create_antidisable_workflow(
        scan_id=scan.id,
        request_message_aggregate_key=MessageAggregateKey(
            SourcePlatform.DISCORD, "guild", "channel", "message-request"
        ),
        requesting_user_id="known-user",
        created_at=OBSERVED_AT,
        expires_at=FINISHED_AT,
    )

    first_event, first_attempt = _receive_and_begin(discord, suffix="first")
    _record_attribution(discord, first_event)
    discord.bind_antidisable_response(
        scan_id=scan.id,
        response_message_aggregate_key=MessageAggregateKey(
            SourcePlatform.DISCORD, "guild", "channel", "message-first"
        ),
        bound_at=OBSERVED_AT,
    )
    first = _coordinate(
        coordinator,
        first_event,
        first_attempt,
        page=_page(number=1),
        scan_id=scan.id,
    )

    second_event, second_attempt = _receive_and_begin(discord, suffix="second")
    _record_attribution(discord, second_event)
    discord.bind_antidisable_response(
        scan_id=scan.id,
        response_message_aggregate_key=MessageAggregateKey(
            SourcePlatform.DISCORD, "guild", "channel", "message-second"
        ),
        bound_at=OBSERVED_AT,
    )
    before = _counts(database_path)
    original_helper = catalog._import_antidisable_page_with_connection

    def fail_after_write(*args, **kwargs):
        original_helper(*args, **kwargs)
        raise RuntimeError("forced later-page failure")

    monkeypatch.setattr(catalog, "_import_antidisable_page_with_connection", fail_after_write)
    with pytest.raises(RuntimeError, match="forced later-page failure"):
        _coordinate(
            coordinator,
            second_event,
            second_attempt,
            page=_page(number=2),
            scan_id=scan.id,
        )

    assert _counts(database_path) == before
    with connect(database_path) as connection:
        pages = connection.execute(
            "SELECT page_number, import_event_id FROM harem_scan_pages "
            "WHERE harem_scan_id = ? ORDER BY page_number",
            (scan.id,),
        ).fetchall()
        scan_row = connection.execute(
            "SELECT expected_page_count, completed_at FROM harem_scans WHERE id = ?",
            (scan.id,),
        ).fetchone()
        workflow_count = connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_workflows WHERE harem_scan_id = ?",
            (scan.id,),
        ).fetchone()[0]
        binding_count = connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_response_bindings "
            "WHERE harem_scan_id = ?",
            (scan.id,),
        ).fetchone()[0]
        source_rows = connection.execute(
            "SELECT id, status, legacy_import_event_id FROM discord_source_events "
            "WHERE id IN (?, ?) ORDER BY id",
            (first_event, second_event),
        ).fetchall()
    assert [tuple(row) for row in pages] == [(1, first.import_event_id)]
    assert tuple(scan_row) == (2, None)
    assert workflow_count == 1
    assert binding_count == 2
    assert [tuple(row) for row in source_rows] == [
        (first_event, "succeeded", first.import_event_id),
        (second_event, "processing", None),
    ]


def test_runner_replay_commit_is_durable_noop_with_other_page_present(tmp_path):
    database_path, catalog, discord, coordinator = _repositories(tmp_path)
    scan = _begin_scan(catalog)
    second_event, second_attempt = _receive_and_begin(discord, suffix="second")
    _record_attribution(discord, second_event)
    second = _coordinate(
        coordinator,
        second_event,
        second_attempt,
        page=_page(number=2),
        scan_id=scan.id,
    )
    first_event, first_attempt = _receive_and_begin(discord, suffix="first")
    _record_attribution(discord, first_event)
    _coordinate(
        coordinator,
        first_event,
        first_attempt,
        page=_page(number=1),
        scan_id=scan.id,
    )

    def durable_snapshot():
        with connect(database_path) as connection:
            return {
                "counts": _counts(database_path),
                "scan": tuple(
                    connection.execute(
                        "SELECT expected_page_count, completed_at FROM harem_scans WHERE id = ?",
                        (scan.id,),
                    ).fetchone()
                ),
                "pages": [
                    tuple(row)
                    for row in connection.execute(
                        "SELECT page_number, import_event_id FROM harem_scan_pages "
                        "WHERE harem_scan_id = ? ORDER BY page_number",
                        (scan.id,),
                    ).fetchall()
                ],
                "links": [
                    tuple(row)
                    for row in connection.execute(
                        "SELECT source_event_id, projection_slot, projection_table, "
                        "projection_row_id, state FROM discord_projection_links ORDER BY id"
                    ).fetchall()
                ],
                "events": [
                    tuple(row)
                    for row in connection.execute(
                        "SELECT id, status, legacy_import_event_id FROM discord_source_events "
                        "ORDER BY id"
                    ).fetchall()
                ],
                "attempts": [
                    tuple(row)
                    for row in connection.execute(
                        "SELECT source_event_id, status FROM discord_processing_attempts ORDER BY id"
                    ).fetchall()
                ],
            }

    before = durable_snapshot()
    replay = _coordinate(
        coordinator,
        second_event,
        None,
        page=_page(number=2),
        scan_id=scan.id,
    )
    assert replay.replay_skipped is True
    assert replay.imported_count == 0
    assert replay.import_event_id == second.import_event_id
    assert durable_snapshot() == before


def test_coordinator_runner_uses_no_public_page_or_scan_wrapper(
    tmp_path, monkeypatch
):
    database_path, catalog, discord, coordinator, source_event_id, attempt_id, scan_id = (
        _new_processing(tmp_path)
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("public transaction owner entered from page runner")

    monkeypatch.setattr(catalog, "import_antidisable_page", forbidden)
    monkeypatch.setattr(catalog, "begin_antidisable_scan", forbidden)
    monkeypatch.setattr(catalog, "complete_antidisable_scan", forbidden)
    result = _coordinate(
        coordinator,
        source_event_id,
        attempt_id,
        page=_page(count=1),
        scan_id=scan_id,
    )
    assert result.imported_count == 1
    progress = catalog.harem_scan_progress(scan_id)
    assert progress is not None
    assert progress.is_complete is True
    assert progress.completed_at is None
    assert _counts(database_path)["harem_scan_pages"] == 1


def test_no_per_series_projection_and_persistent_slot_limitation_is_explicit(tmp_path):
    _, catalog, discord, coordinator, source_event_id, result = _setup(tmp_path)
    assert result.projection_target[0] == "import_events"
    with connect(catalog._database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_projection_links WHERE source_event_id = ?",
            (source_event_id,),
        ).fetchone()[0] == 1
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(import_events)").fetchall()
        }
    assert "slots_used" not in columns
    assert "slots_capacity" not in columns


def test_wrong_input_raw_and_source_are_rejected_before_write(tmp_path):
    database_path, catalog, discord, coordinator, source_event_id, attempt_id, scan_id = _new_processing(
        tmp_path
    )
    with pytest.raises(AntidisablePageProjectionIntegrityError):
        _coordinate(coordinator, source_event_id, attempt_id, scan_id=scan_id, raw="wrong")
    assert _counts(database_path)["import_events"] == 0


def test_invalid_page_metadata_and_identity_inputs_fail_without_writes(tmp_path):
    database_path, catalog, discord, coordinator, source_event_id, attempt_id, scan_id = _new_processing(
        tmp_path
    )
    with pytest.raises(AntidisablePageProjectionStateError):
        _coordinate(coordinator, source_event_id, attempt_id, page=_page(number=None), scan_id=scan_id)
    with pytest.raises(ValueError):
        _coordinate(coordinator, source_event_id, attempt_id, scan_id=True)
    assert _counts(database_path)["import_events"] == 0
