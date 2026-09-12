import json
import sqlite3
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import TimerStateSnapshot
from moa.repositories import timer_state_repository as repository_module
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.timer_state_repository import (
    TimerStateRepository,
    _TimerStateImportConnectionResult,
)


OBSERVED_AT = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


TIMER_STATE_FIELDS = (
    "can_claim_now",
    "claim_reset_minutes",
    "rolls_left",
    "rolls_reset_minutes",
    "rolls_reset_stock",
    "vote_reset_minutes",
    "daily_reset_minutes",
    "daily_kakera_ready",
    "rt_available",
    "can_react_kakera_now",
    "reaction_power_percent",
    "kakera_button_power_cost_percent",
    "soulmate_button_power_cost_percent",
    "kakera_stock",
    "gold_key_stock_remaining",
    "gold_key_reset_minutes",
    "bku_reset_probability_percent",
    "oh_remaining",
    "oc_remaining",
    "oq_remaining",
    "oq_stored",
    "ot_remaining",
    "ouro_refill_minutes",
    "rolls_reset_status",
    "rolls_per_hour_limit",
    "rt_reset_minutes",
)


def _complete_timer_state() -> TimerStateSnapshot:
    return TimerStateSnapshot(
        can_claim_now=True,
        claim_reset_minutes=0,
        rolls_left=17,
        rolls_reset_minutes=None,
        rolls_reset_stock=0,
        vote_reset_minutes=650,
        daily_reset_minutes=None,
        daily_kakera_ready=False,
        rt_available=None,
        can_react_kakera_now=True,
        reaction_power_percent=0,
        kakera_button_power_cost_percent=36,
        soulmate_button_power_cost_percent=18,
        kakera_stock=12114,
        gold_key_stock_remaining=None,
        gold_key_reset_minutes=6,
        bku_reset_probability_percent=10,
        oh_remaining=0,
        oc_remaining=3,
        oq_remaining=0,
        oq_stored=1,
        ot_remaining=8,
        ouro_refill_minutes=918,
        rolls_reset_status="limited_timer",
        rolls_per_hour_limit=17,
        rt_reset_minutes=None,
    )


def test_timer_state_round_trips_complete_snapshot(tmp_path) -> None:
    database_path = tmp_path / "timer-state-round-trip.db"
    catalog = CatalogRepository(database_path)
    state = _complete_timer_state()

    assert tuple(state.model_dump()) == TIMER_STATE_FIELDS
    result = catalog.import_timer_state(
        state,
        "Server A",
        "Account X",
        "timer payload",
        "test",
    )

    observation = catalog.timer_state("server a", "account x")

    assert observation is not None
    assert observation.snapshot == state
    assert observation.snapshot.model_dump() == state.model_dump()
    assert set(observation.snapshot.model_dump()) == set(TIMER_STATE_FIELDS)
    assert observation.snapshot.rolls_reset_status == "limited_timer"
    assert observation.snapshot.oq_stored == 1
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT snapshot_json FROM timer_state_observations WHERE import_event_id = ?",
            (result.import_event_id,),
        ).fetchone()

    assert row is not None
    decoded = json.loads(row["snapshot_json"])
    assert set(decoded) == set(TIMER_STATE_FIELDS)
    assert decoded == state.model_dump()


