from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gitlab_agent.config import AgentSettings
from gitlab_agent.doctor import run_doctor


class FakeAPI:
    def __init__(self, settings: AgentSettings, *, fail: bool = False) -> None:
        self.settings = settings
        self.fail = fail

    def get_json(self, path: str) -> dict[str, object]:
        if self.fail:
            raise RuntimeError("network unavailable")
        if path != "/user":
            raise AssertionError(path)
        return {"username": "test-user"}


class DoctorTests(unittest.TestCase):
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
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def which(name: str) -> str | None:
        installed = {
            "git": "/tools/git",
            "uv": "/tools/uv",
            "copilot": "/tools/copilot",
            "tunnel-client": "/tools/tunnel-client",
        }
        return installed.get(name)

    def test_healthy_offline_doctor_passes_without_network(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = run_doctor(
                offline=True,
                settings_loader=lambda: self.settings,
                which=self.which,
                api_factory=lambda settings: FakeAPI(settings, fail=True),
            )

        self.assertTrue(result["ok"])
        checks = {item["name"]: item for item in result["checks"]}
        self.assertEqual(checks["coding_backend"]["status"], "pass")
        self.assertEqual(checks["gitlab_api_connectivity"]["status"], "skip")
        self.assertEqual(checks["project_allowlist"]["status"], "pass")

    def test_git_only_mode_allows_missing_api_token_when_git_credential_exists(self) -> None:
        git_only = AgentSettings(
            **{
                **self.settings.__dict__,
                "api_token": "",
                "git_token": "git-password",
                "git_credential_source": "git_password",
            }
        )

        with patch.dict(os.environ, {}, clear=True):
            result = run_doctor(
                offline=True,
                git_only=True,
                settings_loader=lambda: git_only,
                which=self.which,
                api_factory=lambda settings: FakeAPI(settings, fail=True),
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["git_only"])
        self.assertEqual(result["operating_mode"], "git-only")
        checks = {item["name"]: item for item in result["checks"]}
        self.assertEqual(checks["gitlab_api_token"]["status"], "skip")
        self.assertEqual(checks["git_credential"]["status"], "pass")
        self.assertEqual(
            checks["git_credential"]["details"]["credential_source"],
            "git_password",
        )
        self.assertEqual(checks["gitlab_api_connectivity"]["status"], "skip")

    def test_git_only_mode_still_fails_without_git_credential(self) -> None:
        broken = AgentSettings(
            **{
                **self.settings.__dict__,
                "api_token": "",
                "git_token": "",
                "git_credential_source": "none",
            }
        )

        with patch.dict(os.environ, {}, clear=True):
            result = run_doctor(
                offline=True,
                git_only=True,
                settings_loader=lambda: broken,
                which=self.which,
            )

        self.assertFalse(result["ok"])
        checks = {item["name"]: item for item in result["checks"]}
        self.assertEqual(checks["gitlab_api_token"]["status"], "skip")
        self.assertEqual(checks["git_credential"]["status"], "fail")

    def test_doctor_fails_without_backend_and_required_allowlist(self) -> None:
        broken = AgentSettings(
            **{
                **self.settings.__dict__,
                "allowed_projects": set(),
            }
        )

        def minimal_which(name: str) -> str | None:
            return "/tools/" + name if name in {"git", "uv"} else None

        with patch.dict(os.environ, {}, clear=True):
            result = run_doctor(
                offline=True,
                settings_loader=lambda: broken,
                which=minimal_which,
            )

        self.assertFalse(result["ok"])
        checks = {item["name"]: item for item in result["checks"]}
        self.assertEqual(checks["coding_backend"]["status"], "fail")
        self.assertEqual(checks["project_allowlist"]["status"], "fail")

    def test_live_gitlab_failure_is_reported(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = run_doctor(
                settings_loader=lambda: self.settings,
                which=self.which,
                api_factory=lambda settings: FakeAPI(settings, fail=True),
            )

        self.assertFalse(result["ok"])
        checks = {item["name"]: item for item in result["checks"]}
        self.assertEqual(checks["gitlab_api_connectivity"]["status"], "fail")
        self.assertIn("network unavailable", checks["gitlab_api_connectivity"]["message"])

    def test_doctor_survives_invalid_configuration(self) -> None:
        def bad_loader() -> AgentSettings:
            raise RuntimeError("GITLAB_BASE_URL is required")

        with patch.dict(os.environ, {}, clear=True):
            result = run_doctor(
                offline=True,
                settings_loader=bad_loader,
                which=self.which,
            )

        self.assertFalse(result["ok"])
        checks = {item["name"]: item for item in result["checks"]}
        self.assertEqual(checks["configuration"]["status"], "fail")


if __name__ == "__main__":
    unittest.main()
