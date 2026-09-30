from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gitlab_agent.config import AgentSettings
from gitlab_agent import setup_actions, setup_config
from gitlab_agent.setup_actions import (
    apply_project_add,
    apply_worker_use,
    candidate_settings,
    plan_project_add,
    plan_worker_use,
)
from gitlab_agent.setup_config import ConfigMutationError


def settings_for(config: Path, *, api_token: str = "api-token", git_token: str = "api-token") -> AgentSettings:
    return AgentSettings(
        config_file=config,
        gitlab_base_url="https://gitlab.example.test",
        api_token=api_token,
        api_verify_ssl=True,
        api_trust_env=False,
        git_token=git_token,
        git_username="oauth2",
        git_trust_env=False,
        allowed_projects={"team/one"},
        require_write_allowlist=True,
        workspace_root=config.parent / "workspace",
        branch_prefix="chatgpt/",
        default_base_ref="main",
        allowed_executables={"python", "uv"},
        command_timeout_seconds=30,
        max_output_bytes=120000,
        max_file_bytes=1000000,
        git_author_name=None,
        git_author_email=None,
        default_backend="auto",
    )


class SetupActionsTests(unittest.TestCase):
    def test_project_add_verifies_before_local_grant(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / ".env"
            config.write_text(
                "GITLAB_BASE_URL=https://gitlab.example.test\n"
                "GITLAB_TOKEN=secret\n"
                "GITLAB_ALLOWED_PROJECTS=team/one\n",
                encoding="utf-8",
            )
            s = settings_for(config)
            target_patch = patch.object(setup_config, "selected_user_config_path", return_value=config)
            alias_patch = patch.object(setup_actions, "selected_user_config_path", return_value=config)
            preflight = {
                "ok": True,
                "resolved_commit_sha": "abc123",
                "ref": "main",
                "writes_performed": False,
            }
            with target_patch, alias_patch, patch.object(
                setup_actions, "preflight_project", return_value=preflight
            ) as probe:
                plan = plan_project_add(s, "team/two", ref="main")
                probe.assert_called_once()
                self.assertEqual(plan["before_projects"], ["team/one"])
                self.assertEqual(plan["after_projects"], ["team/one", "team/two"])
                self.assertFalse(plan["writes_performed"])
                with patch.dict(os.environ, {}, clear=True):
                    result = apply_project_add(s, plan)

            self.assertTrue(result["writes_performed"])
            self.assertTrue(result["reload_required"])
            text = config.read_text()
            self.assertIn("GITLAB_TOKEN=secret", text)
            self.assertIn("GITLAB_ALLOWED_PROJECTS=team/one,team/two", text)

    def test_failed_project_preflight_is_never_applied(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / ".env"
            config.write_text("GITLAB_ALLOWED_PROJECTS=team/one\n", encoding="utf-8")
            s = settings_for(config)
            with (
                patch.object(setup_config, "selected_user_config_path", return_value=config),
                patch.object(setup_actions, "selected_user_config_path", return_value=config),
                patch.object(
                    setup_actions,
                    "preflight_project",
                    return_value={"ok": False, "error": "project_missing_or_inaccessible"},
                ),
            ):
                plan = plan_project_add(s, "team/two")
                self.assertFalse(plan["ok"])
                with self.assertRaisesRegex(ConfigMutationError, "preflight"):
                    apply_project_add(s, plan)

    def test_stale_project_plan_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / ".env"
            config.write_text("GITLAB_ALLOWED_PROJECTS=team/one\n", encoding="utf-8")
            s = settings_for(config)
            plan = {
                "ok": True,
                "project": "team/two",
                "before_projects": [],
                "after_projects": ["team/two"],
                "already_allowed": False,
                "config_file": str(config),
            }
            with self.assertRaisesRegex(ConfigMutationError, "changed after planning"):
                apply_project_add(s, plan)

    def test_already_allowed_project_is_noop_after_verification(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / ".env"
            config.write_text("GITLAB_ALLOWED_PROJECTS=team/one\n", encoding="utf-8")
            s = settings_for(config)
            with (
                patch.object(setup_config, "selected_user_config_path", return_value=config),
                patch.object(setup_actions, "selected_user_config_path", return_value=config),
                patch.object(
                    setup_actions,
                    "preflight_project",
                    return_value={"ok": True, "resolved_commit_sha": "abc", "ref": "main"},
                ),
            ):
                plan = plan_project_add(s, "team/one")
                result = apply_project_add(s, plan)
            self.assertTrue(plan["already_allowed"])
            self.assertFalse(result["writes_performed"])
            self.assertFalse(result["reload_required"])

    def test_worker_plan_requires_specific_backend_to_exist(self) -> None:
        def which(name: str) -> str | None:
            return "/tools/codex" if name == "codex" else None

        with tempfile.TemporaryDirectory() as td:
            socket = Path(td) / "desktop.sock"
            good = plan_worker_use("codex-cli", which=which, desktop_socket=socket)
            bad = plan_worker_use("copilot-cli", which=which, desktop_socket=socket)
            auto = plan_worker_use("auto", which=which, desktop_socket=socket)
        self.assertTrue(good["ok"])
        self.assertFalse(bad["ok"])
        self.assertTrue(auto["ok"])
        self.assertFalse(good["authentication_verified"])

    def test_worker_preference_write_is_atomic_and_nonsecret(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / ".env"
            config.write_text("GITLAB_TOKEN=secret\n", encoding="utf-8")
            plan = {
                "ok": True,
                "backend": "auto",
                "operation": "worker-use",
                "message": "ok",
            }
            with (
                patch.object(setup_actions, "selected_user_config_path", return_value=config),
                patch.dict(os.environ, {}, clear=True),
            ):
                result = apply_worker_use(plan)
            self.assertTrue(result["writes_performed"])
            text = config.read_text()
            self.assertIn("GITLAB_TOKEN=secret", text)
            self.assertIn("REASONFIRST_DEFAULT_BACKEND=auto", text)

    def test_candidate_settings_updates_api_fallback_but_preserves_separate_git_token(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / ".env"
            fallback = settings_for(config, api_token="old", git_token="old")
            updated = candidate_settings(
                base_url="https://gitlab.example.test",
                api_token="new",
                project="team/two",
                default_backend="codex-cli",
                current=fallback,
            )
            self.assertEqual(updated.git_token, "new")

            separate = settings_for(config, api_token="old", git_token="separate")
            updated2 = candidate_settings(
                base_url="https://gitlab.example.test",
                api_token="new",
                project="team/two",
                default_backend="codex-cli",
                current=separate,
            )
            self.assertEqual(updated2.git_token, "separate")


if __name__ == "__main__":
    unittest.main()
