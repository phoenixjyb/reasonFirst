from __future__ import annotations

import unittest

from gitlab_agent.codex_desktop import (
    desktop_app_server_argv,
    desktop_thread_params,
    desktop_turn_params,
)
from gitlab_agent.worker_policy import WorkerPolicy


class CodexDesktopAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = WorkerPolicy(
            backend="codex-desktop",
            model="gpt-5.6-sol",
            reasoning_effort="high",
            execution_mode="interactive",
            sandbox_mode="workspace-write",
            approval_policy="on-request",
            network_access=False,
        )

    def test_app_server_launch_disables_reasonfirst_recursion(self) -> None:
        self.assertEqual(
            desktop_app_server_argv("/Applications/Codex.app/Contents/Resources/codex"),
            [
                "/Applications/Codex.app/Contents/Resources/codex",
                "--config",
                "mcp_servers.reasonfirst.enabled=false",
                "app-server",
            ],
        )

    def test_thread_params_map_worker_policy(self) -> None:
        params = desktop_thread_params(self.policy, "/tmp/worktree")

        self.assertEqual(params["model"], "gpt-5.6-sol")
        self.assertEqual(params["effort"], "high")
        self.assertEqual(params["approvalPolicy"], "on-request")
        self.assertEqual(params["sandbox"], "workspace-write")
        self.assertEqual(params["cwd"], "/tmp/worktree")

    def test_turn_params_keep_workspace_and_network_boundary(self) -> None:
        params = desktop_turn_params(
            self.policy,
            thread_id="thr-1",
            cwd="/tmp/worktree",
            prompt="Implement the reviewed change",
        )

        self.assertEqual(params["model"], "gpt-5.6-sol")
        self.assertEqual(params["effort"], "high")
        self.assertEqual(params["approvalPolicy"], "on-request")
        self.assertEqual(
            params["sandboxPolicy"],
            {
                "type": "workspaceWrite",
                "writableRoots": ["/tmp/worktree"],
                "networkAccess": False,
            },
        )

    def test_read_only_policy_maps_to_read_only_sandbox(self) -> None:
        policy = WorkerPolicy(
            backend="codex-desktop",
            model="gpt-5.6-sol",
            reasoning_effort="medium",
            execution_mode="interactive",
            sandbox_mode="read-only",
            approval_policy="never",
            network_access=False,
        )
        params = desktop_turn_params(
            policy,
            thread_id="thr-2",
            cwd="/tmp/worktree",
            prompt="Inspect only",
        )

        self.assertEqual(
            params["sandboxPolicy"],
            {"type": "readOnly", "networkAccess": False},
        )
        self.assertEqual(params["approvalPolicy"], "never")

    def test_non_desktop_policy_is_rejected(self) -> None:
        policy = WorkerPolicy(
            backend="codex-cli",
            model="gpt-5.6-sol",
            reasoning_effort="high",
            execution_mode="interactive",
            sandbox_mode="workspace-write",
            approval_policy="on-request",
            network_access=False,
        )
        with self.assertRaises(ValueError):
            desktop_thread_params(policy, "/tmp/worktree")


if __name__ == "__main__":
    unittest.main()
