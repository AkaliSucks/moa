import sqlite3

from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import KakeralootStateSnapshot
from moa.repositories import kakeraloot_state_repository as repository_module
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.kakeraloot_state_repository import KakeralootStateRepository


KAKERALOOT_VALUE_FIELDS = (
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

    monkeypatch.setattr(repository_module, "connect_read_only", unexpected_connection)
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


@pytest.mark.parametrize("field_name", KAKERALOOT_VALUE_FIELDS)
@pytest.mark.parametrize(
    ("supplied", "expected_value", "expected_observed", "expected_public"),
    (
        (None, 0, 0, None),
        (0, 0, 1, 0),
        (11, 11, 1, 11),
        (-1, -1, 1, -1),
    ),
)
def test_exact_value_presence_storage_and_read_boundaries(
    tmp_path,
    field_name,
    supplied,
    expected_value,
    expected_observed,
    expected_public,
) -> None:
    database_path, repository = _repositories(tmp_path)
    state = KakeralootStateSnapshot(**{field_name: supplied})

    repository.import_kakeraloot_state(state, "Server", "Account", "boundary payload", "test")

    observation = repository.kakeraloot_state("Server", "Account")
    assert observation is not None
    assert observation.has_kakeraloots is True
    assert observation.status_note == state.status_note
    assert getattr(observation, field_name) == expected_public
    with connect(database_path) as connection:
        row = connection.execute(
            f"SELECT {field_name}, {field_name}_observed FROM kakeraloot_state_observations"
        ).fetchone()
    assert tuple(row) == (expected_value, expected_observed)


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
            "SELECT has_kakeraloots, rolls_stacked, rolls_stacked_observed, "
            "kakera_balance, kakera_balance_observed, status_note "
            "FROM kakeraloot_state_observations"
        ).fetchone()
    assert tuple(row) == (0, 0, 0, 0, 0, NO_KAKERALOOT_STATE.status_note)


def test_no_loot_public_masking_keeps_supplied_numeric_presence(tmp_path) -> None:
    database_path, repository = _repositories(tmp_path)
    state = KakeralootStateSnapshot(
        has_kakeraloots=False,
        rolls_stacked=0,
        protected_wish_level=7,
        quantity_level=-1,
    )

    repository.import_kakeraloot_state(state, "Server", "Account", "synthetic", "test")

    observation = repository.kakeraloot_state("Server", "Account")
    assert observation is not None
    assert observation.has_kakeraloots is False
    assert observation.rolls_stacked is None
    assert observation.protected_wish_level is None
    assert observation.quantity_level is None
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT rolls_stacked, rolls_stacked_observed,
                   protected_wish_level, protected_wish_level_observed,
                   quantity_level, quantity_level_observed
            FROM kakeraloot_state_observations
            """
        ).fetchone()
    assert tuple(row) == (0, 1, 7, 1, -1, 1)


@pytest.mark.parametrize("observed", (0, None))
def test_nonzero_unobserved_storage_fails_repository_integrity_check(tmp_path, observed) -> None:
    database_path, repository = _repositories(tmp_path)
    repository.import_kakeraloot_state(
        KakeralootStateSnapshot(rolls_stacked=1),
        "Server",
        "Account",
        "invalid target",
        "test",
    )
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE kakeraloot_state_observations "
            "SET rolls_stacked = 7, rolls_stacked_observed = ?",
            (observed,),
        )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="inconsistent rolls_stacked presence",
    ):
        repository.kakeraloot_state("Server", "Account")


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
