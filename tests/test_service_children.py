from __future__ import annotations

from contextlib import contextmanager
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from gitlab_agent.config import AgentSettings
from gitlab_agent.upgrade import service_children as c
from gitlab_agent.upgrade.service_configuration import capture_service_configuration


class ServiceChildrenTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rf-child-private-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.bin = self.root / "bin"
        self.home = self.root / "home"
        self.bin.mkdir()
        self.home.mkdir()
        self.suffix = ".exe" if os.name == "nt" else ""
        self.python = self.executable("selected-python" + self.suffix)
        self.settings = AgentSettings(
            config_file=self.root / "selected.env", gitlab_base_url="https://gitlab.example",
            api_token="private-settings-marker", api_verify_ssl=True, api_trust_env=False,
            git_token="private-git-marker", git_username="private-user", git_trust_env=False,
            allowed_projects={"owned/project"}, require_write_allowlist=True,
            workspace_root=self.root / "workspaces", branch_prefix="chatgpt/", default_base_ref="main",
            allowed_executables={"git", "python"}, command_timeout_seconds=30,
            max_output_bytes=10000, max_file_bytes=10000,
            git_author_name=None, git_author_email=None,
        )
        self.configuration = capture_service_configuration(
            self.settings, {
                "version": 4,
                "defaults": {"target": "local", "codex_backend": "global-config-local"},
                "targets": {"local": {"type": "local", "codex_backend": "global-config-local"}},
            },
            bridge_config_path=self.root / "bridge.yaml", state_dir=self.root / "state",
        )
        self.environment = {
            "PATH": str(self.bin), "HOME": str(self.home), "USERPROFILE": str(self.home),
            "CODEX_HOME": str(self.home / "selected-codex"),
            "SYNTHETIC_PROVIDER_TOKEN": "private-environment-marker",
        }

    def executable(self, name, *, directory=None, content=b"synthetic-executable-A"):
        path = (directory or self.bin) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o700)
        return path

    @contextmanager
    def selected(self, *, environment=None, windows=None, helper=False):
        with patch.dict(os.environ, self.environment if environment is None else environment, clear=True), \
                patch.object(sys, "executable", str(self.python)), \
                patch.object(Path, "cwd", return_value=self.root), \
                patch.object(c, "_WINDOWS", c._WINDOWS if windows is None else windows):
            yield (c.capture_helper_children(self.settings) if helper
                   else c.capture_service_children(self.configuration))

    def assert_code(self, code, action):
        with self.assertRaises(c.ServiceChildBindingError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)
        self.assertNotIn("private-", str(raised.exception))
        self.assertNotIn(str(self.root), repr(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        return raised.exception

    def test_capture_retains_exact_authority_and_detached_private_inputs(self):
        with self.selected() as context:
            self.assertIs(context.configuration, self.configuration)
            self.assertEqual(context.working_directory, self.root)
            self.assertEqual(context.python_invocation, str(self.python))
            self.assertEqual(context.environment_copy(), self.environment)
            context.environment_copy()["SYNTHETIC_PROVIDER_TOKEN"] = "changed"
            os.environ["SYNTHETIC_PROVIDER_TOKEN"] = "ambient-change"
            self.assertEqual(context.environment_copy()["SYNTHETIC_PROVIDER_TOKEN"], "private-environment-marker")
            with self.assertRaises(FrozenInstanceError):
                context._cwd = self.home
            self.assertIsNone(context.revalidate())

    def test_helper_capture_retains_exact_settings_instead_of_service_authority(self):
        with self.selected(helper=True) as context:
            self.assertIs(context.configuration, self.settings)
            self.assertIsNot(context.configuration, self.configuration)
            self.assertEqual(context.environment_copy(), self.environment)

    def test_wrong_factory_objects_reject_before_environment_or_file_reads(self):
        with patch.object(c, "_environment_snapshot", side_effect=AssertionError), \
                patch.object(c, "_observe_file", side_effect=AssertionError):
            for action in (lambda: c.capture_service_children(self.settings),
                           lambda: c.capture_helper_children(self.configuration),
                           lambda: c.capture_service_children(object()),
                           lambda: c.ServiceChildContext()):
                self.assert_code("invalid_child_context", action)

    def test_capture_does_not_resolve_optional_backends_or_launch_processes(self):
        with patch.object(c.ServiceChildContext, "_candidate", side_effect=AssertionError), \
                patch("subprocess.run", side_effect=AssertionError), \
                patch("subprocess.Popen", side_effect=AssertionError), \
                self.selected() as context:
            self.assertEqual(context.summary()["selected_executable_count"], 0)

    def test_summary_is_cached_detached_and_contains_no_private_identity(self):
        with self.selected() as context:
            with patch.object(c, "_observe_file", side_effect=AssertionError), \
                    patch.object(Path, "resolve", side_effect=AssertionError), \
                    patch.object(Path, "is_dir", side_effect=AssertionError):
                summary = context.summary()
                summary["environment_retained"] = False
                self.assertIs(context.summary()["environment_retained"], True)
            public = context.summary()
            self.assertEqual(set(public), {
                "schema_version", "scope", "environment_retained", "working_directory_retained",
                "python_invocation_observed", "selected_executable_count", "current_process_only",
                "child_runtime_verified", "provider_configuration_verified", "remote_runtime_verified",
                "desktop_daemon_verified", "activation_authorized",
            })
            self.assertEqual(public["schema_version"], 1)
            self.assertEqual(public["scope"], "owned-local-child-inputs")
            for key in ("child_runtime_verified", "provider_configuration_verified", "remote_runtime_verified",
                        "desktop_daemon_verified", "activation_authorized"):
                self.assertIs(public[key], False)
            rendered = repr(context) + json.dumps(public)
            for private in (str(self.root), "SYNTHETIC_PROVIDER_TOKEN", "private-environment-marker",
                            "private-settings-marker", "CODEX_HOME", "PATH"):
                self.assertNotIn(private, rendered)

    def test_wrong_pid_is_sticky_and_precedes_locks_or_filesystem_work(self):
        with self.selected() as context:
            previous_lock = context._lock
            class ForbiddenLock:
                def __enter__(self):
                    raise AssertionError("inherited lock was used")
                def __exit__(self, *args):
                    return False
            object.__setattr__(context, "_lock", ForbiddenLock())
            with patch.object(c.os, "getpid", return_value=context._pid + 1), \
                    patch.object(c, "_observe_file", side_effect=AssertionError):
                for action in (context.summary, context.revalidate, context.environment_copy,
                               lambda: context.configuration, lambda: context.working_directory,
                               lambda: context.python_invocation, lambda: context.resolve_executable("git"),
                               context.resolve_codex_binary, context.managed_app_server_socket):
                    self.assert_code("child_wrong_process", action)
            object.__setattr__(context, "_lock", previous_lock)
            self.assert_code("child_wrong_process", context.summary)

    def test_uninitialized_nominal_context_has_no_factory_authority(self):
        context = object.__new__(c.ServiceChildContext)
        self.assert_code("invalid_child_context", context.revalidate)

    def test_path_and_home_resolution_use_retained_values_without_ambient_lookup(self):
        selected = self.executable("git" + self.suffix)
        alternative = self.executable("git" + self.suffix, directory=self.root / "other")
        with self.selected() as context:
            os.environ["PATH"] = str(alternative.parent)
            os.environ["HOME"] = str(alternative.parent)
            with patch.object(c.os, "getenv", side_effect=AssertionError):
                self.assertEqual(Path(context.resolve_executable("git")), selected)
                self.assertEqual(context.managed_app_server_socket(),
                                 self.home / "selected-codex" / "app-server-control" / "app-server-control.sock")

    def test_relative_path_entries_are_relative_to_selected_child_cwd(self):
        working = self.root / "worker"
        selected = self.executable("git" + self.suffix, directory=working / "tools")
        env = {**self.environment, "PATH": "tools"}
        with self.selected(environment=env) as context:
            self.assertEqual(Path(context.resolve_executable("git", cwd=working)), selected)

    def test_optional_unavailability_preserves_prior_selections_and_context(self):
        selected = self.executable("git" + self.suffix)
        with self.selected() as context:
            self.assertEqual(Path(context.resolve_executable("git")), selected)
            previous = context.summary()
            self.assert_code("child_executable_unavailable", lambda: context.resolve_executable("missing-optional"))
            self.assertEqual(context.summary(), previous)
            self.assertEqual(Path(context.resolve_executable("git")), selected)

    def test_explicit_missing_codex_never_selects_an_installed_fallback(self):
        self.executable("codex" + self.suffix)
        env = {**self.environment, "CODEX_BRIDGE_CODEX_BIN": str(self.root / "missing-explicit")}
        with self.selected(environment=env) as context:
            for preferred in (False, True):
                self.assert_code("child_executable_unavailable", lambda: context.resolve_codex_binary(prefer_desktop=preferred))
            self.assertEqual(context.summary()["selected_executable_count"], 0)

    def test_explicit_codex_uses_retained_home_and_overrides_path(self):
        selected = self.executable("chosen", directory=self.home)
        self.executable("codex" + self.suffix)
        env = {**self.environment, "CODEX_BRIDGE_CODEX_BIN": "~/chosen"}
        with self.selected(environment=env) as context:
            os.environ["CODEX_BRIDGE_CODEX_BIN"] = "ambient-other"
            self.assertEqual(context.resolve_codex_binary(prefer_desktop=True), str(selected))

    def test_codex_resolver_retains_distinct_path_and_desktop_preference(self):
        path_choice = self.executable("codex" + self.suffix)
        desktop_choice = self.executable("desktop-codex")
        with self.selected() as context, \
                patch.object(c.ServiceChildContext, "_desktop_candidates", return_value=[desktop_choice]):
            self.assertEqual(Path(context.resolve_codex_binary()), path_choice)
            self.assertEqual(context.resolve_codex_binary(prefer_desktop=True), str(desktop_choice))
            self.assertEqual(context.summary()["selected_executable_count"], 2)

    def test_windows_pathext_and_current_directory_switch_are_retained(self):
        selected = self.executable("tool.FIRST")
        self.executable("tool.SECOND")
        self.executable("tool.FIRST", directory=self.root)
        env = {**self.environment, "PATHEXT": ".FIRST", "NoDefaultCurrentDirectoryInExePath": "1"}
        with self.selected(environment=env, windows=True) as context:
            os.environ["PATHEXT"] = ".SECOND"
            os.environ.pop("NoDefaultCurrentDirectoryInExePath", None)
            self.assertEqual(context.resolve_executable("tool"), str(selected))

    def test_absolute_approved_invocation_is_not_replaced_using_pathext(self):
        selected = self.executable("approved.exe")
        self.executable("approved.exe.FIRST")
        env = {**self.environment, "PATHEXT": ".FIRST"}
        with self.selected(environment=env, windows=True) as context:
            self.assertEqual(context.resolve_executable(str(selected)), str(selected))
            selected.unlink()
            self.assert_code("child_executable_changed", lambda: context.resolve_executable(str(selected)))

    def test_absolute_invocation_cache_is_independent_of_disposable_probe_cwd(self):
        selected = self.executable("approved-python" + self.suffix)
        directories = [self.root / "probe-one", self.root / "probe-two", self.root / "probe-three"]
        for directory in directories:
            directory.mkdir()
        with self.selected() as context, patch.object(c, "MAX_SELECTIONS", 1):
            for directory in directories:
                self.assertEqual(context.resolve_executable(str(selected), cwd=directory), str(selected))
            self.assertEqual(len(context._selections), 1)
            self.assertEqual(context.summary()["selected_executable_count"], 1)
            selected.write_bytes(b"synthetic-executable-B")
            self.assert_code("child_executable_changed", lambda: context.resolve_executable(str(selected), cwd=directories[0]))

    def test_cached_selection_is_pinned_when_a_higher_priority_file_appears(self):
        first = self.root / "first"
        first.mkdir()
        selected = self.executable("git" + self.suffix)
        env = {**self.environment, "PATH": str(first) + os.pathsep + str(self.bin)}
        with self.selected(environment=env) as context:
            self.assertEqual(Path(context.resolve_executable("git")), selected)
            self.executable("git" + self.suffix, directory=first)
            self.assertEqual(Path(context.resolve_executable("git")), selected)

    def test_observed_file_drift_is_sticky_and_cached_reuse_revalidates(self):
        selected = self.executable("git" + self.suffix)
        with self.selected() as context:
            context.resolve_executable("git")
            selected.write_bytes(b"synthetic-executable-B")
            self.assert_code("child_executable_changed", lambda: context.resolve_executable("git"))
            selected.write_bytes(b"synthetic-executable-A")
            with patch.object(c, "_observe_file", side_effect=AssertionError):
                for action in (context.revalidate, context.summary, context.environment_copy,
                               lambda: context.resolve_executable("new-choice")):
                    self.assert_code("child_executable_changed", action)

    def test_python_invocation_leaf_is_retained_even_with_shared_file_evidence(self):
        other = self.executable("python", directory=self.root / "other-venv" / "bin")
        shared = c._observe_file(self.python, c.MAX_EXECUTABLE_BYTES)
        with patch.object(c, "_observe_file", return_value=shared), self.selected() as first:
            previous = self.python
            self.python = other
            try:
                with self.selected() as second:
                    self.assertEqual(first.python_invocation, str(previous))
                    self.assertEqual(second.python_invocation, str(other))
                    self.assertNotEqual(first.python_invocation, second.python_invocation)
            finally:
                self.python = previous

    def test_environment_bounds_reject_before_any_executable_read(self):
        for name in ("MAX_ENV_ENTRIES", "MAX_ENV_NAME_BYTES", "MAX_ENV_VALUE_BYTES", "MAX_ENV_BYTES"):
            with self.subTest(bound=name), patch.object(c, name, 0), \
                    patch.object(c, "_observe_file", side_effect=AssertionError), \
                    patch.dict(os.environ, {"SYNTHETIC": "value"}, clear=True):
                self.assert_code("child_environment_limit", lambda: c.capture_service_children(self.configuration))

    def test_selection_limit_does_not_discard_existing_observations(self):
        selected = self.executable("git" + self.suffix)
        self.executable("ssh" + self.suffix)
        with self.selected() as context, patch.object(c, "MAX_SELECTIONS", 1):
            context.resolve_executable("git")
            self.assert_code("child_selection_limit", lambda: context.resolve_executable("ssh"))
            self.assertEqual(Path(context.resolve_executable("git")), selected)
            self.assertEqual(context.summary()["selected_executable_count"], 1)

    def test_changing_initial_executable_read_invalidates_context(self):
        selected = self.executable("git" + self.suffix)
        observe = c._observe_file
        with self.selected() as context:
            def changing(path, limit):
                if path == selected:
                    raise c.ServiceRuntimeError("runtime_file_changed")
                return observe(path, limit)
            with patch.object(c, "_observe_file", side_effect=changing):
                self.assert_code("child_executable_changed", lambda: context.resolve_executable("git"))
            self.assert_code("child_executable_changed", context.summary)

    def test_oversized_new_executable_is_unavailable_without_poisoning_existing_inputs(self):
        selected = self.executable("git" + self.suffix)
        observe = c._observe_file
        with self.selected() as context:
            def bounded(path, limit):
                return observe(path, 1 if path == selected else limit)
            with patch.object(c, "_observe_file", side_effect=bounded):
                self.assert_code("child_executable_unavailable", lambda: context.resolve_executable("git"))
            self.assertEqual(context.summary()["selected_executable_count"], 0)
            self.assertIsNone(context.revalidate())

    def test_unexpected_capture_errors_and_unknown_codes_are_confidential(self):
        with patch.object(c, "_environment_snapshot", side_effect=RuntimeError("private-environment-marker")):
            self.assert_code("child_capture_failed", lambda: c.capture_service_children(self.configuration))
        for unknown in ("private-environment-marker", None, {"private": "value"}):
            self.assertEqual(str(c.ServiceChildBindingError(unknown)), "child_binding_failed")


if __name__ == "__main__":
    unittest.main()
