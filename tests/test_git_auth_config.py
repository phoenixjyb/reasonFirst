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

    def test_explicit_git_password_works_without_api_token(self) -> None:
        settings = self._load(
            GITLAB_TOKEN="",
            GITLAB_GIT_TOKEN="",
            GITLAB_GIT_PASSWORD="password-secret",
            GITLAB_GIT_USERNAME="alice",
        )
        self.assertEqual(settings.api_token, "")
        self.assertEqual(settings.git_token, "password-secret")
        self.assertEqual(settings.git_username, "alice")

    def test_scoped_git_token_remains_preferred_to_api_fallback(self) -> None:
        settings = self._load(
            GITLAB_TOKEN="api-secret",
            GITLAB_GIT_TOKEN="git-secret",
            GITLAB_GIT_PASSWORD="",
        )
        self.assertEqual(settings.git_token, "git-secret")

    def test_api_token_is_last_resort_git_fallback(self) -> None:
        settings = self._load(
            GITLAB_TOKEN="api-secret",
            GITLAB_GIT_TOKEN="",
            GITLAB_GIT_PASSWORD="",
        )
        self.assertEqual(settings.git_token, "api-secret")

    def test_ambiguous_git_token_and_password_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "only one of GITLAB_GIT_TOKEN"):
            self._load(
                GITLAB_TOKEN="",
                GITLAB_GIT_TOKEN="git-secret",
                GITLAB_GIT_PASSWORD="password-secret",
            )


if __name__ == "__main__":
    unittest.main()
