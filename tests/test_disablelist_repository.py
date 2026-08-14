import sqlite3
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import DisableListEntry, DisableListSnapshot
from moa.repositories import disablelist_repository as repository_module
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.disablelist_repository import DisableListRepository


OBSERVED_AT = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
STATE = DisableListSnapshot(
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
BOUNDARY = DisableListSnapshot(
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


def _repository(tmp_path):
    database_path = tmp_path / "disablelist.db"
    CatalogRepository(database_path)
    return database_path, DisableListRepository(database_path)


def test_constructor_uses_explicit_path_without_bootstrap(tmp_path) -> None:
    database_path = tmp_path / "uninitialized.db"

    repository = DisableListRepository(database_path)

    assert repository.database_path == database_path
    assert not database_path.exists()


def test_direct_import_read_latest_and_persisted_linkage(tmp_path) -> None:
    database_path, repository = _repository(tmp_path)

    assert repository.disablelist("Server", "Account") is None
    first = repository.import_disablelist(
        STATE, " Server ", " Account ", "first payload", "test"
    )
    latest_state = STATE.model_copy(update={"slots_used": 14})
    second = repository.import_disablelist(
        latest_state, "Server", "Account", "second payload", "test"
    )

    observation = repository.disablelist(" server ", " account ")
    assert observation is not None
    assert observation.slots_used == 14
    assert observation.entries == STATE.entries
    assert second.import_event_id > first.import_event_id
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 1
        row = connection.execute(
            "SELECT account_context_id, import_event_id, entries_json "
            "FROM disablelist_observations WHERE id = (SELECT MAX(id) FROM disablelist_observations)"
        ).fetchone()
        assert row["account_context_id"] > 0
        assert row["import_event_id"] == second.import_event_id
        assert row["entries_json"] == '[{"name": "Kadokawa Corporation", "disabled_count": 13207}, {"name": "Webcomics", "disabled_count": 11073}, {"name": "Kadokawa Corporation", "disabled_count": 13207}]'


def test_standalone_import_owns_exactly_one_runner(tmp_path, monkeypatch) -> None:
    _database_path, repository = _repository(tmp_path)
    original_runner = repository_module.run_write_transaction
    calls = []

    def observed_runner(database_path, callback):
        calls.append(database_path)
        return original_runner(database_path, callback)

    monkeypatch.setattr(repository_module, "run_write_transaction", observed_runner)

    repository.import_disablelist(STATE, "Server", "Account", "runner payload", "test")

    assert calls == [repository.database_path]


def test_supplied_connection_seam_is_transaction_neutral(tmp_path, monkeypatch) -> None:
    database_path, repository = _repository(tmp_path)

    def unexpected_connection(*args, **kwargs):
        raise AssertionError("Disablelist helper opened an independent connection")

    def unexpected_runner(*args, **kwargs):
        raise AssertionError("Disablelist helper started an independent transaction")

    monkeypatch.setattr(repository_module, "connect", unexpected_connection)
    monkeypatch.setattr(repository_module, "run_write_transaction", unexpected_runner)

    with connect(database_path) as connection:
        imported = repository._import_disablelist_with_connection(
            connection,
            state=STATE,
            server="Server",
            account="Account",
            raw="supplied payload",
            source="test",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction is True
        assert imported.disablelist_observation_id > 0
        with connect(database_path) as observer:
            assert observer.execute(
                "SELECT COUNT(*) FROM disablelist_observations"
            ).fetchone()[0] == 0
        connection.rollback()


def test_boundary_values_and_empty_entries_round_trip_exactly(tmp_path) -> None:
    _database_path, repository = _repository(tmp_path)

    repository.import_disablelist(BOUNDARY, "Server", "Account", "boundary payload", "test")

    observation = repository.disablelist("Server", "Account")
    assert observation is not None
    assert observation.entries == ()
    assert observation.slots_used == 0
    assert observation.slots_capacity == 0
    assert observation.total_disabled == 0
    assert observation.disabled_wa == 0
    assert observation.wa_pool_limit == 0
    assert observation.ha_pool_limit is None
    assert observation.western_disabled is False
    assert observation.irl_disabled is False


def test_failed_import_rolls_back_and_same_database_recovers(tmp_path) -> None:
    database_path, repository = _repository(tmp_path)
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_disablelist_observation
            BEFORE INSERT ON disablelist_observations
            BEGIN
                SELECT RAISE(FAIL, 'forced disablelist failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced disablelist failure"):
        repository.import_disablelist(STATE, "Server", "Account", "failed payload", "test")

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM disablelist_observations").fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_disablelist_observation")

    result = repository.import_disablelist(STATE, "Server", "Account", "recovered payload", "test")
    assert result.import_event_id > 0
    assert repository.disablelist("Server", "Account") is not None
