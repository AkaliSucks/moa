import sqlite3
from datetime import datetime, timezone

import pytest

from moa.database.sqlite import connect
from moa.models.character import ServerSettingMetric, ServerSettingsSnapshot
from moa.repositories.catalog_repository import CatalogRepository
from moa.repositories import server_settings_repository as server_settings_module
from moa.repositories.server_settings_repository import ServerSettingsRepository


SETTINGS = ServerSettingsSnapshot(
    server_premium=False,
    prefix="$",
    language="English",
    claim_reset_minutes=0,
    reset_minute="00",
    reset_shift_minutes=0,
    rolls_per_hour=0,
    claim_reaction_expiry_seconds=0,
    claimed_character_rarity_multiplier=0,
    kakera_bonus_percent=0,
    sphere_bonus_percent=0,
    game_mode=0,
    channel_instance=0,
    metrics=(
        ServerSettingMetric(label="Prefix", value="$"),
        ServerSettingMetric(label="Prefix", value="!"),
    ),
)


def _initialize(path):
    CatalogRepository(path)
    return ServerSettingsRepository(path)


def test_constructor_requires_initialized_explicit_path_and_reads_absence(tmp_path) -> None:
    database_path = tmp_path / "server-settings.db"
    repository = ServerSettingsRepository(database_path)

    assert repository.database_path == database_path
    assert not database_path.exists()

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        repository.server_settings("Server")


def test_standalone_write_read_is_server_scoped_and_preserves_boundaries(tmp_path) -> None:
    database_path = tmp_path / "server-settings.db"
    repository = _initialize(database_path)

    result = repository.import_server_settings(
        SETTINGS, "  Lake Arrowhead 2025  ", "settings payload", "clipboard"
    )
    observation = repository.server_settings("LAKE ARROWHEAD 2025")

    assert result.server_name == "Lake Arrowhead 2025"
    assert observation is not None
    assert observation.server_name == "Lake Arrowhead 2025"
    assert observation.server_premium is False
    assert observation.prefix == "$"
    assert observation.language == "English"
    assert observation.claim_reset_minutes == 0
    assert observation.reset_minute == "00"
    assert observation.reset_shift_minutes == 0
    assert observation.rolls_per_hour == 0
    assert observation.claim_reaction_expiry_seconds == 0
    assert observation.claimed_character_rarity_multiplier == 0
    assert observation.kakera_bonus_percent == 0
    assert observation.sphere_bonus_percent == 0
    assert observation.game_mode == 0
    assert observation.channel_instance == 0
    assert [(metric.label, metric.value) for metric in observation.metrics] == [
        ("Prefix", "$"),
        ("Prefix", "!"),
    ]

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM account_contexts").fetchone()[0] == 0
        event = connection.execute(
            "SELECT kind, source, raw_message FROM import_events WHERE id = ?",
            (result.import_event_id,),
        ).fetchone()
        assert tuple(event) == ("server_settings", "clipboard", "settings payload")
        observation_row = connection.execute(
            """
            SELECT server_context_id, import_event_id
            FROM server_settings_observations WHERE id = (
                SELECT MAX(id) FROM server_settings_observations
            )
            """
        ).fetchone()
        assert observation_row[0] > 0
        assert observation_row[1] == result.import_event_id


def test_standalone_import_uses_one_runner_and_latest_is_id_ordered(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "server-settings.db"
    repository = _initialize(database_path)
    original_runner = server_settings_module.run_write_transaction
    calls = []

    def observed_runner(path, callback):
        calls.append(path)
        return original_runner(path, callback)

    monkeypatch.setattr(server_settings_module, "run_write_transaction", observed_runner)
    repository.import_server_settings(SETTINGS, "Server", "first", "test")
    newer = repository.import_server_settings(
        SETTINGS.model_copy(update={"server_premium": True, "prefix": "!"}),
        " SERVER ",
        "second",
        "test",
    )

    assert calls == [database_path, database_path]
    observation = repository.server_settings("server")
    assert observation is not None
    assert observation.server_premium is True
    assert observation.prefix == "!"
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM server_settings_observations").fetchone()[0] == 2
        assert connection.execute(
            "SELECT import_event_id FROM server_settings_observations ORDER BY id DESC LIMIT 1"
        ).fetchone()[0] == newer.import_event_id


def test_supplied_connection_is_transaction_neutral_and_caller_controls_commit(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "server-settings.db"
    repository = _initialize(database_path)

    def unexpected_connection(*args, **kwargs):
        raise AssertionError("supplied helper opened an independent connection")

    def unexpected_runner(*args, **kwargs):
        raise AssertionError("supplied helper started an independent transaction")

    monkeypatch.setattr(server_settings_module, "connect", unexpected_connection)
    monkeypatch.setattr(server_settings_module, "run_write_transaction", unexpected_runner)

    with connect(database_path) as connection:
        imported = repository._import_server_settings_with_connection(
            connection,
            settings=SETTINGS,
            server="Server",
            raw="payload",
            source="test",
            observed_at=datetime.now(timezone.utc),
        )
        assert connection.in_transaction
        assert imported.import_event_id > 0
        connection.commit()

    monkeypatch.undo()
    assert repository.server_settings("Server") is not None


def test_supplied_connection_rollback_removes_all_rows_and_recovery_succeeds(tmp_path) -> None:
    database_path = tmp_path / "server-settings.db"
    repository = _initialize(database_path)

    with pytest.raises(RuntimeError, match="forced failure"):
        with connect(database_path) as connection:
            repository._import_server_settings_with_connection(
                connection,
                settings=SETTINGS,
                server="Server",
                raw="payload",
                source="test",
                observed_at=datetime.now(timezone.utc),
            )
            raise RuntimeError("forced failure")

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_contexts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_settings_observations").fetchone()[0] == 0

    assert repository.import_server_settings(SETTINGS, "Server", "recovery", "test").import_event_id > 0
