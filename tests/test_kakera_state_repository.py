import json
import sqlite3

from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import BadgeLevel, KakeraStateSnapshot
from moa.repositories import kakera_state_repository as repository_module
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories.kakera_state_repository import KakeraStateRepository


KAKERA_STATE = KakeraStateSnapshot(
    kakera_balance=7_673,
    badges=(
        BadgeLevel(badge_name="bronze", level=4, max_reached=True),
        BadgeLevel(badge_name="silver", level=3, max_reached=False),
    ),
)
ZERO_KAKERA_STATE = KakeraStateSnapshot(kakera_balance=0, badges=())
OBSERVED_EARLY = datetime(2026, 1, 2, tzinfo=timezone.utc)
OBSERVED_LATE = datetime(2026, 1, 3, tzinfo=timezone.utc)


def _repositories(tmp_path):
    database_path = tmp_path / "kakera-state.db"
    CatalogRepository(database_path)
    return database_path, KakeraStateRepository(database_path)


def test_constructor_uses_explicit_path_without_bootstrap(tmp_path) -> None:
    database_path = tmp_path / "uninitialized.db"

    repository = KakeraStateRepository(database_path)

    assert repository.database_path == database_path
    assert not database_path.exists()


def test_no_observation_and_empty_history_are_preserved(tmp_path) -> None:
    _database_path, repository = _repositories(tmp_path)

    assert repository.kakera_state("Server", "Account") is None
    assert repository.kakera_history("Server", "Account") == ()


def test_direct_import_read_latest_and_persisted_linkage(tmp_path) -> None:
    database_path, repository = _repositories(tmp_path)

    first = repository.import_kakera_state(
        KAKERA_STATE, " Server ", " Account ", "first payload", "test"
    )
    second_state = KAKERA_STATE.model_copy(update={"kakera_balance": 8_000})
    second = repository.import_kakera_state(
        second_state, "Server", "Account", "second payload", "test"
    )

    latest = repository.kakera_state(" server ", " account ")
    assert latest is not None
    assert latest.kakera_balance == 8_000
    assert latest.badges == KAKERA_STATE.badges
    assert second.import_event_id > first.import_event_id
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 1
        row = connection.execute(
            """
            SELECT import_event_id, kakera_balance, badges_json
            FROM kakera_state_observations
            WHERE id = (SELECT MAX(id) FROM kakera_state_observations)
            """
        ).fetchone()
    assert tuple(row[:2]) == (second.import_event_id, 8_000)
    assert json.loads(row["badges_json"]) == [badge.model_dump() for badge in KAKERA_STATE.badges]


def test_history_orders_by_timestamp_then_id_and_reconstructs_progress(tmp_path) -> None:
    database_path, repository = _repositories(tmp_path)

    with connect(database_path) as connection:
        repository._import_kakera_state_with_connection(
            connection,
            state=KAKERA_STATE,
            server="Server",
            account="Account",
            raw="late payload",
            source="test",
            observed_at=OBSERVED_LATE,
        )
        connection.commit()
    with connect(database_path) as connection:
        repository._import_kakera_state_with_connection(
            connection,
            state=ZERO_KAKERA_STATE,
            server="Server",
            account="Account",
            raw="early payload",
            source="test",
            observed_at=OBSERVED_EARLY,
        )
        connection.commit()

    history = repository.kakera_history("Server", "Account")

    assert [point.kakera_balance for point in history] == [0, 7_673]
    assert [point.max_badge_count for point in history] == [0, 1]
    assert [point.observed_at for point in history] == [OBSERVED_EARLY, OBSERVED_LATE]
    latest = repository.kakera_state("Server", "Account")
    assert latest is not None
    assert latest.kakera_balance == 0


def test_standalone_import_owns_exactly_one_runner(tmp_path, monkeypatch) -> None:
    _database_path, repository = _repositories(tmp_path)
    original_runner = repository_module.run_write_transaction
    calls = []

    def observed_runner(database_path, callback):
        calls.append(database_path)
        return original_runner(database_path, callback)

    monkeypatch.setattr(repository_module, "run_write_transaction", observed_runner)

    repository.import_kakera_state(KAKERA_STATE, "Server", "Account", "payload", "test")

    assert calls == [repository.database_path]


def test_supplied_connection_seam_is_transaction_neutral(tmp_path, monkeypatch) -> None:
    database_path, repository = _repositories(tmp_path)

    def unexpected_connection(*args, **kwargs):
        raise AssertionError("Kakera-state helper opened an independent connection")

    def unexpected_runner(*args, **kwargs):
        raise AssertionError("Kakera-state helper started an independent transaction")

    monkeypatch.setattr(repository_module, "connect_read_only", unexpected_connection)
    monkeypatch.setattr(repository_module, "run_write_transaction", unexpected_runner)

    with connect(database_path) as connection:
        imported = repository._import_kakera_state_with_connection(
            connection,
            state=KAKERA_STATE,
            server="Server",
            account="Account",
            raw="supplied payload",
            source="test",
            observed_at=OBSERVED_EARLY,
        )
        assert connection.in_transaction is True
        assert imported.kakera_state_observation_id > 0
        with connect(database_path) as observer:
            assert observer.execute(
                "SELECT COUNT(*) FROM kakera_state_observations"
            ).fetchone()[0] == 0
        connection.rollback()


def test_failed_import_rolls_back_and_same_database_recovers(tmp_path) -> None:
    database_path, repository = _repositories(tmp_path)
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_kakera_state_observation
            BEFORE INSERT ON kakera_state_observations
            BEGIN
                SELECT RAISE(FAIL, 'forced Kakera-state failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced Kakera-state failure"):
        repository.import_kakera_state(KAKERA_STATE, "Server", "Account", "failed", "test")

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM kakera_state_observations"
        ).fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_kakera_state_observation")

    result = repository.import_kakera_state(
        ZERO_KAKERA_STATE, "Server", "Account", "recovered", "test"
    )
    assert result.import_event_id > 0
    assert repository.kakera_state("Server", "Account").kakera_balance == 0
