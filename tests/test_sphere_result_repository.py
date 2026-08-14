import json
import sqlite3
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import SphereGain, SphereResultSnapshot
from moa.repositories import sphere_result_repository as repository_module
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.sphere_result_repository import SphereResultRepository


SPHERE_RESULT = SphereResultSnapshot(
    clicks_available=None,
    click_window_minutes=None,
    purple_target=0,
    purple_total=None,
    gains=(
        SphereGain(sphere_type="p", amount=0, is_free=False),
        SphereGain(sphere_type="r", amount=4, is_free=True),
        SphereGain(sphere_type="r", amount=4, is_free=True),
    ),
    total_gained=99,
    stock=None,
)


def _repositories(tmp_path):
    database_path = tmp_path / "sphere-result.db"
    CatalogRepository(database_path)
    return database_path, SphereResultRepository(database_path)


def test_constructor_uses_explicit_path_without_bootstrap(tmp_path) -> None:
    database_path = tmp_path / "uninitialized.db"

    repository = SphereResultRepository(database_path)

    assert repository.database_path == database_path
    assert not database_path.exists()


def test_direct_import_read_latest_scope_and_json_scalar_linkage(tmp_path) -> None:
    database_path, repository = _repositories(tmp_path)

    assert repository.sphere_result("Server", "Account") is None
    first = repository.import_sphere_result(
        SPHERE_RESULT, " Server ", " Account ", "first payload", "test"
    )
    latest_state = SPHERE_RESULT.model_copy(update={"stock": 12})
    second = repository.import_sphere_result(
        latest_state, "server", "account", "second payload", "test"
    )
    repository.import_sphere_result(SPHERE_RESULT, "Other Server", "Account", "other", "test")
    repository.import_sphere_result(SPHERE_RESULT, "Server", "Other Account", "other", "test")

    latest = repository.sphere_result(" SERVER ", " ACCOUNT ")
    assert latest is not None
    assert latest.snapshot == latest_state
    assert latest.observed_at.tzinfo is not None
    assert latest.observed_at.utcoffset() == timezone.utc.utcoffset(latest.observed_at)
    assert second.import_event_id > first.import_event_id
    assert repository.sphere_result("Other Server", "Account") is not None
    assert repository.sphere_result("Server", "Other Account") is not None

    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT sro.snapshot_json, sro.total_gained, sro.stock,
                   sro.import_event_id, sc.normalized_name AS server,
                   ac.normalized_name AS account
            FROM sphere_result_observations AS sro
            JOIN account_contexts AS ac ON ac.id = sro.account_context_id
            JOIN server_contexts AS sc ON sc.id = ac.server_context_id
            ORDER BY sro.id
            """
        ).fetchall()
        assert len(rows) == 4
        stored = rows[1]
        assert json.loads(stored["snapshot_json"]) == latest_state.model_dump(mode="json")
        assert stored["total_gained"] == 99
        assert stored["stock"] == 12
        assert (stored["server"], stored["account"]) == ("server", "account")
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 3


@pytest.mark.parametrize("stock", [None, 0, 12])
def test_nullable_zero_false_empty_and_ordered_gain_values_round_trip(tmp_path, stock) -> None:
    database_path, repository = _repositories(tmp_path)
    state = SPHERE_RESULT.model_copy(
        update={
            "stock": stock,
            "gains": (),
            "total_gained": 0,
            "purple_target": None,
        }
    )

    result = repository.import_sphere_result(state, "Server", "Account", "payload", "test")

    observation = repository.sphere_result("Server", "Account")
    assert observation is not None
    assert observation.snapshot == state
    assert observation.snapshot.gains == ()
    assert observation.snapshot.total_gained == 0
    assert observation.snapshot.stock == stock
    assert observation.snapshot.gains == state.gains
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT snapshot_json, total_gained, stock FROM sphere_result_observations WHERE import_event_id = ?",
            (result.import_event_id,),
        ).fetchone()
    payload = json.loads(row["snapshot_json"])
    assert payload["gains"] == []
    assert payload["total_gained"] == 0
    assert payload["stock"] == stock
    assert row["total_gained"] == 0
    assert row["stock"] == stock


def test_standalone_import_owns_exactly_one_runner(tmp_path, monkeypatch) -> None:
    _database_path, repository = _repositories(tmp_path)
    original_runner = repository_module.run_write_transaction
    calls = []

    def observed_runner(database_path, callback):
        calls.append(database_path)
        return original_runner(database_path, callback)

    monkeypatch.setattr(repository_module, "run_write_transaction", observed_runner)

    repository.import_sphere_result(SPHERE_RESULT, "Server", "Account", "payload", "test")

    assert calls == [repository.database_path]


def test_supplied_connection_helper_is_transaction_neutral(tmp_path, monkeypatch) -> None:
    database_path, repository = _repositories(tmp_path)

    def unexpected_connection(*args, **kwargs):
        raise AssertionError("Sphere Result helper opened an independent connection")

    def unexpected_runner(*args, **kwargs):
        raise AssertionError("Sphere Result helper started an independent transaction")

    monkeypatch.setattr(repository_module, "connect", unexpected_connection)
    monkeypatch.setattr(repository_module, "run_write_transaction", unexpected_runner)

    with connect(database_path) as connection:
        imported = repository._import_sphere_result_with_connection(
            connection,
            state=SPHERE_RESULT,
            server="Server",
            account="Account",
            raw="supplied payload",
            source="test",
            observed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        assert connection.in_transaction is True
        assert imported.import_event_id > 0
        assert imported.sphere_result_observation_id > 0
        connection.rollback()


def test_late_failure_rolls_back_and_same_database_recovers(tmp_path) -> None:
    database_path, repository = _repositories(tmp_path)
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_sphere_result_observation
            BEFORE INSERT ON sphere_result_observations
            BEGIN
                SELECT RAISE(FAIL, 'forced Sphere Result failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced Sphere Result failure"):
        repository.import_sphere_result(SPHERE_RESULT, "Server", "Account", "failed", "test")

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM sphere_result_observations").fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_sphere_result_observation")

    result = repository.import_sphere_result(SPHERE_RESULT, "Server", "Account", "recovered", "test")
    assert result.import_event_id > 0
    assert repository.sphere_result("Server", "Account") is not None
