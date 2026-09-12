"""Profile-global OS coordination for MOA-supported database writers."""

from __future__ import annotations

import errno
import os
import stat
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO

from platformdirs import user_data_path

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes
else:
    import fcntl


_LEASE_FILE_NAME = "database-writer.lease"
_LEASE_MAGIC = b"MOA_DATABASE_WRITER_LEASE_V1\n"
_LOCK_OFFSET = 0
_LOCK_LENGTH = 1
_WINDOWS_LOCKFILE_FAIL_IMMEDIATELY = 0x00000001
_WINDOWS_LOCKFILE_EXCLUSIVE_LOCK = 0x00000002
_WINDOWS_ERROR_LOCK_VIOLATION = 33


class DatabaseWriterLeaseError(RuntimeError):
    """Base error for profile-global database-writer coordination."""


class DatabaseWriterLeaseContendedError(DatabaseWriterLeaseError):
    """Raised when an incompatible database-writer lease is active."""


class DatabaseWriterLeaseResourceError(DatabaseWriterLeaseError):
    """Raised when the lease artifact or native lock cannot be trusted."""


class ExclusiveQuiescenceStatus(StrEnum):
    """Result states for one nonblocking exclusive-quiescence attempt."""

    ACQUIRED = "acquired"
    CONTENDED = "contended"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ExclusiveQuiescenceAttempt:
    """Distinguish held quiescence, active writers, and unknown lock failure."""

    status: ExclusiveQuiescenceStatus
    lease: DatabaseWriterLease | None = None
    error: DatabaseWriterLeaseResourceError | None = None

    @property
    def acquired(self) -> bool:
        return self.status is ExclusiveQuiescenceStatus.ACQUIRED


if os.name == "nt":

    class _Overlapped(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]


    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _lock_file_ex = _kernel32.LockFileEx
    _lock_file_ex.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_Overlapped),
    ]
    _lock_file_ex.restype = wintypes.BOOL
    _unlock_file_ex = _kernel32.UnlockFileEx
    _unlock_file_ex.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_Overlapped),
    ]
    _unlock_file_ex.restype = wintypes.BOOL


def database_writer_lease_path(profile_root: Path | None = None) -> Path:
    """Return the stable profile-global writer-lease artifact path."""
    root = (
        Path(profile_root)
        if profile_root is not None
        else user_data_path(appname="moa", appauthor=False, roaming=False)
    )
    return root.expanduser().resolve(strict=False) / _LEASE_FILE_NAME


class DatabaseWriterLease:
    """One acquired native shared or exclusive lease."""

    def __init__(
        self,
        path: Path,
        handle: BinaryIO,
        *,
        exclusive: bool,
        on_release: Callable[[], None] | None = None,
    ) -> None:
        self._path = path
        self._handle: BinaryIO | None = handle
        self._exclusive = exclusive
        self._on_release = on_release

    @property
    def path(self) -> Path:
        return self._path

    @property
    def exclusive(self) -> bool:
        return self._exclusive

    @property
    def is_acquired(self) -> bool:
        return self._handle is not None

    def release(self) -> None:
        """Release native ownership; repeated release is harmless."""
        handle, self._handle = self._handle, None
        if handle is None:
            return
        release_error: OSError | None = None
        try:
            _release_native_lock(handle)
        except OSError as error:
            release_error = error
        finally:
            try:
                handle.close()
            finally:
                if self._on_release is not None:
                    callback, self._on_release = self._on_release, None
                    callback()
        if release_error is not None:
            raise DatabaseWriterLeaseResourceError(
                f"Could not release the database-writer lease {self._path}."
            ) from release_error

    def __enter__(self) -> DatabaseWriterLease:
        if not self.is_acquired:
            raise DatabaseWriterLeaseError("Database-writer lease is not acquired.")
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.release()


_process_state_guard = threading.Lock()
_process_shared_owners = 0
_process_exclusive_owner: int | None = None
_shared_thread_state = threading.local()


@contextmanager
def shared_database_writer_lease(
    profile_root: Path | None = None,
) -> Iterator[DatabaseWriterLease]:
    """Hold the profile-global shared writer lease with thread-local reentrancy."""
    path = database_writer_lease_path(profile_root)
    held = getattr(_shared_thread_state, "lease", None)
    depth = int(getattr(_shared_thread_state, "depth", 0))
    if held is not None:
        if held.path != path:
            raise DatabaseWriterLeaseResourceError(
                "Nested database-writer lease acquisition changed the profile root."
            )
        _shared_thread_state.depth = depth + 1
        try:
            yield held
        finally:
            _shared_thread_state.depth -= 1
        return

    _reserve_process_shared_owner(path)
    try:
        lease = _acquire_file_lease(path, exclusive=False)
    except BaseException:
        _release_process_shared_owner()
        raise
    _shared_thread_state.lease = lease
    _shared_thread_state.depth = 1
    try:
        yield lease
    finally:
        _shared_thread_state.depth = 0
        _shared_thread_state.lease = None
        try:
            lease.release()
        finally:
            _release_process_shared_owner()


