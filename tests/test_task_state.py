from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from gitlab_agent.config import AgentSettings
from gitlab_agent.workspace import WorkspaceManager


def run(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(
        list(args),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return proc.stdout.strip()


class TaskStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        seed = root / "seed"
        remote = root / "remote.git"
        seed.mkdir()
        run("git", "init", "-b", "main", cwd=seed)
        run("git", "config", "user.name", "Test User", cwd=seed)
        run("git", "config", "user.email", "test@example.com", cwd=seed)
        (seed / "README.md").write_text("seed\n", encoding="utf-8")
        run("git", "add", "README.md", cwd=seed)
        run("git", "commit", "-m", "Initial commit", cwd=seed)
        run("git", "clone", "--bare", str(seed), str(remote))

        self.settings = AgentSettings(
            config_file=root / ".env",
            gitlab_base_url="https://gitlab.example.invalid",
            api_token="",
            api_verify_ssl=True,
            api_trust_env=False,
            git_token="",
            git_username="oauth2",
            git_trust_env=False,
            allowed_projects={"team/project"},
            require_write_allowlist=True,
            workspace_root=root / "agent",
            branch_prefix="chatgpt/",
            default_base_ref="main",
            allowed_executables={"python"},
            command_timeout_seconds=30,
            max_output_bytes=120000,
            max_file_bytes=1000000,
            git_author_name="Agent Test",
            git_author_email="agent@example.com",
        )
        self.manager = WorkspaceManager(
            self.settings,
            url_resolver=lambda project: str(remote),
        )
        created = self.manager.create_workspace(
            "team/project",
            task_slug="persistent-task",
        )
        self.workspace_id = str(created["workspace_id"])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_task_spec_persists_and_attempts_are_bounded(self) -> None:
        status = self.manager.status(self.workspace_id)
        spec = self.manager.ensure_task_spec(
            self.workspace_id,
            task_slug="persistent-task",
            goal="Implement the reviewed task",
            requested_backend="auto",
            project_config_sha=str(status["base_sha"]),
            acceptance_criteria=["Tests pass", "API stays compatible"],
            non_goals=["No unrelated refactor"],
        )
        self.assertEqual(spec["goal"], "Implement the reviewed task")
        self.assertEqual(
            spec["acceptance_criteria"],
            ["Tests pass", "API stays compatible"],
        )

        for index in range(105):
            self.manager.record_attempt(
                self.workspace_id,
                source="resume",
                goal=f"attempt {index}",
                requested_backend="auto",
                selected_backend="codex-cli",
                ci_context_included=False,
                worker_policy={
                    "backend": "codex",
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "high",
                },
            )

        context = self.manager.task_context(self.workspace_id)
        self.assertEqual(context["attempt_count"], 100)
        attempts = context["attempts"]
        self.assertEqual(attempts[0]["goal"], "attempt 5")
        self.assertEqual(attempts[-1]["goal"], "attempt 104")

        reloaded = WorkspaceManager(
            self.settings,
            url_resolver=self.manager._url_resolver,
        )
        persisted = reloaded.task_context(self.workspace_id)
        self.assertEqual(persisted["task_spec"]["goal"], "Implement the reviewed task")
        self.assertEqual(persisted["attempt_count"], 100)

        state_path = self.manager._state_path(self.workspace_id)
        if os.name != "nt":
            self.assertEqual(state_path.stat().st_mode & 0o777, 0o600)

    def test_attempt_goal_is_a_bounded_preview(self) -> None:
        long_goal = "x" * 6000
        attempt = self.manager.record_attempt(
            self.workspace_id,
            source="continue",
            goal=long_goal,
            requested_backend="auto",
            selected_backend="codex-cli",
            ci_context_included=False,
            worker_policy={"backend": "codex"},
        )
        self.assertEqual(len(attempt["goal"]), 4096)
        self.assertTrue(attempt["goal_truncated"])

        reloaded = self.manager.task_context(self.workspace_id)
        self.assertTrue(reloaded["attempts"][0]["goal_truncated"])

    def test_legacy_workspace_state_loads_without_task_fields(self) -> None:
        state_path = self.manager._state_path(self.workspace_id)
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        raw.pop("task_spec", None)
        raw.pop("attempts", None)
        state_path.write_text(json.dumps(raw), encoding="utf-8")

        state = self.manager.get_state(self.workspace_id)
        self.assertIsNone(state.task_spec)
        self.assertEqual(state.attempts, [])

        spec = self.manager.ensure_task_spec(
            self.workspace_id,
            task_slug="legacy-upgrade",
            goal="Recover legacy workspace",
            requested_backend="auto",
            project_config_sha=None,
        )
        self.assertEqual(spec["goal"], "Recover legacy workspace")

    def test_existing_task_spec_is_bound_to_workspace_identity(self) -> None:
        self.manager.ensure_task_spec(
            self.workspace_id,
            task_slug="bound",
            goal="Bound task",
            requested_backend="auto",
            project_config_sha=None,
        )
        state_path = self.manager._state_path(self.workspace_id)
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        raw["task_spec"]["base_sha"] = "0" * 40
        state_path.write_text(json.dumps(raw), encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "identity does not match"):
            self.manager.ensure_task_spec(
                self.workspace_id,
                task_slug="bound",
                goal="Bound task",
                requested_backend="auto",
            )


if __name__ == "__main__":
    unittest.main()
