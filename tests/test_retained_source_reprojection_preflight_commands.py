from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.main import get_command
from typer.testing import CliRunner

import moa.cli.main as main
import moa.cli.retained_source_reprojection_preflight_commands as command_module
from moa.repositories.catalog_repository import CatalogRepository
from moa.services.retained_source_reprojection_preflight_service import (
    ReprojectionPreflightEligibility,
    ReprojectionPreflightFamilyTotal,
    ReprojectionPreflightFailure,
    ReprojectionPreflightReasonTotal,
    RetainedSourceReprojectionPreflight,
    RetainedSourceReprojectionPreflightError,
    RetainedSourceReprojectionPreflightRecord,
)


def _report() -> RetainedSourceReprojectionPreflight:
    return RetainedSourceReprojectionPreflight(
        current_generation_id=4,
        hypothetical_generation_id=5,
        records=(
            RetainedSourceReprojectionPreflightRecord(
                20, "roll", ReprojectionPreflightEligibility.ELIGIBLE, None, 3
            ),
            RetainedSourceReprojectionPreflightRecord(
                3,
                "unknown",
                ReprojectionPreflightEligibility.UNKNOWN,
                "expectations_unknown",
                None,
            ),
            RetainedSourceReprojectionPreflightRecord(
                11,
                "antidisable",
                ReprojectionPreflightEligibility.INELIGIBLE,
                "source_expired",
                None,
            ),
        ),
        family_totals=(
            ReprojectionPreflightFamilyTotal("roll", 1, 1, 0, 0),
            ReprojectionPreflightFamilyTotal("antidisable", 1, 0, 1, 0),
            ReprojectionPreflightFamilyTotal("unknown", 1, 0, 0, 1),
        ),
        reason_totals=(
            ReprojectionPreflightReasonTotal("source_expired", 1),
            ReprojectionPreflightReasonTotal("expectations_unknown", 1),
        ),
        inventory_fingerprint="a" * 64,
    )


class _RecordingConnection:
    def __init__(self, connection: sqlite3.Connection, events: list[str]) -> None:
        self._connection = connection
        self._events = events

    @property
    def in_transaction(self) -> bool:
        return self._connection.in_transaction

    def execute(self, sql: str):
        self._events.append(sql)
        return self._connection.execute(sql)

    def rollback(self) -> None:
        self._events.append("rollback")
        self._connection.rollback()

    def close(self) -> None:
        self._events.append("close")
        self._connection.close()


def test_registration_requires_only_an_explicit_database_path() -> None:
    command = (
        get_command(main.app).commands["catalog"].commands["retained-source-reprojection-preflight"]
    )
    assert [parameter.name for parameter in command.params] == ["database_path"]
    assert command.params[0].required

    result = CliRunner().invoke(main.app, ["catalog", "retained-source-reprojection-preflight"])

    assert result.exit_code == 2
    assert "DATABASE_PATH" in result.output


def test_preflight_renders_deterministic_safe_fields_and_cleans_up_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "isolated" / "catalog.sqlite3"
    database_path.parent.mkdir()
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE marker (value TEXT)")
    events: list[str] = []
    real_connection = sqlite3.connect(database_path)
    wrapped_connection = _RecordingConnection(real_connection, events)

    def open_read_only(path: Path):
        assert path == database_path
        return wrapped_connection

    class FakeService:
        def preflight(self, connection):
            assert connection.in_transaction
            return _report()

    monkeypatch.setattr(command_module, "connect_read_only", open_read_only)
    monkeypatch.setattr(command_module, "RetainedSourceReprojectionPreflightService", FakeService)

    result = CliRunner().invoke(
        main.app,
        ["catalog", "retained-source-reprojection-preflight", str(database_path)],
    )

    assert result.exit_code == 0
    assert events == ["BEGIN", "rollback", "close"]
    assert result.stdout.index("antidisable") < result.stdout.index("roll")
    assert result.stdout.index("expectations_unknown") < result.stdout.index("source_expired")
    assert result.stdout.index("3") < result.stdout.index("11") < result.stdout.index("20")
    assert "Current generation ID: 4" in result.stdout
    assert "Hypothetical generation ID: 5" in result.stdout
    assert "Inventory fingerprint: " + "a" * 64 in result.stdout
    assert "private" not in result.stdout.lower()
    assert "raw" not in result.stdout.lower()
    assert str(database_path) not in result.stdout


def test_absent_explicit_path_fails_without_creating_file_or_parent(tmp_path: Path) -> None:
    database_path = tmp_path / "not-created" / "catalog.sqlite3"

    result = CliRunner().invoke(
        main.app,
        ["catalog", "retained-source-reprojection-preflight", str(database_path)],
    )

    assert result.exit_code == 1
    assert not database_path.exists()
    assert not database_path.parent.exists()
    assert "Unable to open or inspect the explicit database path" in result.stdout
    assert str(database_path) not in result.stdout


def test_real_empty_catalog_is_read_only_and_renders_no_private_data(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    CatalogRepository(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = database_path.read_bytes()

    result = CliRunner().invoke(
        main.app,
        ["catalog", "retained-source-reprojection-preflight", str(database_path)],
    )

    assert result.exit_code == 0
    assert "Current generation ID: 1" in result.stdout
    assert "Hypothetical generation ID: 2" in result.stdout
    assert "Inventory fingerprint: " in result.stdout
    assert "private" not in result.stdout.lower()
    assert database_path.read_bytes() == before


@pytest.mark.parametrize(
    "failure", sorted(command_module._BOUNDED_FAILURES, key=lambda item: item.value)
)
def test_bounded_preflight_failures_are_safe_and_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: ReprojectionPreflightFailure,
) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE marker (value TEXT)")

    class FakeService:
        def preflight(self, connection):
            raise RetainedSourceReprojectionPreflightError(failure)

    monkeypatch.setattr(command_module, "RetainedSourceReprojectionPreflightService", FakeService)
    result = CliRunner().invoke(
        main.app,
        ["catalog", "retained-source-reprojection-preflight", str(database_path)],
    )

    assert result.exit_code == 1
    assert f"Preflight failed: {failure.value}" in result.stdout
    assert "Traceback" not in result.stdout


def test_private_failure_detail_is_not_rendered_and_database_bytes_are_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE marker (value TEXT)")
    before = database_path.read_bytes()
    private_detail = "PRIVATE RAW MESSAGE account=private server=private"

    class FakeService:
        def preflight(self, connection):
            raise RuntimeError(private_detail)

    monkeypatch.setattr(command_module, "RetainedSourceReprojectionPreflightService", FakeService)
    result = CliRunner().invoke(
        main.app,
        ["catalog", "retained-source-reprojection-preflight", str(database_path)],
    )

    assert result.exit_code == 1
    assert "no result is" in result.stdout
    assert private_detail not in result.stdout
    assert "PRIVATE" not in result.stdout
    assert database_path.read_bytes() == before
