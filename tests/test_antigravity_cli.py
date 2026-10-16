"""Antigravity interactive adapter: synthetic fixtures, never provider sessions."""
from __future__ import annotations

import contextlib
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from gitlab_agent import cli, setup_actions, setup_config, setup_inspection, setup_status
from gitlab_agent.config import AgentSettings
from gitlab_agent.doctor import run_doctor
from gitlab_agent.project_config import parse_project_config
from gitlab_agent.setup_state import SetupState, save_setup_state, load_setup_state
from gitlab_agent.setup_status import build_setup_status
from gitlab_agent.worker_policy import (
    WorkerPolicy, build_worker_argv, default_worker_policy, launch_policy_evidence,
    resolve_worker_policy,
)
from gitlab_agent.workspace import WorkspaceManager


def settings(root: Path, **overrides) -> AgentSettings:
    return AgentSettings(
        config_file=root / "agent.env", gitlab_base_url="https://gitlab.example.invalid",
        api_token="", api_verify_ssl=True, api_trust_env=False, git_token="",
        git_username="oauth2", git_trust_env=False, allowed_projects={"team/project"},
        require_write_allowlist=True, workspace_root=root / "state", branch_prefix="chatgpt/",
        default_base_ref="main", allowed_executables={"python", "python3"},
        command_timeout_seconds=30, max_output_bytes=120000, max_file_bytes=1000000,
        git_author_name="Fixture", git_author_email="fixture@example.invalid", **overrides,
    )


class AntigravityPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = default_worker_policy("antigravity")

    def test_default_launch_is_interactive_and_sandbox_request_only(self):
        self.assertEqual(build_worker_argv(self.policy, "Task"),
                         ["agy", "--sandbox", "--prompt-interactive", "Task"])
        self.assertIsNone(self.policy.model)
        self.assertIsNone(self.policy.reasoning_effort)
        self.assertIsNone(self.policy.network_access)
        self.assertIsNone(self.policy.approval_policy)

    def test_provider_overrides_remain_single_arguments(self):
        prompt = 'Task: preserve quotes " $(touch nope);\n中文'
        p = replace(self.policy, model='fixture model " -p injected', reasoning_effort="max")
        self.assertEqual(build_worker_argv(p, prompt), ["agy", "--sandbox",
                         '--model=fixture model " -p injected', "--effort=max",
                         "--prompt-interactive", prompt])

    def test_every_supported_effort_and_invalid_efforts(self):
        for effort in ("low", "medium", "high", "xhigh", "max"):
            with self.subTest(effort=effort):
                self.assertIn("--effort=" + effort, build_worker_argv(replace(self.policy, reasoning_effort=effort), "Task"))
        for effort in ("minimal", "ultra", "", "HIGH"):
            with self.subTest(effort=effort), self.assertRaises(ValueError):
                build_worker_argv(replace(self.policy, reasoning_effort=effort), "Task")

    def test_unsupported_policy_is_rejected_not_silently_dropped(self):
        for override in ({"execution_mode": "programmatic"}, {"execution_mode": "exec"},
                         {"sandbox_mode": None}, {"sandbox_mode": "workspace-write"},
                         {"approval_policy": "never"}, {"network_access": False},
                         {"disable_builtin_mcps": True}, {"allow_tools": ("read",)},
                         {"deny_tools": ("shell(git push)",)}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                build_worker_argv(replace(self.policy, **override), "Task")

    def test_invalid_model_is_rejected(self):
        for value in ("", "  ", "a\nb", "a\x00b", "a\x7fb"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_worker_argv(replace(self.policy, model=value), "Task")

    def test_evidence_does_not_claim_permissions_or_model_verified(self):
        e = launch_policy_evidence(self.policy, surface="antigravity-cli")
        self.assertEqual(e["surface"], "antigravity-cli")
        self.assertTrue(e["sandbox_requested"])
        self.assertFalse(e["permissions_verified"])
        self.assertFalse(e["runtime_verified"])
        self.assertEqual(e["conversation_strategy"], "fresh-task-handoff")
        self.assertEqual(e["permissions_source"], "provider-settings-not-inspected")
        self.assertNotIn("network_disabled", e)

    def test_policy_round_trip_preserves_supported_request(self):
        p = replace(self.policy, model="fixture", reasoning_effort="xhigh")
        self.assertEqual(WorkerPolicy.from_dict(p.to_dict()), p)

    def test_config_load_uses_optional_provider_fields_and_alias(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env = {"GITLAB_AGENT_ENV_FILE": str(root / "missing"),
                   "GITLAB_WORKSPACE_ROOT": str(root / "state"),
                   "GITLAB_BASE_URL": "https://gitlab.example.invalid"}
            with patch.dict(os.environ, env, clear=True):
                s = AgentSettings.load()
                self.assertEqual(s.workspace_root, root / "state")
                self.assertIsNone(s.antigravity_model)
                self.assertIsNone(s.antigravity_reasoning_effort)
            env.update(REASONFIRST_DEFAULT_BACKEND="agy", REASONFIRST_ANTIGRAVITY_MODEL="fixture",
                       REASONFIRST_ANTIGRAVITY_REASONING_EFFORT="max")
            with patch.dict(os.environ, env, clear=True):
                s = AgentSettings.load()
                self.assertEqual(s.workspace_root, root / "state")
            self.assertEqual(s.default_backend, "agy")
            p = resolve_worker_policy(s, "antigravity")
            self.assertEqual((p.model, p.reasoning_effort), ("fixture", "max"))
            env["REASONFIRST_ANTIGRAVITY_REASONING_EFFORT"] = "ultra"
            with patch.dict(os.environ, env, clear=True), self.assertRaisesRegex(
                RuntimeError,
                r"REASONFIRST_ANTIGRAVITY_REASONING_EFFORT must be one of:",
            ):
                AgentSettings.load()


class AntigravitySelectionTests(unittest.TestCase):
    def test_alias_and_canonical_select_agy(self):
        for name in ("agy", "antigravity-cli"):
            selected = cli._select_agent(name, which=lambda _: "/fixture/agy")
            self.assertEqual(selected["selected"], name)
            self.assertEqual(selected["executable"], "agy")
            self.assertEqual(cli._policy_backend_for_agent(name), "antigravity")
            self.assertTrue(selected["installed"])

    def test_automatic_fallback_order_is_unchanged(self):
        self.assertEqual(cli.DEFAULT_AGENT_ORDER, ["codex", "copilot"])
        self.assertEqual(cli._select_agent("auto", which=lambda n: "/fixture/" + n)["selected"], "codex")
        with self.assertRaisesRegex(RuntimeError, "opt-in"):
            cli._select_agent("auto", which=lambda n: "/fixture/agy" if n == "agy" else None)

    def test_pinned_project_can_explicitly_prefer_antigravity(self):
        for name in ("antigravity-cli", "agy"):
            result = cli._select_agent("auto", preferred_agents=[name], which=lambda n: "/fixture/" + n)
            self.assertEqual(result["selected"], name)
            self.assertEqual(result["preference_source"], "project")
            with tempfile.TemporaryDirectory() as td:
                contract = parse_project_config("version: 1\nagents:\n  preferred: [" + name + "]\n", settings=settings(Path(td)), source_ref="fixture-sha")
                self.assertTrue(contract.valid, contract.errors)
                self.assertEqual(contract.effective["preferred_agents"], [name])

    def test_missing_explicit_executable_is_not_available(self):
        result = cli._select_agent("agy", which=lambda _: None)
        self.assertFalse(result["installed"])
        self.assertIsNone(result["path"])

    def test_parser_accepts_alias_on_start_resume_continue(self):
        for backend in ("agy", "antigravity-cli"):
            for command, target in (("start", "team/project"), ("resume", "fixture"), ("continue", "fixture")):
                args = cli._build_parser().parse_args([command, target, "--agent", backend, "--goal", "Fixture task"])
                self.assertEqual(args.agent, backend)

    def test_setup_alias_is_normalized(self):
        with tempfile.TemporaryDirectory() as td:
            plan = setup_actions.plan_worker_use("agy", which=lambda n: "/fixture/agy" if n == "agy" else None,
                                                 desktop_socket=Path(td) / "missing")
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["backend"], "antigravity-cli")
        self.assertFalse(plan["authentication_verified"])

    def test_agent_list_and_config_report_correct_provider(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(cli.shutil, "which", side_effect=lambda n: str(root / n) if n == "agy" else None), \
                 patch.object(cli, "managed_app_server_socket", return_value=root / "missing"):
                listing = cli._available_agents()
            a = next(a for a in listing["agents"] if a["agent"] == "antigravity-cli")
            self.assertEqual(a["aliases"], ["agy"])
            self.assertEqual(a["executable"], "agy")
            self.assertTrue(a["installed"])
            self.assertEqual(a["path"], str(root / "agy"))
            self.assertFalse(a["authentication_checked"])
            desktop = next(a for a in listing["agents"] if a["agent"] == "codex-desktop")
            self.assertFalse(desktop["installed"])
            self.assertIsNone(desktop["path"])
            c = cli._safe_config(settings(root))
        self.assertEqual(c["worker_defaults"]["antigravity-cli"]["backend"], "antigravity")

    def test_setup_state_roundtrip_preserves_other_fields(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "setup.yaml"
            raw = {"mode": "standard", "selected_worker": "agy", "tunnel_id": "synthetic-tunnel",
                   "config_file": str(Path(td) / "agent.env"), "completed_phases": ["gitlab", "tunnel"]}
            save_setup_state(SetupState.from_dict(raw), path)
            value = load_setup_state(path)
            self.assertEqual(value.selected_worker, "antigravity-cli")
            self.assertEqual(value.tunnel_id, "synthetic-tunnel")
            self.assertEqual(value.completed_phases, ("gitlab", "tunnel"))

    def test_worker_use_changes_only_backend_and_preserves_config(self):
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / "agent.env"
            before = "# Keep this comment\nGITLAB_BASE_URL=https://gitlab.example.invalid\nGITLAB_ALLOWED_PROJECTS=team/project\n"
            config.write_text(before)
            before_bytes = config.read_bytes()
            backup_root = Path(td) / "backups"
            apply_env_updates = setup_actions.apply_env_updates

            def apply_updates(path, updates):
                return apply_env_updates(path, updates, backup_root=backup_root)

            with patch.dict(os.environ, {}, clear=True), \
                 patch.object(setup_config, "selected_user_config_path", return_value=config), \
                 patch.object(setup_actions, "selected_user_config_path", return_value=config), \
                 patch.object(setup_actions, "apply_env_updates", side_effect=apply_updates):
                plan = setup_actions.plan_worker_use("agy", which=lambda n: "/fixture/agy" if n == "agy" else None,
                                                    desktop_socket=Path(td) / "missing")
                result = setup_actions.apply_worker_use(plan)
            self.assertTrue(result["writes_performed"])
            self.assertEqual(config.read_text(), before + "REASONFIRST_DEFAULT_BACKEND=antigravity-cli\n")
            backup = Path(result["config_change"]["backup_file"])
            self.assertTrue(backup.resolve().is_relative_to(backup_root.resolve()))
            self.assertEqual(backup.read_bytes(), before_bytes)

    def test_detection_does_not_launch_provider_or_claim_bridge_support(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); config = settings(root); config.config_file.write_text("")
            build_inventory = setup_status.build_deployment_inventory
            real_read_config_bytes = setup_inspection.read_config_bytes
            read_paths = []

            def deployment_inventory(**kwargs):
                return build_inventory(home=root, **kwargs)

            def read_fixture_config(path):
                read_paths.append(path)
                self.assertEqual(path, config.config_file)
                return real_read_config_bytes(path)

            with patch.dict(os.environ, {
                "GITLAB_AGENT_ENV_FILE": str(config.config_file),
                "HOME": str(root),
                "USERPROFILE": str(root),
            }, clear=True), \
                 patch("subprocess.run", side_effect=AssertionError("no provider process")), \
                 patch.object(setup_status, "resolve_tunnel_client", return_value=None), \
                 patch.object(setup_status, "build_deployment_inventory", side_effect=deployment_inventory), \
                 patch.object(setup_inspection, "read_config_bytes", side_effect=read_fixture_config):
                workers = setup_actions.detect_workers(which=lambda n: str(root / n) if n == "agy" else None,
                                                        desktop_socket=root / "missing")
                for system_name, machine in (("Darwin", "arm64"), ("Windows", "AMD64")):
                    with self.subTest(system_name=system_name, machine=machine):
                        read_paths.clear()
                        result = build_setup_status(
                            which=lambda n: str(root / n) if n in {"agy", "git", "uv"} else None,
                            settings_loader=lambda: config, setup_state_loader=lambda _: SetupState(mode="full-chat"),
                            state_path=root / "setup.yaml", desktop_socket=root / "missing",
                            system_name=system_name, machine=machine)
                        self.assertTrue(read_paths)
                        for path in read_paths:
                            self.assertEqual(path, config.config_file)
                        self.assertTrue(result["config"]["valid"])
                        self.assertEqual(result["config"]["source"], "explicit_file")
                        self.assertEqual(result["system"]["platform_system"], system_name)
                        self.assertEqual(result["system"]["architecture"], machine)
                        a = next(w for w in result["workers"] if w["name"] == "antigravity-cli")
                        self.assertTrue(a["available"])
                        self.assertFalse(a["authentication_verified"])
                        self.assertFalse(result["readiness"]["ready"])
                        self.assertTrue(any(a["id"] == "configure-codex-bridge-worker" for a in result["plan"]["actions"]))
            a = next(w for w in workers if w["name"] == "antigravity-cli")
            self.assertTrue(a["available"])
            self.assertFalse(a["authentication_verified"])

    def test_doctor_optional_backend_detection(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); config = settings(root); config.config_file.write_text(""); config.config_file.chmod(0o600)
            with patch.dict(os.environ, {"HOME": td, "USERPROFILE": td}, clear=True):
                result = run_doctor(offline=True, settings_loader=lambda: config,
                                    which=lambda n: str(root / n) if n in {"agy", "git", "uv"} else None)
            c = {c["name"]: c for c in result["checks"]}
            self.assertEqual(c["agent_antigravity"]["status"], "pass")
            self.assertFalse(c["agent_antigravity"]["details"]["authentication_checked"])
            self.assertEqual(c["coding_backend"]["status"], "pass")


class AntigravityLaunchTests(unittest.TestCase):
    def handoff(self, cwd):
        return {"agent": "antigravity-cli", "worktree_path": str(cwd),
                "agent_prompt": "You are the antigravity-cli coding backend.\nTask: test only.",
                "worker_policy": default_worker_policy("antigravity").to_dict()}

    def test_direct_argv_cwd_and_return_code_without_codex_probe(self):
        with tempfile.TemporaryDirectory(prefix="rf space-") as td:
            root = Path(td); runner = Mock(return_value=SimpleNamespace(returncode=7))
            with patch.object(cli.sys.stdin, "isatty", return_value=True), \
                 patch.object(cli.sys.stdout, "isatty", return_value=True), \
                 patch.object(cli, "_backend_path", return_value=str(root / "bin" / "agy")), \
                 patch.object(cli, "AppServerClient") as codex:
                result = cli._launch_handoff(self.handoff(root), runner=runner)
            codex.assert_not_called()
            args, kwargs = runner.call_args
            self.assertEqual(args[0], [str(root / "bin" / "agy"), "--sandbox", "--prompt-interactive", self.handoff(root)["agent_prompt"]])
            self.assertEqual(kwargs, {"cwd": root.resolve(), "check": False})
            self.assertEqual(result["returncode"], 7)
            self.assertEqual(result["worker_policy_evidence"]["surface"], "antigravity-cli")
            self.assertEqual(result["resolved_executable"], str(root / "bin" / "agy"))
            for flag in ("--continue", "--conversation", "--project", "--agent", "--mode", "--add-dir",
                         "--print", "--dangerously-skip-permissions", "--remote-control"):
                self.assertNotIn(flag, args[0])

    def test_noninteractive_terminal_is_rejected_before_launch(self):
        for stdin, stdout in ((False, True), (True, False), (False, False)):
            runner = Mock()
            with self.subTest(stdin=stdin, stdout=stdout), \
                 patch.object(cli.sys.stdin, "isatty", return_value=stdin), \
                 patch.object(cli.sys.stdout, "isatty", return_value=stdout), \
                 self.assertRaisesRegex(RuntimeError, "interactive terminal"):
                cli._launch_handoff(self.handoff(Path.cwd()), runner=runner)
            runner.assert_not_called()

    def test_missing_executable_never_falls_back(self):
        runner = Mock()
        with patch.object(cli.sys.stdin, "isatty", return_value=True), \
             patch.object(cli.sys.stdout, "isatty", return_value=True), \
             patch.object(cli, "_backend_path", return_value=None), \
             self.assertRaisesRegex(RuntimeError, "agy is not available"):
            cli._launch_handoff(self.handoff(Path.cwd()), runner=runner)
        runner.assert_not_called()

    def test_policy_backend_mismatch_is_rejected(self):
        h = self.handoff(Path.cwd()); h["worker_policy"] = default_worker_policy("copilot").to_dict()
        runner = Mock()
        with self.assertRaisesRegex(ValueError, "does not match"):
            cli._launch_handoff(h, runner=runner)
        runner.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "Synthetic executable uses a POSIX shebang; provider integration is separate")
    def test_real_synthetic_executable_receives_exact_prompt_and_worktree(self):
        with tempfile.TemporaryDirectory(prefix="rf-agy process-") as td:
            root = Path(td); work = root / "work tree"; work.mkdir()
            executable = root / "agy"; output = root / "observed.json"
            executable.write_text(f"#!{sys.executable}\nimport os,sys,json\n"
                                  f"open({str(output)!r}, 'w').write(json.dumps({{'argv':sys.argv[1:], 'cwd':os.getcwd()}}))\n"
                                  "sys.exit(3)\n")
            executable.chmod(0o700)
            with patch.object(cli.sys.stdin, "isatty", return_value=True), \
                 patch.object(cli.sys.stdout, "isatty", return_value=True), \
                 patch.object(cli, "_backend_path", return_value=str(executable)):
                result = cli._launch_handoff(self.handoff(work))
            observed = json.loads(output.read_text())
            self.assertEqual(observed["cwd"], str(work.resolve()))
            self.assertEqual(observed["argv"], ["--sandbox", "--prompt-interactive", self.handoff(work)["agent_prompt"]])
            self.assertEqual(result["returncode"], 3)
            self.assertEqual(list(work.iterdir()), [])


class AntigravityTaskTests(unittest.TestCase):
    """Real Git worktrees and saved task state; no external provider/API."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="rf-agy task-"); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.seed = self.root / "seed"; self.seed.mkdir()
        def git(*args, cwd=None):
            return subprocess.check_output(["git", *args], cwd=cwd, stderr=subprocess.PIPE, text=True).strip()
        self.git = git
        git("init", "-b", "main", cwd=self.seed)
        git("config", "user.name", "Fixture", cwd=self.seed)
        git("config", "user.email", "fixture@example.invalid", cwd=self.seed)
        (self.seed / "README.md").write_text("fixture\n")
        (self.seed / ".actualcoder.yaml").write_text("version: 1\nagents:\n  preferred: [antigravity-cli]\n")
        git("add", "README.md", ".actualcoder.yaml", cwd=self.seed)
        git("commit", "-m", "fixture", cwd=self.seed)
        self.sha = git("rev-parse", "HEAD", cwd=self.seed)
        self.remote = self.root / "remote.git"; git("clone", "--bare", str(self.seed), str(self.remote))
        self.settings = settings(self.root)
        self.manager = WorkspaceManager(self.settings, url_resolver=lambda _: str(self.remote))

    def start(self, agent="antigravity-cli"):
        with patch.object(cli.shutil, "which", side_effect=lambda n: str(self.root / n) if n == "agy" else None):
            return cli._prepare_start(self.manager, self.settings, project="team/project", task_slug="agy-smoke",
                                      goal="Read only; run tests if available", requested_agent=agent, base_ref=self.sha,
                                      acceptance_criteria=["No file changes"], non_goals=["No commits or pushes"])

    def test_handoff_persists_scope_and_uses_correct_provider(self):
        h = self.start("agy")
        self.assertEqual(h["agent"], "agy")
        self.assertEqual(h["worker_policy"]["backend"], "antigravity")
        self.assertEqual(h["worker_policy_evidence"]["surface"], "antigravity-cli")
        self.assertNotIn("codex_prompt", h)
        self.assertIn("No commits or pushes", h["agent_prompt"])
        self.assertEqual(h["task_spec"]["requested_backend"], "agy")
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=h["worktree_path"]), self.sha)
        self.assertEqual(self.git("status", "--porcelain", cwd=h["worktree_path"]), "")

    def test_explicit_resume_uses_saved_goal_and_fresh_provider_handoff(self):
        h = self.start(); wid = h["workspace"]["workspace_id"]
        # New manager reloads actual persisted TaskSpec and attempt files.
        manager = WorkspaceManager(self.settings, url_resolver=lambda _: str(self.remote))
        before = manager.task_context(wid)["task_spec"]
        with patch.object(cli.shutil, "which", return_value=str(self.root / "agy")):
            resumed = cli._prepare_resume(manager, self.settings, None, workspace_id=wid,
                                           goal="", requested_agent="antigravity-cli")
        self.assertEqual(resumed["worktree_path"], h["worktree_path"])
        self.assertEqual(manager.task_context(wid)["task_spec"], before)
        self.assertIn(before["goal"], resumed["agent_prompt"])
        self.assertEqual(resumed["worker_policy_evidence"]["conversation_strategy"], "fresh-task-handoff")
        self.assertNotIn("--continue", resumed["agent_argv_shape"])
        self.assertEqual(self.git("status", "--porcelain", cwd=h["worktree_path"]), "")

    def test_user_default_overrides_project_preference(self):
        self.settings = replace(self.settings, default_backend="agy")
        h = self.start("auto")
        self.assertEqual(h["agent"], "agy")
        self.assertEqual(h["agent_selection"]["preference_source"], "user")

    def test_missing_worker_fails_before_workspace_creation(self):
        with patch.object(cli.shutil, "which", return_value=None), self.assertRaisesRegex(RuntimeError, "not installed"):
            cli._prepare_start(self.manager, self.settings, project="team/project", task_slug="missing",
                               goal="read", requested_agent="agy", base_ref=self.sha)
        self.assertFalse(any(self.settings.workspace_root.glob("worktrees/*")))


if __name__ == "__main__":
    unittest.main()
