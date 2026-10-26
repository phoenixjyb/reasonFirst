from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
import uuid
from functools import partial
from pathlib import Path
from unittest.mock import patch

from gitlab_agent import __version__, reasonfirst_cli, setup_actions
from gitlab_agent.config import AgentSettings
from gitlab_agent.setup_config import apply_env_updates, read_env_assignments
from gitlab_agent.setup_state import SetupState, load_setup_state, save_setup_state


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


class ReadFirstSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "gitlab.env"
        self.state_file = self.root / "setup.yaml"
        self.token = "fixture-" + uuid.uuid4().hex
        self.preflight = {
            "ok": True,
            "ref": "main",
            "resolved_commit_sha": "a" * 40,
            "writes_performed": False,
        }

    def args(
        self, *, mode: str | None = "standard", worker: str | None = None,
        skip_chatgpt: bool = True,
    ) -> argparse.Namespace:
        argv = [
            "setup", "--state-file", str(self.state_file),
            "--gitlab-url", "https://gitlab.example.test",
            "--project", "team/project", "--ref", "main", "--json",
        ]
        if mode is not None:
            argv.extend(["--mode", mode])
        if worker is not None:
            argv.extend(["--worker", worker])
        if skip_chatgpt:
            argv.append("--skip-chatgpt")
        return reasonfirst_cli._build_parser().parse_args(argv)

    @contextlib.contextmanager
    def isolated(self, *, workers: tuple[str, ...] = (), environment=None):
        inventory = [
            {
                "name": name, "available": name in workers,
                "path": f"/tools/{name}" if name in workers else None,
                "authentication_verified": False,
            }
            for name in ("codex-cli", "copilot-cli", "codex-desktop")
        ]
        env = {
            "HOME": str(self.root),
            "USERPROFILE": str(self.root),
            "GITLAB_AGENT_ENV_FILE": str(self.config),
            **(environment or {}),
        }
        writer = partial(apply_env_updates, backup_root=self.root / "backups")
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(reasonfirst_cli.sys.stdin, "isatty", return_value=True),
            patch.object(reasonfirst_cli, "detect_workers", return_value=inventory),
            patch.object(setup_actions, "detect_workers", return_value=inventory),
            patch.object(reasonfirst_cli, "apply_env_updates", new=writer),
            patch.object(setup_actions, "apply_env_updates", new=writer),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            yield

    def write_existing_config(self, backend: str | None = None) -> None:
        text = (
            "# Retain existing project grants and unrelated settings.\n"
            "GITLAB_BASE_URL=https://gitlab.example.test\n"
            f"GITLAB_TOKEN={self.token}\n"
            "GITLAB_ALLOWED_PROJECTS=team/existing,team/other\n"
            "GITLAB_VERIFY_SSL=true\n"
            "GITLAB_REQUIRE_WRITE_ALLOWLIST=true\n"
            "GITLAB_DEFAULT_BASE_REF=main\n"
        )
        if backend is not None:
            text += f"export REASONFIRST_DEFAULT_BACKEND='{backend}'\n"
        self.config.write_text(text, encoding="utf-8")

    def write_existing_state(self, worker: str | None) -> SetupState:
        phases = ["system", "gitlab", "tunnel", "chatgpt-read", "bridge"]
        if worker is not None:
            phases.insert(2, "worker")
        state = SetupState(
            mode="standard", selected_worker=worker, config_file=str(self.config),
            tunnel_id="tunnel_" + "a" * 32, tunnel_runtime="existing-read",
            tunnel_client_path="/tools/existing-tunnel-client",
            bridge_tunnel_id="tunnel_" + "b" * 32, bridge_runtime="existing-bridge",
            completed_phases=tuple(phases), installed_version="0.5.3",
            last_verified_at="2026-10-12T12:00:00+00:00",
        )
        save_setup_state(state, self.state_file)
        return state

    def assert_connection_identity_preserved(
        self, before: SetupState, after: SetupState,
    ) -> None:
        for field in (
            "mode", "config_file", "tunnel_id", "tunnel_runtime",
            "tunnel_client_path", "bridge_tunnel_id", "bridge_runtime",
        ):
            self.assertEqual(getattr(after, field), getattr(before, field), field)

    def test_fresh_setup_without_worker_reaches_read_tunnel_handoff(self) -> None:
        tunnel_id = "tunnel_" + "c" * 32
        runtime_key = "fixture-" + uuid.uuid4().hex
        answers = iter(["y", "y", tunnel_id, "n"])
        secrets = iter([self.token, runtime_key])

        def preflight(settings, project, *, ref):
            self.assertFalse(self.config.exists())
            self.assertFalse(self.state_file.exists())
            self.assertEqual(settings.default_backend, "auto")
            self.assertEqual(project, "team/project")
            return self.preflight

        def connect(**kwargs):
            saved = load_setup_state(self.state_file)
            self.assertIsNotNone(saved)
            self.assertIsNone(saved.selected_worker)
            self.assertNotIn("worker", saved.completed_phases)
            self.assertTrue(self.config.is_file())
            return {
                "ok": True, "ready": True, "alias": kwargs["alias"],
                "tunnel_id": kwargs["tunnel_id"],
            }

        with (
            self.isolated(),
            patch.object(reasonfirst_cli, "preflight_project", side_effect=preflight),
            patch.object(reasonfirst_cli, "resolve_tunnel_client",
                         return_value="/tools/tunnel-client"),
            patch.object(reasonfirst_cli, "resolve_read_mcp",
                         return_value="/tools/reasonfirst-gitlab-mcp"),
            patch.object(reasonfirst_cli, "open_handoff_pages", return_value={}),
            patch.object(reasonfirst_cli, "connect_runtime", side_effect=connect) as runtime,
        ):
            code, result = reasonfirst_cli._run_guided_setup(
                self.args(skip_chatgpt=False),
                input_fn=lambda _: next(answers), secret_fn=lambda _: next(secrets),
            )

        self.assertEqual(code, 0)
        runtime.assert_called_once()
        self.assertIsNone(result["worker"])
        self.assertTrue(result["worker_deferred"])
        self.assertTrue(result["local_tunnel_ready"])
        self.assertFalse(result["ready"])
        self.assertFalse(result["chatgpt_ready"])
        self.assertIn("gitlab_whoami", result["tunnel"]["handoff"]["acceptance_prompt"])
        self.assertNotIn("REASONFIRST_DEFAULT_BACKEND", read_env_assignments(self.config))
        saved = load_setup_state(self.state_file)
        self.assertIsNone(saved.selected_worker)
        self.assertNotIn("worker", saved.completed_phases)
        self.assertIn("tunnel", saved.completed_phases)
        self.assertEqual(saved.tunnel_id, tunnel_id)
        self.assertNotIn(self.token, repr(result))
        self.assertNotIn(runtime_key, repr(result))
        self.assertNotIn(runtime_key, self.config.read_text(encoding="utf-8"))
        self.assertNotIn(runtime_key, self.state_file.read_text(encoding="utf-8"))

    def test_deferred_setup_preflight_failure_or_decline_writes_nothing(self) -> None:
        for preflight_ok in (False, True):
            with self.subTest(preflight_ok=preflight_ok):
                with (
                    self.isolated(),
                    patch.object(
                        reasonfirst_cli, "preflight_project",
                        return_value={**self.preflight, "ok": preflight_ok},
                    ),
                    patch.object(reasonfirst_cli, "_guided_tunnel_after_local") as tunnel,
                ):
                    code, result = reasonfirst_cli._run_guided_setup(
                        self.args(), input_fn=lambda _: "n",
                        secret_fn=lambda _: self.token,
                    )
                self.assertEqual(code, 1)
                self.assertFalse(result["writes_performed"])
                tunnel.assert_not_called()
                self.assertEqual(list(self.root.iterdir()), [])

    def test_deferral_preserves_existing_backend_alias_grants_and_metadata(self) -> None:
        for backend in ("copilot-cli", "codex", "copilot"):
            with self.subTest(backend=backend):
                self.write_existing_config(backend)
                previous = self.write_existing_state("copilot-cli")

                def preflight(settings, project, *, ref):
                    self.assertEqual(settings.default_backend, backend)
                    return self.preflight

                with (
                    self.isolated(),
                    patch.object(reasonfirst_cli, "preflight_project", side_effect=preflight),
                ):
                    code, result = reasonfirst_cli._run_guided_setup(
                        self.args(worker="later"), input_fn=lambda _: "y",
                        secret_fn=lambda _: "",
                    )

                self.assertEqual(code, 0)
                self.assertTrue(result["worker_deferred"])
                self.assertIsNone(result["worker"])
                values = read_env_assignments(self.config)
                self.assertEqual(values["REASONFIRST_DEFAULT_BACKEND"], backend)
                self.assertEqual(
                    set(values["GITLAB_ALLOWED_PROJECTS"].split(",")),
                    {"team/existing", "team/other", "team/project"},
                )
                self.assertIn(
                    f"export REASONFIRST_DEFAULT_BACKEND='{backend}'",
                    self.config.read_text(encoding="utf-8"),
                )
                self.assertNotIn(
                    "REASONFIRST_DEFAULT_BACKEND",
                    result["config_change"]["updated_keys"],
                )
                saved = load_setup_state(self.state_file)
                self.assert_connection_identity_preserved(previous, saved)
                self.assertEqual(saved.selected_worker, previous.selected_worker)
                self.assertEqual(saved.completed_phases, previous.completed_phases)

    def test_deferral_preserves_exported_backend_without_copying_it_into_config(self) -> None:
        self.write_existing_config("copilot-cli")

        def preflight(settings, project, *, ref):
            self.assertEqual(settings.default_backend, "codex-desktop")
            return self.preflight

        with (
            self.isolated(environment={"REASONFIRST_DEFAULT_BACKEND": "codex-desktop"}),
            patch.object(reasonfirst_cli, "preflight_project", side_effect=preflight),
        ):
            code, result = reasonfirst_cli._run_guided_setup(
                self.args(worker="later"), input_fn=lambda _: "y", secret_fn=lambda _: "",
            )
            self.assertEqual(os.environ["REASONFIRST_DEFAULT_BACKEND"], "codex-desktop")
        self.assertEqual(code, 0)
        self.assertEqual(
            read_env_assignments(self.config)["REASONFIRST_DEFAULT_BACKEND"], "copilot-cli",
        )
        self.assertNotIn("REASONFIRST_DEFAULT_BACKEND", result["config_change"]["updated_keys"])

    def test_available_worker_can_be_explicitly_deferred_from_prompt(self) -> None:
        prompts = []

        def answer(prompt):
            prompts.append(prompt)
            return "later" if prompt.startswith("Worker ") else "y"

        with (
            self.isolated(workers=("codex-cli",)),
            patch.object(reasonfirst_cli, "preflight_project", return_value=self.preflight),
        ):
            code, result = reasonfirst_cli._run_guided_setup(
                self.args(), input_fn=answer, secret_fn=lambda _: self.token,
            )
        self.assertEqual(code, 0)
        self.assertTrue(result["worker_deferred"])
        worker_prompt = next(prompt for prompt in prompts if prompt.startswith("Worker "))
        self.assertIn("later", worker_prompt)
        self.assertIsNone(load_setup_state(self.state_file).selected_worker)

    def test_explicit_worker_requests_do_not_silently_defer_when_unavailable(self) -> None:
        for mode in ("standard", "cli-only", "full-chat"):
            for worker in ("auto", "codex-cli", "copilot-cli", "codex-desktop"):
                with self.subTest(mode=mode, worker=worker):
                    with (
                        self.isolated(),
                        patch.object(reasonfirst_cli, "preflight_project") as preflight,
                        self.assertRaises(RuntimeError),
                    ):
                        reasonfirst_cli._run_guided_setup(
                            self.args(mode=mode, worker=worker),
                            input_fn=lambda _: "y", secret_fn=lambda _: self.token,
                        )
                    preflight.assert_not_called()
                    self.assertEqual(list(self.root.iterdir()), [])

    def test_worker_deferral_is_rejected_outside_standard_setup(self) -> None:
        for mode in ("cli-only", "full-chat"):
            with self.subTest(mode=mode):
                with (
                    self.isolated(workers=("codex-cli",)),
                    patch.object(reasonfirst_cli, "preflight_project") as preflight,
                    self.assertRaisesRegex(ValueError, "standard"),
                ):
                    reasonfirst_cli._run_guided_setup(
                        self.args(mode=mode, worker="later"),
                        input_fn=lambda _: "y", secret_fn=lambda _: self.token,
                    )
                preflight.assert_not_called()
                self.assertEqual(list(self.root.iterdir()), [])
        for mode in ("cli-only", "full-chat"):
            with self.subTest(mode=mode, source="saved"):
                save_setup_state(SetupState(mode=mode), self.state_file)
                before = self.state_file.read_bytes()
                with (
                    self.isolated(workers=("codex-cli",)),
                    patch.object(reasonfirst_cli, "preflight_project") as preflight,
                    self.assertRaisesRegex(ValueError, "standard"),
                ):
                    reasonfirst_cli._run_guided_setup(
                        self.args(mode=None, worker="later"),
                        input_fn=lambda _: "y", secret_fn=lambda _: self.token,
                    )
                preflight.assert_not_called()
                self.assertEqual(self.state_file.read_bytes(), before)
                self.assertFalse(self.config.exists())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            reasonfirst_cli._build_parser().parse_args(["worker", "use", "later"])

    def test_explicit_worker_change_still_rejects_exported_backend_override(self) -> None:
        with (
            self.isolated(
                workers=("codex-cli",),
                environment={"REASONFIRST_DEFAULT_BACKEND": "copilot-cli"},
            ),
            patch.object(reasonfirst_cli, "preflight_project") as preflight,
            self.assertRaisesRegex(RuntimeError, "REASONFIRST_DEFAULT_BACKEND"),
        ):
            reasonfirst_cli._run_guided_setup(
                self.args(worker="codex-cli"),
                input_fn=lambda _: "y", secret_fn=lambda _: self.token,
            )
        preflight.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_worker_use_after_deferral_preserves_existing_read_connection(self) -> None:
        self.write_existing_config()
        previous = self.write_existing_state(None)
        before = read_env_assignments(self.config)
        output = io.StringIO()
        with (
            self.isolated(workers=("copilot-cli",)),
            contextlib.redirect_stdout(output),
        ):
            code = reasonfirst_cli.main([
                "worker", "use", "copilot-cli",
                "--state-file", str(self.state_file), "--json",
            ])

        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertFalse(result["authentication_verified"])
        saved = load_setup_state(self.state_file)
        self.assert_connection_identity_preserved(previous, saved)
        self.assertEqual(saved.selected_worker, "copilot-cli")
        self.assertEqual(
            set(saved.completed_phases), set(previous.completed_phases) | {"worker"},
        )
        after = read_env_assignments(self.config)
        self.assertEqual(after.pop("REASONFIRST_DEFAULT_BACKEND"), "copilot-cli")
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