def try_acquire_exclusive_database_quiescence(
    profile_root: Path | None = None,
) -> ExclusiveQuiescenceAttempt:
    """Attempt nonblocking profile-global quiescence without touching SQLite."""
    path = database_writer_lease_path(profile_root)
    owner = threading.get_ident()
    if not _reserve_process_exclusive_owner(owner):
        return ExclusiveQuiescenceAttempt(ExclusiveQuiescenceStatus.CONTENDED)
    try:
        lease = _acquire_file_lease(path, exclusive=True)
    except DatabaseWriterLeaseContendedError:
        _release_process_exclusive_owner(owner)
        return ExclusiveQuiescenceAttempt(ExclusiveQuiescenceStatus.CONTENDED)
    except DatabaseWriterLeaseResourceError as error:
        _release_process_exclusive_owner(owner)
        return ExclusiveQuiescenceAttempt(
            ExclusiveQuiescenceStatus.ERROR,
            error=error,
        )
    lease._on_release = lambda: _release_process_exclusive_owner(owner)
    return ExclusiveQuiescenceAttempt(
        ExclusiveQuiescenceStatus.ACQUIRED,
        lease=lease,
    )


def _reserve_process_shared_owner(path: Path) -> None:
    global _process_shared_owners
    with _process_state_guard:
        if _process_exclusive_owner is not None:
            raise DatabaseWriterLeaseContendedError(
                f"Exclusive database quiescence is active for {path}."
            )
        _process_shared_owners += 1


def _release_process_shared_owner() -> None:
    global _process_shared_owners
    with _process_state_guard:
        _process_shared_owners -= 1


def _reserve_process_exclusive_owner(owner: int) -> bool:
    global _process_exclusive_owner
    with _process_state_guard:
        if _process_shared_owners or _process_exclusive_owner is not None:
            return False
        _process_exclusive_owner = owner
        return True


def _release_process_exclusive_owner(owner: int) -> None:
    global _process_exclusive_owner
    with _process_state_guard:
        if _process_exclusive_owner == owner:
            _process_exclusive_owner = None


def _acquire_file_lease(path: Path, *, exclusive: bool) -> DatabaseWriterLease:
    try:
        _require_safe_parent(path.parent)
        handle, created = _open_or_create_artifact(path)
    except DatabaseWriterLeaseResourceError:
        raise
    except OSError as error:
        raise DatabaseWriterLeaseResourceError(
            f"Could not initialize the database-writer lease artifact {path}."
        ) from error

    locked = False
    transferred = False
    try:
        _acquire_native_lock(handle, exclusive=exclusive if not created else True)
        locked = True
        if created:
            _initialize_created_artifact(handle)
            if not exclusive:
                _release_native_lock(handle)
                locked = False
                handle.close()
                return _acquire_existing_file_lease(path, exclusive=False)
        _require_handle_path_identity(handle, path)
        if not created and _artifact_is_empty(handle):
            if exclusive:
                _initialize_created_artifact(handle)
            else:
                _release_native_lock(handle)
                locked = False
                handle.close()
                initializer = _acquire_existing_file_lease(path, exclusive=True)
                initializer.release()
                return _acquire_existing_file_lease(path, exclusive=False)
        _require_valid_artifact(handle, path)
        lease = DatabaseWriterLease(path, handle, exclusive=exclusive)
        transferred = True
        return lease
    except DatabaseWriterLeaseError:
        raise
    except OSError as error:
        if _is_lock_contention(error):
            raise DatabaseWriterLeaseContendedError(
                f"An incompatible database-writer lease is active for {path}."
            ) from error
        raise DatabaseWriterLeaseResourceError(
            f"Could not use the database-writer lease artifact {path}."
        ) from error
    finally:
        if not transferred and not handle.closed:
            if locked:
                try:
                    _release_native_lock(handle)
                except OSError:
                    pass
            handle.close()


def _acquire_existing_file_lease(
    path: Path, *, exclusive: bool
) -> DatabaseWriterLease:
    handle: BinaryIO | None = None
    locked = False
    try:
        handle = _open_existing_artifact(path)
        _acquire_native_lock(handle, exclusive=exclusive)
        locked = True
        _require_handle_path_identity(handle, path)
        if _artifact_is_empty(handle):
            if exclusive:
                _initialize_created_artifact(handle)
            else:
                _release_native_lock(handle)
                locked = False
                handle.close()
                handle = None
                initializer = _acquire_existing_file_lease(path, exclusive=True)
                initializer.release()
                return _acquire_existing_file_lease(path, exclusive=False)
        _require_valid_artifact(handle, path)
        lease = DatabaseWriterLease(path, handle, exclusive=exclusive)
        handle = None
        locked = False
        return lease
    except DatabaseWriterLeaseError:
        raise
    except OSError as error:
        if _is_lock_contention(error):
            raise DatabaseWriterLeaseContendedError(
                f"An incompatible database-writer lease is active for {path}."
            ) from error
        raise DatabaseWriterLeaseResourceError(
            f"Could not use the database-writer lease artifact {path}."
        ) from error
    finally:
        if handle is not None:
            if locked:
                try:
                    _release_native_lock(handle)
                except OSError:
                    pass
            handle.close()


