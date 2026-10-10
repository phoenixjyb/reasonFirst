"""Owned local child inputs across real controller paths and owner transitions."""
from __future__ import annotations

from contextlib import chdir
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from gitlab_agent.bridge_preview import controller as c
from gitlab_agent.bridge_preview.admission import ControllerAdmission
from gitlab_agent.config import AgentSettings
from gitlab_agent.upgrade import service_child, service_children, service_managed as service
from gitlab_agent.upgrade.service_configuration import capture_service_configuration
from gitlab_agent.upgrade.startup_state import create_disposable_configuration

import test_service_binding as prior_binding
from test_bridge_maintenance import FakeApp


WORKSPACE_ID = "a123456789bc"
PRIVATE_VALUE = "synthetic-child-binding-private-value"
MARKER_NAME = "RF_TEST_CHILD_INPUT"


def child_result(result, *, exit_code=0):
    return json.dumps({
        "protocol": service_child.RESPONSE_PROTOCOL,
        "exit_code": exit_code,
        "result": result,
        "error": None,
    }).encode("utf-8")


def child_failure(code):
    return json.dumps({
        "protocol": service_child.RESPONSE_PROTOCOL,
        "exit_code": 1,
        "result": None,
        "error": code,
    }).encode("utf-8")


class ContextApp(FakeApp):
    """An external AppServer stand-in that retains its actual launch context."""

    instances = []

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.child_context = kwargs.get("child_context")


class ControllerChildBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rf-child-controller-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "child 空间's fixture"
        self.root.mkdir()
        self.fixture = create_disposable_configuration(self.root / "selected")
        self.configuration = self.capture_configuration()
        self.cwd = self.root / "selected-cwd"
        self.cwd.mkdir()
        self.other_cwd = self.root / "later-cwd"
        self.other_cwd.mkdir()
        self.context = self.capture_context(self.configuration, marker="selected")
        ContextApp.instances = []

    def capture_configuration(self, *, auth_mode="git-only", state_dir=None, settings=None):
        snapshot = self.fixture.snapshot
        return capture_service_configuration(
            settings or replace(
                snapshot.settings, api_token=PRIVATE_VALUE, git_token=PRIVATE_VALUE,
            ),
            snapshot.bridge_configuration(),
            bridge_config_path=snapshot.bridge_config_path,
            state_dir=state_dir or snapshot.state_dir,
            gitlab_auth_mode=auth_mode,
        )

    def capture_context(self, configuration, *, marker):
        env = {
            key: value for key, value in os.environ.items()
            if key.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC"}
        }
        env.update(self.fixture.environment)
        env.update({
            MARKER_NAME: marker,
            "RF_TEST_PRIVATE_TOKEN": PRIVATE_VALUE,
            "CODEX_BRIDGE_CODEX_BIN": sys.executable,
        })
        with patch.dict(os.environ, env, clear=True), chdir(self.cwd):
            return service_children.capture_service_children(configuration)

    def controller(self, configuration=None, context=None):
        instance = c.BridgeController(
            admission=ControllerAdmission(),
            service_configuration=configuration or self.configuration,
            child_context=context or self.context,
        )
        self.addCleanup(instance.close)
        return instance

    def seed(self, controller):
        root = controller.service_configuration.settings.workspace_root
        worktree = root / "worktrees" / WORKSPACE_ID
        worktree.mkdir(parents=True, exist_ok=True)
        (worktree / "selected.txt").write_text("selected workspace\n", encoding="utf-8")
        state = {
            "workspace_id": WORKSPACE_ID,
            "project": "synthetic/project",
            "repo_path": str(root / "repos" / "synthetic.git"),
            "worktree_path": str(worktree),
            "base_ref": "main", "base_sha": "a" * 40,
            "branch": "chatgpt/selected", "created_at": "2026-01-01T00:00:00Z",
        }
        (root / "state").mkdir(parents=True, exist_ok=True)
        (root / "state" / (WORKSPACE_ID + ".json")).write_text(
            json.dumps(state), encoding="utf-8",
        )
        target = controller.service_configuration.configured_target().to_dict()
        record = {**state, "target": target, "kind": "local", "task": "binding", "created_at": 1}
        session = {
            "thread_id": "selected-thread", "workspace_id": WORKSPACE_ID,
            "target": target, "worktree_path": str(worktree), "codex_cwd": str(worktree),
            "app_key": controller._app_key(controller.service_configuration.configured_target()),
            "pending_approvals": {}, "events": [],
        }
        controller._state["workspaces"][WORKSPACE_ID] = record
        controller._state["sessions"][session["thread_id"]] = session
        return worktree, session

    def expect_children(self, controller, requests, results, *, api_trust_expected=False):
        """Mock only the external child and inspect what the real wrapper supplied."""
        calls = []

        def run(argv, **kwargs):
            index = len(calls)
            self.assertLess(index, len(requests), "unexpected child launch")
            module, arguments = requests[index]
            selected_trust = controller.child_context.api_trust
            if api_trust_expected:
                self.assertIsNotNone(selected_trust)
                self.assertIs(selected_trust.settings, controller.service_configuration.settings)
            else:
                self.assertIsNone(selected_trust)
            self.assertEqual(kwargs.get("input"), service_child.encode_child_request(
                controller.service_configuration.settings, module, arguments,
                api_trust=selected_trust,
            ))
            self.assertEqual(argv[0], controller.child_context.python_invocation)
            self.assertIn("gitlab_agent.upgrade.service_child", argv)
            self.assertNotIn(PRIVATE_VALUE, repr(argv))
            self.assertEqual(kwargs["env"][MARKER_NAME], "selected")
            self.assertEqual(Path(kwargs["cwd"]), self.cwd)
            calls.append((list(argv), kwargs))
            return subprocess.CompletedProcess(argv, 0, child_result(results[index]), b"")

        return patch.object(c.subprocess, "run", side_effect=run), calls

    def test_every_local_inspection_helper_uses_the_private_selected_settings_channel(self):
        actual = "gitlab_agent.actual_coder_cli"
        cases = (
            ("contract", lambda ctrl: ctrl._project_contract_at_ref("synthetic/project", "main"),
             ["project-config", "synthetic/project", "--ref", "main", "--validate"], {"effective": {}}),
            ("preflight", lambda ctrl: ctrl.project_preflight("synthetic/project", ref="main"),
             ["project-config", "synthetic/project", "--validate", "--ref", "main"], {"effective": {}}),
            ("status", lambda ctrl: ctrl.workspace_status(workspace_id=WORKSPACE_ID),
             ["status", WORKSPACE_ID], {}),
            ("files", lambda ctrl: ctrl.files(workspace_id=WORKSPACE_ID, recursive=True, max_entries=17),
             ["files", WORKSPACE_ID, ".", "--max-entries", "17", "--recursive"], {"entries": []}),
            ("read", lambda ctrl: ctrl.read(workspace_id=WORKSPACE_ID, path="selected.txt"),
             ["read", WORKSPACE_ID, "selected.txt"], {"content": "selected workspace\n"}),
            ("diff", lambda ctrl: ctrl.diff(workspace_id=WORKSPACE_ID),
             ["diff", WORKSPACE_ID], {"diff": ""}),
            ("evidence", lambda ctrl: ctrl.evidence(thread_id="selected-thread"),
             ["evidence", WORKSPACE_ID], {"ok": True}),
        )
        for name, operation, arguments, result in cases:
            with self.subTest(route=name):
                controller = self.controller()
                self.seed(controller)
                external, calls = self.expect_children(controller, [(actual, arguments)], [result])
                with (
                    external,
                    patch.object(AgentSettings, "load", side_effect=AssertionError("ordinary reload")),
                    patch.dict(os.environ, {MARKER_NAME: "later", "GITLAB_BASE_URL": "https://later.invalid"}),
                    chdir(self.other_cwd),
                ):
                    operation(controller)
                self.assertEqual(len(calls), 1)

    def test_api_preflight_ci_and_ci_evidence_use_retained_settings(self):
        configuration = self.capture_configuration(auth_mode="api")
        context = self.capture_context(configuration, marker="selected")
        controller = self.controller(configuration, context)
        self.seed(controller)
        requests = [
            ("gitlab_agent.project_access", ["synthetic/project", "--ref", "main"]),
            ("gitlab_agent.actual_coder_cli", ["ci", WORKSPACE_ID]),
            ("gitlab_agent.actual_coder_cli", ["evidence", WORKSPACE_ID, "--from-ci"]),
        ]
        external, calls = self.expect_children(
            controller, requests, [{"ok": True}] * 3, api_trust_expected=True,
        )
        with external, patch.dict(os.environ, {MARKER_NAME: "later", "RF_GITLAB_AUTH_MODE": "git-only"}):
            controller.project_preflight("synthetic/project", ref="main")
            controller.ci(thread_id="selected-thread")
            controller.evidence(thread_id="selected-thread", from_ci=True)
        self.assertEqual(len(calls), 3)

    def test_prepare_supplies_retained_settings_to_both_preflight_and_start(self):
        controller = self.controller()
        worktree, _ = self.seed(controller)
        requests = [
            ("gitlab_agent.actual_coder_cli", ["project-config", "synthetic/project", "--validate", "--ref", "main"]),
            ("gitlab_agent.actual_coder_cli", [
                "start", "synthetic/project", "--task", "binding", "--goal", "selected goal",
                "--agent", "codex", "--no-launch", "--acceptance", "selected result",
                "--non-goal", "unrelated changes", "--git-only", "--base-ref", "main",
            ]),
        ]
        external, calls = self.expect_children(controller, requests, [
            {"effective": {}},
            {"workspace": {"workspace_id": WORKSPACE_ID}, "worktree_path": str(worktree)},
        ])
        with external, patch.dict(os.environ, {MARKER_NAME: "later"}):
            result = controller.prepare(
                project="synthetic/project", task="binding", goal="selected goal", base_ref="main",
                acceptance_criteria=["selected result"], non_goals=["unrelated changes"],
            )
        self.assertEqual(result["workspace_id"], WORKSPACE_ID)
        self.assertFalse(result["codex_started"])
        self.assertEqual(len(calls), 2)

    def test_start_and_continue_use_the_same_context_for_handoffs_and_app(self):
        controller = self.controller()
        worktree, _ = self.seed(controller)
        requests = [
            ("gitlab_agent.actual_coder_cli", ["status", WORKSPACE_ID]),
            ("gitlab_agent.actual_coder_cli", ["resume", WORKSPACE_ID, "--agent", "codex", "--goal", "first"]),
            ("gitlab_agent.actual_coder_cli", ["resume", WORKSPACE_ID, "--agent", "codex", "--goal", "next"]),
        ]
        external, calls = self.expect_children(controller, requests, [
            {"worktree_path": str(worktree)}, {"agent_prompt": "selected first"},
            {"agent_prompt": "selected next"},
        ])
        with external, patch.object(c, "AppServerClient", ContextApp), patch.dict(os.environ, {MARKER_NAME: "later"}):
            first = controller.start_codex(workspace_id=WORKSPACE_ID, goal="first")
            app = ContextApp.instances[-1]
            self.assertIs(app.child_context, self.context)
            app.emit("turn/completed", first["thread_id"], first["turn_id"])
            continued = controller.continue_task(thread_id=first["thread_id"], goal="next")
            app.emit("turn/completed", first["thread_id"], continued["turn_id"])
        self.assertEqual(len(ContextApp.instances), 1)
        self.assertEqual(len(calls), 3)

    def test_artifact_helpers_use_bound_status_child(self):
        controller = self.controller()
        worktree, _ = self.seed(controller)
        requests = [("gitlab_agent.actual_coder_cli", ["status", WORKSPACE_ID])] * 2
        external, calls = self.expect_children(controller, requests, [{"worktree_path": str(worktree)}] * 2)
        with external:
            controller.artifacts(workspace_id=WORKSPACE_ID, changed_only=False)
            result = controller.artifact_descriptor(workspace_id=WORKSPACE_ID, path="selected.txt")
        self.assertEqual(len(calls), 2)
        self.assertIn("selected", json.dumps(result))

    def test_real_read_and_files_children_ignore_changed_env_files_and_parent_cwd(self):
        controller = self.controller()
        self.seed(controller)
        poison = self.root / "later.env"
        poison.write_text(
            "GITLAB_BASE_URL=https://later.invalid\nGITLAB_WORKSPACE_ROOT="
            + str(self.root / "wrong-workspaces") + "\n",
            encoding="utf-8",
        )
        self.configuration.settings.config_file.write_text(poison.read_text(encoding="utf-8"), encoding="utf-8")
        (self.other_cwd / ".env").write_text(poison.read_text(encoding="utf-8"), encoding="utf-8")
        with (
            patch.dict(os.environ, {
                "GITLAB_AGENT_ENV_FILE": str(poison), "GITLAB_WORKSPACE_ROOT": str(self.root / "wrong-workspaces"),
                "GITLAB_BASE_URL": "https://later.invalid", MARKER_NAME: "later",
                "PYTHONPATH": str(self.root / "shadow-import"),
            }),
            chdir(self.other_cwd),
        ):
            read = controller.read(workspace_id=WORKSPACE_ID, path="selected.txt")
            listing = controller.files(workspace_id=WORKSPACE_ID)
        self.assertEqual(read["content"], "1: selected workspace")
        self.assertIn("selected.txt", json.dumps(listing))
        self.assertNotIn(PRIVATE_VALUE, json.dumps({"read": read, "listing": listing}))
        self.assertFalse((self.root / "wrong-workspaces").exists())

    def test_two_controllers_keep_independent_contexts(self):
        other = self.capture_configuration(state_dir=self.root / "other-state")
        second_context = self.capture_context(other, marker="second")
        first, second = self.controller(), self.controller(other, second_context)
        self.seed(first)
        self.seed(second)
        received = []

        def run(argv, **kwargs):
            received.append(kwargs["env"][MARKER_NAME])
            return subprocess.CompletedProcess(argv, 0, child_result({"content": "selected\n"}), b"")

        with patch.object(c.subprocess, "run", side_effect=run), patch.dict(os.environ, {MARKER_NAME: "ambient"}):
            before = dict(os.environ)
            first.read(workspace_id=WORKSPACE_ID, path="selected.txt")
            second.read(workspace_id=WORKSPACE_ID, path="selected.txt")
            first.read(workspace_id=WORKSPACE_ID, path="selected.txt")
            self.assertEqual(dict(os.environ), before)
        self.assertEqual(received, ["selected", "second", "selected"])

    def test_cached_app_cannot_substitute_a_foreign_equal_policy_context(self):
        controller = self.controller()
        worktree, _ = self.seed(controller)
        target = self.configuration.configured_target()
        foreign = self.capture_context(self.configuration, marker="selected")
        with patch.object(c, "AppServerClient", ContextApp):
            key, app = controller._get_app(target, cwd=str(worktree))
            self.assertIs(app.child_context, self.context)
            app.child_context = foreign
            with self.assertRaisesRegex(c.BridgeError, "^child_binding_failed$"):
                controller._get_app(target, cwd=str(worktree))
            self.assertFalse(controller._admission.snapshot()["admission_open"])
            app.child_context = self.context
            with self.assertRaises(c.BridgeError):
                controller.read(workspace_id=WORKSPACE_ID, path="selected.txt")
        self.assertEqual(len(ContextApp.instances), 1)
        self.assertIs(controller._apps[key], app)

    def test_cached_foreign_replacement_is_not_closed_and_original_owned_app_is_cleaned(self):
        controller = self.controller()
        worktree, _ = self.seed(controller)
        target = self.configuration.configured_target()
        foreign_context = self.capture_context(self.configuration, marker="selected")
        foreign = SimpleNamespace(child_context=foreign_context, close=Mock())
        with patch.object(c, "AppServerClient", ContextApp):
            key, owned = controller._get_app(target, cwd=str(worktree))
            controller._apps[key] = foreign
            with self.assertRaisesRegex(c.BridgeError, "^child_binding_failed$"):
                controller._get_app(target, cwd=str(worktree))
        foreign.close.assert_not_called()
        controller.close()
        self.assertTrue(owned.closed)
        foreign.close.assert_not_called()

    def test_returned_foreign_app_is_never_adopted_or_closed(self):
        controller = self.controller()
        worktree, _ = self.seed(controller)
        foreign_context = self.capture_context(self.configuration, marker="selected")
        foreign = SimpleNamespace(child_context=foreign_context, close=Mock())
        with patch.object(c, "AppServerClient", return_value=foreign):
            with self.assertRaisesRegex(c.BridgeError, "^child_binding_failed$"):
                controller._get_app(self.configuration.configured_target(), cwd=str(worktree))
        self.assertEqual(controller._apps, {})
        controller.close()
        foreign.close.assert_not_called()

    def test_doctor_uses_bound_child_and_selected_binary_diagnostics(self):
        controller = self.controller()
        external, calls = self.expect_children(controller, [
            ("gitlab_agent.actual_coder_cli", ["doctor", "--offline", "--git-only"]),
        ], [{"ok": True}])
        with external, patch.dict(os.environ, {MARKER_NAME: "later", "CODEX_BRIDGE_CODEX_BIN": "/missing/later"}):
            report = controller.doctor()
        self.assertEqual(len(calls), 1)
        self.assertEqual(report["codex_bin"], self.context.resolve_codex_binary())

    def test_finish_preview_and_direct_finish_use_the_same_retained_inputs(self):
        controller = self.controller()
        self.seed(controller)
        digest = "d" * 64
        launches = []

        def run(argv, **kwargs):
            launches.append((list(argv), kwargs))
            self.assertEqual(kwargs["env"][MARKER_NAME], "selected")
            if "gitlab_agent.upgrade.service_child" in argv:
                self.assertEqual(kwargs["input"], service_child.encode_child_request(
                    self.configuration.settings, "gitlab_agent.actual_coder_cli",
                    ["finish", WORKSPACE_ID, "--message", "selected message", "--dry-run"],
                ))
                return subprocess.CompletedProcess(argv, 0, child_result({
                    "ok": True, "snapshot": {"digest": digest},
                }), b"")
            return subprocess.CompletedProcess(argv, 0, "synthetic command result", "")

        def plan(**kwargs):
            self.assertIs(kwargs["settings"], self.configuration.settings)
            manager, runner = kwargs["manager"], kwargs["runner"]
            self.assertIs(manager.child_context, self.context)
            self.assertIs(runner.workspaces, manager)
            manager._run_git(["--version"])
            runner.run(WORKSPACE_ID, ["python", "-c", "print('selected')"])
            return {"ok": True, "snapshot": {"digest": digest}}

        with (
            patch.object(c.subprocess, "run", side_effect=run),
            patch.object(service_children.ServiceChildContext, "resolve_executable", return_value=self.context.python_invocation),
            patch.object(c, "build_finish_plan", side_effect=plan),
            patch.object(c, "execute_finish", return_value={"synthetic": True}) as publish,
            patch.dict(os.environ, {MARKER_NAME: "later", "GITLAB_WORKSPACE_ROOT": str(self.root / "wrong-workspaces")}),
        ):
            before = dict(os.environ)
            preview = controller.finish_preview(thread_id="selected-thread", message="selected message")
            result = controller.finish(
                thread_id="selected-thread", message="selected message", snapshot_digest=preview["snapshot_digest"],
            )
            self.assertEqual(dict(os.environ), before)
        self.assertTrue(result["published"])
        self.assertEqual(len(launches), 3)
        self.assertNotIn("RF_TEST_PRIVATE_TOKEN", launches[-1][1]["env"])
        publish.assert_called_once()

    def test_direct_bound_controller_captures_a_context_when_omitted(self):
        with patch.object(service_children, "capture_service_children", return_value=self.context) as capture:
            controller = c.BridgeController(
                admission=ControllerAdmission(), service_configuration=self.configuration,
            )
        self.addCleanup(controller.close)
        capture.assert_called_once_with(self.configuration)
        self.assertIs(controller.child_context, self.context)

    def test_foreign_configuration_context_is_rejected_before_state_loading(self):
        other = self.capture_configuration()
        context = self.capture_context(other, marker="selected")
        with patch.object(c, "_private_state_dir", side_effect=AssertionError("state touched")) as state:
            with self.assertRaisesRegex(c.BridgeError, "^child_binding_failed$"):
                c.BridgeController(
                    admission=ControllerAdmission(), service_configuration=self.configuration,
                    child_context=context,
                )
            state.assert_not_called()

    def test_missing_binding_reference_never_falls_back_to_an_ordinary_child(self):
        for field in ("_child_context", "_service_configuration"):
            with self.subTest(missing=field):
                controller = self.controller()
                self.seed(controller)
                retained = getattr(controller, field)
                setattr(controller, field, None)
                with (
                    patch.object(c, "_run_json", side_effect=AssertionError("ordinary child")) as ordinary,
                    patch.object(c.subprocess, "run", side_effect=AssertionError("external child")) as external,
                    patch.object(AgentSettings, "load", side_effect=AssertionError("ordinary settings")) as loader,
                ):
                    with self.assertRaisesRegex(c.BridgeError, "^child_binding_failed$"):
                        controller.read(workspace_id=WORKSPACE_ID, path="selected.txt")
                    self.assertFalse(controller._admission.snapshot()["admission_open"])
                    setattr(controller, field, retained)
                    with self.assertRaises(c.BridgeError):
                        controller.read(workspace_id=WORKSPACE_ID, path="selected.txt")
                    ordinary.assert_not_called()
                    external.assert_not_called()
                    loader.assert_not_called()

    def test_executable_drift_closes_admission_before_another_child(self):
        controller = self.controller()
        self.seed(controller)
        executable = self.root / "selected-helper.exe"
        executable.write_bytes(b"selected executable bytes")
        executable.chmod(0o700)
        self.context.resolve_executable(str(executable))
        executable.write_bytes(b"changed executable bytes")
        with patch.object(c.subprocess, "run", side_effect=AssertionError("launched after drift")) as runner:
            with self.assertRaisesRegex(c.BridgeError, "^child_binding_failed$"):
                controller.read(workspace_id=WORKSPACE_ID, path="selected.txt")
            runner.assert_not_called()
        self.assertFalse(controller._admission.snapshot()["admission_open"])
        executable.write_bytes(b"selected executable bytes")
        with self.assertRaises(c.BridgeError):
            controller.read(workspace_id=WORKSPACE_ID, path="selected.txt")

    def test_child_context_failure_response_closes_admission_permanently(self):
        controller = self.controller()
        self.seed(controller)
        replies = [
            subprocess.CompletedProcess(
                [], 1, child_failure("child_context_failed"), PRIVATE_VALUE.encode("utf-8"),
            ),
            subprocess.CompletedProcess([], 0, child_result({"content": "later success"}), b""),
        ]
        with patch.object(c.subprocess, "run", side_effect=replies) as runner:
            with self.assertRaisesRegex(c.BridgeError, "^child_binding_failed$") as failed:
                controller.read(workspace_id=WORKSPACE_ID, path="selected.txt")
            self.assertNotIn(PRIVATE_VALUE, str(failed.exception))
            self.assertFalse(controller._admission.snapshot()["admission_open"])
            with self.assertRaises(c.BridgeError):
                controller.read(workspace_id=WORKSPACE_ID, path="selected.txt")
            self.assertEqual(runner.call_count, 1)

    def test_child_command_failure_does_not_invalidate_the_selected_context(self):
        controller = self.controller()
        self.seed(controller)
        replies = [
            subprocess.CompletedProcess([], 1, child_failure("child_command_failed"), b""),
            subprocess.CompletedProcess([], 0, child_result({"content": "later success"}), b""),
        ]
        with patch.object(c.subprocess, "run", side_effect=replies) as runner:
            with self.assertRaisesRegex(c.BridgeError, "^child_command_failed$"):
                controller.read(workspace_id=WORKSPACE_ID, path="selected.txt")
            self.assertTrue(controller._admission.snapshot()["admission_open"])
            result = controller.read(workspace_id=WORKSPACE_ID, path="selected.txt")
            self.assertEqual(result["content"], "1: later success")
            self.assertEqual(runner.call_count, 2)

    def test_ordinary_controller_keeps_its_original_child_route(self):
        with patch.dict(os.environ, dict(self.fixture.environment), clear=True):
            controller = c.BridgeController()
        self.addCleanup(controller.close)
        controller._state["workspaces"][WORKSPACE_ID] = {"workspace_id": WORKSPACE_ID, "kind": "local"}
        with patch.object(c, "_run_json", return_value={"content": "ordinary"}) as ordinary:
            result = controller.read(workspace_id=WORKSPACE_ID, path="selected.txt")
        self.assertIsNone(controller.child_context)
        self.assertEqual(result["content"], "1: ordinary")
        ordinary.assert_called_once()


