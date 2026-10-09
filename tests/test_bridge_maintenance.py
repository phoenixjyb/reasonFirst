from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from gitlab_agent import bridge_mcp
from gitlab_agent.bridge_preview import controller as c
from gitlab_agent.bridge_preview.admission import ControllerAdmission
from gitlab_agent.bridge_preview.bridge_config import ExecutionTarget
from gitlab_agent.bridge_preview.remote_workspace import RemoteWorkspaceError
from gitlab_agent.upgrade.startup_state import create_disposable_configuration
from gitlab_agent.worker_policy import default_worker_policy


class FakeApp:
    instances = []

    def __init__(self, **kwargs):
        self.backend_name = "standalone-local"
        self.event_handler = kwargs["event_handler"]
        self.server_request_tracker = kwargs.get("server_request_tracker")
        self.transport_lost_handler = kwargs.get("transport_lost_handler")
        self.approval_request_handler = kwargs.get("approval_request_handler")
        self.server_request_handler = kwargs.get("server_request_handler")
        self.start_hook = None
        self.thread_count = 0
        self.turn_count = 0
        self.interrupts = []
        self.closed = False
        self.instances.append(self)

    def start_thread(self, **kwargs):
        self.thread_count += 1
        return f"thread-{self.thread_count}"

    def start_turn(self, **kwargs):
        self.turn_count += 1
        turn_id = f"turn-{self.turn_count}"
        if self.start_hook is not None:
            self.start_hook(kwargs["thread_id"], turn_id)
        return turn_id

    def worker_policy_evidence(self, thread_id):
        return {"satisfied": True}

    def resume_thread(self, thread_id, **kwargs):
        return {"satisfied": True}

    def set_thread_name(self, *args):
        pass

    def set_thread_goal(self, *args):
        return {}

    def update_thread_metadata(self, *args, **kwargs):
        return {}

    def read_thread(self, *args, **kwargs):
        return {}

    def interrupt(self, **kwargs):
        self.interrupts.append(kwargs)

    def emit(self, method, thread_id, turn_id, status="completed"):
        self.event_handler({
            "method": method,
            "params": {"threadId": thread_id, "turn": {"id": turn_id, "status": status}},
        })

    def close(self):
        self.closed = True


class BridgeMaintenanceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="rf-maintenance-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.fixture = create_disposable_configuration(self.root / "fixture")
        self.fixture.snapshot.state_dir.mkdir()
        self.target = ExecutionTarget(codex_backend="standalone-local")
        self.policy = default_worker_policy("codex")
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        FakeApp.instances = []
        self.patch(c, "_private_state_dir", return_value=self.fixture.snapshot.state_dir)
        self.patch(c, "load_bridge_config", return_value=self.fixture.snapshot.bridge_configuration())
        self.patch(c, "AppServerClient", FakeApp)
        self.command = self.patch(c, "_run_json", side_effect=self.run_json)
        self.ctrl = c.BridgeController(admission=ControllerAdmission())
        self.addCleanup(self.ctrl.close)
        self.patch(self.ctrl, "_effective_codex_policy", return_value=self.policy)
        self.patch(self.ctrl, "_assert_worktree_allowed", return_value=str(self.worktree))
        self.ctrl._state["workspaces"]["workspace"] = {
            "workspace_id": "workspace", "kind": "local", "target": self.target.to_dict(),
            "project": "synthetic/project", "task": "task", "worktree_path": str(self.worktree),
        }

    def patch(self, target, name, *args, **kwargs):
        patcher = patch.object(target, name, *args, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def run_json(self, argv, **kwargs):
        if "config" in argv:
            return {"gitlab_base_url": "https://example.invalid"}
        if "status" in argv:
            return {"worktree_path": str(self.worktree), "branch": "task", "head": "synthetic"}
        if "resume" in argv:
            return {"agent_prompt": "synthetic task"}
        raise AssertionError(f"Unexpected external command: {argv}")

    def app(self):
        return self.ctrl._get_app(self.target, cwd=str(self.worktree))[1]

    def start(self):
        return self.ctrl.start_codex(workspace_id="workspace", goal="synthetic task")

    def thread(self, fn):
        result, errors = [], []
        def run():
            try:
                result.append(fn())
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.addCleanup(lambda: thread.join(5))
        return thread, result, errors

    def wait(self, event):
        self.assertTrue(event.wait(5), "deterministic interleaving did not arrive")

    def busy(self):
        with self.assertRaisesRegex(c.BridgeError, "maintenance_busy"):
            self.ctrl.try_enter_maintenance()
        self.assertTrue(self.ctrl.maintenance_snapshot()["admission_open"])

    def remote_validation_fixture(self):
        raw_target = {
            "type": "ssh", "host": "example.invalid", "repo": "/synthetic/project",
            "codex_backend": "standalone-local",
            "validation": {
                "engine": "docker", "image": "synthetic-validation",
                "allowed_executables": ["python"],
            },
        }
        self.target = self.ctrl._target_from_dict(raw_target)
        rec = self.ctrl._state["workspaces"]["workspace"]
        rec.update({
            "kind": "ssh", "target": raw_target, "base_sha": "synthetic-base",
            "branch": "chatgpt/task",
            "project_config": {"found": True, "effective": {"validation_commands": [{
                "name": "unit", "argv": ["python", "-m", "unittest"],
                "required": True, "timeout_seconds": 1,
            }]}},
        })
        manager = Mock(spec=c.RemoteWorkspaceManager)
        manager.status.return_value = {"dirty": True, "commits_ahead_of_base": 0}
        manager.changed_paths.return_value = ["test.py"]
        manager.reviewability.return_value = {"ok": True}
        manager.security_diff.return_value = "synthetic diff"
        manager.history_secret_scan.return_value = {"coverage_complete": True, "findings": []}
        manager.snapshot.return_value = {"branch": "chatgpt/task", "digest": "synthetic"}
        self.patch(self.ctrl, "_remote_manager", return_value=manager)
        self.patch(self.ctrl, "_settings_for_operation", return_value=self.fixture.snapshot.settings)
        evaluator = self.patch(c, "evaluate_review_gates", return_value={
            "blockers": ["validation failed"], "warnings": [],
            "protected_paths": [], "protected_path_changes": [],
            "secret_scan": {"findings": []}, "review_diff": {"diff": "synthetic diff"},
        })
        return rec, manager, evaluator

    def assert_unknown_without_active_reservations(self):
        snapshot = self.ctrl.maintenance_snapshot()
        self.assertTrue(all(count == 0 for count in snapshot["counts"].values()))
        self.assertEqual(snapshot["unknown_reasons"], ["operation_outcome_unknown"])
        self.assertFalse(snapshot["idle_observed"])
        self.busy()

    def test_maintenance_wins_before_any_controller_side_effect(self):
        self.command.reset_mock()
        lease = self.ctrl.try_enter_maintenance()
        calls = [
            self.start,
            lambda: self.ctrl.prepare(project="synthetic/project"),
            lambda: self.ctrl._get_app(self.target),
            lambda: self.ctrl.continue_task(thread_id="missing", goal="task"),
            lambda: self.ctrl.status(thread_id="missing"),
            lambda: self.ctrl.compact_status(thread_id="missing"),
            lambda: self.ctrl.finish(thread_id="missing"),
            lambda: self.ctrl.resolve_approval(thread_id="missing", request_id=1, approve=True),
            lambda: self.ctrl._handle_dynamic_tool_request("missing", {"id": 1}),
            lambda: self.ctrl._handle_app_approval_request("missing", {"id": 1}),
            lambda: self.ctrl.artifact_descriptor(path="missing", workspace_id="workspace"),
            self.ctrl.doctor,
        ]
        for call in calls:
            with self.subTest(call=call), self.assertRaisesRegex(c.BridgeError, "maintenance_active"):
                call()
        self.command.assert_not_called()
        self.assertEqual(FakeApp.instances, [])
        self.assertFalse(self.ctrl.state_file.exists())
        self.ctrl.leave_maintenance(lease)

    def test_packaged_mcp_tools_obey_controller_gate_and_catalog_stays_unchanged(self):
        async def check():
            ordinary = c.BridgeController()
            self.addCleanup(ordinary.close)
            for mode in (True, False):
                server = bridge_mcp.build_server(read_only_mode=mode, controller=self.ctrl)
                reference = bridge_mcp.build_server(read_only_mode=mode, controller=ordinary)
                actual = await server.list_tools()
                expected = await reference.list_tools()
                self.assertEqual(
                    [t.model_dump(mode="json") for t in actual],
                    [t.model_dump(mode="json") for t in expected],
                )
                for tool in actual:
                    schema = tool.input_schema
                    args = {
                        name: (1 if schema["properties"][name].get("type") == "integer" else "synthetic")
                        for name in schema.get("required", [])
                    }
                    with self.subTest(mode=mode, tool=tool.name):
                        with self.assertRaises(Exception) as raised:
                            await server.call_tool(tool.name, args)
                        cause = raised.exception
                        while cause.__cause__ is not None:
                            cause = cause.__cause__
                        self.assertIsInstance(cause, c.BridgeError)
                        self.assertEqual(str(cause), "maintenance_active")
        lease = self.ctrl.try_enter_maintenance()
        self.command.reset_mock()
        asyncio.run(check())
        self.command.assert_not_called()
        self.assertEqual(FakeApp.instances, [])
        self.ctrl.leave_maintenance(lease)

    def test_inflight_start_blocks_maintenance_before_rpc_returns(self):
        app = self.app()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def hook(thread_id, turn_id):
            entered.set()
            self.wait(release)
        app.start_hook = hook
        thread, result, errors = self.thread(self.start)
        self.wait(entered)
        self.assertEqual(self.ctrl.maintenance_snapshot()["counts"]["starting_turns"], 1)
        self.busy()
        release.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.ctrl.maintenance_snapshot()["counts"]["running_turns"], 1)
        self.busy()
        app.emit("turn/completed", result[0]["thread_id"], result[0]["turn_id"])
        self.assertTrue(self.ctrl.maintenance_snapshot()["idle_observed"])

    def test_completion_before_start_response_preserves_terminal_state(self):
        app = self.app()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def hook(thread_id, turn_id):
            app.emit("turn/completed", thread_id, turn_id)
            entered.set()
            self.wait(release)
        app.start_hook = hook
        thread, result, errors = self.thread(self.start)
        self.wait(entered)
        self.busy()
        release.set()
        thread.join(5)
        self.assertEqual(errors, [])
        started = result[0]
        self.assertEqual(self.ctrl._session(started["thread_id"])["last_turn_status"], "completed")
        app.emit("turn/started", started["thread_id"], started["turn_id"], "inProgress")
        self.assertEqual(self.ctrl._session(started["thread_id"])["last_turn_status"], "completed")
        lease = self.ctrl.try_enter_maintenance()
        self.ctrl.leave_maintenance(lease)

    def test_continue_tracks_new_turn_and_ignores_prior_terminal_duplicate(self):
        first = self.start()
        app = self.app()
        app.emit("turn/completed", first["thread_id"], first["turn_id"])
        second = self.ctrl.continue_task(thread_id=first["thread_id"], goal="second task")
        app.emit("turn/completed", first["thread_id"], first["turn_id"])
        session = self.ctrl._session(first["thread_id"])
        self.assertEqual(session["last_turn_id"], second["turn_id"])
        self.assertEqual(session["last_turn_status"], "inProgress")
        self.busy()
        app.emit("turn/completed", second["thread_id"], second["turn_id"])
        self.assertTrue(self.ctrl.maintenance_snapshot()["idle_observed"])

    def test_interrupt_ack_does_not_release_running_turn(self):
        started = self.start()
        app = self.app()
        self.ctrl.interrupt(thread_id=started["thread_id"])
        self.assertEqual(len(app.interrupts), 1)
        self.assertEqual(self.ctrl.maintenance_snapshot()["counts"]["stopping_turns"], 1)
        self.busy()
        app.emit("turn/completed", started["thread_id"], started["turn_id"], "interrupted")
        self.assertTrue(self.ctrl.maintenance_snapshot()["idle_observed"])

    def test_late_start_response_cannot_overwrite_a_newer_continuation(self):
        app = self.app()
        bound, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = self.ctrl._start_tracked_turn
        def delayed(*args, **kwargs):
            result = original(*args, **kwargs)
            if result[0] == "turn-1":
                bound.set()
                self.wait(release)
            return result
        self.patch(self.ctrl, "_start_tracked_turn", side_effect=delayed)
        thread, results, errors = self.thread(self.start)
        self.wait(bound)
        app.emit("turn/completed", "thread-1", "turn-1")
        second = self.ctrl.continue_task(thread_id="thread-1", goal="next task")
        release.set()
        thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(results[0]["turn_id"], "turn-1")
        self.assertEqual(self.ctrl._session("thread-1")["last_turn_id"], second["turn_id"])
        self.ctrl.interrupt(thread_id="thread-1")
        self.assertEqual(app.interrupts[-1]["turn_id"], second["turn_id"])
        self.assertEqual(self.ctrl.maintenance_snapshot()["counts"]["stopping_turns"], 1)
        app.emit("turn/completed", "thread-1", second["turn_id"], "interrupted")
        self.assertTrue(self.ctrl.maintenance_snapshot()["idle_observed"])

    def test_ambiguous_start_keeps_unknown_and_normal_observation_available(self):
        app = self.app()
        def hook(thread_id, turn_id):
            raise c.AppServerError("synthetic RPC timeout")
        app.start_hook = hook
        with self.assertRaises(c.AppServerError):
            self.start()
        snapshot = self.ctrl.maintenance_snapshot()
        self.assertEqual(snapshot["counts"]["unknown_turns"], 1)
        self.assertIn("start_outcome_unknown", snapshot["unknown_reasons"])
        self.busy()
        self.assertEqual(self.ctrl.pending_approvals(thread_id="thread-1")["count"], 0)

    def test_unrelated_completion_cannot_release_current_turn(self):
        started = self.start()
        self.app().emit("turn/completed", started["thread_id"], "unrelated-turn")
        self.assertEqual(self.ctrl.maintenance_snapshot()["counts"]["running_turns"], 1)
        self.busy()
        self.app().emit("turn/completed", started["thread_id"], started["turn_id"])
        self.assertFalse(self.ctrl.maintenance_snapshot()["idle_observed"])

    def test_pending_approval_and_returned_response_block_until_wire_finalization(self):
        app_key, app = self.ctrl._get_app(self.target)
        self.ctrl._state["sessions"]["approval-thread"] = {
            "thread_id": "approval-thread", "app_key": app_key,
            "workspace_id": "workspace", "pending_approvals": {},
        }
        pending = threading.Event()
        save = self.ctrl._save_state
        def save_state():
            save()
            if self.ctrl._approval_waiters:
                pending.set()
        self.patch(self.ctrl, "_save_state", side_effect=save_state)
        request = {
            "id": 7, "method": "item/commandExecution/requestApproval",
            "params": {"threadId": "approval-thread", "availableDecisions": ["accept", "decline"]},
        }
        finish = app.server_request_tracker(request)
        thread, results, errors = self.thread(lambda: app.approval_request_handler(request))
        self.wait(pending)
        self.busy()
        self.ctrl.resolve_approval(thread_id="approval-thread", request_id=7, approve=False)
        thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(results, [{"decision": "decline"}])
        self.assertEqual(self.ctrl.pending_approvals(thread_id="approval-thread")["count"], 0)
        self.assertEqual(self.ctrl.maintenance_snapshot()["counts"]["approvals"], 1)
        self.busy()
        finish(True)
        self.assertTrue(self.ctrl.maintenance_snapshot()["idle_observed"])

    def test_unsent_callback_response_remains_unknown(self):
        finish = self.app().server_request_tracker({"id": 8, "method": "item/tool/call"})
        self.busy()
        finish(False)
        self.assertIn("callback_response_unsent", self.ctrl.maintenance_snapshot()["unknown_reasons"])
        self.busy()

    def test_dynamic_remote_validation_timeout_remains_unknown_after_parent_completion(self):
        rec, manager, _ = self.remote_validation_fixture()
        started = self.start()
        app = self.app()
        result = {"timed_out": True, "returncode": None, "stdout": "", "stderr": ""}
        manager.run_argv.return_value = result
        request = {
            "id": 8, "method": "item/tool/call",
            "params": {
                "threadId": started["thread_id"], "namespace": "reasonfirst_remote", "tool": "run",
                "arguments": {"argv": ["python", "-m", "unittest"], "timeout_seconds": 1},
            },
        }
        finish = app.server_request_tracker(request)
        response = app.server_request_handler(request)
        self.assertTrue(response["success"])
        self.assertEqual(json.loads(response["contentItems"][0]["text"]), result)
        self.assertEqual(self.ctrl.maintenance_snapshot()["counts"]["callbacks"], 1)
        finish(True)
        app.emit("turn/completed", started["thread_id"], started["turn_id"])
        manager.run_argv.assert_called_once_with(
            rec, ["python", "-m", "unittest"], cwd=".", timeout_seconds=1,
        )
        self.assert_unknown_without_active_reservations()

    def test_remote_finish_plan_returned_validation_timeout_remains_unknown(self):
        rec, manager, evaluator = self.remote_validation_fixture()
        started = self.start()
        self.app().emit("turn/completed", started["thread_id"], started["turn_id"])
        self.assertTrue(self.ctrl.maintenance_snapshot()["idle_observed"])
        result = {"timed_out": True, "returncode": None, "stdout": "", "stderr": ""}
        manager.run_argv.return_value = result
        report = self.ctrl.finish_preview(thread_id=started["thread_id"], message="Synthetic review")
        self.assertFalse(report["ok"])
        self.assertTrue(report["dry_run"])
        self.assertTrue(report["remote"])
        self.assertEqual(report["blockers"], ["validation failed"])
        self.assertEqual(report["validations"], [{
            "name": "unit", "argv": ["python", "-m", "unittest"],
            "required": True, "passed": False, "blocking": True, "result": result,
        }])
        self.assertEqual(evaluator.call_args.kwargs["validations"], report["validations"])
        manager.run_argv.assert_called_once_with(rec, ["python", "-m", "unittest"], timeout_seconds=1)
        self.assert_unknown_without_active_reservations()

    def test_remote_finish_plan_caught_validation_error_remains_unknown(self):
        rec, manager, evaluator = self.remote_validation_fixture()
        started = self.start()
        self.app().emit("turn/completed", started["thread_id"], started["turn_id"])
        self.assertTrue(self.ctrl.maintenance_snapshot()["idle_observed"])
        manager.run_argv.side_effect = RemoteWorkspaceError("synthetic SSH timeout")
        report = self.ctrl.finish_preview(thread_id=started["thread_id"], message="Synthetic review")
        self.assertFalse(report["ok"])
        self.assertTrue(report["dry_run"])
        self.assertTrue(report["remote"])
        self.assertEqual(report["blockers"], ["validation failed"])
        self.assertEqual(report["validations"], [{
            "name": "unit", "argv": ["python", "-m", "unittest"],
            "required": True, "passed": False, "blocking": True,
            "result": {
                "argv": ["python", "-m", "unittest"], "timed_out": False,
                "returncode": None, "error": "synthetic SSH timeout",
            },
        }])
        self.assertEqual(evaluator.call_args.kwargs["validations"], report["validations"])
        manager.run_argv.assert_called_once_with(rec, ["python", "-m", "unittest"], timeout_seconds=1)
        self.assert_unknown_without_active_reservations()

    def test_transport_loss_blocks_idle_without_reloading_or_connecting(self):
        app = self.app()
        app.transport_lost_handler()
        snapshot = self.ctrl.maintenance_snapshot()
        self.assertIn("transport_lost", snapshot["unknown_reasons"])
        self.busy()

    def test_failed_app_constructor_and_stale_retry_callbacks_stay_untrusted(self):
        captured = {}
        def failed_factory(**kwargs):
            captured.update(kwargs)
            raise RuntimeError("synthetic failure after starting a reader")
        with patch.object(c, "AppServerClient", side_effect=failed_factory):
            with self.assertRaises(RuntimeError):
                self.app()
        self.assertEqual(self.ctrl._apps, {})
        self.assertIn("app_creation_failed", self.ctrl.maintenance_snapshot()["unknown_reasons"])
        self.busy()
        self.app()
        message = {"id": 9, "method": "item/tool/call", "params": {"threadId": "stale"}}
        for name in ("server_request_tracker", "server_request_handler", "approval_request_handler"):
            with self.subTest(hook=name), self.assertRaisesRegex(c.BridgeError, "untracked_app_source"):
                captured[name](message)
        self.assertEqual(self.ctrl._approval_waiters, {})
        self.assertEqual(self.ctrl.maintenance_snapshot()["counts"]["callbacks"], 0)
        self.assertFalse(self.ctrl.state_file.exists())

    def test_snapshot_and_stale_lease_never_touch_runtime_or_saved_state(self):
        lease = self.ctrl.try_enter_maintenance()
        self.ctrl.leave_maintenance(lease)
        current = self.ctrl.try_enter_maintenance()
        for name in ("_load_state", "_save_state", "_get_app", "_app_for_session"):
            self.patch(self.ctrl, name, side_effect=AssertionError("snapshot side effect"))
        with self.assertRaisesRegex(c.BridgeError, "invalid_maintenance_lease"):
            self.ctrl.leave_maintenance(lease)
        snapshot = self.ctrl.maintenance_snapshot()
        self.assertTrue(snapshot["maintenance_held"])
        self.assertFalse(snapshot["admission_open"])
        self.assertFalse(snapshot["global_idle_verified"])
        self.assertFalse(snapshot["activation_authorized"])
        self.assertFalse(self.ctrl.state_file.exists())
        self.ctrl.leave_maintenance(current)

    def test_late_event_during_maintenance_does_not_write_state(self):
        app = self.app()
        lease = self.ctrl.try_enter_maintenance()
        with patch.object(self.ctrl, "_save_state", side_effect=AssertionError("closed state write")):
            app.emit("turn/completed", "untracked-thread", "untracked-turn")
        snapshot = self.ctrl.maintenance_snapshot()
        self.assertTrue(snapshot["maintenance_held"])
        self.assertFalse(snapshot["idle_observed"])
        self.ctrl.leave_maintenance(lease)
        self.busy()

    def test_recovered_state_is_unknown_before_approval_cleanup_can_prove_idle(self):
        self.ctrl.state_file.write_text(json.dumps({
            "version": 3, "workspaces": {}, "finish_approvals": {},
            "sessions": {"old": {"pending_approvals": {"1": {"request_id": 1}}}},
        }), encoding="utf-8")
        before = self.ctrl.state_file.read_bytes()
        restarted = c.BridgeController(admission=ControllerAdmission())
        self.addCleanup(restarted.close)
        self.assertEqual(restarted._state["sessions"]["old"]["pending_approvals"], {})
        self.assertIn("recovered_state_unverified", restarted.maintenance_snapshot()["unknown_reasons"])
        with self.assertRaisesRegex(c.BridgeError, "maintenance_busy"):
            restarted.try_enter_maintenance()
        self.assertEqual(restarted.state_file.read_bytes(), before)

    def test_disposable_and_default_modes_keep_separate_admission_semantics(self):
        with self.assertRaisesRegex(c.BridgeError, "disposable_admission_conflict"):
            c.BridgeController(managed_startup=self.fixture.snapshot, admission=ControllerAdmission())
        observer = c.BridgeController(managed_startup=self.fixture.snapshot)
        self.addCleanup(observer.close)
        with self.assertRaisesRegex(c.BridgeError, "startup_observation_only"):
            observer.start_codex(workspace_id="missing", goal="task")
        with self.assertRaisesRegex(c.BridgeError, "maintenance_admission_not_enabled"):
            observer.try_enter_maintenance()
        ordinary = c.BridgeController()
        self.addCleanup(ordinary.close)
        with self.assertRaisesRegex(c.BridgeError, "Unknown bridge workspace_id"):
            ordinary.start_codex(workspace_id="missing", goal="task")

    def test_gate_cannot_be_enrolled_into_a_second_controller(self):
        with patch.object(c, "_private_state_dir", side_effect=AssertionError("second enrollment touched state")):
            with self.assertRaisesRegex(c.BridgeError, "admission_already_claimed"):
                c.BridgeController(admission=self.ctrl._admission)

    def test_close_waits_for_inflight_app_creation_and_blocks_later_creation(self):
        entered, release = threading.Event(), threading.Event()
        closed = threading.Event()
        self.addCleanup(release.set)
        def factory(**kwargs):
            entered.set()
            self.wait(release)
            return FakeApp(**kwargs)
        self.patch(c, "AppServerClient", side_effect=factory)
        thread, results, errors = self.thread(self.app)
        self.wait(entered)
        original = self.ctrl._admission.close
        def mark_closed():
            original()
            closed.set()
        self.patch(self.ctrl._admission, "close", side_effect=mark_closed)
        closer, _, close_errors = self.thread(self.ctrl.close)
        self.wait(closed)
        with self.assertRaisesRegex(c.BridgeError, "admission_closed"):
            self.app()
        release.set()
        thread.join(5)
        closer.join(5)
        self.assertFalse(thread.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertEqual(errors + close_errors, [])
        self.assertTrue(results[0].closed)
        self.assertEqual(self.ctrl._apps, {})
        self.assertFalse(self.ctrl.maintenance_snapshot()["idle_observed"])


if __name__ == "__main__":
    unittest.main()
