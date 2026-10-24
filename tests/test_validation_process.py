"""Capture bounds, platform adapters, and cleanup failure paths."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from gitlab_agent import validation_process as vp


class CaptureTests(unittest.TestCase):
    def test_split_multibyte_newlines_and_prefix_limits(self):
        source = "甲\r\n乙\r丙\n".encode("utf-8")
        expected = "甲\n乙\n丙\n".encode("utf-8")
        for limit in range(len(expected) + 2):
            with self.subTest(limit=limit):
                capture = vp._Capture(limit, "utf-8")
                for byte in source:
                    capture.feed(bytes([byte]))
                    self.assertLessEqual(len(capture.prefix), limit)
                capture.eof()
                result = capture.result("stdout")
                self.assertEqual(result["stdout"], expected[:limit].decode("utf-8", "ignore"))
                self.assertEqual(result["stdout_original_bytes"], len(expected))
                self.assertEqual(result["stdout_truncated"], len(expected) > limit)
                self.assertTrue(result["stdout_complete"])

    def test_locale_decoding_counts_normalized_utf8_not_raw_pipe_bytes(self):
        capture = vp._Capture(3, "latin-1")
        capture.feed(b"\xe9\xe9\r\n")
        capture.eof()
        self.assertEqual(capture.result("stderr"), {
            "stderr": "é", "stderr_original_bytes": 5,
            "stderr_truncated": True, "stderr_complete": True,
        })

    def test_incomplete_input_is_not_reported_as_complete(self):
        capture = vp._Capture(10, "utf-8")
        capture.feed(b"\xe7")
        result = capture.result("stdout")
        self.assertEqual(result["stdout"], "\ufffd")
        self.assertEqual(result["stdout_original_bytes"], 3)
        self.assertFalse(result["stdout_complete"])
        self.assertEqual(capture.result("stdout"), result)

    def test_incomplete_stateful_encoding_is_bounded(self):
        capture = vp._Capture(10, "utf-7")
        with self.assertRaisesRegex(RuntimeError, "^command_output_decode_failed$"):
            capture.feed(b"+" + b"A" * vp._READ_BYTES)


class WindowsAdapterTests(unittest.TestCase):
    """API-boundary simulations; native Windows is still a separate CI gate."""

    def peek_fixture(self, *, available=0, error=None):
        peek = vp._WindowsPeek.__new__(vp._WindowsPeek)
        peek.dword = ctypes.c_uint32
        peek.ctypes = SimpleNamespace(byref=ctypes.byref, get_last_error=lambda: error)
        peek.get_handle = lambda fd: fd + 1000
        def call(handle, buffer, size, read_count, total, left):
            self.assertEqual(handle, 1007)
            self.assertEqual((buffer, size, read_count, left), (None, 0, None, None))
            total._obj.value = available
            return error is None
        peek.peek = call
        return peek

    def test_availability_empty_and_eof_are_distinct(self):
        self.assertEqual(self.peek_fixture(available=12345).available(7), 12345)
        self.assertEqual(self.peek_fixture().available(7), 0)
        for error in (109, 232, 233):
            self.assertIsNone(self.peek_fixture(error=error).available(7))

    def test_other_errors_fail_without_reflecting_os_details(self):
        with self.assertRaisesRegex(OSError, "^command_output_read_failed$"):
            self.peek_fixture(error=5).available(7)

    def test_reader_never_reads_empty_pipe_or_more_than_one_chunk(self):
        reader = vp._PipeReader.__new__(vp._PipeReader)
        reader.selector = None
        reader.peek = Mock()
        reader.pipes = {"stdout": SimpleNamespace(fileno=lambda: 7)}
        captures = {"stdout": vp._Capture(1, "utf-8")}
        reader.peek.available.return_value = 0
        with patch.object(vp.os, "read") as read, patch.object(vp.time, "sleep") as sleep:
            reader.step(captures, 0.02)
            read.assert_not_called()
            sleep.assert_called_once_with(0.02)
        reader.peek.available.return_value = 1000000
        with patch.object(vp.os, "read", return_value=b"x" * vp._READ_BYTES) as read:
            reader.step(captures, 0.02)
            read.assert_called_once_with(7, vp._READ_BYTES)
        reader.peek.available.return_value = None
        with patch.object(vp.os, "read") as read:
            reader.step(captures, 0.02)
            read.assert_not_called()
        self.assertFalse(reader.pipes)
        self.assertEqual(captures["stdout"].total, vp._READ_BYTES)
        self.assertTrue(captures["stdout"].complete)

    def test_direct_child_cleanup_uses_owned_object_not_process_names(self):
        proc = Mock(returncode=None)
        self.assertEqual(vp._terminate(proc, posix_group=False), [])
        self.assertEqual(proc.method_calls, [call.kill(),
                                            call.wait(timeout=vp._REAP_SECONDS)])


class ProcessCleanupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="rf-capture-process-")
        self.addCleanup(temp.cleanup)
        self.cwd = Path(temp.name)

    def run_code(self, code, *, timeout=2):
        # Standard-library fixtures exclude both environment injection and site hooks.
        return vp.run_bounded_command([sys.executable, "-I", "-S", "-c", code],
                                      cwd=self.cwd, env=os.environ.copy(),
                                      timeout=timeout, max_output_bytes=512,
                                      encoding="utf-8")

    def test_invalid_limits_and_codec_fail_before_launch(self):
        for timeout, budget, codec in ((0, 10, "utf-8"), (float("nan"), 10, "utf-8"),
                                       (float("inf"), 10, "utf-8"), (1, -1, "utf-8"),
                                       (1, True, "utf-8"), (1, 10, "not-a-codec")):
            with self.subTest(timeout=timeout, budget=budget, codec=codec):
                with patch.object(vp.subprocess, "Popen") as launch:
                    with self.assertRaises((ValueError, LookupError)):
                        vp.run_bounded_command([sys.executable], cwd=self.cwd, env={},
                                               timeout=timeout, max_output_bytes=budget,
                                               encoding=codec)
                    launch.assert_not_called()

    def test_launch_error_remains_an_oserror(self):
        with self.assertRaises(OSError):
            vp.run_bounded_command([str(self.cwd / "missing-executable")], cwd=self.cwd,
                                   env=os.environ.copy(), timeout=1, max_output_bytes=512)

    def test_read_error_and_interrupt_reap_real_child_and_close_pipes(self):
        popen = subprocess.Popen
        for exception in (OSError("private detail"), KeyboardInterrupt(), SystemExit(3)):
            with self.subTest(exception=type(exception).__name__):
                owned = []
                def launch(*args, **kwargs):
                    proc = popen(*args, **kwargs)
                    owned.append(proc)
                    return proc
                with patch.object(vp.subprocess, "Popen", side_effect=launch), \
                        patch.object(vp._PipeReader, "step", side_effect=exception):
                    expected = vp.CommandProcessError if isinstance(exception, OSError) else type(exception)
                    with self.assertRaises(expected) as raised:
                        self.run_code("import time;time.sleep(5)")
                self.assertNotIn("private detail", str(raised.exception))
                self.assertEqual(len(owned), 1)
                self.assertIsNotNone(owned[0].returncode)
                self.assertTrue(owned[0].stdout.closed)
                self.assertTrue(owned[0].stderr.closed)
                if isinstance(raised.exception, vp.CommandProcessError):
                    self.assertEqual(raised.exception.reason, "command_output_read_failed")
                    self.assertFalse(raised.exception.cleanup_errors)
                    self.assertTrue(raised.exception.cleanup["direct_child_reaped"])

    def test_reader_setup_failure_still_cleans_up_process_and_pipes(self):
        popen = subprocess.Popen
        owned = []
        def launch(*args, **kwargs):
            owned.append(popen(*args, **kwargs))
            return owned[-1]
        with patch.object(vp.subprocess, "Popen", side_effect=launch), \
                patch.object(vp, "_PipeReader", side_effect=OSError("private startup detail")):
            with self.assertRaisesRegex(vp.CommandProcessError, "^command_output_read_failed$"):
                self.run_code("import time;time.sleep(5)")
        self.assertIsNotNone(owned[0].returncode)
        self.assertTrue(owned[0].stdout.closed)
        self.assertTrue(owned[0].stderr.closed)

    def test_failed_cleanup_does_not_replace_timeout_with_success(self):
        terminate = vp._terminate
        def cleanup(*args, **kwargs):
            # Real process is cleaned up, then simulate incomplete OS evidence.
            return terminate(*args, **kwargs) + ["command_child_reap_failed"]
        with patch.object(vp, "_terminate", side_effect=cleanup):
            with self.assertRaises(vp.CommandProcessError) as raised:
                self.run_code("import time;time.sleep(5)", timeout=0.1)
        self.assertEqual(raised.exception.reason, "command_timed_out")
        self.assertEqual(raised.exception.cleanup_errors, ("command_child_reap_failed",))
        self.assertIn("command_cleanup_failed", str(raised.exception))

    def test_close_failure_is_not_returned_as_zero_exit_success(self):
        close = vp._PipeReader.close
        def fail(reader):
            close(reader)
            raise OSError("private close detail")
        with patch.object(vp._PipeReader, "close", fail):
            with self.assertRaises(vp.CommandProcessError) as raised:
                self.run_code("print('normal exit')")
        self.assertEqual(raised.exception.reason, "command_cleanup_failed")
        self.assertTrue(raised.exception.cleanup["direct_child_reaped"])
        self.assertTrue(raised.exception.cleanup["output_pipes_closed"])
        self.assertNotIn("private", str(raised.exception))

    @unittest.skipUnless(os.name == "posix", "POSIX process-group signalling")
    def test_escalation_precedes_reaping_and_reaped_identity_is_never_signalled(self):
        order = []
        proc = SimpleNamespace(pid=1234, returncode=None,
                               wait=lambda **kwargs: order.append("wait"))
        with patch.object(vp.os, "killpg", side_effect=lambda pid, sig: order.append((pid, sig))), \
                patch.object(vp.time, "sleep", side_effect=lambda _: order.append("grace")):
            self.assertEqual(vp._terminate(proc, posix_group=True), [])
        self.assertEqual(order, [(1234, signal.SIGTERM), "grace", (1234, signal.SIGKILL), "wait"])
        proc.returncode = 0
        with patch.object(vp.os, "killpg") as signal_group:
            self.assertEqual(vp._terminate(proc, posix_group=True), ["command_group_identity_lost"])
            signal_group.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "POSIX process-group isolation")
    def test_timeout_does_not_kill_unrelated_process(self):
        unrelated = subprocess.Popen([sys.executable, "-I", "-S", "-c", "import time;time.sleep(10)"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True)
        try:
            result = self.run_code("import time;time.sleep(5)", timeout=0.2)
            self.assertTrue(result["timed_out"])
            self.assertIsNone(unrelated.poll())
        finally:
            unrelated.kill()
            unrelated.wait(timeout=3)

    @unittest.skipUnless(os.name == "posix", "POSIX signal escalation")
    def test_sigterm_ignoring_group_is_killed(self):
        code = ("import signal,subprocess,sys,time\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "p=subprocess.Popen([sys.executable,'-I','-S','-c',"
                "\"import time;print('grandchild ready',flush=True);time.sleep(4)\"])\n"
                "time.sleep(4)\n")
        start = time.monotonic()
        result = self.run_code(code, timeout=1.5)
        self.assertTrue(result["timed_out"])
        self.assertIn("grandchild ready", result["stdout"])
        self.assertTrue(result["output_complete"])
        self.assertTrue(result["cleanup"]["direct_child_reaped"])
        self.assertLess(time.monotonic() - start, 3.5)

    @unittest.skipUnless(os.name == "posix", "POSIX escaped-session reporting boundary")
    def test_escaped_pipe_holder_is_incomplete_not_an_unbounded_wait(self):
        done = self.cwd / "escaped-child-done"
        child = ("import os,time,pathlib;os.setsid();print('escaped ready',flush=True);"
                 f"time.sleep(2.5);pathlib.Path({str(done)!r}).write_text('done')")
        code = ("import subprocess,sys,time\n"
                f"subprocess.Popen([sys.executable,'-I','-S','-c',{child!r}])\n"
                "time.sleep(5)")
        start = time.monotonic()
        try:
            result = self.run_code(code, timeout=1.5)
            self.assertTrue(result["timed_out"])
            self.assertIn("escaped ready", result["stdout"])
            self.assertFalse(result["output_complete"])
            self.assertFalse(result["stdout_complete"])
            self.assertTrue(result["cleanup"]["output_pipes_closed"])
            self.assertFalse(result["cleanup"]["descendants_verified"])
            self.assertLess(time.monotonic() - start, 2.4)
        finally:
            # This deliberately escaped fixture is self-limited; wait for it,
            # never scan or signal arbitrary PIDs from the production helper.
            deadline = time.monotonic() + 4
            while not done.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
        self.assertTrue(done.exists(), "self-limited escaped fixture did not finish")

    @unittest.skipUnless(sys.platform == "linux", "Linux descriptor inventory")
    def test_repeated_runs_leave_no_output_descriptors(self):
        before = len(os.listdir("/proc/self/fd"))
        for _ in range(6):
            self.run_code("print('ok')")
        self.run_code("import time;time.sleep(2)", timeout=0.1)
        self.assertEqual(len(os.listdir("/proc/self/fd")), before)


if __name__ == "__main__":
    unittest.main()
