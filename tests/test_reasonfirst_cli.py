from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gitlab_agent import __version__, reasonfirst_cli
from gitlab_agent.config import AgentSettings
from gitlab_agent.setup_state import SetupState


STATUS = {
    "ok": True,
    "command": "setup-status",
    "mutating": False,
    "reasonfirst_version": __version__,
    "system": {
        "os": "linux",
        "platform_system": "Linux",
        "architecture": "x86_64",
        "python": "3.12.0",
        "python_executable": "/usr/bin/python3",
    },
    "mode": "standard",
    "probes": [],
    "config": {
        "path": "/home/test/.config/gitlab-agent/.env",
        "exists": True,
        "valid": True,
    },
    "workers": [
        {
            "name": "codex-cli",
            "available": True,
            "path": "/usr/bin/codex",
            "authentication_verified": False,
        }
    ],
    "setup_state": {
        "path": "/home/test/.config/reasonfirst/setup.yaml",
        "exists": False,
        "valid": True,
        "state": None,
    },
    "readiness": {
        "machine_prerequisites": True,
        "control_plane_prerequisites": True,
        "tunnel_client_available": True,
        "chatgpt_connection": "not_verified",
        "ready": False,
        "ready_reason": "detect-only",
    },
    "plan": {
        "actions": [
            {
                "id": "verify-chatgpt",
                "message": "Complete the ChatGPT-side authorization step.",
            }
        ],
        "action_count": 1,
    },
}


