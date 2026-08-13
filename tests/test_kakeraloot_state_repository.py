import sqlite3

from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import KakeralootStateSnapshot
from moa.repositories import kakeraloot_state_repository as repository_module
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.kakeraloot_state_repository import KakeralootStateRepository


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


def _repositories(tmp_path):
    database_path = tmp_path / "kakeraloot-state.db"
    CatalogRepository(database_path)
    return database_path, KakeralootStateRepository(database_path)


def test_constructor_uses_explicit_path_without_bootstrap(tmp_path) -> None:
    database_path = tmp_path / "uninitialized.db"

    repository = KakeralootStateRepository(database_path)

    assert repository.database_path == database_path
    assert not database_path.exists()


def test_direct_import_read_latest_and_persisted_linkage(tmp_path) -> None:
    database_path, repository = _repositories(tmp_path)

    assert repository.kakeraloot_state("Server", "Account") is None
    first = repository.import_kakeraloot_state(
        KAKERALOOT_STATE, " Server ", " Account ", "first payload", "test"
    )
    second_state = KAKERALOOT_STATE.model_copy(update={"rolls_stacked": 18})
    second = repository.import_kakeraloot_state(
        second_state, "Server", "Account", "second payload", "test"
    )

    latest = repository.kakeraloot_state(" server ", " account ")
    assert latest is not None
    assert latest.rolls_stacked == 18
    assert second.import_event_id > first.import_event_id
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 1
        row = connection.execute(
            "SELECT import_event_id, has_kakeraloots, rolls_stacked, status_note "
            "FROM kakeraloot_state_observations WHERE id = "
            "(SELECT MAX(id) FROM kakeraloot_state_observations)"
        ).fetchone()
    assert tuple(row) == (second.import_event_id, 1, 18, "guarded state")


def test_standalone_import_owns_exactly_one_runner(tmp_path, monkeypatch) -> None:
    _database_path, repository = _repositories(tmp_path)
    original_runner = repository_module.run_write_transaction
    calls = []

    def observed_runner(database_path, callback):
        calls.append(database_path)
        return original_runner(database_path, callback)

    monkeypatch.setattr(repository_module, "run_write_transaction", observed_runner)

    repository.import_kakeraloot_state(
        KAKERALOOT_STATE, "Server", "Account", "runner payload", "test"
    )

    assert calls == [repository.database_path]


def test_supplied_connection_seam_is_transaction_neutral(tmp_path, monkeypatch) -> None:
    database_path, repository = _repositories(tmp_path)

    def unexpected_connection(*args, **kwargs):
        raise AssertionError("Kakeraloot-state helper opened an independent connection")

    def unexpected_runner(*args, **kwargs):
        raise AssertionError("Kakeraloot-state helper started an independent transaction")

    monkeypatch.setattr(repository_module, "connect", unexpected_connection)
    monkeypatch.setattr(repository_module, "run_write_transaction", unexpected_runner)

    with connect(database_path) as connection:
        imported = repository._import_kakeraloot_state_with_connection(
            connection,
            state=KAKERALOOT_STATE,
            server="Server",
            account="Account",
            raw="supplied payload",
            source="test",
            observed_at=datetime.now(timezone.utc),
        )
        assert connection.in_transaction is True
        assert imported.kakeraloot_state_observation_id > 0
        with connect(database_path) as observer:
            assert observer.execute(
                "SELECT COUNT(*) FROM kakeraloot_state_observations"
            ).fetchone()[0] == 0
        connection.rollback()


@pytest.mark.parametrize(
    ("state", "expected_progress"),
    [
        (ZERO_KAKERALOOT_STATE, 0),
        (NULL_KAKERALOOT_STATE, 0),
    ],
)
def test_zero_and_none_storage_boundaries_are_preserved(tmp_path, state, expected_progress) -> None:
    database_path, repository = _repositories(tmp_path)

    repository.import_kakeraloot_state(state, "Server", "Account", "boundary payload", "test")

    observation = repository.kakeraloot_state("Server", "Account")
    assert observation is not None
    assert observation.has_kakeraloots is True
    assert observation.status_note == state.status_note
    assert observation.rolls_stacked == expected_progress
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT rolls_stacked, kakera_balance FROM kakeraloot_state_observations"
        ).fetchone()
    assert tuple(row) == (0, 0)


def test_no_loot_round_trip_preserves_false_status_and_read_none(tmp_path) -> None:
    database_path, repository = _repositories(tmp_path)

    repository.import_kakeraloot_state(
        NO_KAKERALOOT_STATE, "Server", "Account", "no loot payload", "test"
    )

    observation = repository.kakeraloot_state("Server", "Account")
    assert observation is not None
    assert observation.has_kakeraloots is False
    assert observation.status_note == NO_KAKERALOOT_STATE.status_note
    assert observation.rolls_stacked is None
    assert observation.kakera_balance is None
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT has_kakeraloots, rolls_stacked, kakera_balance, status_note "
            "FROM kakeraloot_state_observations"
        ).fetchone()
    assert tuple(row) == (0, 0, 0, NO_KAKERALOOT_STATE.status_note)


def test_failed_import_rolls_back_and_same_database_recovers(tmp_path) -> None:
    database_path, repository = _repositories(tmp_path)
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_kakeraloot_state_observation
            BEFORE INSERT ON kakeraloot_state_observations
            BEGIN
                SELECT RAISE(FAIL, 'forced Kakeraloot-state failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced Kakeraloot-state failure"):
        repository.import_kakeraloot_state(
            KAKERALOOT_STATE, "Server", "Account", "failed payload", "test"
        )

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM kakeraloot_state_observations"
        ).fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_kakeraloot_state_observation")

    result = repository.import_kakeraloot_state(
        KAKERALOOT_STATE, "Server", "Account", "recovered payload", "test"
    )
    assert result.import_event_id > 0
    assert repository.kakeraloot_state("Server", "Account") is not None