def test_timer_state_preserves_absence_latest_and_account_within_server_scope(tmp_path) -> None:
    database_path = tmp_path / "timer-state-scope.db"
    catalog = CatalogRepository(database_path)
    snapshot_a = _complete_timer_state()
    snapshot_b = snapshot_a.model_copy(
        update={
            "can_claim_now": False,
            "claim_reset_minutes": 9,
            "rolls_left": 1,
            "rolls_reset_status": "timer",
            "daily_kakera_ready": True,
            "oq_stored": 0,
        }
    )
    snapshot_c = snapshot_a.model_copy(
        update={
            "can_claim_now": None,
            "claim_reset_minutes": 27,
            "rolls_left": 4,
            "rolls_reset_status": "vote_required",
            "daily_kakera_ready": None,
            "oq_stored": 2,
        }
    )

    assert catalog.timer_state("Server A", "Account X") is None

    catalog.import_timer_state(snapshot_a, "Server A", "Account X", "first", "test")
    assert catalog.timer_state("server a", "account x").snapshot == snapshot_a

    catalog.import_timer_state(snapshot_b, " server a ", " ACCOUNT X ", "second", "test")
    latest_a = catalog.timer_state("SERVER A", "account x")
    assert latest_a is not None
    assert latest_a.snapshot == snapshot_b

    catalog.import_timer_state(snapshot_c, "Server B", "Account X", "third", "test")
    latest_b = catalog.timer_state("server b", "account x")
    assert latest_b is not None
    assert latest_b.snapshot == snapshot_c
    assert catalog.timer_state("server a", "account x").snapshot == snapshot_b

    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, account_context_id
            FROM timer_state_observations
            ORDER BY id
            """
        ).fetchall()

    assert len(rows) == 3
    assert rows[0]["id"] < rows[1]["id"] < rows[2]["id"]
    assert rows[0]["account_context_id"] == rows[1]["account_context_id"]
    assert rows[1]["account_context_id"] != rows[2]["account_context_id"]


def _initialized_timer_repository(database_path):
    CatalogRepository(database_path)
    return TimerStateRepository(database_path)


def test_timer_state_connection_result_is_frozen_slotted_with_exact_fields() -> None:
    assert is_dataclass(_TimerStateImportConnectionResult)
    assert _TimerStateImportConnectionResult.__dataclass_params__.frozen is True
    assert [field.name for field in fields(_TimerStateImportConnectionResult)] == [
        "import_event_id",
        "timer_state_observation_id",
    ]
    assert not hasattr(_TimerStateImportConnectionResult(1, 2), "__dict__")


def test_timer_state_repository_requires_initialized_explicit_path(tmp_path) -> None:
    database_path = tmp_path / "timer-state-uninitialized.db"
    repository = TimerStateRepository(database_path)

    assert repository.database_path == database_path
    assert not database_path.exists()
    with pytest.raises(sqlite3.OperationalError, match="unable to open database file"):
        repository.timer_state("Server", "Account")

    initialized = _initialized_timer_repository(database_path)
    assert initialized.timer_state("Server", "Account") is None


def test_timer_state_repository_standalone_write_read_preserves_scope_and_provenance(tmp_path) -> None:
    database_path = tmp_path / "timer-state-standalone.db"
    repository = _initialized_timer_repository(database_path)

    result = repository.import_timer_state(
        _complete_timer_state(),
        "  Server One  ",
        "  Account One  ",
        "raw timer payload",
        "clipboard",
    )
    observation = repository.timer_state(" server   one ", " account one ")

    assert observation is not None
    assert observation.server_name == "Server One"
    assert observation.account_name == "Account One"
    assert observation.snapshot == _complete_timer_state()
    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT kind, source, raw_message, observed_at FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
    assert tuple(event) == (
        "timer_state",
        "clipboard",
        "raw timer payload",
        result.observed_at.isoformat(),
    )


def test_timer_state_repository_standalone_import_owns_exactly_one_runner(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "timer-state-runner.db"
    repository = _initialized_timer_repository(database_path)
    original_runner = repository_module.run_write_transaction
    calls = []

    def observed_runner(path, callback):
        calls.append(path)
        return original_runner(path, callback)

    monkeypatch.setattr(repository_module, "run_write_transaction", observed_runner)
    repository.import_timer_state(_complete_timer_state(), "Server", "Account", "raw", "test")

    assert calls == [database_path]


def test_timer_state_repository_supplied_connection_is_neutral_and_caller_commit_persists(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "timer-state-connection.db"
    repository = _initialized_timer_repository(database_path)

    def unexpected_connection(*args, **kwargs):
        raise AssertionError("supplied helper opened an independent connection")

    def unexpected_runner(*args, **kwargs):
        raise AssertionError("supplied helper started an independent transaction")

    monkeypatch.setattr(repository_module, "connect_read_only", unexpected_connection)
    monkeypatch.setattr(repository_module, "run_write_transaction", unexpected_runner)

    with connect(database_path) as connection:
        imported = repository._import_timer_state_with_connection(
            connection,
            state=_complete_timer_state(),
            server="Server",
            account="Account",
            raw="raw",
            source="test",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction
        connection.commit()

    monkeypatch.undo()
    assert imported.import_event_id > 0
    assert repository.timer_state("Server", "Account") is not None


def test_timer_state_repository_supplied_connection_rolls_back_and_same_db_recovers(tmp_path) -> None:
    database_path = tmp_path / "timer-state-recovery.db"
    repository = _initialized_timer_repository(database_path)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            repository._import_timer_state_with_connection(
                connection,
                state=_complete_timer_state(),
                server="Server",
                account="Account",
                raw="failed raw",
                source="test",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM timer_state_observations").fetchone()[0] == 0

    result = repository.import_timer_state(
        _complete_timer_state(), "Server", "Account", "recovery raw", "test"
    )
    assert result.import_event_id > 0
    assert repository.timer_state("Server", "Account") is not None
