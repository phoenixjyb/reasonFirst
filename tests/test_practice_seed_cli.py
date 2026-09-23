from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gitlab_agent.actual_coder_cli import main
from gitlab_agent.config import AgentSettings


class PracticeSeedCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        config_file = root / ".env"
        config_file.write_text("GITLAB_BASE_URL=https://gitlab.example.test\n", encoding="utf-8")
        if os.name != "nt":
            config_file.chmod(0o600)
        self.settings = AgentSettings(
            config_file=config_file,
            gitlab_base_url="https://gitlab.example.test",
            api_token="read-token",
            api_verify_ssl=True,
            api_trust_env=False,
            git_token="write-token",
            git_username="oauth2",
            git_trust_env=False,
            allowed_projects={"team/reasonfirst-practice"},
            require_write_allowlist=True,
            workspace_root=root / "workspace",
            branch_prefix="chatgpt/",
            default_base_ref="main",
            allowed_executables={"python3"},
            command_timeout_seconds=30,
            max_output_bytes=120000,
            max_file_bytes=1000000,
            git_author_name="ReasonFirst Test",
            git_author_email="reasonfirst@example.test",
        )
        self.plan = {
            "ok": True,
            "dry_run": True,
            "project": "team/reasonfirst-practice",
            "ref": "main",
            "seedable": True,
            "write_performed": False,
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_default_is_dry_run_and_never_executes_seed(self) -> None:
        with (
            patch("gitlab_agent.cli.AgentSettings.load", return_value=self.settings),
            patch("gitlab_agent.cli.build_practice_seed_plan", return_value=self.plan) as plan,
            patch("gitlab_agent.cli.execute_practice_seed") as execute,
        ):
            code = main(["practice-seed", "team/reasonfirst-practice"])

        self.assertEqual(code, 0)
        plan.assert_called_once()
        execute.assert_not_called()

    def test_apply_requires_explicit_synthetic_confirmation(self) -> None:
        with (
            patch("gitlab_agent.cli.AgentSettings.load", return_value=self.settings),
            patch("gitlab_agent.cli.build_practice_seed_plan", return_value=self.plan),
            patch("gitlab_agent.cli.execute_practice_seed") as execute,
        ):
            code = main([
                "practice-seed",
                "team/reasonfirst-practice",
                "--apply",
                "--yes",
            ])

        self.assertEqual(code, 1)
        execute.assert_not_called()

    def test_confirmed_yes_apply_calls_seed_engine(self) -> None:
        result = {
            "ok": True,
            "dry_run": False,
            "write_performed": True,
            "seed_commit": "abc123",
        }
        with (
            patch("gitlab_agent.cli.AgentSettings.load", return_value=self.settings),
            patch("gitlab_agent.cli.build_practice_seed_plan", return_value=self.plan),
            patch("gitlab_agent.cli.execute_practice_seed", return_value=result) as execute,
        ):
            code = main([
                "practice-seed",
                "team/reasonfirst-practice",
                "--apply",
                "--confirm-synthetic",
                "--yes",
            ])

        self.assertEqual(code, 0)
        execute.assert_called_once()


if __name__ == "__main__":
    unittest.main()
