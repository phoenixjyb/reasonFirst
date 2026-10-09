"""Live selected-policy binding without implying configuration or activation proof."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from gitlab_agent import bridge_mcp
from gitlab_agent.bridge_http import HTTPLaunch
from gitlab_agent.bridge_preview import controller as controller_module
from gitlab_agent.bridge_preview.admission import ControllerAdmission
from gitlab_agent.config import AgentSettings
from gitlab_agent.upgrade import service_managed as service
from gitlab_agent.upgrade.service_configuration import (
    ServiceConfigurationError,
    capture_service_configuration,
)
from gitlab_agent.upgrade.service_runtime import ServiceRuntimeError
from gitlab_agent.upgrade.startup_state import create_disposable_configuration

from test_bridge_maintenance import FakeApp as MaintenanceFakeApp
from test_service_owner import FakeServer, FakeSocket, PoisonLock


FALSE_FLAGS = (
    "effective_configuration_verified", "recovered_state_verified",
    "external_producers_quiesced", "global_idle_verified", "runtime_identity_verified",
    "activation_authorized", "ready_for_activation", "existing_service_adopted",
)
PRIVATE_SENTINEL = "synthetic-binding-private-value"


def captured_configuration(snapshot, *, private_value=PRIVATE_SENTINEL, **options):
    return capture_service_configuration(
        replace(snapshot.settings, api_token=private_value, git_token=private_value),
        snapshot.bridge_configuration(),
        bridge_config_path=snapshot.bridge_config_path,
        state_dir=snapshot.state_dir,
        **options,
    )


class PolicyRecordingApp(MaintenanceFakeApp):
    """The external AppServer stand-in records the real controller's requests."""

    instances = []

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.child_context = kwargs.get("child_context")
        self.resume_calls = []
        self.turn_calls = []

    def resume_thread(self, thread_id, **kwargs):
        self.resume_calls.append((thread_id, kwargs))
        return super().resume_thread(thread_id, **kwargs)

    def start_turn(self, **kwargs):
        self.turn_calls.append(kwargs)
        return super().start_turn(**kwargs)


