import json
import sqlite3
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import TowerStateSnapshot
from moa.repositories import tower_state_repository as repository_module
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.tower_state_repository import TowerStateRepository


TOWER_STATE = TowerStateSnapshot(
    current_level=2,
    completed_towers=3,
    next_level_cost=75_000,
    kakera_balance=7_673,
    built_perk_ids=(7, 2, 7),
)
OBSERVED_AT = datetime(2026, 1, 2, tzinfo=timezone.utc)


def _repositories(tmp_path):
    database_path = tmp_path / "tower-state.db"
    CatalogRepository(database_path)
    return database_path, TowerStateRepository(database_path)


def test_constructor_uses_explicit_path_without_bootstrap(tmp_path) -> None:
    database_path = tmp_path / "uninitialized.db"

    repository = TowerStateRepository(database_path)

    assert repository.database_path == database_path
    assert not database_path.exists()


def test_direct_import_read_latest_linkage_and_account_scope(tmp_path) -> None:
    database_path, repository = _repositories(tmp_path)

    assert repository.tower_state("Server", "Account") is None
    first = repository.import_tower_state(
        TOWER_STATE, " Server ", " Account ", "first payload", "test"
    )
    second_state = TOWER_STATE.model_copy(update={"current_level": 4})
    second = repository.import_tower_state(
        second_state, "server", "account", "second payload", "test"
    )
    repository.import_tower_state(
        TOWER_STATE, "Other Server", "Account", "other server", "test"
    )
    repository.import_tower_state(
        TOWER_STATE, "Server", "Other Account", "other account", "test"
    )

    latest = repository.tower_state(" SERVER ", " ACCOUNT ")
    assert latest is not None
    assert latest.current_level == 4
    assert latest.built_perk_ids == TOWER_STATE.built_perk_ids
    assert second.import_event_id > first.import_event_id
    assert repository.tower_state("Other Server", "Account").current_level == 2
    assert repository.tower_state("Server", "Other Account").current_level == 2

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM tower_state_observations").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 3
        row = connection.execute(
            """
            SELECT import_event_id, current_level, next_level_cost, kakera_balance,
                   completed_towers, built_perk_ids_json
            FROM tower_state_observations
            WHERE id = (SELECT MAX(id) FROM tower_state_observations WHERE account_context_id =
                (SELECT account_contexts.id FROM account_contexts
                 JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
                 WHERE server_contexts.normalized_name = 'server'
                   AND account_contexts.normalized_name = 'account'))
            """
        ).fetchone()
    assert tuple(row[:5]) == (second.import_event_id, 4, 75_000, 7_673, 3)
    assert json.loads(row["built_perk_ids_json"]) == [7, 2, 7]


@pytest.mark.parametrize(
    ("completed_towers", "stored", "stored_observed", "read_value"),
    (
        (None, 0, 0, None),
        (0, 0, 1, 0),
        (11, 11, 1, 11),
        (-1, -1, 1, -1),
    ),
)
def test_completed_towers_storage_preserves_presence_and_exact_values(
    tmp_path,
    completed_towers: int | None,
    stored: int,
    stored_observed: int,
    read_value: int | None,
) -> None:
    database_path, repository = _repositories(tmp_path)
    state = TOWER_STATE.model_copy(update={"completed_towers": completed_towers})

    result = repository.import_tower_state(state, "Server", "Account", "payload", "test")

    observation = repository.tower_state("Server", "Account")
    assert observation is not None
    assert observation.completed_towers == read_value
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT completed_towers, completed_towers_observed "
            "FROM tower_state_observations WHERE import_event_id = ?",
            (result.import_event_id,),
        ).fetchone()
    assert row[0] == stored
    assert row[1] == stored_observed


@pytest.mark.parametrize("stored_observed", (0, None))
def test_inconsistent_nonzero_completed_tower_presence_fails_closed(
    tmp_path, stored_observed: int | None
) -> None:
    database_path, repository = _repositories(tmp_path)
    result = repository.import_tower_state(
        TOWER_STATE.model_copy(update={"completed_towers": 11}),
        "Server",
        "Account",
        "payload",
        "test",
    )
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE tower_state_observations SET completed_towers_observed = ? "
            "WHERE import_event_id = ?",
            (stored_observed, result.import_event_id),
        )

    with pytest.raises(sqlite3.IntegrityError, match="inconsistent completed_towers presence"):
        repository.tower_state("Server", "Account")


def test_perks_preserve_order_duplicates_and_empty_vs_absent(tmp_path) -> None:
    database_path, repository = _repositories(tmp_path)

    assert repository.tower_state("Server", "Account") is None
    state = TOWER_STATE.model_copy(update={"built_perk_ids": ()})
    result = repository.import_tower_state(state, "Server", "Account", "empty", "test")

    observation = repository.tower_state("Server", "Account")
    assert observation is not None
    assert observation.built_perk_ids == ()
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT built_perk_ids_json FROM tower_state_observations WHERE import_event_id = ?",
            (result.import_event_id,),
        ).fetchone()
    assert json.loads(row[0]) == []


def test_standalone_import_owns_exactly_one_runner(tmp_path, monkeypatch) -> None:
    _database_path, repository = _repositories(tmp_path)
    original_runner = repository_module.run_write_transaction
    calls = []

    def observed_runner(database_path, callback):
        calls.append(database_path)
        return original_runner(database_path, callback)

    monkeypatch.setattr(repository_module, "run_write_transaction", observed_runner)

    repository.import_tower_state(TOWER_STATE, "Server", "Account", "payload", "test")

    assert calls == [repository.database_path]


def test_supplied_connection_helper_is_transaction_neutral(tmp_path, monkeypatch) -> None:
    database_path, repository = _repositories(tmp_path)

    def unexpected_connection(*args, **kwargs):
        raise AssertionError("Tower-state helper opened an independent connection")

    def unexpected_runner(*args, **kwargs):
        raise AssertionError("Tower-state helper started an independent transaction")

    monkeypatch.setattr(repository_module, "connect", unexpected_connection)
    monkeypatch.setattr(repository_module, "run_write_transaction", unexpected_runner)

    with connect(database_path) as connection:
        imported = repository._import_tower_state_with_connection(
            connection,
            state=TOWER_STATE,
            server="Server",
            account="Account",
            raw="supplied payload",
            source="test",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert imported.tower_state_observation_id > 0
        with connect(database_path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
        connection.rollback()


def test_failed_import_rolls_back_and_same_database_recovers(tmp_path) -> None:
    database_path, repository = _repositories(tmp_path)
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_tower_state_observation
            BEFORE INSERT ON tower_state_observations
            BEGIN
                SELECT RAISE(FAIL, 'forced Tower State failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced Tower State failure"):
        repository.import_tower_state(TOWER_STATE, "Server", "Account", "failed", "test")

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM tower_state_observations").fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_tower_state_observation")

    result = repository.import_tower_state(TOWER_STATE, "Server", "Account", "recovered", "test")
    assert result.import_event_id > 0
    assert repository.tower_state("Server", "Account") is not None
