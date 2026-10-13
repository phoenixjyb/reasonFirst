"""Bounded output collection for the host validation runner, not a sandbox.

No background reader threads, communicate(), growing log files or reader queues.
POSIX timeout cleanup targets our new process group before reaping its leader.
Windows cleanup is deliberately limited to the owned direct process handle.
"""
from __future__ import annotations

import codecs
import io
import locale
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
from typing import BinaryIO


_READ_BYTES = 8192
_POLL_SECONDS = 0.02
_TERMINATE_GRACE_SECONDS = 0.2
_REAP_SECONDS = 1.0
_DRAIN_SECONDS = 0.2


class CommandProcessError(RuntimeError):
    """Fixed, nonrevealing failure codes; never include child output or env."""

    def __init__(self, reason: str, cleanup: dict[str, object], errors: list[str]):
        self.reason = reason
        self.cleanup = cleanup
        self.cleanup_errors = tuple(errors)
        super().__init__(reason + ("; command_cleanup_failed" if errors and reason != "command_cleanup_failed" else ""))


class _Capture:
    """Retain a UTF-8 byte prefix and count normalized bytes as they arrive."""

    def __init__(self, limit: int, encoding: str):
        self.limit = limit
        decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
        self.decoder = io.IncrementalNewlineDecoder(decoder, translate=True)
        self.prefix = bytearray()
        self.total = 0
        self.complete = False
        self.finalized = False

    def feed(self, data: bytes, *, final: bool = False) -> None:
        text = self.decoder.decode(data, final=final)
        # UTF-8 and normal locale encodings have tiny partial characters. Even
        # a stateful codec must not accumulate an unbounded unfinished sequence.
        if len(self.decoder.getstate()[0]) > _READ_BYTES:
            raise RuntimeError("command_output_decode_failed")
        normalized = text.encode("utf-8", errors="replace")
        self.total += len(normalized)
        available = max(0, self.limit - len(self.prefix))
        self.prefix.extend(normalized[:available])
        self.finalized = final

    def eof(self) -> None:
        self.feed(b"", final=True)
        self.complete = True

    def result(self, name: str) -> dict[str, object]:
        if not self.finalized:
            # Forced closure is not EOF. Flush only the bounded observed prefix
            # of the input; the original-byte count is a lower bound in this case.
            self.feed(b"", final=True)
        return {
            name: self.prefix.decode("utf-8", errors="ignore"),
            f"{name}_truncated": self.total > self.limit,
            f"{name}_original_bytes": self.total,
            f"{name}_complete": self.complete,
        }


class _WindowsPeek:
    def __init__(self) -> None:
        # Load only on Windows. Both handles are private read ends, with this
        # loop as their sole reader: no competing synchronous read can block peek.
        import ctypes
        from ctypes import wintypes
        import msvcrt

        self.ctypes = ctypes
        self.dword = wintypes.DWORD
        self.get_handle = msvcrt.get_osfhandle
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.peek = self.kernel.PeekNamedPipe
        self.peek.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD),
                              ctypes.POINTER(wintypes.DWORD),
                              ctypes.POINTER(wintypes.DWORD)]
        self.peek.restype = wintypes.BOOL

    def available(self, fd: int) -> int | None:
        available = self.dword()
        if not self.peek(self.get_handle(fd), None, 0, None,
                         self.ctypes.byref(available), None):
            error = self.ctypes.get_last_error()
            if error in (109, 232, 233):  # broken / closing / disconnected pipe
                return None
            raise OSError("command_output_read_failed")
        return available.value


class _PipeReader:
    def __init__(self, pipes: dict[str, BinaryIO]):
        self.pipes = dict(pipes)
        self.selector = None
        self.peek = None
        try:
            if os.name == "posix":
                self.selector = selectors.DefaultSelector()
                for name, stream in pipes.items():
                    os.set_blocking(stream.fileno(), False)
                    self.selector.register(stream, selectors.EVENT_READ, name)
            elif os.name == "nt":
                self.peek = _WindowsPeek()
            else:
                raise RuntimeError("command_platform_unsupported")
        except BaseException:
            self.close()
            raise

    def step(self, captures: dict[str, _Capture], delay: float) -> None:
        if self.selector is not None:
            ready = [(key.data, _READ_BYTES) for key, _ in self.selector.select(delay)]
        else:
            ready = []
            for name, stream in self.pipes.items():
                count = self.peek.available(stream.fileno())
                if count is None or count > 0:
                    ready.append((name, 0 if count is None else min(count, _READ_BYTES)))
            if not ready:
                time.sleep(delay)
        # One bounded read per stream per iteration; flooding stdout cannot
        # starve stderr or prevent the next monotonic deadline check.
        for name, count in ready:
            stream = self.pipes[name]
            try:
                data = os.read(stream.fileno(), count) if count else b""
            except (BlockingIOError, InterruptedError):
                continue
            if data:
                captures[name].feed(data)
            else:
                captures[name].eof()
                if self.selector is not None:
                    self.selector.unregister(stream)
                del self.pipes[name]

    def close(self) -> None:
        if self.selector is not None:
            self.selector.close()


