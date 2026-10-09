from __future__ import annotations

import asyncio
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, replace
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from gitlab_agent import bridge_mcp
from gitlab_agent.bridge_http import HTTPLaunch
from gitlab_agent.bridge_preview import controller as controller_module
from gitlab_agent.bridge_preview.bridge_config import load_bridge_config
from gitlab_agent.bridge_preview.controller import BridgeController, BridgeError
from gitlab_agent.config import AgentSettings
from gitlab_agent.upgrade import startup_state as s


LAUNCH = HTTPLaunch("127.0.0.1", 8765, "/mcp", "read-only", "disabled")

LOAD = r'''
import json, os
from pathlib import Path
from gitlab_agent.bridge_http import HTTPLaunch
from gitlab_agent.bridge_preview.bridge_config import load_bridge_config
from gitlab_agent.config import AgentSettings
from gitlab_agent.upgrade.startup_state import capture_startup_state
settings = AgentSettings.load()
state = capture_startup_state(
    settings, load_bridge_config(),
    bridge_config_path=Path(os.environ["RF_BRIDGE_CONFIG"]),
    state_dir=Path(os.environ["RF_CODEX_BRIDGE_STATE_DIR"]),
)
launch = HTTPLaunch("127.0.0.1", 8765, "/mcp", "read-only", "disabled")
print(json.dumps({"digest": state.configuration_digest(launch), "projection": state.projection(launch)}))
'''


class StartupStateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="rf-startup-state-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.fixture = s.create_disposable_configuration(self.root / "disposable space")
        self.state = self.fixture.snapshot
        self.config = self.state.bridge_configuration()

    def capture(self, settings=None, config=None):
        return s.capture_startup_state(
            self.state.settings if settings is None else settings,
            self.config if config is None else config,
            bridge_config_path=self.state.bridge_config_path,
            state_dir=self.state.state_dir,
        )

    def child_load(self, extra_env=None):
        env = dict(self.fixture.environment)
        # OS interpreter bootstrap only; never inherit ambient configuration or
        # credentials.  All writable homes/config/temp locations are owned.
        for key in ("SYSTEMROOT", "WINDIR"):
            if key in os.environ:
                env[key] = os.environ[key]
        env.update(extra_env or {})
        child = subprocess.run(
            [sys.executable, "-I", "-B", "-c", LOAD],
            cwd=self.fixture.root, env=env,
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        self.assertEqual(child.returncode, 0, child.stderr)
        return json.loads(child.stdout)

    def test_explicit_synthetic_fixture_matches_existing_loader_in_separate_process(self):
        observed = self.child_load()
        self.assertEqual(observed["projection"], self.state.projection(LAUNCH))
        self.assertEqual(observed["digest"], self.state.configuration_digest(LAUNCH))

    def test_startup_state_can_be_imported_before_controller(self):
        env = dict(self.fixture.environment)
        for key in ("SYSTEMROOT", "WINDIR"):
            if key in os.environ:
                env[key] = os.environ[key]
        child = subprocess.run(
            [sys.executable, "-I", "-B", "-c",
             "from gitlab_agent.upgrade.startup_state import create_disposable_configuration; "
             "from gitlab_agent.bridge_preview.controller import BridgeController; "
             "from gitlab_agent.bridge_mcp import build_server"],
            cwd=self.fixture.root, env=env, capture_output=True, text=True,
            encoding="utf-8", timeout=15,
        )
        self.assertEqual(child.returncode, 0, child.stderr)

    def test_existing_loader_normalizes_legacy_bridge_version_before_capture(self):
        config = json.loads(self.state.bridge_config_path.read_text(encoding="utf-8"))
        config["version"] = 3
        self.state.bridge_config_path.write_text(json.dumps(config), encoding="utf-8")
        self.assertEqual(self.child_load()["digest"], self.state.configuration_digest(LAUNCH))

    def test_fixture_and_projection_do_not_load_or_change_parent_environment(self):
        before = dict(os.environ)
        with patch.object(AgentSettings, "load", side_effect=AssertionError("parent loader")):
            fixture = s.create_disposable_configuration(self.root / "another")
            fixture.snapshot.configuration_digest(LAUNCH)
        self.assertEqual(dict(os.environ), before)
        with self.assertRaises(TypeError):
            fixture.environment["HOME"] = "unexpected"

    def test_factory_is_create_only_and_preserves_existing_bytes(self):
        path = self.state.settings.config_file
        original = path.read_bytes()
        with self.assertRaises(FileExistsError):
            s.create_disposable_configuration(self.fixture.root)
        self.assertEqual(path.read_bytes(), original)

    @unittest.skipIf(os.name == "nt", "POSIX filesystem mode bits")
    def test_owned_configuration_paths_are_private(self):
        self.assertEqual(stat.S_IMODE(self.fixture.root.stat().st_mode), 0o700)
        for path in (self.state.settings.config_file, self.state.bridge_config_path):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_projection_has_exact_versioned_named_sections(self):
        projection = self.state.projection(LAUNCH)
        self.assertEqual(set(projection), {
            "protocol", "scope", "transport", "references", "agent_policy",
            "bridge_policy", "worker_requests",
        })
        self.assertEqual(projection["protocol"], "reasonfirst-selected-startup-policy-v1")
        self.assertEqual(projection["scope"], "disposable-startup-observation")
        self.assertEqual(set(projection["agent_policy"]), {
            "gitlab_base_url", "api_verify_ssl", "api_trust_env", "git_trust_env",
            "allowed_projects", "require_write_allowlist", "branch_prefix",
            "default_base_ref", "allowed_executables", "command_timeout_seconds",
            "max_output_bytes", "max_file_bytes", "default_backend",
        })
        self.assertEqual(projection["transport"]["tool_admission"], "closed")
        self.assertEqual(len(self.state.configuration_digest(LAUNCH)), 64)

    def test_each_selected_agent_setting_binds_the_digest(self):
        changes = {
            "gitlab_base_url": "https://other.invalid",
            "api_verify_ssl": False, "api_trust_env": True, "git_trust_env": True,
            "allowed_projects": {"other/project"}, "require_write_allowlist": False,
            "branch_prefix": "review/", "default_base_ref": "develop",
            "allowed_executables": {"pytest"}, "command_timeout_seconds": 31,
            "max_output_bytes": 4097, "max_file_bytes": 65537, "default_backend": "copilot",
            "config_file": self.root / "other.env", "workspace_root": self.root / "other-workspaces",
            "api_ca_bundle": self.root / "other-ca.pem",
            "codex_model": "synthetic-other-model", "codex_reasoning_effort": "low",
            "codex_execution_mode": "exec", "codex_sandbox_mode": "read-only",
            "codex_approval_policy": "never", "codex_network_access": True,
            "copilot_model": "synthetic-copilot", "copilot_reasoning_effort": "high",
            "copilot_execution_mode": "programmatic", "copilot_disable_builtin_mcps": False,
            "copilot_allow_tools": ("read",), "copilot_deny_tools": ("write",),
        }
        expected = self.state.configuration_digest(LAUNCH)
        for field, value in changes.items():
            with self.subTest(field=field):
                changed = self.capture(replace(self.state.settings, **{field: value}))
                self.assertNotEqual(changed.configuration_digest(LAUNCH), expected)

    def test_transport_and_actual_configuration_references_bind_the_digest(self):
        expected = self.state.configuration_digest(LAUNCH)
        for field, value in {
            "host": "::1", "port": 8766, "path": "/other", "mode": "full-chat",
        }.items():
            with self.subTest(field=field):
                self.assertNotEqual(self.state.configuration_digest(replace(LAUNCH, **{field: value})), expected)
        for field in ("bridge_config_path", "state_dir"):
            changed = replace(self.state, **{field: self.root / ("other-" + field)})
            self.assertNotEqual(changed.configuration_digest(LAUNCH), expected)

    def test_credentials_author_metadata_and_arbitrary_legacy_config_are_excluded(self):
        secret = "synthetic-private-value-do-not-serialize"
        settings = replace(
            self.state.settings, api_token=secret, git_token=secret,
            git_username=secret, git_author_name=secret, git_author_email=secret,
        )
        config = self.state.bridge_configuration()
        config["control"] = {"token": secret, "other": {"secret": secret}}
        config["unrelated"] = secret
        captured = self.capture(settings, config)
        self.assertEqual(captured.configuration_digest(LAUNCH), self.state.configuration_digest(LAUNCH))
        self.assertNotIn(secret, json.dumps(captured.projection(LAUNCH)))
        self.assertNotIn(secret, repr(captured))
        env_file = self.state.settings.config_file
        env_file.write_text(env_file.read_text(encoding="utf-8").replace(
            "GITLAB_TOKEN=\n", "GITLAB_TOKEN=" + secret + "\n",
        ) + "\n# changed comment\n", encoding="utf-8")
        self.assertEqual(self.child_load()["digest"], self.state.configuration_digest(LAUNCH))

    def test_actual_loader_precedence_is_preserved_and_changed_policy_is_detected(self):
        observed = self.child_load({"GITLAB_COMMAND_TIMEOUT_SECONDS": "71"})
        self.assertEqual(observed["projection"]["agent_policy"]["command_timeout_seconds"], 71)
        self.assertNotEqual(observed["digest"], self.state.configuration_digest(LAUNCH))
        env_file = self.state.settings.config_file
        env_file.write_text(env_file.read_text(encoding="utf-8").replace(
            "GITLAB_COMMAND_TIMEOUT_SECONDS=30\n", "GITLAB_COMMAND_TIMEOUT_SECONDS=72\n",
        ), encoding="utf-8")
        self.assertEqual(self.child_load()["projection"]["agent_policy"]["command_timeout_seconds"], 72)
        self.assertEqual(self.child_load({"GITLAB_COMMAND_TIMEOUT_SECONDS": "71"})["projection"]["agent_policy"]["command_timeout_seconds"], 71)

    def test_snapshot_detaches_mutable_settings_and_nested_bridge_configuration(self):
        projects, commands, tools = {"owned/project"}, {"python"}, ["read"]
        settings = replace(self.state.settings, allowed_projects=projects,
                           allowed_executables=commands, copilot_allow_tools=tools)
        config = self.state.bridge_configuration()
        config["targets"]["remote"] = {
            "type": "ssh", "host": "synthetic-host", "repo": "/owned/repo",
            "validation": {"engine": "docker", "image": "fixture:1", "allowed_executables": ["pytest"]},
        }
        captured = self.capture(settings, config)
        digest = captured.configuration_digest(LAUNCH)
        projects.add("unapproved/project"); commands.add("unexpected"); tools.append("write")
        config["defaults"]["target"] = "remote"
        config["targets"]["remote"]["validation"]["allowed_executables"].append("unexpected")
        self.assertEqual(captured.configuration_digest(LAUNCH), digest)
        self.assertEqual(captured.configured_target("remote").validation_allowed_executables, ("pytest",))
        with self.assertRaises(AttributeError):
            captured.settings.allowed_projects.add("unexpected")
        with self.assertRaises(FrozenInstanceError):
            captured.configured_target("remote").host = "unexpected"
        view = captured.bridge_configuration()
        view["targets"]["remote"]["validation"]["allowed_executables"].append("other")
        self.assertEqual(captured.configuration_digest(LAUNCH), digest)

    def test_unordered_set_and_target_map_order_do_not_affect_digest(self):
        config = self.state.bridge_configuration()
        config["targets"]["second"] = {"type": "local", "codex_backend": "standalone-local"}
        a = self.capture(replace(self.state.settings, allowed_projects={"a/b", "c/d"}), config)
        config["targets"] = dict(reversed(tuple(config["targets"].items())))
        b = self.capture(replace(self.state.settings, allowed_projects={"c/d", "a/b"}), config)
        self.assertEqual(a.configuration_digest(LAUNCH), b.configuration_digest(LAUNCH))

    def test_each_resolved_target_policy_field_binds_the_digest(self):
        config = self.state.bridge_configuration()
        config["targets"]["remote"] = {
            "type": "ssh", "name": "remote", "host": "synthetic-host", "repo": "/owned/repo",
            "codex_backend": "desktop-proxy", "remote_codex": "codex", "ssh_connect_timeout": 8,
            "network_access": False,
            "validation": {"engine": "docker", "image": "fixture:1", "allowed_executables": ["pytest"], "network_access": False},
        }
        expected = self.capture(config=config).configuration_digest(LAUNCH)
        for field, value in {
            "type": "local", "name": "other-name", "host": "other-host", "repo": "/other/repo",
            "codex_backend": "remote-ssh", "remote_codex": "other-codex", "ssh_connect_timeout": 9,
            "network_access": True,
        }.items():
            with self.subTest(field=field):
                changed = json.loads(json.dumps(config))
                changed["targets"]["remote"][field] = value
                self.assertNotEqual(self.capture(config=changed).configuration_digest(LAUNCH), expected)
        for field, value in {
            "engine": "podman", "image": "fixture:2", "allowed_executables": ["python"], "network_access": True,
        }.items():
            with self.subTest(validation_field=field):
                changed = json.loads(json.dumps(config))
                changed["targets"]["remote"]["validation"][field] = value
                self.assertNotEqual(self.capture(config=changed).configuration_digest(LAUNCH), expected)
        for field, value in {"target": "remote", "codex_backend": "desktop-proxy"}.items():
            changed = json.loads(json.dumps(config)); changed["defaults"][field] = value
            self.assertNotEqual(self.capture(config=changed).configuration_digest(LAUNCH), expected)

    def test_loaded_bridge_version_is_not_silently_relabelled(self):
        for version in (None, True, "4", 3, 999):
            with self.subTest(version=version):
                config = self.state.bridge_configuration(); config["version"] = version
                with self.assertRaisesRegex(s.StartupStateError, "^invalid_configuration_version$"):
                    self.capture(config=config)

    def test_projection_failure_is_bounded_and_does_not_echo_values(self):
        secret = "synthetic-private-value"
        with self.assertRaisesRegex(s.StartupStateError, "^invalid_configuration_policy$"):
            self.capture(replace(self.state.settings, gitlab_base_url="https://user:" + secret + "@example.invalid"))
        too_large = self.capture(replace(self.state.settings, codex_model="x" * (s.MAX_PROJECTION_BYTES + 1)))
        with self.assertRaisesRegex(s.StartupStateError, "^configuration_projection_too_large$"):
            too_large.configuration_digest(LAUNCH)

    def test_managed_controller_consumes_same_snapshot_without_loading_or_fallback(self):
        with (patch.object(AgentSettings, "load", side_effect=AssertionError("unexpected reload")),
              patch.object(controller_module, "load_bridge_config", side_effect=AssertionError("unexpected reload"))):
            ctrl = BridgeController(managed_startup=self.state)
            try:
                self.assertIs(ctrl.managed_startup_state, self.state)
                self.assertEqual(ctrl.state_dir, self.state.state_dir)
                self.assertEqual(ctrl.state_file, self.state.state_dir / "state.json")
                self.assertIs(ctrl._settings_for_operation(), self.state.settings)
                self.assertIs(ctrl._effective_codex_policy(), self.state.codex_policy)
                self.assertIs(ctrl._requested_target(), self.state.configured_target())
                ctrl.bridge_config["targets"]["local"]["network_access"] = True
                self.assertFalse(ctrl._requested_target().network_access)
                self.assertFalse(ctrl._target_from_dict("local").network_access)
                with self.assertRaisesRegex(BridgeError, "^startup_observation_only$"):
                    ctrl.assert_tool_admitted()
                with self.assertRaisesRegex(BridgeError, "^managed_configuration_failed$"):
                    ctrl._remote_manager(ctrl._requested_target())
            finally:
                ctrl.close()

    def test_unmanaged_controller_keeps_loaders_and_reload_behavior(self):
        with (patch.dict(os.environ, dict(self.fixture.environment), clear=True),
              patch.object(controller_module, "load_bridge_config", wraps=load_bridge_config) as bridge_loader):
            ctrl = BridgeController()
        try:
            self.assertIsNone(ctrl.managed_startup_state)
            ctrl.assert_tool_admitted()
            bridge_loader.assert_called_once_with()
            other = replace(self.state.settings, codex_model="later-model")
            with patch.object(AgentSettings, "load", side_effect=[self.state.settings, other]) as loader:
                self.assertEqual(ctrl._effective_codex_policy().model, self.state.settings.codex_model)
                self.assertEqual(ctrl._effective_codex_policy().model, "later-model")
            self.assertEqual(loader.call_count, 2)
            with patch.object(AgentSettings, "load", side_effect=RuntimeError("fixture failure")):
                remote = self.state.configured_target()
                remote = replace(remote, type="ssh", host="synthetic", repo="/owned")
                self.assertEqual(ctrl._remote_manager(remote).allowed_executables, set())
        finally:
            ctrl.close()

    def test_real_sdk_catalog_is_identical_and_every_managed_tool_call_is_blocked(self):
        methods = (
            "doctor", "target_probe", "dispatch_request", "workspace_status", "files", "read", "diff",
            "start_codex", "continue_task", "steer", "interrupt", "compact_status", "events",
            "pending_approvals", "resolve_approval", "finish_preview", "finish", "ci", "evidence",
            "review_bundle", "artifacts",
        )

        async def check(mode):
            managed = BridgeController(managed_startup=self.state)
            with patch.dict(os.environ, dict(self.fixture.environment), clear=True):
                ordinary = BridgeController()
            try:
                observer = bridge_mcp.build_server(read_only_mode=mode, controller=managed)
                reference = bridge_mcp.build_server(read_only_mode=mode, controller=ordinary)
                actual = await observer.list_tools(); expected = await reference.list_tools()
                self.assertEqual(
                    [tool.model_dump(mode="json") for tool in actual],
                    [tool.model_dump(mode="json") for tool in expected],
                )
                self.assertIs(observer._reasonfirst_controller, managed)
                with ExitStack() as stack:
                    mocks = [stack.enter_context(patch.object(managed, name, side_effect=AssertionError("tool executed"))) for name in methods]
                    for tool in actual:
                        schema = tool.input_schema
                        args = {name: (1 if schema["properties"][name].get("type") == "integer" else "synthetic")
                                for name in schema.get("required", [])}
                        with self.subTest(mode=mode, tool=tool.name):
                            with self.assertRaises(Exception) as error:
                                await observer.call_tool(tool.name, args)
                            cause = error.exception
                            while cause.__cause__ is not None:
                                cause = cause.__cause__
                            self.assertIsInstance(cause, BridgeError)
                            self.assertEqual(str(cause), "startup_observation_only")
                    for mock in mocks:
                        mock.assert_not_called()
            finally:
                managed.close(); ordinary.close()

        for mode in (True, False):
            asyncio.run(check(mode))


if __name__ == "__main__":
    unittest.main()
