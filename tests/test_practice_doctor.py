from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from gitlab_agent.config import AgentSettings
from gitlab_agent.practice import run_practice_doctor


PRACTICE_CONFIG = """version: 1
project:
  base_branch: main
agents:
  preferred: [codex, copilot]
validation:
  commands:
    - name: unit-tests
      argv: [python3, -m, unittest, discover, -s, tests, -v]
      required: true
      timeout_seconds: 120
protected_paths:
  - .actualcoder.yaml
  - .gitlab-ci.yml
  - AGENTS.md
  - EXERCISE.md
instructions:
  - Synthetic practice only.
executables:
  required: [python3]
mr:
  target_branch: main
  title_prefix: "practice: "
"""


class FakeManager:
    def __init__(self, settings: AgentSettings, *, missing: set[str] | None = None) -> None:
        self.settings = settings
        self.missing = missing or set()
        self.commit = "abc123"

    def read_remote_text_file(
        self,
        project: str,
        relative_path: str,
        *,
        ref: str | None = None,
        refresh_remote: bool = True,
        max_bytes: int | None = None,
    ) -> dict[str, object]:
        exists = relative_path not in self.missing
        content = PRACTICE_CONFIG if relative_path == ".actualcoder.yaml" and exists else (
            f"content for {relative_path}\n" if exists else None
        )
        return {
            "project": project,
            "ref": ref or "main",
            "commit_sha": self.commit,
            "path": relative_path,
            "exists": exists,
            "content": content,
        }


class FakeAPI:
    def __init__(self, settings: AgentSettings, *, runners: list[dict[str, object]] | None = None) -> None:
        self.settings = settings
        self.runners = runners if runners is not None else [
            {
                "id": 6,
                "description": "practice-shell",
                "status": "online",
                "paused": False,
                "active": True,
                "runner_type": "project_type",
                "run_untagged": True,
                "access_level": "not_protected",
                "tag_list": ["general"],
            }
        ]

    def project_runners(self, project: str) -> list[dict[str, object]]:
        return list(self.runners)


class PracticeDoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        config_file = root / ".env"
        config_file.write_text("GITLAB_BASE_URL=https://gitlab.example.test\n", encoding="utf-8")
        if os.name != "nt":
            config_file.chmod(0o600)
        self.runner_config = root / "config.toml"
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
            git_author_name=None,
            git_author_email=None,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def which(name: str) -> str | None:
        installed = {
            "gitlab-runner": "/tools/gitlab-runner",
            "copilot": "/tools/copilot",
            "codex": "/tools/codex",
            "python3": "/tools/python3",
        }
        return installed.get(name)

    def test_ready_practice_reports_stage1_and_runner_readiness(self) -> None:
        self.runner_config.write_text(
            """concurrent = 1
[[runners]]
  name = "practice-shell"
  url = "https://gitlab.example.test"
  token = "must-not-leak"
  executor = "shell"
""",
            encoding="utf-8",
        )
        with self.subTest("ready"):
            result = run_practice_doctor(
                project="team/reasonfirst-practice",
                ref="main",
                agent="copilot-cli",
                settings_loader=lambda: self.settings,
                manager_factory=lambda settings: FakeManager(settings),
                api_factory=lambda settings: FakeAPI(settings),
                which=self.which,
                environ={},
                runner_config_path=self.runner_config,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["ready_for_stage1"])
        self.assertEqual(result["resolved_commit_sha"], "abc123")
        self.assertEqual(result["stage1"]["task"], "stage1-strict-summary")
        checks = {item["name"]: item for item in result["checks"]}
        self.assertEqual(checks["practice_worker"]["status"], "pass")
        self.assertEqual(checks["practice_required_files"]["status"], "pass")
        self.assertEqual(checks["practice_project_contract"]["status"], "pass")
        self.assertEqual(checks["gitlab_runner_local_config"]["status"], "pass")
        self.assertEqual(checks["gitlab_project_runner"]["status"], "pass")
        self.assertNotIn("must-not-leak", repr(result))

    def test_unseeded_project_and_custom_runner_fail_closed(self) -> None:
        self.runner_config.write_text(
            """[[runners]]
  name = "broken-custom"
  url = "https://gitlab.example.test"
  executor = "custom"
  token = "secret"
""",
            encoding="utf-8",
        )
        offline_runner = {
            "id": 5,
            "description": "never-contacted",
            "status": "offline",
            "paused": False,
            "active": True,
            "runner_type": "project_type",
            "run_untagged": False,
            "access_level": "not_protected",
            "tag_list": ["general"],
        }

        result = run_practice_doctor(
            project="team/reasonfirst-practice",
            ref="main",
            agent="copilot-cli",
            settings_loader=lambda: self.settings,
            manager_factory=lambda settings: FakeManager(
                settings,
                missing={"EXERCISE.md", ".actualcoder.yaml"},
            ),
            api_factory=lambda settings: FakeAPI(settings, runners=[offline_runner]),
            which=self.which,
            environ={"HTTPS_PROXY": "http://127.0.0.1:7890"},
            runner_config_path=self.runner_config,
        )

        self.assertFalse(result["ok"])
        checks = {item["name"]: item for item in result["checks"]}
        self.assertEqual(checks["practice_required_files"]["status"], "fail")
        self.assertEqual(checks["practice_project_contract"]["status"], "fail")
        self.assertEqual(checks["external_client_proxy"]["status"], "warn")
        self.assertEqual(checks["gitlab_runner_local_config"]["status"], "fail")
        self.assertEqual(checks["gitlab_project_runner"]["status"], "fail")

    def test_no_proxy_avoids_external_client_proxy_warning(self) -> None:
        self.runner_config.write_text(
            """[[runners]]
  name = "practice-shell"
  url = "https://gitlab.example.test"
  executor = "shell"
""",
            encoding="utf-8",
        )
        result = run_practice_doctor(
            project="team/reasonfirst-practice",
            agent="copilot-cli",
            settings_loader=lambda: self.settings,
            manager_factory=lambda settings: FakeManager(settings),
            api_factory=lambda settings: FakeAPI(settings),
            which=self.which,
            environ={
                "HTTPS_PROXY": "http://127.0.0.1:7890",
                "NO_PROXY": "gitlab.example.test,127.0.0.1,localhost",
            },
            runner_config_path=self.runner_config,
        )
        checks = {item["name"]: item for item in result["checks"]}
        self.assertEqual(checks["external_client_proxy"]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
