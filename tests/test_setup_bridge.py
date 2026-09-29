from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gitlab_agent import bridge_mcp
from gitlab_agent.setup_bridge import (
    BridgeSetupError,
    EXPECTED_WRITE_TOOLS,
    bridge_tool_inventory,
    build_bridge_handoff,
    connect_bridge_runtime,
    persist_bridge_state,
    require_eligibility_acknowledgement,
)
from gitlab_agent.setup_state import SetupState, load_setup_state


TUNNEL_ID = "tunnel_" + "b" * 32


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


class FakeServer:
    def __init__(self):
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)


class FakeController:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class BridgeSetupTests(unittest.TestCase):
    def test_inventory_has_expected_write_tools_and_no_remote_push(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "RF_CODEX_BRIDGE_STATE_DIR": str(Path(td) / "real-state"),
                "RF_BRIDGE_CONFIG": str(Path(td) / "real-bridge.yaml"),
            },
            clear=False,
        ):
            before_state = os.environ["RF_CODEX_BRIDGE_STATE_DIR"]
            result = bridge_tool_inventory()
            self.assertEqual(os.environ["RF_CODEX_BRIDGE_STATE_DIR"], before_state)

        self.assertTrue(result["ok"])
        self.assertFalse(result["remote_push_exposed"])
        self.assertEqual(result["forbidden_tools"], [])
        for name in EXPECTED_WRITE_TOOLS:
            with self.subTest(name=name):
                self.assertIn(name, result["tools"])
                self.assertFalse(result["tools"][name]["read_only"])

    def test_packaged_bridge_main_scrubs_tunnel_credentials_and_forces_policy(self) -> None:
        server = FakeServer()
        ctrl = FakeController()
        setattr(server, "_reasonfirst_controller", ctrl)

        with (
            patch.dict(
                os.environ,
                {
                    "CONTROL_PLANE_API_KEY": "runtime-secret",
                    "OPENAI_ADMIN_KEY": "admin-secret",
                    "RF_MCP_READ_ONLY": "true",
                    "RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH": "true",
                },
                clear=False,
            ),
            patch.object(bridge_mcp, "build_server", return_value=server),
        ):
            bridge_mcp.main()
            self.assertNotIn("CONTROL_PLANE_API_KEY", os.environ)
            self.assertNotIn("OPENAI_ADMIN_KEY", os.environ)
            self.assertEqual(os.environ["RF_MCP_READ_ONLY"], "false")
            self.assertEqual(
                os.environ["RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH"],
                "false",
            )

        self.assertEqual(server.calls, [{"transport": "stdio"}])
        self.assertTrue(ctrl.closed)

    def test_connect_bridge_requires_inventory_and_reports_not_chatgpt_verified(self) -> None:
        connect_payload = {
            "alias": "reasonfirst-bridge",
            "tunnel_id": TUNNEL_ID,
            "process_running": True,
            "healthy": True,
            "ready": False,
        }
        ready_payload = {
            "alias": "reasonfirst-bridge",
            "tunnel_id": TUNNEL_ID,
            "runtime_state": "ready",
            "process_running": True,
            "healthy": True,
            "ready": True,
        }
        runner = FakeRunner([(connect_payload, 0), (ready_payload, 0)])

        with tempfile.TemporaryDirectory() as td:
            bridge = Path(td) / "reasonfirst-bridge-mcp"
            bridge.write_text("fixture", encoding="utf-8")
            if os.name != "nt":
                bridge.chmod(0o700)

            with patch(
                "gitlab_agent.setup_bridge.bridge_execution_prerequisites",
                return_value={"ok": True, "codex_app_server_capable": True},
            ), patch(
                "gitlab_agent.setup_bridge.bridge_tool_inventory",
                return_value={
                    "ok": True,
                    "tool_count": 20,
                    "tools": {},
                    "missing_read_tools": [],
                    "missing_write_tools": [],
                    "forbidden_tools": [],
                    "write_tools_marked_read_only": [],
                    "remote_push_exposed": False,
                },
            ):
                result = connect_bridge_runtime(
                    tunnel_id=TUNNEL_ID,
                    runtime_key="bridge-runtime-secret",
                    tunnel_client="/tools/tunnel-client",
                    bridge_executable=str(bridge),
                    runner=runner,
                    sleep=lambda _: None,
                )

        self.assertTrue(result["ok"])
        self.assertTrue(result["ready"])
        self.assertFalse(result["bridge_remote_push_enabled"])
        self.assertFalse(result["chatgpt_write_capability_verified"])
        argv, kwargs = runner.calls[0]
        self.assertNotIn("bridge-runtime-secret", repr(argv))
        self.assertIn("env:CONTROL_PLANE_API_KEY", argv)
        self.assertEqual(
            kwargs["env"]["CONTROL_PLANE_API_KEY"],
            "bridge-runtime-secret",
        )

    def test_missing_codex_app_server_capability_prevents_connect(self) -> None:
        with (
            patch(
                "gitlab_agent.setup_bridge.bridge_execution_prerequisites",
                return_value={
                    "ok": False,
                    "codex_app_server_capable": False,
                    "note": "Codex required",
                },
            ),
            patch("gitlab_agent.setup_bridge.bridge_tool_inventory") as inventory,
        ):
            result = connect_bridge_runtime(
                tunnel_id=TUNNEL_ID,
                runtime_key="secret",
                tunnel_client="/tools/tunnel-client",
                bridge_executable="/does/not/matter",
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "worker-prerequisite")
        inventory.assert_not_called()

    def test_failed_inventory_prevents_tunnel_connect(self) -> None:
        with patch(
            "gitlab_agent.setup_bridge.bridge_execution_prerequisites",
            return_value={"ok": True, "codex_app_server_capable": True},
        ), patch(
            "gitlab_agent.setup_bridge.bridge_tool_inventory",
            return_value={
                "ok": False,
                "missing_write_tools": ["reasonfirst_finish"],
            },
        ):
            result = connect_bridge_runtime(
                tunnel_id=TUNNEL_ID,
                runtime_key="secret",
                tunnel_client="/tools/tunnel-client",
                bridge_executable="/does/not/matter",
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "inventory")

    def test_bridge_state_is_nonsecret(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state_path = Path(td) / "setup.yaml"
            state = SetupState(
                mode="full-chat",
                tunnel_id="tunnel_" + "a" * 32,
                tunnel_runtime="reasonfirst-gitlab",
                completed_phases=("system", "gitlab", "worker", "tunnel"),
            )
            from gitlab_agent.setup_state import save_setup_state

            save_setup_state(state, state_path)
            updated = persist_bridge_state(
                tunnel_id=TUNNEL_ID,
                alias="reasonfirst-bridge",
                state_path=state_path,
            )
            raw = state_path.read_text(encoding="utf-8")
            self.assertEqual(updated.bridge_tunnel_id, TUNNEL_ID)
            self.assertIn("bridge", updated.completed_phases)
            self.assertNotIn("api_key", raw.lower())
            self.assertEqual(load_setup_state(state_path), updated)

    def test_eligibility_acknowledgement_is_mandatory(self) -> None:
        with self.assertRaisesRegex(BridgeSetupError, "explicit acknowledgement"):
            require_eligibility_acknowledgement(acknowledged=False)
        require_eligibility_acknowledgement(acknowledged=True)

    def test_bridge_handoff_never_claims_full_chat_ready(self) -> None:
        payload = build_bridge_handoff(tunnel_id=TUNNEL_ID)
        self.assertFalse(payload["full_chat_ready"])
        self.assertIn("reasonfirst_dispatch", payload["acceptance_prompt"])
        self.assertIn("reasonfirst_authorize_push", payload["acceptance_prompt"])
        self.assertTrue(any(TUNNEL_ID in step for step in payload["chatgpt_steps"]))
        self.assertTrue(any("Codex App Server" in step for step in payload["chatgpt_steps"]))


if __name__ == "__main__":
    unittest.main()
