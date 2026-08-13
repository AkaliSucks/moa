"""Process-lifetime ownership for one live MOA listener database."""

from __future__ import annotations

import errno
import os
import threading
from pathlib import Path
from typing import BinaryIO

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class ListenerProcessGuardError(RuntimeError):
    """Base error for listener process ownership."""


class ListenerAlreadyRunningError(ListenerProcessGuardError):
    """Raised when another listener already owns the same database identity."""


class ListenerProcessGuardResourceError(ListenerProcessGuardError):
    """Raised when the listener lock resource cannot be used safely."""


_owned_database_paths: set[Path] = set()
_owned_database_paths_lock = threading.Lock()
_LOCK_OFFSET = 0
_LOCK_LENGTH = 1


class ListenerProcessGuard:
    """Hold an OS-backed sidecar lock for one canonical database path."""

    def __init__(self, database_path: Path) -> None:
        path_text = str(database_path)
        if path_text == ":memory:" or path_text.startswith("file:"):
            raise ListenerProcessGuardResourceError(
                "The live Discord listener requires a file-backed database path."
            )
        try:
            canonical_path = Path(database_path).resolve(strict=False)
            sidecar_path = canonical_path.with_name(
                f"{canonical_path.name}.listener.lock"
            )
        except (OSError, ValueError) as error:
            raise ListenerProcessGuardResourceError(
                "The live Discord listener database path cannot identify a lock resource."
            ) from error
        self._database_path = canonical_path
        self._sidecar_path = sidecar_path
        self._handle: BinaryIO | None = None
        self._owns_in_process_identity = False

    @property
    def database_path(self) -> Path:
        """Return the canonical database identity protected by this guard."""
        return self._database_path

    @property
    def sidecar_path(self) -> Path:
        """Return the separate sidecar used for OS lock ownership."""
        return self._sidecar_path

    @property
    def is_acquired(self) -> bool:
        """Whether this object currently owns the listener identity."""
        return self._handle is not None

    def acquire(self) -> None:
        """Acquire listener ownership without waiting for another owner."""
        if self._handle is not None:
            raise ListenerProcessGuardError(
                f"Listener guard is already acquired for database {self._database_path}."
            )
        self._claim_in_process_identity()
        handle: BinaryIO | None = None
        os_lock_acquired = False
        try:
            try:
                self._sidecar_path.parent.mkdir(parents=True, exist_ok=True)
                handle = self._sidecar_path.open("a+b", buffering=0)
                self._ensure_lock_byte(handle)
                self._acquire_os_lock(handle)
                os_lock_acquired = True
            except OSError as error:
                raise ListenerProcessGuardResourceError(
                    f"Could not create or open the listener lock for database "
                    f"{self._database_path}."
                ) from error
        except BaseException as error:
            cleanup_errors = self._cleanup_failed_acquisition(handle, os_lock_acquired)
            for cleanup_error in cleanup_errors:
                error.add_note(
                    "Listener guard acquisition cleanup also failed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            raise
        self._handle = handle

    def _cleanup_failed_acquisition(
        self, handle: BinaryIO | None, os_lock_acquired: bool
    ) -> tuple[BaseException, ...]:
        """Release partial acquisition state without replacing its primary failure."""
        cleanup_errors: list[BaseException] = []
        self._handle = None
        try:
            if handle is not None and os_lock_acquired:
                try:
                    self._release_os_lock(handle)
                except BaseException as error:
                    cleanup_errors.append(error)
            if handle is not None:
                try:
                    handle.close()
                except BaseException as error:
                    cleanup_errors.append(error)
        finally:
            self._release_in_process_identity()
        return tuple(cleanup_errors)

    def release(self) -> None:
        """Release ownership; repeated release after success is harmless."""
        handle, self._handle = self._handle, None
        if handle is None:
            return
        unlock_error: OSError | None = None
        try:
            self._release_os_lock(handle)
        except OSError as error:
            unlock_error = error
        finally:
            try:
                handle.close()
            finally:
                self._release_in_process_identity()
        if unlock_error is not None:
            raise ListenerProcessGuardResourceError(
                f"Could not release the listener lock for database {self._database_path}."
            ) from unlock_error

    def __enter__(self) -> ListenerProcessGuard:
        self.acquire()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.release()

    def _claim_in_process_identity(self) -> None:
        with _owned_database_paths_lock:
            if self._database_path in _owned_database_paths:
                raise ListenerAlreadyRunningError(
                    f"Another MOA listener already owns database {self._database_path}."
                )
            _owned_database_paths.add(self._database_path)
            self._owns_in_process_identity = True

    def _release_in_process_identity(self) -> None:
        if not self._owns_in_process_identity:
            return
        with _owned_database_paths_lock:
            _owned_database_paths.discard(self._database_path)
        self._owns_in_process_identity = False

    @staticmethod
    def _ensure_lock_byte(handle: BinaryIO) -> None:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(_LOCK_OFFSET)

    def _acquire_os_lock(self, handle: BinaryIO) -> None:
        try:
            handle.seek(_LOCK_OFFSET)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, _LOCK_LENGTH)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise ListenerAlreadyRunningError(
                    f"Another MOA listener already owns database {self._database_path}."
                ) from error
            raise ListenerProcessGuardResourceError(
                f"Could not lock the listener resource for database {self._database_path}."
            ) from error

    @staticmethod
    def _release_os_lock(handle: BinaryIO) -> None:
        handle.seek(_LOCK_OFFSET)
        if os.name == "nt":
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, _LOCK_LENGTH)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
