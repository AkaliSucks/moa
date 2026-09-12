from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from moa.database.writer_lease import (
    DatabaseWriterLeaseContendedError,
    DatabaseWriterLeaseResourceError,
    ExclusiveQuiescenceStatus,
    database_writer_lease_path,
    shared_database_writer_lease,
    try_acquire_exclusive_database_quiescence,
)
from moa.repositories.catalog_repository import CatalogRepository


_PROCESS_TIMEOUT = 10


def _hold_lease_in_child(profile_root: str, mode: str, ready, release) -> None:
    root = Path(profile_root)
    if mode == "shared":
        with shared_database_writer_lease(root):
            ready.set()
            if not release.wait(_PROCESS_TIMEOUT):
                raise RuntimeError("parent did not release shared lease child")
        return
    attempt = try_acquire_exclusive_database_quiescence(root)
    if not attempt.acquired or attempt.lease is None:
        raise RuntimeError(f"child exclusive acquisition failed: {attempt.status}")
    with attempt.lease:
        ready.set()
        if not release.wait(_PROCESS_TIMEOUT):
            raise RuntimeError("parent did not release exclusive lease child")


def _spawn_owner(profile_root: Path, mode: str):
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_lease_in_child,
        args=(str(profile_root), mode, ready, release),
    )
    process.start()
    if not ready.wait(_PROCESS_TIMEOUT):
        process.terminate()
        process.join(_PROCESS_TIMEOUT)
        pytest.fail(f"child did not acquire {mode} database-writer lease")
    return process, release


def _stop_owner(process, release) -> None:
    release.set()
    process.join(_PROCESS_TIMEOUT)
    if process.is_alive():
        process.terminate()
        process.join(_PROCESS_TIMEOUT)
    assert process.exitcode == 0


def test_shared_writer_lease_initializes_stable_profile_artifact(tmp_path) -> None:
    root = tmp_path / "profile"

    with shared_database_writer_lease(root) as lease:
        assert lease.path == root.resolve() / "database-writer.lease"
        assert lease.is_acquired
        assert not lease.exclusive

    assert database_writer_lease_path(root).read_bytes() == (
        b"MOA_DATABASE_WRITER_LEASE_V1\n"
    )


def test_two_shared_writers_coexist_across_processes(tmp_path) -> None:
    process, release = _spawn_owner(tmp_path, "shared")
    try:
        with shared_database_writer_lease(tmp_path) as second:
            assert second.is_acquired
    finally:
        _stop_owner(process, release)


def test_active_shared_writer_prevents_exclusive_quiescence(tmp_path) -> None:
    process, release = _spawn_owner(tmp_path, "shared")
    try:
        attempt = try_acquire_exclusive_database_quiescence(tmp_path)
        assert attempt.status is ExclusiveQuiescenceStatus.CONTENDED
        assert attempt.lease is None
        assert attempt.error is None
    finally:
        _stop_owner(process, release)


def test_exclusive_quiescence_prevents_new_shared_writer(tmp_path) -> None:
    process, release = _spawn_owner(tmp_path, "exclusive")
    try:
        with pytest.raises(DatabaseWriterLeaseContendedError):
            with shared_database_writer_lease(tmp_path):
                pytest.fail("shared lease entered under exclusive ownership")
    finally:
        _stop_owner(process, release)


def test_exclusive_quiescence_succeeds_and_is_held_until_release(tmp_path) -> None:
    attempt = try_acquire_exclusive_database_quiescence(tmp_path)

    assert attempt.status is ExclusiveQuiescenceStatus.ACQUIRED
    assert attempt.lease is not None
    assert attempt.lease.exclusive
    assert try_acquire_exclusive_database_quiescence(tmp_path).status is (
        ExclusiveQuiescenceStatus.CONTENDED
    )

    attempt.lease.release()
    recovered = try_acquire_exclusive_database_quiescence(tmp_path)
    assert recovered.lease is not None
    recovered.lease.release()


