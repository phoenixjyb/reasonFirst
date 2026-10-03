from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import types
import unittest
from unittest.mock import Mock, patch

from gitlab_agent import bridge_http as mod
from gitlab_agent import bridge_mcp


LAUNCH = mod.HTTPLaunch("127.0.0.1", 8765, "/mcp", "read-only", "disabled")
ARGS = ["--host", "127.0.0.1", "--port", "8765", "--path", "/mcp",
        "--mode", "read-only", "--control-policy", "disabled"]
READ_ONLY = {
    "reasonfirst_doctor", "reasonfirst_target_probe", "reasonfirst_workspace_status",
    "reasonfirst_files", "reasonfirst_read", "reasonfirst_diff", "reasonfirst_codex_status",
    "reasonfirst_codex_events", "reasonfirst_pending_approvals", "reasonfirst_ci",
    "reasonfirst_review_bundle", "reasonfirst_artifacts",
}
WRITES = {
    "reasonfirst_dispatch", "reasonfirst_codex_start", "reasonfirst_codex_continue",
    "reasonfirst_codex_steer", "reasonfirst_codex_interrupt", "reasonfirst_approve",
    "reasonfirst_decline", "reasonfirst_finish",
}
FULL = READ_ONLY | WRITES | {"reasonfirst_finish_preview", "reasonfirst_evidence"}


class HTTPArgumentTests(unittest.TestCase):
    def call(self, args, env=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env or {}, clear=True), redirect_stdout(out), redirect_stderr(err):
            try:
                code = mod.main(args)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_inspection_is_static_and_has_no_io(self):
        with (patch.object(mod, "_build_server") as builder,
              patch("builtins.open", side_effect=AssertionError("file read")),
              patch.object(Path, "open", side_effect=AssertionError("file read")),
              patch.object(Path, "mkdir", side_effect=AssertionError("directory write")),
              patch.object(socket, "socket", side_effect=AssertionError("network")),
              patch.object(subprocess, "Popen", side_effect=AssertionError("process"))):
            code, out, err = self.call(["--inspect", *ARGS])
        self.assertEqual((code, err), (0, ""))
        data = json.loads(out)
        self.assertEqual(data["endpoint"], {"host": "127.0.0.1", "port": 8765, "path": "/mcp"})
        for key in ("mutating", "configuration_inspected", "package_integrity_verified",
                    "server_started", "live_probe_performed", "ready_for_activation"):
            self.assertIs(data[key], False)
        builder.assert_not_called()

    def test_inspection_preserves_credential_environment(self):
        env = {"CONTROL_PLANE_API_KEY": "synthetic", "OPENAI_ADMIN_KEY": "synthetic"}
        out = io.StringIO()
        with patch.dict(os.environ, env, clear=True), redirect_stdout(out):
            before = dict(os.environ)
            self.assertEqual(mod.main(["--inspect", *ARGS]), 0)
            self.assertEqual(dict(os.environ), before)
        self.assertNotIn("synthetic", out.getvalue())

    def test_help_needs_no_settings_or_controller(self):
        with patch.object(mod, "_build_server") as builder:
            code, out, err = self.call(["--help"], {"RF_MCP_READ_ONLY": "invalid"})
        self.assertEqual((code, err), (0, ""))
        self.assertIn("foreground", out)
        builder.assert_not_called()

    def test_action_and_every_identity_field_are_required(self):
        for args in (ARGS, ["--inspect"], ["--serve", "--inspect", *ARGS]):
            with self.subTest(args=args):
                self.assertEqual(self.call(args)[0], 2)
        for index in range(0, len(ARGS), 2):
            self.assertEqual(self.call(["--serve", *(ARGS[:index] + ARGS[index + 2:])])[0], 2)

    def test_argparse_errors_never_reflect_private_values(self):
        value = "sensitive-" + "made-up-for-test"
        for args in (["--serve", *ARGS, "--token", value],
                     ["--inspect", *ARGS, "--port", value],
                     ["--inspect", *ARGS, "--mode", value]):
            code, out, err = self.call(args)
            self.assertEqual(code, 2)
            self.assertEqual(out, "")
            self.assertNotIn(value, err)
            self.assertIn("invalid_arguments", err)

    def test_abbreviated_or_legacy_flags_are_not_accepted(self):
        for extra in ("--ser", "--stdio", "--doctor", "--yes", "--enable-control"):
            self.assertEqual(self.call([*ARGS, extra])[0], 2)

    def test_only_explicit_loopback_literals_are_supported(self):
        for host in ("127.0.0.1", "::1"):
            replace(LAUNCH, host=host).validate({})
        for host in ("0.0.0.0", "::", "localhost", "127.1", "127.0.0.2", "::ffff:127.0.0.1", "example.invalid"):
            with self.subTest(host=host), self.assertRaisesRegex(mod.HTTPLaunchError, "unsupported_host"):
                replace(LAUNCH, host=host).validate({})

    def test_fixed_port_bounds(self):
        for port in (1, 65535):
            replace(LAUNCH, port=port).validate({})
        for port in (0, -1, 65536, True, "8765"):
            with self.subTest(port=port), self.assertRaisesRegex(mod.HTTPLaunchError, "invalid_port"):
                replace(LAUNCH, port=port).validate({})

    def test_path_is_preserved_without_rewriting(self):
        for path in ("/mcp", "/custom/Bridge_v2", "/mcp-legacy"):
            launch = replace(LAUNCH, path=path)
            launch.validate({})
            self.assertEqual(launch.describe()["endpoint"]["path"], path)

    def test_ambiguous_reserved_and_encoded_paths_are_rejected(self):
        for path in ("/", "mcp", "//mcp", "/mcp/", "/../mcp", "/mcp/./x", "/mcp?x",
                     "/mcp#x", "/mcp%2Fx", "/mcp\\x", "/healthz", "/control", "/Control/x",
                     "/HEALTHZ/a", "/mcp\n", "/mcp x", "/中文", "/" + "x" * 256):
            with self.subTest(path=path), self.assertRaisesRegex(mod.HTTPLaunchError, "unsupported_path"):
                replace(LAUNCH, path=path).validate({})

    def test_legacy_control_is_rejected_before_controller_construction(self):
        with patch.object(mod, "_build_server") as builder:
            code, out, err = self.call(["--serve", *ARGS, "--control-policy", "legacy"])
        self.assertEqual((code, out), (2, ""))
        self.assertIn("unsupported_control_policy", err)
        builder.assert_not_called()

    def test_mode_conflicts_fail_in_both_directions(self):
        for mode, env in (("read-only", "false"), ("full-chat", "true")):
            with self.assertRaisesRegex(mod.HTTPLaunchError, "policy_conflict"):
                replace(LAUNCH, mode=mode).validate({"RF_MCP_READ_ONLY": env})

    def test_compatible_environment_booleans(self):
        for value in ("true", "1", "yes", "on", " TRUE "):
            LAUNCH.validate({"RF_MCP_READ_ONLY": value})
        for value in ("false", "0", "no", "off"):
            replace(LAUNCH, mode="full-chat").validate({"RF_MCP_READ_ONLY": value})
            LAUNCH.validate({"RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH": value})

    def test_unknown_or_empty_policy_never_means_false(self):
        for name in ("RF_MCP_READ_ONLY", "RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH"):
            for value in ("", "typo", "maybe", "secret-value"):
                with self.assertRaisesRegex(mod.HTTPLaunchError, "invalid_policy_environment"):
                    LAUNCH.validate({name: value})

    def test_experimental_remote_push_cannot_be_silently_dropped(self):
        for mode in ("read-only", "full-chat"):
            with self.assertRaisesRegex(mod.HTTPLaunchError, "unsupported_remote_push"):
                replace(LAUNCH, mode=mode).validate({"RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH": "true"})

    def test_python_environment_shadowing_is_not_silently_fixed(self):
        for name in ("PYTHONPATH", "PYTHONHOME"):
            with self.assertRaisesRegex(mod.HTTPLaunchError, "python_environment_override"):
                LAUNCH.validate({name: "/synthetic/stale/source"})

    def test_policy_errors_do_not_echo_environment_values(self):
        code, out, err = self.call(["--serve", *ARGS], {"RF_MCP_READ_ONLY": "private-value"})
        self.assertEqual((code, out), (2, ""))
        self.assertNotIn("private-value", err)