def _collect(reader: _PipeReader, captures: dict[str, _Capture], deadline: float) -> bool:
    while reader.pipes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        reader.step(captures, min(remaining, _POLL_SECONDS))
    # A read/decode can finish after its allowed time. EOF alone is not timely
    # completion; the caller checks the same deadline before waiting for exit.
    return True


def _terminate(proc: subprocess.Popen, *, posix_group: bool) -> list[str]:
    """Best effort, bounded cleanup. The caller has not polled/reaped the leader."""
    errors = []
    if posix_group and proc.returncode is not None:
        # Popen.wait can reap during its KeyboardInterrupt grace wait. Once
        # reaped, never signal a numerical group ID that could be reused.
        return ["command_group_identity_lost"]
    if posix_group:
        # Do not poll()/wait() between signals, even when the root has exited:
        # its unreaped PID reserves this group's identity through escalation.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                pass
            except BaseException:
                errors.append("command_group_signal_failed")
            if sig == signal.SIGTERM:
                try:
                    time.sleep(_TERMINATE_GRACE_SECONDS)
                except BaseException:
                    errors.append("command_cleanup_interrupted")
    # Also target our direct process object if group signalling failed. This
    # does not introduce PID/name scanning or claim escaped-child containment.
    if not posix_group or errors:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        except BaseException:
            errors.append("command_child_kill_failed")
    try:
        proc.wait(timeout=_REAP_SECONDS)
    except BaseException:
        errors.append("command_child_reap_failed")
    return errors


def run_bounded_command(
    argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float,
    max_output_bytes: int, encoding: str | None = None,
) -> dict[str, object]:
    """Run one already-authorized command with bounded capture and cleanup.

    Timeout covers collection AND root exit, including inherited pipe writers.
    Process creation itself may exceed the timeout in the platform API. Cleanup
    adds fixed grace/reap/drain budgets. Descendants escaping the POSIX group,
    Windows descendants, and normal-exit background tasks are not contained.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    if type(max_output_bytes) is not int or max_output_bytes < 0:
        raise ValueError("max_output_bytes must be a nonnegative integer")
    if os.name not in ("posix", "nt"):
        raise RuntimeError("command_platform_unsupported")
    encoding = encoding or locale.getpreferredencoding(False)
    captures = {name: _Capture(max_output_bytes // 2, encoding)
                for name in ("stdout", "stderr")}
    posix_group = os.name == "posix"
    deadline = time.monotonic() + timeout
    # Keep stdin semantics, argv, cwd, and resolved environment unchanged. Do
    # not use Popen's context manager: its exit can wait without a time bound.
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, bufsize=0, close_fds=True,
                            start_new_session=posix_group)
    reader = None
    reaped = False
    timed_out = False
    failure = None
    cleanup_errors = []
    termination_requested = False
    pipes = {"stdout": proc.stdout, "stderr": proc.stderr}
    try:
        reader = _PipeReader(pipes)
        if not _collect(reader, captures, deadline):
            timed_out = True
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
            else:
                try:
                    proc.wait(timeout=remaining)
                    reaped = True
                except subprocess.TimeoutExpired:
                    timed_out = True
    except BaseException as exc:
        failure = exc
    finally:
        if not reaped:
            termination_requested = True
            cleanup_errors.extend(_terminate(proc, posix_group=posix_group))
            reaped = proc.returncode is not None
        if timed_out and reader is not None:
            try:
                _collect(reader, captures, time.monotonic() + _DRAIN_SECONDS)
            except BaseException:
                cleanup_errors.append("command_output_drain_failed")
        if reader is not None:
            try:
                reader.close()
            except BaseException:
                cleanup_errors.append("command_reader_close_failed")
        for stream in pipes.values():
            try:
                stream.close()
            except BaseException:
                cleanup_errors.append("command_pipe_close_failed")
    cleanup = {
        "scope": "posix-process-group" if posix_group else "direct-child",
        "termination_requested": termination_requested,
        "direct_child_reaped": reaped,
        "output_pipes_closed": all(stream.closed for stream in pipes.values()),
        "descendants_verified": False,
    }
    if failure is not None or cleanup_errors:
        if isinstance(failure, (KeyboardInterrupt, SystemExit)) and not cleanup_errors:
            raise failure
        reason = ("command_interrupted" if isinstance(failure, (KeyboardInterrupt, SystemExit))
                  else "command_output_read_failed" if failure is not None
                  else "command_timed_out" if timed_out else "command_cleanup_failed")
        raise CommandProcessError(reason, cleanup, cleanup_errors) from None
    result = {
        "timed_out": timed_out,
        "returncode": None if timed_out else proc.returncode,
        "output_complete": all(capture.complete for capture in captures.values()),
        "cleanup": cleanup,
    }
    for name, capture in captures.items():
        result.update(capture.result(name))
    return result