def _open_or_create_artifact(path: Path) -> tuple[BinaryIO, bool]:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        return _open_existing_artifact(path), False
    return os.fdopen(descriptor, "r+b", buffering=0), True


def _open_existing_artifact(path: Path) -> BinaryIO:
    before = _require_regular_non_reparse(path)
    flags = os.O_RDWR
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        after = os.fstat(descriptor)
        current = _require_regular_non_reparse(path)
        if not stat.S_ISREG(after.st_mode) or not _same_file_identity(before, after):
            raise DatabaseWriterLeaseResourceError(
                f"Database-writer lease artifact changed while opening: {path}."
            )
        if not _same_file_identity(after, current):
            raise DatabaseWriterLeaseResourceError(
                f"Database-writer lease path changed while opening: {path}."
            )
        return os.fdopen(descriptor, "r+b", buffering=0)
    except BaseException:
        os.close(descriptor)
        raise


def _require_safe_parent(parent: Path) -> None:
    parent.mkdir(parents=True, exist_ok=True)
    details = os.lstat(parent)
    if not stat.S_ISDIR(details.st_mode) or _is_reparse(details):
        raise DatabaseWriterLeaseResourceError(
            f"Database-writer lease parent is not a safe directory: {parent}."
        )


def _require_regular_non_reparse(path: Path) -> os.stat_result:
    details = os.lstat(path)
    if not stat.S_ISREG(details.st_mode) or _is_reparse(details):
        raise DatabaseWriterLeaseResourceError(
            f"Database-writer lease artifact is not a regular non-reparse file: {path}."
        )
    return details


def _is_reparse(details: os.stat_result) -> bool:
    attributes = getattr(details, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _same_file_identity(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _require_handle_path_identity(handle: BinaryIO, path: Path) -> None:
    opened = os.fstat(handle.fileno())
    current = _require_regular_non_reparse(path)
    if not _same_file_identity(opened, current):
        raise DatabaseWriterLeaseResourceError(
            f"Database-writer lease path changed while acquiring ownership: {path}."
        )


def _initialize_created_artifact(handle: BinaryIO) -> None:
    handle.seek(0)
    handle.write(_LEASE_MAGIC)
    handle.flush()
    os.fsync(handle.fileno())


def _artifact_is_empty(handle: BinaryIO) -> bool:
    handle.seek(0)
    return handle.read(1) == b""


def _require_valid_artifact(handle: BinaryIO, path: Path) -> None:
    handle.seek(0)
    content = handle.read(len(_LEASE_MAGIC) + 1)
    if content != _LEASE_MAGIC:
        raise DatabaseWriterLeaseResourceError(
            f"Database-writer lease artifact is malformed: {path}."
        )


def _acquire_native_lock(handle: BinaryIO, *, exclusive: bool) -> None:
    if os.name == "nt":
        flags = _WINDOWS_LOCKFILE_FAIL_IMMEDIATELY
        if exclusive:
            flags |= _WINDOWS_LOCKFILE_EXCLUSIVE_LOCK
        overlapped = _Overlapped()
        windows_handle = wintypes.HANDLE(msvcrt.get_osfhandle(handle.fileno()))
        if not _lock_file_ex(
            windows_handle,
            flags,
            0,
            _LOCK_LENGTH,
            0,
            ctypes.byref(overlapped),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return
    operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH  # type: ignore[attr-defined]
    fcntl.flock(  # type: ignore[attr-defined]
        handle.fileno(), operation | fcntl.LOCK_NB  # type: ignore[attr-defined]
    )


def _release_native_lock(handle: BinaryIO) -> None:
    if os.name == "nt":
        overlapped = _Overlapped()
        windows_handle = wintypes.HANDLE(msvcrt.get_osfhandle(handle.fileno()))
        if not _unlock_file_ex(
            windows_handle,
            0,
            _LOCK_LENGTH,
            0,
            ctypes.byref(overlapped),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return
    fcntl.flock(  # type: ignore[attr-defined]
        handle.fileno(), fcntl.LOCK_UN  # type: ignore[attr-defined]
    )


def _is_lock_contention(error: OSError) -> bool:
    if os.name == "nt":
        return error.winerror == _WINDOWS_ERROR_LOCK_VIOLATION
    return error.errno in {errno.EACCES, errno.EAGAIN}
