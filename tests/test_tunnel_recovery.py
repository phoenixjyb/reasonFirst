"""Interrupted first-setup and shared Bridge boundary regressions (#88)."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from gitlab_agent import reasonfirst_cli, setup_bridge
from gitlab_agent.setup_state import SetupState, load_setup_state, save_setup_state
from gitlab_agent.setup_tunnel import _command_string


class TunnelRecoveryTests(unittest.TestCase):
    def test_interrupted_tunnel_connect_preserves_config_and_only_records_success(self) -> None:
        tunnel_id = "tunnel_" + "c" * 32
        for succeeds in (False, True):
            with self.subTest(succeeds=succeeds), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                config = root / "private.env"
                config.write_bytes(b"# synthetic private config, never changed by reconnect\n")
                before = config.read_bytes()
                state_path = root / "setup.yaml"
                previous = SetupState(
                    mode="standard", config_file=str(config), selected_worker="codex-cli",
                    tunnel_client_path="/tools/tunnel-client",
                    completed_phases=("system", "gitlab", "worker"),
                )
                save_setup_state(previous, state_path)
                args = argparse.Namespace(
                    state_file=state_path, install=False, tunnel_id=tunnel_id,
                    alias="reasonfirst-gitlab", open_platform=False, json=True,
                )
                result = {"ok": succeeds, "ready": succeeds,
                          "alias": args.alias, "tunnel_id": tunnel_id}
                with (
                    patch.object(reasonfirst_cli, "_resolve_or_install_tunnel_client", return_value="/tools/tunnel-client"),
                    patch.object(reasonfirst_cli, "resolve_read_mcp", return_value="/tools/reasonfirst-gitlab-mcp"),
                    patch.object(reasonfirst_cli, "connect_runtime", return_value=result) as connect,
                    patch.dict(reasonfirst_cli.os.environ, {reasonfirst_cli.RUNTIME_KEY_ENV: "example-runtime-value"}),
                ):
                    payload = reasonfirst_cli._connect_tunnel_from_args(args)
                connect.assert_called_once()
                self.assertEqual(connect.call_args.kwargs["tunnel_id"], tunnel_id)
                self.assertEqual(connect.call_args.kwargs["alias"], "reasonfirst-gitlab")
                after = load_setup_state(state_path)
                self.assertEqual(config.read_bytes(), before)
                self.assertEqual(after.config_file, previous.config_file)
                self.assertEqual(after.selected_worker, "codex-cli")
                self.assertEqual("tunnel" in after.completed_phases, succeeds)
                self.assertEqual(payload["local_tunnel_ready"], succeeds)
                self.assertFalse(payload["chatgpt_ready"])
                self.assertNotIn("example-runtime-value", state_path.read_text(encoding="utf-8"))
                if not succeeds:
                    self.assertEqual(after, previous)

    def test_bridge_launch_uses_same_quoted_command_and_binary_io(self):
        calls = []
        tunnel_id = "tunnel_" + "b" * 32
        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                "alias": "reasonfirst-bridge", "tunnel_id": tunnel_id,
                "process_running": True, "healthy": True, "ready": True,
            }).encode(), b"")
        with tempfile.TemporaryDirectory() as td:
            executable = Path(td) / "O'Brien 测试 reasonfirst-bridge-mcp"
            executable.write_text("not executed", encoding="utf-8")
            executable.chmod(0o700)
            with (
                patch.object(setup_bridge, "bridge_execution_prerequisites", return_value={"ok": True}),
                patch.object(setup_bridge, "bridge_tool_inventory", return_value={"ok": True}),
            ):
                result = setup_bridge.connect_bridge_runtime(
                    tunnel_id=tunnel_id, runtime_key="example-runtime-value",
                    tunnel_client="synthetic", bridge_executable=str(executable),
                    runner=runner, sleep=lambda _: None,
                )
            self.assertTrue(result["ok"])
            argv, kwargs = calls[0]
            self.assertEqual(argv[argv.index("--mcp-command") + 1], _command_string(str(executable.resolve())))
            self.assertFalse(kwargs["text"])
            self.assertNotIn("example-runtime-value", repr(argv))
            self.assertFalse(result["chatgpt_write_capability_verified"])


if __name__ == "__main__":
    unittest.main()
