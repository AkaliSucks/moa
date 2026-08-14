import json
import sqlite3
import threading
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import (
    AntidisablePage,
    BadgeLevel,
    CharacterDetails,
    ClaimConfirmation,
    DisableListEntry,
    DisableListSnapshot,
    DivorceConfirmation,
    HaremKeyEntry,
    HaremKeyPage,
    KakeraReactionReceipt,
    KakeraStateSnapshot,
    KakeralootStateSnapshot,
    PlayerBonusMetric,
    PlayerBonusSnapshot,
    ProfileSnapshot,
    RankedCharacter,
    RankedHaremEntry,
    RankedHaremPage,
    RollObservation,
    ServerSettingMetric,
    ServerSettingsSnapshot,
    KakeralootSettingsSnapshot,
    MudapinSnapshot,
    PersonalRareSnapshot,
    SphereGain,
    SphereResultSnapshot,
    TowerStateSnapshot,
    TimerStateSnapshot,
    TopPage,
    UnavailableCharacter,
    UnavailableCharacterPage,
    WishlistEntry,
    WishlistSnapshot,
)
from moa.models.discord_identity import MessageAggregateKey, MessageRevisionKey, SourcePlatform
from moa.repositories import catalog_repository as catalog_repository_module
from moa.repositories import harem_repository as harem_repository_module
from moa.repositories import profile_repository as profile_repository_module
from moa.repositories.catalog_repository import (
    CatalogRepository,
    ImportEventDeletionBlockedError,
    _AntidisablePageImportConnectionResult,
    _PlayerBonusImportConnectionResult,
)
from moa.repositories.disablelist_repository import _DisableListImportConnectionResult
from moa.repositories.kakeraloot_state_repository import (
    _KakeralootStateImportConnectionResult,
)
from moa.repositories.discord_message_repository import DiscordMessageRepository
from moa.repositories.wishlist_repository import _WishlistImportConnectionResult
from moa.services.disablelist_projection_coordinator import (
    DisableListProjectionCoordinator,
    DisableListProjectionResult,
)
from moa.services.profile_projection_coordinator import (
    ProfileProjectionCoordinator,
    ProfileProjectionResult,
)
from moa.services.settings_projection_coordinator import (
    SettingsProjectionCoordinator,
    SettingsProjectionResult,
)
from moa.services.sphere_result_projection_coordinator import (
    SphereResultProjectionCoordinator,
    SphereResultProjectionResult,
)
from moa.services.timer_projection_coordinator import (
    TimerProjectionCoordinator,
    TimerProjectionResult,
)
from moa.services.wishlist_projection_coordinator import (
    WishlistProjectionCoordinator,
    WishlistProjectionResult,
)


_THREAD_TIMEOUT = 5.0


