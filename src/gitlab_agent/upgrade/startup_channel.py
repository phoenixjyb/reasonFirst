"""Bounded one-frame transport on caller-owned private POSIX pipe endpoints.

The wire format is a four-byte big-endian unsigned length, 1..16 KiB payload
bytes, then EOF. A receiver accepts exactly one frame, including that final EOF;
an open writer, a truncated frame or any trailing byte cannot report success.

The caller supplies ONE absolute monotonic deadline established no later than
the protocol attempt. These functions never extend it. They leave the descriptor
open, nonblocking and noninheritable. The writer's owner must close its endpoint
promptly after write_frame, including on failure. O_NONBLOCK is shared with dup
descriptors: endpoints and their lifecycle must belong exclusively to the caller.
Only the caller launches/reaps processes and consumes a failed protocol attempt.

The optional cancellation event and liveness callback are trusted, prompt local
observations, not peer claims. Polls are at most 50 ms; checks surround each I/O
operation, include elapsed callback time, and run again before successful return.
An inherited pipe associates bytes with its handle holders; it is not binary
attestation, a sandbox, complete descendant containment, or activation authority.
"""
from __future__ import annotations

import os
import platform
import selectors
import stat
import threading
import time
from typing import Callable

from .startup_protocol import MAX_MESSAGE_BYTES

_POLL_NS = 50_000_000
_CODES = frozenset({
    "unsupported_platform", "invalid_fd", "invalid_deadline", "invalid_clock",
    "invalid_cancel", "invalid_liveness", "invalid_frame", "frame_too_large",
    "channel_eof", "truncated_frame", "trailing_frame_data",
    "channel_broken_pipe", "channel_timeout", "channel_cancelled",
    "child_exited", "channel_io_error",
})


class ChannelError(RuntimeError):
    """Fixed nonrevealing codes, without descriptor, wire or peer values."""

    def __init__(self, code: str):
        self.code = code if type(code) is str and code in _CODES else "channel_io_error"
        super().__init__(self.code)


def _fail(code: str):
    raise ChannelError(code) from None


def require_supported() -> None:
    """Reject unsupported adapters before descriptor or process side effects."""
    if os.name != "posix" or platform.system() not in {"Darwin", "Linux"}:
        _fail("unsupported_platform")


def _validate_fd(fd: int) -> None:
    if type(fd) is not int or not 0 <= fd <= 2**31 - 1:
        _fail("invalid_fd")
    try:
        mode = os.fstat(fd).st_mode
    except (OSError, ValueError, OverflowError):
        _fail("invalid_fd")
    # Regular-file I/O need not honor O_NONBLOCK. The owner must additionally
    # establish that this FIFO is its freshly created anonymous-pipe endpoint.
    if not stat.S_ISFIFO(mode):
        _fail("invalid_fd")


def mark_noninheritable(*fds: int) -> None:
    """Set CLOEXEC on owned pipe endpoints; retain only explicit pass_fds copies.

    Validate all descriptors before changing any. This neither creates endpoints
    nor proves ownership; callers must serialize their own launch/close lifecycle.
    """
    require_supported()
    for fd in fds:
        _validate_fd(fd)
    for fd in fds:
        try:
            os.set_inheritable(fd, False)
        except (OSError, ValueError, OverflowError):
            _fail("channel_io_error")


class _Budget:
    def __init__(self, deadline_ns: int, cancel: threading.Event | None,
                 is_alive: Callable[[], bool] | None, clock_ns: Callable[[], int]):
        if type(deadline_ns) is not int or deadline_ns < 0:
            _fail("invalid_deadline")
        if not callable(clock_ns):
            _fail("invalid_clock")
        if cancel is not None and not isinstance(cancel, threading.Event):
            _fail("invalid_cancel")
        if is_alive is not None and not callable(is_alive):
            _fail("invalid_liveness")
        self.deadline_ns = deadline_ns
        self.cancel = cancel
        self.is_alive = is_alive
        self.clock_ns = clock_ns
        self.last_ns: int | None = None

    def _remaining_ns(self) -> int:
        try:
            now = self.clock_ns()
        except Exception:
            _fail("invalid_clock")
        if (type(now) is not int or now < 0
                or (self.last_ns is not None and now < self.last_ns)):
            _fail("invalid_clock")
        self.last_ns = now
        if now >= self.deadline_ns:
            _fail("channel_timeout")
        return self.deadline_ns - now

    def check(self) -> float:
        self._remaining_ns()
        if self.cancel is not None:
            try:
                cancelled = self.cancel.is_set()
            except Exception:
                _fail("invalid_cancel")
            if type(cancelled) is not bool:
                _fail("invalid_cancel")
            if cancelled:
                _fail("channel_cancelled")
        if self.is_alive is not None:
            try:
                alive = self.is_alive()
            except Exception:
                _fail("invalid_liveness")
            if type(alive) is not bool:
                _fail("invalid_liveness")
            if not alive:
                _fail("child_exited")
        # Callback time is part of the same budget. Cap before converting to a
        # float so even a caller-supplied very large integer cannot overflow.
        return min(self._remaining_ns(), _POLL_NS) / 1_000_000_000


