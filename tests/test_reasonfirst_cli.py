from __future__ import annotations

import contextlib
import io
import json
import unittest
from unittest.mock import patch

from gitlab_agent import reasonfirst_cli


STATUS = {
    "ok": True,
    "command": "setup-status",
    "mutating": False,
    "reasonfirst_version": "0.5.0",
    "system": {
        "os": "linux",
        "platform_system": "Linux",
        "architecture": "x86_64",
        "python": "3.12.0",
        "python_executable": "/usr/bin/python3",
    },
    "mode": "standard",
    "probes": [],
    "config": {
        "path": "/home/test/.config/gitlab-agent/.env",
        "exists": True,
        "valid": True,
    },
    "workers": [
        {
            "name": "codex-cli",
            "available": True,
            "path": "/usr/bin/codex",
            "authentication_verified": False,
        }
    ],
    "setup_state": {
        "path": "/home/test/.config/reasonfirst/setup.yaml",
        "exists": False,
        "valid": True,
        "state": None,
    },
    "readiness": {
        "machine_prerequisites": True,
        "control_plane_prerequisites": True,
        "tunnel_client_available": True,
        "chatgpt_connection": "not_verified",
        "ready": False,
        "ready_reason": "detect-only",
    },
    "plan": {
        "actions": [
            {
                "id": "verify-chatgpt",
                "message": "Complete the ChatGPT-side authorization step.",
            }
        ],
        "action_count": 1,
    },
}


class ReasonFirstCLITests(unittest.TestCase):
    def test_setup_status_json(self) -> None:
        out = io.StringIO()
        with (
            patch.object(reasonfirst_cli, "build_setup_status", return_value=STATUS),
            contextlib.redirect_stdout(out),
        ):
            code = reasonfirst_cli.main(["setup", "--status", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertFalse(payload["mutating"])
        self.assertFalse(payload["readiness"]["ready"])
        self.assertEqual(payload["system"]["os"], "linux")

    def test_setup_status_human_output_states_no_changes(self) -> None:
        out = io.StringIO()
        with (
            patch.object(reasonfirst_cli, "build_setup_status", return_value=STATUS),
            contextlib.redirect_stdout(out),
        ):
            code = reasonfirst_cli.main(["setup", "--status"])
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("ReasonFirst setup status", text)
        self.assertIn("ChatGPT connection: not verified", text)
        self.assertIn("No changes were made.", text)

    def test_setup_apply_is_not_silently_available_in_slice1(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = reasonfirst_cli.main(["setup"])
        self.assertEqual(code, 2)
        self.assertIn("next v0.5.1 slice", err.getvalue())

    def test_version(self) -> None:
        out = io.StringIO()
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stdout(out):
            reasonfirst_cli.main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("reasonfirst 0.5.0", out.getvalue())


if __name__ == "__main__":
    unittest.main()
