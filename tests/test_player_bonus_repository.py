import sqlite3
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import PlayerBonusMetric, PlayerBonusSnapshot
from moa.repositories import player_bonus_repository as player_bonus_repository_module
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.player_bonus_repository import (
    PlayerBonusRepository,
)


PLAYER_BONUS = PlayerBonusSnapshot(
    metrics=(
        PlayerBonusMetric(label="Rolls per hour", detail="+9"),
        PlayerBonusMetric(label="Wish spawn", detail="+210%"),
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


def _repositories(tmp_path):
    database_path = tmp_path / "player-bonus.db"
    CatalogRepository(database_path)
    return database_path, PlayerBonusRepository(database_path)


def test_constructor_uses_explicit_path_without_bootstrap(tmp_path) -> None:
    database_path = tmp_path / "uninitialized.db"

    repository = PlayerBonusRepository(database_path)

    assert repository.database_path == database_path
    assert not database_path.exists()


def test_direct_import_read_latest_and_no_observation(tmp_path) -> None:
    database_path, repository = _repositories(tmp_path)

    assert repository.player_bonus("Server", "Account") is None
    first = repository.import_player_bonus(
        PLAYER_BONUS, " Server ", " Account ", "first payload", "test"
    )
    latest = PLAYER_BONUS.model_copy(update={"rolls_per_hour_bonus": 10})
    second = repository.import_player_bonus(
        latest, "Server", "Account", "second payload", "test"
    )

    assert second.import_event_id > first.import_event_id
    assert repository.player_bonus(" server ", " account ").model_dump() == {
        "server_name": "Server",
        "account_name": "Account",
        **latest.model_dump(),
        "observed_at": repository.player_bonus("Server", "Account").observed_at,
    }
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM player_bonus_observations").fetchone()[0] == 2
        row = connection.execute(
            "SELECT import_event_id FROM player_bonus_observations WHERE id = "
            "(SELECT MAX(id) FROM player_bonus_observations)"
        ).fetchone()
        assert row[0] == second.import_event_id


def test_standalone_import_owns_exactly_one_runner(tmp_path, monkeypatch) -> None:
    _database_path, repository = _repositories(tmp_path)
    original_runner = player_bonus_repository_module.run_write_transaction
    calls = []

    def observed_runner(database_path, callback):
        calls.append(database_path)
        return original_runner(database_path, callback)

    monkeypatch.setattr(
        player_bonus_repository_module, "run_write_transaction", observed_runner
    )

    repository.import_player_bonus(
        PLAYER_BONUS, "Server", "Account", "runner payload", "test"
    )

    assert calls == [repository.database_path]


def test_supplied_connection_seam_does_not_open_or_start_transaction(
    tmp_path, monkeypatch
) -> None:
    database_path, catalog = _repositories(tmp_path)

    def unexpected_connection(*args, **kwargs):
        raise AssertionError("player-bonus helper opened an independent connection")

    def unexpected_runner(*args, **kwargs):
        raise AssertionError("player-bonus helper started an independent transaction")

    monkeypatch.setattr(
        player_bonus_repository_module, "connect_read_only", unexpected_connection
    )
    monkeypatch.setattr(
        player_bonus_repository_module, "run_write_transaction", unexpected_runner
    )

    with connect(database_path) as connection:
        imported = catalog._import_player_bonus_with_connection(
            connection,
            state=PLAYER_BONUS,
            server="Server",
            account="Account",
            raw="supplied payload",
            source="test",
            observed_at=datetime.now(timezone.utc),
        )
        assert connection.in_transaction is True
        assert imported.player_bonus_observation_id > 0

        with connect(database_path) as observer:
            assert observer.execute(
                "SELECT COUNT(*) FROM player_bonus_observations"
            ).fetchone()[0] == 0
        connection.rollback()


def test_boundary_values_round_trip_exactly(tmp_path) -> None:
    database_path, repository = _repositories(tmp_path)

    repository.import_player_bonus(
        BOUNDARY_PLAYER_BONUS, "Server", "Account", "boundary payload", "test"
    )

    observation = repository.player_bonus("Server", "Account")
    assert observation is not None
    assert observation.metrics == BOUNDARY_PLAYER_BONUS.metrics
    assert observation.rolls_per_hour_bonus == 0
    assert observation.wishlist_slot_bonus is None
    assert observation.wish_spawn_bonus_percent == -1
    assert observation.starwish_spawn_bonus_percent == 0
    assert observation.starwish_total_spawn_bonus_percent is None
    assert observation.kakera_button_power_cost_percent == -2
    assert observation.light_kakera_minimum == 0
    assert observation.light_kakera_maximum is None

    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT metrics_json, rolls_per_hour_bonus, wish_spawn_bonus_percent, "
            "kakera_button_power_cost_percent FROM player_bonus_observations"
        ).fetchone()
        assert row[0] == '[{"label": "", "detail": ""}]'
        assert tuple(row)[1:] == (0, -1, -2)


def test_failed_import_rolls_back_and_same_database_recovers(tmp_path) -> None:
    database_path, repository = _repositories(tmp_path)
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_player_bonus_observation
            BEFORE INSERT ON player_bonus_observations
            BEGIN
                SELECT RAISE(FAIL, 'forced player bonus failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced player bonus failure"):
        repository.import_player_bonus(
            PLAYER_BONUS, "Server", "Account", "failed payload", "test"
        )

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM player_bonus_observations").fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_player_bonus_observation")

    result = repository.import_player_bonus(
        PLAYER_BONUS, "Server", "Account", "recovered payload", "test"
    )
    assert result.import_event_id > 0
    assert repository.player_bonus("Server", "Account") is not None
