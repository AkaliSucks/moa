import multiprocessing
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from moa.cli import main
from moa.services.listener_process_guard import (
    ListenerAlreadyRunningError,
    ListenerProcessGuard,
    ListenerProcessGuardError,
    ListenerProcessGuardResourceError,
)


def _hold_guard_in_child(database_path: str, ready, release) -> None:
    guard = ListenerProcessGuard(Path(database_path))
    guard.acquire()
    ready.set()
    if not release.wait(10):
        raise RuntimeError("parent did not release child guard")
    guard.release()


def _spawn_guard_owner(database_path: Path):
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_guard_in_child,
        args=(str(database_path), ready, release),
    )
    process.start()
    if not ready.wait(10):
        process.terminate()
        process.join(10)
        pytest.fail("child did not acquire listener guard")
    return process, release


def test_guard_rejects_same_key_until_owner_releases(tmp_path) -> None:
    database_path = tmp_path / "moa.db"
    owner = ListenerProcessGuard(database_path)
    competitor = ListenerProcessGuard(database_path)

    owner.acquire()
    with pytest.raises(ListenerAlreadyRunningError):
        competitor.acquire()
    assert not competitor.is_acquired

    owner.release()
    competitor.acquire()
    assert competitor.is_acquired
    competitor.release()


def test_guard_allows_distinct_database_keys(tmp_path) -> None:
    first = ListenerProcessGuard(tmp_path / "first.db")
    second = ListenerProcessGuard(tmp_path / "second.db")

    first.acquire()
    second.acquire()

    assert first.is_acquired
    assert second.is_acquired
    second.release()
    first.release()


def test_guard_canonicalizes_supported_path_aliases(tmp_path) -> None:
    database_path = tmp_path / "moa.db"
    alias_path = tmp_path / "unused" / ".." / "moa.db"
    owner = ListenerProcessGuard(alias_path)
    competitor = ListenerProcessGuard(database_path.resolve())

    assert owner.database_path == competitor.database_path
    assert owner.sidecar_path == database_path.with_name("moa.db.listener.lock")
    owner.acquire()
    with pytest.raises(ListenerAlreadyRunningError):
        competitor.acquire()
    owner.release()


def test_stale_sidecar_file_is_not_ownership(tmp_path) -> None:
    guard = ListenerProcessGuard(tmp_path / "moa.db")
    guard.sidecar_path.write_bytes(b"\0")

    guard.acquire()

    assert guard.is_acquired
    guard.release()
    assert guard.sidecar_path.exists()


def test_same_process_competing_thread_is_rejected(tmp_path) -> None:
    database_path = tmp_path / "moa.db"
    ready = threading.Event()
    release = threading.Event()
    failures: list[BaseException] = []

    def hold_guard() -> None:
        try:
            with ListenerProcessGuard(database_path):
                ready.set()
                if not release.wait(10):
                    raise RuntimeError("test did not release owner thread")
        except BaseException as error:
            failures.append(error)

    owner_thread = threading.Thread(target=hold_guard)
    owner_thread.start()
    assert ready.wait(10)
    try:
        with pytest.raises(ListenerAlreadyRunningError):
            ListenerProcessGuard(database_path).acquire()
    finally:
        release.set()
        owner_thread.join(10)

    assert not owner_thread.is_alive()
    assert failures == []


def test_cross_process_owner_conflicts_then_releases(tmp_path) -> None:
    database_path = tmp_path / "moa.db"
    process, release = _spawn_guard_owner(database_path)
    try:
        with pytest.raises(ListenerAlreadyRunningError):
            ListenerProcessGuard(database_path).acquire()
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(10)

    assert process.exitcode == 0
    with ListenerProcessGuard(database_path) as recovered:
        assert recovered.is_acquired


def test_hard_process_termination_releases_os_lock(tmp_path) -> None:
    database_path = tmp_path / "moa.db"
    process, _release = _spawn_guard_owner(database_path)
    try:
        with pytest.raises(ListenerAlreadyRunningError):
            ListenerProcessGuard(database_path).acquire()
    finally:
        process.terminate()
        process.join(10)

    assert not process.is_alive()
    with ListenerProcessGuard(database_path) as recovered:
        assert recovered.is_acquired


def test_structurally_invalid_resource_is_not_reported_as_contention(tmp_path) -> None:
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("ordinary file", encoding="utf-8")
    guard = ListenerProcessGuard(parent_file / "moa.db")

    with pytest.raises(ListenerProcessGuardResourceError) as error:
        guard.acquire()

    assert not isinstance(error.value, ListenerAlreadyRunningError)
    assert not guard.is_acquired


@pytest.mark.parametrize("database_path", [Path(":memory:"), Path("file:moa.db")])
def test_non_file_backed_database_forms_fail_closed(database_path) -> None:
    with pytest.raises(ListenerProcessGuardResourceError, match="file-backed"):
        ListenerProcessGuard(database_path)


def test_double_acquire_is_clear_and_double_release_cannot_unlock_new_owner(tmp_path) -> None:
    database_path = tmp_path / "moa.db"
    first = ListenerProcessGuard(database_path)
    first.acquire()
    with pytest.raises(ListenerProcessGuardError, match="already acquired"):
        first.acquire()
    first.release()

    second = ListenerProcessGuard(database_path)
    second.acquire()
    first.release()
    with pytest.raises(ListenerAlreadyRunningError):
        ListenerProcessGuard(database_path).acquire()
    second.release()


def test_cli_surfaces_listener_conflict_without_token_or_traceback(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "moa.db"
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)
    owner = ListenerProcessGuard(database_path)
    owner.acquire()
    try:
        result = CliRunner().invoke(
            main.app,
            ["discord", "listen", "--token", "test-token"],
        )
    finally:
        owner.release()

    assert result.exit_code == 1
    assert "Another MOA listener already owns database" in result.stdout
    assert str(database_path.resolve()) in result.stdout.replace("\n", "")
    assert "test-token" not in result.stdout
    assert "Traceback" not in result.stdout


def test_cli_distinguishes_listener_resource_failure(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "moa.db"
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", database_path)

    class ResourceFailureListener:
        def __init__(self, **_kwargs) -> None:
            pass

        def run(self, _token, _mudae_user_id) -> None:
            raise ListenerProcessGuardResourceError(
                f"Could not lock the listener resource for database {database_path.resolve()}."
            )

    monkeypatch.setattr(main, "DiscordListenerService", ResourceFailureListener)
    result = CliRunner().invoke(
        main.app,
        ["discord", "listen", "--token", "test-token"],
    )

    assert result.exit_code == 1
    assert "Listener ownership unavailable" in result.stdout
    assert "test-token" not in result.stdout
    assert "Traceback" not in result.stdout