class LookupCountingDict(dict):
    """Keep normal state-map behavior while observing whether it was consulted."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lookups = []

    def get(self, key, default=None):
        self.lookups.append(key)
        return super().get(key, default)


class ControllerServiceBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rf-controller-binding-")
        self.addCleanup(temporary.cleanup)
        self.fixture = create_disposable_configuration(Path(temporary.name) / "selected")
        self.configuration = captured_configuration(
            self.fixture.snapshot, approval_timeout_seconds=71, gitlab_auth_mode="git-only",
        )
        PolicyRecordingApp.instances = []

    def bound_controller(self, configuration=None):
        controller = controller_module.BridgeController(
            admission=ControllerAdmission(),
            service_configuration=configuration or self.configuration,
        )
        self.addCleanup(controller.close)
        return controller

    def remote_configuration(self):
        bridge = self.fixture.snapshot.bridge_configuration()
        bridge["defaults"]["target"] = "selected-remote"
        bridge["targets"]["selected-remote"] = {
            "type": "ssh", "name": "selected-remote", "host": "worker.invalid",
            "repo": "/selected/repository", "codex_backend": "remote-ssh",
            "remote_codex": "codex", "ssh_connect_timeout": 8, "network_access": True,
            "validation": {
                "engine": "docker", "image": "synthetic:1",
                "allowed_executables": ["python", "pytest"], "network_access": True,
            },
        }
        return capture_service_configuration(
            self.configuration.settings, bridge,
            bridge_config_path=self.configuration.bridge_config_path,
            state_dir=self.configuration.state_dir,
        )

    def seed_session(self, controller, *, name="selected-thread"):
        configuration = controller.service_configuration or self.configuration
        target = configuration.configured_target()
        worktree = configuration.settings.workspace_root / "selected-worktree"
        worktree.mkdir(parents=True, exist_ok=True)
        record = {
            "workspace_id": "selected-workspace", "kind": target.type,
            "target": json.loads(json.dumps(target.to_dict())),
            "worktree_path": str(worktree), "project": "synthetic/project", "task": "binding",
        }
        session = {
            "thread_id": name, "workspace_id": record["workspace_id"],
            "target": json.loads(json.dumps(target.to_dict())),
            "worktree_path": str(worktree), "codex_cwd": str(worktree),
            "app_key": controller._app_key(target), "pending_approvals": {}, "events": [],
        }
        controller._state["workspaces"][record["workspace_id"]] = record
        controller._state["sessions"][name] = session
        return session, record

    @staticmethod
    def dynamic_push_message():
        return {
            "id": 71, "method": "item/tool/call",
            "params": {
                "threadId": "selected-thread", "namespace": "reasonfirst_remote",
                "tool": "commit_push", "arguments": {},
            },
        }

    def test_live_controller_consumes_exact_selected_policy_without_ordinary_loaders(self):
        gate = ControllerAdmission()
        with (
            patch.object(AgentSettings, "load", side_effect=AssertionError("unexpected settings reload")),
            patch.object(controller_module, "load_bridge_config", side_effect=AssertionError("unexpected bridge reload")),
            patch.object(controller_module, "_run_json", side_effect=AssertionError("unexpected configuration child")),
            patch.dict(os.environ, {
                "RF_GITLAB_AUTH_MODE": "api", "RF_APPROVAL_TIMEOUT_SECONDS": "999",
                "RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH": "true",
            }),
        ):
            controller = controller_module.BridgeController(
                admission=gate, service_configuration=self.configuration,
            )
            try:
                self.assertIs(controller.service_configuration, self.configuration)
                self.assertIsNone(controller.managed_startup_state)
                self.assertIs(controller._admission, gate)
                self.assertEqual(controller.state_dir, self.configuration.state_dir)
                self.assertIs(controller._settings_for_operation(), self.configuration.settings)
                self.assertIs(controller._effective_codex_policy(), self.configuration.codex_policy)
                self.assertIs(controller._requested_target(), self.configuration.configured_target())
                self.assertEqual(controller._allowed_workspace_root(), self.configuration.settings.workspace_root)
                report = controller._reasonfirst_config()
                self.assertEqual(report["gitlab_base_url"], self.configuration.settings.gitlab_base_url)
                self.assertEqual(report["workspace_root"], str(self.configuration.settings.workspace_root))
                self.assertIs(report["api_token_set"], True)
                self.assertIs(report["git_token_set"], True)
                self.assertTrue(controller._git_only_mode())
                self.assertEqual(controller._effective_approval_timeout_seconds(), 71)
                self.assertFalse(controller._effective_remote_push_enabled())
                controller.assert_tool_admitted()

                # The compatibility view is intentionally detached. Mutating it
                # cannot change the policy actually consumed by live helpers.
                controller.bridge_config["defaults"]["target"] = "unselected"
                controller.bridge_config["targets"]["local"]["network_access"] = True
                self.assertFalse(controller._requested_target().network_access)
                self.assertFalse(controller._target_from_dict("local").network_access)
                with self.assertRaises(ServiceConfigurationError):
                    controller._requested_target("unselected")
            finally:
                controller.close()

    def test_shared_core_retains_the_exact_live_configuration_in_both_modes(self):
        controller = controller_module.BridgeController(
            admission=ControllerAdmission(), service_configuration=self.configuration,
        )
        try:
            for read_only in (False, True):
                with self.subTest(read_only=read_only):
                    core = bridge_mcp.build_server(
                        controller=controller, read_only_mode=read_only, subscriptions=False,
                    )
                    self.assertIs(core._reasonfirst_controller, controller)
                    self.assertIs(core._reasonfirst_service_configuration, self.configuration)
        finally:
            controller.close()

    def test_disposable_observer_stays_separate_and_cannot_enroll_with_live_configuration(self):
        observer = controller_module.BridgeController(managed_startup=self.fixture.snapshot)
        try:
            self.assertIsNone(observer.service_configuration)
            with self.assertRaisesRegex(controller_module.BridgeError, "^startup_observation_only$"):
                observer.assert_tool_admitted()
        finally:
            observer.close()
        with self.assertRaises(controller_module.BridgeError):
            controller_module.BridgeController(
                managed_startup=self.fixture.snapshot,
                admission=ControllerAdmission(), service_configuration=self.configuration,
            )

    def test_live_configuration_requires_an_enrolled_controller(self):
        with self.assertRaisesRegex(controller_module.BridgeError, "^service_configuration_requires_admission$"):
            controller_module.BridgeController(service_configuration=self.configuration)
        self.assertFalse(self.configuration.state_dir.exists())

    def test_ordinary_controller_keeps_settings_reload_and_subprocess_configuration(self):
        with (
            patch.dict(os.environ, dict(self.fixture.environment), clear=True),
            patch.object(controller_module, "load_bridge_config", wraps=controller_module.load_bridge_config) as loader,
        ):
            controller = controller_module.BridgeController()
        try:
            self.assertIsNone(controller.service_configuration)
            loader.assert_called_once_with()
            controller.assert_tool_admitted()
            later = replace(self.fixture.snapshot.settings, codex_model="later-synthetic-model")
            with patch.object(AgentSettings, "load", side_effect=[self.fixture.snapshot.settings, later]) as settings:
                self.assertEqual(controller._effective_codex_policy().model, self.fixture.snapshot.settings.codex_model)
                self.assertEqual(controller._effective_codex_policy().model, "later-synthetic-model")
            self.assertEqual(settings.call_count, 2)
            with patch.object(controller_module, "_run_json", return_value={"workspace_root": "synthetic"}) as run:
                self.assertEqual(controller._reasonfirst_config(), {"workspace_root": "synthetic"})
            run.assert_called_once()
        finally:
            controller.close()

    def test_stored_target_preserves_flat_validation_and_json_sequence_round_trip(self):
        configuration = self.remote_configuration()
        controller = self.bound_controller(configuration)
        selected = configuration.configured_target()
        canonical = selected.to_dict()
        serialized = json.loads(json.dumps(canonical))
        self.assertIsInstance(canonical["validation_allowed_executables"], tuple)
        self.assertIsInstance(serialized["validation_allowed_executables"], list)
        for value in (canonical, serialized, "selected-remote", None):
            with self.subTest(route=type(value).__name__):
                actual = controller._target_from_dict(value)
                self.assertIs(actual, selected)
                self.assertEqual(actual.validation_engine, "docker")
                self.assertEqual(actual.validation_image, "synthetic:1")
                self.assertEqual(actual.validation_allowed_executables, ("python", "pytest"))
                self.assertIs(actual.validation_network_access, True)

    def test_stored_target_rejects_policy_changes_unknown_fields_and_scalar_coercion(self):
        configuration = self.remote_configuration()
        controller = self.bound_controller(configuration)
        canonical = json.loads(json.dumps(configuration.configured_target().to_dict()))
        changes = (
            {"host": "other.invalid"}, {"repo": "/other/repository"},
            {"codex_backend": "desktop-proxy"}, {"validation_engine": "podman"},
            {"validation_image": "synthetic:2"},
            {"validation_allowed_executables": ["python", "unexpected"]},
            {"validation_network_access": False}, {"validation_network_access": 1},
            {"network_access": 1}, {"ssh_connect_timeout": 8.0},
            {"unselected_field": "synthetic"},
            {"validation": {"engine": "docker", "image": "synthetic:1"}},
        )
        for change in changes:
            with self.subTest(change=change):
                with self.assertRaisesRegex(controller_module.BridgeError, "^service_target_mismatch$"):
                    controller._target_from_dict({**canonical, **change})
        incomplete = dict(canonical)
        incomplete.pop("validation_image")
        with self.assertRaisesRegex(controller_module.BridgeError, "^service_target_mismatch$"):
            controller._target_from_dict(incomplete)
        self.assertFalse(controller.state_file.exists())

    def test_bound_legacy_remote_target_does_not_probe_change_backend_or_persist(self):
        configuration = self.remote_configuration()
        controller = self.bound_controller(configuration)
        selected = configuration.configured_target()
        record = {
            "workspace_id": "legacy-workspace", "kind": "ssh",
            "target": json.loads(json.dumps(selected.to_dict())),
            "worktree_path": "/selected/worktree", "base_sha": "a" * 40,
        }
        before = json.dumps(record, sort_keys=True)
        with patch.object(
            controller_module, "RemoteWorkspaceManager",
            side_effect=AssertionError("unexpected remote migration probe"),
        ) as manager:
            target, migrated = controller._migrate_legacy_remote_target_if_needed(
                record["workspace_id"], record, controller._target_from_dict(record["target"]),
            )
        self.assertIs(target, selected)
        self.assertFalse(migrated)
        self.assertEqual(target.codex_backend, "remote-ssh")
        self.assertEqual(json.dumps(record, sort_keys=True), before)
        self.assertFalse(controller.state_file.exists())
        manager.assert_not_called()

    def test_changed_saved_worker_policy_fails_before_app_connection_resume_or_continue(self):
        controller = self.bound_controller()
        session, _ = self.seed_session(controller)
        selected = self.configuration.codex_policy
        canonical = json.loads(json.dumps(selected.to_dict()))
        invalid = (
            {**canonical, "model": "different-synthetic-model"},
            {**canonical, "network_access": True},
            {**canonical, "network_access": 0},
            {**canonical, "disable_builtin_mcps": 0},
            {**canonical, "unknown_policy": "synthetic"},
            {"backend": "codex"}, "invalid-saved-policy",
        )
        with (
            patch.object(AgentSettings, "load", side_effect=AssertionError("unexpected policy reload")),
            patch.object(controller_module, "AppServerClient", side_effect=AssertionError("unexpected app connection")) as factory,
            patch.object(controller_module.subprocess, "run", side_effect=AssertionError("unexpected resume command")) as command,
        ):
            for raw in invalid:
                session["worker_policy"] = raw
                for operation in (
                    lambda: controller._app_for_session(session),
                    lambda: controller.continue_task(thread_id=session["thread_id"], goal="synthetic next step"),
                ):
                    with self.subTest(policy=raw, operation=operation):
                        with self.assertRaisesRegex(controller_module.BridgeError, "^service_worker_policy_mismatch$"):
                            operation()
            factory.assert_not_called()
            command.assert_not_called()
        self.assertEqual(controller._apps, {})
        self.assertEqual(controller._app_generations, {})
        self.assertFalse(controller.state_file.exists())

    def test_missing_and_matching_saved_policy_resume_and_continue_with_exact_retained_policy(self):
        controller = self.bound_controller()
        selected = self.configuration.codex_policy
        with (
            patch.object(AgentSettings, "load", side_effect=AssertionError("unexpected policy reload")),
            patch.object(controller_module, "AppServerClient", PolicyRecordingApp),
            patch.object(controller, "_run_json", return_value={"agent_prompt": "synthetic continuation"}) as command,
        ):
            for label in ("missing", "matching"):
                with self.subTest(policy=label):
                    session, _ = self.seed_session(controller, name="selected-thread-" + label)
                    if label == "matching":
                        session["worker_policy"] = json.loads(json.dumps(selected.to_dict()))
                    key, app = controller._app_for_session(session)
                    self.assertEqual(key, session["app_key"])
                    self.assertIs(app.resume_calls[-1][1]["policy"], selected)
                    result = controller.continue_task(
                        thread_id=session["thread_id"], goal="synthetic continuation",
                    )
                    self.assertTrue(result["ok"])
                    self.assertIs(app.turn_calls[-1]["policy"], selected)
                    app.emit("turn/completed", session["thread_id"], result["turn_id"])
            self.assertEqual(command.call_count, 2)
            self.assertTrue(all("resume" in call.args[0] for call in command.call_args_list))
        self.assertEqual(len(PolicyRecordingApp.instances), 1)
        self.assertEqual(len(PolicyRecordingApp.instances[0].resume_calls), 2)

    def test_cached_app_cannot_continue_after_saved_policy_changes(self):
        controller = self.bound_controller()
        session, _ = self.seed_session(controller)
        selected = self.configuration.codex_policy
        session["worker_policy"] = json.loads(json.dumps(selected.to_dict()))
        with patch.object(controller_module, "AppServerClient", PolicyRecordingApp):
            _, app = controller._app_for_session(session)
        session["worker_policy"]["model"] = "different-synthetic-model"
        saved_before = controller.state_file.read_bytes()
        with patch.object(controller_module.subprocess, "run", side_effect=AssertionError("unexpected resume command")) as command:
            with self.assertRaisesRegex(controller_module.BridgeError, "^service_worker_policy_mismatch$"):
                controller.continue_task(thread_id=session["thread_id"], goal="synthetic next step")
        self.assertEqual(len(app.resume_calls), 1)
        self.assertEqual(app.turn_calls, [])
        self.assertEqual(controller.state_file.read_bytes(), saved_before)
        command.assert_not_called()

    def test_unbound_session_preserves_explicit_saved_worker_policy(self):
        with patch.dict(os.environ, dict(self.fixture.environment), clear=True):
            controller = controller_module.BridgeController()
        self.addCleanup(controller.close)
        session, _ = self.seed_session(controller)
        previous = replace(self.configuration.codex_policy, model="previous-saved-model", network_access=True)
        session["worker_policy"] = json.loads(json.dumps(previous.to_dict()))
        with (
            patch.object(AgentSettings, "load", side_effect=AssertionError("saved policy should not reload settings")),
            patch.object(controller_module, "AppServerClient", PolicyRecordingApp),
        ):
            _, app = controller._app_for_session(session)
        resumed = app.resume_calls[-1][1]["policy"]
        self.assertEqual(resumed, previous)
        self.assertIsNot(resumed, self.configuration.codex_policy)

    def test_bound_dynamic_push_rejects_stale_approval_before_state_or_manager_access(self):
        configuration = self.remote_configuration()
        controller = self.bound_controller(configuration)
        session, record = self.seed_session(controller)
        session["push_approval"] = {
            "snapshot": {"digest": "a" * 64}, "message": "Previously approved synthetic change",
        }
        sessions = LookupCountingDict({session["thread_id"]: session})
        workspaces = LookupCountingDict({record["workspace_id"]: record})
        controller._state["sessions"] = sessions
        controller._state["workspaces"] = workspaces
        controller._app_generations[session["app_key"]] = "current-generation"
        before = json.dumps(controller._state, sort_keys=True)
        with patch.object(controller_module, "RemoteWorkspaceManager", side_effect=AssertionError("unexpected push manager")) as manager:
            with self.assertRaisesRegex(controller_module.BridgeError, "^service_remote_push_disabled$"):
                controller._handle_dynamic_tool_request(
                    session["app_key"], self.dynamic_push_message(), admission_app_key="current-generation",
                )
        self.assertEqual(sessions.lookups, [])
        self.assertEqual(workspaces.lookups, [])
        self.assertEqual(json.dumps(controller._state, sort_keys=True), before)
        self.assertFalse(controller.state_file.exists())
        manager.assert_not_called()

    def test_dynamic_push_generation_is_checked_before_the_bound_push_rejection(self):
        controller = self.bound_controller()
        app_key = "selected-app"
        controller._app_generations[app_key] = "current-generation"
        sessions = LookupCountingDict()
        controller._state["sessions"] = sessions
        for generation in (None, "stale-generation"):
            with self.subTest(generation=generation):
                with self.assertRaisesRegex(controller_module.BridgeError, "^untracked_app_source$"):
                    controller._handle_dynamic_tool_request(
                        app_key, self.dynamic_push_message(), admission_app_key=generation,
                    )
        self.assertEqual(sessions.lookups, [])
        self.assertIn("untracked_request", controller.maintenance_snapshot()["unknown_reasons"])
        self.assertFalse(controller.state_file.exists())


class FakeRuntimeObservation:
    """Owner tests isolate runtime evidence; its collector has separate tests."""

    digest = "a" * 64

    def __init__(self):
        self.revalidate = Mock(return_value=None)

    def summary(self):
        return {
            "scope": "selected-service-runtime-files-v1", "digest": self.digest,
            "selected_module_count": 9,
            "interpreter_files_observed": True, "selected_module_origins_observed": True,
            "selected_module_files_observed": True, "current_process_only": True,
            "runtime_identity_verified": False, "running_code_verified": False,
            "activation_authorized": False,
        }


class ManagedServiceBindingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rf-service-binding-")
        self.addCleanup(temporary.cleanup)
        self.fixture = create_disposable_configuration(Path(temporary.name) / "selected")
        self.configuration = captured_configuration(self.fixture.snapshot)
        self.launch = HTTPLaunch("127.0.0.1", 41247, "/selected", "read-only", "disabled")
        self.patch_dict(os.environ, dict(self.fixture.environment), clear=True)
        self.patch(AgentSettings, "load", side_effect=AssertionError("unexpected settings reload"))
        self.bridge_loader = self.patch(
            controller_module, "load_bridge_config", side_effect=AssertionError("unexpected bridge reload"),
        )
        self.patch(service, "_require_supported", return_value=None)
        self.patch(service, "_require_sdk", return_value=None)
        self.patch(service, "CLOSE_TIMEOUT_SECONDS", 0.2)
        self.runtime = FakeRuntimeObservation()
        self.patch(service, "ServiceRuntimeObservation", FakeRuntimeObservation)
        self.runtime_factory = self.patch(service, "capture_service_runtime", return_value=self.runtime)
        self.controllers, self.cores, self.sockets, self.servers = [], [], [], []
        original_factory = service._create_controller

        def create_controller(*args, **kwargs):
            controller = original_factory(*args, **kwargs)
            self.controllers.append(controller)
            return controller

        def build_core(launch, controller):
            core = Mock(name="selected-owned-core")
            core._reasonfirst_controller = controller
            core._reasonfirst_service_configuration = controller.service_configuration
            core._reasonfirst_child_context = controller.child_context
            core.streamable_http_app.return_value = SimpleNamespace(routes=[
                SimpleNamespace(path=launch.path), SimpleNamespace(path="/healthz"),
            ])
            self.cores.append(core)
            return core

        def bind_socket(launch):
            listener = FakeSocket(launch)
            self.sockets.append(listener)
            return listener

        def create_server(app, launch):
            server = FakeServer(app, launch)
            self.servers.append(server)
            return server

        self.controller_factory = self.patch(service, "_create_controller", side_effect=create_controller)
        self.core_factory = self.patch(service, "_build_core", side_effect=build_core)
        self.socket_factory = self.patch(service, "_bind_socket", side_effect=bind_socket)
        self.server_factory = self.patch(service, "_create_server", side_effect=create_server)
        self.patch(service, "_observe_listener", return_value=True)
        self.owner = service.ManagedBridgeService(self.launch, configuration=self.configuration)
        self.owners = [self.owner]
        self.addAsyncCleanup(self.cleanup_owners)

    def patch(self, target, name, *args, **kwargs):
        patcher = patch.object(target, name, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def patch_dict(self, target, *args, **kwargs):
        patcher = patch.dict(target, *args, **kwargs)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def cleanup_owners(self):
        self.runtime.revalidate.side_effect = None
        for server in self.servers:
            server.allow_start.set()
            server.allow_exit.set()
        for owner in reversed(self.owners):
            try:
                await owner.aclose()
            except service.ServiceError:
                pass
            task = owner._server_task
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    def assert_code(self, code, function, *args):
        with self.assertRaises(service.ServiceError) as failure:
            function(*args)
        self.assertEqual(failure.exception.code, code)
        return failure.exception

    async def assert_async_code(self, code, awaitable):
        with self.assertRaises(service.ServiceError) as failure:
            await awaitable
        self.assertEqual(failure.exception.code, code)
        return failure.exception

    def assert_unverified(self, snapshot):
        for name in FALSE_FLAGS:
            self.assertIs(snapshot[name], False)

    def assert_no_resources(self):
        for factory in (self.controller_factory, self.core_factory, self.socket_factory, self.server_factory):
            factory.assert_not_called()

    async def test_bound_constructor_is_lazy_and_rejects_non_configuration_objects(self):
        self.runtime_factory.assert_not_called()
        self.assert_no_resources()
        self.assert_code(
            "invalid_service_configuration",
            lambda: service.ManagedBridgeService(self.launch, configuration=object()),
        )
        snapshot = self.owner.maintenance_snapshot()
        self.assertFalse(snapshot["resolved_policy_bound"])
        self.assertFalse(snapshot["current_process_bound"])
        self.runtime_factory.assert_not_called()
        self.assert_no_resources()

    async def test_started_owner_binds_exact_objects_and_emits_only_scoped_evidence(self):
        await self.owner.start()
        self.runtime_factory.assert_called_once_with()
        self.assertGreaterEqual(self.runtime.revalidate.call_count, 1)
        self.assertIs(self.owner._controller.service_configuration, self.configuration)
        self.assertIs(self.owner._core._reasonfirst_service_configuration, self.configuration)
        snapshot = self.owner.maintenance_snapshot()
        self.assertTrue(snapshot["resolved_policy_bound"])
        self.assertTrue(snapshot["current_process_bound"])
        self.assertEqual(snapshot["configuration_digest"], self.configuration.configuration_digest(self.launch))
        self.assertEqual(snapshot["runtime_observation"], self.runtime.summary())
        self.assert_unverified(snapshot)
        serialized = json.dumps(snapshot)
        self.assertNotIn(PRIVATE_SENTINEL, serialized)
        self.assertNotIn(str(self.configuration.state_dir), serialized)
        self.assertNotIn(str(self.configuration.settings.config_file), serialized)
        expected_observation = self.runtime.summary()
        snapshot["runtime_observation"]["selected_module_count"] = 999
        with patch.object(self.runtime, "summary", side_effect=AssertionError("snapshot must use cached observation")):
            self.assertEqual(self.owner.maintenance_snapshot()["runtime_observation"], expected_observation)
            await self.owner.aclose()
            self.assertEqual(self.owner.maintenance_snapshot()["runtime_observation"], expected_observation)
        closed = self.owner.maintenance_snapshot()
        self.assertFalse(closed["resolved_policy_bound"])
        self.assertFalse(closed["current_process_bound"])
        self.assert_unverified(closed)

    async def test_owner_configuration_digest_binds_its_actual_launch(self):
        await self.owner.start()
        digest = self.owner.maintenance_snapshot()["configuration_digest"]
        for change in ({"port": 41248}, {"path": "/other"}, {"mode": "full-chat"}, {"host": "::1"}):
            with self.subTest(field=next(iter(change))):
                self.assertNotEqual(digest, self.configuration.configuration_digest(replace(self.launch, **change)))

    async def test_unbound_service_keeps_ordinary_loading_without_runtime_capture(self):
        self.bridge_loader.side_effect = None
        self.bridge_loader.return_value = self.fixture.snapshot.bridge_configuration()
        owner = service.ManagedBridgeService(self.launch)
        self.owners.append(owner)
        await owner.start()
        self.runtime_factory.assert_not_called()
        self.bridge_loader.assert_called_once_with()
        self.assertIsNone(owner._controller.service_configuration)
        snapshot = owner.maintenance_snapshot()
        self.assertIsNone(snapshot["runtime_observation"])
        self.assertIsNone(snapshot["configuration_digest"])
        self.assertFalse(snapshot["resolved_policy_bound"])
        self.assertFalse(snapshot["current_process_bound"])
        self.assert_unverified(snapshot)

    async def test_equal_digest_owned_controller_configuration_mismatch_is_cleaned(self):
        foreign = captured_configuration(self.fixture.snapshot, private_value="different-synthetic-private-value")
        self.assertIsNot(foreign, self.configuration)
        self.assertEqual(foreign.configuration_digest(self.launch), self.configuration.configuration_digest(self.launch))
        controller = controller_module.BridgeController(
            admission=self.owner._admission, service_configuration=foreign,
        )
        self.addCleanup(controller.close)
        self.controller_factory.side_effect = None
        self.controller_factory.return_value = controller
        with patch.object(controller, "close", wraps=controller.close) as close:
            await self.assert_async_code("controller_binding_failed", self.owner.start())
            close.assert_called_once_with()
        self.assertTrue(controller._controller_closed)
        self.core_factory.assert_not_called()
        self.assertTrue(all(listener.closed for listener in self.sockets))

    async def test_foreign_gate_controller_is_not_adopted_or_closed(self):
        foreign_gate = ControllerAdmission()
        controller = controller_module.BridgeController(
            admission=foreign_gate, service_configuration=self.configuration,
        )
        self.addCleanup(controller.close)
        self.controller_factory.side_effect = None
        self.controller_factory.return_value = controller
        with patch.object(controller, "close", wraps=controller.close) as close:
            await self.assert_async_code("controller_binding_failed", self.owner.start())
            close.assert_not_called()
        self.assertIsNone(self.owner._controller)
        self.assertFalse(controller._controller_closed)
        self.assertTrue(foreign_gate.snapshot()["admission_open"])
        self.core_factory.assert_not_called()
        self.assertTrue(all(listener.closed for listener in self.sockets))

    async def test_equal_digest_foreign_core_configuration_refuses_running_service(self):
        foreign = captured_configuration(self.fixture.snapshot)
        original = self.core_factory.side_effect

        def foreign_core(launch, controller):
            core = original(launch, controller)
            core._reasonfirst_service_configuration = foreign
            return core

        self.core_factory.side_effect = foreign_core
        await self.assert_async_code("core_binding_failed", self.owner.start())
        self.server_factory.assert_not_called()
        self.assertTrue(self.controllers[0]._controller_closed)
        snapshot = self.owner.maintenance_snapshot()
        self.assertFalse(snapshot["resolved_policy_bound"])
        self.assertFalse(snapshot["current_process_bound"])
        self.assertTrue(snapshot["cleanup_complete"])

    async def test_running_controller_cannot_substitute_an_equal_digest_configuration(self):
        await self.owner.start()
        foreign = captured_configuration(self.fixture.snapshot, private_value="different-synthetic-private-value")
        self.assertEqual(foreign.configuration_digest(self.launch), self.configuration.configuration_digest(self.launch))
        self.owner._controller._service_configuration = foreign
        self.assert_code("configuration_binding_failed", self.owner.try_enter_maintenance)
        snapshot = self.owner.maintenance_snapshot()
        self.assertFalse(snapshot["admission"]["admission_open"])
        self.assertFalse(snapshot["resolved_policy_bound"])
        self.assertFalse(snapshot["current_process_bound"])
        self.owner._controller._service_configuration = self.configuration
        with self.assertRaises(service.ServiceError):
            self.owner.try_enter_maintenance()

    async def test_externally_closed_bound_controller_latches_configuration_failure(self):
        await self.owner.start()
        self.owner._controller.close()
        self.assert_code("configuration_binding_failed", self.owner.try_enter_maintenance)
        snapshot = self.owner.maintenance_snapshot()
        self.assertEqual(snapshot["error_code"], "configuration_binding_failed")
        self.assertFalse(snapshot["admission"]["admission_open"])
        self.assertFalse(snapshot["resolved_policy_bound"])
        self.assertFalse(snapshot["current_process_bound"])
        with self.assertRaises(service.ServiceError):
            self.owner.try_enter_maintenance()
        self.runtime_factory.assert_called_once_with()

    async def test_externally_closed_bound_gate_latches_configuration_failure(self):
        await self.owner.start()
        self.owner._admission.close()
        self.assertFalse(self.owner._controller._controller_closed)
        self.assert_code("configuration_binding_failed", self.owner.try_enter_maintenance)
        snapshot = self.owner.maintenance_snapshot()
        self.assertEqual(snapshot["error_code"], "configuration_binding_failed")
        self.assertFalse(snapshot["admission"]["admission_open"])
        self.assertFalse(snapshot["resolved_policy_bound"])
        self.assertFalse(snapshot["current_process_bound"])
        with self.assertRaises(service.ServiceError):
            self.owner.try_enter_maintenance()
        self.runtime_factory.assert_called_once_with()

    async def test_saved_state_uncertainty_survives_valid_policy_and_runtime_binding(self):
        self.configuration.state_dir.mkdir()
        (self.configuration.state_dir / "state.json").write_text(json.dumps({
            "version": 3, "sessions": {}, "workspaces": {}, "finish_approvals": {},
        }), encoding="utf-8")
        await self.owner.start()
        snapshot = self.owner.maintenance_snapshot()
        self.assertTrue(snapshot["resolved_policy_bound"])
        self.assertTrue(snapshot["current_process_bound"])
        self.assertIn("recovered_state_unverified", snapshot["admission"]["unknown_reasons"])
        self.assert_unverified(snapshot)
        self.assert_code("maintenance_busy", self.owner.try_enter_maintenance)

    async def test_runtime_capture_failure_precedes_socket_and_controller_resources(self):
        self.runtime_factory.side_effect = ServiceRuntimeError("runtime_capture_failed")
        await self.assert_async_code("runtime_binding_failed", self.owner.start())
        self.assert_no_resources()
        snapshot = self.owner.maintenance_snapshot()
        self.assertFalse(snapshot["current_process_bound"])
        self.assertFalse(snapshot["resolved_policy_bound"])
        self.assertTrue(snapshot["cleanup_complete"])

    async def test_invalid_runtime_factory_result_cannot_bind_a_service(self):
        self.runtime_factory.return_value = object()
        await self.assert_async_code("runtime_binding_failed", self.owner.start())
        self.assert_no_resources()

    async def test_malformed_nominal_runtime_summary_fails_before_resources(self):
        valid = self.runtime.summary()
        malformed = (
            {}, {**valid, "unexpected": PRIVATE_SENTINEL},
            {**valid, "digest": "b" * 64},
            {**valid, "current_process_only": 1},
            {**valid, "selected_module_count": True},
            {**valid, "runtime_identity_verified": True},
        )
        for index, summary in enumerate(malformed):
            with self.subTest(summary=index):
                owner = self.owner if index == 0 else service.ManagedBridgeService(
                    self.launch, configuration=self.configuration,
                )
                if index:
                    self.owners.append(owner)
                with patch.object(self.runtime, "summary", return_value=summary):
                    await self.assert_async_code("runtime_binding_failed", owner.start())
                    snapshot = owner.maintenance_snapshot()
                self.assertIsNone(snapshot["runtime_observation"])
                self.assertFalse(snapshot["current_process_bound"])
                self.assertTrue(snapshot["cleanup_complete"])
                self.assertNotIn(PRIVATE_SENTINEL, json.dumps(snapshot))
        self.assert_no_resources()

    async def test_runtime_revalidation_failure_cannot_publish_running_service(self):
        self.runtime.revalidate.side_effect = ServiceRuntimeError("runtime_file_changed")
        await self.assert_async_code("runtime_binding_failed", self.owner.start())
        snapshot = self.owner.maintenance_snapshot()
        self.assertFalse(snapshot["listener_serving"])
        self.assertFalse(snapshot["resolved_policy_bound"])
        self.assertFalse(snapshot["current_process_bound"])
        self.assertTrue(snapshot["cleanup_complete"])
        self.assertTrue(all(listener.closed for listener in self.sockets))
        self.assertTrue(all(controller._controller_closed for controller in self.controllers))

    async def test_runtime_drift_at_maintenance_entry_is_sticky_and_closes_admission(self):
        await self.owner.start()
        self.runtime.revalidate.side_effect = ServiceRuntimeError("runtime_file_changed")
        self.assert_code("runtime_binding_failed", self.owner.try_enter_maintenance)
        snapshot = self.owner.maintenance_snapshot()
        self.assertFalse(snapshot["admission"]["admission_open"])
        self.assertFalse(snapshot["maintenance_window_held"])
        self.assertFalse(snapshot["resolved_policy_bound"])
        self.assertFalse(snapshot["current_process_bound"])
        self.assertEqual(snapshot["error_code"], "runtime_binding_failed")
        self.runtime.revalidate.side_effect = None
        with self.assertRaises(service.ServiceError):
            self.owner.try_enter_maintenance()
        self.assertFalse(self.owner.maintenance_snapshot()["admission"]["admission_open"])
        self.runtime_factory.assert_called_once_with()

    async def test_runtime_drift_at_lease_release_cannot_reopen_admission(self):
        await self.owner.start()
        lease = self.owner.try_enter_maintenance()
        self.runtime.revalidate.side_effect = ServiceRuntimeError("runtime_file_changed")
        self.assert_code("runtime_binding_failed", self.owner.leave_maintenance, lease)
        snapshot = self.owner.maintenance_snapshot()
        self.assertFalse(snapshot["admission"]["admission_open"])
        self.assertFalse(snapshot["maintenance_window_held"])
        self.assertFalse(snapshot["current_process_bound"])
        self.runtime.revalidate.side_effect = None
        with self.assertRaises(service.ServiceError):
            self.owner.leave_maintenance(lease)
        self.assertFalse(self.owner.maintenance_snapshot()["admission"]["admission_open"])

    async def test_wrong_process_precedes_owner_lock_and_runtime_revalidation(self):
        await self.owner.start()
        before = self.runtime.revalidate.call_count
        pid = os.getpid()
        with patch.object(self.owner, "_lock", PoisonLock()), patch.object(service.os, "getpid", return_value=pid + 1):
            self.assert_code("service_wrong_process", self.owner.maintenance_snapshot)
            self.assert_code("service_wrong_process", self.owner.try_enter_maintenance)
            self.assert_code("service_wrong_process", self.owner.leave_maintenance, object())
            await self.assert_async_code("service_wrong_process", self.owner.start())
            await self.assert_async_code("service_wrong_process", self.owner.aclose())
        self.assertEqual(self.runtime.revalidate.call_count, before)
        self.runtime_factory.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
