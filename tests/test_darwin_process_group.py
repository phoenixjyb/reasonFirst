"""Darwin zombie-only EPERM evidence; mocks are not native acceptance."""
from __future__ import annotations

import ctypes
import errno
import os
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from gitlab_agent import _darwin_process_group as dg
from gitlab_agent import validation_process as vp


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.proc = SimpleNamespace(pid=1234, returncode=None)
        self.info = Mock()
        self.info.members.return_value = frozenset({1234})
        self.info.status.return_value = (1234, os.getpid(), 1234, dg._SZOMB)
        self.platform = patch.object(dg.sys, "platform", "darwin")
        self.platform.start()
        self.addCleanup(self.platform.stop)
        factory = patch.object(dg, "_GroupInfo", return_value=self.info)
        factory.start()
        self.addCleanup(factory.stop)

    def test_complete_stable_owned_zombie_group_is_accepted_without_reaping(self):
        self.assertTrue(dg.owned_group_is_zombie_only(self.proc))
        self.assertIsNone(self.proc.returncode)
        self.assertEqual(self.info.method_calls, [call.members(1234),
                                                 call.status(1234), call.members(1234)])

    def test_multiple_zombies_are_accepted_only_after_each_is_observed(self):
        self.info.members.return_value = frozenset({1234, 5678})
        self.info.status.side_effect = [(1234, os.getpid(), 1234, 5), (5678, 1, 1234, 5)]
        self.assertTrue(dg.owned_group_is_zombie_only(self.proc))
        self.assertEqual(self.info.status.call_count, 2)

    def test_zombie_leader_does_not_hide_live_descendant(self):
        self.info.members.return_value = frozenset({1234, 5678})
        self.info.status.side_effect = [(1234, os.getpid(), 1234, 5), (5678, 1, 1234, 2)]
        self.assertFalse(dg.owned_group_is_zombie_only(self.proc))

    def test_all_non_zombie_states_are_rejected(self):
        for state in (0, 1, 2, 3, 4, 6, 999):
            with self.subTest(state=state):
                self.info.status.return_value = (1234, os.getpid(), 1234, state)
                self.assertFalse(dg.owned_group_is_zombie_only(self.proc))

    def test_missing_or_mismatched_leader_and_parent_are_rejected(self):
        for status in ((999, os.getpid(), 1234, 5), (1234, -1, 1234, 5),
                       (1234, os.getpid(), 5678, 5)):
            with self.subTest(status=status):
                self.info.status.return_value = status
                self.assertFalse(dg.owned_group_is_zombie_only(self.proc))
        self.info.members.return_value = frozenset({5678})
        self.assertFalse(dg.owned_group_is_zombie_only(self.proc))

    def test_changed_membership_is_unknown_not_zombie_only(self):
        for after in (frozenset(), frozenset({1234, 5678})):
            with self.subTest(after=after):
                self.info.members.side_effect = [frozenset({1234}), after]
                self.assertFalse(dg.owned_group_is_zombie_only(self.proc))

    def test_inaccessible_or_missing_member_does_not_suppress_error(self):
        for error in (PermissionError(errno.EPERM, "private"), ProcessLookupError(),
                      RuntimeError(), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                self.info.status.side_effect = error
                self.assertFalse(dg.owned_group_is_zombie_only(self.proc))

    def test_expired_budget_does_not_become_success(self):
        with patch.object(dg.time, "monotonic", side_effect=[0, 1]):
            self.assertFalse(dg.owned_group_is_zombie_only(self.proc))
        self.info.members.assert_not_called()

    def test_expiry_after_final_query_is_rejected(self):
        with patch.object(dg.time, "monotonic", side_effect=[0, 0, 0, 0, 1]):
            self.assertFalse(dg.owned_group_is_zombie_only(self.proc))

    def test_reaped_or_invalid_identity_is_rejected_before_observation(self):
        for proc in (SimpleNamespace(pid=1234, returncode=0),
                     SimpleNamespace(pid=0, returncode=None),
                     SimpleNamespace(pid=True, returncode=None)):
            with self.subTest(proc=proc):
                self.assertFalse(dg.owned_group_is_zombie_only(proc))
        self.info.members.assert_not_called()

    def test_reaping_during_observation_is_rejected(self):
        def members(_):
            self.proc.returncode = -15
            return frozenset({1234})
        self.info.members.side_effect = members
        self.assertFalse(dg.owned_group_is_zombie_only(self.proc))

    def test_other_platform_never_loads_libproc(self):
        with patch.object(dg.sys, "platform", "linux"), patch.object(dg, "_GroupInfo") as load:
            self.assertFalse(dg.owned_group_is_zombie_only(object()))
            load.assert_not_called()

    def test_unavailable_library_is_a_failed_observation(self):
        with patch.object(dg, "_GroupInfo", side_effect=OSError("private")):
            self.assertFalse(dg.owned_group_is_zombie_only(self.proc))


class NativeAPIBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.lib = Mock()
        with patch.object(dg.ctypes, "CDLL", return_value=self.lib) as load:
            self.observer = dg._GroupInfo()
        load.assert_called_once_with("/usr/lib/libproc.dylib", use_errno=True)

    def test_short_bsd_layout_and_function_declarations(self):
        self.assertEqual(ctypes.sizeof(dg._ShortBSDInfo), 64)
        self.assertEqual(dg._ShortBSDInfo.status.offset, 12)
        self.assertEqual(self.lib.proc_pidinfo.argtypes[2], ctypes.c_uint64)

    def test_pid_list_is_group_scoped_and_fixed_size(self):
        def fill(kind, group, values, size):
            self.assertEqual((kind, group, size), (2, 1234, 4 * (dg._MAX_GROUP_MEMBERS + 1)))
            values[0], values[1] = 1234, 5678
            return 8
        self.lib.proc_listpids.side_effect = fill
        self.assertEqual(self.observer.members(1234), frozenset({1234, 5678}))
        self.lib.proc_listpids.assert_called_once()

    def test_empty_failed_misaligned_oversized_lists_are_not_complete(self):
        for count in (-1, 0, 1, 3, 4 * (dg._MAX_GROUP_MEMBERS + 1), 99999):
            with self.subTest(count=count):
                self.lib.proc_listpids.return_value = count
                with self.assertRaises(RuntimeError):
                    self.observer.members(1234)

    def test_duplicate_and_invalid_pids_rejected(self):
        for ids in ((1234, 1234), (1234, 0), (1234, 1), (1234, -7)):
            def fill(kind, group, values, size):
                values[0], values[1] = ids
                return 8
            self.lib.proc_listpids.side_effect = fill
            with self.subTest(ids=ids), self.assertRaises(RuntimeError):
                self.observer.members(1234)

    def test_short_status_requests_zombies_and_returns_only_numeric_identity(self):
        def fill(pid, flavor, arg, pointer, size):
            self.assertEqual((pid, flavor, arg, size), (1234, 13, 1, 64))
            info = ctypes.cast(pointer, ctypes.POINTER(dg._ShortBSDInfo)).contents
            info.pid, info.ppid, info.pgid, info.status = 1234, os.getpid(), 1234, 5
            info.comm = b"not-reported"
            return size
        self.lib.proc_pidinfo.side_effect = fill
        self.assertEqual(self.observer.status(1234), (1234, os.getpid(), 1234, 5))

    def test_short_reads_fail_instead_of_using_zero_filled_fields(self):
        for size in (-1, 0, 16, 63, 65):
            with self.subTest(size=size):
                self.lib.proc_pidinfo.return_value = size
                with self.assertRaises(RuntimeError):
                    self.observer.status(1234)

    def test_errno_is_checked_even_with_plausible_byte_count(self):
        def fail(*args):
            ctypes.set_errno(errno.EPERM)
            return 64
        self.lib.proc_pidinfo.side_effect = fail
        with self.assertRaises(RuntimeError):
            self.observer.status(1234)


@unittest.skipUnless(os.name == "posix", "POSIX group-signal path")
class SignalClassificationTests(unittest.TestCase):
    def setUp(self):
        self.proc = Mock(pid=1234, returncode=None)

    def test_eperm_requires_positive_group_evidence_and_reaps_last(self):
        order = []
        def killpg(pid, sig):
            order.append(sig)
            if sig == signal.SIGKILL:
                raise PermissionError(errno.EPERM, "private")
        def observe(proc):
            self.assertIsNone(proc.returncode)
            order.append("observe")
            return True
        self.proc.wait.side_effect = lambda **kw: order.append("wait")
        with patch.object(vp.os, "killpg", side_effect=killpg), \
                patch.object(vp.time, "sleep"), \
                patch.object(vp, "owned_group_is_zombie_only", side_effect=observe):
            self.assertEqual(vp._terminate(self.proc, posix_group=True), [])
        self.assertEqual(order, [signal.SIGTERM, signal.SIGKILL, "observe", "wait"])
        self.proc.kill.assert_not_called()

    def test_unknown_or_live_group_retains_signal_failure(self):
        with patch.object(vp.os, "killpg", side_effect=[None, PermissionError(errno.EPERM, "private")]), \
                patch.object(vp.time, "sleep"), \
                patch.object(vp, "owned_group_is_zombie_only", return_value=False):
            self.assertEqual(vp._terminate(self.proc, posix_group=True), ["command_group_signal_failed"])
        self.proc.kill.assert_called_once()
        self.proc.wait.assert_called_once_with(timeout=vp._REAP_SECONDS)

    def test_non_eperm_permission_error_is_not_disambiguated(self):
        with patch.object(vp.os, "killpg", side_effect=[None, PermissionError(errno.EACCES, "private")]), \
                patch.object(vp.time, "sleep"), \
                patch.object(vp, "owned_group_is_zombie_only") as observe:
            self.assertEqual(vp._terminate(self.proc, posix_group=True), ["command_group_signal_failed"])
        observe.assert_not_called()

    def test_failed_first_signal_is_not_erased_by_successful_escalation(self):
        with patch.object(vp.os, "killpg", side_effect=[PermissionError(errno.EPERM, "private"), None]), \
                patch.object(vp.time, "sleep"), \
                patch.object(vp, "owned_group_is_zombie_only", return_value=False):
            self.assertEqual(vp._terminate(self.proc, posix_group=True), ["command_group_signal_failed"])

    def test_both_signals_on_already_exited_group_require_their_own_evidence(self):
        with patch.object(vp.os, "killpg", side_effect=PermissionError(errno.EPERM, "private")), \
                patch.object(vp.time, "sleep"), \
                patch.object(vp, "owned_group_is_zombie_only", return_value=True) as observe:
            self.assertEqual(vp._terminate(self.proc, posix_group=True), [])
        self.assertEqual(observe.call_count, 2)

    def test_direct_child_cleanup_does_not_consult_group_observer(self):
        with patch.object(vp, "owned_group_is_zombie_only") as observe:
            self.assertEqual(vp._terminate(self.proc, posix_group=False), [])
        observe.assert_not_called()


@unittest.skipUnless(sys.platform == "darwin", "native Darwin libproc observation")
class DarwinNativeTests(unittest.TestCase):
    def test_native_zombie_group_and_live_descendant_are_distinguished(self):
        for live_descendant in (False, True):
            with self.subTest(live_descendant=live_descendant):
                code = ("import subprocess,sys;subprocess.Popen([sys.executable,'-I','-S','-c',"
                        "'import time;time.sleep(5)'])" if live_descendant else "pass")
                proc = subprocess.Popen([sys.executable, "-I", "-S", "-c", code],
                                        env={}, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        start_new_session=True)
                try:
                    info = dg._GroupInfo()
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        if info.status(proc.pid)[3] == dg._SZOMB:
                            break
                        time.sleep(0.01)
                    else:
                        self.fail("fixture leader did not exit")
                    self.assertEqual(dg.owned_group_is_zombie_only(proc), not live_descendant)
                    self.assertIsNone(proc.returncode)
                finally:
                    # Signal only this fixture's still-unreaped group.
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except PermissionError as exc:
                        if exc.errno != errno.EPERM or not dg.owned_group_is_zombie_only(proc):
                            raise
                    finally:
                        proc.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
