from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from gitlab_agent.config import AgentSettings
from gitlab_agent.project_config import parse_project_config
from gitlab_agent.remote_targets import RemoteTarget
from gitlab_agent.remote_validation import (
    RemoteValidationError,
    RemoteValidationRunner,
)


class RemoteValidationRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = AgentSettings(
            config_file=root / ".env",
            gitlab_base_url="https://gitlab.example.test",
            api_token="token",
            api_verify_ssl=True,
            api_trust_env=False,
            git_token="token",
            git_username="oauth2",
            git_trust_env=False,
            allowed_projects={"team/project"},
            require_write_allowlist=True,
            workspace_root=root / "workspace",
            branch_prefix="chatgpt/",
            default_base_ref="main",
            allowed_executables={"pytest", "uv"},
            command_timeout_seconds=300,
            max_output_bytes=120000,
            max_file_bytes=1000000,
            git_author_name=None,
            git_author_email=None,
        )
        self.config = parse_project_config(
            """
version: 1
validation:
  commands:
    - name: unit
      argv: [pytest, -q]
      timeout_seconds: 90
    - name: integration
      argv: [uv, run, pytest, tests/integration]
      required: false
""",
            settings=self.settings,
            source_ref="main",
        )
        self.target = RemoteTarget(
            name="gpu-a",
            host="gpu-a",
            repo="/srv/repo",
            workspace_root="/srv/reasonfirst/worktrees",
            allowed_projects=("team/project",),
            ssh_connect_timeout=7,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_runner_transports_exact_contract_argv_without_shell_payload(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_runner(argv, **kwargs):
            calls.append({"argv": list(argv), **kwargs})
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=json.dumps(
                    {
                        "returncode": 0,
                        "timed_out": False,
                        "stdout": "ok\n",
                        "stderr": "",
                        "duration_ms": 12,
                    }
                ),
                stderr="",
            )

        result = RemoteValidationRunner(
            self.target,
            runner=fake_runner,
        ).run(
            project="team/project",
            worktree_path="/srv/reasonfirst/worktrees/ws-1",
            project_config=self.config,
            validation_name="unit",
        )

        self.assertEqual(result["argv"], ["pytest", "-q"])
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["timeout_seconds"], 90)

        call = calls[0]
        ssh_argv = call["argv"]
        self.assertEqual(ssh_argv[0], "ssh")
        self.assertIn("BatchMode=yes", ssh_argv)
        self.assertIn("ConnectTimeout=7", ssh_argv)
        self.assertEqual(ssh_argv[-2], "gpu-a")
        # User/project argv is carried only as JSON stdin, never interpolated
        # into the remote shell command string.
        self.assertNotIn("pytest", ssh_argv[-1])

        payload = json.loads(call["input"])
        self.assertEqual(payload["argv"], ["pytest", "-q"])
        self.assertEqual(
            payload["workspace_root"],
            "/srv/reasonfirst/worktrees",
        )

    def test_unknown_or_forged_command_is_not_accepted(self) -> None:
        runner = RemoteValidationRunner(
            self.target,
            runner=lambda *args, **kwargs: None,
        )
        with self.assertRaisesRegex(RemoteValidationError, "not an exact unique"):
            runner.run(
                project="team/project",
                worktree_path="/srv/reasonfirst/worktrees/ws-1",
                project_config=self.config,
                validation_name="bash -lc whoami",
            )

    def test_project_target_grant_is_enforced_before_ssh(self) -> None:
        called = False

        def should_not_run(*args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("SSH must not run")

        runner = RemoteValidationRunner(self.target, runner=should_not_run)
        with self.assertRaisesRegex(Exception, "not allowlisted"):
            runner.run(
                project="other/project",
                worktree_path="/srv/reasonfirst/worktrees/ws-1",
                project_config=self.config,
                validation_name="unit",
            )
        self.assertFalse(called)

    def test_remote_worktree_must_be_absolute_and_safe(self) -> None:
        runner = RemoteValidationRunner(
            self.target,
            runner=lambda *args, **kwargs: None,
        )
        with self.assertRaisesRegex(RemoteValidationError, "absolute safe"):
            runner.run(
                project="team/project",
                worktree_path="../escape",
                project_config=self.config,
                validation_name="unit",
            )

    def test_invalid_project_contract_blocks_execution(self) -> None:
        invalid = parse_project_config(
            """
version: 1
validation:
  commands:
    - name: unsafe
      argv: [bash, -lc, whoami]
""",
            settings=self.settings,
            source_ref="main",
        )
        self.assertFalse(invalid.valid)
        runner = RemoteValidationRunner(
            self.target,
            runner=lambda *args, **kwargs: None,
        )
        with self.assertRaisesRegex(RemoteValidationError, "invalid"):
            runner.run(
                project="team/project",
                worktree_path="/srv/reasonfirst/worktrees/ws-1",
                project_config=invalid,
                validation_name="unsafe",
            )


if __name__ == "__main__":
    unittest.main()
