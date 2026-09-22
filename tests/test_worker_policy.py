from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gitlab_agent.config import AgentSettings


class WorkerPolicyConfigTests(unittest.TestCase):
    def _load(self, **overrides: str) -> AgentSettings:
        with tempfile.TemporaryDirectory() as td:
            env_file = Path(td) / "missing.env"
            env = {
                "GITLAB_AGENT_ENV_FILE": str(env_file),
                "GITLAB_BASE_URL": "https://gitlab.example.test",
                "GITLAB_TOKEN": "read-token",
                "GITLAB_GIT_TOKEN": "git-token",
                "GITLAB_ALLOWED_PROJECTS": "team/project",
                **overrides,
            }
            with patch.dict(os.environ, env, clear=True):
                return AgentSettings.load()

    def test_codex_defaults_are_explicit_and_safe(self) -> None:
        settings = self._load()

        self.assertEqual(settings.codex_model, "gpt-5.6-sol")
        self.assertEqual(settings.codex_reasoning_effort, "high")
        self.assertEqual(settings.codex_execution_mode, "interactive")
        self.assertEqual(settings.codex_sandbox_mode, "workspace-write")
        self.assertEqual(settings.codex_approval_policy, "on-request")
        self.assertFalse(settings.codex_network_access)

    def test_copilot_defaults_preserve_provider_model_and_block_push(self) -> None:
        settings = self._load()

        self.assertIsNone(settings.copilot_model)
        self.assertIsNone(settings.copilot_reasoning_effort)
        self.assertEqual(settings.copilot_execution_mode, "interactive")
        self.assertTrue(settings.copilot_disable_builtin_mcps)
        self.assertEqual(settings.copilot_allow_tools, ())
        self.assertEqual(settings.copilot_deny_tools, ("shell(git push)",))

    def test_explicit_provider_settings_are_loaded(self) -> None:
        settings = self._load(
            REASONFIRST_CODEX_MODEL="gpt-5.6-sol",
            REASONFIRST_CODEX_REASONING_EFFORT="xhigh",
            REASONFIRST_CODEX_EXECUTION_MODE="exec",
            REASONFIRST_CODEX_SANDBOX="read-only",
            REASONFIRST_CODEX_APPROVAL_POLICY="never",
            REASONFIRST_CODEX_NETWORK_ACCESS="true",
            REASONFIRST_COPILOT_MODEL="gpt-5.3-codex",
            REASONFIRST_COPILOT_REASONING_EFFORT="high",
            REASONFIRST_COPILOT_EXECUTION_MODE="programmatic",
            REASONFIRST_COPILOT_DISABLE_BUILTIN_MCPS="false",
            REASONFIRST_COPILOT_ALLOW_TOOLS="write,shell(pytest)",
            REASONFIRST_COPILOT_DENY_TOOLS="shell(git push),shell(rm)",
        )

        self.assertEqual(settings.codex_reasoning_effort, "xhigh")
        self.assertEqual(settings.codex_execution_mode, "exec")
        self.assertEqual(settings.codex_sandbox_mode, "read-only")
        self.assertEqual(settings.codex_approval_policy, "never")
        self.assertTrue(settings.codex_network_access)
        self.assertEqual(settings.copilot_model, "gpt-5.3-codex")
        self.assertEqual(settings.copilot_reasoning_effort, "high")
        self.assertEqual(settings.copilot_execution_mode, "programmatic")
        self.assertFalse(settings.copilot_disable_builtin_mcps)
        self.assertEqual(settings.copilot_allow_tools, ("write", "shell(pytest)"))
        self.assertEqual(
            settings.copilot_deny_tools,
            ("shell(git push)", "shell(rm)"),
        )

    def test_invalid_policy_choice_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "REASONFIRST_CODEX_SANDBOX"):
            self._load(REASONFIRST_CODEX_SANDBOX="danger-full-access")


if __name__ == "__main__":
    unittest.main()
