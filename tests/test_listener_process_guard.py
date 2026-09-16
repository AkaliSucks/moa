import multiprocessing
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

import moa.cli.discord_commands as discord_commands_module
import moa.services.discord_listener_service as listener_service_module
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


def test_acquisition_cleanup_preserves_primary_failure_when_close_also_fails(
    monkeypatch, tmp_path
) -> None:
    database_path = tmp_path / "moa.db"
    guard = ListenerProcessGuard(database_path)

    class CloseFailureHandle:
        def seek(self, *_args) -> None:
            pass

        def tell(self) -> int:
            return 1

        def close(self) -> None:
            raise RuntimeError("secondary close failure")

    handle = CloseFailureHandle()
    original_open = Path.open

    def open_sidecar(path, *args, **kwargs):
        if path == guard.sidecar_path:
            return handle
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_sidecar)
    monkeypatch.setattr(
        guard,
        "_acquire_os_lock",
        lambda _handle: (_ for _ in ()).throw(
            ListenerProcessGuardResourceError("primary acquisition failure")
        ),
    )

    with pytest.raises(ListenerProcessGuardResourceError) as error:
        guard.acquire()

    assert str(error.value) == "primary acquisition failure"
    assert any("secondary close failure" in note for note in error.value.__notes__)
    assert not guard.is_acquired

    monkeypatch.undo()
    recovered = ListenerProcessGuard(database_path)
    recovered.acquire()
    recovered.release()


def test_baseexception_during_acquisition_releases_local_identity(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "moa.db"
    guard = ListenerProcessGuard(database_path)
    monkeypatch.setattr(
        guard,
        "_ensure_lock_byte",
        lambda _handle: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        guard.acquire()

    assert not guard.is_acquired
    recovered = ListenerProcessGuard(database_path)
    recovered.acquire()
    recovered.release()


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


def test_contended_cli_stops_before_listener_composition(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "database" / "moa.db"
    config_path = tmp_path / "config.json"
    monkeypatch.setenv("MOA_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("MOA_DATABASE_PATH", str(database_path))

    def unexpected(*_args, **_kwargs):
        pytest.fail("contended listener reached repository or Gateway composition")

    monkeypatch.setattr(discord_commands_module, "CatalogRepository", unexpected)
    monkeypatch.setattr(discord_commands_module, "DiscordMessageRepository", unexpected)
    monkeypatch.setattr(discord_commands_module, "DiscordListenerService", unexpected)
    monkeypatch.setattr(listener_service_module, "_MOADiscordClient", unexpected)

    with ListenerProcessGuard(database_path):
        result = CliRunner().invoke(
            main.app, ["discord", "listen", "--token", "test-token"]
        )

    assert result.exit_code == 1
    assert "Another MOA listener already owns database" in result.stdout
    assert not database_path.exists()
    assert not database_path.with_name("moa.db-wal").exists()
    assert not database_path.with_name("moa.db-shm").exists()
    assert not database_path.parent.joinpath("database-writer.lease").exists()
    assert not config_path.exists()


def test_cli_uses_one_guard_for_composition_and_run(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "database" / "moa.db"
    monkeypatch.setenv("MOA_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setenv("MOA_DATABASE_PATH", str(database_path))
    acquired = 0
    released = 0
    client_runs = 0
    original_acquire = ListenerProcessGuard.acquire
    original_release = ListenerProcessGuard.release

    def acquire(guard):
        nonlocal acquired
        original_acquire(guard)
        acquired += 1

    def release(guard):
        nonlocal released
        released += 1
        original_release(guard)

    class FakeClient:
        def __init__(self, _listener, **_kwargs):
            assert acquired == 1
            assert released == 0

        def run(self, _token):
            nonlocal client_runs
            client_runs += 1
            with pytest.raises(ListenerAlreadyRunningError):
                ListenerProcessGuard(database_path).acquire()

    monkeypatch.setattr(ListenerProcessGuard, "acquire", acquire)
    monkeypatch.setattr(ListenerProcessGuard, "release", release)
    monkeypatch.setattr(listener_service_module, "_MOADiscordClient", FakeClient)

    result = CliRunner().invoke(main.app, ["discord", "listen", "--token", "test-token"])

    assert result.exit_code == 0, result.exception
    assert database_path.exists()
    assert (acquired, released, client_runs) == (1, 1, 1)
    with ListenerProcessGuard(database_path) as recovered:
        assert recovered.is_acquired


def test_cli_releases_guard_after_repository_composition_failure(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "database" / "moa.db"
    monkeypatch.setenv("MOA_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setenv("MOA_DATABASE_PATH", str(database_path))

    def fail_catalog(_database_path):
        with pytest.raises(ListenerAlreadyRunningError):
            ListenerProcessGuard(database_path).acquire()
        raise RuntimeError("repository composition failed")

    monkeypatch.setattr(discord_commands_module, "CatalogRepository", fail_catalog)

    result = CliRunner().invoke(main.app, ["discord", "listen", "--token", "test-token"])

    assert isinstance(result.exception, RuntimeError)
    assert str(result.exception) == "repository composition failed"
    assert not database_path.exists()
    with ListenerProcessGuard(database_path) as recovered:
        assert recovered.is_acquired


def test_cli_listener_guard_follows_runtime_platform_database(monkeypatch, tmp_path) -> None:
    from moa.database import legacy_database_relocation, sqlite

    platform_root = tmp_path / "user-data" / "moa"
    target = platform_root / "moa.db"
    captured: dict[str, object] = {}
    # Exercise the patched disposable platform provider rather than the test runner override.
    monkeypatch.delenv("MOA_DATABASE_PATH", raising=False)
    monkeypatch.setattr(sqlite, "user_data_path", lambda **_kwargs: platform_root)
    monkeypatch.setattr(
        legacy_database_relocation,
        "_source_file_path",
        lambda: tmp_path / "site-packages" / "moa" / "database" / "module.py",
    )

    class RecordingListener:
        def __init__(self, **kwargs) -> None:
            captured["database_path"] = kwargs["database_path"]
            captured["catalog_path"] = kwargs["catalog_service"].database_path
            captured["discord_path"] = kwargs["discord_message_repository"].database_path
            captured["guard"] = ListenerProcessGuard(kwargs["database_path"])

        def run(self, _token, _mudae_user_id) -> None:
            return None

    monkeypatch.setattr(discord_commands_module, "DiscordListenerService", RecordingListener)

    result = CliRunner().invoke(
        main.app,
        ["discord", "listen", "--token", "test-token"],
    )

    assert result.exit_code == 0
    assert captured["database_path"] == target
    assert captured["catalog_path"] == target
    assert captured["discord_path"] == target
    guard = captured["guard"]
    assert guard.sidecar_path == target.with_name("moa.db.listener.lock")
    assert "test-token" not in result.stdout


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

    monkeypatch.setattr(discord_commands_module, "DiscordListenerService", ResourceFailureListener)
    result = CliRunner().invoke(
        main.app,
        ["discord", "listen", "--token", "test-token"],
    )

    assert result.exit_code == 1
    assert "Listener ownership unavailable" in result.stdout
    assert "test-token" not in result.stdout
    assert "Traceback" not in result.stdout
