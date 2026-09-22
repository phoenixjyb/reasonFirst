from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gitlab_agent.cli import _build_parser
from gitlab_agent.config import AgentSettings
from gitlab_agent.doctor import run_doctor


class GitOnlyModeTests(unittest.TestCase):
    @staticmethod
    def _which(name: str) -> str | None:
        if name in {"git", "uv", "codex"}:
            return f"/tools/{name}"
        return None

    def _settings(self, root: Path) -> AgentSettings:
        cfg = root / ".env"
        cfg.write_text("GITLAB_BASE_URL=https://gitlab.example.test\n", encoding="utf-8")
        if os.name != "nt":
            cfg.chmod(0o600)
        return AgentSettings(
            config_file=cfg,
            gitlab_base_url="https://gitlab.example.test",
            api_token="",
            api_verify_ssl=True,
            api_trust_env=False,
            git_token="git-password",
            git_username="developer",
            git_trust_env=False,
            allowed_projects={"team/project"},
            require_write_allowlist=True,
            workspace_root=root / "workspace",
            branch_prefix="chatgpt/",
            default_base_ref="main",
            allowed_executables={"python"},
            command_timeout_seconds=30,
            max_output_bytes=120000,
            max_file_bytes=1000000,
            git_author_name=None,
            git_author_email=None,
            git_credential_source="git-password",
        )

    def test_git_only_doctor_accepts_missing_api_token(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            settings = self._settings(Path(td))
            result = run_doctor(
                offline=True,
                git_only=True,
                settings_loader=lambda: settings,
                which=self._which,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["git_only"])
        checks = {item["name"]: item for item in result["checks"]}
        self.assertEqual(checks["gitlab_api_token"]["status"], "skip")
        self.assertEqual(checks["git_credential"]["status"], "pass")
        self.assertEqual(
            checks["git_credential"]["details"]["credential_source"],
            "git-password",
        )

    def test_normal_doctor_still_requires_api_token(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            settings = self._settings(Path(td))
            result = run_doctor(
                offline=True,
                settings_loader=lambda: settings,
                which=self._which,
            )

        self.assertFalse(result["ok"])
        checks = {item["name"]: item for item in result["checks"]}
        self.assertEqual(checks["gitlab_api_token"]["status"], "fail")

    def test_cli_accepts_git_only_for_doctor_and_start(self) -> None:
        parser = _build_parser(prog="actual-coder")
        doctor = parser.parse_args(["doctor", "--offline", "--git-only"])
        self.assertTrue(doctor.git_only)
        start = parser.parse_args(
            [
                "start",
                "team/project",
                "--goal",
                "test",
                "--no-launch",
                "--offline-doctor",
                "--git-only",
            ]
        )
        self.assertTrue(start.git_only)

    def test_git_credential_precedence_is_token_then_password_then_api(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = {
                "GITLAB_AGENT_ENV_FILE": str(root / "missing.env"),
                "GITLAB_BASE_URL": "https://gitlab.example.test",
                "GITLAB_ALLOWED_PROJECTS": "team/project",
                "GITLAB_WORKSPACE_ROOT": str(root / "workspace"),
            }
            cases = [
                (
                    {
                        **base,
                        "GITLAB_TOKEN": "api",
                        "GITLAB_GIT_PASSWORD": "password",
                        "GITLAB_GIT_TOKEN": "git-token",
                    },
                    "git-token",
                    "git-token",
                ),
                (
                    {
                        **base,
                        "GITLAB_TOKEN": "api",
                        "GITLAB_GIT_PASSWORD": "password",
                    },
                    "password",
                    "git-password",
                ),
                (
                    {
                        **base,
                        "GITLAB_TOKEN": "api",
                    },
                    "api",
                    "api-token",
                ),
            ]
            for env, expected_value, expected_source in cases:
                with self.subTest(source=expected_source):
                    with patch.dict(os.environ, env, clear=True):
                        settings = AgentSettings.load()
                    self.assertEqual(settings.git_token, expected_value)
                    self.assertEqual(
                        settings.git_credential_source,
                        expected_source,
                    )


if __name__ == "__main__":
    unittest.main()
