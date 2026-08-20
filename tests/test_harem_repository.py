import sqlite3

import pytest

from moa.database.sqlite import connect
from moa.models.character import (
    DivorceConfirmation,
    HaremKeyEntry,
    HaremKeyPage,
    RankedCharacter,
    RankedHaremEntry,
    RankedHaremPage,
    RollObservation,
    TopPage,
)
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.harem_repository import HaremRepository


def _initialized_repositories(tmp_path):
    database_path = tmp_path / "catalog.db"
    catalog = CatalogRepository(database_path)
    return database_path, catalog, HaremRepository(database_path)


def _seed_characters(catalog: CatalogRepository) -> None:
    catalog.import_top_page(
        TopPage(
            limit=None,
            page_number=1,
            page_count=1,
            characters=(
                RankedCharacter(name="Alpha", series="Series A", claim_rank=1),
                RankedCharacter(name="Beta", series="Series B", claim_rank=2),
                RankedCharacter(name="Duplicate", series="Series C", claim_rank=3),
                RankedCharacter(name="Duplicate", series="Series D", claim_rank=4),
            ),
        ),
        "seed catalog",
        "test",
    )


def test_constructor_requires_explicit_path_and_does_not_bootstrap_schema(tmp_path) -> None:
    database_path = tmp_path / "uninitialized.db"

    repository = HaremRepository(database_path)

    assert repository.database_path == database_path
    assert not database_path.exists()


def test_ranked_harem_roulette_presence_round_trips_absent_observed_and_empty(
    tmp_path,
) -> None:
    database_path, _catalog, repository = _initialized_repositories(tmp_path)

    repository.import_ranked_harem_page(
        RankedHaremPage(
            page_number=None,
            page_count=None,
            entries=(
                RankedHaremEntry(
                    name="Absent", claim_rank=1, roulette_types=None
                ),
                RankedHaremEntry(
                    name="Observed", claim_rank=2, roulette_types=("wa", "ha")
                ),
                RankedHaremEntry(
                    name="Observed Empty", claim_rank=3, roulette_types=()
                ),
            ),
        ),
        "Server",
        "Account",
        "ranked harem presence",
        "test",
    )

    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT character_name, roulette_types_json, roulette_types_observed "
            "FROM owned_character_observations ORDER BY id"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("Absent", "[]", 0),
            ("Observed", '["wa", "ha"]', 1),
            ("Observed Empty", "[]", 1),
        ]

    observations = {
        observation.character_name: observation
        for observation in repository.owned_characters("Server", "Account")
    }
    assert observations["Absent"].roulette_types is None
    assert observations["Observed"].roulette_types == ("wa", "ha")
    assert observations["Observed Empty"].roulette_types == ()


def test_ranked_harem_legacy_empty_presence_reads_none_fail_closed(tmp_path) -> None:
    database_path, _catalog, repository = _initialized_repositories(tmp_path)
    repository.import_ranked_harem_page(
        RankedHaremPage(
            page_number=None,
            page_count=None,
            entries=(
                RankedHaremEntry(
                    name="Legacy Ambiguous", claim_rank=1, roulette_types=None
                ),
            ),
        ),
        "Server",
        "Account",
        "legacy ambiguous",
        "test",
    )
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE owned_character_observations "
            "SET roulette_types_observed = NULL"
        )

    observation = repository.owned_characters("Server", "Account")[0]
    assert observation.roulette_types is None


@pytest.mark.parametrize("observed", [0, None])
def test_ranked_harem_nonempty_roulette_requires_observed_presence(
    tmp_path, observed
) -> None:
    database_path, _catalog, repository = _initialized_repositories(tmp_path)
    repository.import_ranked_harem_page(
        RankedHaremPage(
            page_number=None,
            page_count=None,
            entries=(
                RankedHaremEntry(
                    name="Contradiction", claim_rank=1, roulette_types=("wa",)
                ),
            ),
        ),
        "Server",
        "Account",
        "contradictory presence",
        "test",
    )
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE owned_character_observations "
            "SET roulette_types_observed = ?",
            (observed,),
        )

    with pytest.raises(sqlite3.IntegrityError, match="conflict"):
        repository.owned_characters("Server", "Account")


