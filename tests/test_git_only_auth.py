from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gitlab_agent.cli import _build_parser
from gitlab_agent.config import AgentSettings
from gitlab_agent.doctor import run_doctor


class GitOnlyAuthenticationTests(unittest.TestCase):
    def _load(self, **overrides: str) -> AgentSettings:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env = {
                "GITLAB_AGENT_ENV_FILE": str(root / "missing.env"),
                "GITLAB_BASE_URL": "https://gitlab.example.test",
                "GITLAB_ALLOWED_PROJECTS": "team/project",
                "GITLAB_WORKSPACE_ROOT": str(root / "workspace"),
                **overrides,
            }
            with patch.dict(os.environ, env, clear=True):
                return AgentSettings.load()

    def test_explicit_git_token_has_clear_source(self) -> None:
        settings = self._load(
            GITLAB_TOKEN="api-token",
            GITLAB_GIT_TOKEN="git-token",
        )
        self.assertEqual(settings.git_token, "git-token")
        self.assertEqual(settings.git_credential_source, "git-token")

    def test_explicit_https_password_is_supported_without_api_token(self) -> None:
        settings = self._load(
            GITLAB_TOKEN="",
            GITLAB_GIT_PASSWORD="password-value",
            GITLAB_GIT_USERNAME="developer@example.test",
        )
        self.assertEqual(settings.api_token, "")
        self.assertEqual(settings.git_token, "password-value")
        self.assertEqual(settings.git_credential_source, "git-password")
        self.assertEqual(
            settings.git_username,
            "developer@example.test",
        )

    def test_ambiguous_git_token_and_password_fail_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "only one"):
            self._load(
                GITLAB_GIT_TOKEN="git-token",
                GITLAB_GIT_PASSWORD="password-value",
            )

    def test_api_token_remains_compatibility_fallback(self) -> None:
        settings = self._load(GITLAB_TOKEN="api-token")
        self.assertEqual(settings.git_token, "api-token")
        self.assertEqual(settings.git_credential_source, "api-token")

    def test_git_only_doctor_skips_api_but_requires_git_credential(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_file = root / ".env"
            config_file.write_text("", encoding="utf-8")
            if os.name != "nt":
                config_file.chmod(0o600)
            settings = AgentSettings(
                config_file=config_file,
                gitlab_base_url="https://gitlab.example.test",
                api_token="",
                api_verify_ssl=True,
                api_trust_env=False,
                git_token="git-secret",
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

            def which(name: str) -> str | None:
                if name in {"git", "uv", "codex"}:
                    return f"/tools/{name}"
                return None

            normal = run_doctor(
                offline=True,
                settings_loader=lambda: settings,
                which=which,
            )
            git_only = run_doctor(
                offline=True,
                git_only=True,
                settings_loader=lambda: settings,
                which=which,
            )

        self.assertFalse(normal["ok"])
        self.assertTrue(git_only["ok"])
        self.assertTrue(git_only["git_only"])
        checks = {item["name"]: item for item in git_only["checks"]}
        self.assertEqual(checks["gitlab_api_token"]["status"], "skip")
        self.assertEqual(checks["git_credential"]["status"], "pass")
        self.assertEqual(
            checks["git_credential"]["details"]["credential_source"],
            "git-password",
        )
        self.assertEqual(
            checks["gitlab_api_connectivity"]["status"],
            "skip",
        )

    def test_git_only_doctor_still_fails_without_git_credential(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            settings = AgentSettings(
                config_file=root / "missing.env",
                gitlab_base_url="https://gitlab.example.test",
                api_token="",
                api_verify_ssl=True,
                api_trust_env=False,
                git_token="",
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

            def which(name: str) -> str | None:
                return f"/tools/{name}" if name in {"git", "uv", "codex"} else None

            result = run_doctor(
                offline=True,
                git_only=True,
                settings_loader=lambda: settings,
                which=which,
            )

        self.assertFalse(result["ok"])
        checks = {item["name"]: item for item in result["checks"]}
        self.assertEqual(checks["git_credential"]["status"], "fail")

    def test_cli_exposes_git_only_on_doctor_and_start(self) -> None:
        parser = _build_parser(prog="actual-coder")
        doctor = parser.parse_args(["doctor", "--offline", "--git-only"])
        self.assertTrue(doctor.git_only)

        start = parser.parse_args(
            [
                "start",
                "team/project",
                "--goal",
                "inspect",
                "--no-launch",
                "--git-only",
            ]
        )
        self.assertTrue(start.git_only)


if __name__ == "__main__":
    unittest.main()
