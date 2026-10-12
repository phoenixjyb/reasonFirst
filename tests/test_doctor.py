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
        self.home = root / "home"
        self.home.mkdir()
        self.environment = {"HOME": str(self.home), "USERPROFILE": str(self.home)}
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
        with patch.dict(os.environ, self.environment, clear=True):
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
        self.assertEqual(
            checks["tunnel_client"]["details"]["path"], str(Path("/tools/tunnel-client"))
        )

    def test_managed_tunnel_bundle_is_discovered_without_path_or_process_launch(self) -> None:
        bundle = self.home / ".local/share/reasonfirst/tunnel-client/v1.0.0-linux-amd64-fixture"
        bundle.mkdir(parents=True)
        executable_name = "tunnel-client.exe" if os.name == "nt" else "tunnel-client"
        executable = bundle / executable_name
        executable.write_bytes(b"executable discovery fixture; never run")
        executable.chmod(0o700)

        def without_tunnel(name: str) -> str | None:
            return None if name in {"tunnel-client", "tunnel-client.exe"} else self.which(name)

        with (
            patch.dict(os.environ, self.environment, clear=True),
            patch("subprocess.Popen", side_effect=AssertionError("Doctor must not launch the tunnel")),
        ):
            result = run_doctor(
                offline=True,
                settings_loader=lambda: self.settings,
                which=without_tunnel,
                api_factory=lambda settings: FakeAPI(settings, fail=True),
            )

        self.assertTrue(result["ok"])
        checks = {item["name"]: item for item in result["checks"]}
        tunnel = checks["tunnel_client"]
        self.assertEqual(tunnel["status"], "pass")
        self.assertEqual(tunnel["details"]["path"], str(executable.resolve()))
        self.assertFalse(tunnel["details"]["runtime_health_checked"])
        self.assertIn("reasonfirst tunnel status", tunnel["message"])

    def test_tunnel_key_check_only_reports_current_process_without_exposing_value(self) -> None:
        secret = "doctor-test-runtime-secret"
        for present in (False, True):
            with self.subTest(present=present):
                environment = dict(self.environment)
                if present:
                    environment["CONTROL_PLANE_API_KEY"] = secret
                with patch.dict(os.environ, environment, clear=True):
                    result = run_doctor(
                        offline=True,
                        settings_loader=lambda: self.settings,
                        which=self.which,
                    )

                self.assertTrue(result["ok"])
                checks = {item["name"]: item for item in result["checks"]}
                key = checks["tunnel_runtime_key"]
                self.assertEqual(key["status"], "pass" if present else "skip")
                self.assertEqual(key["details"]["scope"], "current_process_environment")
                self.assertFalse(key["details"]["runtime_credential_checked"])
                self.assertFalse(key["details"]["runtime_health_checked"])
                self.assertIn("this doctor process", key["message"])
                self.assertNotIn(secret, str(result))

    def test_managed_socket_path_does_not_claim_desktop_installation_or_health(self) -> None:
        socket_path = self.home / ".codex/app-server-control/app-server-control.sock"
        socket_path.parent.mkdir(parents=True)
        socket_path.touch()  # A path alone is not a connected, authenticated server.

        with patch.dict(os.environ, self.environment, clear=True):
            result = run_doctor(
                offline=True,
                settings_loader=lambda: self.settings,
                which=self.which,
            )

        checks = {item["name"]: item for item in result["checks"]}
        desktop = checks["agent_codex_desktop"]
        self.assertEqual(desktop["status"], "pass")
        self.assertEqual(desktop["details"]["path"], str(socket_path.resolve()))
        self.assertFalse(desktop["details"]["connection_checked"])
        self.assertFalse(desktop["details"]["authentication_checked"])
        self.assertFalse(desktop["details"]["desktop_application_checked"])

    def test_doctor_fails_without_backend_and_required_allowlist(self) -> None:
        broken = AgentSettings(
            **{
                **self.settings.__dict__,
                "allowed_projects": set(),
            }
        )

        def minimal_which(name: str) -> str | None:
            return "/tools/" + name if name in {"git", "uv"} else None

        with patch.dict(os.environ, self.environment, clear=True):
            result = run_doctor(
                offline=True,
                settings_loader=lambda: broken,
                which=minimal_which,
            )

        self.assertFalse(result["ok"])
        checks = {item["name"]: item for item in result["checks"]}
        self.assertEqual(checks["coding_backend"]["status"], "fail")
        self.assertEqual(checks["project_allowlist"]["status"], "fail")

    def test_git_only_allows_missing_api_token_but_requires_git_credential(self) -> None:
        git_only = AgentSettings(
            **{
                **self.settings.__dict__,
                "api_token": "",
                "git_token": "git-password-or-token",
            }
        )
        with patch.dict(os.environ, self.environment, clear=True):
            result = run_doctor(
                offline=True,
                git_only=True,
                settings_loader=lambda: git_only,
                which=self.which,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["git_only"])
        checks = {item["name"]: item for item in result["checks"]}
        self.assertEqual(checks["gitlab_api_token"]["status"], "skip")
        self.assertEqual(checks["git_credential"]["status"], "pass")

        no_git = AgentSettings(
            **{
                **git_only.__dict__,
                "git_token": "",
            }
        )
        with patch.dict(os.environ, self.environment, clear=True):
            failed = run_doctor(
                offline=True,
                git_only=True,
                settings_loader=lambda: no_git,
                which=self.which,
            )
        self.assertFalse(failed["ok"])
        failed_checks = {item["name"]: item for item in failed["checks"]}
        self.assertEqual(failed_checks["git_credential"]["status"], "fail")

    def test_live_gitlab_failure_is_reported(self) -> None:
        with patch.dict(os.environ, self.environment, clear=True):
            result = run_doctor(
                settings_loader=lambda: self.settings,
                which=self.which,
                api_factory=lambda settings: FakeAPI(settings, fail=True),
            )

        self.assertFalse(result["ok"])
        checks = {item["name"]: item for item in result["checks"]}
        self.assertEqual(checks["gitlab_api_connectivity"]["status"], "fail")
        self.assertIn("network unavailable", checks["gitlab_api_connectivity"]["message"])

    def test_git_only_mode_allows_missing_api_token_but_requires_git_credential(self) -> None:
        git_only = AgentSettings(
            **{
                **self.settings.__dict__,
                "api_token": "",
                "git_token": "git-only-secret",
            }
        )
        with patch.dict(
            os.environ, {**self.environment, "GITLAB_GIT_TOKEN": "git-only-secret"}, clear=True
        ):
            result = run_doctor(
                offline=True,
                git_only=True,
                settings_loader=lambda: git_only,
                which=self.which,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["git_only"])
        checks = {item["name"]: item for item in result["checks"]}
        self.assertEqual(checks["gitlab_api_token"]["status"], "skip")
        self.assertEqual(checks["git_credential"]["status"], "pass")

        no_git = AgentSettings(
            **{
                **git_only.__dict__,
                "git_token": "",
            }
        )
        with patch.dict(os.environ, self.environment, clear=True):
            failed = run_doctor(
                offline=True,
                git_only=True,
                settings_loader=lambda: no_git,
                which=self.which,
            )
        failed_checks = {item["name"]: item for item in failed["checks"]}
        self.assertFalse(failed["ok"])
        self.assertEqual(failed_checks["git_credential"]["status"], "fail")

    def test_doctor_survives_invalid_configuration(self) -> None:
        def bad_loader() -> AgentSettings:
            raise RuntimeError("GITLAB_BASE_URL is required")

        with patch.dict(os.environ, self.environment, clear=True):
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