def test_direct_repository_preserves_page_rows_matching_and_scan_lifecycle(tmp_path) -> None:
    database_path, catalog, repository = _initialized_repositories(tmp_path)
    _seed_characters(catalog)
    assert catalog._harem_repository.database_path == catalog.database_path == database_path

    key_scan = repository.begin_harem_scan(" Server ", " Account ", " KeYs ")
    assert repository.has_complete_harem_scan("Server", "Account") is False
    key_result = repository.import_harem_key_page(
        HaremKeyPage(
            page_number=1,
            page_count=1,
            entries=(
                HaremKeyEntry(
                    name=" alpha ", key_type="gold", key_count=7, kakera_value=100
                ),
                HaremKeyEntry(
                    name="Duplicate", key_type="silver", key_count=5, kakera_value=80
                ),
                HaremKeyEntry(
                    name="Unknown", key_type="bronze", key_count=1, kakera_value=None
                ),
            ),
        ),
        "SERVER",
        "ACCOUNT",
        "key page",
        "test",
        key_scan.id,
    )

    assert key_result.entries_imported == 3
    assert key_result.entries_linked == 1
    progress = repository.harem_scan_progress(key_scan.id)
    assert progress is not None
    assert progress.expected_page_count == 1
    assert progress.imported_pages == (1,)
    completed_keys = repository.complete_harem_scan(key_scan.id)
    assert completed_keys.is_complete is True
    assert repository.has_complete_harem_scan(" server ", " account ") is True
    assert [entry.character_name for entry in repository.harem_keys("Server", "Account")] == [
        " alpha ",
        "Duplicate",
        "Unknown",
    ]

    owned_scan = repository.begin_harem_scan("Server", "Account", "owned")
    ranked_result = repository.import_ranked_harem_page(
        RankedHaremPage(
            page_number=1,
            page_count=1,
            entries=(
                RankedHaremEntry(
                    name="Beta", claim_rank=2, kakera_value=200, roulette_types=None
                ),
                RankedHaremEntry(
                    name="Alpha", claim_rank=2, kakera_value=100, roulette_types=None
                ),
                RankedHaremEntry(
                    name="Missing", claim_rank=5, kakera_value=50, roulette_types=None
                ),
            ),
        ),
        "Server",
        "Account",
        "owned page",
        "test",
        owned_scan.id,
    )
    repository.complete_harem_scan(owned_scan.id)

    assert ranked_result.entries_imported == 3
    assert ranked_result.entries_linked == 2
    assert repository.has_complete_harem_scan("Server", "Account", "owned") is True
    assert [entry.character_name for entry in repository.owned_characters("Server", "Account")] == [
        "Alpha",
        "Beta",
        "Missing",
    ]

    with connect(database_path) as connection:
        key_rows = connection.execute(
            """
            SELECT observations.import_event_id, observations.character_id,
                   observations.normalized_character_name, observations.harem_scan_id,
                   pages.page_number
            FROM harem_key_observations AS observations
            LEFT JOIN harem_scan_pages AS pages
              ON pages.import_event_id = observations.import_event_id
            WHERE observations.import_event_id = ?
            ORDER BY observations.id
            """,
            (key_result.import_event_id,),
        ).fetchall()
        owned_rows = connection.execute(
            """
            SELECT import_event_id, character_id, normalized_character_name, harem_scan_id
            FROM owned_character_observations
            WHERE import_event_id = ?
            ORDER BY id
            """,
            (ranked_result.import_event_id,),
        ).fetchall()
        contexts = connection.execute(
            """
            SELECT server_contexts.name, account_contexts.name
            FROM account_contexts
            JOIN server_contexts ON server_contexts.id = account_contexts.server_context_id
            """
        ).fetchall()

    assert [tuple(row)[2:] for row in key_rows] == [
        ("alpha", key_scan.id, 1),
        ("duplicate", key_scan.id, 1),
        ("unknown", key_scan.id, 1),
    ]
    assert key_rows[0]["character_id"] is not None
    assert key_rows[1]["character_id"] is None
    assert key_rows[2]["character_id"] is None
    assert [tuple(row)[2:] for row in owned_rows] == [
        ("beta", owned_scan.id),
        ("alpha", owned_scan.id),
        ("missing", owned_scan.id),
    ]
    assert all(row["character_id"] is not None for row in owned_rows[:2])
    assert owned_rows[2]["character_id"] is None
    assert [tuple(row) for row in contexts] == [("Server", "Account")]