@pytest.mark.parametrize("mode", ["shared", "exclusive"])
def test_process_termination_releases_native_lease(tmp_path, mode: str) -> None:
    process, _release = _spawn_owner(tmp_path, mode)
    process.terminate()
    process.join(_PROCESS_TIMEOUT)
    assert not process.is_alive()

    recovered = try_acquire_exclusive_database_quiescence(tmp_path)
    assert recovered.status is ExclusiveQuiescenceStatus.ACQUIRED
    assert recovered.lease is not None
    recovered.lease.release()


def test_stale_artifact_without_owner_is_quiescent_not_contended(tmp_path) -> None:
    with shared_database_writer_lease(tmp_path):
        pass
    artifact = database_writer_lease_path(tmp_path)
    assert artifact.exists()

    attempt = try_acquire_exclusive_database_quiescence(tmp_path)

    assert attempt.status is ExclusiveQuiescenceStatus.ACQUIRED
    assert attempt.lease is not None
    attempt.lease.release()


def test_empty_interrupted_initialization_is_recovered_under_exclusive_lock(
    tmp_path,
) -> None:
    artifact = tmp_path / "database-writer.lease"
    artifact.write_bytes(b"")

    with shared_database_writer_lease(tmp_path):
        assert artifact.read_bytes() == b"MOA_DATABASE_WRITER_LEASE_V1\n"

    attempt = try_acquire_exclusive_database_quiescence(tmp_path)
    assert attempt.status is ExclusiveQuiescenceStatus.ACQUIRED
    assert attempt.lease is not None
    attempt.lease.release()


def test_malformed_artifact_is_unknown_error_not_quiescence(tmp_path) -> None:
    tmp_path.joinpath("database-writer.lease").write_bytes(b"not-a-lease")

    attempt = try_acquire_exclusive_database_quiescence(tmp_path)

    assert attempt.status is ExclusiveQuiescenceStatus.ERROR
    assert attempt.lease is None
    assert isinstance(attempt.error, DatabaseWriterLeaseResourceError)
    with pytest.raises(DatabaseWriterLeaseResourceError, match="malformed"):
        with shared_database_writer_lease(tmp_path):
            pytest.fail("malformed artifact admitted a writer")


def test_non_regular_artifact_fails_closed(tmp_path) -> None:
    tmp_path.joinpath("database-writer.lease").mkdir()

    attempt = try_acquire_exclusive_database_quiescence(tmp_path)

    assert attempt.status is ExclusiveQuiescenceStatus.ERROR
    assert isinstance(attempt.error, DatabaseWriterLeaseResourceError)


def test_nested_shared_acquisition_retains_outer_ownership(tmp_path) -> None:
    with shared_database_writer_lease(tmp_path) as outer:
        with shared_database_writer_lease(tmp_path) as inner:
            assert inner is outer
        assert outer.is_acquired
        assert try_acquire_exclusive_database_quiescence(tmp_path).status is (
            ExclusiveQuiescenceStatus.CONTENDED
        )
    assert not outer.is_acquired

    attempt = try_acquire_exclusive_database_quiescence(tmp_path)
    assert attempt.lease is not None
    attempt.lease.release()


def test_exclusive_probe_does_not_open_or_mutate_sqlite(tmp_path) -> None:
    database = tmp_path / "candidate.db"
    database.write_bytes(b"sentinel database bytes")
    before = database.read_bytes()

    attempt = try_acquire_exclusive_database_quiescence(tmp_path / "profile")

    assert attempt.lease is not None
    assert database.read_bytes() == before
    assert not Path(f"{database}-wal").exists()
    assert not Path(f"{database}-shm").exists()
    attempt.lease.release()


def test_catalog_bootstrap_cannot_open_database_under_exclusive_quiescence(
    tmp_path,
) -> None:
    database = tmp_path / "bootstrap.db"
    attempt = try_acquire_exclusive_database_quiescence()
    assert attempt.lease is not None
    try:
        with pytest.raises(DatabaseWriterLeaseContendedError):
            CatalogRepository(database)
    finally:
        attempt.lease.release()

    assert not database.exists()
    CatalogRepository(database)
    assert database.is_file()
