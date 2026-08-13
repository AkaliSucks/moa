import sqlite3

import pytest

from moa.database.sqlite import connect
from moa.models.character import ProfileSnapshot
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.profile_repository import ProfileRepository


PROFILE = ProfileSnapshot(
    profile_name="Account",
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


def test_constructor_uses_explicit_path_without_bootstrap(tmp_path) -> None:
    database_path = tmp_path / "uninitialized.db"

    repository = ProfileRepository(database_path)

    assert repository.database_path == database_path
    assert not database_path.exists()


def test_catalog_composes_profile_repository_on_same_path(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    catalog = CatalogRepository(database_path)

    assert catalog._profile_repository.database_path == catalog.database_path == database_path


def test_direct_profile_write_read_latest_and_no_profile(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    catalog = CatalogRepository(database_path)
    repository = ProfileRepository(database_path)

    assert repository.profile("Server", "Account") is None
    first = repository.import_profile(PROFILE, " Server ", " Account ", "first", "test")
    latest = PROFILE.model_copy(update={"collection_size": 36, "spheres": {":spP:": 3}})
    second = repository.import_profile(latest, "Server", "Account", "second", "test")

    assert second.import_event_id > first.import_event_id
    assert repository.profile(" server ", " account ").snapshot == latest
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM profile_observations").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 1
    assert catalog.profile("SERVER", "ACCOUNT").snapshot == latest


def test_profile_write_rolls_back_and_same_database_recovers(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    catalog = CatalogRepository(database_path)
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_profile_observation
            BEFORE INSERT ON profile_observations
            BEGIN
                SELECT RAISE(FAIL, 'forced profile failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced profile failure"):
        catalog.import_profile(PROFILE, "Server", "Account", "failed", "test")

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM profile_observations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_profile_observation")

    result = catalog.import_profile(PROFILE, "Server", "Account", "recovered", "test")

    assert result.import_event_id > 0
    assert catalog.profile("Server", "Account").snapshot == PROFILE