def fake_settings(config: Path) -> AgentSettings:
    return AgentSettings(
        config_file=config,
        gitlab_base_url="https://gitlab.example.test",
        api_token="existing-api-token",
        api_verify_ssl=True,
        api_trust_env=False,
        git_token="existing-api-token",
        git_username="oauth2",
        git_trust_env=False,
        allowed_projects={"team/existing"},
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


class ReasonFirstCLITests(unittest.TestCase):
    def test_setup_status_json(self) -> None:
        out = io.StringIO()
        with (
            patch.object(reasonfirst_cli, "build_setup_status", return_value=STATUS),
            contextlib.redirect_stdout(out),
        ):
            code = reasonfirst_cli.main(["setup", "--status", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertFalse(payload["mutating"])
        self.assertFalse(payload["readiness"]["ready"])
        self.assertEqual(payload["system"]["os"], "linux")

    def test_setup_status_human_output_states_no_changes(self) -> None:
        out = io.StringIO()
        with (
            patch.object(reasonfirst_cli, "build_setup_status", return_value=STATUS),
            contextlib.redirect_stdout(out),
        ):
            code = reasonfirst_cli.main(["setup", "--status"])
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("ReasonFirst setup status", text)
        self.assertIn("ChatGPT connection: not verified", text)
        self.assertIn("No changes were made.", text)

    def test_guided_setup_requires_tty(self) -> None:
        err = io.StringIO()
        with (
            patch.object(reasonfirst_cli.sys.stdin, "isatty", return_value=False),
            contextlib.redirect_stderr(err),
        ):
            code = reasonfirst_cli.main(["setup"])
        self.assertEqual(code, 1)
        self.assertIn("interactive TTY", err.getvalue())

    def test_guided_setup_verifies_before_writing_and_never_outputs_secret(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            args = argparse.Namespace(
                state_file=root / "setup.yaml",
                mode="standard",
                gitlab_url="https://gitlab.example.test",
                project="team/project",
                ref="main",
                worker="codex-cli",
                json=True,
                skip_chatgpt=True,
            )
            candidate = fake_settings(root / ".env")
            preflight = {
                "ok": True,
                "ref": "main",
                "resolved_commit_sha": "abc123",
                "writes_performed": False,
            }

            with (
                patch.object(reasonfirst_cli.sys.stdin, "isatty", return_value=True),
                patch.object(
                    reasonfirst_cli,
                    "assert_no_effective_env_override",
                ),
                patch.object(
                    reasonfirst_cli,
                    "_load_settings_clean",
                    side_effect=RuntimeError("no config"),
                ),
                patch.object(
                    reasonfirst_cli,
                    "resolve_env_file",
                    return_value=root / "missing.env",
                ),
                patch.object(reasonfirst_cli, "load_setup_state", return_value=None),
                patch.object(reasonfirst_cli, "_choose_worker", return_value="codex-cli"),
                patch.object(reasonfirst_cli, "candidate_settings", return_value=candidate),
                patch.object(
                    reasonfirst_cli,
                    "preflight_project",
                    return_value=preflight,
                ) as probe,
                patch.object(
                    reasonfirst_cli,
                    "apply_env_updates",
                    return_value={
                        "changed": True,
                        "config_file": str(root / ".env"),
                        "backup_file": None,
                        "updated_keys": [],
                    },
                ) as apply,
                patch.object(
                    reasonfirst_cli,
                    "_persist_progress",
                    return_value=root / "setup.yaml",
                ),
            ):
                code, payload = reasonfirst_cli._run_guided_setup(
                    args,
                    input_fn=lambda _: "y",
                    secret_fn=lambda _: "super-secret-token",
                )

            self.assertEqual(code, 0)
            probe.assert_called_once()
            apply.assert_called_once()
            self.assertEqual(payload["resolved_commit_sha"], "abc123")
            self.assertTrue(payload["local_control_configured"])
            self.assertFalse(payload["ready"])
            self.assertNotIn("super-secret-token", repr(payload))

    def test_guided_setup_failed_preflight_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            args = argparse.Namespace(
                state_file=root / "setup.yaml",
                mode="standard",
                gitlab_url="https://gitlab.example.test",
                project="team/project",
                ref="main",
                worker="codex-cli",
                json=True,
                skip_chatgpt=True,
            )
            candidate = fake_settings(root / ".env")
            with (
                patch.object(reasonfirst_cli.sys.stdin, "isatty", return_value=True),
                patch.object(reasonfirst_cli, "assert_no_effective_env_override"),
                patch.object(
                    reasonfirst_cli,
                    "_load_settings_clean",
                    side_effect=RuntimeError("no config"),
                ),
                patch.object(
                    reasonfirst_cli,
                    "resolve_env_file",
                    return_value=root / "missing.env",
                ),
                patch.object(reasonfirst_cli, "load_setup_state", return_value=None),
                patch.object(reasonfirst_cli, "_choose_worker", return_value="codex-cli"),
                patch.object(reasonfirst_cli, "candidate_settings", return_value=candidate),
                patch.object(
                    reasonfirst_cli,
                    "preflight_project",
                    return_value={
                        "ok": False,
                        "error": "project_missing_or_inaccessible",
                        "writes_performed": False,
                    },
                ),
                patch.object(reasonfirst_cli, "apply_env_updates") as apply,
            ):
                code, payload = reasonfirst_cli._run_guided_setup(
                    args,
                    input_fn=lambda _: "y",
                    secret_fn=lambda _: "secret",
                )
            self.assertEqual(code, 1)
            self.assertEqual(payload["stage"], "project-preflight")
            apply.assert_not_called()

    def test_guided_tunnel_continuation_persists_ready_runtime_but_not_chatgpt_ready(self) -> None:
        tunnel_id = "tunnel_" + "a" * 32
        answers = iter(["y", tunnel_id, "n"])
        runtime = {
            "ok": True,
            "ready": True,
            "alias": "reasonfirst-gitlab",
            "tunnel_id": tunnel_id,
            "ui_url": "http://127.0.0.1:9000/ui",
        }
        with tempfile.TemporaryDirectory() as td:
            state_file = Path(td) / "setup.yaml"
            state = SetupState(
                mode="standard",
                selected_worker="codex-cli",
                completed_phases=("system", "gitlab", "worker"),
            )
            with (
                patch.object(reasonfirst_cli, "load_setup_state", return_value=state),
                patch.object(
                    reasonfirst_cli,
                    "resolve_tunnel_client",
                    return_value="/tools/tunnel-client",
                ),
                patch.object(
                    reasonfirst_cli,
                    "resolve_read_mcp",
                    return_value="/tools/reasonfirst-gitlab-mcp",
                ),
                patch.object(
                    reasonfirst_cli,
                    "open_handoff_pages",
                    return_value={"platform_opened": True, "chatgpt_opened": False},
                ),
                patch.object(reasonfirst_cli, "connect_runtime", return_value=runtime),
                patch.object(reasonfirst_cli, "persist_tunnel_state") as persist,
                patch.object(
                    reasonfirst_cli,
                    "build_chatgpt_handoff",
                    return_value={
                        "chatgpt_ready": False,
                        "acceptance_prompt": "probe now",
                    },
                ),
                patch.dict(reasonfirst_cli.os.environ, {}, clear=True),
            ):
                result = reasonfirst_cli._guided_tunnel_after_local(
                    state_path=state_file,
                    project="team/project",
                    ref="main",
                    input_fn=lambda _: next(answers),
                    secret_fn=lambda _: "runtime-secret",
                )
        self.assertTrue(result["local_tunnel_ready"])
        self.assertFalse(result["chatgpt_ready"])
        self.assertEqual(result["next"], "probe now")
        self.assertNotIn("runtime-secret", repr(result))
        persist.assert_called_once()

    def test_project_add_yes_json_emits_one_final_json_object(self) -> None:
        plan = {
            "ok": True,
            "project": "team/two",
            "resolved_commit_sha": "abc123",
            "already_allowed": False,
            "config_file": "/tmp/config",
            "before_projects": ["team/one"],
            "after_projects": ["team/one", "team/two"],
        }
        result = {
            **plan,
            "writes_performed": True,
            "reload_required": True,
            "config_change": {"changed": True, "config_file": "/tmp/config"},
        }
        out = io.StringIO()
        with (
            patch.object(reasonfirst_cli, "_load_settings_clean", return_value=object()),
            patch.object(reasonfirst_cli, "plan_project_add", return_value=plan),
            patch.object(reasonfirst_cli, "apply_project_add", return_value=result),
            contextlib.redirect_stdout(out),
        ):
            code = reasonfirst_cli.main(
                ["project", "add", "team/two", "--ref", "main", "--yes", "--json"]
            )
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["writes_performed"])
        self.assertEqual(payload["project"], "team/two")

    def test_project_add_json_without_yes_is_plan_only(self) -> None:
        plan = {
            "ok": True,
            "project": "team/two",
            "resolved_commit_sha": "abc123",
            "already_allowed": False,
            "config_file": "/tmp/config",
            "before_projects": ["team/one"],
            "after_projects": ["team/one", "team/two"],
            "writes_performed": False,
        }
        out = io.StringIO()
        with (
            patch.object(reasonfirst_cli, "_load_settings_clean", return_value=object()),
            patch.object(reasonfirst_cli, "plan_project_add", return_value=plan),
            patch.object(reasonfirst_cli, "apply_project_add") as apply,
            contextlib.redirect_stdout(out),
        ):
            code = reasonfirst_cli.main(
                ["project", "add", "team/two", "--ref", "main", "--json"]
            )
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertFalse(payload["writes_performed"])
        apply.assert_not_called()

    def test_project_add_noninteractive_requires_explicit_yes(self) -> None:
        plan = {
            "ok": True,
            "project": "team/two",
            "resolved_commit_sha": "abc123",
            "already_allowed": False,
            "config_file": "/tmp/config",
            "before_projects": [],
            "after_projects": ["team/two"],
        }
        err = io.StringIO()
        with (
            patch.object(reasonfirst_cli, "_load_settings_clean", return_value=object()),
            patch.object(reasonfirst_cli, "plan_project_add", return_value=plan),
            patch.object(reasonfirst_cli.sys.stdin, "isatty", return_value=False),
            contextlib.redirect_stderr(err),
        ):
            code = reasonfirst_cli.main(["project", "add", "team/two"])
        self.assertEqual(code, 1)
        self.assertIn("--yes", err.getvalue())

    def test_worker_use_refuses_ambiguous_invalid_legacy_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            legacy = Path(td) / "repo" / ".env"
            legacy.parent.mkdir()
            legacy.write_text("malformed but existing", encoding="utf-8")
            err = io.StringIO()
            with (
                patch.object(
                    reasonfirst_cli,
                    "_load_settings_clean",
                    side_effect=RuntimeError("invalid config"),
                ),
                patch.object(
                    reasonfirst_cli,
                    "resolve_env_file",
                    return_value=legacy,
                ),
                patch.object(
                    reasonfirst_cli,
                    "ensure_persistent_config_target",
                    side_effect=RuntimeError("partial higher-precedence config"),
                ),
                patch.object(reasonfirst_cli, "plan_worker_use") as plan,
                contextlib.redirect_stderr(err),
            ):
                code = reasonfirst_cli.main(["worker", "use", "auto"])
            self.assertEqual(code, 1)
            self.assertIn("partial higher-precedence", err.getvalue())
            plan.assert_not_called()

    def test_worker_use_updates_setup_progress(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state_file = Path(td) / "setup.yaml"
            plan = {
                "ok": True,
                "backend": "codex-cli",
                "available": True,
                "message": "ok",
            }
            result = {**plan, "writes_performed": True}
            saved: list[SetupState] = []

            with (
                patch.object(
                    reasonfirst_cli,
                    "_load_settings_clean",
                    side_effect=RuntimeError("no config"),
                ),
                patch.object(reasonfirst_cli, "plan_worker_use", return_value=plan),
                patch.object(reasonfirst_cli, "apply_worker_use", return_value=result),
                patch.object(reasonfirst_cli, "load_setup_state", return_value=None),
                patch.object(
                    reasonfirst_cli,
                    "save_setup_state",
                    side_effect=lambda state, _: saved.append(state) or state_file,
                ),
            ):
                code = reasonfirst_cli.main(
                    [
                        "worker",
                        "use",
                        "codex-cli",
                        "--state-file",
                        str(state_file),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(saved[0].selected_worker, "codex-cli")
            self.assertIn("worker", saved[0].completed_phases)

    def test_tunnel_connect_persists_only_nonsecret_runtime_metadata(self) -> None:
        tunnel_id = "tunnel_" + "a" * 32
        with tempfile.TemporaryDirectory() as td:
            state_file = Path(td) / "setup.yaml"
            state = SetupState()
            result = {
                "ok": True,
                "ready": True,
                "alias": "reasonfirst-gitlab",
                "tunnel_id": tunnel_id,
                "local_tunnel_ready": True,
                "chatgpt_ready": False,
            }
            with (
                patch.object(reasonfirst_cli, "load_setup_state", return_value=state),
                patch.object(
                    reasonfirst_cli,
                    "_resolve_or_install_tunnel_client",
                    return_value="/tools/tunnel-client",
                ),
                patch.object(
                    reasonfirst_cli,
                    "resolve_read_mcp",
                    return_value="/tools/reasonfirst-gitlab-mcp",
                ),
                patch.dict(
                    reasonfirst_cli.os.environ,
                    {reasonfirst_cli.RUNTIME_KEY_ENV: "runtime-secret"},
                    clear=False,
                ),
                patch.object(
                    reasonfirst_cli,
                    "connect_runtime",
                    return_value=result,
                ) as connect,
                patch.object(reasonfirst_cli, "persist_tunnel_state") as persist,
            ):
                args = argparse.Namespace(
                    state_file=state_file,
                    install=False,
                    tunnel_id=tunnel_id,
                    alias="reasonfirst-gitlab",
                    open_platform=False,
                    json=True,
                )
                payload = reasonfirst_cli._connect_tunnel_from_args(args)

            self.assertTrue(payload["local_tunnel_ready"])
            self.assertFalse(payload["runtime_key_stored"])
            self.assertNotIn("runtime-secret", repr(payload))
            connect.assert_called_once()
            persist.assert_called_once()

    def test_tunnel_connect_noninteractive_never_accepts_runtime_key_on_argv(self) -> None:
        parser = reasonfirst_cli._build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "tunnel",
                    "connect",
                    "--tunnel-id",
                    "tunnel_" + "a" * 32,
                    "--runtime-key",
                    "secret",
                ]
            )

    def test_tunnel_status_uses_recorded_alias_and_binary(self) -> None:
        state = SetupState(
            tunnel_id="tunnel_" + "a" * 32,
            tunnel_runtime="reasonfirst-gitlab",
            tunnel_client_path="/tools/tunnel-client",
            completed_phases=("tunnel",),
        )
        out = io.StringIO()
        with (
            patch.object(reasonfirst_cli, "load_setup_state", return_value=state),
            patch.object(
                reasonfirst_cli,
                "resolve_tunnel_client",
                return_value="/tools/tunnel-client",
            ),
            patch.object(
                reasonfirst_cli,
                "runtime_status",
                return_value={
                    "ok": True,
                    "ready": True,
                    "alias": "reasonfirst-gitlab",
                    "process_running": True,
                    "healthy": True,
                    "native_ready": True,
                },
            ) as status,
            contextlib.redirect_stdout(out),
        ):
            code = reasonfirst_cli.main(
                ["tunnel", "status", "--json"]
            )
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["ready"])
        status.assert_called_once_with(
            alias="reasonfirst-gitlab",
            tunnel_client="/tools/tunnel-client",
        )

    def test_chatgpt_handoff_never_claims_ready(self) -> None:
        state = SetupState(
            tunnel_id="tunnel_" + "a" * 32,
            tunnel_runtime="reasonfirst-gitlab",
            completed_phases=("tunnel",),
        )
        settings = fake_settings(Path("/tmp/config"))
        settings = AgentSettings(**{**settings.__dict__, "allowed_projects": {"team/project"}})
        out = io.StringIO()
        with (
            patch.object(reasonfirst_cli, "load_setup_state", return_value=state),
            patch.object(reasonfirst_cli, "_load_settings_clean", return_value=settings),
            contextlib.redirect_stdout(out),
        ):
            code = reasonfirst_cli.main(
                [
                    "chatgpt",
                    "handoff",
                    "--project",
                    "team/project",
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertFalse(payload["chatgpt_ready"])
        self.assertIn("gitlab_whoami", payload["acceptance_prompt"])

    def test_bridge_inventory_command_is_nonmutating(self) -> None:
        payload = {
            "ok": True,
            "tool_count": 22,
            "remote_push_exposed": False,
        }
        out = io.StringIO()
        with (
            patch.object(reasonfirst_cli, "bridge_tool_inventory", return_value=payload),
            contextlib.redirect_stdout(out),
        ):
            code = reasonfirst_cli.main(["bridge", "inventory", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue()), payload)

    def test_bridge_connect_rejects_reusing_read_tunnel(self) -> None:
        read_tunnel = "tunnel_" + "a" * 32
        state = SetupState(
            mode="full-chat",
            tunnel_id=read_tunnel,
            tunnel_runtime="reasonfirst-gitlab",
            tunnel_client_path="/tools/tunnel-client",
            completed_phases=("system", "gitlab", "worker", "tunnel"),
        )
        args = argparse.Namespace(
            state_file=Path("/tmp/setup.yaml"),
            eligible_workspace=True,
            install=False,
            tunnel_id=read_tunnel,
            alias="reasonfirst-bridge",
            open_platform=False,
            json=True,
        )
        with (
            patch.object(reasonfirst_cli.sys.stdin, "isatty", return_value=False),
            patch.object(reasonfirst_cli, "load_setup_state", return_value=state),
            patch.object(
                reasonfirst_cli,
                "_resolve_or_install_tunnel_client",
                return_value="/tools/tunnel-client",
            ),
            patch.object(
                reasonfirst_cli,
                "resolve_bridge_mcp",
                return_value="/tools/reasonfirst-bridge-mcp",
            ),
            patch.dict(
                reasonfirst_cli.os.environ,
                {reasonfirst_cli.RUNTIME_KEY_ENV: "runtime-secret"},
                clear=False,
            ),
            patch.object(reasonfirst_cli, "connect_bridge_runtime") as connect,
        ):
            with self.assertRaisesRegex(RuntimeError, "separate tunnel ID"):
                reasonfirst_cli._connect_bridge_from_args(args)
        connect.assert_not_called()

    def test_bridge_connect_requires_explicit_noninteractive_eligibility_ack(self) -> None:
        state = SetupState(
            mode="full-chat",
            tunnel_id="tunnel_" + "a" * 32,
            tunnel_runtime="reasonfirst-gitlab",
            completed_phases=("tunnel",),
        )
        args = argparse.Namespace(
            state_file=Path("/tmp/setup.yaml"),
            eligible_workspace=False,
            install=False,
            tunnel_id="tunnel_" + "b" * 32,
            alias="reasonfirst-bridge",
            open_platform=False,
            json=True,
        )
        with (
            patch.object(reasonfirst_cli.sys.stdin, "isatty", return_value=False),
            patch.object(reasonfirst_cli, "load_setup_state", return_value=state),
        ):
            with self.assertRaisesRegex(Exception, "explicit acknowledgement"):
                reasonfirst_cli._connect_bridge_from_args(args)

    def test_bridge_handoff_never_claims_full_chat_ready(self) -> None:
        state = SetupState(
            mode="full-chat",
            tunnel_id="tunnel_" + "a" * 32,
            tunnel_runtime="reasonfirst-gitlab",
            bridge_tunnel_id="tunnel_" + "b" * 32,
            bridge_runtime="reasonfirst-bridge",
            completed_phases=("tunnel", "bridge"),
        )
        out = io.StringIO()
        with (
            patch.object(reasonfirst_cli, "load_setup_state", return_value=state),
            contextlib.redirect_stdout(out),
        ):
            code = reasonfirst_cli.main(["bridge", "handoff", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertFalse(payload["full_chat_ready"])
        self.assertIn("reasonfirst_dispatch", payload["acceptance_prompt"])

    def test_guided_setup_reuses_healthy_recorded_read_tunnel(self) -> None:
        state = SetupState(
            mode="standard",
            tunnel_id="tunnel_" + "a" * 32,
            tunnel_runtime="reasonfirst-gitlab",
            tunnel_client_path="/tools/tunnel-client",
            completed_phases=("system", "gitlab", "worker", "tunnel"),
        )
        with (
            patch.object(reasonfirst_cli, "load_setup_state", return_value=state),
            patch.object(
                reasonfirst_cli,
                "resolve_tunnel_client",
                return_value="/tools/tunnel-client",
            ),
            patch.object(
                reasonfirst_cli,
                "runtime_status",
                return_value={
                    "ok": True,
                    "ready": True,
                    "alias": "reasonfirst-gitlab",
                },
            ),
            patch.object(
                reasonfirst_cli,
                "build_chatgpt_handoff",
                return_value={
                    "chatgpt_ready": False,
                    "acceptance_prompt": "read acceptance",
                },
            ),
        ):
            result = reasonfirst_cli._guided_tunnel_after_local(
                state_path=Path("/tmp/setup.yaml"),
                project="team/project",
                ref="main",
                input_fn=lambda _: (_ for _ in ()).throw(
                    AssertionError("healthy resume must not prompt")
                ),
                secret_fn=lambda _: (_ for _ in ()).throw(
                    AssertionError("healthy resume must not request a secret")
                ),
            )
        self.assertTrue(result["reused"])
        self.assertTrue(result["local_tunnel_ready"])
        self.assertFalse(result["chatgpt_ready"])
        self.assertEqual(result["next"], "read acceptance")

    def test_guided_setup_reuses_healthy_recorded_bridge(self) -> None:
        state = SetupState(
            mode="full-chat",
            tunnel_id="tunnel_" + "a" * 32,
            tunnel_runtime="reasonfirst-gitlab",
            bridge_tunnel_id="tunnel_" + "b" * 32,
            bridge_runtime="reasonfirst-bridge",
            tunnel_client_path="/tools/tunnel-client",
            completed_phases=("system", "gitlab", "worker", "tunnel", "bridge"),
        )
        with (
            patch.object(reasonfirst_cli, "load_setup_state", return_value=state),
            patch.object(
                reasonfirst_cli,
                "resolve_tunnel_client",
                return_value="/tools/tunnel-client",
            ),
            patch.object(
                reasonfirst_cli,
                "bridge_runtime_status",
                return_value={
                    "ok": True,
                    "ready": True,
                    "alias": "reasonfirst-bridge",
                },
            ),
            patch.object(
                reasonfirst_cli,
                "build_bridge_handoff",
                return_value={
                    "full_chat_ready": False,
                    "acceptance_prompt": "bridge acceptance",
                },
            ),
        ):
            result = reasonfirst_cli._guided_bridge_after_read(
                state_path=Path("/tmp/setup.yaml"),
                input_fn=lambda _: (_ for _ in ()).throw(
                    AssertionError("healthy Bridge resume must not prompt")
                ),
                secret_fn=lambda _: (_ for _ in ()).throw(
                    AssertionError("healthy Bridge resume must not request a secret")
                ),
            )
        self.assertTrue(result["reused"])
        self.assertTrue(result["local_bridge_ready"])
        self.assertFalse(result["full_chat_ready"])
        self.assertEqual(result["next"], "bridge acceptance")

    def test_setup_repair_healthy_recorded_tunnel_is_noop(self) -> None:
        state = SetupState(
            mode="standard",
            config_file="/tmp/config",
            tunnel_id="tunnel_" + "a" * 32,
            tunnel_runtime="reasonfirst-gitlab",
            tunnel_client_path="/tools/tunnel-client",
            completed_phases=("system", "gitlab", "worker", "tunnel"),
        )
        args = argparse.Namespace(
            state_file=Path("/tmp/setup.yaml"),
            install=False,
            eligible_workspace=False,
        )
        with (
            patch.object(reasonfirst_cli, "load_setup_state", return_value=state),
            patch.object(
                reasonfirst_cli,
                "resolve_tunnel_client",
                return_value="/tools/tunnel-client",
            ),
            patch.object(
                reasonfirst_cli,
                "runtime_status",
                return_value={
                    "ok": True,
                    "ready": True,
                    "alias": "reasonfirst-gitlab",
                },
            ),
            patch.object(reasonfirst_cli, "connect_runtime") as connect,
        ):
            code, payload = reasonfirst_cli._run_setup_repair(args)
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["writes_performed"])
        self.assertFalse(payload["read_tunnel"]["repaired"])
        self.assertFalse(payload["ready"])
        connect.assert_not_called()

    def test_setup_repair_noninteractive_missing_runtime_key_is_action_required(self) -> None:
        state = SetupState(
            mode="standard",
            config_file="/tmp/config",
            tunnel_id="tunnel_" + "a" * 32,
            tunnel_runtime="reasonfirst-gitlab",
            tunnel_client_path="/tools/tunnel-client",
            completed_phases=("system", "gitlab", "worker", "tunnel"),
        )
        args = argparse.Namespace(
            state_file=Path("/tmp/setup.yaml"),
            install=False,
            eligible_workspace=False,
        )
        with (
            patch.object(reasonfirst_cli, "load_setup_state", return_value=state),
            patch.object(
                reasonfirst_cli,
                "resolve_tunnel_client",
                return_value="/tools/tunnel-client",
            ),
            patch.object(
                reasonfirst_cli,
                "runtime_status",
                return_value={
                    "ok": False,
                    "ready": False,
                    "alias": "reasonfirst-gitlab",
                },
            ),
            patch.object(reasonfirst_cli.sys.stdin, "isatty", return_value=False),
            patch.dict(reasonfirst_cli.os.environ, {}, clear=True),
            patch.object(reasonfirst_cli, "connect_runtime") as connect,
        ):
            code, payload = reasonfirst_cli._run_setup_repair(args)
        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["read_tunnel"]["action_required"])
        self.assertTrue(any("CONTROL_PLANE_API_KEY" in item for item in payload["actions"]))
        connect.assert_not_called()

    def test_version(self) -> None:
        out = io.StringIO()
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stdout(out):
            reasonfirst_cli.main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(f"reasonfirst {__version__}", out.getvalue().strip())


if __name__ == "__main__":
    unittest.main()
