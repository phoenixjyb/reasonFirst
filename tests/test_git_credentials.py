from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gitlab_agent.config import AgentSettings


class GitCredentialConfigTests(unittest.TestCase):
    def _load(self, **overrides: str) -> AgentSettings:
        with tempfile.TemporaryDirectory() as td:
            env = {
                "GITLAB_AGENT_ENV_FILE": str(Path(td) / "missing.env"),
                "GITLAB_BASE_URL": "https://gitlab.example.test",
                "GITLAB_ALLOWED_PROJECTS": "team/project",
                "GITLAB_WORKSPACE_ROOT": str(Path(td) / "workspace"),
                **overrides,
            }
            with patch.dict(os.environ, env, clear=True):
                return AgentSettings.load()

    def test_scoped_git_token_has_highest_precedence(self) -> None:
        settings = self._load(
            GITLAB_TOKEN="api-token",
            GITLAB_GIT_PASSWORD="account-password",
            GITLAB_GIT_TOKEN="scoped-git-token",
        )

        self.assertEqual(settings.git_token, "scoped-git-token")
        self.assertEqual(settings.git_credential_source, "git_token")

    def test_explicit_git_password_precedes_api_fallback(self) -> None:
        settings = self._load(
            GITLAB_TOKEN="api-token",
            GITLAB_GIT_PASSWORD="account-password",
            GITLAB_GIT_TOKEN="",
        )

        self.assertEqual(settings.git_token, "account-password")
        self.assertEqual(settings.git_credential_source, "git_password")

    def test_api_token_remains_compatibility_fallback(self) -> None:
        settings = self._load(
            GITLAB_TOKEN="api-token",
            GITLAB_GIT_PASSWORD="",
            GITLAB_GIT_TOKEN="",
        )

        self.assertEqual(settings.git_token, "api-token")
        self.assertEqual(settings.git_credential_source, "api_token_fallback")

    def test_git_only_configuration_can_omit_api_token(self) -> None:
        settings = self._load(
            GITLAB_TOKEN="",
            GITLAB_GIT_PASSWORD="account-password",
            GITLAB_GIT_USERNAME="user@example.com",
        )

        self.assertEqual(settings.api_token, "")
        self.assertEqual(settings.git_token, "account-password")
        self.assertEqual(settings.git_username, "user@example.com")
        self.assertEqual(settings.git_credential_source, "git_password")


if __name__ == "__main__":
    unittest.main()
