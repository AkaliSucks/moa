import sqlite3
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import KakeralootSettingsSnapshot
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories import kakeraloot_settings_repository as repository_module
from moa.repositories.kakeraloot_settings_repository import (
    KakeralootSettingsRepository,
    _KakeralootSettingsImportConnectionResult,
)


ZERO_SETTINGS = KakeralootSettingsSnapshot(
    loot_cost=0,
    quantity_quality_base_cost=0,
    quantity_quality_level_increment=0,
)
POSITIVE_SETTINGS = KakeralootSettingsSnapshot(
    loot_cost=500,
    quantity_quality_base_cost=2000,
    quantity_quality_level_increment=200,
)
OBSERVED_AT = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


def _initialize(path):
    CatalogRepository(path)
    return KakeralootSettingsRepository(path)


def test_connection_result_is_frozen_slotted_with_exact_fields() -> None:
    assert is_dataclass(_KakeralootSettingsImportConnectionResult)
    assert _KakeralootSettingsImportConnectionResult.__dataclass_params__.frozen is True
    assert [field.name for field in fields(_KakeralootSettingsImportConnectionResult)] == [
        "import_event_id",
        "kakeraloot_settings_observation_id",
    ]
    assert not hasattr(_KakeralootSettingsImportConnectionResult(1, 2), "__dict__")


def test_constructor_requires_initialized_explicit_path_and_reads_absence(tmp_path) -> None:
    database_path = tmp_path / "kakeraloot-settings.db"
    repository = KakeralootSettingsRepository(database_path)

    assert repository.database_path == database_path
    assert not database_path.exists()

    with pytest.raises(sqlite3.OperationalError, match="unable to open database file"):
        repository.kakeraloot_settings("Server")

    initialized = _initialize(database_path)
    assert initialized.kakeraloot_settings("Server") is None


def test_standalone_write_read_is_server_scoped_and_preserves_zero_positive_and_linkage(
    tmp_path,
) -> None:
    database_path = tmp_path / "kakeraloot-settings.db"
    repository = _initialize(database_path)

    zero_result = repository.import_kakeraloot_settings(
        ZERO_SETTINGS, "  Lake One  ", "zero payload", "clipboard"
    )
    positive_result = repository.import_kakeraloot_settings(
        POSITIVE_SETTINGS, "LAKE ONE", "positive payload", "discord"
    )
    other_result = repository.import_kakeraloot_settings(
        ZERO_SETTINGS, "Lake Two", "other payload", "clipboard"
    )

    assert zero_result.server_name == "Lake One"
    assert positive_result.server_name == "LAKE ONE"
    assert repository.kakeraloot_settings(" lake   one ").model_dump() == {
        "server_name": "LAKE ONE",
        "loot_cost": 500,
        "quantity_quality_base_cost": 2000,
        "quantity_quality_level_increment": 200,
        "observed_at": positive_result.observed_at,
    }
    assert repository.kakeraloot_settings("Lake Two").loot_cost == 0
    assert repository.kakeraloot_settings("Unknown") is None

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM kakeraloot_settings_observations"
        ).fetchone()[0] == 3
        event = connection.execute(
            "SELECT kind, source, raw_message FROM import_events WHERE id = ?",
            (positive_result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("kakeraloot_settings", "discord", "positive payload")
        row = connection.execute(
            """
            SELECT server_context_id, loot_cost, quantity_quality_base_cost,
                   quantity_quality_level_increment, observed_at, import_event_id
            FROM kakeraloot_settings_observations
            WHERE id = (SELECT MAX(id) FROM kakeraloot_settings_observations
                        WHERE import_event_id = ?)
            """,
            (positive_result.import_event_id,),
        ).fetchone()
        assert tuple(row)[1:] == (
            500,
            2000,
            200,
            positive_result.observed_at.isoformat(),
            positive_result.import_event_id,
        )
        assert row["server_context_id"] == connection.execute(
            "SELECT id FROM server_contexts WHERE normalized_name = ?", ("lake one",)
        ).fetchone()[0]
        assert other_result.import_event_id > positive_result.import_event_id > zero_result.import_event_id


def test_standalone_import_owns_exactly_one_runner_and_latest_is_id_ordered(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "kakeraloot-settings.db"
    repository = _initialize(database_path)
    original_runner = repository_module.run_write_transaction
    calls = []

    def observed_runner(path, callback):
        calls.append(path)
        return original_runner(path, callback)

    monkeypatch.setattr(repository_module, "run_write_transaction", observed_runner)
    repository.import_kakeraloot_settings(ZERO_SETTINGS, "Server", "first", "test")
    newer = repository.import_kakeraloot_settings(
        POSITIVE_SETTINGS, " SERVER ", "second", "test"
    )

    assert calls == [database_path, database_path]
    assert repository.kakeraloot_settings("server").loot_cost == 500
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM kakeraloot_settings_observations"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT import_event_id FROM kakeraloot_settings_observations ORDER BY id DESC LIMIT 1"
        ).fetchone()[0] == newer.import_event_id


def test_supplied_connection_is_transaction_neutral_and_caller_controls_commit(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "kakeraloot-settings.db"
    repository = _initialize(database_path)

    def unexpected_connection(*args, **kwargs):
        raise AssertionError("supplied helper opened an independent connection")

    def unexpected_runner(*args, **kwargs):
        raise AssertionError("supplied helper started an independent transaction")

    monkeypatch.setattr(repository_module, "connect_read_only", unexpected_connection)
    monkeypatch.setattr(repository_module, "run_write_transaction", unexpected_runner)

    with connect(database_path) as connection:
        imported = repository._import_kakeraloot_settings_with_connection(
            connection,
            settings=POSITIVE_SETTINGS,
            server="Server",
            raw="payload",
            source="test",
            observed_at=OBSERVED_AT,
        )
        assert connection.in_transaction
        assert imported.import_event_id > 0
        connection.commit()

    monkeypatch.undo()
    assert repository.kakeraloot_settings("Server").quantity_quality_base_cost == 2000


def test_supplied_connection_rollback_removes_all_rows_and_recovery_succeeds(tmp_path) -> None:
    database_path = tmp_path / "kakeraloot-settings.db"
    repository = _initialize(database_path)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            repository._import_kakeraloot_settings_with_connection(
                connection,
                settings=POSITIVE_SETTINGS,
                server="Server",
                raw="payload",
                source="test",
                observed_at=OBSERVED_AT,
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM kakeraloot_settings_observations"
        ).fetchone()[0] == 0

    result = repository.import_kakeraloot_settings(POSITIVE_SETTINGS, "Server", "recovery", "test")
    assert result.import_event_id > 0
    assert repository.kakeraloot_settings("Server") is not None
