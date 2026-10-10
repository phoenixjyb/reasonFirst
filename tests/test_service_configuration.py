from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError, replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gitlab_agent.bridge_http import HTTPLaunch
from gitlab_agent.bridge_preview import bridge_config as bridge_config_module
from gitlab_agent import config as config_module
from gitlab_agent.config import AgentSettings
from gitlab_agent.upgrade import service_configuration as configuration
from gitlab_agent.upgrade.startup_state import capture_startup_state


LAUNCH = HTTPLaunch("127.0.0.1", 8765, "/mcp", "read-only", "disabled")


class ServiceConfigurationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rf-service-configuration-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.settings = AgentSettings(
            config_file=self.root / "agent.env", gitlab_base_url="https://gitlab.example.invalid",
            api_token="", api_verify_ssl=True, api_trust_env=False,
            git_token="", git_username="oauth2", git_trust_env=False,
            allowed_projects={"owned/project"}, require_write_allowlist=True,
            workspace_root=self.root / "workspaces", branch_prefix="chatgpt/",
            default_base_ref="main", allowed_executables={"python", "pytest"},
            command_timeout_seconds=300, max_output_bytes=120000, max_file_bytes=1000000,
            git_author_name=None, git_author_email=None,
        )
        self.bridge_config = {
            "version": 4,
            "defaults": {"target": "local", "codex_backend": "global-config-local"},
            "targets": {
                "local": {"type": "local", "codex_backend": "global-config-local"},
                "remote": {
                    "type": "ssh", "host": "synthetic-host", "repo": "/owned/repository",
                    "codex_backend": "desktop-proxy", "ssh_connect_timeout": 8,
                    "network_access": False,
                    "validation": {"engine": "docker", "image": "fixture:1",
                                   "allowed_executables": ["pytest"], "network_access": False},
                },
            },
        }

    def capture(self, settings=None, bridge_config=None, **kwargs):
        return configuration.capture_service_configuration(
            self.settings if settings is None else settings,
            self.bridge_config if bridge_config is None else bridge_config,
            bridge_config_path=kwargs.pop("bridge_config_path", self.root / "bridge.yaml"),
            state_dir=kwargs.pop("state_dir", self.root / "state"),
            **kwargs,
        )

    def assert_code(self, code, fn, *args, **kwargs):
        with self.assertRaises(configuration.ServiceConfigurationError) as failure:
            fn(*args, **kwargs)
        self.assertEqual(failure.exception.code, code)
        self.assertEqual(str(failure.exception), code)
        return failure.exception

    def test_projection_has_service_domain_and_explicit_allowlisted_sections(self):
        captured = self.capture(approval_timeout_seconds=60, gitlab_auth_mode="git-only")
        projection = captured.projection(LAUNCH)
        self.assertEqual(set(projection), {
            "protocol", "scope", "transport", "references", "agent_policy",
            "bridge_policy", "worker_requests", "service_policy",
        })
        self.assertEqual(projection["protocol"], "reasonfirst-selected-service-policy-v1")
        self.assertEqual(projection["scope"], "selected-parent-service-policy")
        self.assertEqual(projection["service_policy"], {
            "approval_timeout_seconds": 60, "gitlab_auth_mode": "git-only",
        })
        self.assertNotIn("tool_admission", projection["transport"])
        self.assertNotIn("disposable", json.dumps(projection))
        self.assertEqual(set(projection["agent_policy"]), {
            "gitlab_base_url", "api_verify_ssl", "api_trust_env", "git_trust_env",
            "allowed_projects", "require_write_allowlist", "branch_prefix", "default_base_ref",
            "allowed_executables", "command_timeout_seconds", "max_output_bytes", "max_file_bytes",
            "default_backend",
        })
        observer = capture_startup_state(self.settings, self.bridge_config,
                                         bridge_config_path=self.root / "bridge.yaml", state_dir=self.root / "state")
        self.assertNotEqual(captured.configuration_digest(LAUNCH), observer.configuration_digest(LAUNCH))
        self.assertEqual(len(captured.configuration_digest(LAUNCH)), 64)

    def test_detaches_original_settings_collections_and_nested_target_policy(self):
        projects, commands, tools = {"owned/project"}, {"python"}, ["read"]
        settings = replace(self.settings, allowed_projects=projects,
                           allowed_executables=commands, copilot_allow_tools=tools)
        captured = self.capture(settings)
        digest = captured.configuration_digest(LAUNCH)
        projects.add("unapproved/project")
        commands.add("unexpected")
        tools.append("write")
        self.bridge_config["defaults"]["target"] = "remote"
        self.bridge_config["targets"]["remote"]["host"] = "unexpected-host"
        self.bridge_config["targets"]["remote"]["validation"]["allowed_executables"].append("unexpected")
        self.assertEqual(captured.configuration_digest(LAUNCH), digest)
        self.assertEqual(captured.configured_target().type, "local")
        self.assertEqual(captured.configured_target("remote").host, "synthetic-host")
        self.assertEqual(captured.configured_target("remote").validation_allowed_executables, ("pytest",))
        self.assertEqual(captured.copilot_policy.allow_tools, ("read",))
        self.assertIsNot(captured.settings, settings)
        self.assertIs(type(captured.settings.allowed_projects), frozenset)
        self.assertIs(type(captured.settings.allowed_executables), frozenset)

    def test_all_returned_views_are_detached_and_retained_policy_is_frozen(self):
        captured = self.capture()
        digest = captured.configuration_digest(LAUNCH)
        view = captured.bridge_configuration()
        view["defaults"]["target"] = "remote"
        view["targets"]["remote"]["validation"]["allowed_executables"].append("unexpected")
        projection = captured.projection(LAUNCH)
        projection["agent_policy"]["allowed_projects"].append("unexpected/project")
        projection["worker_requests"]["copilot"]["allow_tools"].append("write")
        projection["service_policy"]["approval_timeout_seconds"] = 1800
        self.assertEqual(captured.configuration_digest(LAUNCH), digest)
        with self.assertRaises(FrozenInstanceError):
            captured.approval_timeout_seconds = 1800
        with self.assertRaises(FrozenInstanceError):
            captured.configured_target("remote").host = "unexpected"
        with self.assertRaises(FrozenInstanceError):
            captured.codex_policy.model = "unexpected"
        with self.assertRaises(AttributeError):
            captured.settings.allowed_projects.add("unexpected/project")

    def test_excluded_secrets_and_private_metadata_do_not_change_public_identity(self):
        secret = "synthetic-private-marker-not-for-projection"
        original = self.capture()
        settings = replace(self.settings, api_token=secret, git_token=secret,
                           git_username=secret, git_author_name=secret, git_author_email=secret)
        self.bridge_config["control"] = {"token": secret, "nested": {"value": secret}}
        self.bridge_config["unrelated"] = secret
        other = self.capture(settings)
        self.assertIsNot(original, other)
        self.assertNotEqual(original, other)
        self.assertEqual(original.configuration_digest(LAUNCH), other.configuration_digest(LAUNCH))
        self.assertEqual(other.settings.api_token, secret)
        self.assertEqual(other.settings.git_token, secret)
        public = json.dumps(other.projection(LAUNCH)) + repr(other) + str(other)
        self.assertNotIn(secret, public)
        for excluded in ("api_token", "git_token", "git_username", "git_author_name", "git_author_email"):
            self.assertNotIn(excluded, public)

    def test_capture_and_projection_use_no_loaders_file_contents_or_environment(self):
        synthetic_environment = {
            "RF_BRIDGE_CONFIG": str(self.root / "unselected.yaml"),
            "GITLAB_AGENT_ENV_FILE": str(self.root / "unselected.env"),
            "RF_GITLAB_AUTH_MODE": "api", "RF_APPROVAL_TIMEOUT_SECONDS": "1800",
            "RF_MCP_READ_ONLY": "false", "RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH": "true",
        }
        with (patch.dict(os.environ, synthetic_environment, clear=True),
              patch.object(AgentSettings, "load", side_effect=AssertionError("unexpected loader")),
              patch.object(config_module, "load_env_file", side_effect=AssertionError("unexpected loader")),
              patch.object(bridge_config_module, "load_bridge_config", side_effect=AssertionError("unexpected loader")),
              patch.object(os, "getenv", side_effect=AssertionError("unexpected environment read")) as getenv,
              patch.object(Path, "read_text", side_effect=AssertionError("unexpected file read")),
              patch.object(Path, "read_bytes", side_effect=AssertionError("unexpected file read")),
              patch.object(Path, "open", side_effect=AssertionError("unexpected file read"))):
            captured = self.capture()
            captured.projection(LAUNCH)
            captured.configuration_digest(LAUNCH)
            captured.configured_target("remote")
            captured.bridge_configuration()
            self.assertEqual(dict(os.environ), synthetic_environment)
            self.assertEqual(captured.gitlab_auth_mode, "auto")
            self.assertEqual(captured.approval_timeout_seconds, 300)
            invalid = copy.deepcopy(self.bridge_config)
            invalid["defaults"]["target"] = "unknown-target"
            self.assert_code("invalid_configuration_policy", self.capture, bridge_config=invalid)
            getenv.assert_not_called()

    def test_changing_referenced_files_does_not_redefine_captured_objects(self):
        self.settings.config_file.write_text("synthetic original settings\n", encoding="utf-8")
        bridge_file = self.root / "bridge.yaml"
        bridge_file.write_text("synthetic original bridge policy\n", encoding="utf-8")
        captured = self.capture()
        digest = captured.configuration_digest(LAUNCH)
        self.settings.config_file.write_text("synthetic replacement settings\n", encoding="utf-8")
        bridge_file.write_text("synthetic replacement bridge policy\n", encoding="utf-8")
        self.assertEqual(captured.configuration_digest(LAUNCH), digest)
        self.assertEqual(captured.settings.allowed_projects, frozenset({"owned/project"}))

    def test_each_selected_agent_setting_changes_the_projection(self):
        changes = {
            "gitlab_base_url": "https://other.example.invalid", "api_verify_ssl": False,
            "api_trust_env": True, "git_trust_env": True, "allowed_projects": {"other/project"},
            "require_write_allowlist": False, "branch_prefix": "review/", "default_base_ref": "develop",
            "allowed_executables": {"make"}, "command_timeout_seconds": 301,
            "max_output_bytes": 120001, "max_file_bytes": 1000001, "default_backend": "copilot",
            "config_file": self.root / "other.env", "workspace_root": self.root / "other-workspaces",
            "api_ca_bundle": self.root / "ca.pem", "codex_model": "synthetic-other-model",
            "codex_reasoning_effort": "low", "codex_execution_mode": "exec",
            "codex_sandbox_mode": "read-only", "codex_approval_policy": "never",
            "codex_network_access": True, "copilot_model": "synthetic-copilot",
            "copilot_reasoning_effort": "low", "copilot_execution_mode": "programmatic",
            "copilot_disable_builtin_mcps": False, "copilot_allow_tools": ("read",),
            "copilot_deny_tools": ("write",),
        }
        expected = self.capture().configuration_digest(LAUNCH)
        for field, value in changes.items():
            with self.subTest(field=field):
                changed = self.capture(replace(self.settings, **{field: value}))
                self.assertNotEqual(changed.configuration_digest(LAUNCH), expected)

    def test_selected_transport_service_options_and_references_bind_the_digest(self):
        captured = self.capture()
        expected = captured.configuration_digest(LAUNCH)
        for field, value in {"host": "::1", "port": 8766, "path": "/other", "mode": "full-chat"}.items():
            with self.subTest(field=field):
                self.assertNotEqual(captured.configuration_digest(replace(LAUNCH, **{field: value})), expected)
        for changes in ({"approval_timeout_seconds": 60}, {"gitlab_auth_mode": "api"},
                        {"gitlab_auth_mode": "git-only"}, {"state_dir": self.root / "other-state"},
                        {"bridge_config_path": self.root / "other.yaml"}):
            with self.subTest(changes=changes):
                self.assertNotEqual(self.capture(**changes).configuration_digest(LAUNCH), expected)

    def test_normalized_target_policies_and_default_target_bind_the_digest(self):
        expected = self.capture().configuration_digest(LAUNCH)
        changes = {
            "host": "other-host", "repo": "/other/repository", "name": "other-name",
            "codex_backend": "remote-ssh", "remote_codex": "other-codex",
            "ssh_connect_timeout": 9, "network_access": True,
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                config = copy.deepcopy(self.bridge_config)
                config["targets"]["remote"][field] = value
                self.assertNotEqual(self.capture(bridge_config=config).configuration_digest(LAUNCH), expected)
        for field, value in {"engine": "podman", "image": "fixture:2",
                             "allowed_executables": ["python"], "network_access": True}.items():
            with self.subTest(validation=field):
                config = copy.deepcopy(self.bridge_config)
                config["targets"]["remote"]["validation"][field] = value
                self.assertNotEqual(self.capture(bridge_config=config).configuration_digest(LAUNCH), expected)
        config = copy.deepcopy(self.bridge_config)
        config["defaults"]["target"] = "remote"
        self.assertEqual(self.capture(bridge_config=config).configured_target().host, "synthetic-host")
        self.assertNotEqual(self.capture(bridge_config=config).configuration_digest(LAUNCH), expected)

    def test_unordered_inputs_and_semantic_normalization_are_deterministic(self):
        original = self.capture(replace(self.settings, allowed_projects={"a/b", "c/d"}))
        config = copy.deepcopy(self.bridge_config)
        config["targets"] = dict(reversed(list(config["targets"].items())))
        config["targets"]["remote"]["type"] = " SSH "
        config["targets"]["remote"]["validation"]["allowed_executables"] = ["pytest", "pytest"]
        equivalent = self.capture(replace(self.settings, allowed_projects=["c/d", "a/b"]), config)
        self.assertEqual(original.configuration_digest(LAUNCH), equivalent.configuration_digest(LAUNCH))

    def test_factory_is_required_and_selection_failures_do_not_reflect_input(self):
        secret = "synthetic-private-invalid-object"
        self.assert_code("invalid_configuration_objects", configuration.ManagedServiceConfiguration)
        self.assert_code("invalid_configuration_objects", configuration.ManagedServiceConfiguration, secret)
        self.assert_code("invalid_configuration_objects", configuration.capture_service_configuration,
                         object(), {}, bridge_config_path=self.root, state_dir=self.root)
        captured = self.capture()
        self.assertIs(captured.configured_target(), captured.configured_target(""))
        for invalid in ({"host": secret}, secret, 1, True, [secret], "host:/arbitrary"):
            self.assert_code("invalid_configuration_policy", captured.configured_target, invalid)
        self.assertEqual(configuration.ServiceConfigurationError(secret).code, "invalid_configuration_objects")
        self.assertNotIn(secret, str(configuration.ServiceConfigurationError(secret)))

    def test_settings_scalar_types_choices_and_bounds_are_strict(self):
        class StringSubclass(str):
            pass

        invalid = {
            "api_verify_ssl": 1, "git_trust_env": "false", "require_write_allowlist": None,
            "command_timeout_seconds": True, "max_output_bytes": 0, "max_file_bytes": 2**31,
            "api_token": [], "git_author_name": 123, "git_username": "x" * (16 * 1024 + 1),
            "default_backend": "unexpected", "codex_model": StringSubclass("synthetic"),
            "codex_reasoning_effort": "unexpected", "copilot_model": "",
            "copilot_reasoning_effort": "unexpected", "codex_execution_mode": "unexpected",
            "codex_sandbox_mode": "danger-full-access", "codex_approval_policy": "always",
            "codex_network_access": "false", "copilot_execution_mode": "unexpected",
            "copilot_disable_builtin_mcps": 1, "copilot_allow_tools": {"read"},
            "copilot_deny_tools": [123], "allowed_projects": "owned/project",
            "allowed_executables": {""}, "branch_prefix": "with space/",
            "default_base_ref": "main\nother",
            "gitlab_base_url": "https://" + "user:synthetic-private" + "@example.invalid",
        }
        for field, value in invalid.items():
            with self.subTest(field=field):
                self.assert_code("invalid_configuration_policy", self.capture,
                                 replace(self.settings, **{field: value}))
        for value in (0, -1, True, 1.5, float("inf"), float("nan"), "300"):
            self.assert_code("invalid_configuration_policy", self.capture,
                             replace(self.settings, command_timeout_seconds=value))

    def test_bridge_known_scalar_types_and_resolver_failures_are_bounded(self):
        self.assert_code("invalid_configuration_objects", self.capture, bridge_config=[])
        for config in ({}, {"version": True, "defaults": {}, "targets": {}},
                       {"version": 4, "defaults": [], "targets": {}},
                       {"version": 4, "defaults": {}, "targets": []}):
            self.assert_code("invalid_configuration_policy", self.capture, bridge_config=config)
        for field, value in {"type": 123, "host": [], "repo": True, "codex_backend": "unexpected",
                             "ssh_connect_timeout": 0, "network_access": "false"}.items():
            with self.subTest(field=field):
                config = copy.deepcopy(self.bridge_config)
                config["targets"]["remote"][field] = value
                self.assert_code("invalid_configuration_policy", self.capture, bridge_config=config)
        for field, value in {"engine": 123, "image": "-invalid", "network_access": 1,
                             "allowed_executables": ("pytest",)}.items():
            with self.subTest(validation=field):
                config = copy.deepcopy(self.bridge_config)
                config["targets"]["remote"]["validation"][field] = value
                self.assert_code("invalid_configuration_policy", self.capture, bridge_config=config)
        config = copy.deepcopy(self.bridge_config)
        config["defaults"]["target"] = "unknown-target"
        self.assert_code("invalid_configuration_policy", self.capture, bridge_config=config)

    def test_nondefault_shorthand_target_name_cannot_override_configured_mapping(self):
        for name in ("alias:/shorthand/repository", "  alias:/shorthand/repository  "):
            with self.subTest(name=name):
                config = copy.deepcopy(self.bridge_config)
                config["targets"][name] = {
                    "type": "ssh", "host": "configured-host", "repo": "/configured/repository",
                    "codex_backend": "remote-ssh",
                }
                self.assertEqual(config["defaults"]["target"], "local")
                with patch.object(configuration, "capture_startup_state",
                                  side_effect=AssertionError("unexpected normalization")) as normalize:
                    self.assert_code("invalid_configuration_policy", self.capture, bridge_config=config)
                normalize.assert_not_called()

    def test_absolute_bounded_path_objects_are_required_and_normalized(self):
        for path in ("/not-a-Path", Path("relative"), Path("~/ambient-home"), self.root / ("x" * 4097)):
            for field in ("bridge_config_path", "state_dir"):
                with self.subTest(field=field, value_type=type(path).__name__):
                    self.assert_code("invalid_configuration_reference", self.capture, **{field: path})
            for field in ("config_file", "workspace_root", "api_ca_bundle"):
                with self.subTest(field=field, value_type=type(path).__name__):
                    self.assert_code("invalid_configuration_reference", self.capture,
                                     replace(self.settings, **{field: path}))
        captured = self.capture(state_dir=self.root / "missing" / ".." / "state")
        self.assertEqual(captured.state_dir, self.root / "state")
        self.assertFalse(captured.state_dir.exists())

    def test_service_option_values_and_launch_objects_are_strict(self):
        for timeout in (29, 1801, True, 300.0, "300", None):
            self.assert_code("invalid_configuration_policy", self.capture, approval_timeout_seconds=timeout)
        for mode in ("API", "git_only", "password", "", True, None):
            self.assert_code("invalid_configuration_policy", self.capture, gitlab_auth_mode=mode)
        for timeout in (30, 1800):
            self.assertEqual(self.capture(approval_timeout_seconds=timeout).approval_timeout_seconds, timeout)
        captured = self.capture()
        self.assert_code("invalid_configuration_launch", captured.projection, object())
        for field, value in {"host": "localhost", "port": True, "path": "/healthz",
                             "mode": "unexpected", "control_policy": "legacy"}.items():
            self.assert_code("invalid_configuration_launch", captured.configuration_digest,
                             replace(LAUNCH, **{field: value}))

    def test_collection_string_and_encoded_projection_limits(self):
        for field, value in {
            "allowed_projects": {str(index) for index in range(513)},
            "copilot_allow_tools": ["read"] * 513,
            "codex_model": "x" * 4097,
        }.items():
            self.assert_code("invalid_configuration_policy", self.capture,
                             replace(self.settings, **{field: value}))
        config = copy.deepcopy(self.bridge_config)
        config["targets"] = {str(index): {"type": "local"} for index in range(513)}
        self.assert_code("invalid_configuration_policy", self.capture, bridge_config=config)
        # Each value and the collection are bounded; the UTF-8 projection still
        # exceeds the byte limit. No credential material participates in sizing.
        large = self.capture(replace(self.settings, allowed_projects={
            str(index) + "/" + "\u4e2d" * 1000 for index in range(30)
        }))
        self.assert_code("configuration_projection_too_large", large.projection, LAUNCH)
        self.assert_code("configuration_projection_too_large", large.configuration_digest, LAUNCH)


if __name__ == "__main__":
    unittest.main()
