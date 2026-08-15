import sqlite3

import pytest

from moa.database.sqlite import connect
from moa.models.character import ProfileSnapshot
from moa.parser.mudae import MudaeTextParser
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


MINIMAL_PROFILE_RESPONSE = (
    "moa\n"
    "Collection size: 0 (0%:female: 0% :male:)"
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


def test_profile_repository_characterizes_minimal_profile_absence_storage(tmp_path) -> None:
    database_path = tmp_path / "catalog.db"
    CatalogRepository(database_path)
    repository = ProfileRepository(database_path)
    snapshot = MudaeTextParser().parse_profile(MINIMAL_PROFILE_RESPONSE)

    repository.import_profile(
        snapshot,
        "Server",
        "moa",
        MINIMAL_PROFILE_RESPONSE,
        "test",
    )

    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT pokedex_count, pokedex_json, kakera_reacts_json,
                   mudapins_collected, mudapins_total, kakera_balance,
                   bronze_keys, silver_keys, gold_keys, sphere_stock,
                   spheres_json, displayed_badges_json
            FROM profile_observations
            """
        ).fetchone()
    assert tuple(row) == (
        None,
        "[]",
        "{}",
        None,
        None,
        None,
        0,
        0,
        0,
        None,
        "{}",
        "[]",
    )

    observation = repository.profile("Server", "moa")
    assert observation is not None
    assert observation.snapshot.profile_name == "moa"
    assert observation.snapshot.collection_size == 0
    assert (observation.snapshot.female_percent, observation.snapshot.male_percent) == (0, 0)
    assert observation.snapshot.pokedex_count is None
    assert observation.snapshot.pokedex_pokemon == ()
    assert observation.snapshot.kakera_reacts == {}
    assert observation.snapshot.mudapins_collected is None
    assert observation.snapshot.mudapins_total is None
    assert observation.snapshot.kakera_balance is None
    assert (
        observation.snapshot.bronze_keys,
        observation.snapshot.silver_keys,
        observation.snapshot.gold_keys,
    ) == (0, 0, 0)
    assert observation.snapshot.sphere_stock is None
    assert observation.snapshot.spheres == {}
    assert observation.snapshot.displayed_badges == ()