def test_public_harem_scan_startup_persists_keys_progress(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    progress = catalog.begin_harem_scan(" Server ", " Account ", "keys")

    assert progress.id > 0
    assert progress.server_name == "Server"
    assert progress.account_name == "Account"
    assert progress.expected_page_count is None
    assert progress.imported_pages == ()
    assert progress.completed_at is None
    assert progress.scan_kind == "keys"
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT harem_scans.id, server_contexts.name AS server_name,
                   account_contexts.name AS account_name,
                   harem_scans.expected_page_count, harem_scans.started_at,
                   harem_scans.completed_at, harem_scans.scan_kind
            FROM harem_scans
            JOIN account_contexts ON account_contexts.id = harem_scans.account_context_id
            JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
            WHERE harem_scans.id = ?
            """,
            (progress.id,),
        ).fetchone()

    assert row is not None
    assert row["id"] == progress.id
    assert row["server_name"] == progress.server_name
    assert row["account_name"] == progress.account_name
    assert row["expected_page_count"] == progress.expected_page_count
    assert datetime.fromisoformat(row["started_at"]).tzinfo is not None
    assert row["completed_at"] is None
    assert row["scan_kind"] == progress.scan_kind


def test_public_harem_scan_startup_persists_owned_kind(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    progress = catalog.begin_harem_scan("Server", "Account", " OwNeD ")

    assert progress.id > 0
    assert progress.scan_kind == "owned"
    assert progress.expected_page_count is None
    assert progress.imported_pages == ()
    assert progress.completed_at is None
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT scan_kind, expected_page_count, completed_at "
            "FROM harem_scans WHERE id = ?",
            (progress.id,),
        ).fetchone()

    assert tuple(row) == ("owned", None, None)


def test_public_harem_scan_startup_rolls_back_contexts_and_recovers(
    tmp_path,
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    existing = catalog.begin_harem_scan("Original Server", "Original Account", "keys")
    with connect(database_path) as connection:
        before_server = connection.execute(
            "SELECT id, name, normalized_name, created_at, updated_at FROM server_contexts"
        ).fetchone()
        before_account = connection.execute(
            "SELECT id, name, normalized_name, created_at, updated_at FROM account_contexts"
        ).fetchone()
        before_scan = connection.execute(
            "SELECT id, account_context_id, expected_page_count, started_at, completed_at, scan_kind "
            "FROM harem_scans WHERE id = ?",
            (existing.id,),
        ).fetchone()
        connection.execute(
            """
            CREATE TRIGGER fail_harem_scan_startup
            BEFORE INSERT ON harem_scans
            BEGIN
                SELECT RAISE(FAIL, 'forced harem scan startup failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced harem scan startup failure"):
        catalog.begin_harem_scan("New Server", "New Account", "keys")

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM server_contexts WHERE normalized_name = 'new server'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM account_contexts WHERE normalized_name = 'new account'"
        ).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM harem_scans").fetchone()[0] == 1

    with pytest.raises(sqlite3.IntegrityError, match="forced harem scan startup failure"):
        catalog.begin_harem_scan(" ORIGINAL SERVER ", " ORIGINAL ACCOUNT ", "owned")

    with connect(database_path) as connection:
        after_server = connection.execute(
            "SELECT id, name, normalized_name, created_at, updated_at FROM server_contexts"
        ).fetchone()
        after_account = connection.execute(
            "SELECT id, name, normalized_name, created_at, updated_at FROM account_contexts"
        ).fetchone()
        after_scan = connection.execute(
            "SELECT id, account_context_id, expected_page_count, started_at, completed_at, scan_kind "
            "FROM harem_scans WHERE id = ?",
            (existing.id,),
        ).fetchone()
        assert tuple(after_server) == tuple(before_server)
        assert tuple(after_account) == tuple(before_account)
        assert tuple(after_scan) == tuple(before_scan)
        connection.execute("DROP TRIGGER fail_harem_scan_startup")

    recovered = catalog.begin_harem_scan("Original Server", "Original Account", "owned")

    assert recovered.id > existing.id
    assert recovered.scan_kind == "owned"
    assert recovered.server_name == "Original Server"
    assert recovered.account_name == "Original Account"
    assert recovered.expected_page_count is None
    assert recovered.imported_pages == ()
    assert recovered.completed_at is None
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM harem_scans").fetchone()[0] == 2


def test_harem_scan_completion_uses_supplied_connection_and_persists_result(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    scan = catalog.begin_harem_scan("Server", "Account", "keys")
    imported = catalog.import_harem_key_page(
        HaremKeyPage(
            page_number=1,
            page_count=1,
            entries=(
                HaremKeyEntry(
                    name="Complete Character",
                    key_type="gold",
                    key_count=7,
                    kakera_value=1_453,
                ),
            ),
        ),
        "Server",
        "Account",
        "complete page",
        "test",
        scan.id,
    )
    with connect(database_path) as connection:
        before_pages = [
            tuple(row)
            for row in connection.execute(
                "SELECT page_number, import_event_id FROM harem_scan_pages "
                "WHERE harem_scan_id = ? ORDER BY page_number",
                (scan.id,),
            ).fetchall()
        ]

    original_helper = catalog._harem_repository._harem_scan_progress_with_connection
    helper_calls: list[tuple[int, bool]] = []

    def observed_helper(connection: sqlite3.Connection, scan_id: int):
        helper_calls.append((id(connection), connection.in_transaction))
        return original_helper(connection, scan_id)

    def unexpected_connection():
        raise AssertionError("completion opened an independent repository connection")

    monkeypatch.setattr(
        catalog._harem_repository,
        "_harem_scan_progress_with_connection",
        observed_helper,
    )
    monkeypatch.setattr(catalog._harem_repository, "_connection", unexpected_connection)

    completed = catalog.complete_harem_scan(scan.id)

    assert len(helper_calls) == 2
    assert helper_calls[0][0] == helper_calls[1][0]
    assert [in_transaction for _connection_id, in_transaction in helper_calls] == [True, True]
    assert completed.id == scan.id
    assert completed.expected_page_count == 1
    assert completed.imported_pages == (1,)
    assert completed.completed_at is not None
    assert completed.is_complete is True
    with connect(database_path) as connection:
        before_total_changes = connection.total_changes
        durable = original_helper(connection, scan.id)
        assert connection.in_transaction is False
        assert connection.total_changes == before_total_changes
        after_pages = [
            tuple(row)
            for row in connection.execute(
                "SELECT page_number, import_event_id FROM harem_scan_pages "
                "WHERE harem_scan_id = ? ORDER BY page_number",
                (scan.id,),
            ).fetchall()
        ]
    assert durable == completed
    assert before_pages == after_pages == [(1, imported.import_event_id)]


def test_catalog_scan_progress_helper_delegates_on_supplied_connection(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    scan = catalog.begin_harem_scan("Server", "Account", "keys")

    def unexpected_connection():
        raise AssertionError("caller-owned helper opened an independent connection")

    def unexpected_runner(_database_path, _callback):
        raise AssertionError("caller-owned helper opened a runner transaction")

    monkeypatch.setattr(catalog._harem_repository, "_connection", unexpected_connection)
    monkeypatch.setattr(
        harem_repository_module, "run_write_transaction", unexpected_runner
    )

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        progress = catalog._harem_scan_progress_with_connection(connection, scan.id)
        assert connection.in_transaction is True
        connection.rollback()

    assert progress == scan


def test_harem_scan_completion_rejects_incomplete_scan_without_changes(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    scan = catalog.begin_harem_scan("Server", "Account", "keys")
    imported = catalog.import_harem_key_page(
        HaremKeyPage(
            page_number=1,
            page_count=2,
            entries=(HaremKeyEntry(name="Page One", key_type="silver", key_count=5),),
        ),
        "Server",
        "Account",
        "incomplete page",
        "test",
        scan.id,
    )

    with pytest.raises(ValueError, match="Harem scan is incomplete"):
        catalog.complete_harem_scan(scan.id)

    progress = catalog.harem_scan_progress(scan.id)
    assert progress is not None
    assert progress.expected_page_count == 2
    assert progress.imported_pages == (1,)
    assert progress.completed_at is None
    with connect(database_path) as connection:
        pages = connection.execute(
            "SELECT page_number, import_event_id FROM harem_scan_pages WHERE harem_scan_id = ?",
            (scan.id,),
        ).fetchall()
    assert [tuple(row) for row in pages] == [(1, imported.import_event_id)]


def test_harem_scan_completion_update_failure_rolls_back_and_same_database_recovers(
    tmp_path,
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    scan = catalog.begin_harem_scan("Server", "Account", "keys")
    imported = catalog.import_harem_key_page(
        HaremKeyPage(
            page_number=1,
            page_count=1,
            entries=(HaremKeyEntry(name="Rollback Character", key_type="gold", key_count=6),),
        ),
        "Server",
        "Account",
        "rollback page",
        "test",
        scan.id,
    )
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_harem_scan_completion
            BEFORE UPDATE OF completed_at ON harem_scans
            WHEN NEW.completed_at IS NOT NULL
            BEGIN
                SELECT RAISE(FAIL, 'forced harem scan completion failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced harem scan completion failure"):
        catalog.complete_harem_scan(scan.id)

    progress = catalog.harem_scan_progress(scan.id)
    assert progress is not None
    assert progress.expected_page_count == 1
    assert progress.imported_pages == (1,)
    assert progress.completed_at is None
    with connect(database_path) as connection:
        pages = connection.execute(
            "SELECT page_number, import_event_id FROM harem_scan_pages WHERE harem_scan_id = ?",
            (scan.id,),
        ).fetchall()
        connection.execute("DROP TRIGGER fail_harem_scan_completion")
    assert [tuple(row) for row in pages] == [(1, imported.import_event_id)]

    recovered = catalog.complete_harem_scan(scan.id)

    assert recovered.id == scan.id
    assert recovered.imported_pages == (1,)
    assert recovered.completed_at is not None


def test_harem_scan_completion_final_read_failure_rolls_back_update(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    scan = catalog.begin_harem_scan("Server", "Account", "keys")
    catalog.import_harem_key_page(
        HaremKeyPage(
            page_number=1,
            page_count=1,
            entries=(HaremKeyEntry(name="Final Read", key_type="bronze", key_count=1),),
        ),
        "Server",
        "Account",
        "final read page",
        "test",
        scan.id,
    )
    original_helper = catalog._harem_repository._harem_scan_progress_with_connection
    failure = RuntimeError("forced final harem progress read failure")
    helper_call_count = 0

    def fail_final_read(connection: sqlite3.Connection, scan_id: int):
        nonlocal helper_call_count
        helper_call_count += 1
        if helper_call_count == 2:
            raise failure
        return original_helper(connection, scan_id)

    monkeypatch.setattr(
        catalog._harem_repository,
        "_harem_scan_progress_with_connection",
        fail_final_read,
    )

    with pytest.raises(RuntimeError) as raised:
        catalog.complete_harem_scan(scan.id)

    assert raised.value is failure
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT expected_page_count, completed_at FROM harem_scans WHERE id = ?",
            (scan.id,),
        ).fetchone()
        pages = connection.execute(
            "SELECT page_number FROM harem_scan_pages WHERE harem_scan_id = ?",
            (scan.id,),
        ).fetchall()
    assert tuple(row) == (1, None)
    assert [page["page_number"] for page in pages] == [1]


def test_harem_scan_completion_serializes_validation_before_competing_page_writer(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    scan = catalog.begin_harem_scan("Server", "Account", "keys")
    catalog.import_harem_key_page(
        HaremKeyPage(
            page_number=1,
            page_count=1,
            entries=(HaremKeyEntry(name="Initial Page", key_type="gold", key_count=7),),
        ),
        "Server",
        "Account",
        "initial page",
        "test",
        scan.id,
    )
    original_helper = catalog._harem_repository._harem_scan_progress_with_connection
    original_runner = harem_repository_module.run_write_transaction
    validation_finished = threading.Event()
    release_completion = threading.Event()
    writer_started = threading.Event()
    writer_entered_callback = threading.Event()
    completion_failures: list[BaseException] = []
    writer_failures: list[BaseException] = []
    helper_call_count = 0

    def pause_after_validation(connection: sqlite3.Connection, scan_id: int):
        nonlocal helper_call_count
        progress = original_helper(connection, scan_id)
        helper_call_count += 1
        if helper_call_count == 1:
            validation_finished.set()
            assert release_completion.wait(_THREAD_TIMEOUT), "completion was not released"
        return progress

    def observed_runner(database_path, callback):
        if threading.current_thread().name != "harem-key-page-writer":
            return original_runner(database_path, callback)

        def observed_callback(connection: sqlite3.Connection):
            writer_entered_callback.set()
            return callback(connection)

        return original_runner(database_path, observed_callback)

    monkeypatch.setattr(
        catalog._harem_repository,
        "_harem_scan_progress_with_connection",
        pause_after_validation,
    )
    monkeypatch.setattr(harem_repository_module, "run_write_transaction", observed_runner)

    def complete_scan() -> None:
        try:
            catalog.complete_harem_scan(scan.id)
        except BaseException as exc:
            completion_failures.append(exc)

    completion_thread = threading.Thread(target=complete_scan)
    completion_thread.start()
    assert validation_finished.wait(_THREAD_TIMEOUT), "completion did not finish validation"

    def import_competing_page() -> None:
        writer_started.set()
        try:
            catalog.import_harem_key_page(
                HaremKeyPage(
                    page_number=2,
                    page_count=2,
                    entries=(
                        HaremKeyEntry(name="Competing Page", key_type="silver", key_count=5),
                    ),
                ),
                "Server",
                "Account",
                "competing page",
                "test",
                scan.id,
            )
        except BaseException as exc:
            writer_failures.append(exc)

    writer_thread = threading.Thread(
        target=import_competing_page,
        name="harem-key-page-writer",
    )
    writer_thread.start()
    assert writer_started.wait(_THREAD_TIMEOUT), "competing page writer did not start"
    assert not writer_entered_callback.is_set()

    release_completion.set()
    completion_thread.join(timeout=_THREAD_TIMEOUT)
    writer_thread.join(timeout=_THREAD_TIMEOUT)

    assert not completion_thread.is_alive(), "completion worker did not terminate"
    assert not writer_thread.is_alive(), "page writer worker did not terminate"
    assert completion_failures == []
    assert writer_entered_callback.is_set()
    assert len(writer_failures) == 1
    assert isinstance(writer_failures[0], ValueError)
    assert str(writer_failures[0]) == "Harem scan is already complete; begin a new scan to refresh it."
    with connect(database_path) as connection:
        completed_at = connection.execute(
            "SELECT completed_at FROM harem_scans WHERE id = ?", (scan.id,)
        ).fetchone()[0]
        pages = connection.execute(
            "SELECT page_number FROM harem_scan_pages WHERE harem_scan_id = ? ORDER BY page_number",
            (scan.id,),
        ).fetchall()
        competing_events = connection.execute(
            "SELECT COUNT(*) FROM import_events WHERE raw_message = 'competing page'"
        ).fetchone()[0]
    assert completed_at is not None
    assert [row["page_number"] for row in pages] == [1]
    assert competing_events == 0


def test_public_harem_key_page_import_persists_complete_scanned_page(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    scan = catalog.begin_harem_scan("Server", "Account", "keys")
    seeded_at = "2026-07-01T00:00:00+00:00"
    with connect(database_path) as connection:
        resolved_character_id = connection.execute(
            """
            INSERT INTO characters (
                name, series, normalized_name, normalized_series, gender, roulette,
                created_at, updated_at
            ) VALUES ('Resolved Character', 'Resolved Series', 'resolved character',
                      'resolved series', 'female', 'wa', ?, ?)
            """,
            (seeded_at, seeded_at),
        ).lastrowid
        connection.execute(
            """
            INSERT INTO characters (
                name, series, normalized_name, normalized_series, gender, roulette,
                created_at, updated_at
            ) VALUES ('Ambiguous Character', 'First Series', 'ambiguous character',
                      'first series', NULL, NULL, ?, ?)
            """,
            (seeded_at, seeded_at),
        )
        connection.execute(
            """
            INSERT INTO characters (
                name, series, normalized_name, normalized_series, gender, roulette,
                created_at, updated_at
            ) VALUES ('Ambiguous Character', 'Second Series', 'ambiguous character',
                      'second series', NULL, NULL, ?, ?)
            """,
            (seeded_at, seeded_at),
        )

    original_runner = harem_repository_module.run_write_transaction
    original_prepare = catalog._harem_repository._prepare_harem_scan_page
    callback_calls: list[tuple[int, bool]] = []
    prepare_calls: list[tuple[int, bool]] = []

    def observed_runner(database_path, callback):
        def observed_callback(connection: sqlite3.Connection):
            callback_calls.append((id(connection), connection.in_transaction))
            return callback(connection)

        return original_runner(database_path, observed_callback)

    def observed_prepare(connection: sqlite3.Connection, *args, **kwargs) -> None:
        prepare_calls.append((id(connection), connection.in_transaction))
        original_prepare(connection, *args, **kwargs)

    def unexpected_connection():
        raise AssertionError("harem-key page import opened an independent connection")

    monkeypatch.setattr(harem_repository_module, "run_write_transaction", observed_runner)
    monkeypatch.setattr(
        catalog._harem_repository, "_prepare_harem_scan_page", observed_prepare
    )
    monkeypatch.setattr(catalog._harem_repository, "_connection", unexpected_connection)

    result = catalog.import_harem_key_page(
        HaremKeyPage(
            page_number=2,
            page_count=3,
            entries=(
                HaremKeyEntry(
                    name="RESOLVED CHARACTER",
                    key_type="gold",
                    key_count=7,
                    kakera_value=1_453,
                ),
                HaremKeyEntry(
                    name="Ambiguous Character",
                    key_type="silver",
                    key_count=5,
                    kakera_value=None,
                ),
                HaremKeyEntry(
                    name="Missing Character",
                    key_type="bronze",
                    key_count=0,
                    kakera_value=0,
                ),
            ),
        ),
        " Server ",
        " Account ",
        "complete harem-key page",
        "discord:test",
        scan.id,
    )

    assert callback_calls == prepare_calls
    assert len(callback_calls) == 1
    assert callback_calls[0][1] is True
    assert set(result.model_dump()) == {
        "import_event_id",
        "server_name",
        "account_name",
        "entries_imported",
        "entries_linked",
        "observed_at",
        "scan_id",
        "page_number",
        "page_count",
    }
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.entries_imported == 3
    assert result.entries_linked == 1
    assert result.scan_id == scan.id
    assert result.page_number == 2
    assert result.page_count == 3
    assert result.observed_at.tzinfo is not None
    assert result.observed_at.utcoffset().total_seconds() == 0
    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        context = connection.execute(
            """
            SELECT server_contexts.name, server_contexts.normalized_name,
                   account_contexts.name, account_contexts.normalized_name
            FROM harem_scans
            JOIN account_contexts ON account_contexts.id = harem_scans.account_context_id
            JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
            WHERE harem_scans.id = ?
            """,
            (scan.id,),
        ).fetchone()
        observations = connection.execute(
            """
            SELECT character_id, character_name, normalized_character_name,
                   key_type, key_count, kakera_value, observed_at,
                   import_event_id, harem_scan_id
            FROM harem_key_observations ORDER BY id
            """
        ).fetchall()
        scan_row = connection.execute(
            "SELECT expected_page_count, completed_at FROM harem_scans WHERE id = ?",
            (scan.id,),
        ).fetchone()
        page_rows = connection.execute(
            """
            SELECT harem_scan_id, page_number, import_event_id
            FROM harem_scan_pages WHERE harem_scan_id = ?
            """,
            (scan.id,),
        ).fetchall()

    assert tuple(event) == (
        "harem_key_page",
        "discord:test",
        result.observed_at.isoformat(),
        "complete harem-key page",
    )
    assert tuple(context) == ("Server", "server", "Account", "account")
    assert [tuple(row) for row in observations] == [
        (
            resolved_character_id,
            "RESOLVED CHARACTER",
            "resolved character",
            "gold",
            7,
            1_453,
            result.observed_at.isoformat(),
            result.import_event_id,
            scan.id,
        ),
        (
            None,
            "Ambiguous Character",
            "ambiguous character",
            "silver",
            5,
            None,
            result.observed_at.isoformat(),
            result.import_event_id,
            scan.id,
        ),
        (
            None,
            "Missing Character",
            "missing character",
            "bronze",
            0,
            0,
            result.observed_at.isoformat(),
            result.import_event_id,
            scan.id,
        ),
    ]
    assert tuple(scan_row) == (3, None)
    assert [tuple(row) for row in page_rows] == [(scan.id, 2, result.import_event_id)]


def test_public_harem_key_page_non_scan_preserves_null_page_metadata(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_harem_key_page(
        HaremKeyPage(
            page_number=None,
            page_count=None,
            entries=(HaremKeyEntry(name="Unscanned Character", key_type="bronze", key_count=0),),
        ),
        "Server",
        "Account",
        "unscanned harem-key page",
        "clipboard",
    )

    assert result.scan_id is None
    assert result.page_number is None
    assert result.page_count is None
    assert result.entries_imported == 1
    assert result.entries_linked == 0
    with connect(database_path) as connection:
        observation = connection.execute(
            """
            SELECT character_name, key_type, key_count, harem_scan_id, import_event_id
            FROM harem_key_observations
            """
        ).fetchone()
        scan_count = connection.execute("SELECT COUNT(*) FROM harem_scans").fetchone()[0]
        page_count = connection.execute("SELECT COUNT(*) FROM harem_scan_pages").fetchone()[0]

    assert tuple(observation) == (
        "Unscanned Character",
        "bronze",
        0,
        None,
        result.import_event_id,
    )
    assert scan_count == 0
    assert page_count == 0


def test_public_harem_key_page_late_failure_restores_rows_and_recovers(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    seeded_at = "2026-07-01T00:00:00+00:00"
    with connect(database_path) as connection:
        server_id = connection.execute(
            """
            INSERT INTO server_contexts (name, normalized_name, created_at, updated_at)
            VALUES ('Original Server Display', 'server', ?, ?)
            """,
            (seeded_at, seeded_at),
        ).lastrowid
        account_id = connection.execute(
            """
            INSERT INTO account_contexts (
                server_context_id, name, normalized_name, created_at, updated_at
            ) VALUES (?, 'Original Account Display', 'account', ?, ?)
            """,
            (server_id, seeded_at, seeded_at),
        ).lastrowid
        target_scan_id = connection.execute(
            """
            INSERT INTO harem_scans (
                account_context_id, expected_page_count, started_at, completed_at, scan_kind
            ) VALUES (?, NULL, ?, NULL, 'keys')
            """,
            (account_id, seeded_at),
        ).lastrowid
        unrelated_scan_id = connection.execute(
            """
            INSERT INTO harem_scans (
                account_context_id, expected_page_count, started_at, completed_at, scan_kind
            ) VALUES (?, 1, ?, NULL, 'keys')
            """,
            (account_id, seeded_at),
        ).lastrowid
        unrelated_event_id = connection.execute(
            """
            INSERT INTO import_events (kind, source, observed_at, raw_message)
            VALUES ('harem_key_page', 'seed', ?, 'unrelated page')
            """,
            (seeded_at,),
        ).lastrowid
        connection.execute(
            """
            INSERT INTO harem_scan_pages (harem_scan_id, page_number, import_event_id)
            VALUES (?, 1, ?)
            """,
            (unrelated_scan_id, unrelated_event_id),
        )
        before_context = tuple(
            connection.execute(
                """
                SELECT server_contexts.id, server_contexts.name,
                       server_contexts.normalized_name, server_contexts.created_at,
                       server_contexts.updated_at, account_contexts.id,
                       account_contexts.name, account_contexts.normalized_name,
                       account_contexts.created_at, account_contexts.updated_at
                FROM server_contexts
                JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
                WHERE account_contexts.id = ?
                """,
                (account_id,),
            ).fetchone()
        )
        before_target_scan = tuple(
            connection.execute(
                """
                SELECT id, account_context_id, expected_page_count, started_at,
                       completed_at, scan_kind
                FROM harem_scans WHERE id = ?
                """,
                (target_scan_id,),
            ).fetchone()
        )
        before_unrelated_page = tuple(
            connection.execute(
                """
                SELECT harem_scan_id, page_number, import_event_id
                FROM harem_scan_pages WHERE harem_scan_id = ?
                """,
                (unrelated_scan_id,),
            ).fetchone()
        )
        connection.execute(
            """
            CREATE TRIGGER fail_later_harem_key_observation
            BEFORE INSERT ON harem_key_observations
            WHEN NEW.normalized_character_name = 'later character'
            BEGIN
                SELECT RAISE(FAIL, 'forced later harem key failure');
            END
            """
        )

    page = HaremKeyPage(
        page_number=2,
        page_count=2,
        entries=(
            HaremKeyEntry(name="First Character", key_type="silver", key_count=5),
            HaremKeyEntry(name="Later Character", key_type="gold", key_count=7),
        ),
    )

    with pytest.raises(sqlite3.IntegrityError) as raised:
        catalog.import_harem_key_page(
            page,
            " SERVER ",
            " ACCOUNT ",
            "retryable harem-key page",
            "discord:test",
            target_scan_id,
        )
    assert str(raised.value) == "forced later harem key failure"

    with connect(database_path) as connection:
        after_context = tuple(
            connection.execute(
                """
                SELECT server_contexts.id, server_contexts.name,
                       server_contexts.normalized_name, server_contexts.created_at,
                       server_contexts.updated_at, account_contexts.id,
                       account_contexts.name, account_contexts.normalized_name,
                       account_contexts.created_at, account_contexts.updated_at
                FROM server_contexts
                JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
                WHERE account_contexts.id = ?
                """,
                (account_id,),
            ).fetchone()
        )
        after_target_scan = tuple(
            connection.execute(
                """
                SELECT id, account_context_id, expected_page_count, started_at,
                       completed_at, scan_kind
                FROM harem_scans WHERE id = ?
                """,
                (target_scan_id,),
            ).fetchone()
        )
        target_pages = connection.execute(
            "SELECT page_number FROM harem_scan_pages WHERE harem_scan_id = ?",
            (target_scan_id,),
        ).fetchall()
        after_unrelated_page = tuple(
            connection.execute(
                """
                SELECT harem_scan_id, page_number, import_event_id
                FROM harem_scan_pages WHERE harem_scan_id = ?
                """,
                (unrelated_scan_id,),
            ).fetchone()
        )
        failed_events = connection.execute(
            "SELECT id FROM import_events WHERE raw_message = 'retryable harem-key page'"
        ).fetchall()
        failed_observations = connection.execute(
            "SELECT id FROM harem_key_observations WHERE harem_scan_id = ?",
            (target_scan_id,),
        ).fetchall()

    assert after_context == before_context
    assert after_target_scan == before_target_scan
    assert target_pages == []
    assert after_unrelated_page == before_unrelated_page
    assert failed_events == []
    assert failed_observations == []

    with connect(database_path) as connection:
        connection.execute("DROP TRIGGER fail_later_harem_key_observation")

    recovered = catalog.import_harem_key_page(
        page,
        " SERVER ",
        " ACCOUNT ",
        "retryable harem-key page",
        "discord:test",
        target_scan_id,
    )

    with connect(database_path) as connection:
        recovered_events = connection.execute(
            "SELECT id FROM import_events WHERE raw_message = 'retryable harem-key page'"
        ).fetchall()
        recovered_observations = connection.execute(
            """
            SELECT character_name, import_event_id, harem_scan_id
            FROM harem_key_observations WHERE harem_scan_id = ? ORDER BY id
            """,
            (target_scan_id,),
        ).fetchall()
        recovered_scan = connection.execute(
            "SELECT expected_page_count, completed_at FROM harem_scans WHERE id = ?",
            (target_scan_id,),
        ).fetchone()
        recovered_pages = connection.execute(
            """
            SELECT page_number, import_event_id FROM harem_scan_pages
            WHERE harem_scan_id = ?
            """,
            (target_scan_id,),
        ).fetchall()
        durable_unrelated_page = tuple(
            connection.execute(
                """
                SELECT harem_scan_id, page_number, import_event_id
                FROM harem_scan_pages WHERE harem_scan_id = ?
                """,
                (unrelated_scan_id,),
            ).fetchone()
        )

    assert [row["id"] for row in recovered_events] == [recovered.import_event_id]
    assert [tuple(row) for row in recovered_observations] == [
        ("First Character", recovered.import_event_id, target_scan_id),
        ("Later Character", recovered.import_event_id, target_scan_id),
    ]
    assert tuple(recovered_scan) == (2, None)
    assert [tuple(row) for row in recovered_pages] == [(2, recovered.import_event_id)]
    assert durable_unrelated_page == before_unrelated_page


def test_public_harem_key_page_duplicate_rejection_rolls_back_attempt(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    scan = catalog.begin_harem_scan("Server", "Account", "keys")
    page = HaremKeyPage(
        page_number=1,
        page_count=2,
        entries=(HaremKeyEntry(name="Character", key_type="gold", key_count=7),),
    )
    imported = catalog.import_harem_key_page(
        page,
        "Server",
        "Account",
        "original harem-key page",
        "test",
        scan.id,
    )
    with connect(database_path) as connection:
        before_context = tuple(
            connection.execute(
                """
                SELECT server_contexts.id, server_contexts.name,
                       server_contexts.normalized_name, server_contexts.created_at,
                       server_contexts.updated_at, account_contexts.id,
                       account_contexts.name, account_contexts.normalized_name,
                       account_contexts.created_at, account_contexts.updated_at
                FROM server_contexts
                JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
                WHERE account_contexts.id = (
                    SELECT account_context_id FROM harem_scans WHERE id = ?
                )
                """,
                (scan.id,),
            ).fetchone()
        )
        before_scan = tuple(
            connection.execute(
                "SELECT expected_page_count, completed_at FROM harem_scans WHERE id = ?",
                (scan.id,),
            ).fetchone()
        )
        before_pages = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT page_number, import_event_id FROM harem_scan_pages
                WHERE harem_scan_id = ? ORDER BY page_number
                """,
                (scan.id,),
            ).fetchall()
        ]
        before_observations = connection.execute(
            "SELECT COUNT(*) FROM harem_key_observations WHERE harem_scan_id = ?",
            (scan.id,),
        ).fetchone()[0]

    with pytest.raises(ValueError) as raised:
        catalog.import_harem_key_page(
            page,
            " SERVER ",
            " ACCOUNT ",
            "duplicate harem-key page",
            "test",
            scan.id,
        )
    assert str(raised.value) == "This harem scan already contains that page."

    with connect(database_path) as connection:
        after_context = tuple(
            connection.execute(
                """
                SELECT server_contexts.id, server_contexts.name,
                       server_contexts.normalized_name, server_contexts.created_at,
                       server_contexts.updated_at, account_contexts.id,
                       account_contexts.name, account_contexts.normalized_name,
                       account_contexts.created_at, account_contexts.updated_at
                FROM server_contexts
                JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
                WHERE account_contexts.id = (
                    SELECT account_context_id FROM harem_scans WHERE id = ?
                )
                """,
                (scan.id,),
            ).fetchone()
        )
        after_scan = tuple(
            connection.execute(
                "SELECT expected_page_count, completed_at FROM harem_scans WHERE id = ?",
                (scan.id,),
            ).fetchone()
        )
        after_pages = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT page_number, import_event_id FROM harem_scan_pages
                WHERE harem_scan_id = ? ORDER BY page_number
                """,
                (scan.id,),
            ).fetchall()
        ]
        after_observations = connection.execute(
            "SELECT COUNT(*) FROM harem_key_observations WHERE harem_scan_id = ?",
            (scan.id,),
        ).fetchone()[0]
        duplicate_events = connection.execute(
            "SELECT id FROM import_events WHERE raw_message = 'duplicate harem-key page'"
        ).fetchall()

    assert after_context == before_context
    assert after_scan == before_scan == (2, None)
    assert after_pages == before_pages == [(1, imported.import_event_id)]
    assert after_observations == before_observations == 1
    assert duplicate_events == []


def test_public_ranked_harem_page_import_persists_complete_scanned_page(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    scan = catalog.begin_harem_scan("Server", "Account", "owned")
    seeded_at = "2026-07-01T00:00:00+00:00"
    with connect(database_path) as connection:
        resolved_character_id = connection.execute(
            """
            INSERT INTO characters (
                name, series, normalized_name, normalized_series, gender, roulette,
                created_at, updated_at
            ) VALUES ('Resolved Character', 'Resolved Series', 'resolved character',
                      'resolved series', 'female', 'wa', ?, ?)
            """,
            (seeded_at, seeded_at),
        ).lastrowid
        connection.execute(
            """
            INSERT INTO characters (
                name, series, normalized_name, normalized_series, gender, roulette,
                created_at, updated_at
            ) VALUES ('Ambiguous Character', 'First Series', 'ambiguous character',
                      'first series', NULL, NULL, ?, ?)
            """,
            (seeded_at, seeded_at),
        )
        connection.execute(
            """
            INSERT INTO characters (
                name, series, normalized_name, normalized_series, gender, roulette,
                created_at, updated_at
            ) VALUES ('Ambiguous Character', 'Second Series', 'ambiguous character',
                      'second series', NULL, NULL, ?, ?)
            """,
            (seeded_at, seeded_at),
        )

    original_runner = harem_repository_module.run_write_transaction
    original_prepare = catalog._harem_repository._prepare_harem_scan_page
    callback_calls: list[tuple[int, bool]] = []
    prepare_calls: list[tuple[int, bool, str]] = []

    def observed_runner(database_path, callback):
        def observed_callback(connection: sqlite3.Connection):
            callback_calls.append((id(connection), connection.in_transaction))
            return callback(connection)

        return original_runner(database_path, observed_callback)

    def observed_prepare(
        connection: sqlite3.Connection,
        scan_id: int,
        account_id: int,
        page: RankedHaremPage,
        scan_kind: str,
    ) -> None:
        prepare_calls.append((id(connection), connection.in_transaction, scan_kind))
        original_prepare(connection, scan_id, account_id, page, scan_kind)

    def unexpected_connection():
        raise AssertionError("ranked-Harem page import opened an independent connection")

    monkeypatch.setattr(harem_repository_module, "run_write_transaction", observed_runner)
    monkeypatch.setattr(
        catalog._harem_repository, "_prepare_harem_scan_page", observed_prepare
    )
    monkeypatch.setattr(catalog._harem_repository, "_connection", unexpected_connection)

    result = catalog.import_ranked_harem_page(
        RankedHaremPage(
            page_number=2,
            page_count=3,
            entries=(
                RankedHaremEntry(
                    name="RESOLVED CHARACTER",
                    claim_rank=2,
                    kakera_value=1_453,
                    roulette_types=("wa",),
                    key_type="gold",
                    key_count=7,
                ),
                RankedHaremEntry(
                    name="Ambiguous Character",
                    claim_rank=11,
                    kakera_value=None,
                    roulette_types=("ha", "hg"),
                ),
                RankedHaremEntry(
                    name="Missing Character",
                    claim_rank=57,
                    kakera_value=0,
                    key_type="bronze",
                    key_count=None,
                ),
            ),
        ),
        " Server ",
        " Account ",
        "complete ranked-Harem page",
        "discord:test",
        scan.id,
    )

    assert callback_calls == [(prepare_calls[0][0], True)]
    assert prepare_calls == [(callback_calls[0][0], True, "owned")]
    assert set(result.model_dump()) == {
        "import_event_id",
        "server_name",
        "account_name",
        "entries_imported",
        "entries_linked",
        "observed_at",
        "scan_id",
        "page_number",
        "page_count",
    }
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.entries_imported == 3
    assert result.entries_linked == 1
    assert result.scan_id == scan.id
    assert result.page_number == 2
    assert result.page_count == 3
    assert result.observed_at.tzinfo is not None
    assert result.observed_at.utcoffset().total_seconds() == 0
    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        context = connection.execute(
            """
            SELECT server_contexts.name, server_contexts.normalized_name,
                   account_contexts.name, account_contexts.normalized_name
            FROM harem_scans
            JOIN account_contexts ON account_contexts.id = harem_scans.account_context_id
            JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
            WHERE harem_scans.id = ?
            """,
            (scan.id,),
        ).fetchone()
        owned = connection.execute(
            """
            SELECT character_id, character_name, normalized_character_name,
                   claim_rank, kakera_value, roulette_types_json, observed_at,
                   import_event_id, harem_scan_id
            FROM owned_character_observations ORDER BY id
            """
        ).fetchall()
        keys = connection.execute(
            """
            SELECT character_id, character_name, normalized_character_name,
                   key_type, key_count, kakera_value, observed_at,
                   import_event_id, harem_scan_id
            FROM harem_key_observations ORDER BY id
            """
        ).fetchall()
        scan_row = connection.execute(
            "SELECT expected_page_count, completed_at FROM harem_scans WHERE id = ?",
            (scan.id,),
        ).fetchone()
        pages = connection.execute(
            """
            SELECT harem_scan_id, page_number, import_event_id
            FROM harem_scan_pages WHERE harem_scan_id = ?
            """,
            (scan.id,),
        ).fetchall()

    assert tuple(event) == (
        "ranked_harem_page",
        "discord:test",
        result.observed_at.isoformat(),
        "complete ranked-Harem page",
    )
    assert tuple(context) == ("Server", "server", "Account", "account")
    assert [tuple(row) for row in owned] == [
        (
            resolved_character_id,
            "RESOLVED CHARACTER",
            "resolved character",
            2,
            1_453,
            json.dumps(["wa"]),
            result.observed_at.isoformat(),
            result.import_event_id,
            scan.id,
        ),
        (
            None,
            "Ambiguous Character",
            "ambiguous character",
            11,
            None,
            json.dumps(["ha", "hg"]),
            result.observed_at.isoformat(),
            result.import_event_id,
            scan.id,
        ),
        (
            None,
            "Missing Character",
            "missing character",
            57,
            0,
            json.dumps([]),
            result.observed_at.isoformat(),
            result.import_event_id,
            scan.id,
        ),
    ]
    assert [tuple(row) for row in keys] == [
        (
            resolved_character_id,
            "RESOLVED CHARACTER",
            "resolved character",
            "gold",
            7,
            1_453,
            result.observed_at.isoformat(),
            result.import_event_id,
            None,
        )
    ]
    assert tuple(scan_row) == (3, None)
    assert [tuple(row) for row in pages] == [(scan.id, 2, result.import_event_id)]


def test_public_ranked_harem_page_non_scan_preserves_unscanned_evidence(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_ranked_harem_page(
        RankedHaremPage(
            page_number=None,
            page_count=None,
            entries=(
                RankedHaremEntry(
                    name="Unscanned Keyed Character",
                    claim_rank=9,
                    key_type="silver",
                    key_count=4,
                ),
                RankedHaremEntry(name="Unscanned Character", claim_rank=10),
            ),
        ),
        "Server",
        "Account",
        "unscanned ranked-Harem page",
        "clipboard",
    )

    assert result.scan_id is None
    assert result.page_number is None
    assert result.page_count is None
    assert result.entries_imported == 2
    with connect(database_path) as connection:
        owned = connection.execute(
            """
            SELECT character_name, claim_rank, harem_scan_id, import_event_id
            FROM owned_character_observations ORDER BY id
            """
        ).fetchall()
        keys = connection.execute(
            """
            SELECT character_name, key_type, key_count, harem_scan_id, import_event_id
            FROM harem_key_observations ORDER BY id
            """
        ).fetchall()
        scan_count = connection.execute("SELECT COUNT(*) FROM harem_scans").fetchone()[0]
        page_count = connection.execute("SELECT COUNT(*) FROM harem_scan_pages").fetchone()[0]

    assert [tuple(row) for row in owned] == [
        ("Unscanned Keyed Character", 9, None, result.import_event_id),
        ("Unscanned Character", 10, None, result.import_event_id),
    ]
    assert [tuple(row) for row in keys] == [
        ("Unscanned Keyed Character", "silver", 4, None, result.import_event_id)
    ]
    assert scan_count == 0
    assert page_count == 0


def test_public_ranked_harem_page_key_failure_restores_page_and_recovers(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    seeded_at = "2026-07-01T00:00:00+00:00"
    with connect(database_path) as connection:
        server_id = connection.execute(
            """
            INSERT INTO server_contexts (name, normalized_name, created_at, updated_at)
            VALUES ('Original Server Display', 'server', ?, ?)
            """,
            (seeded_at, seeded_at),
        ).lastrowid
        account_id = connection.execute(
            """
            INSERT INTO account_contexts (
                server_context_id, name, normalized_name, created_at, updated_at
            ) VALUES (?, 'Original Account Display', 'account', ?, ?)
            """,
            (server_id, seeded_at, seeded_at),
        ).lastrowid
        target_scan_id = connection.execute(
            """
            INSERT INTO harem_scans (
                account_context_id, expected_page_count, started_at, completed_at, scan_kind
            ) VALUES (?, NULL, ?, NULL, 'owned')
            """,
            (account_id, seeded_at),
        ).lastrowid
        unrelated_scan_id = connection.execute(
            """
            INSERT INTO harem_scans (
                account_context_id, expected_page_count, started_at, completed_at, scan_kind
            ) VALUES (?, 1, ?, NULL, 'owned')
            """,
            (account_id, seeded_at),
        ).lastrowid
        unrelated_event_id = connection.execute(
            """
            INSERT INTO import_events (kind, source, observed_at, raw_message)
            VALUES ('ranked_harem_page', 'seed', ?, 'unrelated ranked page')
            """,
            (seeded_at,),
        ).lastrowid
        connection.execute(
            """
            INSERT INTO harem_scan_pages (harem_scan_id, page_number, import_event_id)
            VALUES (?, 1, ?)
            """,
            (unrelated_scan_id, unrelated_event_id),
        )
        before_context = tuple(
            connection.execute(
                """
                SELECT server_contexts.id, server_contexts.name,
                       server_contexts.normalized_name, server_contexts.created_at,
                       server_contexts.updated_at, account_contexts.id,
                       account_contexts.name, account_contexts.normalized_name,
                       account_contexts.created_at, account_contexts.updated_at
                FROM server_contexts
                JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
                WHERE account_contexts.id = ?
                """,
                (account_id,),
            ).fetchone()
        )
        before_target_scan = tuple(
            connection.execute(
                """
                SELECT id, account_context_id, expected_page_count, started_at,
                       completed_at, scan_kind
                FROM harem_scans WHERE id = ?
                """,
                (target_scan_id,),
            ).fetchone()
        )
        before_unrelated_page = tuple(
            connection.execute(
                """
                SELECT harem_scan_id, page_number, import_event_id
                FROM harem_scan_pages WHERE harem_scan_id = ?
                """,
                (unrelated_scan_id,),
            ).fetchone()
        )
        connection.execute(
            """
            CREATE TRIGGER fail_later_ranked_key_observation
            BEFORE INSERT ON harem_key_observations
            WHEN NEW.normalized_character_name = 'later character'
            BEGIN
                SELECT RAISE(FAIL, 'forced later ranked key failure');
            END
            """
        )

    page = RankedHaremPage(
        page_number=2,
        page_count=2,
        entries=(
            RankedHaremEntry(
                name="First Character",
                claim_rank=1,
                key_type="silver",
                key_count=5,
            ),
            RankedHaremEntry(
                name="Later Character",
                claim_rank=2,
                key_type="gold",
                key_count=7,
            ),
        ),
    )

    with pytest.raises(sqlite3.IntegrityError) as raised:
        catalog.import_ranked_harem_page(
            page,
            " SERVER ",
            " ACCOUNT ",
            "retryable ranked-Harem page",
            "discord:test",
            target_scan_id,
        )
    assert str(raised.value) == "forced later ranked key failure"

    with connect(database_path) as connection:
        after_context = tuple(
            connection.execute(
                """
                SELECT server_contexts.id, server_contexts.name,
                       server_contexts.normalized_name, server_contexts.created_at,
                       server_contexts.updated_at, account_contexts.id,
                       account_contexts.name, account_contexts.normalized_name,
                       account_contexts.created_at, account_contexts.updated_at
                FROM server_contexts
                JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
                WHERE account_contexts.id = ?
                """,
                (account_id,),
            ).fetchone()
        )
        after_target_scan = tuple(
            connection.execute(
                """
                SELECT id, account_context_id, expected_page_count, started_at,
                       completed_at, scan_kind
                FROM harem_scans WHERE id = ?
                """,
                (target_scan_id,),
            ).fetchone()
        )
        target_pages = connection.execute(
            "SELECT page_number FROM harem_scan_pages WHERE harem_scan_id = ?",
            (target_scan_id,),
        ).fetchall()
        after_unrelated_page = tuple(
            connection.execute(
                """
                SELECT harem_scan_id, page_number, import_event_id
                FROM harem_scan_pages WHERE harem_scan_id = ?
                """,
                (unrelated_scan_id,),
            ).fetchone()
        )
        failed_events = connection.execute(
            "SELECT id FROM import_events WHERE raw_message = 'retryable ranked-Harem page'"
        ).fetchall()
        failed_owned = connection.execute(
            """
            SELECT id FROM owned_character_observations
            WHERE character_name IN ('First Character', 'Later Character')
            """
        ).fetchall()
        failed_keys = connection.execute(
            """
            SELECT id FROM harem_key_observations
            WHERE character_name IN ('First Character', 'Later Character')
            """
        ).fetchall()

    assert after_context == before_context
    assert after_target_scan == before_target_scan
    assert target_pages == []
    assert after_unrelated_page == before_unrelated_page
    assert failed_events == []
    assert failed_owned == []
    assert failed_keys == []

    with connect(database_path) as connection:
        connection.execute("DROP TRIGGER fail_later_ranked_key_observation")

    recovered = catalog.import_ranked_harem_page(
        page,
        " SERVER ",
        " ACCOUNT ",
        "retryable ranked-Harem page",
        "discord:test",
        target_scan_id,
    )

    with connect(database_path) as connection:
        recovered_events = connection.execute(
            "SELECT id FROM import_events WHERE raw_message = 'retryable ranked-Harem page'"
        ).fetchall()
        recovered_owned = connection.execute(
            """
            SELECT character_name, import_event_id, harem_scan_id
            FROM owned_character_observations WHERE import_event_id = ? ORDER BY id
            """,
            (recovered.import_event_id,),
        ).fetchall()
        recovered_keys = connection.execute(
            """
            SELECT character_name, import_event_id, harem_scan_id
            FROM harem_key_observations WHERE import_event_id = ? ORDER BY id
            """,
            (recovered.import_event_id,),
        ).fetchall()
        recovered_scan = connection.execute(
            "SELECT expected_page_count, completed_at FROM harem_scans WHERE id = ?",
            (target_scan_id,),
        ).fetchone()
        recovered_pages = connection.execute(
            """
            SELECT page_number, import_event_id FROM harem_scan_pages
            WHERE harem_scan_id = ?
            """,
            (target_scan_id,),
        ).fetchall()
        durable_unrelated_page = tuple(
            connection.execute(
                """
                SELECT harem_scan_id, page_number, import_event_id
                FROM harem_scan_pages WHERE harem_scan_id = ?
                """,
                (unrelated_scan_id,),
            ).fetchone()
        )

    assert [row["id"] for row in recovered_events] == [recovered.import_event_id]
    assert [tuple(row) for row in recovered_owned] == [
        ("First Character", recovered.import_event_id, target_scan_id),
        ("Later Character", recovered.import_event_id, target_scan_id),
    ]
    assert [tuple(row) for row in recovered_keys] == [
        ("First Character", recovered.import_event_id, None),
        ("Later Character", recovered.import_event_id, None),
    ]
    assert tuple(recovered_scan) == (2, None)
    assert [tuple(row) for row in recovered_pages] == [(2, recovered.import_event_id)]
    assert durable_unrelated_page == before_unrelated_page


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("duplicate", "This harem scan already contains that page."),
        ("wrong_kind", "This harem scan expects $mmy pages."),
        ("completed", "Harem scan is already complete; begin a new scan to refresh it."),
    ),
)
def test_public_ranked_harem_page_rejection_rolls_back_attempt(
    tmp_path, case: str, message: str
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    scan_kind = "keys" if case == "wrong_kind" else "owned"
    scan = catalog.begin_harem_scan("Server", "Account", scan_kind)
    page = RankedHaremPage(
        page_number=1,
        page_count=2,
        entries=(
            RankedHaremEntry(
                name="Rejected Character",
                claim_rank=1,
                key_type="gold",
                key_count=7,
            ),
        ),
    )
    if case == "duplicate":
        catalog.import_ranked_harem_page(
            page,
            "Server",
            "Account",
            "original ranked-Harem page",
            "test",
            scan.id,
        )
    elif case == "completed":
        completed_page = page.model_copy(update={"page_count": 1})
        catalog.import_ranked_harem_page(
            completed_page,
            "Server",
            "Account",
            "completed ranked-Harem page",
            "test",
            scan.id,
        )
        catalog.complete_harem_scan(scan.id)

    with connect(database_path) as connection:
        before_context = tuple(
            connection.execute(
                """
                SELECT server_contexts.id, server_contexts.name,
                       server_contexts.normalized_name, server_contexts.created_at,
                       server_contexts.updated_at, account_contexts.id,
                       account_contexts.name, account_contexts.normalized_name,
                       account_contexts.created_at, account_contexts.updated_at
                FROM server_contexts
                JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
                WHERE account_contexts.id = (
                    SELECT account_context_id FROM harem_scans WHERE id = ?
                )
                """,
                (scan.id,),
            ).fetchone()
        )
        before_scan = tuple(
            connection.execute(
                """
                SELECT account_context_id, expected_page_count, started_at,
                       completed_at, scan_kind
                FROM harem_scans WHERE id = ?
                """,
                (scan.id,),
            ).fetchone()
        )
        before_pages = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT page_number, import_event_id FROM harem_scan_pages
                WHERE harem_scan_id = ? ORDER BY page_number
                """,
                (scan.id,),
            ).fetchall()
        ]
        before_owned_count = connection.execute(
            "SELECT COUNT(*) FROM owned_character_observations"
        ).fetchone()[0]
        before_key_count = connection.execute(
            "SELECT COUNT(*) FROM harem_key_observations"
        ).fetchone()[0]

    attempt_raw = f"{case} rejected ranked-Harem page"
    with pytest.raises(ValueError) as raised:
        catalog.import_ranked_harem_page(
            page,
            " SERVER ",
            " ACCOUNT ",
            attempt_raw,
            "test",
            scan.id,
        )
    assert str(raised.value) == message

    with connect(database_path) as connection:
        after_context = tuple(
            connection.execute(
                """
                SELECT server_contexts.id, server_contexts.name,
                       server_contexts.normalized_name, server_contexts.created_at,
                       server_contexts.updated_at, account_contexts.id,
                       account_contexts.name, account_contexts.normalized_name,
                       account_contexts.created_at, account_contexts.updated_at
                FROM server_contexts
                JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
                WHERE account_contexts.id = (
                    SELECT account_context_id FROM harem_scans WHERE id = ?
                )
                """,
                (scan.id,),
            ).fetchone()
        )
        after_scan = tuple(
            connection.execute(
                """
                SELECT account_context_id, expected_page_count, started_at,
                       completed_at, scan_kind
                FROM harem_scans WHERE id = ?
                """,
                (scan.id,),
            ).fetchone()
        )
        after_pages = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT page_number, import_event_id FROM harem_scan_pages
                WHERE harem_scan_id = ? ORDER BY page_number
                """,
                (scan.id,),
            ).fetchall()
        ]
        after_owned_count = connection.execute(
            "SELECT COUNT(*) FROM owned_character_observations"
        ).fetchone()[0]
        after_key_count = connection.execute(
            "SELECT COUNT(*) FROM harem_key_observations"
        ).fetchone()[0]
        rejected_events = connection.execute(
            "SELECT id FROM import_events WHERE raw_message = ?", (attempt_raw,)
        ).fetchall()

    assert after_context == before_context
    assert after_scan == before_scan
    assert after_pages == before_pages
    assert after_owned_count == before_owned_count
    assert after_key_count == before_key_count
    assert rejected_events == []


def test_ranked_harem_page_waits_for_completion_then_rejects(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    scan = catalog.begin_harem_scan("Server", "Account", "owned")
    catalog.import_ranked_harem_page(
        RankedHaremPage(
            page_number=1,
            page_count=1,
            entries=(RankedHaremEntry(name="Initial Page", claim_rank=1),),
        ),
        "Server",
        "Account",
        "initial ranked-Harem page",
        "test",
        scan.id,
    )
    original_progress = catalog._harem_repository._harem_scan_progress_with_connection
    original_runner = harem_repository_module.run_write_transaction
    validation_finished = threading.Event()
    release_completion = threading.Event()
    writer_started = threading.Event()
    writer_entered_callback = threading.Event()
    completion_failures: list[BaseException] = []
    writer_failures: list[BaseException] = []
    progress_call_count = 0

    def pause_after_validation(connection: sqlite3.Connection, scan_id: int):
        nonlocal progress_call_count
        progress = original_progress(connection, scan_id)
        progress_call_count += 1
        if progress_call_count == 1:
            validation_finished.set()
            assert release_completion.wait(_THREAD_TIMEOUT), "completion was not released"
        return progress

    def observed_runner(database_path, callback):
        if threading.current_thread().name != "ranked-Harem-page-writer":
            return original_runner(database_path, callback)

        def observed_callback(connection: sqlite3.Connection):
            writer_entered_callback.set()
            return callback(connection)

        return original_runner(database_path, observed_callback)

    monkeypatch.setattr(
        catalog._harem_repository,
        "_harem_scan_progress_with_connection",
        pause_after_validation,
    )
    monkeypatch.setattr(harem_repository_module, "run_write_transaction", observed_runner)

    def complete_scan() -> None:
        try:
            catalog.complete_harem_scan(scan.id)
        except BaseException as exc:
            completion_failures.append(exc)

    completion_thread = threading.Thread(target=complete_scan)
    completion_thread.start()
    assert validation_finished.wait(_THREAD_TIMEOUT), "completion did not finish validation"

    def import_competing_page() -> None:
        writer_started.set()
        try:
            catalog.import_ranked_harem_page(
                RankedHaremPage(
                    page_number=2,
                    page_count=2,
                    entries=(
                        RankedHaremEntry(
                            name="Competing Page",
                            claim_rank=2,
                            key_type="silver",
                            key_count=5,
                        ),
                    ),
                ),
                "Server",
                "Account",
                "competing ranked-Harem page",
                "test",
                scan.id,
            )
        except BaseException as exc:
            writer_failures.append(exc)

    writer_thread = threading.Thread(
        target=import_competing_page,
        name="ranked-Harem-page-writer",
    )
    writer_thread.start()
    assert writer_started.wait(_THREAD_TIMEOUT), "competing page writer did not start"
    assert not writer_entered_callback.is_set()

    release_completion.set()
    completion_thread.join(timeout=_THREAD_TIMEOUT)
    writer_thread.join(timeout=_THREAD_TIMEOUT)

    assert not completion_thread.is_alive(), "completion worker did not terminate"
    assert not writer_thread.is_alive(), "page writer worker did not terminate"
    assert completion_failures == []
    assert writer_entered_callback.is_set()
    assert len(writer_failures) == 1
    assert isinstance(writer_failures[0], ValueError)
    assert str(writer_failures[0]) == (
        "Harem scan is already complete; begin a new scan to refresh it."
    )
    with connect(database_path) as connection:
        completed_at = connection.execute(
            "SELECT completed_at FROM harem_scans WHERE id = ?", (scan.id,)
        ).fetchone()[0]
        pages = connection.execute(
            "SELECT page_number FROM harem_scan_pages WHERE harem_scan_id = ? ORDER BY page_number",
            (scan.id,),
        ).fetchall()
        competing_events = connection.execute(
            "SELECT COUNT(*) FROM import_events WHERE raw_message = 'competing ranked-Harem page'"
        ).fetchone()[0]
        competing_owned = connection.execute(
            "SELECT COUNT(*) FROM owned_character_observations WHERE character_name = 'Competing Page'"
        ).fetchone()[0]
        competing_keys = connection.execute(
            "SELECT COUNT(*) FROM harem_key_observations WHERE character_name = 'Competing Page'"
        ).fetchone()[0]

    assert completed_at is not None
    assert [row["page_number"] for row in pages] == [1]
    assert competing_events == 0
    assert competing_owned == 0
    assert competing_keys == 0


def test_public_personal_rare_wrapper_persists_expected_atomic_result(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_personal_rare(
        PersonalRareSnapshot(personal_rare_multiplier=2),
        " Server ",
        " Account ",
        "personal rare payload",
        "discord:test",
    )

    assert result.import_event_id > 0
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.observed_at.tzinfo is not None
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT
                import_events.id AS import_event_id,
                import_events.kind,
                import_events.source,
                import_events.raw_message,
                import_events.observed_at AS event_observed_at,
                server_contexts.name AS server_name,
                account_contexts.name AS account_name,
                personal_rare_observations.personal_rare_multiplier,
                personal_rare_observations.observed_at AS personal_rare_observed_at,
                personal_rare_observations.import_event_id AS observation_import_event_id
            FROM personal_rare_observations
            JOIN import_events
              ON import_events.id = personal_rare_observations.import_event_id
            JOIN account_contexts
              ON account_contexts.id = personal_rare_observations.account_context_id
            JOIN server_contexts
              ON server_contexts.id = account_contexts.server_context_id
            """
        ).fetchone()
        assert connection.execute(
            "SELECT COUNT(*) FROM import_events WHERE kind = 'personal_rare'"
        ).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM personal_rare_observations"
        ).fetchone()[0] == 1

    assert row is not None
    assert row["import_event_id"] == result.import_event_id
    assert row["kind"] == "personal_rare"
    assert row["source"] == "discord:test"
    assert row["raw_message"] == "personal rare payload"
    assert row["event_observed_at"] == result.observed_at.isoformat()
    assert row["server_name"] == "Server"
    assert row["account_name"] == "Account"
    assert row["personal_rare_multiplier"] == 2
    assert row["personal_rare_observed_at"] == result.observed_at.isoformat()
    assert row["observation_import_event_id"] == result.import_event_id


def test_public_personal_rare_wrapper_rolls_back_contexts_and_remains_usable(
    tmp_path,
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    state = PersonalRareSnapshot(personal_rare_multiplier=2)
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_personal_rare
            AFTER INSERT ON personal_rare_observations
            BEGIN
                SELECT RAISE(FAIL, 'forced personal rare failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced personal rare failure"):
        catalog.import_personal_rare(
            state,
            "New Server",
            "New Account",
            "failed new-context payload",
            "discord:test",
        )

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM import_events WHERE kind = 'personal_rare'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM personal_rare_observations"
        ).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0
        connection.execute(
            """
            INSERT INTO server_contexts (name, normalized_name, created_at, updated_at)
            VALUES ('Original Server', 'original server', 'server created', 'server updated')
            """
        )
        server_id = connection.execute(
            "SELECT id FROM server_contexts WHERE normalized_name = 'original server'"
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO account_contexts (
                server_context_id, name, normalized_name, created_at, updated_at
            ) VALUES (
                ?, 'Original Account', 'original account', 'account created', 'account updated'
            )
            """,
            (server_id,),
        )
        before_server = connection.execute(
            "SELECT id, name, normalized_name, created_at, updated_at FROM server_contexts"
        ).fetchone()
        before_account = connection.execute(
            "SELECT id, name, normalized_name, created_at, updated_at FROM account_contexts"
        ).fetchone()

    with pytest.raises(sqlite3.IntegrityError, match="forced personal rare failure"):
        catalog.import_personal_rare(
            state,
            " ORIGINAL SERVER ",
            " ORIGINAL ACCOUNT ",
            "failed existing-context payload",
            "discord:test",
        )

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM import_events WHERE kind = 'personal_rare'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM personal_rare_observations"
        ).fetchone()[0] == 0
        after_server = connection.execute(
            "SELECT id, name, normalized_name, created_at, updated_at FROM server_contexts"
        ).fetchone()
        after_account = connection.execute(
            "SELECT id, name, normalized_name, created_at, updated_at FROM account_contexts"
        ).fetchone()
        assert tuple(after_server) == tuple(before_server)
        assert tuple(after_account) == tuple(before_account)
        connection.execute("DROP TRIGGER fail_personal_rare")

    result = catalog.import_personal_rare(
        state,
        "Original Server",
        "Original Account",
        "successful payload",
        "discord:test",
    )

    assert result.import_event_id > 0
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM import_events WHERE kind = 'personal_rare'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM personal_rare_observations"
        ).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 1


def test_public_divorce_wrapper_persists_expected_atomic_result(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    with connect(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO characters (
                name, series, normalized_name, normalized_series,
                gender, roulette, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Professor Layton",
                "Professor Layton",
                "professor layton",
                "professor layton",
                None,
                None,
                "created",
                "updated",
            ),
        )
        character_id = int(cursor.lastrowid)
    divorce = DivorceConfirmation(
        account_name="Account",
        character_name="Professor Layton",
        kakera_refund=54,
    )

    result = catalog.import_divorce(
        divorce,
        " Server ",
        " Account ",
        "divorce payload",
        "discord:test",
    )

    assert result.import_event_id > 0
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.character_name == "Professor Layton"
    assert result.character_id == character_id
    assert result.kakera_refund == 54
    assert result.observed_at.tzinfo is not None
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT
                import_events.id AS import_event_id,
                import_events.kind,
                import_events.source,
                import_events.raw_message,
                import_events.observed_at AS event_observed_at,
                server_contexts.name AS server_name,
                account_contexts.name AS account_name,
                divorce_observations.character_id,
                divorce_observations.character_name,
                divorce_observations.normalized_character_name,
                divorce_observations.kakera_refund,
                divorce_observations.observed_at AS divorce_observed_at,
                divorce_observations.import_event_id AS divorce_import_event_id
            FROM divorce_observations
            JOIN import_events
              ON import_events.id = divorce_observations.import_event_id
            JOIN account_contexts
              ON account_contexts.id = divorce_observations.account_context_id
            JOIN server_contexts
              ON server_contexts.id = account_contexts.server_context_id
            """
        ).fetchone()

    assert row is not None
    assert row["import_event_id"] == result.import_event_id
    assert row["kind"] == "divorce"
    assert row["source"] == "discord:test"
    assert row["raw_message"] == "divorce payload"
    assert row["event_observed_at"] == result.observed_at.isoformat()
    assert row["server_name"] == "Server"
    assert row["account_name"] == "Account"
    assert row["character_id"] == character_id
    assert row["character_name"] == "Professor Layton"
    assert row["normalized_character_name"] == "professor layton"
    assert row["kakera_refund"] == 54
    assert row["divorce_observed_at"] == result.observed_at.isoformat()
    assert row["divorce_import_event_id"] == result.import_event_id


def test_public_divorce_wrapper_rolls_back_contexts_and_remains_usable(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    divorce = DivorceConfirmation(
        account_name="Account",
        character_name="Professor Layton",
        kakera_refund=54,
    )
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_divorce
            BEFORE INSERT ON divorce_observations
            BEGIN
                SELECT RAISE(FAIL, 'forced divorce failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced divorce failure"):
        catalog.import_divorce(
            divorce,
            "New Server",
            "New Account",
            "failed new-context payload",
            "discord:test",
        )

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM import_events WHERE kind = 'divorce'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM divorce_observations"
        ).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0
        connection.execute(
            """
            INSERT INTO server_contexts (name, normalized_name, created_at, updated_at)
            VALUES ('Original Server', 'original server', 'server created', 'server updated')
            """
        )
        server_id = connection.execute(
            "SELECT id FROM server_contexts WHERE normalized_name = 'original server'"
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO account_contexts (
                server_context_id, name, normalized_name, created_at, updated_at
            ) VALUES (
                ?, 'Original Account', 'original account', 'account created', 'account updated'
            )
            """,
            (server_id,),
        )
        before_server = connection.execute(
            "SELECT id, name, normalized_name, created_at, updated_at FROM server_contexts"
        ).fetchone()
        before_account = connection.execute(
            "SELECT id, name, normalized_name, created_at, updated_at FROM account_contexts"
        ).fetchone()

    with pytest.raises(sqlite3.IntegrityError, match="forced divorce failure"):
        catalog.import_divorce(
            divorce,
            " ORIGINAL SERVER ",
            " ORIGINAL ACCOUNT ",
            "failed existing-context payload",
            "discord:test",
        )

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM import_events WHERE kind = 'divorce'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM divorce_observations"
        ).fetchone()[0] == 0
        after_server = connection.execute(
            "SELECT id, name, normalized_name, created_at, updated_at FROM server_contexts"
        ).fetchone()
        after_account = connection.execute(
            "SELECT id, name, normalized_name, created_at, updated_at FROM account_contexts"
        ).fetchone()
        assert tuple(after_server) == tuple(before_server)
        assert tuple(after_account) == tuple(before_account)
        connection.execute("DROP TRIGGER fail_divorce")

    result = catalog.import_divorce(
        divorce,
        "Original Server",
        "Original Account",
        "successful payload",
        "discord:test",
    )

    assert result.import_event_id > 0
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM import_events WHERE kind = 'divorce'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM divorce_observations"
        ).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 1


def test_public_kakera_reaction_wrapper_persists_expected_atomic_result(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    receipt = KakeraReactionReceipt(
        reaction_label=":kakeraY:", account_name="Account", kakera_earned=497
    )

    result = catalog.import_kakera_reaction(
        receipt, " Server ", "reaction payload", "discord:test"
    )

    assert result.import_event_id > 0
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.observed_at.tzinfo is not None
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT
                import_events.id AS import_event_id,
                import_events.kind,
                import_events.source,
                import_events.raw_message,
                import_events.observed_at AS event_observed_at,
                server_contexts.name AS server_name,
                account_contexts.name AS account_name,
                kakera_reaction_observations.reaction_label,
                kakera_reaction_observations.kakera_earned,
                kakera_reaction_observations.observed_at AS reaction_observed_at,
                kakera_reaction_observations.import_event_id AS reaction_import_event_id
            FROM kakera_reaction_observations
            JOIN import_events
              ON import_events.id = kakera_reaction_observations.import_event_id
            JOIN account_contexts
              ON account_contexts.id = kakera_reaction_observations.account_context_id
            JOIN server_contexts
              ON server_contexts.id = account_contexts.server_context_id
            """
        ).fetchone()

    assert row is not None
    assert row["import_event_id"] == result.import_event_id
    assert row["reaction_import_event_id"] == result.import_event_id
    assert row["kind"] == "kakera_reaction"
    assert row["source"] == "discord:test"
    assert row["raw_message"] == "reaction payload"
    assert row["server_name"] == "Server"
    assert row["account_name"] == "Account"
    assert row["reaction_label"] == ":kakeraY:"
    assert row["kakera_earned"] == 497
    assert datetime.fromisoformat(row["event_observed_at"]) == result.observed_at
    assert datetime.fromisoformat(row["reaction_observed_at"]) == result.observed_at


def test_public_kakera_reaction_wrapper_rolls_back_and_remains_usable(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    receipt = KakeraReactionReceipt(
        reaction_label=":kakeraG:", account_name="New Account", kakera_earned=524
    )
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_kakera_reaction
            BEFORE INSERT ON kakera_reaction_observations
            BEGIN
                SELECT RAISE(FAIL, 'forced kakera reaction failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced kakera reaction failure"):
        catalog.import_kakera_reaction(
            receipt, "New Server", "failed new-context payload", "discord:test"
        )

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM import_events WHERE kind = 'kakera_reaction'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM kakera_reaction_observations"
        ).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0
        connection.execute(
            """
            INSERT INTO server_contexts (name, normalized_name, created_at, updated_at)
            VALUES ('Original Server', 'original server', 'created', 'server original')
            """
        )
        server_id = connection.execute(
            "SELECT id FROM server_contexts WHERE normalized_name = 'original server'"
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO account_contexts (
                server_context_id, name, normalized_name, created_at, updated_at
            ) VALUES (?, 'Original Account', 'original account', 'created', 'account original')
            """,
            (server_id,),
        )

    existing_receipt = KakeraReactionReceipt(
        reaction_label=":kakeraP:", account_name="ORIGINAL ACCOUNT", kakera_earned=110
    )
    with pytest.raises(sqlite3.IntegrityError, match="forced kakera reaction failure"):
        catalog.import_kakera_reaction(
            existing_receipt,
            "ORIGINAL SERVER",
            "failed existing-context payload",
            "discord:test",
        )

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM import_events WHERE kind = 'kakera_reaction'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM kakera_reaction_observations"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT name, created_at, updated_at FROM server_contexts"
        ).fetchone()[:] == ("Original Server", "created", "server original")
        assert connection.execute(
            "SELECT name, created_at, updated_at FROM account_contexts"
        ).fetchone()[:] == ("Original Account", "created", "account original")
        connection.execute("DROP TRIGGER fail_kakera_reaction")

    result = catalog.import_kakera_reaction(
        receipt, "New Server", "successful payload", "discord:test"
    )

    assert result.import_event_id > 0
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM import_events WHERE kind = 'kakera_reaction'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM kakera_reaction_observations"
        ).fetchone()[0] == 1


def test_public_command_observation_wrapper_persists_one_expected_row(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_command_observation(
        " /$GiVeK ", "command observation payload", "discord:test"
    )

    assert result is None
    with connect(database_path) as connection:
        events = connection.execute(
            """
            SELECT kind, source, observed_at, raw_message
            FROM import_events
            """
        ).fetchall()
    assert len(events) == 1
    assert events[0]["kind"] == "command_observation"
    assert events[0]["source"] == "discord:test:command=$givek"
    assert datetime.fromisoformat(events[0]["observed_at"]).tzinfo is not None
    assert events[0]["raw_message"] == "command observation payload"


def test_public_command_observation_wrapper_rolls_back_and_remains_usable(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_command_observation
            AFTER INSERT ON import_events
            WHEN NEW.kind = 'command_observation'
            BEGIN
                SELECT RAISE(FAIL, 'forced command observation failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced command observation failure"):
        catalog.import_command_observation("givek", "failed payload", "discord:test")

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM import_events WHERE kind = 'command_observation'"
        ).fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_command_observation")

    result = catalog.import_command_observation(
        "givek", "successful payload", "discord:test"
    )

    assert result is None
    with connect(database_path) as connection:
        events = connection.execute(
            "SELECT source, raw_message FROM import_events WHERE kind = 'command_observation'"
        ).fetchall()
    assert [tuple(event) for event in events] == [
        ("discord:test:command=$givek", "successful payload")
    ]


def test_public_top_page_import_persists_complete_page_and_reuses_character(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    seeded_at = "2026-07-01T00:00:00+00:00"
    with connect(database_path) as connection:
        existing_character_id = connection.execute(
            """
            INSERT INTO characters (
                name, series, normalized_name, normalized_series, gender, roulette,
                created_at, updated_at
            ) VALUES (
                'Original Character', 'Original Series', 'existing character',
                'existing series', 'female', 'wa', ?, ?
            )
            """,
            (seeded_at, seeded_at),
        ).lastrowid

    page = TopPage(
        limit=1000,
        page_number=1,
        page_count=2,
        characters=(
            RankedCharacter(
                name="EXISTING CHARACTER",
                series="EXISTING SERIES",
                claim_rank=10,
                owner_name="Owner",
            ),
            RankedCharacter(
                name="New Character",
                series="New Series",
                claim_rank=20,
                owner_name=None,
            ),
        ),
    )

    result = catalog.import_top_page(
        page, "complete top payload", "clipboard", "  Server  "
    )

    assert set(result.model_dump()) == {
        "import_event_id",
        "characters_imported",
        "observed_at",
    }
    assert result.characters_imported == 2
    assert result.observed_at.tzinfo is not None
    assert result.observed_at.utcoffset().total_seconds() == 0
    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        server = connection.execute(
            "SELECT id, name, normalized_name FROM server_contexts"
        ).fetchone()
        characters = connection.execute(
            """
            SELECT id, name, series, normalized_name, normalized_series, gender, roulette
            FROM characters ORDER BY normalized_name
            """
        ).fetchall()
        ranks = connection.execute(
            """
            SELECT character_id, claim_rank, like_rank, owner_name, observed_at, import_event_id
            FROM rank_snapshots ORDER BY id
            """
        ).fetchall()
        owners = connection.execute(
            """
            SELECT server_context_id, character_id, owner_name, observed_at, import_event_id
            FROM top_owner_observations ORDER BY id
            """
        ).fetchall()

    assert tuple(event) == (
        "top_page",
        "clipboard",
        result.observed_at.isoformat(),
        "complete top payload",
    )
    assert tuple(server)[1:] == ("Server", "server")
    assert len(characters) == 2
    assert tuple(characters[0]) == (
        existing_character_id,
        "EXISTING CHARACTER",
        "EXISTING SERIES",
        "existing character",
        "existing series",
        "female",
        "wa",
    )
    assert tuple(characters[1])[1:] == (
        "New Character",
        "New Series",
        "new character",
        "new series",
        None,
        None,
    )
    assert [tuple(row) for row in ranks] == [
        (
            existing_character_id,
            10,
            None,
            "Owner",
            result.observed_at.isoformat(),
            result.import_event_id,
        ),
        (
            characters[1]["id"],
            20,
            None,
            None,
            result.observed_at.isoformat(),
            result.import_event_id,
        ),
    ]
    assert [tuple(row) for row in owners] == [
        (
            server["id"],
            existing_character_id,
            "Owner",
            result.observed_at.isoformat(),
            result.import_event_id,
        ),
        (
            server["id"],
            characters[1]["id"],
            None,
            result.observed_at.isoformat(),
            result.import_event_id,
        ),
    ]


def test_public_top_page_import_late_failure_removes_new_page_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    page = TopPage(
        limit=None,
        page_number=None,
        page_count=None,
        characters=(
            RankedCharacter(
                name="First Character",
                series="First Series",
                claim_rank=1,
                owner_name="Owner",
            ),
            RankedCharacter(
                name="Later Character",
                series="Later Series",
                claim_rank=2,
                owner_name=None,
            ),
        ),
    )
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_later_top_rank
            BEFORE INSERT ON rank_snapshots
            WHEN EXISTS (
                SELECT 1 FROM characters
                WHERE id = NEW.character_id AND normalized_name = 'later character'
            )
            BEGIN
                SELECT RAISE(FAIL, 'forced later top rank failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced later top rank failure"):
        catalog.import_top_page(page, "failed top payload", "discord", "New Server")

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM rank_snapshots").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM top_owner_observations"
        ).fetchone()[0] == 0


def test_public_top_page_import_restores_existing_rows_and_recovers(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    seeded_at = "2026-07-01T00:00:00+00:00"
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO server_contexts (name, normalized_name, created_at, updated_at)
            VALUES ('Original Server', 'server', ?, ?)
            """,
            (seeded_at, seeded_at),
        )
        connection.execute(
            """
            INSERT INTO characters (
                name, series, normalized_name, normalized_series, gender, roulette,
                created_at, updated_at
            ) VALUES (
                'Original Character', 'Original Series', 'existing character',
                'existing series', 'female', 'wa', ?, ?
            )
            """,
            (seeded_at, seeded_at),
        )
        before_server = tuple(
            connection.execute(
                """
                SELECT id, name, normalized_name, created_at, updated_at
                FROM server_contexts
                """
            ).fetchone()
        )
        before_character = tuple(
            connection.execute(
                """
                SELECT id, name, series, normalized_name, normalized_series, gender, roulette,
                       created_at, updated_at
                FROM characters
                """
            ).fetchone()
        )
        connection.execute(
            """
            CREATE TRIGGER fail_later_top_owner
            BEFORE INSERT ON top_owner_observations
            WHEN EXISTS (
                SELECT 1 FROM characters
                WHERE id = NEW.character_id AND normalized_name = 'later character'
            )
            BEGIN
                SELECT RAISE(FAIL, 'forced later top owner failure');
            END
            """
        )

    page = TopPage(
        limit=1000,
        page_number=1,
        page_count=1,
        characters=(
            RankedCharacter(
                name="EXISTING CHARACTER",
                series="EXISTING SERIES",
                claim_rank=10,
                owner_name="Owner",
            ),
            RankedCharacter(
                name="Later Character",
                series="Later Series",
                claim_rank=20,
                owner_name=None,
            ),
        ),
    )

    with pytest.raises(sqlite3.IntegrityError, match="forced later top owner failure"):
        catalog.import_top_page(page, "failed top payload", "discord", " SERVER ")

    with connect(database_path) as connection:
        current_server = tuple(
            connection.execute(
                """
                SELECT id, name, normalized_name, created_at, updated_at
                FROM server_contexts
                """
            ).fetchone()
        )
        current_character = tuple(
            connection.execute(
                """
                SELECT id, name, series, normalized_name, normalized_series, gender, roulette,
                       created_at, updated_at
                FROM characters
                """
            ).fetchone()
        )
        assert current_server == before_server
        assert current_character == before_character
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM rank_snapshots").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM top_owner_observations"
        ).fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_later_top_owner")

    result = catalog.import_top_page(
        page, "successful top payload", "discord", " SERVER "
    )

    assert result.characters_imported == 2
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM rank_snapshots").fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM top_owner_observations"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(DISTINCT import_event_id) FROM rank_snapshots"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(DISTINCT import_event_id) FROM top_owner_observations"
        ).fetchone()[0] == 1


def test_public_character_details_import_persists_complete_evidence(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    details = CharacterDetails(
        name="Character",
        series="Series",
        gender="female",
        roulette="animanga",
        kakera_value=321,
        claim_rank=12,
        like_rank=34,
        key_type="silver",
        key_count=5,
    )

    result = catalog.import_character_details(
        details, "  Server  ", "complete character payload", "clipboard", "  Account  "
    )

    assert set(result.model_dump()) == {
        "import_event_id",
        "character_id",
        "server_name",
        "observed_at",
    }
    assert result.server_name == "Server"
    assert result.observed_at.tzinfo is not None
    assert result.observed_at.utcoffset().total_seconds() == 0
    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        character = connection.execute(
            "SELECT id, name, series, normalized_name, normalized_series, gender, roulette "
            "FROM characters"
        ).fetchone()
        server = connection.execute(
            "SELECT id, name, normalized_name FROM server_contexts"
        ).fetchone()
        account = connection.execute(
            "SELECT id, server_context_id, name, normalized_name FROM account_contexts"
        ).fetchone()
        rank = connection.execute(
            "SELECT character_id, claim_rank, like_rank, observed_at, import_event_id "
            "FROM rank_snapshots"
        ).fetchone()
        server_observation = connection.execute(
            "SELECT server_context_id, character_id, kakera_value, observed_at, import_event_id "
            "FROM server_character_observations"
        ).fetchone()
        key = connection.execute(
            "SELECT account_context_id, character_id, character_name, "
            "normalized_character_name, key_type, key_count, kakera_value, observed_at, "
            "import_event_id FROM harem_key_observations"
        ).fetchone()

    assert tuple(event) == (
        "character_details",
        "clipboard",
        result.observed_at.isoformat(),
        "complete character payload",
    )
    assert tuple(character) == (
        result.character_id,
        "Character",
        "Series",
        "character",
        "series",
        "female",
        "animanga",
    )
    assert tuple(server)[1:] == ("Server", "server")
    assert tuple(account)[1:] == (server["id"], "Account", "account")
    assert tuple(rank) == (
        result.character_id,
        12,
        34,
        result.observed_at.isoformat(),
        result.import_event_id,
    )
    assert tuple(server_observation) == (
        server["id"],
        result.character_id,
        321,
        result.observed_at.isoformat(),
        result.import_event_id,
    )
    assert tuple(key) == (
        account["id"],
        result.character_id,
        "Character",
        "character",
        "silver",
        5,
        321,
        result.observed_at.isoformat(),
        result.import_event_id,
    )


def test_public_character_details_import_reuses_partial_character_and_skips_incomplete_evidence(
    tmp_path,
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    seeded_at = "2026-07-01T00:00:00+00:00"
    with connect(database_path) as connection:
        character_id = connection.execute(
            """
            INSERT INTO characters (
                name, series, normalized_name, normalized_series, gender, roulette,
                created_at, updated_at
            ) VALUES ('Original Name', 'Original Series', 'character', 'series',
                      'female', 'game', ?, ?)
            """,
            (seeded_at, seeded_at),
        ).lastrowid

    result = catalog.import_character_details(
        CharacterDetails(
            name="CHARACTER",
            series="SERIES",
            gender="male",
            roulette=None,
            kakera_value=None,
            claim_rank=None,
            like_rank=None,
            key_type="bronze",
            key_count=None,
        ),
        "Server",
        "partial character payload",
        "clipboard",
        "Account",
    )

    assert result.character_id == character_id
    with connect(database_path) as connection:
        character = connection.execute(
            """
            SELECT id, name, series, normalized_name, normalized_series, gender, roulette,
                   created_at, updated_at
            FROM characters
            """
        ).fetchone()
        server_observation = connection.execute(
            "SELECT character_id, kakera_value, import_event_id "
            "FROM server_character_observations"
        ).fetchone()
        assert connection.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM rank_snapshots").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM harem_key_observations"
        ).fetchone()[0] == 0

    assert tuple(character) == (
        character_id,
        "CHARACTER",
        "SERIES",
        "character",
        "series",
        "male",
        "game",
        seeded_at,
        result.observed_at.isoformat(),
    )
    assert tuple(server_observation) == (character_id, None, result.import_event_id)


def test_public_character_details_import_restores_existing_rows_and_recovers(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    seeded_at = "2026-07-01T00:00:00+00:00"
    with connect(database_path) as connection:
        server_id = connection.execute(
            """
            INSERT INTO server_contexts (name, normalized_name, created_at, updated_at)
            VALUES ('Original Server', 'server', ?, ?)
            """,
            (seeded_at, seeded_at),
        ).lastrowid
        connection.execute(
            """
            INSERT INTO account_contexts (
                server_context_id, name, normalized_name, created_at, updated_at
            ) VALUES (?, 'Original Account', 'account', ?, ?)
            """,
            (server_id, seeded_at, seeded_at),
        )
        connection.execute(
            """
            INSERT INTO characters (
                name, series, normalized_name, normalized_series, gender, roulette,
                created_at, updated_at
            ) VALUES ('Original Name', 'Original Series', 'character', 'series',
                      'female', 'game', ?, ?)
            """,
            (seeded_at, seeded_at),
        )
        before_character = tuple(
            connection.execute(
                "SELECT id, name, series, normalized_name, normalized_series, gender, roulette, "
                "created_at, updated_at FROM characters"
            ).fetchone()
        )
        before_server = tuple(
            connection.execute(
                "SELECT id, name, normalized_name, created_at, updated_at FROM server_contexts"
            ).fetchone()
        )
        before_account = tuple(
            connection.execute(
                "SELECT id, server_context_id, name, normalized_name, created_at, updated_at "
                "FROM account_contexts"
            ).fetchone()
        )
        connection.execute(
            """
            CREATE TRIGGER fail_character_details_key
            BEFORE INSERT ON harem_key_observations
            BEGIN
                SELECT RAISE(FAIL, 'forced character details key failure');
            END
            """
        )

    details = CharacterDetails(
        name="CHARACTER",
        series="SERIES",
        gender="male",
        roulette=None,
        kakera_value=444,
        claim_rank=7,
        like_rank=8,
        key_type="gold",
        key_count=9,
    )
    with pytest.raises(
        sqlite3.IntegrityError, match="forced character details key failure"
    ):
        catalog.import_character_details(
            details, " SERVER ", "failed character payload", "discord", " ACCOUNT "
        )

    with connect(database_path) as connection:
        current_character = tuple(
            connection.execute(
                "SELECT id, name, series, normalized_name, normalized_series, gender, roulette, "
                "created_at, updated_at FROM characters"
            ).fetchone()
        )
        current_server = tuple(
            connection.execute(
                "SELECT id, name, normalized_name, created_at, updated_at FROM server_contexts"
            ).fetchone()
        )
        current_account = tuple(
            connection.execute(
                "SELECT id, server_context_id, name, normalized_name, created_at, updated_at "
                "FROM account_contexts"
            ).fetchone()
        )
        assert current_character == before_character
        assert current_server == before_server
        assert current_account == before_account
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM rank_snapshots").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM server_character_observations"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM harem_key_observations"
        ).fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_character_details_key")

    result = catalog.import_character_details(
        details, " SERVER ", "successful character payload", "discord", " ACCOUNT "
    )

    assert result.character_id == before_character[0]
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM rank_snapshots").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM server_character_observations"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM harem_key_observations"
        ).fetchone()[0] == 1
        linked_event_ids = {
            connection.execute("SELECT import_event_id FROM rank_snapshots").fetchone()[0],
            connection.execute(
                "SELECT import_event_id FROM server_character_observations"
            ).fetchone()[0],
            connection.execute(
                "SELECT import_event_id FROM harem_key_observations"
            ).fetchone()[0],
        }
    assert linked_event_ids == {result.import_event_id}


def test_public_unavailable_character_import_persists_complete_page_and_reuses_character(
    tmp_path,
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    seeded_at = "2026-07-01T00:00:00+00:00"
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO characters (
                name, series, normalized_name, normalized_series, gender, roulette,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Existing Character",
                "Original Series",
                "existing character",
                "original series",
                "female",
                "wa",
                seeded_at,
                seeded_at,
            ),
        )
        existing_character_id = connection.execute(
            "SELECT id FROM characters WHERE normalized_name = 'existing character'"
        ).fetchone()["id"]

    page = UnavailableCharacterPage(
        limit=1000,
        page_number=1,
        page_count=2,
        characters=(
            UnavailableCharacter(
                name="EXISTING CHARACTER",
                series="ORIGINAL SERIES",
                claim_rank=10,
                reason=None,
            ),
            UnavailableCharacter(
                name="New Character",
                series="New Series",
                claim_rank=88,
                reason="$togglewestern",
            ),
        ),
    )

    result = catalog.import_unavailable_characters(
        page,
        "  Server  ",
        "  Account  ",
        "complete topx payload",
        "clipboard",
    )

    assert set(result.model_dump()) == {
        "import_event_id",
        "server_name",
        "account_name",
        "characters_imported",
        "observed_at",
    }
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.characters_imported == 2
    assert result.observed_at.tzinfo is not None
    assert result.observed_at.utcoffset().total_seconds() == 0
    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        context = connection.execute(
            """
            SELECT server_contexts.name AS server_name,
                   server_contexts.normalized_name AS normalized_server_name,
                   account_contexts.name AS account_name,
                   account_contexts.normalized_name AS normalized_account_name
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        characters = connection.execute(
            """
            SELECT id, name, series, normalized_name, normalized_series, gender, roulette
            FROM characters ORDER BY normalized_name
            """
        ).fetchall()
        ranks = connection.execute(
            """
            SELECT characters.name, rank_snapshots.character_id, rank_snapshots.claim_rank,
                   rank_snapshots.like_rank, rank_snapshots.observed_at,
                   rank_snapshots.import_event_id
            FROM rank_snapshots
            JOIN characters ON characters.id = rank_snapshots.character_id
            ORDER BY rank_snapshots.id
            """
        ).fetchall()
        unavailable = connection.execute(
            """
            SELECT characters.name, unavailable_character_observations.character_id,
                   unavailable_character_observations.reason,
                   unavailable_character_observations.observed_at,
                   unavailable_character_observations.import_event_id
            FROM unavailable_character_observations
            JOIN characters ON characters.id = unavailable_character_observations.character_id
            ORDER BY unavailable_character_observations.id
            """
        ).fetchall()

    assert tuple(event) == (
        "topx_page",
        "clipboard",
        result.observed_at.isoformat(),
        "complete topx payload",
    )
    assert tuple(context) == ("Server", "server", "Account", "account")
    assert len(characters) == 2
    assert tuple(characters[0]) == (
        existing_character_id,
        "EXISTING CHARACTER",
        "ORIGINAL SERIES",
        "existing character",
        "original series",
        "female",
        "wa",
    )
    assert tuple(characters[1])[1:] == (
        "New Character",
        "New Series",
        "new character",
        "new series",
        None,
        None,
    )
    assert [tuple(row) for row in ranks] == [
        (
            "EXISTING CHARACTER",
            existing_character_id,
            10,
            None,
            result.observed_at.isoformat(),
            result.import_event_id,
        ),
        (
            "New Character",
            characters[1]["id"],
            88,
            None,
            result.observed_at.isoformat(),
            result.import_event_id,
        ),
    ]
    assert [tuple(row) for row in unavailable] == [
        (
            "EXISTING CHARACTER",
            existing_character_id,
            None,
            result.observed_at.isoformat(),
            result.import_event_id,
        ),
        (
            "New Character",
            characters[1]["id"],
            "$togglewestern",
            result.observed_at.isoformat(),
            result.import_event_id,
        ),
    ]


def test_public_unavailable_character_import_late_failure_removes_new_page_rows(
    tmp_path,
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    page = UnavailableCharacterPage(
        limit=None,
        page_number=None,
        page_count=None,
        characters=(
            UnavailableCharacter(
                name="First Character", series="First Series", claim_rank=1, reason=None
            ),
            UnavailableCharacter(
                name="Later Character", series="Later Series", claim_rank=2, reason="$toggleirl"
            ),
        ),
    )
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_later_unavailable_character_rank
            BEFORE INSERT ON rank_snapshots
            WHEN EXISTS (
                SELECT 1 FROM characters
                WHERE id = NEW.character_id AND normalized_name = 'later character'
            )
            BEGIN
                SELECT RAISE(FAIL, 'forced later unavailable character failure');
            END
            """
        )

    with pytest.raises(
        sqlite3.IntegrityError, match="forced later unavailable character failure"
    ):
        catalog.import_unavailable_characters(
            page, "New Server", "New Account", "failed topx payload", "discord"
        )

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM rank_snapshots").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM unavailable_character_observations"
        ).fetchone()[0] == 0


def test_public_unavailable_character_import_restores_existing_rows_and_recovers(
    tmp_path,
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    seeded_at = "2026-07-01T00:00:00+00:00"
    with connect(database_path) as connection:
        server_id = connection.execute(
            """
            INSERT INTO server_contexts (name, normalized_name, created_at, updated_at)
            VALUES ('Original Server', 'server', ?, ?)
            """,
            (seeded_at, seeded_at),
        ).lastrowid
        connection.execute(
            """
            INSERT INTO account_contexts (
                server_context_id, name, normalized_name, created_at, updated_at
            ) VALUES (?, 'Original Account', 'account', ?, ?)
            """,
            (server_id, seeded_at, seeded_at),
        )
        connection.execute(
            """
            INSERT INTO characters (
                name, series, normalized_name, normalized_series, gender, roulette,
                created_at, updated_at
            ) VALUES (
                'Original Character', 'Original Series', 'existing character',
                'existing series', 'female', 'wa', ?, ?
            )
            """,
            (seeded_at, seeded_at),
        )
        before_context = tuple(
            connection.execute(
                """
                SELECT server_contexts.id, server_contexts.name, server_contexts.normalized_name,
                       server_contexts.created_at, server_contexts.updated_at,
                       account_contexts.id, account_contexts.name,
                       account_contexts.normalized_name, account_contexts.created_at,
                       account_contexts.updated_at
                FROM server_contexts
                JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
                """
            ).fetchone()
        )
        before_character = tuple(
            connection.execute(
                """
                SELECT id, name, series, normalized_name, normalized_series, gender, roulette,
                       created_at, updated_at
                FROM characters
                """
            ).fetchone()
        )
        connection.execute(
            """
            CREATE TRIGGER fail_later_unavailable_character_observation
            BEFORE INSERT ON unavailable_character_observations
            WHEN EXISTS (
                SELECT 1 FROM characters
                WHERE id = NEW.character_id AND normalized_name = 'later character'
            )
            BEGIN
                SELECT RAISE(FAIL, 'forced later unavailable observation failure');
            END
            """
        )

    page = UnavailableCharacterPage(
        limit=1000,
        page_number=1,
        page_count=1,
        characters=(
            UnavailableCharacter(
                name="EXISTING CHARACTER",
                series="EXISTING SERIES",
                claim_rank=10,
                reason=None,
            ),
            UnavailableCharacter(
                name="Later Character",
                series="Later Series",
                claim_rank=20,
                reason="$togglewestern",
            ),
        ),
    )

    with pytest.raises(
        sqlite3.IntegrityError, match="forced later unavailable observation failure"
    ):
        catalog.import_unavailable_characters(
            page, " SERVER ", " ACCOUNT ", "failed topx payload", "discord"
        )

    with connect(database_path) as connection:
        current_context = tuple(
            connection.execute(
                """
                SELECT server_contexts.id, server_contexts.name, server_contexts.normalized_name,
                       server_contexts.created_at, server_contexts.updated_at,
                       account_contexts.id, account_contexts.name,
                       account_contexts.normalized_name, account_contexts.created_at,
                       account_contexts.updated_at
                FROM server_contexts
                JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
                """
            ).fetchone()
        )
        current_character = tuple(
            connection.execute(
                """
                SELECT id, name, series, normalized_name, normalized_series, gender, roulette,
                       created_at, updated_at
                FROM characters
                """
            ).fetchone()
        )
        assert current_context == before_context
        assert current_character == before_character
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM rank_snapshots").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM unavailable_character_observations"
        ).fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_later_unavailable_character_observation")

    result = catalog.import_unavailable_characters(
        page, " SERVER ", " ACCOUNT ", "successful topx payload", "discord"
    )

    assert result.characters_imported == 2
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM rank_snapshots").fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM unavailable_character_observations"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(DISTINCT import_event_id) FROM rank_snapshots"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(DISTINCT import_event_id) FROM unavailable_character_observations"
        ).fetchone()[0] == 1


ROLL = RollObservation(
    name="Transaction Character",
    series="Transaction Series",
    claim_rank=7,
    kakera_value=12,
    displayed_key_type="gold",
    displayed_key_count=3,
)
CLAIM = ClaimConfirmation(account_name="Account", character_name="Transaction Character")
PROFILE = ProfileSnapshot(
    profile_name="profile-account",
    collection_size=35,
    female_percent=100,
    male_percent=0,
    pokedex_count=2,
    pokedex_pokemon=("gulpin", "piloswine"),
    kakera_reacts={":kakeraY:": 497},
    mudapins_collected=None,
    mudapins_total=None,
    kakera_balance=812,
    bronze_keys=3,
    silver_keys=0,
    gold_keys=0,
    sphere_stock=None,
    spheres={":spP:": 2},
    displayed_badges=(":silvmudae:", ":DiamondI:"),
)
SETTINGS = ServerSettingsSnapshot(
    server_premium=False,
    prefix="$",
    language="English",
    claim_reset_minutes=60,
    reset_minute="00",
    reset_shift_minutes=0,
    rolls_per_hour=10,
    claim_reaction_expiry_seconds=30,
    claimed_character_rarity_multiplier=2,
    kakera_bonus_percent=15,
    sphere_bonus_percent=5,
    game_mode=1,
    channel_instance=2,
    metrics=(
        ServerSettingMetric(label="Prefix", value="$"),
        ServerSettingMetric(label="Lang", value="English"),
    ),
)
KAKERALOOT_SETTINGS = KakeralootSettingsSnapshot(
    loot_cost=500,
    quantity_quality_base_cost=2000,
    quantity_quality_level_increment=200,
)
TIMER_STATE = TimerStateSnapshot(
    can_claim_now=True,
    claim_reset_minutes=0,
    rolls_left=17,
    rolls_reset_minutes=42,
    rolls_reset_stock=0,
    vote_reset_minutes=None,
    daily_reset_minutes=613,
    daily_kakera_ready=False,
    rt_available=None,
    can_react_kakera_now=True,
    reaction_power_percent=72,
    kakera_button_power_cost_percent=36,
    soulmate_button_power_cost_percent=18,
    kakera_stock=12114,
    gold_key_stock_remaining=0,
    gold_key_reset_minutes=None,
    bku_reset_probability_percent=10,
    oh_remaining=3,
    oc_remaining=None,
    oq_remaining=1,
    oq_stored=0,
    ot_remaining=8,
    ouro_refill_minutes=918,
    rolls_reset_status="limited_timer",
    rolls_per_hour_limit=17,
    rt_reset_minutes=612,
)
KAKERALOOT_STATE = KakeralootStateSnapshot(
    status_note="guarded state",
    rolls_stacked=17,
    disable_wa_ha_reduction=102,
    disable_wg_hg_reduction=68,
    protected_wish_level=42,
    protected_wish_denominator=4_642,
    mudapins=22,
    rt_cooldown_reduction_hours=2,
    permanent_roll_bonus=1,
    star_branches=3,
    starwish_slots_from_branches=4,
    quantity_level=5,
    quality_level=6,
    usage_count=1_234,
    kakera_balance=7_673,
)
ZERO_KAKERALOOT_STATE = KakeralootStateSnapshot(
    status_note="",
    rolls_stacked=0,
    disable_wa_ha_reduction=0,
    disable_wg_hg_reduction=0,
    protected_wish_level=0,
    protected_wish_denominator=0,
    mudapins=0,
    rt_cooldown_reduction_hours=0,
    permanent_roll_bonus=0,
    star_branches=0,
    starwish_slots_from_branches=0,
    quantity_level=0,
    quality_level=0,
    usage_count=0,
    kakera_balance=0,
)
NULL_KAKERALOOT_STATE = KakeralootStateSnapshot(
    status_note=None,
    rolls_stacked=None,
    disable_wa_ha_reduction=None,
    disable_wg_hg_reduction=None,
    protected_wish_level=None,
    protected_wish_denominator=None,
    mudapins=None,
    rt_cooldown_reduction_hours=None,
    permanent_roll_bonus=None,
    star_branches=None,
    starwish_slots_from_branches=None,
    quantity_level=None,
    quality_level=None,
    usage_count=None,
    kakera_balance=None,
)
NO_KAKERALOOT_STATE = KakeralootStateSnapshot(
    has_kakeraloots=False,
    status_note="No Kakeraloots bought; Mudae did not report loot statistics.",
)
KAKERA_STATE = KakeraStateSnapshot(
    kakera_balance=7_673,
    badges=(
        BadgeLevel(badge_name="bronze", level=4, max_reached=True),
        BadgeLevel(badge_name="silver", level=3, max_reached=False),
        BadgeLevel(badge_name="gold", level=2, max_reached=True),
    ),
)
ZERO_KAKERA_STATE = KakeraStateSnapshot(kakera_balance=0, badges=())
PLAYER_BONUS = PlayerBonusSnapshot(
    metrics=(
        PlayerBonusMetric(label="Rolls per hour", detail="+9"),
        PlayerBonusMetric(label="Spawn bonus", detail="+210% ($k + $bw + slash)"),
    ),
    rolls_per_hour_bonus=9,
    wishlist_slot_bonus=8,
    wish_spawn_bonus_percent=210,
    starwish_spawn_bonus_percent=180,
    starwish_total_spawn_bonus_percent=390,
    starwish_slot_bonus=1,
    additional_wish_key_chance_percent=10,
    kakera_max_power_percent=25,
    kakera_button_power_cost_percent=12,
    starwish_kakera_button_bonus_percent=20,
    light_kakera_minimum=4,
    light_kakera_maximum=5,
)
BOUNDARY_PLAYER_BONUS = PlayerBonusSnapshot(
    metrics=(PlayerBonusMetric(label="", detail=""),),
    rolls_per_hour_bonus=0,
    wishlist_slot_bonus=None,
    wish_spawn_bonus_percent=-1,
    starwish_spawn_bonus_percent=0,
    starwish_total_spawn_bonus_percent=None,
    starwish_slot_bonus=0,
    additional_wish_key_chance_percent=None,
    kakera_max_power_percent=0,
    kakera_button_power_cost_percent=-2,
    starwish_kakera_button_bonus_percent=None,
    light_kakera_minimum=0,
    light_kakera_maximum=None,
)
TOWER_STATE = TowerStateSnapshot(
    current_level=2,
    completed_towers=3,
    next_level_cost=75_000,
    kakera_balance=7_673,
    built_perk_ids=(2, 7),
)
TOWER_STATE_WITHOUT_COMPLETED_TOWERS = TOWER_STATE.model_copy(update={"completed_towers": None})
MUDAPINS = MudapinSnapshot(pin_markers=(":pin139:", ":pin182:", ":logopin6:"))
SPHERE_RESULT = SphereResultSnapshot(
    clicks_available=2,
    click_window_minutes=60,
    purple_target=10,
    purple_total=8,
    gains=(
        SphereGain(sphere_type="purple", amount=3),
        SphereGain(sphere_type="blue", amount=4, is_free=True),
    ),
    total_gained=7,
    stock=None,
)
WISHLIST = WishlistSnapshot(
    wishlist_count=3,
    wishlist_capacity=13,
    starwish_count=2,
    starwish_capacity=2,
    entries=(
        WishlistEntry(
            name="Saber",
            is_starwish=False,
            is_owned_marker_present=True,
            kakera_marker_present=True,
        ),
        WishlistEntry(
            name="Emilia",
            is_starwish=True,
            is_owned_marker_present=False,
            kakera_marker_present=False,
        ),
        WishlistEntry(
            name="Saber",
            is_starwish=False,
            is_owned_marker_present=True,
            kakera_marker_present=True,
        ),
    ),
)
ANTIDISABLE_PAGE = AntidisablePage(
    page_number=1,
    page_count=2,
    slots_used=0,
    slots_capacity=0,
    antidisabled_character_count=2_614,
    series_names=("Series B", "Series A", "Series B"),
)
ANTIDISABLE_CONTINUATION_PAGE = AntidisablePage(
    page_number=2,
    page_count=2,
    slots_used=7,
    slots_capacity=9,
    antidisabled_character_count=None,
    series_names=("Series C", "Series A"),
)
EMPTY_WISHLIST = WishlistSnapshot(
    wishlist_count=0,
    wishlist_capacity=0,
    starwish_count=0,
    starwish_capacity=0,
    entries=(),
)
DISABLELIST = DisableListSnapshot(
    slots_used=13,
    slots_capacity=16,
    total_disabled=107_529,
    disabled_wa=41_247,
    disabled_ha=42_438,
    disabled_wg=20_996,
    disabled_hg=14_789,
    wa_pool_limit=40_861,
    ha_pool_limit=42_213,
    western_disabled=True,
    irl_disabled=False,
    entries=(
        DisableListEntry(name="Kadokawa Corporation", disabled_count=13_207),
        DisableListEntry(name="Webcomics", disabled_count=11_073),
        DisableListEntry(name="Kadokawa Corporation", disabled_count=13_207),
    ),
)
BOUNDARY_DISABLELIST = DisableListSnapshot(
    slots_used=0,
    slots_capacity=0,
    total_disabled=0,
    disabled_wa=0,
    disabled_ha=0,
    disabled_wg=0,
    disabled_hg=0,
    wa_pool_limit=0,
    ha_pool_limit=None,
    western_disabled=False,
    irl_disabled=False,
    entries=(),
)
OBSERVED_AT = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
FINISHED_AT = datetime(2026, 7, 21, 12, 1, tzinfo=timezone.utc)


def _repositories(tmp_path):
    database_path = tmp_path / "transaction-seams.db"
    return database_path, CatalogRepository(database_path), DiscordMessageRepository(database_path)


def _receive_and_begin(repository: DiscordMessageRepository):
    aggregate_key = MessageAggregateKey(SourcePlatform.DISCORD, "guild", "channel", "message")
    received = repository.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(aggregate_key, "payload-hash", "revision"),
        event_key="event",
        event_kind="message_create",
        raw_text="raw",
        payload_json='{"content":"raw"}',
        payload_capture_version="capture-1",
        source_observed_at=OBSERVED_AT,
        received_at=OBSERVED_AT,
    )
    attempt = repository.begin_processing_attempt(
        source_event_id=received.source_event_id,
        parser_version="parser-1",
        router_version="router-1",
        started_at=OBSERVED_AT,
    )
    return received.source_event_id, attempt.attempt_id


def _record_sphere_result_attribution(
    repository: DiscordMessageRepository, source_event_id: int
) -> None:
    repository.record_server_attribution(
        source_event_id,
        status="resolved",
        server_name="Server",
        recorded_at=OBSERVED_AT,
    )
    repository.record_account_attribution(
        source_event_id,
        status="resolved",
        server_name="Server",
        account_name="Account",
        recorded_at=OBSERVED_AT,
    )


def _record_wishlist_attribution(
    repository: DiscordMessageRepository, source_event_id: int
) -> None:
    repository.record_server_attribution(
        source_event_id,
        status="resolved",
        server_name="Server",
        recorded_at=OBSERVED_AT,
    )
    repository.record_account_attribution(
        source_event_id,
        status="resolved",
        server_name="Server",
        account_name="Account",
        recorded_at=OBSERVED_AT,
    )


def _record_server_attribution(
    repository: DiscordMessageRepository, source_event_id: int
) -> None:
    repository.record_server_attribution(
        source_event_id,
        status="resolved",
        server_name="Server",
        recorded_at=OBSERVED_AT,
    )


def _record_account_scoped_attribution(
    repository: DiscordMessageRepository, source_event_id: int
) -> None:
    _record_server_attribution(repository, source_event_id)
    repository.record_account_attribution(
        source_event_id,
        status="resolved",
        server_name="Server",
        account_name="Account",
        recorded_at=OBSERVED_AT,
    )


def _receive_request(repository: DiscordMessageRepository) -> MessageAggregateKey:
    aggregate_key = MessageAggregateKey(
        SourcePlatform.DISCORD,
        "guild-1",
        "channel-1",
        "request-message",
    )
    repository.receive_message(
        aggregate_key=aggregate_key,
        revision_key=MessageRevisionKey.versioned(
            aggregate_key, "request-payload-hash", "request-revision"
        ),
        event_key="request-event",
        event_kind="message_create",
        raw_text="request",
        payload_json='{"content":"request"}',
        payload_capture_version="capture-1",
        source_observed_at=OBSERVED_AT,
        received_at=OBSERVED_AT,
    )
    return aggregate_key


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "characters",
        "server_contexts",
        "account_contexts",
        "claim_observations",
        "profile_observations",
        "roll_observations",
        "harem_key_observations",
        "rank_snapshots",
        "server_character_observations",
        "discord_projection_links",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _settings_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "server_settings_observations",
        "discord_projection_links",
        "discord_source_event_server_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _profile_projection_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "profile_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def test_public_import_event_delete_uses_one_active_runner_connection(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    target = catalog.import_roll(
        ROLL, "Server", "Account", "target roll", "clipboard"
    )
    unrelated = catalog.import_roll(
        ROLL, "Server", "Account", "unrelated roll", "clipboard"
    )
    original_runner = catalog_repository_module.run_write_transaction
    original_helper = catalog._delete_import_event_from_connection
    callback_calls: list[tuple[int, bool]] = []
    existence_reads: list[tuple[int, bool]] = []
    helper_calls: list[tuple[int, bool]] = []

    def observed_runner(database_path, callback):
        def observed_callback(connection: sqlite3.Connection):
            callback_calls.append((id(connection), connection.in_transaction))

            def trace(statement: str) -> None:
                if "SELECT 1 FROM import_events" in statement:
                    existence_reads.append((id(connection), connection.in_transaction))

            connection.set_trace_callback(trace)
            try:
                return callback(connection)
            finally:
                connection.set_trace_callback(None)

        return original_runner(database_path, observed_callback)

    def observed_helper(connection: sqlite3.Connection, import_event_id: int) -> None:
        helper_calls.append((id(connection), connection.in_transaction))
        original_helper(connection, import_event_id)

    def unexpected_connection():
        raise AssertionError("public deletion opened an independent repository connection")

    monkeypatch.setattr(catalog_repository_module, "run_write_transaction", observed_runner)
    monkeypatch.setattr(catalog, "_delete_import_event_from_connection", observed_helper)
    monkeypatch.setattr(catalog, "_connection", unexpected_connection)

    assert catalog.delete_import_event(target.import_event_id) is True

    assert len(callback_calls) == len(existence_reads) == len(helper_calls) == 1
    assert callback_calls[0][0] == existence_reads[0][0] == helper_calls[0][0]
    assert callback_calls[0][1] is existence_reads[0][1] is helper_calls[0][1] is True
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM import_events WHERE id = ?", (target.import_event_id,)
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM roll_observations WHERE import_event_id = ?",
            (target.import_event_id,),
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM import_events WHERE id = ?", (unrelated.import_event_id,)
        ).fetchone() is not None
        assert connection.execute(
            "SELECT 1 FROM roll_observations WHERE import_event_id = ?",
            (unrelated.import_event_id,),
        ).fetchone() is not None


def test_public_import_event_delete_missing_runs_inside_runner_without_changes(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    existing = catalog.import_roll(
        ROLL, "Server", "Account", "existing roll", "clipboard"
    )
    original_runner = catalog_repository_module.run_write_transaction
    callback_states: list[bool] = []

    def observed_runner(database_path, callback):
        def observed_callback(connection: sqlite3.Connection):
            callback_states.append(connection.in_transaction)
            return callback(connection)

        return original_runner(database_path, observed_callback)

    monkeypatch.setattr(catalog_repository_module, "run_write_transaction", observed_runner)
    with connect(database_path) as connection:
        before = (
            tuple(connection.execute("SELECT * FROM import_events").fetchone()),
            tuple(connection.execute("SELECT * FROM roll_observations").fetchone()),
        )

    assert catalog.delete_import_event(existing.import_event_id + 10_000) is False

    assert callback_states == [True]
    with connect(database_path) as connection:
        after = (
            tuple(connection.execute("SELECT * FROM import_events").fetchone()),
            tuple(connection.execute("SELECT * FROM roll_observations").fetchone()),
        )
    assert after == before


def test_public_import_event_delete_durable_refusal_rolls_back_runner(tmp_path) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    coordinator = ProfileProjectionCoordinator(catalog, discord)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_account_scoped_attribution(discord, source_event_id)
    result = coordinator.coordinate_profile(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        profile=PROFILE.model_copy(update={"profile_name": "Account"}),
        server="Server",
        account="Account",
        raw="profile payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )
    with connect(database_path) as connection:
        before = (
            tuple(connection.execute("SELECT * FROM import_events").fetchone()),
            tuple(connection.execute("SELECT * FROM profile_observations").fetchone()),
            tuple(connection.execute("SELECT * FROM discord_source_events").fetchone()),
            tuple(connection.execute("SELECT * FROM discord_processing_attempts").fetchone()),
            tuple(connection.execute("SELECT * FROM discord_projection_links").fetchone()),
        )

    with pytest.raises(ImportEventDeletionBlockedError, match="durable/replayable"):
        catalog.delete_import_event(result.import_event_id)

    with connect(database_path) as connection:
        after = (
            tuple(connection.execute("SELECT * FROM import_events").fetchone()),
            tuple(connection.execute("SELECT * FROM profile_observations").fetchone()),
            tuple(connection.execute("SELECT * FROM discord_source_events").fetchone()),
            tuple(connection.execute("SELECT * FROM discord_processing_attempts").fetchone()),
            tuple(connection.execute("SELECT * FROM discord_projection_links").fetchone()),
        )
    assert after == before


def test_public_import_event_delete_failure_rolls_back_and_same_database_recovers(
    tmp_path,
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    target = catalog.import_roll(
        ROLL, "Server", "Account", "target roll", "clipboard"
    )
    unrelated = catalog.import_roll(
        ROLL, "Server", "Account", "unrelated roll", "clipboard"
    )
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_target_import_event_delete
            BEFORE DELETE ON import_events
            BEGIN
                SELECT RAISE(FAIL, 'forced public import event delete failure');
            END
            """
        )

    with pytest.raises(
        sqlite3.IntegrityError, match="forced public import event delete failure"
    ):
        catalog.delete_import_event(target.import_event_id)

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM roll_observations").fetchone()[0] == 2
        connection.execute("DROP TRIGGER fail_target_import_event_delete")

    assert catalog.delete_import_event(target.import_event_id) is True
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM import_events WHERE id = ?", (target.import_event_id,)
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM roll_observations WHERE import_event_id = ?",
            (target.import_event_id,),
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM import_events WHERE id = ?", (unrelated.import_event_id,)
        ).fetchone() is not None
        assert connection.execute(
            "SELECT 1 FROM roll_observations WHERE import_event_id = ?",
            (unrelated.import_event_id,),
        ).fetchone() is not None


def _kakeraloot_settings_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "kakeraloot_settings_observations",
        "discord_projection_links",
        "discord_source_event_server_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _timer_state_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "timer_state_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _kakeraloot_state_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "kakeraloot_state_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _mudapins_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "mudapin_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _sphere_result_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "sphere_result_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _player_bonus_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "player_bonus_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _wishlist_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "wishlist_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _disablelist_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "disablelist_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _antidisable_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "harem_scans",
        "harem_scan_pages",
        "antidisable_series_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _kakera_state_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "kakera_state_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _tower_state_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "import_events",
        "server_contexts",
        "account_contexts",
        "tower_state_observations",
        "discord_projection_links",
        "discord_source_events",
        "discord_source_event_server_attributions",
        "discord_source_event_account_attributions",
        "discord_processing_attempts",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def test_public_kakera_import_preserves_compatibility_and_stored_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_kakera_state(
        KAKERA_STATE,
        "  Server  ",
        "  Account  ",
        "kakera payload",
        "discord",
    )

    assert set(result.model_dump()) == {"import_event_id", "server_name", "account_name", "observed_at"}
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.observed_at.tzinfo is not None
    assert result.observed_at.utcoffset().total_seconds() == 0
    with connect(database_path) as connection:
        assert _kakera_state_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "kakera_state_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT id, account_context_id, kakera_balance, badges_json, observed_at, import_event_id
            FROM kakera_state_observations
            WHERE import_event_id = ?
            """,
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == (
            "kakera_state",
            "discord",
            result.observed_at.isoformat(),
            "kakera payload",
        )
        assert observation["id"] > 0
        assert observation["account_context_id"] > 0
        assert observation["kakera_balance"] == 7_673
        assert json.loads(observation["badges_json"]) == [badge.model_dump() for badge in KAKERA_STATE.badges]
        assert observation["observed_at"] == result.observed_at.isoformat()
        assert observation["import_event_id"] == result.import_event_id


def test_public_kakera_wrapper_runner_rolls_back_and_recovers(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    repository = catalog._kakera_state_repository
    original_helper = repository._import_kakera_state_with_connection

    def fail_after_write(connection: sqlite3.Connection, **kwargs):
        original_helper(connection, **kwargs)
        raise RuntimeError("forced Kakera State import failure")

    monkeypatch.setattr(
        repository,
        "_import_kakera_state_with_connection",
        fail_after_write,
    )

    with pytest.raises(RuntimeError, match="forced Kakera State import failure"):
        catalog.import_kakera_state(
            KAKERA_STATE,
            "Server",
            "Account",
            "failed payload",
            "discord",
        )

    with connect(database_path) as connection:
        assert _kakera_state_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "kakera_state_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }

    monkeypatch.setattr(
        repository,
        "_import_kakera_state_with_connection",
        original_helper,
    )
    result = catalog.import_kakera_state(
        KAKERA_STATE,
        "Server",
        "Account",
        "successful payload",
        "discord",
    )
    assert result.import_event_id > 0
    with connect(database_path) as connection:
        assert _kakera_state_counts(connection)["kakera_state_observations"] == 1


def test_kakera_helper_uses_supplied_connection_and_returns_actual_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_kakera_state_with_connection(
            connection,
            state=KAKERA_STATE,
            server=" Server ",
            account=" Account ",
            raw="raw kakera",
            source="clipboard",
            observed_at=OBSERVED_AT,
        )
        assert is_dataclass(imported)
        assert [field.name for field in fields(imported)] == [
            "import_event_id",
            "kakera_state_observation_id",
        ]
        assert imported.import_event_id > 0
        assert imported.kakera_state_observation_id > 0
        assert connection.in_transaction is True
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM kakera_state_observations").fetchone()[0] == 1
        observation = connection.execute(
            """
            SELECT kakera_state_observations.*, account_contexts.normalized_name AS account_name
            FROM kakera_state_observations
            JOIN account_contexts ON account_contexts.id = kakera_state_observations.account_context_id
            WHERE kakera_state_observations.id = ?
            """,
            (imported.kakera_state_observation_id,),
        ).fetchone()
        assert observation["account_name"] == "account"
        assert observation["kakera_balance"] == 7_673
        assert json.loads(observation["badges_json"]) == [badge.model_dump() for badge in KAKERA_STATE.badges]
        assert observation["observed_at"] == OBSERVED_AT.isoformat()
        assert observation["import_event_id"] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM kakera_state_observations").fetchone()[0] == 0
        connection.commit()

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT id FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()[0] == imported.import_event_id
        assert connection.execute(
            "SELECT id FROM kakera_state_observations WHERE id = ?",
            (imported.kakera_state_observation_id,),
        ).fetchone()[0] == imported.kakera_state_observation_id


def test_kakera_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_kakera_state_with_connection(
            connection,
            state=ZERO_KAKERA_STATE,
            server="Server",
            account="Account",
            raw="rollback kakera",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        connection.rollback()

    with connect(database_path) as connection:
        assert _kakera_state_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "kakera_state_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_kakera_helper_reuses_existing_contexts_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_kakera_state(ZERO_KAKERA_STATE, "Server", "Account", "existing", "discord")

    with connect(database_path) as connection:
        before = connection.execute(
            """
            SELECT server_contexts.id AS server_id, server_contexts.name AS server_name,
                   account_contexts.id AS account_id, account_contexts.name AS account_name
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        before_counts = _kakera_state_counts(connection)

    with connect(database_path) as connection:
        imported = catalog._import_kakera_state_with_connection(
            connection,
            state=KAKERA_STATE,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="reused contexts",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, server_contexts.name AS server_name,
                   account_contexts.id AS account_id, account_contexts.name AS account_name
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert (current["server_id"], current["account_id"]) == (before["server_id"], before["account_id"])
        assert (current["server_name"], current["account_name"]) == ("SERVER", "ACCOUNT")
        assert _kakera_state_counts(connection)["server_contexts"] == 1
        assert _kakera_state_counts(connection)["account_contexts"] == 1
        assert imported.import_event_id > 0
        assert imported.kakera_state_observation_id > 0
        connection.rollback()

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, server_contexts.name AS server_name,
                   account_contexts.id AS account_id, account_contexts.name AS account_name
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(before)
        assert _kakera_state_counts(connection) == before_counts


def test_public_tower_import_preserves_compatibility_and_stored_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_tower_state(
        TOWER_STATE,
        "  Server  ",
        "  Account  ",
        "tower payload",
        "discord",
    )

    assert set(result.model_dump()) == {"import_event_id", "server_name", "account_name", "observed_at"}
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    with connect(database_path) as connection:
        assert _tower_state_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "tower_state_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT id, account_context_id, current_level, completed_towers, next_level_cost,
                   kakera_balance, built_perk_ids_json, observed_at, import_event_id
            FROM tower_state_observations
            WHERE import_event_id = ?
            """,
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("tower_state", "discord", result.observed_at.isoformat(), "tower payload")
        assert observation["id"] > 0
        assert observation["account_context_id"] > 0
        assert observation["current_level"] == TOWER_STATE.current_level
        assert observation["completed_towers"] == TOWER_STATE.completed_towers
        assert observation["next_level_cost"] == TOWER_STATE.next_level_cost
        assert observation["kakera_balance"] == TOWER_STATE.kakera_balance
        assert json.loads(observation["built_perk_ids_json"]) == list(TOWER_STATE.built_perk_ids)
        assert observation["observed_at"] == result.observed_at.isoformat()
        assert observation["import_event_id"] == result.import_event_id


def test_public_tower_wrapper_runner_rolls_back_and_recovers(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    original_helper = catalog._import_tower_state_with_connection

    def fail_after_write(connection: sqlite3.Connection, **kwargs):
        original_helper(connection, **kwargs)
        raise RuntimeError("forced Tower State import failure")

    monkeypatch.setattr(
        catalog,
        "_import_tower_state_with_connection",
        fail_after_write,
    )

    with pytest.raises(RuntimeError, match="forced Tower State import failure"):
        catalog.import_tower_state(
            TOWER_STATE,
            "Server",
            "Account",
            "failed payload",
            "discord",
        )

    with connect(database_path) as connection:
        assert _tower_state_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "tower_state_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }

    monkeypatch.setattr(
        catalog,
        "_import_tower_state_with_connection",
        original_helper,
    )
    result = catalog.import_tower_state(
        TOWER_STATE,
        "Server",
        "Account",
        "successful payload",
        "discord",
    )
    assert result.import_event_id > 0
    with connect(database_path) as connection:
        assert _tower_state_counts(connection)["tower_state_observations"] == 1


def test_tower_helper_uses_supplied_connection_returns_actual_ids_and_commits(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_tower_state_with_connection(
            connection,
            state=TOWER_STATE,
            server=" Server ",
            account=" Account ",
            raw="raw tower",
            source="clipboard",
            observed_at=OBSERVED_AT,
        )
        assert is_dataclass(imported)
        assert [field.name for field in fields(imported)] == [
            "import_event_id",
            "tower_state_observation_id",
        ]
        assert imported.import_event_id > 0
        assert imported.tower_state_observation_id > 0
        assert connection.in_transaction is True
        assert connection.execute(
            "SELECT id FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()[0] == imported.import_event_id
        assert connection.execute(
            "SELECT id FROM tower_state_observations WHERE id = ?",
            (imported.tower_state_observation_id,),
        ).fetchone()[0] == imported.tower_state_observation_id
        assert _tower_state_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "tower_state_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        observation = connection.execute(
            "SELECT observed_at, import_event_id FROM tower_state_observations WHERE id = ?",
            (imported.tower_state_observation_id,),
        ).fetchone()
        assert observation["observed_at"] == OBSERVED_AT.isoformat()
        assert observation["import_event_id"] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM tower_state_observations").fetchone()[0] == 0
        connection.commit()

    with connect(database_path) as connection:
        assert _tower_state_counts(connection)["tower_state_observations"] == 1


def test_tower_helper_rollback_removes_new_rows_and_contexts(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_tower_state_with_connection(
            connection,
            state=TOWER_STATE,
            server="Server",
            account="Account",
            raw="rollback tower",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.rollback()

    with connect(database_path) as connection:
        assert _tower_state_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "tower_state_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_tower_helper_reuses_existing_contexts_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_tower_state(TOWER_STATE, "Server", "Account", "existing", "discord")

    with connect(database_path) as connection:
        before = connection.execute(
            """
            SELECT server_contexts.id AS server_id, server_contexts.name AS server_name,
                   account_contexts.id AS account_id, account_contexts.name AS account_name
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        before_counts = _tower_state_counts(connection)

    with connect(database_path) as connection:
        catalog._import_tower_state_with_connection(
            connection,
            state=TOWER_STATE,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="reused contexts",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, server_contexts.name AS server_name,
                   account_contexts.id AS account_id, account_contexts.name AS account_name
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert (current["server_id"], current["account_id"]) == (before["server_id"], before["account_id"])
        assert (current["server_name"], current["account_name"]) == ("SERVER", "ACCOUNT")
        assert _tower_state_counts(connection)["server_contexts"] == 1
        assert _tower_state_counts(connection)["account_contexts"] == 1
        connection.rollback()

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, server_contexts.name AS server_name,
                   account_contexts.id AS account_id, account_contexts.name AS account_name
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(before)
        assert _tower_state_counts(connection) == before_counts


@pytest.mark.parametrize(
    ("state", "expected_completed_towers"),
    [
        (TOWER_STATE, 3),
        (TOWER_STATE_WITHOUT_COMPLETED_TOWERS, 0),
    ],
)
def test_tower_helper_preserves_completed_tower_values(tmp_path, state, expected_completed_towers) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_tower_state_with_connection(
            connection,
            state=state,
            server="Server",
            account=f"Account {expected_completed_towers}",
            raw="completed towers",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.execute(
            "SELECT completed_towers FROM tower_state_observations WHERE id = ?",
            (imported.tower_state_observation_id,),
        ).fetchone()[0] == expected_completed_towers


def test_roll_helper_writes_all_projections_on_supplied_connection(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        result = catalog._import_roll_with_connection(
            connection,
            roll=ROLL,
            server="Server",
            account="Account",
            raw="roll payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert result.import_event_id > 0
        assert result.character_id > 0
        assert result.roll_observation_id > 0
        assert result.harem_key_observation_id > 0
        assert result.rank_snapshot_id > 0
        assert result.server_character_observation_id > 0

    with connect(database_path) as connection:
        counts = _counts(connection)
        assert counts == {
            "import_events": 1,
            "characters": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "claim_observations": 0,
            "profile_observations": 0,
            "roll_observations": 1,
            "harem_key_observations": 1,
            "rank_snapshots": 1,
            "server_character_observations": 1,
            "discord_projection_links": 0,
        }


def test_public_roll_wrapper_keeps_public_result_and_transaction_behavior(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_roll(ROLL, " Server ", " Account ", "roll payload", "discord")

    assert result.import_event_id > 0
    assert result.character_id > 0
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 1


def test_public_roll_wrapper_rolls_back_and_remains_usable(tmp_path, monkeypatch) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    original = catalog._import_roll_with_connection

    def fail_after_writes(connection, **kwargs):
        original(connection, **kwargs)
        raise RuntimeError("forced public roll failure")

    monkeypatch.setattr(catalog, "_import_roll_with_connection", fail_after_writes)
    with pytest.raises(RuntimeError, match="forced public roll failure"):
        catalog.import_roll(ROLL, "Server", "Account", "roll payload", "discord")

    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 0,
            "characters": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "claim_observations": 0,
            "profile_observations": 0,
            "roll_observations": 0,
            "harem_key_observations": 0,
            "rank_snapshots": 0,
            "server_character_observations": 0,
            "discord_projection_links": 0,
        }

    monkeypatch.setattr(catalog, "_import_roll_with_connection", original)
    result = catalog.import_roll(ROLL, "Server", "Account", "roll payload", "discord")

    assert result.character_id > 0
    with connect(database_path) as connection:
        counts = _counts(connection)
        assert counts["import_events"] == 1
        assert counts["roll_observations"] == 1
        assert counts["harem_key_observations"] == 1
        assert counts["rank_snapshots"] == 1
        assert counts["server_character_observations"] == 1


def test_public_claim_wrapper_keeps_result_and_writes_expected_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_claim(CLAIM, " Server ", " Account ", "claim payload", "discord")

    assert result.import_event_id > 0
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.character_name == "Transaction Character"
    assert result.character_id is None
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 1
        assert _counts(connection)["server_contexts"] == 1
        assert _counts(connection)["account_contexts"] == 1
        assert _counts(connection)["claim_observations"] == 1
        assert _counts(connection)["discord_projection_links"] == 0
        event = connection.execute(
            "SELECT kind, source, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("claim", "discord", "claim payload")
        observation = connection.execute(
            """
            SELECT id, character_id, character_name, normalized_character_name, import_event_id
            FROM claim_observations
            """
        ).fetchone()
        assert tuple(observation) == (
            observation["id"],
            None,
            "Transaction Character",
            "transaction character",
            result.import_event_id,
        )


def test_public_claim_wrapper_rolls_back_and_remains_usable(tmp_path, monkeypatch) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    original = catalog._import_claim_with_connection

    def fail_after_writes(connection, **kwargs):
        original(connection, **kwargs)
        raise RuntimeError("forced public claim failure")

    monkeypatch.setattr(catalog, "_import_claim_with_connection", fail_after_writes)
    with pytest.raises(RuntimeError, match="forced public claim failure"):
        catalog.import_claim(CLAIM, "Server", "Account", "claim payload", "discord")

    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 0
        assert _counts(connection)["server_contexts"] == 0
        assert _counts(connection)["account_contexts"] == 0
        assert _counts(connection)["claim_observations"] == 0

    monkeypatch.setattr(catalog, "_import_claim_with_connection", original)
    result = catalog.import_claim(CLAIM, "Server", "Account", "claim payload", "discord")

    assert result.character_id is None
    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 1
        assert _counts(connection)["claim_observations"] == 1


def test_claim_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_claim_with_connection(
            connection,
            claim=CLAIM,
            server="Server",
            account="Account",
            raw="claim payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM claim_observations").fetchone()[0] == 1
        assert connection.execute(
            "SELECT import_event_id FROM claim_observations WHERE id = ?",
            (imported.claim_observation_id,),
        ).fetchone()[0] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM claim_observations").fetchone()[0] == 0


def test_claim_helper_commit_persists_all_rows_and_result_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_claim_with_connection(
            connection,
            claim=CLAIM,
            server="Server",
            account="Account",
            raw="claim payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT id FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()[0] == imported.import_event_id
        assert connection.execute(
            "SELECT id FROM claim_observations WHERE id = ?", (imported.claim_observation_id,)
        ).fetchone()[0] == imported.claim_observation_id
        assert connection.execute(
            "SELECT import_event_id FROM claim_observations WHERE id = ?",
            (imported.claim_observation_id,),
        ).fetchone()[0] == imported.import_event_id
        assert _counts(connection)["claim_observations"] == 1
        assert _counts(connection)["discord_projection_links"] == 0


def test_claim_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_claim_with_connection(
                connection,
                claim=CLAIM,
                server="Server",
                account="Account",
                raw="claim payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        assert _counts(connection)["import_events"] == 0
        assert _counts(connection)["claim_observations"] == 0
        assert _counts(connection)["server_contexts"] == 0
        assert _counts(connection)["account_contexts"] == 0
        assert _counts(connection)["characters"] == 0
        assert _counts(connection)["discord_projection_links"] == 0


def test_claim_helper_reuses_canonical_rows_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    roll_result = catalog.import_roll(ROLL, "Server", "Account", "roll payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id,
                   characters.id AS character_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            JOIN characters ON characters.normalized_name = ?
            """,
            ("transaction character",),
        ).fetchone()
        before_counts = _counts(connection)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            imported = catalog._import_claim_with_connection(
                connection,
                claim=CLAIM,
                server=" SERVER ",
                account=" ACCOUNT ",
                raw="claim payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            assert imported.character_id == roll_result.character_id
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id,
                   characters.id AS character_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            JOIN characters ON characters.normalized_name = ?
            """,
            ("transaction character",),
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _counts(connection) == before_counts


def test_public_profile_wrapper_keeps_result_and_writes_expected_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_profile(PROFILE, " Server ", " Account ", "profile payload", "discord")

    assert result.import_event_id > 0
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 1,
            "characters": 0,
            "server_contexts": 1,
            "account_contexts": 1,
            "claim_observations": 0,
            "profile_observations": 1,
            "roll_observations": 0,
            "harem_key_observations": 0,
            "rank_snapshots": 0,
            "server_character_observations": 0,
            "discord_projection_links": 0,
        }
        event = connection.execute(
            "SELECT kind, source, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("profile", "discord", "profile payload")
        row = connection.execute(
            """
            SELECT id, profile_name, collection_size, pokedex_json, kakera_reacts_json,
                   displayed_badges_json, import_event_id
            FROM profile_observations
            """
        ).fetchone()
        assert row["id"] > 0
        assert (row["profile_name"], row["collection_size"], row["import_event_id"]) == (
            "profile-account",
            35,
            result.import_event_id,
        )
        assert row["pokedex_json"] == '["gulpin", "piloswine"]'
        assert row["kakera_reacts_json"] == '{":kakeraY:": 497}'
        assert row["displayed_badges_json"] == '[":silvmudae:", ":DiamondI:"]'


def test_public_server_settings_wrapper_preserves_result_rows_and_metrics(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_server_settings(SETTINGS, " Server ", "settings payload", "discord")

    assert result.import_event_id > 0
    assert result.server_name == "Server"
    with connect(database_path) as connection:
        assert _settings_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "server_settings_observations": 1,
            "discord_projection_links": 0,
            "discord_source_event_server_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("server_settings", "discord", "settings payload")
        observation = connection.execute(
            """
            SELECT id, server_premium, prefix, language, claim_reset_minutes, reset_minute,
                   reset_shift_minutes, rolls_per_hour, claim_reaction_expiry_seconds,
                   claimed_character_rarity_multiplier, kakera_bonus_percent, sphere_bonus_percent,
                   game_mode, channel_instance, metrics_json, import_event_id
            FROM server_settings_observations
            """
        ).fetchone()
        assert observation["id"] > 0
        assert tuple(observation)[1:14] == (
            0,
            "$",
            "English",
            60,
            "00",
            0,
            10,
            30,
            2,
            15,
            5,
            1,
            2,
        )
        assert observation["metrics_json"] == '[{"label": "Prefix", "value": "$"}, {"label": "Lang", "value": "English"}]'
        assert observation["import_event_id"] == result.import_event_id


def test_server_settings_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_server_settings_with_connection(
            connection,
            settings=SETTINGS,
            server="Server",
            raw="settings payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM server_settings_observations").fetchone()[0] == 1
        assert connection.execute(
            "SELECT import_event_id FROM server_settings_observations WHERE id = ?",
            (imported.server_settings_observation_id,),
        ).fetchone()[0] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM server_settings_observations").fetchone()[0] == 0


def test_server_settings_helper_commit_persists_rows_and_result_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_server_settings_with_connection(
            connection,
            settings=SETTINGS,
            server="Server",
            raw="settings payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT id FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()[0] == imported.import_event_id
        assert connection.execute(
            "SELECT id FROM server_settings_observations WHERE id = ?",
            (imported.server_settings_observation_id,),
        ).fetchone()[0] == imported.server_settings_observation_id
        assert _settings_counts(connection)["server_settings_observations"] == 1


def test_server_settings_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_server_settings_with_connection(
                connection,
                settings=SETTINGS,
                server="Server",
                raw="settings payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        assert _settings_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "server_settings_observations": 0,
            "discord_projection_links": 0,
            "discord_source_event_server_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_server_settings_helper_reuses_server_and_rollback_preserves_it(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_server_settings(SETTINGS, "Server", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            "SELECT id, name, normalized_name FROM server_contexts"
        ).fetchone()
        before_counts = _settings_counts(connection)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_server_settings_with_connection(
                connection,
                settings=SETTINGS,
                server=" SERVER ",
                raw="second payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        current = connection.execute(
            "SELECT id, name, normalized_name FROM server_contexts"
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _settings_counts(connection) == before_counts


def test_server_settings_helper_does_not_write_projection_or_attribution_state(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        before = _settings_counts(connection)
        catalog._import_server_settings_with_connection(
            connection,
            settings=SETTINGS,
            server="Server",
            raw="settings payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        after = _settings_counts(connection)

    assert after["import_events"] == before["import_events"] + 1
    assert after["server_contexts"] == before["server_contexts"] + 1
    assert after["server_settings_observations"] == before["server_settings_observations"] + 1
    assert after["discord_projection_links"] == before["discord_projection_links"]
    assert after["discord_source_event_server_attributions"] == before["discord_source_event_server_attributions"]
    assert after["discord_processing_attempts"] == before["discord_processing_attempts"]


def test_public_kakeraloot_settings_wrapper_preserves_result_rows_and_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_kakeraloot_settings(
        KAKERALOOT_SETTINGS, " Server ", "infokl payload", "discord"
    )

    assert result.import_event_id > 0
    assert result.server_name == "Server"
    settings = catalog.kakeraloot_settings("Server")
    assert settings is not None
    assert (settings.loot_cost, settings.quantity_quality_base_cost, settings.quantity_quality_level_increment) == (
        500,
        2000,
        200,
    )
    with connect(database_path) as connection:
        assert _kakeraloot_settings_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "kakeraloot_settings_observations": 1,
            "discord_projection_links": 0,
            "discord_source_event_server_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("kakeraloot_settings", "discord", "infokl payload")
        observation = connection.execute(
            """
            SELECT id, server_context_id, loot_cost, quantity_quality_base_cost,
                   quantity_quality_level_increment, observed_at, import_event_id
            FROM kakeraloot_settings_observations
            """
        ).fetchone()
        assert observation["id"] > 0
        assert observation["server_context_id"] == connection.execute(
            "SELECT id FROM server_contexts WHERE normalized_name = ?",
            ("server",),
        ).fetchone()[0]
        assert tuple(observation)[2:] == (
            500,
            2000,
            200,
            result.observed_at.isoformat(),
            result.import_event_id,
        )


def test_public_kakeraloot_settings_wrapper_runner_rolls_back_and_recovers(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    original_helper = catalog._import_kakeraloot_settings_with_connection

    def fail_after_write(connection: sqlite3.Connection, **kwargs):
        original_helper(connection, **kwargs)
        raise RuntimeError("forced Kakeraloot settings import failure")

    monkeypatch.setattr(
        catalog,
        "_import_kakeraloot_settings_with_connection",
        fail_after_write,
    )

    with pytest.raises(RuntimeError, match="forced Kakeraloot settings import failure"):
        catalog.import_kakeraloot_settings(
            KAKERALOOT_SETTINGS, "Server", "failed payload", "discord"
        )

    with connect(database_path) as connection:
        assert _kakeraloot_settings_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "kakeraloot_settings_observations": 0,
            "discord_projection_links": 0,
            "discord_source_event_server_attributions": 0,
            "discord_processing_attempts": 0,
        }

    monkeypatch.setattr(
        catalog,
        "_import_kakeraloot_settings_with_connection",
        original_helper,
    )
    result = catalog.import_kakeraloot_settings(
        KAKERALOOT_SETTINGS, "Server", "successful payload", "discord"
    )
    assert result.import_event_id > 0
    with connect(database_path) as connection:
        assert _kakeraloot_settings_counts(connection)[
            "kakeraloot_settings_observations"
        ] == 1


def test_kakeraloot_settings_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_kakeraloot_settings_with_connection(
            connection,
            settings=KAKERALOOT_SETTINGS,
            server="Server",
            raw="infokl payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM kakeraloot_settings_observations").fetchone()[0] == 1
        assert connection.execute(
            "SELECT import_event_id FROM kakeraloot_settings_observations WHERE id = ?",
            (imported.kakeraloot_settings_observation_id,),
        ).fetchone()[0] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM kakeraloot_settings_observations").fetchone()[0] == 0


def test_kakeraloot_settings_helper_commit_persists_rows_and_result_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_kakeraloot_settings_with_connection(
            connection,
            settings=KAKERALOOT_SETTINGS,
            server="Server",
            raw="infokl payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT id FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()[0] == imported.import_event_id
        assert connection.execute(
            "SELECT id FROM kakeraloot_settings_observations WHERE id = ?",
            (imported.kakeraloot_settings_observation_id,),
        ).fetchone()[0] == imported.kakeraloot_settings_observation_id
        row = connection.execute(
            """
            SELECT loot_cost, quantity_quality_base_cost, quantity_quality_level_increment,
                   import_event_id
            FROM kakeraloot_settings_observations
            WHERE id = ?
            """,
            (imported.kakeraloot_settings_observation_id,),
        ).fetchone()
        assert tuple(row) == (500, 2000, 200, imported.import_event_id)


def test_kakeraloot_settings_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_kakeraloot_settings_with_connection(
                connection,
                settings=KAKERALOOT_SETTINGS,
                server="Server",
                raw="infokl payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        assert _kakeraloot_settings_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "kakeraloot_settings_observations": 0,
            "discord_projection_links": 0,
            "discord_source_event_server_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_kakeraloot_settings_helper_reuses_server_and_rollback_preserves_it(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_kakeraloot_settings(KAKERALOOT_SETTINGS, "Server", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            "SELECT id, name, normalized_name FROM server_contexts"
        ).fetchone()
        before_counts = _kakeraloot_settings_counts(connection)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_kakeraloot_settings_with_connection(
                connection,
                settings=KAKERALOOT_SETTINGS,
                server=" SERVER ",
                raw="second payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        current = connection.execute(
            "SELECT id, name, normalized_name FROM server_contexts"
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _kakeraloot_settings_counts(connection) == before_counts


def test_kakeraloot_settings_helper_does_not_write_projection_or_discord_state(tmp_path) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    with connect(database_path) as connection:
        before_event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()
        before_attempt = connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()[0]
        before_counts = _kakeraloot_settings_counts(connection)
        catalog._import_kakeraloot_settings_with_connection(
            connection,
            settings=KAKERALOOT_SETTINGS,
            server="Server",
            raw="infokl payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        after_counts = _kakeraloot_settings_counts(connection)
        after_event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()
        after_attempt = connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()[0]

    assert after_counts["import_events"] == before_counts["import_events"] + 1
    assert after_counts["server_contexts"] == before_counts["server_contexts"] + 1
    assert after_counts["kakeraloot_settings_observations"] == before_counts["kakeraloot_settings_observations"] + 1
    assert after_counts["discord_projection_links"] == before_counts["discord_projection_links"]
    assert after_counts["discord_source_event_server_attributions"] == before_counts[
        "discord_source_event_server_attributions"
    ]
    assert after_counts["discord_processing_attempts"] == before_counts["discord_processing_attempts"]
    assert tuple(after_event) == tuple(before_event)
    assert after_attempt == before_attempt


def test_profile_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_profile_with_connection(
            connection,
            profile=PROFILE,
            server="Server",
            account="Account",
            raw="profile payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM profile_observations").fetchone()[0] == 1
        assert connection.execute(
            "SELECT import_event_id FROM profile_observations WHERE id = ?",
            (imported.profile_observation_id,),
        ).fetchone()[0] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM profile_observations").fetchone()[0] == 0


def test_profile_helper_is_transaction_neutral_on_supplied_connection(
    tmp_path, monkeypatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    def unexpected_connection(*args, **kwargs):
        raise AssertionError("profile helper opened an independent connection")

    def unexpected_runner(*args, **kwargs):
        raise AssertionError("profile helper started an independent transaction")

    monkeypatch.setattr(profile_repository_module, "connect", unexpected_connection)
    monkeypatch.setattr(profile_repository_module, "run_write_transaction", unexpected_runner)

    with connect(database_path) as connection:
        imported = catalog._import_profile_with_connection(
            connection,
            profile=PROFILE,
            server="Server",
            account="Account",
            raw="profile payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert imported.profile_observation_id > 0


def test_profile_helper_commit_persists_all_rows_and_result_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_profile_with_connection(
            connection,
            profile=PROFILE,
            server="Server",
            account="Account",
            raw="profile payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT id FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()[0] == imported.import_event_id
        assert connection.execute(
            "SELECT id FROM profile_observations WHERE id = ?", (imported.profile_observation_id,)
        ).fetchone()[0] == imported.profile_observation_id
        assert _counts(connection)["profile_observations"] == 1
        assert _counts(connection)["discord_projection_links"] == 0


def test_profile_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_profile_with_connection(
                connection,
                profile=PROFILE,
                server="Server",
                account="Account",
                raw="profile payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 0,
            "characters": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "claim_observations": 0,
            "profile_observations": 0,
            "roll_observations": 0,
            "harem_key_observations": 0,
            "rank_snapshots": 0,
            "server_character_observations": 0,
            "discord_projection_links": 0,
        }


def test_profile_helper_reuses_canonical_rows_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_profile(PROFILE, "Server", "Account", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_profile_with_connection(
                connection,
                profile=PROFILE,
                server=" SERVER ",
                account=" ACCOUNT ",
                raw="second payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _counts(connection)["import_events"] == 1
        assert _counts(connection)["profile_observations"] == 1


def test_processing_success_helper_updates_supplied_connection(tmp_path) -> None:
    database_path, _catalog, discord = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    with connect(database_path) as connection:
        import_event_id = int(
            connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) VALUES (?, ?, ?, ?)",
                ("roll", "discord", OBSERVED_AT.isoformat(), "roll payload"),
            ).lastrowid
        )
        result = discord._mark_processing_success_with_connection(
            connection,
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            finished_at=FINISHED_AT,
            legacy_import_event_id=import_event_id,
        )
        assert connection.in_transaction is True
        assert result.attempt_status == "succeeded"
        assert result.source_event_status == "succeeded"
        assert result.legacy_import_event_id == import_event_id
        assert connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()[0] == "succeeded"


def test_one_caller_transaction_commits_roll_and_success_atomically(tmp_path) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    with connect(database_path) as connection:
        roll_result = catalog._import_roll_with_connection(
            connection,
            roll=ROLL,
            server="Server",
            account="Account",
            raw="roll payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        success = discord._mark_processing_success_with_connection(
            connection,
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            finished_at=FINISHED_AT,
            legacy_import_event_id=roll_result.import_event_id,
        )
        assert success.attempt_status == "succeeded"

    with connect(database_path) as connection:
        counts = _counts(connection)
        assert counts["import_events"] == 1
        assert counts["roll_observations"] == 1
        assert counts["discord_projection_links"] == 0
        event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events"
        ).fetchone()
        assert (event["status"], event["legacy_import_event_id"]) == (
            "succeeded",
            roll_result.import_event_id,
        )
        assert connection.execute("SELECT status FROM discord_processing_attempts").fetchone()[0] == "succeeded"


def test_failure_after_roll_helper_rolls_back_catalog_and_canonical_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            catalog._import_roll_with_connection(
                connection,
                roll=ROLL,
                server="Server",
                account="Account",
                raw="roll payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        assert _counts(connection) == {
            "import_events": 0,
            "characters": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "claim_observations": 0,
            "profile_observations": 0,
            "roll_observations": 0,
            "harem_key_observations": 0,
            "rank_snapshots": 0,
            "server_character_observations": 0,
            "discord_projection_links": 0,
        }


def test_failure_after_both_helpers_rolls_back_catalog_and_processing_success(tmp_path) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            roll_result = catalog._import_roll_with_connection(
                connection,
                roll=ROLL,
                server="Server",
                account="Account",
                raw="roll payload",
                source="discord",
                observed_at=OBSERVED_AT,
            )
            discord._mark_processing_success_with_connection(
                connection,
                source_event_id=source_event_id,
                attempt_id=attempt_id,
                finished_at=FINISHED_AT,
                legacy_import_event_id=roll_result.import_event_id,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        counts = _counts(connection)
        assert counts["import_events"] == 0
        assert counts["characters"] == 0
        assert counts["roll_observations"] == 0
        assert counts["discord_projection_links"] == 0
        event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events"
        ).fetchone()
        assert (event["status"], event["legacy_import_event_id"]) == ("processing", None)
        assert connection.execute("SELECT status FROM discord_processing_attempts").fetchone()[0] == "processing"


def test_public_timer_state_wrapper_preserves_result_rows_and_snapshot(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    raw_message = "timer payload with exact source text"

    result = catalog.import_timer_state(
        TIMER_STATE,
        " Server ",
        " Account ",
        raw_message,
        "discord",
    )

    assert set(result.model_dump()) == {"import_event_id", "server_name", "account_name", "observed_at"}
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.observed_at.tzinfo is not None
    with connect(database_path) as connection:
        assert _timer_state_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "timer_state_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("timer_state", "discord", raw_message, result.observed_at.isoformat())
        observation = connection.execute(
            """
            SELECT id, account_context_id, snapshot_json, observed_at, import_event_id
            FROM timer_state_observations
            WHERE id = (SELECT MAX(id) FROM timer_state_observations)
            """
        ).fetchone()
        account = connection.execute(
            "SELECT id, name, normalized_name FROM account_contexts"
        ).fetchone()
        server = connection.execute(
            "SELECT name, normalized_name FROM server_contexts"
        ).fetchone()
        assert tuple(account) == (account["id"], "Account", "account")
        assert tuple(server) == ("Server", "server")
        assert observation["id"] > 0
        assert observation["account_context_id"] == account["id"]
        assert json.loads(observation["snapshot_json"]) == TIMER_STATE.model_dump()
        assert observation["observed_at"] == result.observed_at.isoformat()
        assert observation["import_event_id"] == result.import_event_id


def test_timer_state_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        imported = catalog._import_timer_state_with_connection(
            connection,
            state=TIMER_STATE,
            server="Server",
            account="Account",
            raw="timer payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert _timer_state_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "timer_state_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        observation = connection.execute(
            "SELECT import_event_id FROM timer_state_observations WHERE id = ?",
            (imported.timer_state_observation_id,),
        ).fetchone()
        assert observation["import_event_id"] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM timer_state_observations").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0


def test_timer_state_helper_commit_persists_rows_and_returned_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_timer_state_with_connection(
            connection,
            state=TIMER_STATE,
            server="Server",
            account="Account",
            raw="timer payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT id, kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (imported.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            "SELECT id, observed_at, import_event_id, account_context_id FROM timer_state_observations WHERE id = ?",
            (imported.timer_state_observation_id,),
        ).fetchone()
        account = connection.execute(
            "SELECT id FROM account_contexts WHERE normalized_name = ?",
            ("account",),
        ).fetchone()
        assert event["id"] == imported.import_event_id
        assert tuple(event)[1:] == ("timer_state", "discord", "timer payload", OBSERVED_AT.isoformat())
        assert observation["id"] == imported.timer_state_observation_id
        assert observation["observed_at"] == OBSERVED_AT.isoformat()
        assert observation["import_event_id"] == imported.import_event_id
        assert observation["account_context_id"] == account["id"]


def test_timer_state_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_timer_state_with_connection(
            connection,
            state=TIMER_STATE,
            server="Server",
            account="Account",
            raw="timer payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.rollback()

    with connect(database_path) as connection:
        assert _timer_state_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "timer_state_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_timer_state_helper_reuses_contexts_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_timer_state(TIMER_STATE, "Server", "Account", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        before_counts = _timer_state_counts(connection)

    with connect(database_path) as connection:
        imported = catalog._import_timer_state_with_connection(
            connection,
            state=TIMER_STATE,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="second payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert imported.import_event_id > 0
        assert imported.timer_state_observation_id > 0
        connection.rollback()

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _timer_state_counts(connection) == before_counts


def test_kakeraloot_state_connection_result_is_frozen_slotted_with_exact_fields() -> None:
    assert is_dataclass(_KakeralootStateImportConnectionResult)
    assert _KakeralootStateImportConnectionResult.__dataclass_params__.frozen is True
    assert [field.name for field in fields(_KakeralootStateImportConnectionResult)] == [
        "import_event_id",
        "kakeraloot_state_observation_id",
    ]
    assert not hasattr(_KakeralootStateImportConnectionResult(1, 2), "__dict__")


def test_public_kakeraloot_state_wrapper_preserves_result_and_stored_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    raw_message = "kakeraloot state payload with exact source text"

    result = catalog.import_kakeraloot_state(
        KAKERALOOT_STATE,
        "  Server  ",
        "  Account  ",
        raw_message,
        "discord",
    )

    assert result.import_event_id > 0
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.observed_at.tzinfo is not None
    observation = catalog.kakeraloot_state("Server", "Account")
    assert observation is not None
    assert observation.server_name == "Server"
    assert observation.account_name == "Account"
    assert observation.has_kakeraloots is True
    assert observation.status_note == KAKERALOOT_STATE.status_note
    for field in (
        "rolls_stacked",
        "disable_wa_ha_reduction",
        "disable_wg_hg_reduction",
        "protected_wish_level",
        "protected_wish_denominator",
        "mudapins",
        "rt_cooldown_reduction_hours",
        "permanent_roll_bonus",
        "star_branches",
        "starwish_slots_from_branches",
        "quantity_level",
        "quality_level",
        "usage_count",
        "kakera_balance",
    ):
        assert getattr(observation, field) == getattr(KAKERALOOT_STATE, field)
    assert observation.observed_at == result.observed_at

    with connect(database_path) as connection:
        assert _kakeraloot_state_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "kakeraloot_state_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == (
            "kakeraloot_state",
            "discord",
            raw_message,
            result.observed_at.isoformat(),
        )
        stored = connection.execute(
            """
            SELECT has_kakeraloots, status_note, rolls_stacked, disable_wa_ha_reduction,
                   disable_wg_hg_reduction, protected_wish_level, protected_wish_denominator,
                   mudapins, rt_cooldown_reduction_hours, permanent_roll_bonus,
                   star_branches, starwish_slots_from_branches, quantity_level, quality_level,
                   usage_count, kakera_balance, observed_at, import_event_id
            FROM kakeraloot_state_observations
            """
        ).fetchone()
        assert tuple(stored) == (
            1,
            "guarded state",
            17,
            102,
            68,
            42,
            4_642,
            22,
            2,
            1,
            3,
            4,
            5,
            6,
            1_234,
            7_673,
            result.observed_at.isoformat(),
            result.import_event_id,
        )


def test_public_kakeraloot_state_wrapper_runner_rolls_back_and_recovers(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    original_helper = (
        catalog._kakeraloot_state_repository._import_kakeraloot_state_with_connection
    )

    def fail_after_write(connection: sqlite3.Connection, **kwargs):
        original_helper(connection, **kwargs)
        raise RuntimeError("forced Kakeraloot State import failure")

    monkeypatch.setattr(
        catalog._kakeraloot_state_repository,
        "_import_kakeraloot_state_with_connection",
        fail_after_write,
    )

    with pytest.raises(RuntimeError, match="forced Kakeraloot State import failure"):
        catalog.import_kakeraloot_state(
            KAKERALOOT_STATE,
            "Server",
            "Account",
            "failed payload",
            "discord",
        )

    with connect(database_path) as connection:
        assert _kakeraloot_state_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "kakeraloot_state_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }

    monkeypatch.setattr(
        catalog._kakeraloot_state_repository,
        "_import_kakeraloot_state_with_connection",
        original_helper,
    )
    result = catalog.import_kakeraloot_state(
        KAKERALOOT_STATE,
        "Server",
        "Account",
        "successful payload",
        "discord",
    )
    assert result.import_event_id > 0
    with connect(database_path) as connection:
        assert _kakeraloot_state_counts(connection)[
            "kakeraloot_state_observations"
        ] == 1


def test_kakeraloot_state_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        imported = catalog._import_kakeraloot_state_with_connection(
            connection,
            state=KAKERALOOT_STATE,
            server="Server",
            account="Account",
            raw="kakeraloot payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert imported.import_event_id > 0
        assert imported.kakeraloot_state_observation_id > 0
        assert _kakeraloot_state_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "kakeraloot_state_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        observation = connection.execute(
            "SELECT import_event_id FROM kakeraloot_state_observations WHERE id = ?",
            (imported.kakeraloot_state_observation_id,),
        ).fetchone()
        assert observation["import_event_id"] == imported.import_event_id
        with connect(database_path) as observer:
            assert _kakeraloot_state_counts(observer) == {
                "import_events": 0,
                "server_contexts": 0,
                "account_contexts": 0,
                "kakeraloot_state_observations": 0,
                "discord_projection_links": 0,
                "discord_source_events": 0,
                "discord_source_event_server_attributions": 0,
                "discord_source_event_account_attributions": 0,
                "discord_processing_attempts": 0,
            }


def test_kakeraloot_state_helper_commit_persists_rows_and_returned_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_kakeraloot_state_with_connection(
            connection,
            state=KAKERALOOT_STATE,
            server="Server",
            account="Account",
            raw="kakeraloot payload",
            source="clipboard",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT id, kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (imported.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT id, account_context_id, observed_at, import_event_id
            FROM kakeraloot_state_observations
            WHERE id = ?
            """,
            (imported.kakeraloot_state_observation_id,),
        ).fetchone()
        assert event["id"] == imported.import_event_id
        assert tuple(event)[1:] == (
            "kakeraloot_state",
            "clipboard",
            "kakeraloot payload",
            OBSERVED_AT.isoformat(),
        )
        assert observation["id"] == imported.kakeraloot_state_observation_id
        assert observation["account_context_id"] == connection.execute(
            "SELECT id FROM account_contexts WHERE normalized_name = ?",
            ("account",),
        ).fetchone()[0]
        assert observation["observed_at"] == OBSERVED_AT.isoformat()
        assert observation["import_event_id"] == imported.import_event_id


def test_kakeraloot_state_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_kakeraloot_state_with_connection(
            connection,
            state=KAKERALOOT_STATE,
            server="Server",
            account="Account",
            raw="kakeraloot payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.rollback()

    with connect(database_path) as connection:
        assert _kakeraloot_state_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "kakeraloot_state_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_kakeraloot_state_helper_reuses_contexts_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_kakeraloot_state(KAKERALOOT_STATE, "Server", "Account", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        before_counts = _kakeraloot_state_counts(connection)

    with connect(database_path) as connection:
        imported = catalog._import_kakeraloot_state_with_connection(
            connection,
            state=KAKERALOOT_STATE,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="second payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert imported.import_event_id > 0
        assert imported.kakeraloot_state_observation_id > 0
        connection.rollback()

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _kakeraloot_state_counts(connection) == before_counts


@pytest.mark.parametrize(
    ("state", "expected_has_kakeraloots", "expected_status_note"),
    [
        (ZERO_KAKERALOOT_STATE, 1, ""),
        (NULL_KAKERALOOT_STATE, 1, None),
        (NO_KAKERALOOT_STATE, 0, "No Kakeraloots bought; Mudae did not report loot statistics."),
    ],
)
def test_kakeraloot_state_helper_preserves_boundary_values(tmp_path, state, expected_has_kakeraloots, expected_status_note) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_kakeraloot_state_with_connection(
            connection,
            state=state,
            server="Server",
            account="Account",
            raw="boundary kakeraloot payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT has_kakeraloots, status_note, rolls_stacked, disable_wa_ha_reduction,
                   disable_wg_hg_reduction, protected_wish_level, protected_wish_denominator,
                   mudapins, rt_cooldown_reduction_hours, permanent_roll_bonus,
                   star_branches, starwish_slots_from_branches, quantity_level, quality_level,
                   usage_count, kakera_balance, observed_at, import_event_id
            FROM kakeraloot_state_observations
            WHERE id = ?
            """,
            (imported.kakeraloot_state_observation_id,),
        ).fetchone()
        assert tuple(row) == (
            expected_has_kakeraloots,
            expected_status_note,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            OBSERVED_AT.isoformat(),
            imported.import_event_id,
        )
        observation = catalog.kakeraloot_state("Server", "Account")
        assert observation is not None
        assert observation.has_kakeraloots is bool(expected_has_kakeraloots)
        assert observation.status_note == expected_status_note
        if expected_has_kakeraloots:
            assert observation.rolls_stacked == 0
            assert observation.kakera_balance == 0
        else:
            assert observation.rolls_stacked is None
            assert observation.kakera_balance is None


def test_kakeraloot_state_helper_does_not_write_projection_or_discord_state(tmp_path) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    with connect(database_path) as connection:
        before_event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()
        before_attempt = connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()[0]
        before_counts = _kakeraloot_state_counts(connection)
        catalog._import_kakeraloot_state_with_connection(
            connection,
            state=KAKERALOOT_STATE,
            server="Server",
            account="Account",
            raw="kakeraloot payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        after_counts = _kakeraloot_state_counts(connection)
        after_event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()
        after_attempt = connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()[0]

    assert after_counts["import_events"] == before_counts["import_events"] + 1
    assert after_counts["server_contexts"] == before_counts["server_contexts"] + 1
    assert after_counts["account_contexts"] == before_counts["account_contexts"] + 1
    assert after_counts["kakeraloot_state_observations"] == before_counts["kakeraloot_state_observations"] + 1
    assert after_counts["discord_projection_links"] == before_counts["discord_projection_links"]
    assert after_counts["discord_source_events"] == before_counts["discord_source_events"]
    assert after_counts["discord_source_event_server_attributions"] == before_counts[
        "discord_source_event_server_attributions"
    ]
    assert after_counts["discord_source_event_account_attributions"] == before_counts[
        "discord_source_event_account_attributions"
    ]
    assert after_counts["discord_processing_attempts"] == before_counts["discord_processing_attempts"]
    assert tuple(after_event) == tuple(before_event)
    assert after_attempt == before_attempt


def test_player_bonus_connection_result_is_frozen_slotted_with_exact_fields() -> None:
    assert is_dataclass(_PlayerBonusImportConnectionResult)
    assert _PlayerBonusImportConnectionResult.__dataclass_params__.frozen is True
    assert [field.name for field in fields(_PlayerBonusImportConnectionResult)] == [
        "import_event_id",
        "player_bonus_observation_id",
    ]
    assert not hasattr(_PlayerBonusImportConnectionResult(1, 2), "__dict__")


def test_public_player_bonus_wrapper_preserves_result_and_stored_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    raw_message = "player bonus payload with exact source text"

    result = catalog.import_player_bonus(
        PLAYER_BONUS,
        "  Server  ",
        "  Account  ",
        raw_message,
        "discord",
    )

    assert set(result.model_dump()) == {"import_event_id", "server_name", "account_name", "observed_at"}
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.observed_at.tzinfo is not None
    assert result.observed_at.utcoffset().total_seconds() == 0
    with connect(database_path) as connection:
        assert _player_bonus_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "player_bonus_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT account_context_id, metrics_json, rolls_per_hour_bonus, wishlist_slot_bonus,
                   wish_spawn_bonus_percent, starwish_spawn_bonus_percent,
                   starwish_total_spawn_bonus_percent, starwish_slot_bonus,
                   additional_wish_key_chance_percent, kakera_max_power_percent,
                   kakera_button_power_cost_percent, starwish_kakera_button_bonus_percent,
                   light_kakera_minimum, light_kakera_maximum, observed_at, import_event_id
            FROM player_bonus_observations WHERE import_event_id = ?
            """,
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("player_bonus", "discord", result.observed_at.isoformat(), raw_message)
        assert observation["metrics_json"] == json.dumps(
            [metric.model_dump() for metric in PLAYER_BONUS.metrics]
        )
        assert tuple(observation)[2:] == (
            PLAYER_BONUS.rolls_per_hour_bonus,
            PLAYER_BONUS.wishlist_slot_bonus,
            PLAYER_BONUS.wish_spawn_bonus_percent,
            PLAYER_BONUS.starwish_spawn_bonus_percent,
            PLAYER_BONUS.starwish_total_spawn_bonus_percent,
            PLAYER_BONUS.starwish_slot_bonus,
            PLAYER_BONUS.additional_wish_key_chance_percent,
            PLAYER_BONUS.kakera_max_power_percent,
            PLAYER_BONUS.kakera_button_power_cost_percent,
            PLAYER_BONUS.starwish_kakera_button_bonus_percent,
            PLAYER_BONUS.light_kakera_minimum,
            PLAYER_BONUS.light_kakera_maximum,
            result.observed_at.isoformat(),
            result.import_event_id,
        )


def test_public_player_bonus_wrapper_runner_rolls_back_and_recovers(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    original_helper = catalog._player_bonus_repository._import_player_bonus_with_connection

    def fail_after_write(connection: sqlite3.Connection, **kwargs):
        original_helper(connection, **kwargs)
        raise RuntimeError("forced Player Bonus import failure")

    monkeypatch.setattr(
        catalog._player_bonus_repository,
        "_import_player_bonus_with_connection",
        fail_after_write,
    )

    with pytest.raises(RuntimeError, match="forced Player Bonus import failure"):
        catalog.import_player_bonus(
            PLAYER_BONUS,
            "Server",
            "Account",
            "failed payload",
            "discord",
        )

    with connect(database_path) as connection:
        assert _player_bonus_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "player_bonus_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }

    monkeypatch.setattr(
        catalog._player_bonus_repository,
        "_import_player_bonus_with_connection",
        original_helper,
    )
    result = catalog.import_player_bonus(
        PLAYER_BONUS,
        "Server",
        "Account",
        "successful payload",
        "discord",
    )
    assert result.import_event_id > 0
    with connect(database_path) as connection:
        assert _player_bonus_counts(connection)["player_bonus_observations"] == 1


def test_player_bonus_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        imported = catalog._import_player_bonus_with_connection(
            connection,
            state=PLAYER_BONUS,
            server="Server",
            account="Account",
            raw="player bonus payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert _player_bonus_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "player_bonus_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        observation = connection.execute(
            "SELECT id, import_event_id FROM player_bonus_observations WHERE id = ?",
            (imported.player_bonus_observation_id,),
        ).fetchone()
        assert observation["id"] == imported.player_bonus_observation_id
        assert observation["import_event_id"] == imported.import_event_id
        with connect(database_path) as observer:
            assert _player_bonus_counts(observer) == {
                "import_events": 0,
                "server_contexts": 0,
                "account_contexts": 0,
                "player_bonus_observations": 0,
                "discord_projection_links": 0,
                "discord_source_events": 0,
                "discord_source_event_server_attributions": 0,
                "discord_source_event_account_attributions": 0,
                "discord_processing_attempts": 0,
            }
        connection.rollback()


def test_player_bonus_helper_commit_persists_values_and_returned_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_player_bonus_with_connection(
            connection,
            state=PLAYER_BONUS,
            server="Server",
            account="Account",
            raw="player bonus payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT id, kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (imported.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT id, account_context_id, metrics_json, rolls_per_hour_bonus, wishlist_slot_bonus,
                   wish_spawn_bonus_percent, starwish_spawn_bonus_percent,
                   starwish_total_spawn_bonus_percent, starwish_slot_bonus,
                   additional_wish_key_chance_percent, kakera_max_power_percent,
                   kakera_button_power_cost_percent, starwish_kakera_button_bonus_percent,
                   light_kakera_minimum, light_kakera_maximum, observed_at, import_event_id
            FROM player_bonus_observations WHERE id = ?
            """,
            (imported.player_bonus_observation_id,),
        ).fetchone()
        account = connection.execute(
            "SELECT id, name, normalized_name, server_context_id FROM account_contexts"
        ).fetchone()
        server = connection.execute(
            "SELECT id, name, normalized_name FROM server_contexts"
        ).fetchone()
        assert event["id"] == imported.import_event_id
        assert tuple(event)[1:] == ("player_bonus", "discord", "player bonus payload", OBSERVED_AT.isoformat())
        assert observation["id"] == imported.player_bonus_observation_id
        assert observation["account_context_id"] == account["id"]
        assert observation["metrics_json"] == json.dumps(
            [metric.model_dump() for metric in PLAYER_BONUS.metrics]
        )
        assert tuple(observation)[3:] == (
            PLAYER_BONUS.rolls_per_hour_bonus,
            PLAYER_BONUS.wishlist_slot_bonus,
            PLAYER_BONUS.wish_spawn_bonus_percent,
            PLAYER_BONUS.starwish_spawn_bonus_percent,
            PLAYER_BONUS.starwish_total_spawn_bonus_percent,
            PLAYER_BONUS.starwish_slot_bonus,
            PLAYER_BONUS.additional_wish_key_chance_percent,
            PLAYER_BONUS.kakera_max_power_percent,
            PLAYER_BONUS.kakera_button_power_cost_percent,
            PLAYER_BONUS.starwish_kakera_button_bonus_percent,
            PLAYER_BONUS.light_kakera_minimum,
            PLAYER_BONUS.light_kakera_maximum,
            OBSERVED_AT.isoformat(),
            imported.import_event_id,
        )
        assert tuple(server) == (server["id"], "Server", "server")
        assert tuple(account) == (account["id"], "Account", "account", server["id"])
        assert _player_bonus_counts(connection)["player_bonus_observations"] == 1


def test_player_bonus_helper_rollback_removes_new_rows_and_contexts(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_player_bonus_with_connection(
            connection,
            state=PLAYER_BONUS,
            server="Server",
            account="Account",
            raw="player bonus payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.rollback()

    with connect(database_path) as connection:
        assert _player_bonus_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "player_bonus_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_player_bonus_helper_reuses_contexts_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_player_bonus(PLAYER_BONUS, "Server", "Account", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        before_counts = _player_bonus_counts(connection)

    with connect(database_path) as connection:
        imported = catalog._import_player_bonus_with_connection(
            connection,
            state=PLAYER_BONUS,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="second payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert imported.import_event_id > 0
        assert imported.player_bonus_observation_id > 0
        connection.rollback()

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _player_bonus_counts(connection) == before_counts


def test_player_bonus_helper_preserves_null_zero_negative_and_empty_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_player_bonus_with_connection(
            connection,
            state=BOUNDARY_PLAYER_BONUS,
            server="Server",
            account="Account",
            raw="boundary player bonus payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        observation = connection.execute(
            """
            SELECT metrics_json, rolls_per_hour_bonus, wishlist_slot_bonus,
                   wish_spawn_bonus_percent, starwish_spawn_bonus_percent,
                   starwish_total_spawn_bonus_percent, starwish_slot_bonus,
                   additional_wish_key_chance_percent, kakera_max_power_percent,
                   kakera_button_power_cost_percent, starwish_kakera_button_bonus_percent,
                   light_kakera_minimum, light_kakera_maximum, import_event_id
            FROM player_bonus_observations WHERE id = ?
            """,
            (imported.player_bonus_observation_id,),
        ).fetchone()
        assert json.loads(observation["metrics_json"]) == [
            {"label": "", "detail": ""}
        ]
        assert tuple(observation)[1:] == (
            0,
            None,
            -1,
            0,
            None,
            0,
            None,
            0,
            -2,
            None,
            0,
            None,
            imported.import_event_id,
        )


def test_public_sphere_result_wrapper_preserves_compatibility_and_stored_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    raw_message = "sphere result payload with exact source text"

    result = catalog.import_sphere_result(
        SPHERE_RESULT,
        "  Server  ",
        "  Account  ",
        raw_message,
        "discord",
    )

    assert set(result.model_dump()) == {"import_event_id", "server_name", "account_name", "observed_at"}
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.observed_at.tzinfo is not None
    assert result.observed_at.utcoffset().total_seconds() == 0
    with connect(database_path) as connection:
        assert _sphere_result_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "sphere_result_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT account_context_id, snapshot_json, total_gained, stock, observed_at, import_event_id
            FROM sphere_result_observations WHERE import_event_id = ?
            """,
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("sphere_result", "discord", result.observed_at.isoformat(), raw_message)
        assert json.loads(observation["snapshot_json"]) == SPHERE_RESULT.model_dump(mode="json")
        assert observation["total_gained"] == SPHERE_RESULT.total_gained
        assert observation["stock"] is None
        assert observation["observed_at"] == result.observed_at.isoformat()
        assert observation["import_event_id"] == result.import_event_id


def test_public_sphere_result_wrapper_rolls_back_helper_failure_and_remains_usable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    original_helper = catalog._import_sphere_result_with_connection

    def fail_after_write(connection: sqlite3.Connection, **kwargs):
        original_helper(connection, **kwargs)
        raise RuntimeError("forced sphere-result import failure")

    monkeypatch.setattr(catalog, "_import_sphere_result_with_connection", fail_after_write)

    with pytest.raises(RuntimeError, match="forced sphere-result import failure"):
        catalog.import_sphere_result(
            SPHERE_RESULT, "Server", "Account", "failed payload", "discord"
        )

    with connect(database_path) as connection:
        assert _sphere_result_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "sphere_result_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }

    monkeypatch.setattr(catalog, "_import_sphere_result_with_connection", original_helper)
    result = catalog.import_sphere_result(
        SPHERE_RESULT, "Server", "Account", "successful payload", "discord"
    )
    assert result.import_event_id > 0
    with connect(database_path) as connection:
        assert _sphere_result_counts(connection)["sphere_result_observations"] == 1


def test_sphere_result_coordinator_runner_commits_without_public_wrapper_nesting(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    coordinator = SphereResultProjectionCoordinator(catalog, discord)
    assert catalog._database_path == coordinator._database_path == database_path
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_sphere_result_attribution(discord, source_event_id)
    monkeypatch.setattr(
        catalog,
        "import_sphere_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("coordinator called public sphere-result wrapper")
        ),
    )

    result = coordinator.coordinate_sphere_result(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        state=SPHERE_RESULT,
        server=" Server ",
        account=" Account ",
        raw="sphere payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )

    assert result == SphereResultProjectionResult(
        imported_count=1,
        import_event_id=result.import_event_id,
        sphere_result_observation_id=result.sphere_result_observation_id,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=(
            "sphere_result_observations",
            result.sphere_result_observation_id,
        ),
    )
    with connect(database_path) as connection:
        assert _sphere_result_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "sphere_result_observations": 1,
            "discord_projection_links": 1,
            "discord_source_events": 1,
            "discord_source_event_server_attributions": 1,
            "discord_source_event_account_attributions": 1,
            "discord_processing_attempts": 1,
        }
        assert connection.execute(
            "SELECT status FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()[0] == "succeeded"
        assert connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()[0] == "succeeded"
        assert connection.execute(
            "SELECT state FROM discord_projection_links WHERE source_event_id = ?",
            (source_event_id,),
        ).fetchone()[0] == "completed"


def test_sphere_result_coordinator_runner_rolls_back_partial_projection(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    coordinator = SphereResultProjectionCoordinator(catalog, discord)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_sphere_result_attribution(discord, source_event_id)
    monkeypatch.setattr(
        coordinator,
        "_complete_projection_link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("forced sphere-result projection failure")
        ),
    )

    with pytest.raises(RuntimeError, match="forced sphere-result projection failure"):
        coordinator.coordinate_sphere_result(
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            state=SPHERE_RESULT,
            server="Server",
            account="Account",
            raw="sphere payload",
            source="discord",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        )

    with connect(database_path) as connection:
        assert _sphere_result_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "sphere_result_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 1,
            "discord_source_event_server_attributions": 1,
            "discord_source_event_account_attributions": 1,
            "discord_processing_attempts": 1,
        }
        assert connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()[:] == ("processing", None)
        assert connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()[0] == "processing"


def test_sphere_result_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        imported = catalog._import_sphere_result_with_connection(
            connection,
            state=SPHERE_RESULT,
            server="Server",
            account="Account",
            raw="sphere payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert _sphere_result_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "sphere_result_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        observation = connection.execute(
            "SELECT id, import_event_id FROM sphere_result_observations WHERE id = ?",
            (imported.sphere_result_observation_id,),
        ).fetchone()
        assert observation["id"] == imported.sphere_result_observation_id
        assert observation["import_event_id"] == imported.import_event_id
        with connect(database_path) as observer:
            assert _sphere_result_counts(observer) == {
                "import_events": 0,
                "server_contexts": 0,
                "account_contexts": 0,
                "sphere_result_observations": 0,
                "discord_projection_links": 0,
                "discord_source_events": 0,
                "discord_source_event_server_attributions": 0,
                "discord_source_event_account_attributions": 0,
                "discord_processing_attempts": 0,
            }


@pytest.mark.parametrize("stock", [None, 0, 123])
def test_sphere_result_helper_commit_persists_values_and_returned_ids(tmp_path, stock) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    state = SPHERE_RESULT.model_copy(update={"stock": stock})

    with connect(database_path) as connection:
        imported = catalog._import_sphere_result_with_connection(
            connection,
            state=state,
            server="Server",
            account="Account",
            raw="sphere payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT id, kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (imported.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT id, account_context_id, snapshot_json, total_gained, stock, observed_at, import_event_id
            FROM sphere_result_observations WHERE id = ?
            """,
            (imported.sphere_result_observation_id,),
        ).fetchone()
        account = connection.execute(
            "SELECT id, name, normalized_name FROM account_contexts"
        ).fetchone()
        server = connection.execute(
            "SELECT id, name, normalized_name FROM server_contexts"
        ).fetchone()
        assert event["id"] == imported.import_event_id
        assert tuple(event)[1:] == ("sphere_result", "discord", "sphere payload", OBSERVED_AT.isoformat())
        assert observation["id"] == imported.sphere_result_observation_id
        assert observation["account_context_id"] == account["id"]
        assert observation["snapshot_json"] == json.dumps(state.model_dump())
        assert observation["total_gained"] == SPHERE_RESULT.total_gained
        assert observation["stock"] == stock
        assert observation["observed_at"] == OBSERVED_AT.isoformat()
        assert observation["import_event_id"] == imported.import_event_id
        assert tuple(server) == (server["id"], "Server", "server")
        assert tuple(account) == (account["id"], "Account", "account")
        assert _sphere_result_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "sphere_result_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_sphere_result_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_sphere_result_with_connection(
            connection,
            state=SPHERE_RESULT,
            server="Server",
            account="Account",
            raw="sphere payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.rollback()

    with connect(database_path) as connection:
        assert _sphere_result_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "sphere_result_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_sphere_result_helper_reuses_contexts_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_sphere_result(SPHERE_RESULT, "Server", "Account", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        before_counts = _sphere_result_counts(connection)

    with connect(database_path) as connection:
        imported = catalog._import_sphere_result_with_connection(
            connection,
            state=SPHERE_RESULT,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="second payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert imported.import_event_id > 0
        assert imported.sphere_result_observation_id > 0
        connection.rollback()

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _sphere_result_counts(connection) == before_counts


def test_wishlist_connection_result_is_frozen_slotted_with_exact_fields() -> None:
    assert is_dataclass(_WishlistImportConnectionResult)
    assert _WishlistImportConnectionResult.__dataclass_params__.frozen is True
    assert [field.name for field in fields(_WishlistImportConnectionResult)] == [
        "import_event_id",
        "wishlist_observation_id",
    ]
    assert not hasattr(_WishlistImportConnectionResult(1, 2), "__dict__")


def test_public_wishlist_wrapper_preserves_result_and_stored_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    raw_message = "wishlist payload with exact source text"

    result = catalog.import_wishlist(
        WISHLIST,
        "  Server  ",
        "  Account  ",
        raw_message,
        "discord",
    )

    assert set(result.model_dump()) == {"import_event_id", "server_name", "account_name", "observed_at"}
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.observed_at.tzinfo is not None
    assert result.observed_at.utcoffset().total_seconds() == 0
    with connect(database_path) as connection:
        assert _wishlist_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "wishlist_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT account_context_id, wishlist_count, wishlist_capacity, starwish_count,
                   starwish_capacity, entries_json, observed_at, import_event_id
            FROM wishlist_observations WHERE import_event_id = ?
            """,
            (result.import_event_id,),
        ).fetchone()
        account = connection.execute(
            "SELECT id, name, normalized_name FROM account_contexts"
        ).fetchone()
        server = connection.execute(
            "SELECT id, name, normalized_name FROM server_contexts"
        ).fetchone()
        assert tuple(event) == ("wishlist", "discord", result.observed_at.isoformat(), raw_message)
        assert observation["account_context_id"] == account["id"]
        assert tuple(observation)[1:5] == (
            WISHLIST.wishlist_count,
            WISHLIST.wishlist_capacity,
            WISHLIST.starwish_count,
            WISHLIST.starwish_capacity,
        )
        assert observation["entries_json"] == json.dumps(
            [entry.model_dump() for entry in WISHLIST.entries]
        )
        assert observation["observed_at"] == result.observed_at.isoformat()
        assert observation["import_event_id"] == result.import_event_id
        assert tuple(server) == (server["id"], "Server", "server")
        assert tuple(account) == (account["id"], "Account", "account")


def test_public_wishlist_wrapper_rolls_back_helper_failure_and_remains_usable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    original_helper = catalog._wishlist_repository._import_wishlist_with_connection

    def fail_after_write(connection: sqlite3.Connection, **kwargs):
        original_helper(connection, **kwargs)
        raise RuntimeError("forced wishlist import failure")

    monkeypatch.setattr(
        catalog._wishlist_repository,
        "_import_wishlist_with_connection",
        fail_after_write,
    )

    with pytest.raises(RuntimeError, match="forced wishlist import failure"):
        catalog.import_wishlist(
            WISHLIST, "Server", "Account", "failed payload", "discord"
        )

    with connect(database_path) as connection:
        assert _wishlist_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "wishlist_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }

    monkeypatch.setattr(
        catalog._wishlist_repository,
        "_import_wishlist_with_connection",
        original_helper,
    )
    result = catalog.import_wishlist(
        WISHLIST, "Server", "Account", "successful payload", "discord"
    )
    assert result.import_event_id > 0
    with connect(database_path) as connection:
        assert _wishlist_counts(connection)["wishlist_observations"] == 1


def test_wishlist_coordinator_runner_commits_without_public_wrapper_nesting(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    coordinator = WishlistProjectionCoordinator(catalog, discord)
    assert catalog._database_path == coordinator._database_path == database_path
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_wishlist_attribution(discord, source_event_id)
    monkeypatch.setattr(
        catalog,
        "import_wishlist",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("coordinator called public wishlist wrapper")
        ),
    )

    result = coordinator.coordinate_wishlist(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        state=WISHLIST,
        server=" Server ",
        account=" Account ",
        raw="wishlist payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )

    assert result == WishlistProjectionResult(
        imported_count=1,
        import_event_id=result.import_event_id,
        wishlist_observation_id=result.wishlist_observation_id,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("wishlist_observations", result.wishlist_observation_id),
    )
    with connect(database_path) as connection:
        assert _wishlist_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "wishlist_observations": 1,
            "discord_projection_links": 1,
            "discord_source_events": 1,
            "discord_source_event_server_attributions": 1,
            "discord_source_event_account_attributions": 1,
            "discord_processing_attempts": 1,
        }
        assert connection.execute(
            "SELECT status FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()[0] == "succeeded"
        assert connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()[0] == "succeeded"
        assert connection.execute(
            "SELECT state FROM discord_projection_links WHERE source_event_id = ?",
            (source_event_id,),
        ).fetchone()[0] == "completed"


def test_wishlist_coordinator_runner_rolls_back_partial_projection(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    coordinator = WishlistProjectionCoordinator(catalog, discord)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_wishlist_attribution(discord, source_event_id)
    monkeypatch.setattr(
        coordinator,
        "_complete_projection_link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("forced wishlist projection failure")
        ),
    )

    with pytest.raises(RuntimeError, match="forced wishlist projection failure"):
        coordinator.coordinate_wishlist(
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            state=WISHLIST,
            server="Server",
            account="Account",
            raw="wishlist payload",
            source="discord",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        )

    with connect(database_path) as connection:
        assert _wishlist_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "wishlist_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 1,
            "discord_source_event_server_attributions": 1,
            "discord_source_event_account_attributions": 1,
            "discord_processing_attempts": 1,
        }
        assert tuple(
            connection.execute(
                "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
                (source_event_id,),
            ).fetchone()
        ) == ("processing", None)
        assert connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()[0] == "processing"


def test_wishlist_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        imported = catalog._import_wishlist_with_connection(
            connection,
            state=WISHLIST,
            server="Server",
            account="Account",
            raw="wishlist payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert isinstance(imported, _WishlistImportConnectionResult)
        assert connection.in_transaction is True
        assert _wishlist_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "wishlist_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT id, kind FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()
        observation = connection.execute(
            "SELECT id, import_event_id FROM wishlist_observations WHERE id = ?",
            (imported.wishlist_observation_id,),
        ).fetchone()
        assert tuple(event) == (imported.import_event_id, "wishlist")
        assert tuple(observation) == (
            imported.wishlist_observation_id,
            imported.import_event_id,
        )
        with connect(database_path) as observer:
            assert _wishlist_counts(observer) == {
                "import_events": 0,
                "server_contexts": 0,
                "account_contexts": 0,
                "wishlist_observations": 0,
                "discord_projection_links": 0,
                "discord_source_events": 0,
                "discord_source_event_server_attributions": 0,
                "discord_source_event_account_attributions": 0,
                "discord_processing_attempts": 0,
            }
        connection.rollback()


def test_wishlist_helper_commit_persists_values_and_returned_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_wishlist_with_connection(
            connection,
            state=WISHLIST,
            server="Server",
            account="Account",
            raw="wishlist payload",
            source="clipboard",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT id, kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (imported.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT id, account_context_id, wishlist_count, wishlist_capacity, starwish_count,
                   starwish_capacity, entries_json, observed_at, import_event_id
            FROM wishlist_observations WHERE id = ?
            """,
            (imported.wishlist_observation_id,),
        ).fetchone()
        assert event["id"] == imported.import_event_id
        assert tuple(event)[1:] == ("wishlist", "clipboard", "wishlist payload", OBSERVED_AT.isoformat())
        assert observation["id"] == imported.wishlist_observation_id
        assert observation["wishlist_count"] == WISHLIST.wishlist_count
        assert observation["wishlist_capacity"] == WISHLIST.wishlist_capacity
        assert observation["starwish_count"] == WISHLIST.starwish_count
        assert observation["starwish_capacity"] == WISHLIST.starwish_capacity
        assert observation["entries_json"] == json.dumps(
            [entry.model_dump() for entry in WISHLIST.entries]
        )
        assert observation["observed_at"] == OBSERVED_AT.isoformat()
        assert observation["import_event_id"] == imported.import_event_id
        wishlist = catalog.wishlist("Server", "Account")
        assert wishlist is not None
        assert wishlist.entries == WISHLIST.entries


def test_wishlist_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_wishlist_with_connection(
            connection,
            state=WISHLIST,
            server="Server",
            account="Account",
            raw="wishlist payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.rollback()

    with connect(database_path) as connection:
        assert _wishlist_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "wishlist_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_wishlist_helper_reuses_contexts_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_wishlist(WISHLIST, "Server", "Account", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        before_counts = _wishlist_counts(connection)

    with connect(database_path) as connection:
        imported = catalog._import_wishlist_with_connection(
            connection,
            state=EMPTY_WISHLIST,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="second payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert imported.import_event_id > 0
        assert imported.wishlist_observation_id > 0
        connection.rollback()

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _wishlist_counts(connection) == before_counts


@pytest.mark.parametrize("state", [EMPTY_WISHLIST])
def test_wishlist_helper_preserves_empty_zero_and_duplicate_boundaries(tmp_path, state) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_wishlist_with_connection(
            connection,
            state=state,
            server="Server",
            account="Account",
            raw="boundary wishlist payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT wishlist_count, wishlist_capacity, starwish_count, starwish_capacity, entries_json
            FROM wishlist_observations WHERE id = ?
            """,
            (imported.wishlist_observation_id,),
        ).fetchone()
        assert tuple(row) == (0, 0, 0, 0, "[]")
        assert json.loads(row["entries_json"]) == []

        duplicate_row = connection.execute(
            "SELECT entries_json FROM wishlist_observations WHERE import_event_id = ?",
            (catalog.import_wishlist(WISHLIST, "Server", "Account", "duplicate payload", "discord").import_event_id,),
        ).fetchone()
        assert json.loads(duplicate_row["entries_json"]) == [entry.model_dump() for entry in WISHLIST.entries]


def test_disablelist_connection_result_is_frozen_slotted_with_exact_fields() -> None:
    assert is_dataclass(_DisableListImportConnectionResult)
    assert _DisableListImportConnectionResult.__dataclass_params__.frozen is True
    assert [field.name for field in fields(_DisableListImportConnectionResult)] == [
        "import_event_id",
        "disablelist_observation_id",
    ]
    assert not hasattr(_DisableListImportConnectionResult(1, 2), "__dict__")


def test_public_disablelist_wrapper_preserves_result_and_stored_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    raw_message = "disablelist payload with exact source text"

    result = catalog.import_disablelist(
        DISABLELIST,
        "  Server  ",
        "  Account  ",
        raw_message,
        "discord",
    )

    assert set(result.model_dump()) == {"import_event_id", "server_name", "account_name", "observed_at"}
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.observed_at.tzinfo is not None
    assert result.observed_at.utcoffset().total_seconds() == 0
    with connect(database_path) as connection:
        assert _disablelist_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "disablelist_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT account_context_id, slots_used, slots_capacity, total_disabled, disabled_wa,
                   disabled_ha, disabled_wg, disabled_hg, wa_pool_limit, ha_pool_limit,
                   western_disabled, irl_disabled, entries_json, observed_at, import_event_id
            FROM disablelist_observations WHERE import_event_id = ?
            """,
            (result.import_event_id,),
        ).fetchone()
        account = connection.execute("SELECT id, name, normalized_name FROM account_contexts").fetchone()
        server = connection.execute("SELECT id, name, normalized_name FROM server_contexts").fetchone()
        assert tuple(event) == ("disablelist", "discord", result.observed_at.isoformat(), raw_message)
        assert observation["account_context_id"] == account["id"]
        assert tuple(observation)[1:12] == (
            DISABLELIST.slots_used,
            DISABLELIST.slots_capacity,
            DISABLELIST.total_disabled,
            DISABLELIST.disabled_wa,
            DISABLELIST.disabled_ha,
            DISABLELIST.disabled_wg,
            DISABLELIST.disabled_hg,
            DISABLELIST.wa_pool_limit,
            DISABLELIST.ha_pool_limit,
            DISABLELIST.western_disabled,
            DISABLELIST.irl_disabled,
        )
        assert observation["entries_json"] == json.dumps(
            [entry.model_dump() for entry in DISABLELIST.entries]
        )
        assert observation["observed_at"] == result.observed_at.isoformat()
        assert observation["import_event_id"] == result.import_event_id
        assert tuple(server) == (server["id"], "Server", "server")
        assert tuple(account) == (account["id"], "Account", "account")


def test_public_disablelist_wrapper_rolls_back_helper_failure_and_remains_usable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    original_helper = catalog._disablelist_repository._import_disablelist_with_connection

    def fail_after_write(connection: sqlite3.Connection, **kwargs):
        original_helper(connection, **kwargs)
        raise RuntimeError("forced disablelist import failure")

    monkeypatch.setattr(
        catalog._disablelist_repository,
        "_import_disablelist_with_connection",
        fail_after_write,
    )

    with pytest.raises(RuntimeError, match="forced disablelist import failure"):
        catalog.import_disablelist(
            DISABLELIST, "Server", "Account", "failed payload", "discord"
        )

    with connect(database_path) as connection:
        assert _disablelist_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "disablelist_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }

    monkeypatch.setattr(
        catalog._disablelist_repository,
        "_import_disablelist_with_connection",
        original_helper,
    )
    result = catalog.import_disablelist(
        DISABLELIST, "Server", "Account", "successful payload", "discord"
    )
    assert result.import_event_id > 0
    with connect(database_path) as connection:
        assert _disablelist_counts(connection)["disablelist_observations"] == 1


def test_disablelist_coordinator_runner_commits_without_public_wrapper_nesting(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    coordinator = DisableListProjectionCoordinator(catalog, discord)
    assert catalog._database_path == coordinator._database_path == database_path
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_account_scoped_attribution(discord, source_event_id)
    monkeypatch.setattr(
        catalog,
        "import_disablelist",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("coordinator called public disablelist wrapper")
        ),
    )

    result = coordinator.coordinate_disablelist(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        state=DISABLELIST,
        server=" Server ",
        account=" Account ",
        raw="disablelist payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )

    assert result == DisableListProjectionResult(
        imported_count=1,
        import_event_id=result.import_event_id,
        disablelist_observation_id=result.disablelist_observation_id,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("disablelist_observations", result.disablelist_observation_id),
    )
    with connect(database_path) as connection:
        assert _disablelist_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "disablelist_observations": 1,
            "discord_projection_links": 1,
            "discord_source_events": 1,
            "discord_source_event_server_attributions": 1,
            "discord_source_event_account_attributions": 1,
            "discord_processing_attempts": 1,
        }
        assert tuple(
            connection.execute(
                "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
                (source_event_id,),
            ).fetchone()
        ) == ("succeeded", result.import_event_id)
        assert connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()[0] == "succeeded"
        assert tuple(
            connection.execute(
                "SELECT state, projection_table, projection_row_id FROM discord_projection_links WHERE source_event_id = ?",
                (source_event_id,),
            ).fetchone()
        ) == ("completed", "disablelist_observations", result.disablelist_observation_id)


def test_disablelist_coordinator_runner_rolls_back_partial_projection(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    coordinator = DisableListProjectionCoordinator(catalog, discord)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_account_scoped_attribution(discord, source_event_id)
    monkeypatch.setattr(
        coordinator,
        "_complete_projection_link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("forced disablelist projection failure")
        ),
    )

    with pytest.raises(RuntimeError, match="forced disablelist projection failure"):
        coordinator.coordinate_disablelist(
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            state=DISABLELIST,
            server="Server",
            account="Account",
            raw="disablelist payload",
            source="discord",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        )

    with connect(database_path) as connection:
        assert _disablelist_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "disablelist_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 1,
            "discord_source_event_server_attributions": 1,
            "discord_source_event_account_attributions": 1,
            "discord_processing_attempts": 1,
        }
        assert tuple(
            connection.execute(
                "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
                (source_event_id,),
            ).fetchone()
        ) == ("processing", None)
        assert connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()[0] == "processing"


def test_disablelist_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        imported = catalog._import_disablelist_with_connection(
            connection,
            state=DISABLELIST,
            server="Server",
            account="Account",
            raw="disablelist payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert isinstance(imported, _DisableListImportConnectionResult)
        assert connection.in_transaction is True
        assert _disablelist_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "disablelist_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT id, kind FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()
        observation = connection.execute(
            "SELECT id, import_event_id FROM disablelist_observations WHERE id = ?",
            (imported.disablelist_observation_id,),
        ).fetchone()
        assert tuple(event) == (imported.import_event_id, "disablelist")
        assert tuple(observation) == (
            imported.disablelist_observation_id,
            imported.import_event_id,
        )
        with connect(database_path) as observer:
            assert _disablelist_counts(observer) == {
                "import_events": 0,
                "server_contexts": 0,
                "account_contexts": 0,
                "disablelist_observations": 0,
                "discord_projection_links": 0,
                "discord_source_events": 0,
                "discord_source_event_server_attributions": 0,
                "discord_source_event_account_attributions": 0,
                "discord_processing_attempts": 0,
            }
        connection.rollback()


def test_disablelist_helper_commit_preserves_all_values_and_returned_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_disablelist_with_connection(
            connection,
            state=DISABLELIST,
            server="Server",
            account="Account",
            raw="disablelist payload",
            source="clipboard",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT id, kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (imported.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT id, slots_used, slots_capacity, total_disabled, disabled_wa, disabled_ha,
                   disabled_wg, disabled_hg, wa_pool_limit, ha_pool_limit, western_disabled,
                   irl_disabled, entries_json, observed_at, import_event_id
            FROM disablelist_observations WHERE id = ?
            """,
            (imported.disablelist_observation_id,),
        ).fetchone()
        assert event["id"] == imported.import_event_id
        assert tuple(event)[1:] == (
            "disablelist",
            "clipboard",
            "disablelist payload",
            OBSERVED_AT.isoformat(),
        )
        assert observation["id"] == imported.disablelist_observation_id
        assert tuple(observation)[1:12] == (
            DISABLELIST.slots_used,
            DISABLELIST.slots_capacity,
            DISABLELIST.total_disabled,
            DISABLELIST.disabled_wa,
            DISABLELIST.disabled_ha,
            DISABLELIST.disabled_wg,
            DISABLELIST.disabled_hg,
            DISABLELIST.wa_pool_limit,
            DISABLELIST.ha_pool_limit,
            DISABLELIST.western_disabled,
            DISABLELIST.irl_disabled,
        )
        assert observation["entries_json"] == json.dumps(
            [entry.model_dump() for entry in DISABLELIST.entries]
        )
        assert observation["observed_at"] == OBSERVED_AT.isoformat()
        assert observation["import_event_id"] == imported.import_event_id
        disablelist = catalog.disablelist("Server", "Account")
        assert disablelist is not None
        assert disablelist.entries == DISABLELIST.entries


def test_disablelist_helper_rollback_removes_new_rows_and_contexts(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_disablelist_with_connection(
            connection,
            state=DISABLELIST,
            server="Server",
            account="Account",
            raw="disablelist payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.rollback()

    with connect(database_path) as connection:
        assert _disablelist_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "disablelist_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_disablelist_helper_reuses_contexts_and_preserves_boundary_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_disablelist(DISABLELIST, "Server", "Account", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        before_counts = _disablelist_counts(connection)

    with connect(database_path) as connection:
        imported = catalog._import_disablelist_with_connection(
            connection,
            state=BOUNDARY_DISABLELIST,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="boundary disablelist payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        row = connection.execute(
            """
            SELECT slots_used, slots_capacity, total_disabled, disabled_wa, disabled_ha, disabled_wg,
                   disabled_hg, wa_pool_limit, ha_pool_limit, western_disabled, irl_disabled, entries_json
            FROM disablelist_observations WHERE id = ?
            """,
            (imported.disablelist_observation_id,),
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert tuple(row) == (0, 0, 0, 0, 0, 0, 0, 0, None, 0, 0, "[]")
        connection.rollback()

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _disablelist_counts(connection) == before_counts


def test_public_mudapins_wrapper_preserves_compatibility_and_stored_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    raw_message = "mudapins payload with exact source text"

    result = catalog.import_mudapins(
        MUDAPINS,
        "  Server  ",
        "  Account  ",
        raw_message,
        "discord",
    )

    assert set(result.model_dump()) == {"import_event_id", "server_name", "account_name", "observed_at"}
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.observed_at.tzinfo is not None
    assert result.observed_at.utcoffset().total_seconds() == 0
    with connect(database_path) as connection:
        assert _mudapins_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "mudapin_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT id, account_context_id, pin_markers_json, pin_count, observed_at, import_event_id
            FROM mudapin_observations WHERE import_event_id = ?
            """,
            (result.import_event_id,),
        ).fetchone()
        account = connection.execute("SELECT id, name, normalized_name FROM account_contexts").fetchone()
        server = connection.execute("SELECT name, normalized_name FROM server_contexts").fetchone()
        assert tuple(event) == ("mudapins", "discord", result.observed_at.isoformat(), raw_message)
        assert observation["id"] > 0
        assert observation["account_context_id"] == account["id"]
        assert json.loads(observation["pin_markers_json"]) == list(MUDAPINS.pin_markers)
        assert observation["pin_count"] == len(MUDAPINS.pin_markers)
        assert observation["observed_at"] == result.observed_at.isoformat()
        assert observation["import_event_id"] == result.import_event_id
        assert tuple(account) == (account["id"], "Account", "account")
        assert tuple(server) == ("Server", "server")


def test_public_mudapins_wrapper_runner_rolls_back_recovers_and_preserves_contexts(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_mudapins(MUDAPINS, "Server", "Account", "initial payload", "discord")
    with connect(database_path) as connection:
        before_counts = _mudapins_counts(connection)
        before_context = tuple(connection.execute(
            """
            SELECT server_contexts.id, server_contexts.name, server_contexts.normalized_name,
                   server_contexts.created_at, server_contexts.updated_at,
                   account_contexts.id, account_contexts.name,
                   account_contexts.normalized_name, account_contexts.created_at,
                   account_contexts.updated_at
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone())
    original_helper = catalog._import_mudapins_with_connection

    def fail_after_write(connection: sqlite3.Connection, **kwargs):
        original_helper(connection, **kwargs)
        changed_context = connection.execute(
            """
            SELECT server_contexts.name, account_contexts.name
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(changed_context) == ("SERVER", "ACCOUNT")
        raise RuntimeError("forced MudaPins import failure")

    monkeypatch.setattr(
        catalog,
        "_import_mudapins_with_connection",
        fail_after_write,
    )

    with pytest.raises(RuntimeError, match="forced MudaPins import failure"):
        catalog.import_mudapins(
            MUDAPINS,
            " SERVER ",
            " ACCOUNT ",
            "failed payload",
            "discord",
        )

    with connect(database_path) as connection:
        assert _mudapins_counts(connection) == before_counts
        current_context = tuple(connection.execute(
            """
            SELECT server_contexts.id, server_contexts.name, server_contexts.normalized_name,
                   server_contexts.created_at, server_contexts.updated_at,
                   account_contexts.id, account_contexts.name,
                   account_contexts.normalized_name, account_contexts.created_at,
                   account_contexts.updated_at
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone())
        assert current_context == before_context

    monkeypatch.setattr(
        catalog,
        "_import_mudapins_with_connection",
        original_helper,
    )
    result = catalog.import_mudapins(
        MUDAPINS,
        "Server",
        "Account",
        "successful payload",
        "discord",
    )
    assert result.import_event_id > 0
    with connect(database_path) as connection:
        assert _mudapins_counts(connection)["mudapin_observations"] == 2


def test_mudapins_helper_writes_on_supplied_connection_before_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        imported = catalog._import_mudapins_with_connection(
            connection,
            snapshot=MUDAPINS,
            server="Server",
            account="Account",
            raw="mudapins payload",
            source="clipboard",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert _mudapins_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "mudapin_observations": 1,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        assert connection.execute(
            "SELECT import_event_id FROM mudapin_observations WHERE id = ?",
            (imported.mudapin_observation_id,),
        ).fetchone()[0] == imported.import_event_id
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM mudapin_observations").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
            assert observer.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0


def test_mudapins_helper_commit_persists_rows_and_returned_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_mudapins_with_connection(
            connection,
            snapshot=MUDAPINS,
            server="Server",
            account="Account",
            raw="mudapins payload",
            source="clipboard",
            observed_at=OBSERVED_AT,
        )
        connection.commit()

    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT id, kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (imported.import_event_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT id, observed_at, import_event_id, account_context_id
            FROM mudapin_observations WHERE id = ?
            """,
            (imported.mudapin_observation_id,),
        ).fetchone()
        account = connection.execute(
            "SELECT id FROM account_contexts WHERE normalized_name = ?", ("account",)
        ).fetchone()
        assert event["id"] == imported.import_event_id
        assert tuple(event)[1:] == (
            "mudapins",
            "clipboard",
            "mudapins payload",
            OBSERVED_AT.isoformat(),
        )
        assert observation["id"] == imported.mudapin_observation_id
        assert observation["observed_at"] == OBSERVED_AT.isoformat()
        assert observation["import_event_id"] == imported.import_event_id
        assert observation["account_context_id"] == account["id"]


def test_mudapins_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_mudapins_with_connection(
            connection,
            snapshot=MUDAPINS,
            server="Server",
            account="Account",
            raw="mudapins payload",
            source="clipboard",
            observed_at=OBSERVED_AT,
        )
        connection.rollback()

    with connect(database_path) as connection:
        assert _mudapins_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "mudapin_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_mudapins_helper_reuses_contexts_and_rollback_preserves_them(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    catalog.import_mudapins(MUDAPINS, "Server", "Account", "initial payload", "discord")

    with connect(database_path) as connection:
        existing = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        before_counts = _mudapins_counts(connection)

    with connect(database_path) as connection:
        imported = catalog._import_mudapins_with_connection(
            connection,
            snapshot=MUDAPINS,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="second payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert imported.import_event_id > 0
        assert imported.mudapin_observation_id > 0
        connection.rollback()

    with connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT server_contexts.id AS server_id, account_contexts.id AS account_id
            FROM server_contexts
            JOIN account_contexts ON account_contexts.server_context_id = server_contexts.id
            """
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert _mudapins_counts(connection) == before_counts


def test_mudapins_marker_storage_preserves_order_duplicates_and_empty_inventory(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    snapshots = (
        (MudapinSnapshot(pin_markers=(":pin1:", ":pin2:")), (":pin1:", ":pin2:")),
        (MudapinSnapshot(pin_markers=(":pin3:", ":logopin4:")), (":pin3:", ":logopin4:")),
        (
            MudapinSnapshot(pin_markers=(":pin5:", ":pin5:", ":logopin6:")),
            (":pin5:", ":pin5:", ":logopin6:"),
        ),
        (MudapinSnapshot(pin_markers=()), ()),
    )

    results = [
        catalog.import_mudapins(snapshot, "Server", f"Account {index}", "payload", "discord")
        for index, (snapshot, _expected) in enumerate(snapshots)
    ]

    with connect(database_path) as connection:
        for result, (_snapshot, expected) in zip(results, snapshots):
            observation = connection.execute(
                "SELECT pin_markers_json, pin_count FROM mudapin_observations WHERE import_event_id = ?",
                (result.import_event_id,),
            ).fetchone()
            assert json.loads(observation["pin_markers_json"]) == list(expected)
            assert observation["pin_count"] == len(expected)


def test_mudapins_helper_does_not_write_discord_or_legacy_state(tmp_path) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    source_event_id, attempt_id = _receive_and_begin(discord)

    with connect(database_path) as connection:
        before_event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()
        before_attempt = connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()[0]
        before_counts = _mudapins_counts(connection)
        imported = catalog._import_mudapins_with_connection(
            connection,
            snapshot=MUDAPINS,
            server="Server",
            account="Account",
            raw="mudapins payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        after_counts = _mudapins_counts(connection)
        after_event = connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()
        after_attempt = connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()[0]

    assert after_counts["import_events"] == before_counts["import_events"] + 1
    assert after_counts["server_contexts"] == before_counts["server_contexts"] + 1
    assert after_counts["account_contexts"] == before_counts["account_contexts"] + 1
    assert after_counts["mudapin_observations"] == before_counts["mudapin_observations"] + 1
    assert after_counts["discord_projection_links"] == before_counts["discord_projection_links"]
    assert after_counts["discord_source_event_server_attributions"] == before_counts[
        "discord_source_event_server_attributions"
    ]
    assert after_counts["discord_source_event_account_attributions"] == before_counts[
        "discord_source_event_account_attributions"
    ]
    assert after_counts["discord_processing_attempts"] == before_counts["discord_processing_attempts"]
    assert tuple(after_event) == tuple(before_event)
    assert after_attempt == before_attempt
    assert imported.import_event_id > 0
    assert imported.mudapin_observation_id > 0


def test_public_antidisable_import_preserves_result_and_stored_values(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    result = catalog.import_antidisable_page(
        ANTIDISABLE_PAGE,
        "  Server  ",
        "  Account  ",
        "antidisable payload",
        "clipboard",
    )

    assert set(result.model_dump()) == {
        "import_event_id",
        "server_name",
        "account_name",
        "series_imported",
        "observed_at",
        "scan_id",
        "page_number",
        "page_count",
    }
    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert result.series_imported == 3
    assert result.scan_id is None
    assert result.page_number == 1
    assert result.page_count == 2
    assert result.observed_at == datetime.fromisoformat(result.observed_at.isoformat())

    with connect(database_path) as connection:
        assert _antidisable_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "harem_scans": 0,
            "harem_scan_pages": 0,
            "antidisable_series_observations": 3,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        event = connection.execute(
            "SELECT kind, source, observed_at, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        rows = connection.execute(
            """
            SELECT series_name, normalized_series_name, antidisabled_character_count,
                   observed_at, import_event_id, harem_scan_id
            FROM antidisable_series_observations
            WHERE import_event_id = ?
            ORDER BY id
            """,
            (result.import_event_id,),
        ).fetchall()
        assert tuple(event) == (
            "antidisable",
            "clipboard",
            result.observed_at.isoformat(),
            "antidisable payload",
        )
        assert [tuple(row) for row in rows] == [
            ("Series B", "series b", 2_614, result.observed_at.isoformat(), result.import_event_id, None),
            ("Series A", "series a", 2_614, result.observed_at.isoformat(), result.import_event_id, None),
            ("Series B", "series b", 2_614, result.observed_at.isoformat(), result.import_event_id, None),
        ]


def test_public_antidisable_wrapper_rolls_back_helper_failure_and_remains_usable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    scan = catalog.begin_antidisable_scan("Server", "Account")
    original_helper = catalog._import_antidisable_page_with_connection

    def fail_after_write(connection: sqlite3.Connection, **kwargs):
        original_helper(connection, **kwargs)
        raise RuntimeError("forced antidisable page import failure")

    with connect(database_path) as connection:
        before = _antidisable_counts(connection)

    monkeypatch.setattr(catalog, "_import_antidisable_page_with_connection", fail_after_write)
    with pytest.raises(RuntimeError, match="forced antidisable page import failure"):
        catalog.import_antidisable_page(
            ANTIDISABLE_CONTINUATION_PAGE,
            "Server",
            "Account",
            "failed page two",
            "discord",
            scan.id,
        )

    with connect(database_path) as connection:
        assert _antidisable_counts(connection) == before
    progress = catalog.harem_scan_progress(scan.id)
    assert progress is not None
    assert progress.expected_page_count is None
    assert progress.imported_pages == ()
    assert progress.completed_at is None

    monkeypatch.setattr(catalog, "_import_antidisable_page_with_connection", original_helper)
    result = catalog.import_antidisable_page(
        ANTIDISABLE_CONTINUATION_PAGE,
        "Server",
        "Account",
        "successful page two",
        "discord",
        scan.id,
    )
    assert result.scan_id == scan.id
    assert result.page_number == 2
    progress = catalog.harem_scan_progress(scan.id)
    assert progress is not None
    assert progress.expected_page_count == 2
    assert progress.imported_pages == (2,)
    assert progress.completed_at is None


def test_antidisable_scan_start_helper_visibility_and_commit(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        scan_id = catalog._begin_antidisable_scan_with_connection(
            connection,
            server=" Server ",
            account=" Account ",
            observed_at=OBSERVED_AT,
        )
        assert scan_id > 0
        assert connection.in_transaction is True
        scan = connection.execute(
            """
            SELECT harem_scans.id, server_contexts.name, account_contexts.name,
                   harem_scans.expected_page_count, harem_scans.completed_at,
                   harem_scans.scan_kind, harem_scans.started_at
            FROM harem_scans
            JOIN account_contexts ON account_contexts.id = harem_scans.account_context_id
            JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
            WHERE harem_scans.id = ?
            """,
            (scan_id,),
        ).fetchone()
        assert tuple(scan) == (
            scan_id,
            "Server",
            "Account",
            None,
            None,
            "antidisable",
            OBSERVED_AT.isoformat(),
        )
        assert _antidisable_counts(connection) == {
            "import_events": 0,
            "server_contexts": 1,
            "account_contexts": 1,
            "harem_scans": 1,
            "harem_scan_pages": 0,
            "antidisable_series_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        with connect(database_path) as observer:
            assert _antidisable_counts(observer) == {
                "import_events": 0,
                "server_contexts": 0,
                "account_contexts": 0,
                "harem_scans": 0,
                "harem_scan_pages": 0,
                "antidisable_series_observations": 0,
                "discord_projection_links": 0,
                "discord_source_events": 0,
                "discord_source_event_server_attributions": 0,
                "discord_source_event_account_attributions": 0,
                "discord_processing_attempts": 0,
            }
        connection.commit()

    with connect(database_path) as observer:
        assert _antidisable_counts(observer) == {
            "import_events": 0,
            "server_contexts": 1,
            "account_contexts": 1,
            "harem_scans": 1,
            "harem_scan_pages": 0,
            "antidisable_series_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        assert observer.execute(
            "SELECT account_context_id FROM harem_scans WHERE id = ?", (scan_id,)
        ).fetchone()[0] > 0


def test_antidisable_scan_start_helper_external_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        scan_id = catalog._begin_antidisable_scan_with_connection(
            connection,
            server="Server",
            account="Account",
            observed_at=OBSERVED_AT,
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM harem_scans WHERE id = ?", (scan_id,)
        ).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 1
        with connect(database_path) as observer:
            assert _antidisable_counts(observer) == {
                "import_events": 0,
                "server_contexts": 0,
                "account_contexts": 0,
                "harem_scans": 0,
                "harem_scan_pages": 0,
                "antidisable_series_observations": 0,
                "discord_projection_links": 0,
                "discord_source_events": 0,
                "discord_source_event_server_attributions": 0,
                "discord_source_event_account_attributions": 0,
                "discord_processing_attempts": 0,
            }
        connection.rollback()

    with connect(database_path) as observer:
        assert _antidisable_counts(observer) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "harem_scans": 0,
            "harem_scan_pages": 0,
            "antidisable_series_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_public_antidisable_scan_start_preserves_result_and_commits(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    original_progress = catalog.harem_scan_progress

    def progress_after_commit(scan_id: int):
        with connect(database_path) as observer:
            assert observer.execute(
                "SELECT COUNT(*) FROM harem_scans WHERE id = ?", (scan_id,)
            ).fetchone()[0] == 1
        return original_progress(scan_id)

    monkeypatch.setattr(catalog, "harem_scan_progress", progress_after_commit)

    scan = catalog.begin_antidisable_scan(" Server ", " Account ")

    assert scan.id > 0
    assert scan.server_name == "Server"
    assert scan.account_name == "Account"
    assert scan.expected_page_count is None
    assert scan.imported_pages == ()
    assert scan.completed_at is None
    assert scan.scan_kind == "antidisable"
    with connect(database_path) as connection:
        assert _antidisable_counts(connection) == {
            "import_events": 0,
            "server_contexts": 1,
            "account_contexts": 1,
            "harem_scans": 1,
            "harem_scan_pages": 0,
            "antidisable_series_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }
        row = connection.execute(
            "SELECT started_at, scan_kind FROM harem_scans WHERE id = ?", (scan.id,)
        ).fetchone()
        assert datetime.fromisoformat(row["started_at"]).tzinfo is not None
        assert row["scan_kind"] == "antidisable"


def test_public_antidisable_scan_start_rolls_back_contexts_and_recovers(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    existing = catalog.begin_antidisable_scan("Original Server", "Original Account")
    with connect(database_path) as connection:
        before_server = connection.execute(
            "SELECT id, name, normalized_name, created_at, updated_at FROM server_contexts"
        ).fetchone()
        before_account = connection.execute(
            "SELECT id, server_context_id, name, normalized_name, created_at, updated_at "
            "FROM account_contexts"
        ).fetchone()
        before_scan = connection.execute(
            "SELECT id, account_context_id, expected_page_count, started_at, completed_at, scan_kind "
            "FROM harem_scans WHERE id = ?",
            (existing.id,),
        ).fetchone()
        connection.execute(
            """
            CREATE TRIGGER fail_antidisable_scan_startup
            BEFORE INSERT ON harem_scans
            BEGIN
                SELECT RAISE(FAIL, 'forced antidisable scan startup failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced antidisable scan startup failure"):
        catalog.begin_antidisable_scan("New Server", "New Account")

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM server_contexts WHERE normalized_name = 'new server'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM account_contexts WHERE normalized_name = 'new account'"
        ).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM harem_scans").fetchone()[0] == 1

    with pytest.raises(sqlite3.IntegrityError, match="forced antidisable scan startup failure"):
        catalog.begin_antidisable_scan(" ORIGINAL SERVER ", " ORIGINAL ACCOUNT ")

    with connect(database_path) as connection:
        after_server = connection.execute(
            "SELECT id, name, normalized_name, created_at, updated_at FROM server_contexts"
        ).fetchone()
        after_account = connection.execute(
            "SELECT id, server_context_id, name, normalized_name, created_at, updated_at "
            "FROM account_contexts"
        ).fetchone()
        after_scan = connection.execute(
            "SELECT id, account_context_id, expected_page_count, started_at, completed_at, scan_kind "
            "FROM harem_scans WHERE id = ?",
            (existing.id,),
        ).fetchone()
        assert tuple(after_server) == tuple(before_server)
        assert tuple(after_account) == tuple(before_account)
        assert tuple(after_scan) == tuple(before_scan)
        connection.execute("DROP TRIGGER fail_antidisable_scan_startup")

    recovered = catalog.begin_antidisable_scan("Original Server", "Original Account")

    assert recovered.id > existing.id
    assert recovered.server_name == "Original Server"
    assert recovered.account_name == "Original Account"
    assert recovered.expected_page_count is None
    assert recovered.imported_pages == ()
    assert recovered.completed_at is None
    assert recovered.scan_kind == "antidisable"
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM harem_scans").fetchone()[0] == 2


def test_public_antidisable_scan_start_progress_failure_leaves_committed_scan(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    def fail_progress(_scan_id: int):
        raise RuntimeError("forced post-commit progress failure")

    monkeypatch.setattr(catalog, "harem_scan_progress", fail_progress)

    with pytest.raises(RuntimeError, match="forced post-commit progress failure"):
        catalog.begin_antidisable_scan("Server", "Account")

    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT harem_scans.id, server_contexts.name, account_contexts.name,
                   harem_scans.expected_page_count, harem_scans.completed_at,
                   harem_scans.scan_kind
            FROM harem_scans
            JOIN account_contexts ON account_contexts.id = harem_scans.account_context_id
            JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
            """
        ).fetchone()

    assert tuple(row)[1:] == ("Server", "Account", None, None, "antidisable")
    recovered = CatalogRepository(database_path).harem_scan_progress(row["id"])
    assert recovered is not None
    assert recovered.id == row["id"]
    assert recovered.scan_kind == "antidisable"


def test_antidisable_helper_uses_supplied_connection_and_returns_actual_ids(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        imported = catalog._import_antidisable_page_with_connection(
            connection,
            page=ANTIDISABLE_PAGE,
            scan_id=None,
            server=" Server ",
            account=" Account ",
            raw="raw antidisable",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert isinstance(imported, _AntidisablePageImportConnectionResult)
        assert [field.name for field in fields(imported)] == ["import_event_id", "scan_id"]
        assert imported.import_event_id > 0
        assert imported.scan_id is None
        assert connection.in_transaction is True
        assert _antidisable_counts(connection)["antidisable_series_observations"] == 3
        with connect(database_path) as observer:
            assert _antidisable_counts(observer) == {
                "import_events": 0,
                "server_contexts": 0,
                "account_contexts": 0,
                "harem_scans": 0,
                "harem_scan_pages": 0,
                "antidisable_series_observations": 0,
                "discord_projection_links": 0,
                "discord_source_events": 0,
                "discord_source_event_server_attributions": 0,
                "discord_source_event_account_attributions": 0,
                "discord_processing_attempts": 0,
            }
        connection.commit()

    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT kind FROM import_events WHERE id = ?", (imported.import_event_id,)
        ).fetchone()[0] == "antidisable"
        assert connection.execute(
            "SELECT COUNT(*) FROM antidisable_series_observations WHERE import_event_id = ?",
            (imported.import_event_id,),
        ).fetchone()[0] == 3


def test_antidisable_helper_rollback_removes_new_rows(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)

    with connect(database_path) as connection:
        catalog._import_antidisable_page_with_connection(
            connection,
            page=ANTIDISABLE_PAGE,
            scan_id=None,
            server="Server",
            account="Account",
            raw="rollback antidisable",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        connection.rollback()

    with connect(database_path) as connection:
        assert _antidisable_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "harem_scans": 0,
            "harem_scan_pages": 0,
            "antidisable_series_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }


def test_antidisable_helper_rollback_preserves_existing_scan_and_contexts(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    scan = catalog.begin_antidisable_scan("Server", "Account")

    with connect(database_path) as connection:
        before = _antidisable_counts(connection)
        existing = connection.execute(
            "SELECT account_context_id, expected_page_count FROM harem_scans WHERE id = ?",
            (scan.id,),
        ).fetchone()

    with connect(database_path) as connection:
        imported = catalog._import_antidisable_page_with_connection(
            connection,
            page=ANTIDISABLE_PAGE,
            scan_id=scan.id,
            server=" SERVER ",
            account=" ACCOUNT ",
            raw="rolled back page",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert imported.scan_id == scan.id
        connection.rollback()

    with connect(database_path) as connection:
        after = _antidisable_counts(connection)
        current = connection.execute(
            "SELECT account_context_id, expected_page_count FROM harem_scans WHERE id = ?",
            (scan.id,),
        ).fetchone()
        assert tuple(current) == tuple(existing)
        assert after == before


def test_antidisable_scanned_pages_preserve_order_nulls_and_scan_state(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    scan = catalog.begin_antidisable_scan("Server", "Account")

    with connect(database_path) as connection:
        second = catalog._import_antidisable_page_with_connection(
            connection,
            page=ANTIDISABLE_CONTINUATION_PAGE,
            scan_id=scan.id,
            server="Server",
            account="Account",
            raw="page two",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert second.scan_id == scan.id
        assert connection.execute(
            "SELECT expected_page_count FROM harem_scans WHERE id = ?", (scan.id,)
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT page_number FROM harem_scan_pages WHERE harem_scan_id = ?", (scan.id,)
        ).fetchone()[0] == 2
        continuation_rows = connection.execute(
            """
            SELECT series_name, antidisabled_character_count
            FROM antidisable_series_observations
            WHERE import_event_id = ?
            ORDER BY id
            """,
            (second.import_event_id,),
        ).fetchall()
        assert [tuple(row) for row in continuation_rows] == [
            ("Series C", None),
            ("Series A", None),
        ]
        with connect(database_path) as observer:
            assert observer.execute(
                "SELECT COUNT(*) FROM harem_scan_pages WHERE harem_scan_id = ?", (scan.id,)
            ).fetchone()[0] == 0
        connection.commit()

    first = catalog.import_antidisable_page(
        ANTIDISABLE_PAGE,
        "Server",
        "Account",
        "page one",
        "discord",
        scan.id,
    )
    assert first.scan_id == scan.id
    progress = catalog.harem_scan_progress(scan.id)
    assert progress is not None
    assert progress.imported_pages == (1, 2)
    assert progress.is_complete is True
    with connect(database_path) as connection:
        pages = connection.execute(
            "SELECT page_number, import_event_id FROM harem_scan_pages WHERE harem_scan_id = ? "
            "ORDER BY page_number",
            (scan.id,),
        ).fetchall()
        assert [tuple(row) for row in pages] == [(1, first.import_event_id), (2, second.import_event_id)]
        rows = connection.execute(
            """
            SELECT series_name, antidisabled_character_count
            FROM antidisable_series_observations
            WHERE harem_scan_id = ?
            ORDER BY id
            """,
            (scan.id,),
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("Series C", None),
            ("Series A", None),
            ("Series B", 2_614),
            ("Series A", 2_614),
            ("Series B", 2_614),
        ]

    catalog.complete_antidisable_scan(scan.id)
    assert catalog.antidisable_series("Server", "Account") == ("Series A", "Series B", "Series C")


def test_antidisable_scan_completion_uses_supplied_connection_and_persists_result(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    scan = catalog.begin_antidisable_scan("Server", "Account")
    imported = catalog.import_antidisable_page(
        ANTIDISABLE_PAGE.model_copy(update={"page_count": 1}),
        "Server",
        "Account",
        "complete antidisable page",
        "test",
        scan.id,
    )
    original_helper = catalog._harem_scan_progress_with_connection
    helper_calls: list[tuple[int, bool]] = []

    def observed_helper(connection: sqlite3.Connection, scan_id: int):
        helper_calls.append((id(connection), connection.in_transaction))
        return original_helper(connection, scan_id)

    def unexpected_connection():
        raise AssertionError("completion opened an independent repository connection")

    monkeypatch.setattr(catalog, "_harem_scan_progress_with_connection", observed_helper)
    monkeypatch.setattr(catalog, "_connection", unexpected_connection)

    completed = catalog.complete_antidisable_scan(scan.id)

    assert len(helper_calls) == 2
    assert helper_calls[0][0] == helper_calls[1][0]
    assert [in_transaction for _connection_id, in_transaction in helper_calls] == [True, True]
    assert completed.scan_kind == "antidisable"
    assert completed.expected_page_count == 1
    assert completed.imported_pages == (1,)
    assert completed.completed_at is not None
    with connect(database_path) as connection:
        durable = original_helper(connection, scan.id)
        pages = connection.execute(
            "SELECT page_number, import_event_id FROM harem_scan_pages "
            "WHERE harem_scan_id = ? ORDER BY page_number",
            (scan.id,),
        ).fetchall()
    assert durable == completed
    assert [tuple(row) for row in pages] == [(1, imported.import_event_id)]


def test_antidisable_scan_completion_rejects_wrong_kind_and_incomplete_without_changes(
    tmp_path,
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    wrong_kind = catalog.begin_harem_scan("Server", "Account", "keys")
    wrong_kind_page = catalog.import_harem_key_page(
        HaremKeyPage(
            page_number=1,
            page_count=1,
            entries=(HaremKeyEntry(name="Wrong Kind", key_type="silver", key_count=1),),
        ),
        "Server",
        "Account",
        "wrong-kind page",
        "test",
        wrong_kind.id,
    )
    incomplete = catalog.begin_antidisable_scan("Server", "Account")
    incomplete_page = catalog.import_antidisable_page(
        ANTIDISABLE_PAGE,
        "Server",
        "Account",
        "incomplete antidisable page",
        "test",
        incomplete.id,
    )

    with pytest.raises(ValueError, match="The scan is not an antidisable scan"):
        catalog.complete_antidisable_scan(wrong_kind.id)
    with pytest.raises(ValueError, match="Antidisable scan is incomplete"):
        catalog.complete_antidisable_scan(incomplete.id)

    with connect(database_path) as connection:
        scans = connection.execute(
            "SELECT id, completed_at FROM harem_scans WHERE id IN (?, ?) ORDER BY id",
            (wrong_kind.id, incomplete.id),
        ).fetchall()
        pages = connection.execute(
            "SELECT harem_scan_id, page_number, import_event_id FROM harem_scan_pages "
            "WHERE harem_scan_id IN (?, ?) ORDER BY harem_scan_id, page_number",
            (wrong_kind.id, incomplete.id),
        ).fetchall()
    assert [tuple(row) for row in scans] == [(wrong_kind.id, None), (incomplete.id, None)]
    assert [tuple(row) for row in pages] == [
        (wrong_kind.id, 1, wrong_kind_page.import_event_id),
        (incomplete.id, 1, incomplete_page.import_event_id),
    ]


def test_antidisable_scan_completion_update_failure_rolls_back_and_same_database_recovers(
    tmp_path,
) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    request_message = _receive_request(discord)
    scan = catalog.begin_antidisable_scan("Server", "Account")
    discord.create_antidisable_workflow(
        scan_id=scan.id,
        request_message_aggregate_key=request_message,
        requesting_user_id="known-user",
        created_at=OBSERVED_AT,
        expires_at=FINISHED_AT,
    )
    imported = catalog.import_antidisable_page(
        ANTIDISABLE_PAGE.model_copy(update={"page_count": 1}),
        "Server",
        "Account",
        "rollback antidisable page",
        "test",
        scan.id,
    )
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_antidisable_scan_completion
            BEFORE UPDATE OF completed_at ON harem_scans
            WHEN NEW.completed_at IS NOT NULL
            BEGIN
                SELECT RAISE(FAIL, 'forced antidisable scan completion failure');
            END
            """
        )

    with pytest.raises(
        sqlite3.IntegrityError, match="forced antidisable scan completion failure"
    ):
        catalog.complete_antidisable_scan(scan.id)

    with connect(database_path) as connection:
        progress = catalog._harem_scan_progress_with_connection(connection, scan.id)
        pages = connection.execute(
            "SELECT page_number, import_event_id FROM harem_scan_pages WHERE harem_scan_id = ?",
            (scan.id,),
        ).fetchall()
        workflow_count = connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_workflows WHERE harem_scan_id = ?",
            (scan.id,),
        ).fetchone()[0]
        connection.execute("DROP TRIGGER fail_antidisable_scan_completion")
    assert progress is not None and progress.completed_at is None
    assert [tuple(row) for row in pages] == [(1, imported.import_event_id)]
    assert workflow_count == 1

    recovered = catalog.complete_antidisable_scan(scan.id)

    assert recovered.id == scan.id
    assert recovered.scan_kind == "antidisable"
    assert recovered.imported_pages == (1,)
    assert recovered.completed_at is not None


def test_antidisable_scan_completion_final_read_failure_rolls_back_update(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    scan = catalog.begin_antidisable_scan("Server", "Account")
    catalog.import_antidisable_page(
        ANTIDISABLE_PAGE.model_copy(update={"page_count": 1}),
        "Server",
        "Account",
        "final-read antidisable page",
        "test",
        scan.id,
    )
    original_helper = catalog._harem_scan_progress_with_connection
    failure = RuntimeError("forced final antidisable progress read failure")
    helper_call_count = 0

    def fail_final_read(connection: sqlite3.Connection, scan_id: int):
        nonlocal helper_call_count
        helper_call_count += 1
        if helper_call_count == 2:
            raise failure
        return original_helper(connection, scan_id)

    monkeypatch.setattr(catalog, "_harem_scan_progress_with_connection", fail_final_read)

    with pytest.raises(RuntimeError) as raised:
        catalog.complete_antidisable_scan(scan.id)

    assert raised.value is failure
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT expected_page_count, completed_at FROM harem_scans WHERE id = ?",
            (scan.id,),
        ).fetchone()
        pages = connection.execute(
            "SELECT page_number FROM harem_scan_pages WHERE harem_scan_id = ?",
            (scan.id,),
        ).fetchall()
    assert tuple(row) == (1, None)
    assert [row["page_number"] for row in pages] == [1]


def test_antidisable_scan_completion_serializes_before_competing_page_writer(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    scan = catalog.begin_antidisable_scan("Server", "Account")
    catalog.import_antidisable_page(
        ANTIDISABLE_PAGE.model_copy(update={"page_count": 1}),
        "Server",
        "Account",
        "initial antidisable page",
        "test",
        scan.id,
    )
    original_progress_helper = catalog._harem_scan_progress_with_connection
    original_page_helper = catalog._import_antidisable_page_with_connection
    validation_finished = threading.Event()
    release_completion = threading.Event()
    writer_started = threading.Event()
    writer_entered_callback = threading.Event()
    completion_failures: list[BaseException] = []
    writer_failures: list[BaseException] = []
    progress_call_count = 0

    def pause_after_validation(connection: sqlite3.Connection, scan_id: int):
        nonlocal progress_call_count
        progress = original_progress_helper(connection, scan_id)
        progress_call_count += 1
        if progress_call_count == 1:
            validation_finished.set()
            assert release_completion.wait(_THREAD_TIMEOUT), "completion was not released"
        return progress

    def observe_page_callback(connection: sqlite3.Connection, **kwargs):
        writer_entered_callback.set()
        return original_page_helper(connection, **kwargs)

    monkeypatch.setattr(catalog, "_harem_scan_progress_with_connection", pause_after_validation)
    monkeypatch.setattr(catalog, "_import_antidisable_page_with_connection", observe_page_callback)

    def complete_scan() -> None:
        try:
            catalog.complete_antidisable_scan(scan.id)
        except BaseException as exc:
            completion_failures.append(exc)

    def import_competing_page() -> None:
        writer_started.set()
        try:
            catalog.import_antidisable_page(
                ANTIDISABLE_CONTINUATION_PAGE,
                "Server",
                "Account",
                "competing antidisable page",
                "test",
                scan.id,
            )
        except BaseException as exc:
            writer_failures.append(exc)

    completion_thread = threading.Thread(target=complete_scan)
    completion_thread.start()
    assert validation_finished.wait(_THREAD_TIMEOUT), "completion did not finish validation"

    writer_thread = threading.Thread(target=import_competing_page)
    writer_thread.start()
    assert writer_started.wait(_THREAD_TIMEOUT), "competing page writer did not start"
    assert not writer_entered_callback.is_set()

    release_completion.set()
    completion_thread.join(timeout=_THREAD_TIMEOUT)
    writer_thread.join(timeout=_THREAD_TIMEOUT)

    assert not completion_thread.is_alive(), "completion worker did not terminate"
    assert not writer_thread.is_alive(), "page writer worker did not terminate"
    assert completion_failures == []
    assert writer_entered_callback.is_set()
    assert len(writer_failures) == 1
    assert isinstance(writer_failures[0], ValueError)
    assert str(writer_failures[0]) == (
        "Antidisable scan is already complete; begin a new scan to refresh it."
    )
    with connect(database_path) as connection:
        completed_at = connection.execute(
            "SELECT completed_at FROM harem_scans WHERE id = ?", (scan.id,)
        ).fetchone()[0]
        pages = connection.execute(
            "SELECT page_number FROM harem_scan_pages WHERE harem_scan_id = ? ORDER BY page_number",
            (scan.id,),
        ).fetchall()
        competing_events = connection.execute(
            "SELECT COUNT(*) FROM import_events WHERE raw_message = 'competing antidisable page'"
        ).fetchone()[0]
    assert completed_at is not None
    assert [row["page_number"] for row in pages] == [1]
    assert competing_events == 0


def test_antidisable_scan_and_workflow_helpers_commit_atomically_on_one_connection(
    tmp_path,
) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    request_message = _receive_request(discord)

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        scan_id = catalog._begin_antidisable_scan_with_connection(
            connection,
            server="Server",
            account="Account",
            observed_at=OBSERVED_AT,
        )
        workflow_result = discord._create_antidisable_workflow_with_connection(
            connection,
            scan_id=scan_id,
            request_message_aggregate_key=request_message,
            requesting_user_id="known-user",
            created_at=OBSERVED_AT,
            expires_at=FINISHED_AT,
        )
        assert connection.in_transaction is True
        assert connection.execute(
            "SELECT COUNT(*) FROM harem_scans WHERE id = ?", (scan_id,)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_workflows WHERE harem_scan_id = ?",
            (scan_id,),
        ).fetchone()[0] == 1
        assert workflow_result.created is True
        with connect(database_path) as observer:
            assert observer.execute(
                "SELECT COUNT(*) FROM harem_scans WHERE id = ?", (scan_id,)
            ).fetchone()[0] == 0
            assert observer.execute(
                "SELECT COUNT(*) FROM discord_antidisable_workflows WHERE harem_scan_id = ?",
                (scan_id,),
            ).fetchone()[0] == 0
        connection.commit()

    with connect(database_path) as observer:
        assert observer.execute(
            "SELECT COUNT(*) FROM harem_scans WHERE id = ?", (scan_id,)
        ).fetchone()[0] == 1
        assert observer.execute(
            "SELECT COUNT(*) FROM discord_antidisable_workflows WHERE harem_scan_id = ?",
            (scan_id,),
        ).fetchone()[0] == 1


def test_antidisable_scan_and_workflow_helpers_rollback_atomically_on_one_connection(
    tmp_path,
) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    request_message = _receive_request(discord)

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        scan_id = catalog._begin_antidisable_scan_with_connection(
            connection,
            server="Server",
            account="Account",
            observed_at=OBSERVED_AT,
        )
        discord._create_antidisable_workflow_with_connection(
            connection,
            scan_id=scan_id,
            request_message_aggregate_key=request_message,
            requesting_user_id="known-user",
            created_at=OBSERVED_AT,
            expires_at=FINISHED_AT,
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM harem_scans WHERE id = ?", (scan_id,)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM discord_antidisable_workflows WHERE harem_scan_id = ?",
            (scan_id,),
        ).fetchone()[0] == 1
        connection.rollback()

    with connect(database_path) as observer:
        assert observer.execute(
            "SELECT COUNT(*) FROM harem_scans WHERE id = ?", (scan_id,)
        ).fetchone()[0] == 0
        assert observer.execute(
            "SELECT COUNT(*) FROM discord_antidisable_workflows WHERE harem_scan_id = ?",
            (scan_id,),
        ).fetchone()[0] == 0
        assert observer.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
        assert observer.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0
        assert observer.execute("SELECT COUNT(*) FROM discord_message_aggregates").fetchone()[0] == 1


def test_antidisable_duplicate_and_invalid_pages_keep_existing_rejections(tmp_path) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    scan = catalog.begin_antidisable_scan("Server", "Account")
    catalog.import_antidisable_page(
        ANTIDISABLE_PAGE, "Server", "Account", "first", "discord", scan.id
    )
    with connect(database_path) as connection:
        before = _antidisable_counts(connection)

    with pytest.raises(ValueError, match="already contains that page"):
        catalog.import_antidisable_page(
            ANTIDISABLE_PAGE, "Server", "Account", "duplicate", "discord", scan.id
        )
    with pytest.raises(ValueError, match="include its Page X / Y"):
        catalog.import_antidisable_page(
            ANTIDISABLE_PAGE.model_copy(update={"page_number": None}),
            "Server",
            "Account",
            "invalid",
            "discord",
            scan.id,
        )
    with pytest.raises(ValueError, match="page count does not match"):
        catalog.import_antidisable_page(
            ANTIDISABLE_CONTINUATION_PAGE.model_copy(update={"page_count": 3}),
            "Server",
            "Account",
            "mismatch",
            "discord",
            scan.id,
        )

    with connect(database_path) as connection:
        assert _antidisable_counts(connection) == before

    other_scan = catalog.begin_antidisable_scan("Server", "Account")
    other = catalog.import_antidisable_page(
        ANTIDISABLE_PAGE, "Server", "Account", "other scan", "discord", other_scan.id
    )
    assert other.scan_id == other_scan.id
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM harem_scan_pages WHERE page_number = 1"
        ).fetchone()[0] == 2


def test_public_profile_wrapper_rolls_back_helper_failure_and_remains_usable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    original_helper = catalog._profile_repository._import_profile_with_connection

    def fail_after_write(connection: sqlite3.Connection, **kwargs):
        original_helper(connection, **kwargs)
        raise RuntimeError("forced profile import failure")

    monkeypatch.setattr(catalog._profile_repository, "_import_profile_with_connection", fail_after_write)

    with pytest.raises(RuntimeError, match="forced profile import failure"):
        catalog.import_profile(PROFILE, "Server", "Account", "failed payload", "discord")

    with connect(database_path) as connection:
        assert _profile_projection_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "profile_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }

    monkeypatch.setattr(catalog._profile_repository, "_import_profile_with_connection", original_helper)
    result = catalog.import_profile(
        PROFILE, "Server", "Account", "successful payload", "discord"
    )
    assert result.import_event_id > 0
    with connect(database_path) as connection:
        assert _profile_projection_counts(connection)["profile_observations"] == 1


def test_profile_coordinator_runner_commits_without_public_wrapper_nesting(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    coordinator = ProfileProjectionCoordinator(catalog, discord)
    assert catalog._database_path == coordinator._database_path == database_path
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_account_scoped_attribution(discord, source_event_id)
    monkeypatch.setattr(
        catalog,
        "import_profile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("coordinator called public profile wrapper")
        ),
    )
    profile = PROFILE.model_copy(update={"profile_name": "Account"})

    result = coordinator.coordinate_profile(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        profile=profile,
        server=" Server ",
        account=" Account ",
        raw="profile payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )

    assert result == ProfileProjectionResult(
        imported_count=1,
        import_event_id=result.import_event_id,
        profile_observation_id=result.profile_observation_id,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=("profile_observations", result.profile_observation_id),
    )
    with connect(database_path) as connection:
        assert _profile_projection_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "profile_observations": 1,
            "discord_projection_links": 1,
            "discord_source_events": 1,
            "discord_source_event_server_attributions": 1,
            "discord_source_event_account_attributions": 1,
            "discord_processing_attempts": 1,
        }
        assert connection.execute(
            "SELECT status FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()[0] == "succeeded"
        assert connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()[0] == "succeeded"
        assert connection.execute(
            """
            SELECT state, projection_table, projection_row_id
            FROM discord_projection_links WHERE source_event_id = ?
            """,
            (source_event_id,),
        ).fetchone()[:] == (
            "completed",
            "profile_observations",
            result.profile_observation_id,
        )


def test_profile_coordinator_runner_rolls_back_partial_projection(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    coordinator = ProfileProjectionCoordinator(catalog, discord)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_account_scoped_attribution(discord, source_event_id)
    monkeypatch.setattr(
        coordinator,
        "_complete_projection_link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("forced profile projection failure")
        ),
    )
    profile = PROFILE.model_copy(update={"profile_name": "Account"})

    with pytest.raises(RuntimeError, match="forced profile projection failure"):
        coordinator.coordinate_profile(
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            profile=profile,
            server="Server",
            account="Account",
            raw="profile payload",
            source="discord",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        )

    with connect(database_path) as connection:
        assert _profile_projection_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "profile_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 1,
            "discord_source_event_server_attributions": 1,
            "discord_source_event_account_attributions": 1,
            "discord_processing_attempts": 1,
        }
        assert connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()[:] == ("processing", None)
        assert connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()[0] == "processing"


def test_public_server_settings_wrapper_rolls_back_helper_failure_and_remains_usable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    original_helper = catalog._server_settings_repository._import_server_settings_with_connection

    def fail_after_write(connection: sqlite3.Connection, **kwargs):
        original_helper(connection, **kwargs)
        raise RuntimeError("forced server-settings import failure")

    monkeypatch.setattr(
        catalog._server_settings_repository,
        "_import_server_settings_with_connection",
        fail_after_write,
    )

    with pytest.raises(RuntimeError, match="forced server-settings import failure"):
        catalog.import_server_settings(SETTINGS, "Server", "failed payload", "discord")

    with connect(database_path) as connection:
        assert _settings_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "server_settings_observations": 0,
            "discord_projection_links": 0,
            "discord_source_event_server_attributions": 0,
            "discord_processing_attempts": 0,
        }

    monkeypatch.setattr(
        catalog._server_settings_repository,
        "_import_server_settings_with_connection",
        original_helper,
    )
    result = catalog.import_server_settings(
        SETTINGS, "Server", "successful payload", "discord"
    )
    assert result.import_event_id > 0
    with connect(database_path) as connection:
        assert _settings_counts(connection)["server_settings_observations"] == 1


def test_settings_coordinator_runner_commits_without_public_wrapper_nesting(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    coordinator = SettingsProjectionCoordinator(catalog, discord)
    assert catalog._database_path == coordinator._database_path == database_path
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_server_attribution(discord, source_event_id)
    monkeypatch.setattr(
        catalog,
        "import_server_settings",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("coordinator called public server-settings wrapper")
        ),
    )

    result = coordinator.coordinate_settings(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        settings=SETTINGS,
        server="Server",
        raw="settings payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )

    assert result == SettingsProjectionResult(
        imported_count=1,
        import_event_id=result.import_event_id,
        server_settings_observation_id=result.server_settings_observation_id,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=(
            "server_settings_observations",
            result.server_settings_observation_id,
        ),
    )
    with connect(database_path) as connection:
        assert _settings_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "server_settings_observations": 1,
            "discord_projection_links": 1,
            "discord_source_event_server_attributions": 1,
            "discord_processing_attempts": 1,
        }
        assert connection.execute(
            "SELECT status FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()[0] == "succeeded"
        assert connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()[0] == "succeeded"
        assert connection.execute(
            """
            SELECT state, projection_table, projection_row_id
            FROM discord_projection_links WHERE source_event_id = ?
            """,
            (source_event_id,),
        ).fetchone()[:] == (
            "completed",
            "server_settings_observations",
            result.server_settings_observation_id,
        )


def test_settings_coordinator_runner_rolls_back_partial_projection(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    coordinator = SettingsProjectionCoordinator(catalog, discord)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_server_attribution(discord, source_event_id)
    monkeypatch.setattr(
        coordinator,
        "_complete_projection_link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("forced settings projection failure")
        ),
    )

    with pytest.raises(RuntimeError, match="forced settings projection failure"):
        coordinator.coordinate_settings(
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            settings=SETTINGS,
            server="Server",
            raw="settings payload",
            source="discord",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        )

    with connect(database_path) as connection:
        assert _settings_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "server_settings_observations": 0,
            "discord_projection_links": 0,
            "discord_source_event_server_attributions": 1,
            "discord_processing_attempts": 1,
        }
        assert connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()[:] == ("processing", None)
        assert connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()[0] == "processing"


def test_public_timer_state_wrapper_rolls_back_helper_failure_and_remains_usable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, _discord = _repositories(tmp_path)
    original_helper = catalog._import_timer_state_with_connection

    def fail_after_write(connection: sqlite3.Connection, **kwargs):
        original_helper(connection, **kwargs)
        raise RuntimeError("forced timer-state import failure")

    monkeypatch.setattr(catalog, "_import_timer_state_with_connection", fail_after_write)

    with pytest.raises(RuntimeError, match="forced timer-state import failure"):
        catalog.import_timer_state(
            TIMER_STATE, "Server", "Account", "failed payload", "discord"
        )

    with connect(database_path) as connection:
        assert _timer_state_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "timer_state_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 0,
            "discord_source_event_server_attributions": 0,
            "discord_source_event_account_attributions": 0,
            "discord_processing_attempts": 0,
        }

    monkeypatch.setattr(catalog, "_import_timer_state_with_connection", original_helper)
    result = catalog.import_timer_state(
        TIMER_STATE, "Server", "Account", "successful payload", "discord"
    )
    assert result.import_event_id > 0
    with connect(database_path) as connection:
        assert _timer_state_counts(connection)["timer_state_observations"] == 1


def test_timer_coordinator_runner_commits_without_public_wrapper_nesting(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    coordinator = TimerProjectionCoordinator(catalog, discord)
    assert catalog._database_path == coordinator._database_path == database_path
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_account_scoped_attribution(discord, source_event_id)
    monkeypatch.setattr(
        catalog,
        "import_timer_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("coordinator called public timer-state wrapper")
        ),
    )

    result = coordinator.coordinate_timer_state(
        source_event_id=source_event_id,
        attempt_id=attempt_id,
        state=TIMER_STATE,
        server=" Server ",
        account=" Account ",
        raw="timer payload",
        source="discord",
        observed_at=OBSERVED_AT,
        finished_at=FINISHED_AT,
    )

    assert result == TimerProjectionResult(
        imported_count=1,
        import_event_id=result.import_event_id,
        timer_state_observation_id=result.timer_state_observation_id,
        replay_skipped=False,
        durable_success_recorded=True,
        projection_target=(
            "timer_state_observations",
            result.timer_state_observation_id,
        ),
    )
    with connect(database_path) as connection:
        assert _timer_state_counts(connection) == {
            "import_events": 1,
            "server_contexts": 1,
            "account_contexts": 1,
            "timer_state_observations": 1,
            "discord_projection_links": 1,
            "discord_source_events": 1,
            "discord_source_event_server_attributions": 1,
            "discord_source_event_account_attributions": 1,
            "discord_processing_attempts": 1,
        }
        assert connection.execute(
            "SELECT status FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()[0] == "succeeded"
        assert connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()[0] == "succeeded"
        assert connection.execute(
            """
            SELECT state, projection_table, projection_row_id
            FROM discord_projection_links WHERE source_event_id = ?
            """,
            (source_event_id,),
        ).fetchone()[:] == (
            "completed",
            "timer_state_observations",
            result.timer_state_observation_id,
        )


def test_timer_coordinator_runner_rolls_back_partial_projection(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, catalog, discord = _repositories(tmp_path)
    coordinator = TimerProjectionCoordinator(catalog, discord)
    source_event_id, attempt_id = _receive_and_begin(discord)
    _record_account_scoped_attribution(discord, source_event_id)
    monkeypatch.setattr(
        coordinator,
        "_complete_projection_link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("forced timer projection failure")
        ),
    )

    with pytest.raises(RuntimeError, match="forced timer projection failure"):
        coordinator.coordinate_timer_state(
            source_event_id=source_event_id,
            attempt_id=attempt_id,
            state=TIMER_STATE,
            server="Server",
            account="Account",
            raw="timer payload",
            source="discord",
            observed_at=OBSERVED_AT,
            finished_at=FINISHED_AT,
        )

    with connect(database_path) as connection:
        assert _timer_state_counts(connection) == {
            "import_events": 0,
            "server_contexts": 0,
            "account_contexts": 0,
            "timer_state_observations": 0,
            "discord_projection_links": 0,
            "discord_source_events": 1,
            "discord_source_event_server_attributions": 1,
            "discord_source_event_account_attributions": 1,
            "discord_processing_attempts": 1,
        }
        assert connection.execute(
            "SELECT status, legacy_import_event_id FROM discord_source_events WHERE id = ?",
            (source_event_id,),
        ).fetchone()[:] == ("processing", None)
        assert connection.execute(
            "SELECT status FROM discord_processing_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()[0] == "processing"
