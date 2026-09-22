from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import MethodType

from gitlab_agent.codex_desktop import (
    CodexDesktopError,
    CodexDesktopWorker,
    _approval_name,
    _sandbox_name,
)
from gitlab_agent.worker_policy import WorkerPolicy


def policy(**overrides) -> WorkerPolicy:
    values = {
        "backend": "codex-desktop",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "execution_mode": "interactive",
        "sandbox_mode": "workspace-write",
        "approval_policy": "on-request",
        "network_access": False,
    }
    values.update(overrides)
    return WorkerPolicy(**values)


class CodexDesktopPolicyTests(unittest.TestCase):
    def test_policy_names_translate_to_app_server_protocol(self) -> None:
        self.assertEqual(_approval_name(policy()), "onRequest")
        self.assertEqual(
            _approval_name(policy(approval_policy="never")),
            "never",
        )
        self.assertEqual(_sandbox_name(policy()), "workspaceWrite")
        self.assertEqual(
            _sandbox_name(policy(sandbox_mode="read-only")),
            "readOnly",
        )

    def test_run_sends_model_effort_permissions_and_network(self) -> None:
        worker = object.__new__(CodexDesktopWorker)
        worker._closed = False
        worker._events = []
        worker._turn_status = {}
        worker._turn_done = threading.Condition()
        calls: list[tuple[str, dict[str, object]]] = []

        def fake_request(self, method, params=None, *, timeout=60.0):
            params = dict(params or {})
            calls.append((method, params))
            if method == "configRequirements/read":
                return {
                    "requirements": {
                        "allowedApprovalPolicies": ["onRequest", "never"],
                        "allowedSandboxModes": ["readOnly", "workspaceWrite"],
                    }
                }
            if method == "thread/start":
                return {"thread": {"id": "thr_test"}}
            if method == "turn/start":
                with self._turn_done:
                    self._turn_status["turn_test"] = "completed"
                    self._turn_done.notify_all()
                return {
                    "turn": {
                        "id": "turn_test",
                        "status": "inProgress",
                    }
                }
            raise AssertionError(method)

        worker._request = MethodType(fake_request, worker)

        with tempfile.TemporaryDirectory() as td:
            result = worker.run(
                cwd=Path(td),
                prompt="Implement the reviewed task",
                policy=policy(),
                timeout_seconds=5,
            )

        self.assertEqual(result["turn_status"], "completed")
        thread_call = next(params for method, params in calls if method == "thread/start")
        turn_call = next(params for method, params in calls if method == "turn/start")

        self.assertEqual(thread_call["model"], "gpt-5.6-sol")
        self.assertEqual(thread_call["approvalPolicy"], "onRequest")
        self.assertEqual(thread_call["sandbox"], "workspaceWrite")

        self.assertEqual(turn_call["model"], "gpt-5.6-sol")
        self.assertEqual(turn_call["effort"], "high")
        self.assertEqual(turn_call["approvalPolicy"], "onRequest")
        self.assertEqual(turn_call["sandboxPolicy"]["type"], "workspaceWrite")
        self.assertFalse(turn_call["sandboxPolicy"]["networkAccess"])

    def test_managed_requirements_fail_closed(self) -> None:
        worker = object.__new__(CodexDesktopWorker)

        def fake_request(self, method, params=None, *, timeout=60.0):
            self.assertEqual(method, "configRequirements/read")
            return {
                "requirements": {
                    "allowedApprovalPolicies": ["never"],
                    "allowedSandboxModes": ["readOnly"],
                }
            }

        # Bind a tiny assertion helper without depending on TestCase as self.
        def request(self, method, params=None, *, timeout=60.0):
            if method != "configRequirements/read":
                raise AssertionError(method)
            return {
                "requirements": {
                    "allowedApprovalPolicies": ["never"],
                    "allowedSandboxModes": ["readOnly"],
                }
            }

        worker._request = MethodType(request, worker)

        with self.assertRaisesRegex(CodexDesktopError, "approvalPolicy"):
            worker._assert_policy_allowed(
                approval="onRequest",
                sandbox="workspaceWrite",
            )


if __name__ == "__main__":
    unittest.main()
