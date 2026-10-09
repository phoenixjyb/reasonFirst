"""Real owned-pipe framing regressions; never a running service fixture."""
from __future__ import annotations

from contextlib import ExitStack
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest
from unittest.mock import patch

from gitlab_agent.upgrade import startup_channel as channel


_SUPPORTED = os.name == "posix" and platform.system() in {"Darwin", "Linux"}


def _deadline(seconds=2):
    return time.monotonic_ns() + int(seconds * 1_000_000_000)


class _Clock:
    def __init__(self, now=1):
        self.now = now

    def __call__(self):
        return self.now


class ChannelPlatformTests(unittest.TestCase):
    def test_unsupported_platform_rejects_before_descriptor_side_effects(self):
        for system, name in (("Windows", "nt"), ("FreeBSD", "posix")):
            with self.subTest(system=system), ExitStack() as stack:
                stack.enter_context(patch.object(channel.platform, "system", return_value=system))
                stack.enter_context(patch.object(channel.os, "name", name))
                operations = [stack.enter_context(patch.object(channel.os, attr))
                              for attr in ("fstat", "set_inheritable", "set_blocking", "read", "write")]
                for operation in (
                        lambda: channel.require_supported(),
                        lambda: channel.mark_noninheritable(42),
                        lambda: channel.read_frame(42, deadline_ns=1),
                        lambda: channel.write_frame(42, b"x", deadline_ns=1)):
                    with self.assertRaises(channel.ChannelError) as error:
                        operation()
                    self.assertEqual(error.exception.code, "unsupported_platform")
                for operation in operations:
                    operation.assert_not_called()

    def test_error_constructor_cannot_reflect_unknown_values(self):
        marker = "do-not-reflect-channel-value"
        for value in (marker, {"raw": marker}, None, True):
            error = channel.ChannelError(value)
            self.assertEqual(error.code, "channel_io_error")
            self.assertEqual(str(error), "channel_io_error")
            self.assertNotIn(marker, repr(error))