def test_queries_preserve_order_divorce_exclusion_and_recent_gain_order(tmp_path) -> None:
    _database_path, catalog, repository = _initialized_repositories(tmp_path)
    _seed_characters(catalog)
    repository.import_ranked_harem_page(
        RankedHaremPage(
            page_number=None,
            page_count=None,
            entries=(
                RankedHaremEntry(
                    name="Beta", claim_rank=2, kakera_value=50, roulette_types=None
                ),
                RankedHaremEntry(
                    name="Alpha", claim_rank=2, kakera_value=50, roulette_types=None
                ),
            ),
        ),
        "Server",
        "Account",
        "owned",
        "test",
    )
    repository.import_harem_key_page(
        HaremKeyPage(
            page_number=None,
            page_count=None,
            entries=(
                HaremKeyEntry(name="Beta", key_type="silver", key_count=5, kakera_value=50),
                HaremKeyEntry(name="Alpha", key_type="gold", key_count=5, kakera_value=50),
            ),
        ),
        "Server",
        "Account",
        "keys",
        "test",
    )

    assert [entry.character_name for entry in repository.owned_characters("Server", "Account")] == [
        "Alpha",
        "Beta",
    ]
    assert [entry.character_name for entry in repository.harem_keys("Server", "Account")] == [
        "Alpha",
        "Beta",
    ]

    catalog.import_divorce(
        DivorceConfirmation(
            account_name="Account", character_name="Alpha", kakera_refund=10
        ),
        "Server",
        "Account",
        "divorce",
        "test",
    )
    assert [entry.character_name for entry in repository.owned_characters("Server", "Account")] == [
        "Beta"
    ]
    assert [entry.character_name for entry in repository.harem_keys("Server", "Account")] == [
        "Beta"
    ]

    catalog.import_roll(
        RollObservation(
            name="Alpha",
            series="Series A",
            claim_rank=1,
            kakera_value=100,
            displayed_key_type="silver",
            displayed_key_count=4,
        ),
        "Server",
        "Account",
        "first roll",
        "test",
    )
    catalog.import_roll(
        RollObservation(
            name="Beta",
            series="Series B",
            claim_rank=2,
            kakera_value=200,
            displayed_key_type="gold",
            displayed_key_count=7,
        ),
        "Server",
        "Account",
        "second roll",
        "test",
    )

    gains = repository.recent_key_gains("Server", "Account", 2)
    assert [(gain.character_name, gain.key_count) for gain in gains] == [
        ("Beta", 7),
        ("Alpha", 4),
    ]
    with pytest.raises(ValueError, match="positive"):
        repository.recent_key_gains("Server", "Account", 0)