class HTTPExecutionTests(unittest.TestCase):
    def run_fake(self, *, mode="read-only", fail=None, close_fail=None):
        ctrl = Mock()
        ctrl.close.side_effect = close_fail
        server = Mock(_reasonfirst_controller=ctrl)
        server.run.side_effect = fail
        env = {"CONTROL_PLANE_API_KEY": "synthetic", "OPENAI_ADMIN_KEY": "synthetic"}
        err = io.StringIO()
        def build(**kwargs):
            self.assertNotIn("CONTROL_PLANE_API_KEY", os.environ)
            self.assertNotIn("OPENAI_ADMIN_KEY", os.environ)
            self.assertNotIn("RF_MCP_READ_ONLY", os.environ)
            return server
        with patch.dict(os.environ, env, clear=True), patch.object(mod, "_build_server", side_effect=build) as builder, redirect_stderr(err):
            code = mod.main(["--serve", *ARGS, "--mode", mode])
        builder.assert_called_once_with(read_only_mode=mode == "read-only")
        server.run.assert_called_once_with(transport="streamable-http", host="127.0.0.1", port=8765, streamable_http_path="/mcp")
        ctrl.close.assert_called_once_with()
        return code, err.getvalue()

    def test_read_only_launch_preserves_explicit_transport(self):
        self.assertEqual(self.run_fake(), (0, ""))

    def test_full_chat_launch_requires_explicit_mode(self):
        self.assertEqual(self.run_fake(mode="full-chat"), (0, ""))

    def test_runtime_error_closes_controller_and_returns_failure(self):
        code, err = self.run_fake(fail=RuntimeError("private-value"))
        self.assertEqual(code, 1)
        self.assertIn("server_failed", err)
        self.assertNotIn("private-value", err)

    def test_interrupt_closes_controller(self):
        self.assertEqual(self.run_fake(fail=KeyboardInterrupt()), (130, ""))

    def test_close_failure_is_not_reported_as_success(self):
        code, err = self.run_fake(close_fail=RuntimeError("private-value"))
        self.assertEqual(code, 1)
        self.assertIn("controller_cleanup_failed", err)
        self.assertNotIn("private-value", err)

    def test_build_failure_is_safe_and_never_starts_server(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(mod, "_build_server", side_effect=RuntimeError("private-value")), redirect_stderr(io.StringIO()) as err:
            self.assertEqual(mod.main(["--serve", *ARGS]), 1)
        self.assertNotIn("private-value", err.getvalue())

    def test_second_policy_check_catches_changed_environment(self):
        with patch.dict(os.environ, {"RF_MCP_READ_ONLY": "false"}, clear=True), patch.object(mod, "_build_server") as builder:
            with self.assertRaisesRegex(mod.HTTPLaunchError, "policy_conflict"):
                mod._serve(LAUNCH)
        builder.assert_not_called()


class FakeServer:
    def __init__(self, *args, **kwargs):
        self.tools, self.routes = {}, {}
    def tool(self, name, **kwargs):
        def decorate(fn):
            self.tools[name] = (fn, kwargs["annotations"])
            return fn
        return decorate
    def custom_route(self, path, **kwargs):
        def decorate(fn):
            self.routes[path] = fn
            return fn
        return decorate


class HTTPCoreTests(unittest.TestCase):
    def build(self, mode=None, env=None):
        server_mod, types_mod = types.ModuleType("mcp.server"), types.ModuleType("mcp.types")
        server_mod.MCPServer = FakeServer
        types_mod.ToolAnnotations = lambda **kwargs: kwargs
        with (patch.dict(sys.modules, {"mcp.server": server_mod, "mcp.types": types_mod}),
              patch.dict(os.environ, env or {}, clear=True),
              patch.object(bridge_mcp, "BridgeController", return_value=Mock())):
            return bridge_mcp.build_server(read_only_mode=mode)

    def test_explicit_read_only_uses_exact_existing_read_only_tool_set(self):
        server = self.build(True, {"RF_MCP_READ_ONLY": "false"})
        self.assertEqual(set(server.tools), READ_ONLY)
        self.assertTrue(all(meta["read_only_hint"] for fn, meta in server.tools.values()))

    def test_explicit_full_chat_uses_exact_packaged_tool_set(self):
        server = self.build(False)
        self.assertEqual(set(server.tools), FULL)
        self.assertTrue(all(not server.tools[name][1]["read_only_hint"] for name in WRITES))

    def test_no_control_route_or_remote_push_even_with_ambient_opt_in(self):
        for mode in (True, False):
            server = self.build(mode, {"RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH": "true"})
            self.assertEqual(set(server.routes), {"/healthz"})
            self.assertNotIn("reasonfirst_authorize_push", server.tools)

    def test_existing_implicit_mode_behavior_is_preserved(self):
        self.assertEqual(set(self.build(env={"RF_MCP_READ_ONLY": "true"}).tools), READ_ONLY)
        self.assertEqual(set(self.build().tools), FULL)

    def test_nonboolean_explicit_mode_fails_before_controller(self):
        server_mod, types_mod = types.ModuleType("mcp.server"), types.ModuleType("mcp.types")
        server_mod.MCPServer = FakeServer
        types_mod.ToolAnnotations = lambda **kwargs: kwargs
        with (patch.dict(sys.modules, {"mcp.server": server_mod, "mcp.types": types_mod}),
              patch.object(bridge_mcp, "BridgeController") as ctrl):
            for value in ("false", 0, 1):
                with self.assertRaises(ValueError):
                    bridge_mcp.build_server(read_only_mode=value)
        ctrl.assert_not_called()


class HTTPDocumentationTests(unittest.TestCase):
    def test_bilingual_docs_distinguish_transport_from_upgrade(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("BRIDGE_HTTP.md", "BRIDGE_HTTP_CN.md"):
            text = (root / "docs" / name).read_text(encoding="utf-8")
            for marker in ("v0.5.1", "--inspect", "--serve", "--control-policy legacy", "/control", "ready_for_activation", "PYTHONPATH"):
                self.assertIn(marker, text)

    def test_both_console_transports_are_packaged(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('reasonfirst-bridge-mcp = "gitlab_agent.bridge_mcp:main"', text)
        self.assertIn('reasonfirst-bridge-http = "gitlab_agent.bridge_http:main"', text)

    def test_install_harness_runs_wire_tests_in_isolated_wheel_python(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts/install_e2e.py").read_text(encoding="utf-8")
        self.assertIn('"RF_HTTP_TEST_WHEEL_ROOT"', text)
        self.assertIn('"-I", "-B", str(source_root / "tests/test_bridge_http_integration.py")', text)
        self.assertIn('verify_source_route(source_root=source_root, root=root)', text)


if __name__ == "__main__":
    unittest.main()