class ManagedChildBindingTests(unittest.IsolatedAsyncioTestCase):
    """Reuse only prior fixture methods, without inheriting its test cases."""

    patch = prior_binding.ManagedServiceBindingTests.patch
    patch_dict = prior_binding.ManagedServiceBindingTests.patch_dict
    cleanup_owners = prior_binding.ManagedServiceBindingTests.cleanup_owners
    assert_code = prior_binding.ManagedServiceBindingTests.assert_code
    assert_async_code = prior_binding.ManagedServiceBindingTests.assert_async_code
    assert_unverified = prior_binding.ManagedServiceBindingTests.assert_unverified
    assert_no_resources = prior_binding.ManagedServiceBindingTests.assert_no_resources

    async def asyncSetUp(self):
        await prior_binding.ManagedServiceBindingTests.asyncSetUp(self)
        original_core = self.core_factory.side_effect

        def build_core(launch, controller):
            core = original_core(launch, controller)
            core._reasonfirst_child_context = controller.child_context
            return core

        self.core_factory.side_effect = build_core
        self.child_factory = self.patch(service, "capture_service_children", wraps=service_children.capture_service_children)

    def select_helper(self):
        helper = self.configuration.state_dir.parent / "selected-helper.exe"
        helper.write_bytes(b"selected executable bytes")
        helper.chmod(0o700)
        self.owner._child_context.resolve_executable(str(helper))
        return helper

    async def test_owner_retains_exact_context_and_only_scoped_detached_evidence(self):
        await self.owner.start()
        context = self.owner._child_context
        self.child_factory.assert_called_once_with(self.configuration)
        self.assertIs(context.configuration, self.configuration)
        self.assertIs(self.owner._controller.child_context, context)
        self.assertIs(self.owner._core._reasonfirst_child_context, context)
        with patch.object(service_children.ServiceChildContext, "revalidate", side_effect=AssertionError("snapshot reread")):
            snapshot = self.owner.maintenance_snapshot()
        self.assertTrue(snapshot["child_inputs_bound"])
        evidence = dict(snapshot["child_observation"])
        self.assertEqual(evidence["scope"], "owned-local-child-inputs")
        for name in ("child_runtime_verified", "provider_configuration_verified", "remote_runtime_verified", "desktop_daemon_verified", "activation_authorized"):
            self.assertIs(evidence[name], False)
        self.assert_unverified(snapshot)
        self.assertNotIn(prior_binding.PRIVATE_SENTINEL, json.dumps(snapshot))
        self.assertNotIn(str(self.configuration.settings.workspace_root), json.dumps(snapshot))
        snapshot["child_observation"]["environment_retained"] = False
        self.assertEqual(self.owner.maintenance_snapshot()["child_observation"], evidence)
        await self.owner.aclose()
        closed = self.owner.maintenance_snapshot()
        self.assertFalse(closed["child_inputs_bound"])
        self.assertFalse(closed["resolved_policy_bound"])
        self.assertFalse(closed["current_process_bound"])
        self.assertEqual(closed["child_observation"], evidence)

    async def test_invalid_context_capture_fails_before_resource_factories(self):
        self.child_factory.side_effect = None
        self.child_factory.return_value = object()
        await self.assert_async_code("child_binding_failed", self.owner.start())
        self.assert_no_resources()
        self.runtime_factory.assert_not_called()

    async def test_helper_context_cannot_replace_owner_configuration_context(self):
        helper = service_children.capture_helper_children(self.configuration.settings)
        self.child_factory.side_effect = None
        self.child_factory.return_value = helper
        await self.assert_async_code("child_binding_failed", self.owner.start())
        self.assert_no_resources()
        self.runtime_factory.assert_not_called()

    async def test_malformed_nominal_context_summary_fails_before_resources(self):
        with patch.object(service_children.ServiceChildContext, "summary", return_value={"scope": PRIVATE_VALUE}):
            failure = await self.assert_async_code("child_binding_failed", self.owner.start())
        self.assertNotIn(PRIVATE_VALUE, str(failure))
        self.assert_no_resources()
        self.runtime_factory.assert_not_called()

    async def test_same_gate_foreign_context_controller_is_cleaned_before_core(self):
        foreign_context = service_children.capture_service_children(self.configuration)
        controller = c.BridgeController(
            admission=self.owner._admission, service_configuration=self.configuration,
            child_context=foreign_context,
        )
        self.addCleanup(controller.close)
        self.controller_factory.side_effect = None
        self.controller_factory.return_value = controller
        with patch.object(controller, "close", wraps=controller.close) as close:
            await self.assert_async_code("child_binding_failed", self.owner.start())
            close.assert_called_once_with()
        self.core_factory.assert_not_called()
        self.assertTrue(all(listener.closed for listener in self.sockets))

    async def test_foreign_core_context_never_publishes_running_owner(self):
        foreign = service_children.capture_service_children(self.configuration)
        original = self.core_factory.side_effect

        def wrong_core(launch, controller):
            core = original(launch, controller)
            core._reasonfirst_child_context = foreign
            return core

        self.core_factory.side_effect = wrong_core
        await self.assert_async_code("child_binding_failed", self.owner.start())
        self.server_factory.assert_not_called()
        self.assertTrue(self.controllers[0]._controller_closed)

    async def test_context_identity_replacement_is_sticky(self):
        await self.owner.start()
        original = self.owner._controller._child_context
        self.owner._controller._child_context = service_children.capture_service_children(self.configuration)
        self.assert_code("child_binding_failed", self.owner.try_enter_maintenance)
        self.owner._controller._child_context = original
        with self.assertRaises(service.ServiceError):
            self.owner.try_enter_maintenance()
        snapshot = self.owner.maintenance_snapshot()
        self.assertEqual(snapshot["error_code"], "child_binding_failed")
        self.assertFalse(snapshot["child_inputs_bound"])
        self.assertFalse(snapshot["admission"]["admission_open"])

    async def test_selected_executable_drift_blocks_maintenance_without_refresh(self):
        await self.owner.start()
        helper = self.select_helper()
        helper.write_bytes(b"changed executable bytes")
        self.assert_code("child_binding_failed", self.owner.try_enter_maintenance)
        helper.write_bytes(b"selected executable bytes")
        with self.assertRaises(service.ServiceError):
            self.owner.try_enter_maintenance()
        self.child_factory.assert_called_once_with(self.configuration)
        snapshot = self.owner.maintenance_snapshot()
        self.assertFalse(snapshot["child_inputs_bound"])
        self.assertFalse(snapshot["admission"]["admission_open"])

    async def test_selected_executable_drift_prevents_lease_release(self):
        await self.owner.start()
        helper = self.select_helper()
        lease = self.owner.try_enter_maintenance()
        helper.write_bytes(b"changed executable bytes")
        self.assert_code("child_binding_failed", self.owner.leave_maintenance, lease)
        snapshot = self.owner.maintenance_snapshot()
        self.assertFalse(snapshot["child_inputs_bound"])
        self.assertFalse(snapshot["admission"]["admission_open"])

    async def test_unbound_owner_does_not_capture_child_inputs(self):
        with patch.object(AgentSettings, "load", return_value=self.configuration.settings), \
                patch.object(c, "load_bridge_config", return_value=self.configuration.bridge_configuration()):
            owner = service.ManagedBridgeService(self.launch)
            self.owners.append(owner)
            await owner.start()
        self.child_factory.assert_not_called()
        self.assertIsNone(owner._controller.child_context)
        snapshot = owner.maintenance_snapshot()
        self.assertFalse(snapshot["child_inputs_bound"])
        self.assertIsNone(snapshot["child_observation"])


if __name__ == "__main__":
    unittest.main()