def test_ambiguous_name_history_does_not_cross_canonical_harem_identity(tmp_path) -> None:
    database_path, catalog, repository = _initialized_repositories(tmp_path)
    _seed_characters(catalog)
    repository.import_ranked_harem_page(
        RankedHaremPage(
            page_number=None,
            page_count=None,
            entries=(
                RankedHaremEntry(
                    name="Duplicate", claim_rank=30, roulette_types=None
                ),
            ),
        ),
        "Server",
        "Account",
        "unresolved owned",
        "test",
    )
    repository.import_harem_key_page(
        HaremKeyPage(
            page_number=None,
            page_count=None,
            entries=(
                HaremKeyEntry(name="Duplicate", key_type="bronze", key_count=1),
            ),
        ),
        "Server",
        "Account",
        "unresolved keys",
        "test",
    )

    timestamp = "2026-08-14T00:00:00+00:00"
    with connect(database_path) as connection:
        characters = connection.execute(
            "SELECT id, series FROM characters WHERE normalized_name = 'duplicate' "
            "ORDER BY normalized_series"
        ).fetchall()
        character_ids = {row["series"]: int(row["id"]) for row in characters}
        account_id = int(connection.execute("SELECT id FROM account_contexts").fetchone()[0])

        for series, claim_rank, key_count in (
            ("Series C", 3, 7),
            ("Series D", 4, 9),
        ):
            event_id = int(
                connection.execute(
                    "INSERT INTO import_events (kind, source, observed_at, raw_message) "
                    "VALUES ('test', 'test', ?, ?)",
                    (timestamp, f"explicit {series}"),
                ).lastrowid
            )
            connection.execute(
                """
                INSERT INTO owned_character_observations (
                    account_context_id, character_id, character_name,
                    normalized_character_name, claim_rank, kakera_value,
                    roulette_types_json, observed_at, import_event_id
                ) VALUES (?, ?, 'Duplicate', 'duplicate', ?, ?, '[]', ?, ?)
                """,
                (
                    account_id,
                    character_ids[series],
                    claim_rank,
                    claim_rank * 100,
                    timestamp,
                    event_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO harem_key_observations (
                    account_context_id, character_id, character_name,
                    normalized_character_name, key_type, key_count,
                    kakera_value, observed_at, import_event_id
                ) VALUES (?, ?, 'Duplicate', 'duplicate', 'gold', ?, ?, ?, ?)
                """,
                (
                    account_id,
                    character_ids[series],
                    key_count,
                    claim_rank * 100,
                    timestamp,
                    event_id,
                ),
            )

    owned = repository.owned_characters("Server", "Account")
    keys = repository.harem_keys("Server", "Account")
    assert {entry.character.id for entry in owned if entry.character is not None} == set(
        character_ids.values()
    )
    assert {entry.character.id for entry in keys if entry.character is not None} == set(
        character_ids.values()
    )
    assert sum(entry.character is None for entry in owned) == 1
    assert sum(entry.character is None for entry in keys) == 1

    with connect(database_path) as connection:
        unresolved_divorce_event_id = int(
            connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) "
                "VALUES ('divorce', 'test', ?, 'unresolved divorce')",
                (timestamp,),
            ).lastrowid
        )
        connection.execute(
            """
            INSERT INTO divorce_observations (
                account_context_id, character_id, character_name,
                normalized_character_name, observed_at, import_event_id
            ) VALUES (?, NULL, 'Duplicate', 'duplicate', ?, ?)
            """,
            (account_id, timestamp, unresolved_divorce_event_id),
        )

    assert {
        entry.character.id
        for entry in repository.owned_characters("Server", "Account")
        if entry.character is not None
    } == set(character_ids.values())
    assert {
        entry.character.id
        for entry in repository.harem_keys("Server", "Account")
        if entry.character is not None
    } == set(character_ids.values())

    with connect(database_path) as connection:
        exact_divorce_event_id = int(
            connection.execute(
                "INSERT INTO import_events (kind, source, observed_at, raw_message) "
                "VALUES ('divorce', 'test', ?, 'exact divorce')",
                (timestamp,),
            ).lastrowid
        )
        connection.execute(
            """
            INSERT INTO divorce_observations (
                account_context_id, character_id, character_name,
                normalized_character_name, observed_at, import_event_id
            ) VALUES (?, ?, 'Duplicate', 'duplicate', ?, ?)
            """,
            (
                account_id,
                character_ids["Series C"],
                timestamp,
                exact_divorce_event_id,
            ),
        )

    assert {
        entry.character.id
        for entry in repository.owned_characters("Server", "Account")
        if entry.character is not None
    } == {character_ids["Series D"]}
    assert {
        entry.character.id
        for entry in repository.harem_keys("Server", "Account")
        if entry.character is not None
    } == {character_ids["Series D"]}
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM owned_character_observations "
            "WHERE character_id IS NULL AND normalized_character_name = 'duplicate'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM harem_key_observations "
            "WHERE character_id IS NULL AND normalized_character_name = 'duplicate'"
        ).fetchone()[0] == 1


def test_harem_page_failure_rolls_back_all_rows_and_runner_recovers(tmp_path) -> None:
    database_path, _catalog, repository = _initialized_repositories(tmp_path)
    scan = repository.begin_harem_scan("Server", "Account")
    page = HaremKeyPage(
        page_number=1,
        page_count=1,
        entries=(HaremKeyEntry(name="Failure", key_type="gold", key_count=7),),
    )
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_harem_observation
            BEFORE INSERT ON harem_key_observations
            BEGIN
                SELECT RAISE(FAIL, 'forced harem observation failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced harem observation failure"):
        repository.import_harem_key_page(
            page, "Server", "Account", "failed page", "test", scan.id
        )

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM harem_key_observations"
        ).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM harem_scan_pages").fetchone()[0] == 0
        assert connection.execute(
            "SELECT expected_page_count FROM harem_scans WHERE id = ?", (scan.id,)
        ).fetchone()[0] is None
        connection.execute("DROP TRIGGER fail_harem_observation")

    recovered = repository.import_harem_key_page(
        page, "Server", "Account", "valid page", "test", scan.id
    )
    assert recovered.entries_imported == 1
    assert repository.harem_scan_progress(scan.id).imported_pages == (1,)
