from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gitlab_agent.setup_state import SetupState, load_setup_state
from gitlab_agent.setup_tunnel import (
    RUNTIME_KEY_ENV,
    build_chatgpt_handoff,
    connect_runtime,
    persist_tunnel_state,
    runtime_status,
    stop_runtime,
    validate_tunnel_id,
)


TUNNEL_ID = "tunnel_" + "a" * 32


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        payload, code = self.responses.pop(0)
        return subprocess.CompletedProcess(
            argv,
            code,
            stdout=json.dumps(payload),
            stderr="",
        )


class TunnelSetupTests(unittest.TestCase):
    def test_tunnel_id_validation_matches_upstream_contract(self) -> None:
        self.assertEqual(validate_tunnel_id(TUNNEL_ID), TUNNEL_ID)
        for bad in ("", "tunnel_SHORT", "Tunnel_" + "a" * 32, "tunnel_" + "A" * 32):
            with self.subTest(bad=bad), self.assertRaises(Exception):
                validate_tunnel_id(bad)

    def test_connect_uses_secret_reference_not_secret_argv_and_waits_for_ready(self) -> None:
        connect = {
            "alias": "reasonfirst-gitlab",
            "tunnel_id": TUNNEL_ID,
            "process_running": True,
            "healthy": True,
            "ready": False,
        }
        ready = {
            "alias": "reasonfirst-gitlab",
            "tunnel_id": TUNNEL_ID,
            "runtime_state": "ready",
            "process_running": True,
            "healthy": True,
            "ready": True,
            "ui_url": "http://127.0.0.1:9000/ui",
        }
        runner = FakeRunner([(connect, 0), (ready, 0)])
        with tempfile.TemporaryDirectory() as td:
            mcp = Path(td) / "reasonfirst-gitlab-mcp"
            mcp.write_text("fixture", encoding="utf-8")
            if os.name != "nt":
                mcp.chmod(0o700)
            result = connect_runtime(
                tunnel_id=TUNNEL_ID,
                runtime_key="runtime-secret-value",
                tunnel_client="/tools/tunnel-client",
                mcp_executable=str(mcp),
                runner=runner,
                sleep=lambda _: None,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["ready"])
        connect_argv, connect_kwargs = runner.calls[0]
        self.assertIn("env:CONTROL_PLANE_API_KEY", connect_argv)
        self.assertNotIn("runtime-secret-value", repr(connect_argv))
        self.assertEqual(
            connect_kwargs["env"][RUNTIME_KEY_ENV],
            "runtime-secret-value",
        )
        self.assertNotIn("OPENAI_ADMIN_KEY", connect_kwargs["env"])

    def test_connect_failure_redacts_secret_from_native_output(self) -> None:
        runner = FakeRunner(
            [({"error": "bad runtime-secret-value"}, 2)]
        )
        with tempfile.TemporaryDirectory() as td:
            mcp = Path(td) / "reasonfirst-gitlab-mcp"
            mcp.write_text("fixture", encoding="utf-8")
            if os.name != "nt":
                mcp.chmod(0o700)
            result = connect_runtime(
                tunnel_id=TUNNEL_ID,
                runtime_key="runtime-secret-value",
                tunnel_client="/tools/tunnel-client",
                mcp_executable=str(mcp),
                runner=runner,
            )
        self.assertFalse(result["ok"])
        self.assertNotIn("runtime-secret-value", repr(result))
        self.assertIn("<redacted>", repr(result))

    def test_runtime_status_and_stop_use_native_json(self) -> None:
        status_payload = {
            "alias": "reasonfirst-gitlab",
            "tunnel_id": TUNNEL_ID,
            "runtime_state": "ready",
            "process_running": True,
            "healthy": True,
            "ready": True,
        }
        stop_payload = {
            "alias": "reasonfirst-gitlab",
            "stopped": True,
            "already_stopped": False,
        }
        runner = FakeRunner([(status_payload, 0), (stop_payload, 0)])
        status = runtime_status(
            alias="reasonfirst-gitlab",
            tunnel_client="/tools/tunnel-client",
            runner=runner,
        )
        stopped = stop_runtime(
            alias="reasonfirst-gitlab",
            tunnel_client="/tools/tunnel-client",
            runner=runner,
        )
        self.assertTrue(status["ok"])
        self.assertTrue(status["ready"])
        self.assertTrue(stopped["ok"])

    def test_persisted_tunnel_state_contains_no_runtime_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "setup.yaml"
            state = persist_tunnel_state(
                tunnel_id=TUNNEL_ID,
                alias="reasonfirst-gitlab",
                tunnel_client="/tools/tunnel-client",
                state_path=path,
            )
            raw = path.read_text(encoding="utf-8")
            self.assertEqual(state.tunnel_id, TUNNEL_ID)
            self.assertIn("tunnel", state.completed_phases)
            self.assertNotIn("runtime-secret", raw)
            self.assertNotIn("api_key", raw.lower())
            self.assertEqual(load_setup_state(path), state)

    def test_chatgpt_handoff_is_explicitly_not_ready(self) -> None:
        payload = build_chatgpt_handoff(
            tunnel_id=TUNNEL_ID,
            project="team/project",
            ref="main",
        )
        self.assertFalse(payload["chatgpt_ready"])
        self.assertIn("gitlab_whoami", payload["acceptance_prompt"])
        self.assertIn("team/project", payload["acceptance_prompt"])
        self.assertIn(TUNNEL_ID, payload["chatgpt_steps"][2])


if __name__ == "__main__":
    unittest.main()
