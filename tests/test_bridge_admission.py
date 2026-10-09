from __future__ import annotations

import copy
import json
import os
import select
import signal
import threading
import unittest
from unittest import mock

from gitlab_agent.bridge_preview import admission as a


class _Poison:
    def __str__(self):
        raise AssertionError("must not stringify external values")

    __repr__ = __str__

    def __hash__(self):
        raise AssertionError("must not hash external values")


class ControllerAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.gate = a.ControllerAdmission()
        self.gate.claim_controller()

    def assert_code(self, code, method, *args, **kwargs):
        with self.assertRaises(a.AdmissionError) as caught:
            method(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def turn(self, *, app="app-instance", thread="thread", workspace="workspace"):
        return self.gate.reserve_turn(app_key=app, thread_id=thread, workspace_key=workspace)

    def event(self, method, *, app="app-instance", thread="thread", turn="turn"):
        return getattr(self.gate, method)(app_key=app, thread_id=thread, turn_id=turn)

    def assert_busy_open(self):
        before = self.gate.snapshot()
        self.assert_code("maintenance_busy", self.gate.try_enter_maintenance)
        self.assertEqual(self.gate.snapshot(), before)
        self.assertTrue(before["admission_open"])
        self.assertFalse(before["idle_observed"])

    def test_snapshot_is_fixed_scoped_and_detached(self):
        result = self.gate.snapshot()
        self.assertEqual(set(result), {
            "schema_version", "scope", "state", "admission_open", "counts",
            "unknown_reasons", "idle_observed", "maintenance_held",
            "activation_authorized", "global_idle_verified",
        })
        self.assertEqual(result["scope"], "controller-instance")
        self.assertEqual(result["counts"], {
            "operations": 0, "callbacks": 0, "approvals": 0, "starting_turns": 0,
            "running_turns": 0, "stopping_turns": 0, "unknown_turns": 0,
        })
        self.assertTrue(result["idle_observed"])
        self.assertFalse(result["activation_authorized"])
        self.assertFalse(result["global_idle_verified"])
        result["counts"]["operations"] = 999
        result["unknown_reasons"].append("external text")
        self.assertEqual(self.gate.snapshot()["counts"]["operations"], 0)
        self.assertEqual(self.gate.snapshot()["unknown_reasons"], [])

    def test_unclaimed_gate_cannot_admit_or_lease(self):
        gate = a.ControllerAdmission()
        self.assertEqual(gate.snapshot()["state"], "unclaimed")
        self.assertFalse(gate.snapshot()["idle_observed"])
        self.assertFalse(gate.snapshot()["admission_open"])
        self.assert_code("admission_unclaimed", gate.reserve_operation)
        self.assert_code("admission_unclaimed", gate.reserve_turn, app_key="a", thread_id="t")
        self.assert_code("admission_unclaimed", gate.try_enter_maintenance)

    def test_controller_claim_is_one_use_even_after_close(self):
        self.assert_code("admission_already_claimed", self.gate.claim_controller)
        self.gate.close()
        self.assert_code("admission_already_claimed", self.gate.claim_controller)
        unused = a.ControllerAdmission()
        unused.close()
        self.assert_code("admission_closed", unused.claim_controller)

    def test_recovered_unknown_can_precede_claim_without_becoming_idle(self):
        gate = a.ControllerAdmission()
        gate.mark_unknown("recovered_state_unverified")
        gate.claim_controller()
        self.assertTrue(gate.snapshot()["admission_open"])
        self.assertFalse(gate.snapshot()["idle_observed"])
        self.assert_code("maintenance_busy", gate.try_enter_maintenance)

    def test_each_operation_kind_blocks_maintenance_until_finished(self):
        tokens = {kind: self.gate.reserve_operation(kind=kind)
                  for kind in ("operation", "approval", "callback")}
        self.assertEqual({k: self.gate.snapshot()["counts"][k]
                          for k in ("operations", "approvals", "callbacks")},
                         {"operations": 1, "approvals": 1, "callbacks": 1})
        for token in tokens.values():
            self.assert_busy_open()
            self.gate.finish_operation(token)
        lease = self.gate.try_enter_maintenance()
        self.assertFalse(self.gate.snapshot()["admission_open"])
        self.gate.leave_maintenance(lease)

    def test_approval_reservation_covers_response_delivery_after_turn_completion(self):
        turn = self.turn()
        self.gate.bind_turn(turn, turn_id="turn")
        approval = self.gate.reserve_operation(kind="approval")
        self.event("turn_completed")
        self.assertEqual(self.gate.snapshot()["counts"]["running_turns"], 0)
        self.assert_busy_open()
        self.gate.finish_operation(approval, uncertain=True)
        self.assertEqual(self.gate.snapshot()["counts"]["approvals"], 0)
        self.assertIn("operation_outcome_unknown", self.gate.snapshot()["unknown_reasons"])
        self.assert_busy_open()

    def test_unknown_operation_outcome_is_sticky_but_normal_work_stays_available(self):
        token = self.gate.reserve_operation()
        self.gate.finish_operation(token, uncertain=True)
        for kind in ("operation", "approval", "callback"):
            token = self.gate.reserve_operation(kind=kind)
            self.gate.finish_operation(token)
        self.assertEqual(self.gate.snapshot()["unknown_reasons"], ["operation_outcome_unknown"])
        self.assert_busy_open()

    def test_invalid_kind_and_outcome_are_fixed_errors_without_releasing_work(self):
        for value in ("secret-token-value", None, [], _Poison()):
            self.assert_code("invalid_activity_kind", self.gate.reserve_operation, kind=value)
        token = self.gate.reserve_operation()
        for value in (0, 1, "false", _Poison()):
            self.assert_code("invalid_outcome", self.gate.finish_operation, token, uncertain=value)
        self.assertEqual(self.gate.snapshot()["counts"]["operations"], 1)
        self.gate.finish_operation(token)

    def test_operation_tokens_are_exact_one_use_objects_from_the_owning_gate(self):
        other = a.ControllerAdmission()
        other.claim_controller()
        token = self.gate.reserve_operation()
        foreign = other.reserve_operation()

        class Derived(a.OperationReservation):
            pass

        before = self.gate.snapshot()
        for bad in (foreign, copy.copy(token), Derived(token._instance), object(), None):
            self.assert_code("invalid_reservation", self.gate.finish_operation, bad)
            self.assertEqual(self.gate.snapshot(), before)
        self.gate.finish_operation(token)
        self.assert_code("invalid_reservation", self.gate.finish_operation, token)

    def test_operation_capacity_is_bounded_before_admission(self):
        for _ in range(a.MAX_OPERATIONS):
            self.gate.reserve_operation()
        self.assert_code("admission_capacity", self.gate.reserve_operation, kind="approval")
        self.assertEqual(self.gate.snapshot()["counts"]["operations"], a.MAX_OPERATIONS)

    def test_starting_running_and_stopping_remain_busy_until_exact_completion(self):
        token = self.turn()
        self.assertEqual(self.gate.snapshot()["counts"]["starting_turns"], 1)
        self.assert_busy_open()
        self.assertTrue(self.gate.bind_turn(token, turn_id="turn"))
        self.assertEqual(self.gate.snapshot()["counts"]["running_turns"], 1)
        self.assert_busy_open()
        self.assertTrue(self.event("turn_stopping"))
        self.assertFalse(self.event("turn_started"))
        self.assertEqual(self.gate.snapshot()["counts"]["stopping_turns"], 1)
        self.assert_busy_open()
        self.assertTrue(self.event("turn_completed"))
        self.assertTrue(self.gate.snapshot()["idle_observed"])

    def test_concurrent_turns_for_same_thread_or_workspace_are_rejected(self):
        self.turn()
        before = self.gate.snapshot()
        self.assert_code("turn_already_active", self.turn, workspace="other")
        self.assert_code("workspace_already_active", self.turn, app="other-app", thread="other-thread")
        self.assertEqual(self.gate.snapshot(), before)
        self.turn(app="other-app", thread="other-thread", workspace="other-workspace")
        self.assertEqual(self.gate.snapshot()["counts"]["starting_turns"], 2)

    def test_empty_workspace_key_does_not_combine_unrelated_workspaces(self):
        self.turn(workspace="")
        self.turn(thread="other-thread", workspace="")
        self.assertEqual(self.gate.snapshot()["counts"]["starting_turns"], 2)

    def test_turn_identity_inputs_are_exact_bounded_strings(self):
        for value in ("", "secret" * (a.MAX_IDENTITY_CHARS + 1), None, 1, _Poison()):
            self.assert_code("invalid_activity_identity", self.gate.reserve_turn,
                             app_key=value, thread_id="thread")
            self.assert_code("invalid_activity_identity", self.gate.reserve_turn,
                             app_key="app", thread_id=value)
        for value in (None, 1, _Poison()):
            self.assert_code("invalid_activity_identity", self.gate.reserve_turn,
                             app_key="app", thread_id="thread", workspace_key=value)
        self.assertEqual(self.gate.snapshot()["counts"]["starting_turns"], 0)

    def test_completion_before_start_reply_is_retained_and_cannot_be_revived(self):
        outer = self.gate.reserve_operation()
        token = self.turn()
        self.assertTrue(self.event("turn_started"))
        self.assertTrue(self.event("turn_completed"))
        self.assertEqual(self.gate.snapshot()["counts"]["starting_turns"], 1)
        self.assert_busy_open()
        self.assertFalse(self.gate.bind_turn(token, turn_id="turn"))
        self.assertEqual(self.gate.snapshot()["counts"]["running_turns"], 0)
        self.assert_busy_open()  # Caller has not finished its remaining work.
        self.gate.finish_operation(outer)
        self.assertTrue(self.gate.snapshot()["idle_observed"])

    def test_completion_can_precede_started_event_and_start_reply(self):
        token = self.turn()
        self.assertTrue(self.event("turn_completed"))
        self.assertFalse(self.event("turn_started"))
        self.assertFalse(self.event("turn_completed"))
        self.assertFalse(self.gate.bind_turn(token, turn_id="turn"))
        self.assertTrue(self.gate.snapshot()["idle_observed"])

    def test_stop_before_start_reply_is_preserved(self):
        token = self.turn()
        self.assertTrue(self.event("turn_stopping"))
        self.assertFalse(self.event("turn_started"))
        self.assertTrue(self.gate.bind_turn(token, turn_id="turn"))
        self.assertEqual(self.gate.snapshot()["counts"]["stopping_turns"], 1)

    def test_mismatched_start_reply_never_consumes_an_early_completion(self):
        token = self.turn()
        self.event("turn_completed", turn="observed-turn")
        self.assert_code("turn_identity_mismatch", self.gate.bind_turn, token, turn_id="different-turn")
        self.assertEqual(self.gate.snapshot()["counts"]["starting_turns"], 1)
        self.gate.fail_turn(token)
        self.assertEqual(self.gate.snapshot()["counts"]["unknown_turns"], 1)
        self.assert_busy_open()

    def test_conflicting_early_event_latches_unknown_without_releasing_start(self):
        token = self.turn()
        self.event("turn_started", turn="first")
        self.assertFalse(self.event("turn_completed", turn="other"))
        self.assertIn("unexpected_event", self.gate.snapshot()["unknown_reasons"])
        self.assertTrue(self.gate.bind_turn(token, turn_id="first"))
        self.event("turn_completed", turn="first")
        self.assert_busy_open()  # Exact completion cannot erase the conflict.

    def test_uncertain_start_keeps_thread_and_workspace_reserved(self):
        token = self.turn()
        self.gate.fail_turn(token)
        self.gate.fail_turn(token)
        self.assertEqual(self.gate.snapshot()["counts"]["unknown_turns"], 1)
        self.assert_code("turn_already_active", self.turn, workspace="other")
        self.assert_code("workspace_already_active", self.turn, thread="other")
        self.assertFalse(self.event("turn_completed", turn="unconfirmed-turn"))
        self.assertEqual(self.gate.snapshot()["counts"]["unknown_turns"], 1)
        self.turn(app="another-app", thread="another-thread", workspace="another-workspace")
        self.assert_busy_open()

    def test_known_terminal_may_retire_failed_turn_but_unknown_is_sticky(self):
        token = self.turn()
        self.gate.bind_turn(token, turn_id="turn")
        self.gate.fail_turn(token)
        self.assertTrue(self.event("turn_completed"))
        self.assertEqual(self.gate.snapshot()["counts"]["unknown_turns"], 0)
        self.assert_busy_open()

    def test_exact_app_instance_and_turn_are_required_for_terminal_release(self):
        token = self.turn()
        self.gate.bind_turn(token, turn_id="turn")
        for change in ({"app": "another-instance"}, {"thread": "another-thread"}, {"turn": "another-turn"}):
            self.assertFalse(self.event("turn_completed", **change))
            self.assertEqual(self.gate.snapshot()["counts"]["running_turns"], 1)
        self.assertTrue(self.event("turn_completed"))
        self.assert_busy_open()

    def test_completed_identity_duplicates_cannot_overwrite_newer_turn(self):
        token = self.turn()
        self.gate.bind_turn(token, turn_id="old-turn")
        self.event("turn_completed", turn="old-turn")
        new = self.turn()
        before = self.gate.snapshot()
        for method in ("turn_started", "turn_completed", "turn_stopping"):
            self.assertFalse(self.event(method, turn="old-turn"))
            self.assertEqual(self.gate.snapshot(), before)
        self.assertTrue(self.gate.bind_turn(new, turn_id="new-turn"))
        self.assertTrue(self.event("turn_completed", turn="new-turn"))
        self.assertTrue(self.gate.snapshot()["idle_observed"])

    def test_started_and_stopping_duplicates_do_not_undo_state(self):
        token = self.turn()
        self.gate.bind_turn(token, turn_id="turn")
        self.assertTrue(self.event("turn_started"))
        self.assertFalse(self.event("turn_started"))
        self.assertTrue(self.event("turn_stopping"))
        self.assertFalse(self.event("turn_stopping"))
        self.assertFalse(self.event("turn_started"))
        self.assertEqual(self.gate.snapshot()["counts"]["stopping_turns"], 1)

    def test_reusing_a_retired_turn_identity_is_not_a_new_start(self):
        token = self.turn()
        self.gate.bind_turn(token, turn_id="turn")
        self.event("turn_completed")
        new = self.turn()
        self.assert_code("turn_identity_mismatch", self.gate.bind_turn, new, turn_id="turn")
        self.assert_busy_open()

    def test_turn_tokens_are_exact_objects_and_binding_is_one_use(self):
        token = self.turn()
        other = a.ControllerAdmission()
        other.claim_controller()
        foreign = other.reserve_turn(app_key="a", thread_id="t")
        for bad in (copy.copy(token), foreign, None, object()):
            self.assert_code("invalid_reservation", self.gate.bind_turn, bad, turn_id="turn")
            self.assert_code("invalid_reservation", self.gate.fail_turn, bad)
        self.gate.bind_turn(token, turn_id="turn")
        self.assert_code("invalid_turn_transition", self.gate.bind_turn, token, turn_id="turn")

    def test_turn_capacity_is_bounded_before_start(self):
        for number in range(a.MAX_TURNS):
            self.turn(thread=str(number), workspace="")
        self.assert_code("admission_capacity", self.turn, thread="extra", workspace="")
        self.assertEqual(self.gate.snapshot()["counts"]["starting_turns"], a.MAX_TURNS)

    def test_completed_history_capacity_rejects_before_launch_and_preserves_idle(self):
        for number in range(a.MAX_COMPLETED_TURNS):
            token = self.turn()
            self.gate.bind_turn(token, turn_id=str(number))
            self.event("turn_completed", turn=str(number))
        self.assertEqual(len(self.gate._completed), a.MAX_COMPLETED_TURNS)
        before = self.gate.snapshot()
        self.assert_code("admission_capacity", self.turn)
        self.assertEqual(self.gate.snapshot(), before)
        self.assertEqual(before["unknown_reasons"], [])
        self.assertTrue(before["idle_observed"])
        self.assertFalse(self.event("turn_completed", turn="0"))
        lease = self.gate.try_enter_maintenance()
        self.gate.leave_maintenance(lease)

    def test_each_admitted_turn_has_reserved_terminal_history_capacity(self):
        for number in range(a.MAX_COMPLETED_TURNS - 2):
            token = self.turn()
            self.gate.bind_turn(token, turn_id=str(number))
            self.event("turn_completed", turn=str(number))
        first = self.turn(thread="first", workspace="first-workspace")
        second = self.turn(thread="second", workspace="second-workspace")
        before = self.gate.snapshot()
        self.assert_code("admission_capacity", self.turn, thread="extra", workspace="extra-workspace")
        self.assertEqual(self.gate.snapshot(), before)
        for token, thread in ((first, "first"), (second, "second")):
            self.gate.bind_turn(token, turn_id="last-turn")
            self.event("turn_completed", thread=thread, turn="last-turn")
        self.assertTrue(self.gate.snapshot()["idle_observed"])
        self.assertEqual(self.gate.snapshot()["unknown_reasons"], [])

    def test_unknown_and_invalid_event_values_never_reach_diagnostics(self):
        self.gate.mark_unknown("credential=do-not-echo")
        self.gate.mark_unknown(_Poison())
        self.assert_code("invalid_activity_identity", self.gate.turn_completed,
                         app_key="app", thread_id="thread", turn_id=_Poison())
        self.assertEqual(self.gate.snapshot()["unknown_reasons"], ["unexpected_event", "unknown_activity"])
        self.assertNotIn("credential", json.dumps(self.gate.snapshot()))
        self.assertEqual(str(a.AdmissionError(_Poison())), "admission_failed")

    def test_fixed_recovery_and_transport_reasons_are_supported(self):
        reasons = {"recovered_state_unverified", "transport_lost", "unexpected_event",
                   "untracked_request", "operation_outcome_unknown", "app_creation_failed"}
        for reason in reasons:
            self.gate.mark_unknown(reason)
        self.assertEqual(set(self.gate.snapshot()["unknown_reasons"]), reasons)
        token = self.gate.reserve_operation(kind="approval")
        self.gate.finish_operation(token)
        self.assert_busy_open()

    def test_diagnostics_never_include_workspace_or_worker_identities(self):
        token = self.turn(app="private-app-instance", thread="private-thread", workspace="/private/workspace")
        self.gate.bind_turn(token, turn_id="private-turn")
        rendered = json.dumps(self.gate.snapshot())
        for forbidden in ("private-app", "private-thread", "/private/workspace", "private-turn"):
            self.assertNotIn(forbidden, rendered)

    def test_maintenance_denies_new_work_and_nested_leases_before_side_effects(self):
        lease = self.gate.try_enter_maintenance()
        before = self.gate.snapshot()
        for kind in ("operation", "approval", "callback"):
            self.assert_code("maintenance_active", self.gate.reserve_operation, kind=kind)
        self.assert_code("maintenance_active", self.turn)
        self.assert_code("maintenance_busy", self.gate.try_enter_maintenance)
        self.assertEqual(self.gate.snapshot(), before)
        self.gate.leave_maintenance(lease)
        self.assertTrue(self.gate.snapshot()["admission_open"])

    def test_foreign_copied_derived_stale_and_duplicate_leases_cannot_reopen(self):
        other = a.ControllerAdmission()
        other.claim_controller()
        foreign = other.try_enter_maintenance()
        first = self.gate.try_enter_maintenance()

        class Derived(a.MaintenanceLease):
            pass

        before = self.gate.snapshot()
        for bad in (foreign, copy.copy(first), Derived(first._instance, first._generation, first._pid), None, object()):
            self.assert_code("invalid_maintenance_lease", self.gate.leave_maintenance, bad)
            self.assertEqual(self.gate.snapshot(), before)
        self.gate.leave_maintenance(first)
        second = self.gate.try_enter_maintenance()
        self.assertGreater(second._generation, first._generation)
        before = self.gate.snapshot()
        self.assert_code("invalid_maintenance_lease", self.gate.leave_maintenance, first)
        self.assertEqual(self.gate.snapshot(), before)
        self.gate.leave_maintenance(second)
        self.assert_code("invalid_maintenance_lease", self.gate.leave_maintenance, second)
        self.assertTrue(self.gate.snapshot()["admission_open"])

    def test_unknown_while_maintenance_held_is_not_idle_and_survives_release(self):
        lease = self.gate.try_enter_maintenance()
        self.gate.mark_unknown("transport_lost")
        snapshot = self.gate.snapshot()
        self.assertTrue(snapshot["maintenance_held"])
        self.assertFalse(snapshot["admission_open"])
        self.assertFalse(snapshot["idle_observed"])
        self.assertEqual(snapshot["state"], "unknown")
        self.gate.leave_maintenance(lease)
        token = self.gate.reserve_operation(kind="approval")
        self.gate.finish_operation(token)
        self.assert_busy_open()

    def test_unmatched_callback_event_during_lease_latches_unknown(self):
        lease = self.gate.try_enter_maintenance()
        self.assertFalse(self.event("turn_completed"))
        self.assertFalse(self.gate.snapshot()["idle_observed"])
        self.assertFalse(self.gate.snapshot()["admission_open"])
        self.gate.leave_maintenance(lease)
        self.assert_busy_open()

    def test_shutdown_is_permanent_and_preserves_outstanding_evidence(self):
        operation = self.gate.reserve_operation()
        token = self.turn()
        self.gate.bind_turn(token, turn_id="turn")
        before = self.gate.snapshot()["counts"]
        self.gate.close()
        self.gate.close()
        self.assertEqual(self.gate.snapshot()["counts"], before)
        self.assert_code("admission_closed", self.gate.reserve_operation)
        self.assert_code("admission_closed", self.turn, thread="other", workspace="other")
        self.assert_code("admission_closed", self.gate.try_enter_maintenance)
        self.gate.finish_operation(operation)
        self.event("turn_completed")
        self.assertEqual(self.gate.snapshot()["state"], "closed")
        self.assertFalse(self.gate.snapshot()["idle_observed"])
        self.assertFalse(self.gate.snapshot()["admission_open"])

    def test_shutdown_invalidates_current_lease_without_reopening(self):
        lease = self.gate.try_enter_maintenance()
        self.gate.close()
        before = self.gate.snapshot()
        self.assert_code("invalid_maintenance_lease", self.gate.leave_maintenance, lease)
        self.assertEqual(self.gate.snapshot(), before)

    def test_start_reply_after_shutdown_preserves_closed_unknown_state(self):
        token = self.turn()
        self.gate.close()
        self.assertFalse(self.gate.bind_turn(token, turn_id="turn"))
        self.assertEqual(self.gate.snapshot()["counts"]["unknown_turns"], 1)
        self.assertIn("operation_outcome_unknown", self.gate.snapshot()["unknown_reasons"])
        self.assertFalse(self.gate.snapshot()["admission_open"])
        self.gate.fail_turn(token)  # Cleanup may also report the uncertain outcome.
        self.event("turn_completed")
        self.assertEqual(self.gate.snapshot()["state"], "closed")
        self.assertFalse(self.gate.snapshot()["idle_observed"])

    def test_early_completion_and_late_start_reply_after_shutdown_are_safe(self):
        token = self.turn()
        self.event("turn_completed")
        self.gate.close()
        self.assertFalse(self.gate.bind_turn(token, turn_id="turn"))
        self.assertEqual(self.gate.snapshot()["counts"]["starting_turns"], 0)
        self.assertEqual(self.gate.snapshot()["state"], "closed")
        self.assertIn("operation_outcome_unknown", self.gate.snapshot()["unknown_reasons"])

    def test_atomic_launch_and_maintenance_race_has_exactly_one_winner(self):
        start = threading.Barrier(3)
        attempted = threading.Barrier(3)
        release = threading.Event()
        results = {}

        def contender(name):
            held = None
            try:
                start.wait(timeout=3)
                try:
                    held = self.gate.reserve_operation() if name == "work" else self.gate.try_enter_maintenance()
                    results[name] = "reserved"
                except a.AdmissionError as exc:
                    results[name] = exc.code
                attempted.wait(timeout=3)
                release.wait(timeout=3)
            finally:
                if held is not None:
                    if name == "work":
                        self.gate.finish_operation(held)
                    else:
                        self.gate.leave_maintenance(held)

        threads = [threading.Thread(target=contender, args=(name,)) for name in ("work", "maintenance")]
        for thread in threads:
            thread.start()
        try:
            start.wait(timeout=3)
            attempted.wait(timeout=3)
            self.assertEqual(list(results.values()).count("reserved"), 1)
            self.assertIn(set(results.values()), ({"reserved", "maintenance_active"}, {"reserved", "maintenance_busy"}))
        finally:
            release.set()
            for thread in threads:
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
        self.assertTrue(self.gate.snapshot()["idle_observed"])

    def test_foreign_pid_is_rejected_before_any_mutex_for_all_public_operations(self):
        operation = self.gate.reserve_operation()
        turn = self.turn()

        class ForbiddenLock:
            def __enter__(self):
                raise AssertionError("forked process acquired inherited mutex")

            def __exit__(self, *_args):
                pass

        methods = [
            lambda: self.gate.claim_controller(), lambda: self.gate.snapshot(),
            lambda: self.gate.reserve_operation(), lambda: self.gate.finish_operation(operation),
            lambda: self.turn(), lambda: self.gate.bind_turn(turn, turn_id="turn"),
            lambda: self.gate.fail_turn(turn), lambda: self.event("turn_started"),
            lambda: self.event("turn_completed"), lambda: self.event("turn_stopping"),
            lambda: self.gate.mark_unknown("transport_lost"),
            lambda: self.gate.try_enter_maintenance(), lambda: self.gate.leave_maintenance(None),
            lambda: self.gate.close(),
        ]
        with mock.patch.object(self.gate, "_lock", ForbiddenLock()), mock.patch.object(a.os, "getpid", return_value=os.getpid() + 1):
            for method in methods:
                self.assert_code("admission_wrong_process", method)

    @unittest.skipUnless(hasattr(os, "fork"), "Native POSIX fork regression")
    def test_forked_copy_cannot_claim_parent_lease_or_snapshot(self):
        lease = self.gate.try_enter_maintenance()
        read_fd, write_fd = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(read_fd)
            try:
                codes = []
                for method in (self.gate.snapshot, lambda: self.gate.leave_maintenance(lease)):
                    try:
                        method()
                    except a.AdmissionError as exc:
                        codes.append(exc.code)
                os.write(write_fd, json.dumps(codes).encode())
                os._exit(0)
            except BaseException:
                os._exit(1)
        os.close(write_fd)
        try:
            ready, _, _ = select.select([read_fd], [], [], 3)
            self.assertTrue(ready, "owned fork child did not return bounded evidence")
            self.assertEqual(json.loads(os.read(read_fd, 1024)), ["admission_wrong_process"] * 2)
        finally:
            os.close(read_fd)
            pid, status = os.waitpid(child, os.WNOHANG)
            if pid == 0:
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)
            else:
                self.assertEqual(status, 0)
            self.gate.leave_maintenance(lease)


if __name__ == "__main__":
    unittest.main()