@unittest.skipUnless(_SUPPORTED, "Private startup pipe adapter supports macOS/Linux only")
class StartupChannelTests(unittest.TestCase):
    def setUp(self):
        self._fds = set()

    def _pipe(self):
        pair = os.pipe()
        self._fds.update(pair)
        for fd in pair:
            self.addCleanup(self._close, fd)
        return pair

    def _close(self, fd):
        if fd in self._fds:
            self._fds.remove(fd)
            os.close(fd)

    def _assert_code(self, code, action):
        with self.assertRaises(channel.ChannelError) as error:
            action()
        self.assertEqual(error.exception.code, code)
        self.assertEqual(str(error.exception), code)

    def _thread(self, action):
        errors = []

        def execute():
            try:
                action()
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=execute)
        worker.start()

        def join():
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive(), "Disposable pipe worker did not finish")
            if errors:
                raise errors[0]

        self.addCleanup(join)
        return join

    def test_minimum_and_maximum_payload_real_pipe_roundtrip(self):
        for payload in (b"x", bytes(range(256)) * 64):
            with self.subTest(size=len(payload)):
                reader, writer = self._pipe()
                deadline = _deadline()

                def send():
                    try:
                        channel.write_frame(writer, payload, deadline_ns=deadline)
                        self.assertFalse(os.get_blocking(writer))
                        self.assertFalse(os.get_inheritable(writer))
                    finally:
                        self._close(writer)

                join = self._thread(send)
                self.assertEqual(channel.read_frame(reader, deadline_ns=deadline), payload)
                join()
                self.assertFalse(os.get_blocking(reader))
                self.assertFalse(os.get_inheritable(reader))
                os.fstat(reader)  # The reader remains owned/open for its caller.

    def test_fragmented_header_and_payload_within_original_budget(self):
        reader, writer = self._pipe()
        payload = b"fragmented"
        wire = len(payload).to_bytes(4, "big") + payload

        def send():
            try:
                for byte in wire:
                    os.write(writer, bytes([byte]))
                    threading.Event().wait(0.003)
            finally:
                self._close(writer)

        self._thread(send)
        self.assertEqual(channel.read_frame(reader, deadline_ns=_deadline()), payload)

    def test_empty_truncated_zero_and_extra_frames_fail(self):
        cases = ((b"", "channel_eof"), (b"\0\0", "truncated_frame"),
                 (b"\0\0\0\x03x", "truncated_frame"),
                 (b"\0\0\0\0", "invalid_frame"),
                 (b"\0\0\0\x01xy", "trailing_frame_data"),
                 (b"\0\0\0\x01x\0\0\0\x01x", "trailing_frame_data"))
        for wire, code in cases:
            with self.subTest(code=code, size=len(wire)):
                reader, writer = self._pipe()
                if wire:
                    os.write(writer, wire)
                self._close(writer)
                self._assert_code(code, lambda: channel.read_frame(reader, deadline_ns=_deadline()))

    def test_oversized_header_rejected_without_consuming_payload(self):
        reader, writer = self._pipe()
        sentinel = b"untouched-payload"
        os.write(writer, (channel.MAX_MESSAGE_BYTES + 1).to_bytes(4, "big") + sentinel)
        # Keep the writer open: rejection must not wait for payload or EOF.
        self._assert_code("frame_too_large", lambda: channel.read_frame(
            reader, deadline_ns=_deadline(0.5)))
        self.assertEqual(os.read(reader, len(sentinel)), sentinel)

    def test_complete_frame_without_eof_cannot_succeed(self):
        reader, writer = self._pipe()
        channel.write_frame(writer, b"x", deadline_ns=_deadline())
        os.fstat(writer)  # write_frame deliberately leaves its endpoint open.
        started = time.monotonic()
        self._assert_code("channel_timeout", lambda: channel.read_frame(
            reader, deadline_ns=_deadline(0.08)))
        self.assertLess(time.monotonic() - started, 1.5)

    def test_slow_writer_cannot_renew_budget_with_partial_progress(self):
        reader, writer = self._pipe()
        stop = threading.Event()

        def send():
            try:
                os.write(writer, (100).to_bytes(4, "big"))
                while not stop.wait(0.02):
                    os.write(writer, b"x")
            finally:
                self._close(writer)

        join = self._thread(send)
        try:
            self._assert_code("channel_timeout", lambda: channel.read_frame(
                reader, deadline_ns=_deadline(0.12)))
        finally:
            stop.set()
            join()

    def _fill_pipe(self, writer):
        os.set_blocking(writer, False)
        while True:
            try:
                os.write(writer, b"x" * 4096)
            except BlockingIOError:
                return

    def test_stalled_reader_bounds_nonblocking_write(self):
        _, writer = self._pipe()
        self._fill_pipe(writer)
        started = time.monotonic()
        self._assert_code("channel_timeout", lambda: channel.write_frame(
            writer, b"x", deadline_ns=_deadline(0.08)))
        self.assertLess(time.monotonic() - started, 1.5)

    def test_broken_pipe_is_fixed_failure(self):
        reader, writer = self._pipe()
        self._close(reader)
        self._assert_code("channel_broken_pipe", lambda: channel.write_frame(
            writer, b"synthetic-value", deadline_ns=_deadline()))

    def test_invalid_fd_types_closed_descriptors_and_nonpipes_rejected(self):
        reader, writer = self._pipe()
        self._close(reader)
        self._assert_code("invalid_fd", lambda: channel.read_frame(reader, deadline_ns=_deadline()))
        self._assert_code("invalid_fd", lambda: channel.write_frame(reader, b"x", deadline_ns=_deadline()))
        with tempfile.TemporaryFile() as regular:
            for fd in (-1, True, None, "3", 2**64, regular.fileno()):
                with self.subTest(fd_type=type(fd).__name__):
                    self._assert_code("invalid_fd", lambda: channel.read_frame(fd, deadline_ns=_deadline()))
                    self._assert_code("invalid_fd", lambda: channel.write_frame(fd, b"x", deadline_ns=_deadline()))
        os.fstat(writer)

    def test_invalid_payloads_rejected_before_descriptor_mutation(self):
        _, writer = self._pipe()
        for value in (b"", "x", bytearray(b"x"), memoryview(b"x"), None, True,
                      b"x" * (channel.MAX_MESSAGE_BYTES + 1)):
            code = "frame_too_large" if type(value) is bytes and value else "invalid_frame"
            self._assert_code(code, lambda: channel.write_frame(writer, value, deadline_ns=_deadline()))
            self.assertTrue(os.get_blocking(writer))

    def test_exact_deadline_rejects_before_io(self):
        reader, writer = self._pipe()
        with patch.object(channel.os, "fstat") as observe:
            self._assert_code("channel_timeout", lambda: channel.read_frame(
                reader, deadline_ns=100, _clock_ns=lambda: 100))
            self._assert_code("channel_timeout", lambda: channel.write_frame(
                writer, b"x", deadline_ns=100, _clock_ns=lambda: 100))
        observe.assert_not_called()

    def test_invalid_deadline_and_clock_fail_closed(self):
        reader, _ = self._pipe()
        for deadline in (-1, True, None, "100", 1.0):
            self._assert_code("invalid_deadline", lambda: channel.read_frame(
                reader, deadline_ns=deadline))
        for clock in (None, lambda: -1, lambda: True, lambda: "1", lambda: 1.0):
            self._assert_code("invalid_clock", lambda: channel.read_frame(
                reader, deadline_ns=100, _clock_ns=clock))

        def failed_clock():
            raise ValueError("do-not-reflect-clock")

        self._assert_code("invalid_clock", lambda: channel.read_frame(
            reader, deadline_ns=100, _clock_ns=failed_clock))

    def test_backward_clock_fails_during_actual_read(self):
        reader, writer = self._pipe()
        os.write(writer, b"\0\0\0\x01x")
        self._close(writer)
        clock = _Clock(10)
        original_read = os.read

        def rewind(fd, size):
            result = original_read(fd, size)
            clock.now = 9
            return result

        with patch.object(channel.os, "read", side_effect=rewind):
            self._assert_code("invalid_clock", lambda: channel.read_frame(
                reader, deadline_ns=100, _clock_ns=clock))

    def test_deadline_cancellation_and_exit_after_complete_read_rejected(self):
        for failure in ("channel_timeout", "channel_cancelled", "child_exited"):
            with self.subTest(failure=failure):
                reader, writer = self._pipe()
                os.write(writer, b"\0\0\0\x01x")
                self._close(writer)
                clock = _Clock()
                cancel = threading.Event()
                alive = [True]
                original_read = os.read

                def after_read(fd, size):
                    result = original_read(fd, size)
                    if not result:  # Failure occurs only after full frame AND EOF.
                        if failure == "channel_timeout":
                            clock.now = 100
                        elif failure == "channel_cancelled":
                            cancel.set()
                        else:
                            alive[0] = False
                    return result

                with patch.object(channel.os, "read", side_effect=after_read):
                    self._assert_code(failure, lambda: channel.read_frame(
                        reader, deadline_ns=100, _clock_ns=clock,
                        cancel=cancel, is_alive=lambda: alive[0]))

    def test_deadline_cancellation_and_exit_after_complete_write_rejected(self):
        for failure in ("channel_timeout", "channel_cancelled", "child_exited"):
            with self.subTest(failure=failure):
                _, writer = self._pipe()
                clock = _Clock()
                cancel = threading.Event()
                alive = [True]
                original_write = os.write

                def after_write(fd, data):
                    result = original_write(fd, data)
                    if bytes(data) == b"x":
                        if failure == "channel_timeout":
                            clock.now = 100
                        elif failure == "channel_cancelled":
                            cancel.set()
                        else:
                            alive[0] = False
                    return result

                with patch.object(channel.os, "write", side_effect=after_write):
                    self._assert_code(failure, lambda: channel.write_frame(
                        writer, b"x", deadline_ns=100, _clock_ns=clock,
                        cancel=cancel, is_alive=lambda: alive[0]))

    def test_pending_read_and_write_cancellation_and_poll_bound(self):
        for writing in (False, True):
            with self.subTest(writing=writing):
                reader, writer = self._pipe()
                if writing:
                    self._fill_pipe(writer)
                cancel = threading.Event()
                timer = threading.Timer(0.08, cancel.set)
                timer.start()
                self.addCleanup(timer.join, 1)
                selector = channel.selectors.DefaultSelector()
                original_select = selector.select
                timeouts = []

                def observe_select(timeout):
                    timeouts.append(timeout)
                    return original_select(timeout)

                started = time.monotonic()
                with patch.object(channel.selectors, "DefaultSelector", return_value=selector), \
                     patch.object(selector, "select", side_effect=observe_select):
                    self._assert_code("channel_cancelled", lambda: (
                        channel.write_frame(writer, b"x", deadline_ns=_deadline(), cancel=cancel)
                        if writing else channel.read_frame(reader, deadline_ns=_deadline(), cancel=cancel)))
                self.assertTrue(timeouts)
                self.assertLessEqual(max(timeouts), 0.05)
                self.assertLess(time.monotonic() - started, 1.5)

    def test_child_exit_before_exchange_rejects_without_io(self):
        reader, writer = self._pipe()
        with patch.object(channel.os, "fstat") as observe:
            self._assert_code("child_exited", lambda: channel.read_frame(
                reader, deadline_ns=_deadline(), is_alive=lambda: False))
            self._assert_code("child_exited", lambda: channel.write_frame(
                writer, b"x", deadline_ns=_deadline(), is_alive=lambda: False))
        observe.assert_not_called()

    def test_elapsed_callback_time_counts_against_deadline(self):
        reader, _ = self._pipe()
        clock = _Clock()

        def callback():
            clock.now = 100
            return True

        self._assert_code("channel_timeout", lambda: channel.read_frame(
            reader, deadline_ns=100, _clock_ns=clock, is_alive=callback))

    def test_invalid_or_throwing_observers_return_fixed_codes(self):
        reader, _ = self._pipe()
        self._assert_code("invalid_cancel", lambda: channel.read_frame(
            reader, deadline_ns=_deadline(), cancel=True))
        for observer in (True, lambda: None, lambda: 1):
            self._assert_code("invalid_liveness", lambda: channel.read_frame(
                reader, deadline_ns=_deadline(), is_alive=observer))

        def failed_observer():
            raise ValueError("do-not-reflect-observer")

        try:
            channel.read_frame(reader, deadline_ns=_deadline(), is_alive=failed_observer)
        except channel.ChannelError:
            text = traceback.format_exc()
        else:
            self.fail("Expected channel observer failure")
        self.assertNotIn("do-not-reflect-observer", text)
        self.assertNotIn("ValueError", text)
        self.assertIn("invalid_liveness", text)

    def test_interrupted_read_and_write_retry_with_same_budget(self):
        reader, writer = self._pipe()
        original_write = os.write
        write_calls = [0]

        def interrupted_write(fd, data):
            write_calls[0] += 1
            if write_calls[0] == 1:
                raise InterruptedError()
            return original_write(fd, data)

        deadline = _deadline()
        with patch.object(channel.os, "write", side_effect=interrupted_write):
            channel.write_frame(writer, b"x", deadline_ns=deadline)
        self._close(writer)
        original_read = os.read
        read_calls = [0]

        def interrupted_read(fd, size):
            read_calls[0] += 1
            if read_calls[0] == 1:
                raise InterruptedError()
            return original_read(fd, size)

        with patch.object(channel.os, "read", side_effect=interrupted_read):
            self.assertEqual(channel.read_frame(reader, deadline_ns=deadline), b"x")

    def test_noninheritance_helper_validates_all_descriptors_before_changes(self):
        reader, writer = self._pipe()
        os.set_inheritable(reader, True)
        os.set_inheritable(writer, True)
        self._assert_code("invalid_fd", lambda: channel.mark_noninheritable(reader, None, writer))
        self.assertTrue(os.get_inheritable(reader))
        self.assertTrue(os.get_inheritable(writer))
        channel.mark_noninheritable(reader, writer)
        self.assertFalse(os.get_inheritable(reader))
        self.assertFalse(os.get_inheritable(writer))

    def test_unrelated_exec_does_not_inherit_owned_pipe_endpoints(self):
        reader, writer = self._pipe()
        # Deliberately begin inheritable, so the helper's boundary is exercised.
        for fd in (reader, writer):
            os.set_inheritable(fd, True)
        identities = [(fd, os.fstat(fd).st_dev, os.fstat(fd).st_ino)
                      for fd in (reader, writer)]
        channel.mark_noninheritable(reader, writer)
        script = """
import json,os,sys
leaked = []
for fd,dev,ino in json.loads(sys.argv[1]):
    try:
        observed = os.fstat(fd)
    except OSError:
        continue
    if (observed.st_dev, observed.st_ino) == (dev, ino):
        leaked.append(fd)
print(json.dumps({'owned_endpoints_leaked': bool(leaked)}))
"""
        result = subprocess.run([sys.executable, "-I", "-S", "-c", script, json.dumps(identities)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, close_fds=False, timeout=3,
                                env={key: value for key, value in os.environ.items()
                                     if key in ("PATH", "LANG", "TMPDIR")}, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"owned_endpoints_leaked": False})

    def test_selected_child_can_receive_only_explicitly_passed_endpoint(self):
        reader, writer = self._pipe()
        channel.mark_noninheritable(reader, writer)
        script = """
import os,sys
fd = int(sys.argv[1])
os.set_inheritable(fd, False)
os.write(fd, b'\\0\\0\\0\\x01x')
os.close(fd)
"""
        child = subprocess.Popen([sys.executable, "-I", "-S", "-c", script, str(writer)],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, close_fds=True, pass_fds=(writer,),
                                 env={key: value for key, value in os.environ.items()
                                      if key in ("PATH", "LANG", "TMPDIR")})

        def cleanup_child():
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)

        self.addCleanup(cleanup_child)
        self._close(writer)
        self.assertEqual(channel.read_frame(reader, deadline_ns=_deadline()), b"x")
        self.assertEqual(child.wait(timeout=3), 0)


if __name__ == "__main__":
    unittest.main()
