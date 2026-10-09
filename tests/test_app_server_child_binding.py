from __future__ import annotations

from contextlib import contextmanager
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from gitlab_agent import codex_app_server as a
from gitlab_agent.config import AgentSettings
from gitlab_agent.upgrade import service_children as c


class FakeProcess:
    def __init__(self):
        self.stdin, self.stdout, self.stderr = io.StringIO(), io.StringIO(), io.StringIO()
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -1

    def wait(self, timeout=None):
        return self.returncode


class FakeThread:
    ident = None
    def __init__(self, *args, **kwargs):
        pass
    def start(self):
        pass
    def join(self, timeout=None):
        pass
    def is_alive(self):
        return False


class AppServerChildBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rf-app-private-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.bin, self.home, self.worker = self.root / "bin", self.root / "home", self.root / "worker"
        for directory in (self.bin, self.home, self.worker):
            directory.mkdir()
        suffix = ".exe" if os.name == "nt" else ""
        self.python = self.executable("python" + suffix)
        self.codex = self.executable("codex" + suffix)
        self.ssh = self.executable("ssh" + suffix)
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
        self.environment = {
            "PATH": str(self.bin), "HOME": str(self.home), "USERPROFILE": str(self.home),
            "CODEX_HOME": str(self.home / "codex"), "CODEX_BRIDGE_CODEX_BIN": str(self.codex),
            "SYNTHETIC_PROVIDER_TOKEN": "private-environment-marker",
        }

    def executable(self, name):
        path = self.bin / name
        path.write_bytes(b"synthetic-executable-A")
        path.chmod(0o700)
        return path

    @contextmanager
    def selected(self, *, environment=None):
        with patch.dict(os.environ, self.environment if environment is None else environment, clear=True), \
                patch.object(sys, "executable", str(self.python)), \
                patch.object(Path, "cwd", return_value=self.root):
            yield c.capture_helper_children(self.settings)

    @contextmanager
    def launches(self):
        process = FakeProcess()
        absent = SimpleNamespace(returncode=1, stdout="", stderr="No MCP server named 'reasonfirst' found.")
        with patch.object(a.subprocess, "Popen", return_value=process) as launch, \
                patch.object(a.subprocess, "run", return_value=absent) as probe, \
                patch.object(a.threading, "Thread", FakeThread), \
                patch.object(a.AppServerClient, "_initialize", return_value=None):
            yield launch, probe, process
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()

    def assert_code(self, code, action):
        with self.assertRaises(c.ServiceChildBindingError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)
        self.assertNotIn("private-", str(raised.exception))
        self.assertNotIn(str(self.root), repr(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        return raised.exception

    def test_global_probe_and_process_share_selected_binary_environment_and_worker_cwd(self):
        for configured in (False, True):
            with self.subTest(configured=configured), self.selected() as context, self.launches() as (launch, probe, process):
                if configured:
                    probe.return_value = SimpleNamespace(returncode=0, stdout='{"transport":{"type":"stdio"}}', stderr="")
                os.environ["SYNTHETIC_PROVIDER_TOKEN"] = "ambient-changed"
                os.environ["CODEX_BRIDGE_CODEX_BIN"] = str(self.root / "ambient-other")
                app = a.AppServerClient.global_config_local(cwd=str(self.worker), child_context=context)
                try:
                    self.assertIs(app.child_context, context)
                    self.assertEqual(probe.call_args.args[0], [str(self.codex), "mcp", "get", "reasonfirst", "--json"])
                    expected = [str(self.codex)]
                    if configured:
                        expected += ["--config", "mcp_servers.reasonfirst.enabled=false"]
                    self.assertEqual(launch.call_args.args[0], expected + ["app-server"])
                    for call in (probe.call_args, launch.call_args):
                        self.assertEqual(call.kwargs["env"], self.environment)
                        self.assertEqual(call.kwargs["cwd"], self.worker)
                    self.assertIsNot(probe.call_args.kwargs["env"], launch.call_args.kwargs["env"])
                    self.assertEqual(os.environ["SYNTHETIC_PROVIDER_TOKEN"], "ambient-changed")
                finally:
                    app.close()

    def test_bound_standalone_honors_explicit_worker_cwd(self):
        with self.selected() as context, self.launches() as (launch, probe, process):
            app = a.AppServerClient(child_context=context, cwd=self.worker)
            try:
                self.assertEqual(launch.call_args.args[0], [str(self.codex), "app-server"])
                self.assertEqual(launch.call_args.kwargs["cwd"], self.worker)
                self.assertEqual(launch.call_args.kwargs["env"], self.environment)
                probe.assert_not_called()
            finally:
                app.close()

    def test_bound_omitted_cwd_uses_retained_parent_directory(self):
        with self.selected() as context, self.launches() as (launch, probe, process):
            app = a.AppServerClient(child_context=context)
            try:
                self.assertEqual(launch.call_args.kwargs["cwd"], self.root)
            finally:
                app.close()

    def test_invalid_context_factories_reject_before_resolution_or_processes(self):
        with self.launches() as (launch, probe, process), \
                patch.object(a, "resolve_codex_binary", side_effect=AssertionError), \
                patch.object(a, "resolve_desktop_or_codex_binary", side_effect=AssertionError):
            for invalid in (object(), object.__new__(c.ServiceChildContext)):
                for action in (lambda: a.AppServerClient(child_context=invalid),
                               lambda: a.AppServerClient.global_config_local(child_context=invalid),
                               lambda: a.AppServerClient.remote_ssh("selected-host", child_context=invalid),
                               lambda: a.AppServerClient.desktop_preferred(required=True, child_context=invalid)):
                    self.assert_code("invalid_child_context", action)
            launch.assert_not_called()
            probe.assert_not_called()

    def test_relative_bound_worker_cwd_rejects_before_probe_or_process(self):
        with self.selected() as context, self.launches() as (launch, probe, process):
            self.assert_code("invalid_child_context", lambda: a.AppServerClient.global_config_local(cwd="relative-worker", child_context=context))
            launch.assert_not_called()
            probe.assert_not_called()

    def test_missing_explicit_backend_does_not_launch_installed_codex(self):
        env = {**self.environment, "CODEX_BRIDGE_CODEX_BIN": str(self.root / "missing-explicit")}
        with self.selected(environment=env) as context, self.launches() as (launch, probe, process):
            self.assert_code("child_executable_unavailable", lambda: a.AppServerClient.global_config_local(child_context=context))
            launch.assert_not_called()
            probe.assert_not_called()
            self.assertEqual(context.summary()["selected_executable_count"], 0)

    def test_bound_probe_failures_never_publish_private_child_output(self):
        for result in (OSError("private-environment-marker"),
                       SimpleNamespace(returncode=1, stdout="private-environment-marker", stderr=str(self.root)),
                       SimpleNamespace(returncode=0, stdout="private-environment-marker", stderr="")):
            with self.subTest(result_type=type(result).__name__), self.selected() as context, self.launches() as (launch, probe, process):
                if isinstance(result, Exception):
                    probe.side_effect = result
                else:
                    probe.return_value = result
                self.assert_code("child_probe_failed", lambda: a.AppServerClient.global_config_local(child_context=context))
                launch.assert_not_called()

    def test_file_drift_between_probe_and_launch_rejects_before_popen(self):
        with self.selected() as context, self.launches() as (launch, probe, process):
            def changed(*args, **kwargs):
                self.codex.write_bytes(b"synthetic-executable-B")
                return SimpleNamespace(returncode=1, stdout="", stderr="No MCP server named 'reasonfirst' found.")
            probe.side_effect = changed
            self.assert_code("child_executable_changed", lambda: a.AppServerClient.global_config_local(child_context=context))
            launch.assert_not_called()
            self.assert_code("child_executable_changed", context.summary)

    def test_bound_launch_failure_has_a_fixed_diagnostic(self):
        with self.selected() as context, self.launches() as (launch, probe, process):
            launch.side_effect = ValueError("private-environment-marker")
            self.assert_code("child_launch_failed", lambda: a.AppServerClient(child_context=context))

    def test_remote_ssh_binds_only_local_launch_inputs_and_preserves_remote_command(self):
        with self.selected() as context, self.launches() as (launch, probe, process):
            app = a.AppServerClient.remote_ssh("selected-host", remote_codex="/opt/selected-codex", connect_timeout=9, child_context=context)
            try:
                self.assertIs(app.child_context, context)
                self.assertEqual(Path(launch.call_args.args[0][0]), self.ssh)
                self.assertEqual(launch.call_args.args[0][1:], [
                    "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=9",
                    "--", "selected-host", "sh -lc 'exec /opt/selected-codex app-server'",
                ])
                self.assertEqual(launch.call_args.kwargs["env"], self.environment)
                self.assertEqual(launch.call_args.kwargs["cwd"], self.root)
                self.assertIs(context.summary()["remote_runtime_verified"], False)
                probe.assert_not_called()
            finally:
                app.close()

    def test_required_desktop_retains_endpoint_and_never_launches_a_child(self):
        with self.selected() as context, self.launches() as (launch, probe, process):
            endpoint = context.managed_app_server_socket()
            endpoint.parent.mkdir(parents=True)
            endpoint.write_bytes(b"synthetic endpoint marker")
            connection = SimpleNamespace(close=Mock())
            def connect(app, path):
                self.assertEqual(path, str(endpoint))
                app.ws = connection
            os.environ["CODEX_HOME"] = str(self.root / "ambient-other")
            with patch.object(a.AppServerClient, "_connect_unix_socket", connect), \
                    patch.object(c.ServiceChildContext, "resolve_codex_binary", side_effect=AssertionError):
                app = a.AppServerClient.desktop_preferred(required=True, child_context=context)
            self.assertIs(app.child_context, context)
            self.assertEqual(app.backend_name, "desktop-managed")
            self.assertIs(context.summary()["desktop_daemon_verified"], False)
            app.close()
            connection.close.assert_called_once_with()
            launch.assert_not_called()
            probe.assert_not_called()

    def test_required_desktop_absence_does_not_fallback_to_a_local_binary(self):
        with self.selected() as context, self.launches() as (launch, probe, process), \
                patch.object(c.ServiceChildContext, "resolve_codex_binary", side_effect=AssertionError):
            self.assert_code("child_endpoint_unavailable", lambda: a.AppServerClient.desktop_preferred(required=True, child_context=context))
            launch.assert_not_called()
            probe.assert_not_called()

    def test_failed_bound_initialization_reaps_owned_process_without_raw_error(self):
        with self.selected() as context, self.launches() as (launch, probe, process), \
                patch.object(a.AppServerClient, "_initialize", side_effect=RuntimeError("private-environment-marker")):
            self.assert_code("child_launch_failed", lambda: a.AppServerClient(child_context=context))
            self.assertTrue(process.terminated)
            self.assertTrue(all(stream.closed for stream in (process.stdin, process.stdout, process.stderr)))

    def test_bound_close_cleans_owned_process_after_sticky_drift_and_closed_flag(self):
        with self.selected() as context, self.launches() as (launch, probe, process):
            app = a.AppServerClient(child_context=context)
            self.codex.write_bytes(b"synthetic-executable-B")
            self.assert_code("child_executable_changed", context.revalidate)
            app._closed = True
            with patch.object(c.ServiceChildContext, "revalidate", side_effect=AssertionError):
                app.close()
            self.assertTrue(process.terminated)
            self.assertTrue(all(stream.closed for stream in (process.stdin, process.stdout, process.stderr)))

    def test_bound_rpc_diagnostics_do_not_reflect_backend_or_stderr_fields(self):
        with self.selected() as context, self.launches() as (launch, probe, process):
            app = a.AppServerClient(child_context=context)
            def respond(message):
                app._pending[message["id"]].put({"error": {
                    "code": -32099, "message": "private-environment-marker",
                    "backend": str(self.root), "stderr_tail": ["private-environment-marker"],
                }})
            try:
                with patch.object(app, "_write", side_effect=respond):
                    self.assert_code("child_binding_failed", lambda: app.request("synthetic-method", timeout=.1))
            finally:
                app.close()

    def test_bound_stderr_is_drained_without_retaining_private_output(self):
        with self.selected() as context, self.launches() as (launch, probe, process):
            app = a.AppServerClient(child_context=context)
            process.stderr.write("private-environment-marker\n")
            process.stderr.seek(0)
            app._read_stderr()
            self.assertEqual(app._stderr_tail, [])
            app.close()

    def test_bound_reader_failure_is_fixed_and_close_still_reaps_the_child(self):
        class FailedStream(io.StringIO):
            def __iter__(self):
                raise OSError("private-environment-marker")
        with self.selected() as context, self.launches() as (launch, probe, process):
            app = a.AppServerClient(child_context=context)
            process.stdout.close()
            process.stdout = FailedStream()
            app._read_stdio_loop()
            self.assertEqual(app._stderr_tail, ["child_stream_failed"])
            app.close()
            self.assertTrue(process.terminated)

    def test_bound_close_rejects_wrong_pid_before_owned_handles(self):
        with self.selected() as context, self.launches() as (launch, probe, process):
            app = a.AppServerClient(child_context=context)
            with patch.object(c.os, "getpid", return_value=context._pid + 1):
                self.assert_code("child_wrong_process", app.close)
                self.assertFalse(process.terminated)
            app.close()
            self.assertTrue(process.terminated)

    def test_ordinary_factory_call_shape_and_subprocess_inheritance_are_unchanged(self):
        with patch.object(a.AppServerClient, "__init__", return_value=None) as create, \
                patch.object(a, "resolve_desktop_or_codex_binary", return_value=str(self.codex)), \
                patch.object(a, "_reasonfirst_mcp_configured", return_value=False) as probe:
            a.AppServerClient.global_config_local(cwd=str(self.worker))
            self.assertNotIn("child_context", create.call_args.kwargs)
            self.assertNotIn("cwd", create.call_args.kwargs)
            probe.assert_called_once_with(str(self.codex), cwd=str(self.worker))
        with self.launches() as (launch, probe, process):
            app = a.AppServerClient(codex_bin=str(self.codex))
            self.assertIsNone(app.child_context)
            self.assertNotIn("env", launch.call_args.kwargs)
            self.assertNotIn("cwd", launch.call_args.kwargs)
            app.close()

    def test_ordinary_launch_failure_keeps_existing_error_detail(self):
        with self.launches() as (launch, probe, process):
            launch.side_effect = OSError("ordinary diagnostic detail")
            with self.assertRaisesRegex(a.AppServerError, "ordinary diagnostic detail"):
                a.AppServerClient(codex_bin=str(self.codex))


if __name__ == "__main__":
    unittest.main()
