from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from gitlab_agent.config import AgentSettings
from gitlab_agent.setup_state import SetupState, save_setup_state
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

    def _status_from_config(
        self, mode: str, *, git_only: bool = False, coding_worker: bool = False,
    ) -> dict[str, object]:
        token = "fixture-" + uuid.uuid4().hex
        credential_key = "GITLAB_GIT_TOKEN" if git_only else "GITLAB_TOKEN"
        self.config.write_text(
            "GITLAB_BASE_URL=https://gitlab.example.test\n"
            f"{credential_key}={token}\n"
            "GITLAB_ALLOWED_PROJECTS=team/project\n",
            encoding="utf-8",
        )
        state_file = Path(self.temp.name) / "setup.yaml"
        save_setup_state(
            SetupState(
                mode=mode, config_file=str(self.config),
                selected_worker="codex-cli" if coding_worker else None,
                completed_phases=("system", "gitlab"),
            ),
            state_file,
        )
        config_before = self.config.read_bytes()
        state_before = state_file.read_bytes()
        installed = {"git", "uv", "tunnel-client"}
        if coding_worker:
            installed.add("codex")

        with patch.dict(
            os.environ,
            {
                "HOME": self.temp.name,
                "USERPROFILE": self.temp.name,
                "GITLAB_AGENT_ENV_FILE": str(self.config),
            },
            clear=True,
        ):
            result = build_setup_status(
                state_path=state_file,
                which=lambda name: f"/tools/{name}" if name in installed else None,
                system_name="Linux", machine="x86_64",
                desktop_socket=self.desktop_socket,
            )
        self.assertEqual(self.config.read_bytes(), config_before)
        self.assertEqual(state_file.read_bytes(), state_before)
        self.assertNotIn(token, repr(result))
        return result

    def test_standard_read_setup_does_not_require_a_coding_worker(self) -> None:
        result = self._status_from_config("standard")
        self.assertTrue(result["readiness"]["control_plane_prerequisites"])
        self.assertTrue(result["readiness"]["read_only_prerequisites"])
        self.assertFalse(result["readiness"]["coding_worker_available"])
        self.assertFalse(result["readiness"]["ready"])
        self.assertEqual(result["readiness"]["chatgpt_connection"], "not_verified")
        actions = {item["id"]: item for item in result["plan"]["actions"]}
        self.assertIn("configure-tunnel", actions)
        self.assertIn("configure-worker", actions)
        self.assertIn("optional", actions["configure-worker"]["message"].lower())

    def test_execution_modes_still_require_a_coding_worker(self) -> None:
        for mode in ("cli-only", "full-chat"):
            with self.subTest(mode=mode):
                result = self._status_from_config(mode)
                self.assertTrue(result["readiness"]["read_only_prerequisites"])
                self.assertFalse(result["readiness"]["coding_worker_available"])
                self.assertFalse(result["readiness"]["control_plane_prerequisites"])
                self.assertFalse(result["readiness"]["ready"])
                self.assertEqual(
                    result["readiness"]["chatgpt_connection"], "not_verified",
                )
                action = next(
                    item for item in result["plan"]["actions"]
                    if item["id"] == "configure-worker"
                )
                self.assertNotIn("optional", action["message"].lower())

    def test_git_only_credential_does_not_establish_read_api_prerequisites(self) -> None:
        for mode in ("standard", "cli-only", "full-chat"):
            with self.subTest(mode=mode):
                result = self._status_from_config(mode, git_only=True, coding_worker=True)
                self.assertTrue(result["config"]["valid"])
                self.assertTrue(result["config"]["has_git_credential"])
                self.assertFalse(result["config"]["has_api_token"])
                self.assertFalse(result["readiness"]["read_only_prerequisites"])
                self.assertTrue(result["readiness"]["coding_worker_available"])
                self.assertEqual(
                    result["readiness"]["control_plane_prerequisites"],
                    mode == "cli-only",
                )
                self.assertFalse(result["readiness"]["ready"])
                action_ids = [item["id"] for item in result["plan"]["actions"]]
                if mode == "cli-only":
                    self.assertNotIn("configure-read-api-token", action_ids)
                else:
                    self.assertIn("configure-read-api-token", action_ids)

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
        self.assertNotIn("verify-chatgpt", action_ids)

    def test_recorded_tunnel_is_reported_without_live_status_guessing(self) -> None:
        state = SetupState(
            mode="standard",
            selected_worker="codex-cli",
            tunnel_id="tunnel_" + "a" * 32,
            tunnel_runtime="reasonfirst-gitlab",
            tunnel_client_path="/tools/tunnel-client",
            completed_phases=("system", "gitlab", "worker", "tunnel"),
        )
        result = build_setup_status(
            which=self.which,
            settings_loader=lambda: self.settings,
            setup_state_loader=lambda _: state,
            system_name="Linux",
            machine="x86_64",
            desktop_socket=self.desktop_socket,
        )
        self.assertTrue(result["readiness"]["tunnel_recorded"])
        self.assertFalse(result["readiness"]["ready"])
        action_ids = [item["id"] for item in result["plan"]["actions"]]
        self.assertIn("verify-tunnel-live", action_ids)
        self.assertIn("verify-chatgpt", action_ids)
        self.assertNotIn("configure-tunnel", action_ids)

    def test_full_chat_recorded_bridge_requires_live_and_chatgpt_verification(self) -> None:
        state = SetupState(
            mode="full-chat",
            selected_worker="codex-cli",
            tunnel_id="tunnel_" + "a" * 32,
            tunnel_runtime="reasonfirst-gitlab",
            tunnel_client_path="/tools/tunnel-client",
            bridge_tunnel_id="tunnel_" + "b" * 32,
            bridge_runtime="reasonfirst-bridge",
            completed_phases=("system", "gitlab", "worker", "tunnel", "bridge"),
        )
        result = build_setup_status(
            which=self.which,
            settings_loader=lambda: self.settings,
            setup_state_loader=lambda _: state,
            system_name="Linux",
            machine="x86_64",
            desktop_socket=self.desktop_socket,
        )
        self.assertTrue(result["readiness"]["tunnel_recorded"])
        self.assertTrue(result["readiness"]["bridge_recorded"])
        self.assertFalse(result["readiness"]["ready"])
        self.assertEqual(
            result["readiness"]["full_chat_connection"],
            "not_verified",
        )
        action_ids = [item["id"] for item in result["plan"]["actions"]]
        self.assertIn("verify-bridge-live", action_ids)
        self.assertIn("verify-full-chat", action_ids)
        self.assertNotIn("configure-bridge", action_ids)

    def test_full_chat_copilot_only_reports_codex_bridge_prerequisite(self) -> None:
        def copilot_only(name: str) -> str | None:
            if name in {"git", "uv", "tunnel-client", "copilot"}:
                return f"/tools/{name}"
            return None

        state = SetupState(
            mode="full-chat",
            selected_worker="copilot-cli",
            tunnel_id="tunnel_" + "a" * 32,
            tunnel_runtime="reasonfirst-gitlab",
            completed_phases=("system", "gitlab", "worker", "tunnel"),
        )
        result = build_setup_status(
            which=copilot_only,
            settings_loader=lambda: self.settings,
            setup_state_loader=lambda _: state,
            system_name="Linux",
            machine="x86_64",
            desktop_socket=self.desktop_socket,
        )
        self.assertFalse(result["readiness"]["bridge_worker_available"])
        action_ids = [item["id"] for item in result["plan"]["actions"]]
        self.assertIn("configure-codex-bridge-worker", action_ids)

    def test_full_chat_without_bridge_points_to_bridge_configuration(self) -> None:
        state = SetupState(
            mode="full-chat",
            selected_worker="codex-cli",
            tunnel_id="tunnel_" + "a" * 32,
            tunnel_runtime="reasonfirst-gitlab",
            tunnel_client_path="/tools/tunnel-client",
            completed_phases=("system", "gitlab", "worker", "tunnel"),
        )
        result = build_setup_status(
            which=self.which,
            settings_loader=lambda: self.settings,
            setup_state_loader=lambda _: state,
            system_name="Windows",
            machine="AMD64",
            desktop_socket=self.desktop_socket,
        )
        action_ids = [item["id"] for item in result["plan"]["actions"]]
        self.assertIn("configure-bridge", action_ids)
        self.assertFalse(result["readiness"]["bridge_recorded"])

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
