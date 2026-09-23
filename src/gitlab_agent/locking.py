from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import threading
import time
from typing import BinaryIO, Iterator


class WorkspaceBusyError(RuntimeError):
    """Raised when a managed workspace is already being mutated elsewhere."""


_REGISTRY_GUARD = threading.Lock()
_LOCAL_LOCKS: dict[str, threading.RLock] = {}
_HELD: dict[tuple[str, int], tuple[BinaryIO, int]] = {}


def _local_lock(path: Path) -> threading.RLock:
    key = str(path.resolve(strict=False))
    with _REGISTRY_GUARD:
        lock = _LOCAL_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCAL_LOCKS[key] = lock
        return lock


def _try_os_lock(handle: BinaryIO) -> bool:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _unlock_os(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def file_lock(
    path: Path,
    *,
    timeout_seconds: float = 10.0,
    poll_seconds: float = 0.05,
) -> Iterator[None]:
    """Acquire a re-entrant, cross-process file lock.

    The OS owns the actual lock, so an unclean process exit releases it. A
    per-path RLock serializes threads in this process and makes nested
    operations in the same thread re-entrant.
    """

    lock_path = path.expanduser().resolve(strict=False)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_path.parent.chmod(0o700)
    except OSError:
        pass

    local = _local_lock(lock_path)
    timeout = max(0.0, float(timeout_seconds))
    acquired_local = local.acquire(timeout=timeout)
    if not acquired_local:
        raise WorkspaceBusyError(
            f"Workspace mutation lock is busy: {lock_path}"
        )

    thread_key = (str(lock_path), threading.get_ident())
    handle: BinaryIO | None = None
    reentrant = False
    try:
        with _REGISTRY_GUARD:
            existing = _HELD.get(thread_key)
            if existing is not None:
                held_handle, count = existing
                _HELD[thread_key] = (held_handle, count + 1)
                reentrant = True

        if reentrant:
            yield
            return

        handle = open(lock_path, "a+b")
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass

        deadline = time.monotonic() + timeout
        while not _try_os_lock(handle):
            if time.monotonic() >= deadline:
                raise WorkspaceBusyError(
                    f"Workspace mutation lock is busy: {lock_path}"
                )
            time.sleep(max(0.01, poll_seconds))

        with _REGISTRY_GUARD:
            _HELD[thread_key] = (handle, 1)

        yield
    finally:
        release_handle: BinaryIO | None = None
        with _REGISTRY_GUARD:
            existing = _HELD.get(thread_key)
            if existing is not None:
                held_handle, count = existing
                if count > 1:
                    _HELD[thread_key] = (held_handle, count - 1)
                else:
                    _HELD.pop(thread_key, None)
                    release_handle = held_handle

        if release_handle is not None:
            _unlock_os(release_handle)
            release_handle.close()
        elif handle is not None and not reentrant:
            # Acquisition may have failed before the handle was registered.
            try:
                handle.close()
            except OSError:
                pass
        local.release()
