import json
import sqlite3
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import WishlistEntry, WishlistSnapshot
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories import wishlist_repository as wishlist_repository_module
from moa.repositories.wishlist_repository import (
    WishlistRepository,
    _WishlistImportConnectionResult,
)


OBSERVED_AT = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


def _snapshot() -> WishlistSnapshot:
    return WishlistSnapshot(
        wishlist_count=0,
        wishlist_capacity=0,
        starwish_count=0,
        starwish_capacity=0,
        entries=(
            WishlistEntry(
                name="First",
                is_starwish=False,
                is_owned_marker_present=True,
                kakera_marker_present=False,
            ),
            WishlistEntry(
                name="Duplicate",
                is_starwish=True,
                is_owned_marker_present=False,
                kakera_marker_present=True,
            ),
            WishlistEntry(
                name="Duplicate",
                is_starwish=False,
                is_owned_marker_present=False,
                kakera_marker_present=False,
            ),
        ),
    )


def _repositories(tmp_path):
    database_path = tmp_path / "wishlist.db"
    CatalogRepository(database_path)
    return database_path, WishlistRepository(database_path)


def test_constructor_uses_explicit_path_without_bootstrap(tmp_path) -> None:
    database_path = tmp_path / "not-created.db"

    repository = WishlistRepository(database_path)

    assert repository.database_path == database_path
    assert not database_path.exists()


def test_import_read_preserves_zero_empty_absent_and_ordered_duplicate_markers(tmp_path) -> None:
    database_path, repository = _repositories(tmp_path)

    assert repository.wishlist("Server", "Account") is None

    result = repository.import_wishlist(
        _snapshot(), "  Server  ", "  Account  ", "wishlist payload", "clipboard"
    )
    observation = repository.wishlist(" server ", " account ")

    assert result.server_name == "Server"
    assert result.account_name == "Account"
    assert observation is not None
    assert observation.wishlist_count == 0
    assert observation.wishlist_capacity == 0
    assert observation.starwish_count == 0
    assert observation.starwish_capacity == 0
    assert observation.entries == _snapshot().entries

    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT import_event_id, account_context_id, wishlist_count, wishlist_capacity,
                   starwish_count, starwish_capacity, entries_json
            FROM wishlist_observations
            WHERE id = ?
            """,
            (result.import_event_id,),
        ).fetchone()
        assert row["import_event_id"] == result.import_event_id
        assert row["wishlist_count"] == 0
        assert row["wishlist_capacity"] == 0
        assert row["starwish_count"] == 0
        assert row["starwish_capacity"] == 0
        assert json.loads(row["entries_json"]) == [
            entry.model_dump() for entry in _snapshot().entries
        ]


def test_import_is_append_only_latest_by_id_and_reuses_normalized_contexts(tmp_path) -> None:
    database_path, repository = _repositories(tmp_path)

    first = repository.import_wishlist(
        _snapshot(), "Server", "Account", "first", "discord"
    )
    second_snapshot = WishlistSnapshot(
        wishlist_count=1,
        wishlist_capacity=2,
        starwish_count=1,
        starwish_capacity=2,
        entries=(),
    )
    second = repository.import_wishlist(
        second_snapshot, " SERVER ", " ACCOUNT ", "second", "discord"
    )

    latest = repository.wishlist("SERVER", "ACCOUNT")
    assert latest is not None
    assert latest.entries == ()
    assert latest.wishlist_count == 1
    assert second.import_event_id > first.import_event_id

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM wishlist_observations").fetchone()[0] == 2


def test_public_import_owns_one_runner(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _database_path, repository = _repositories(tmp_path)
    original = wishlist_repository_module.run_write_transaction
    calls = []

    def counted(path, callback):
        calls.append(path)
        return original(path, callback)

    monkeypatch.setattr(wishlist_repository_module, "run_write_transaction", counted)

    repository.import_wishlist(_snapshot(), "Server", "Account", "payload", "discord")

    assert calls == [repository.database_path]


def test_supplied_connection_helper_is_transaction_neutral_and_rolls_back(tmp_path, monkeypatch) -> None:
    database_path, repository = _repositories(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("supplied helper opened or owned another transaction")

    monkeypatch.setattr(wishlist_repository_module, "connect", forbidden)
    monkeypatch.setattr(wishlist_repository_module, "run_write_transaction", forbidden)

    with connect(database_path) as connection:
        connection.execute("BEGIN")
        imported = repository._import_wishlist_with_connection(
            connection,
            state=_snapshot(),
            server="Server",
            account="Account",
            raw="payload",
            source="discord",
            observed_at=OBSERVED_AT,
        )
        assert isinstance(imported, _WishlistImportConnectionResult)
        assert connection.in_transaction
        assert connection.execute("SELECT COUNT(*) FROM wishlist_observations").fetchone()[0] == 1
        connection.rollback()

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM wishlist_observations").fetchone()[0] == 0


def test_late_failure_rolls_back_and_same_database_recovers(tmp_path, monkeypatch) -> None:
    _database_path, repository = _repositories(tmp_path)
    original = repository._import_wishlist_with_connection

    def fail_after_write(connection: sqlite3.Connection, **kwargs):
        original(connection, **kwargs)
        raise RuntimeError("forced wishlist failure")

    monkeypatch.setattr(repository, "_import_wishlist_with_connection", fail_after_write)
    with pytest.raises(RuntimeError, match="forced wishlist failure"):
        repository.import_wishlist(_snapshot(), "Server", "Account", "failed", "discord")

    with connect(repository.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM wishlist_observations").fetchone()[0] == 0

    monkeypatch.setattr(repository, "_import_wishlist_with_connection", original)
    result = repository.import_wishlist(_snapshot(), "Server", "Account", "success", "discord")
    assert result.import_event_id > 0
    assert repository.wishlist("Server", "Account") is not None
