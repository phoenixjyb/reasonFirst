from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gitlab_agent.config import AgentSettings
from gitlab_agent.setup_state import SetupState
from gitlab_agent.setup_status import build_setup_status


class SetupStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = root / ".env"
        self.config.write_text(
            "GITLAB_BASE_URL=https://gitlab.example.test\n",
            encoding="utf-8",
        )
        self.settings = AgentSettings(
            config_file=self.config,
            gitlab_base_url="https://gitlab.example.test",
            api_token="api-secret-not-for-output",
            api_verify_ssl=True,
            api_trust_env=False,
            git_token="git-secret-not-for-output",
            git_username="oauth2",
            git_trust_env=False,
            allowed_projects={"team/project"},
            require_write_allowlist=True,
            workspace_root=root / "workspaces",
            branch_prefix="chatgpt/",
            default_base_ref="main",
            allowed_executables={"python"},
            command_timeout_seconds=30,
            max_output_bytes=120000,
            max_file_bytes=1000000,
            git_author_name=None,
            git_author_email=None,
            default_backend="codex-cli",
        )
        self.desktop_socket = root / "desktop.sock"

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def which(name: str) -> str | None:
        installed = {
            "git": "/tools/git",
            "uv": "/tools/uv",
            "codex": "/tools/codex",
            "tunnel-client": "/tools/tunnel-client",
        }
        return installed.get(name)

    def test_status_is_detect_only_and_never_claims_full_ready(self) -> None:
        original = dict(os.environ)

        def loader() -> AgentSettings:
            os.environ["GITLAB_TOKEN"] = "temporary-loader-side-effect"
            return self.settings

        try:
            result = build_setup_status(
                state_path=Path(self.temp.name) / "missing.yaml",
                which=self.which,
                settings_loader=loader,
                setup_state_loader=lambda _: None,
                system_name="Darwin",
                machine="arm64",
                desktop_socket=self.desktop_socket,
            )
            self.assertEqual(os.environ, original)
        finally:
            os.environ.clear()
            os.environ.update(original)

        self.assertTrue(result["ok"])
        self.assertFalse(result["mutating"])
        self.assertEqual(result["system"]["os"], "macos")
        self.assertEqual(result["mode"], "standard")
        self.assertTrue(result["readiness"]["machine_prerequisites"])
        self.assertTrue(result["readiness"]["control_plane_prerequisites"])
        self.assertTrue(result["readiness"]["tunnel_client_available"])
        self.assertFalse(result["readiness"]["ready"])
        self.assertEqual(result["readiness"]["chatgpt_connection"], "not_verified")
        self.assertNotIn("api-secret-not-for-output", repr(result))
        self.assertNotIn("git-secret-not-for-output", repr(result))

    def test_cli_only_mode_does_not_require_tunnel_client(self) -> None:
        def no_tunnel(name: str) -> str | None:
            if name in {"git", "uv", "copilot"}:
                return f"/tools/{name}"
            return None

        result = build_setup_status(
            which=no_tunnel,
            settings_loader=lambda: self.settings,
            setup_state_loader=lambda _: SetupState(
                mode="cli-only",
                selected_worker="copilot-cli",
            ),
            system_name="Windows",
            machine="AMD64",
            desktop_socket=self.desktop_socket,
        )
        self.assertEqual(result["system"]["os"], "windows")
        action_ids = [item["id"] for item in result["plan"]["actions"]]
        self.assertNotIn("install-tunnel-client", action_ids)
        self.assertNotIn("verify-chatgpt", action_ids)

    def test_missing_config_worker_and_tunnel_become_actionable_plan(self) -> None:
        def only_core(name: str) -> str | None:
            return f"/tools/{name}" if name in {"git", "uv"} else None

        def bad_loader() -> AgentSettings:
            raise RuntimeError("GITLAB_BASE_URL is required")

        result = build_setup_status(
            which=only_core,
            settings_loader=bad_loader,
            setup_state_loader=lambda _: None,
            system_name="Linux",
            machine="x86_64",
            desktop_socket=self.desktop_socket,
        )

        self.assertFalse(result["config"]["valid"])
        self.assertFalse(result["readiness"]["control_plane_prerequisites"])
        action_ids = [item["id"] for item in result["plan"]["actions"]]
        self.assertIn("configure-gitlab", action_ids)
        self.assertIn("configure-worker", action_ids)
        self.assertIn("install-tunnel-client", action_ids)
        self.assertIn("verify-chatgpt", action_ids)

    def test_existing_desktop_socket_counts_as_worker(self) -> None:
        self.desktop_socket.write_text("", encoding="utf-8")

        def core_only(name: str) -> str | None:
            return f"/tools/{name}" if name in {"git", "uv"} else None

        result = build_setup_status(
            which=core_only,
            settings_loader=lambda: self.settings,
            setup_state_loader=lambda _: SetupState(mode="cli-only"),
            system_name="Linux",
            machine="aarch64",
            desktop_socket=self.desktop_socket,
        )
        desktop = next(
            item for item in result["workers"] if item["name"] == "codex-desktop"
        )
        self.assertTrue(desktop["available"])
        self.assertTrue(result["readiness"]["control_plane_prerequisites"])


if __name__ == "__main__":
    unittest.main()
