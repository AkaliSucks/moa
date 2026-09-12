import json
import sqlite3
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import MudapinSnapshot
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories import mudapins_repository as repository_module
from moa.repositories.mudapins_repository import (
    MudapinsRepository,
    _MudapinImportConnectionResult,
)


OBSERVED_AT = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


def _initialize(path):
    CatalogRepository(path)
    return MudapinsRepository(path)


def test_connection_result_is_frozen_slotted_with_exact_fields() -> None:
    assert is_dataclass(_MudapinImportConnectionResult)
    assert _MudapinImportConnectionResult.__dataclass_params__.frozen is True
    assert [field.name for field in fields(_MudapinImportConnectionResult)] == [
        "import_event_id",
        "mudapin_observation_id",
    ]
    assert not hasattr(_MudapinImportConnectionResult(1, 2), "__dict__")


def test_constructor_requires_initialized_explicit_path_and_preserves_absence(tmp_path) -> None:
    database_path = tmp_path / "mudapins.db"
    repository = MudapinsRepository(database_path)

    assert repository.database_path == database_path
    assert not database_path.exists()

    with pytest.raises(sqlite3.OperationalError, match="unable to open database file"):
        repository.mudapins("Server", "Account")

    initialized = _initialize(database_path)
    assert initialized.mudapins("Server", "Account") is None


def test_standalone_write_read_preserves_scope_markers_count_and_provenance(tmp_path) -> None:
    database_path = tmp_path / "mudapins.db"
    repository = _initialize(database_path)
    snapshot = MudapinSnapshot(pin_markers=("A", "B", "A"))

    result = repository.import_mudapins(
        snapshot,
        "  Server One  ",
        "  Account One  ",
        "raw payload",
        "clipboard",
    )
    other = repository.import_mudapins(
        MudapinSnapshot(pin_markers=()),
        "Server One",
        "Account Two",
        "empty payload",
        "discord",
    )

    observation = repository.mudapins(" server   one ", " account one ")
    assert observation is not None
    assert observation.snapshot.pin_markers == ("A", "B", "A")
    assert observation.server_name == "Server One"
    assert observation.account_name == "Account One"
    assert repository.mudapins("Server One", "Unknown") is None

    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        row = connection.execute(
            """
            SELECT account_context_id, pin_markers_json, pin_count, observed_at, import_event_id
            FROM mudapin_observations WHERE import_event_id = ?
            """,
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == (
            "mudapins",
            "clipboard",
            "raw payload",
            result.observed_at.isoformat(),
        )
        assert json.loads(row["pin_markers_json"]) == ["A", "B", "A"]
        assert row["pin_count"] == 3
        assert row["pin_count"] == len(json.loads(row["pin_markers_json"]))
        assert row["observed_at"] == result.observed_at.isoformat()
        assert row["import_event_id"] == result.import_event_id
        assert other.import_event_id > result.import_event_id
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 2


def test_empty_inventory_is_present_and_latest_is_id_ordered_append_only(tmp_path) -> None:
    database_path = tmp_path / "mudapins.db"
    repository = _initialize(database_path)

    first = repository.import_mudapins(
        MudapinSnapshot(pin_markers=("old",)), "Server", "Account", "first", "test"
    )
    second = repository.import_mudapins(
        MudapinSnapshot(pin_markers=()), "Server", "Account", "second", "test"
    )

    observation = repository.mudapins("Server", "Account")
    assert observation is not None
    assert observation.snapshot.pin_markers == ()
    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT id, pin_markers_json, pin_count FROM mudapin_observations ORDER BY id"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["id"] < rows[1]["id"]
        assert rows[0]["pin_markers_json"] == '["old"]'
        assert rows[0]["pin_count"] == 1
        assert rows[1]["pin_markers_json"] == "[]"
        assert rows[1]["pin_count"] == 0
        assert rows[1]["id"] > first.import_event_id - 1
        assert second.import_event_id > first.import_event_id


def test_standalone_import_owns_exactly_one_runner(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "mudapins.db"
    repository = _initialize(database_path)
    original_runner = repository_module.run_write_transaction
    calls = []

    def observed_runner(path, callback):
        calls.append(path)
        return original_runner(path, callback)

    monkeypatch.setattr(repository_module, "run_write_transaction", observed_runner)
    repository.import_mudapins(MudapinSnapshot(pin_markers=("A",)), "Server", "Account", "raw", "test")

    assert calls == [database_path]


def test_supplied_connection_is_neutral_and_caller_commit_persists(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "mudapins.db"
    repository = _initialize(database_path)

    def unexpected_connection(*args, **kwargs):
        raise AssertionError("supplied helper opened an independent connection")

    def unexpected_runner(*args, **kwargs):
        raise AssertionError("supplied helper started an independent transaction")

    monkeypatch.setattr(repository_module, "connect_read_only", unexpected_connection)
    monkeypatch.setattr(repository_module, "run_write_transaction", unexpected_runner)

    with connect(database_path) as connection:
        imported = repository._import_mudapins_with_connection(
            connection,
            snapshot=MudapinSnapshot(pin_markers=("A", "A")),
            server="Server",
            account="Account",
            raw="raw",
            source="test",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction
        connection.commit()

    monkeypatch.undo()
    assert repository.mudapins("Server", "Account").snapshot.pin_markers == ("A", "A")
    assert imported.import_event_id > 0


def test_supplied_connection_rollback_removes_all_rows_and_same_db_recovers(tmp_path) -> None:
    database_path = tmp_path / "mudapins.db"
    repository = _initialize(database_path)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            repository._import_mudapins_with_connection(
                connection,
                snapshot=MudapinSnapshot(pin_markers=("A",)),
                server="Server",
                account="Account",
                raw="raw",
                source="test",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM mudapin_observations").fetchone()[0] == 0

    result = repository.import_mudapins(
        MudapinSnapshot(pin_markers=("recovered",)), "Server", "Account", "recovery", "test"
    )
    assert result.import_event_id > 0
    assert repository.mudapins("Server", "Account").snapshot.pin_markers == ("recovered",)