def _prepare_fd(fd: int, budget: _Budget) -> None:
    budget.check()
    _validate_fd(fd)
    budget.check()
    try:
        os.set_inheritable(fd, False)
        budget.check()
        os.set_blocking(fd, False)
    except (OSError, ValueError, OverflowError):
        _fail("channel_io_error")
    budget.check()


def _wait(selector: selectors.BaseSelector, budget: _Budget) -> None:
    timeout = budget.check()
    try:
        selector.select(timeout)
    except InterruptedError:
        pass
    except (OSError, ValueError, OverflowError):
        _fail("channel_io_error")
    budget.check()


def _read_some(fd: int, size: int, selector: selectors.BaseSelector,
               budget: _Budget) -> bytes:
    while True:
        budget.check()
        try:
            data = os.read(fd, size)
        except BlockingIOError:
            _wait(selector, budget)
            continue
        except InterruptedError:
            budget.check()
            continue
        except (OSError, ValueError, OverflowError):
            _fail("channel_io_error")
        budget.check()
        return data


def _read_exact(fd: int, size: int, selector: selectors.BaseSelector,
                budget: _Budget, *, empty_code: str) -> bytes:
    output = bytearray()
    while len(output) < size:
        data = _read_some(fd, size - len(output), selector, budget)
        if not data:
            _fail("truncated_frame" if output else empty_code)
        output.extend(data)
        budget.check()
    return bytes(output)


def read_frame(fd: int, *, deadline_ns: int,
               cancel: threading.Event | None = None,
               is_alive: Callable[[], bool] | None = None,
               _clock_ns: Callable[[], int] = time.monotonic_ns) -> bytes:
    """Read one bounded frame AND EOF, retaining ownership of the open fd.

    The peer must close its writer after sending. An EOF before the frame is
    complete fails. A complete frame without EOF still waits within the original
    deadline, and a complete frame from a child observed exited still fails.
    """
    require_supported()
    budget = _Budget(deadline_ns, cancel, is_alive, _clock_ns)
    _prepare_fd(fd, budget)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(fd, selectors.EVENT_READ)
            header = _read_exact(fd, 4, selector, budget, empty_code="channel_eof")
            size = int.from_bytes(header, "big")
            if size == 0:
                _fail("invalid_frame")
            if size > MAX_MESSAGE_BYTES:
                _fail("frame_too_large")
            payload = _read_exact(fd, size, selector, budget, empty_code="truncated_frame")
            if _read_some(fd, 1, selector, budget):
                _fail("trailing_frame_data")
    except (OSError, ValueError, OverflowError):
        _fail("channel_io_error")
    budget.check()
    return payload


def write_frame(fd: int, payload: bytes, *, deadline_ns: int,
                cancel: threading.Event | None = None,
                is_alive: Callable[[], bool] | None = None,
                _clock_ns: Callable[[], int] = time.monotonic_ns) -> None:
    """Write one frame without closing fd; its owner must close it promptly.

    Success establishes only completion of these writes, not peer receipt. A
    failure may leave a partial frame; it must consume the caller's attempt and
    cannot be retried on this channel with a fresh deadline.
    """
    require_supported()
    budget = _Budget(deadline_ns, cancel, is_alive, _clock_ns)
    budget.check()
    if type(payload) is not bytes or not payload:
        _fail("invalid_frame")
    if len(payload) > MAX_MESSAGE_BYTES:
        _fail("frame_too_large")
    _prepare_fd(fd, budget)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(fd, selectors.EVENT_WRITE)
            for part in (len(payload).to_bytes(4, "big"), payload):
                remaining = memoryview(part)
                while remaining:
                    budget.check()
                    try:
                        written = os.write(fd, remaining)
                    except BlockingIOError:
                        _wait(selector, budget)
                        continue
                    except InterruptedError:
                        budget.check()
                        continue
                    except BrokenPipeError:
                        _fail("channel_broken_pipe")
                    except (OSError, ValueError, OverflowError):
                        _fail("channel_io_error")
                    budget.check()
                    if type(written) is not int or not 0 < written <= len(remaining):
                        _fail("channel_io_error")
                    remaining = remaining[written:]
    except (OSError, ValueError, OverflowError):
        _fail("channel_io_error")
    budget.check()
