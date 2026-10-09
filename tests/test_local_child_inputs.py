"""Selected launch inputs at the actual Git, validation, Python and SSH edges."""
from __future__ import annotations

from contextlib import chdir
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from gitlab_agent import history_scan, python_runtime, runner, workspace
from gitlab_agent.bridge_preview.bridge_config import ExecutionTarget
from gitlab_agent.bridge_preview.remote_workspace import RemoteWorkspaceError, RemoteWorkspaceManager
from gitlab_agent.upgrade.service_children import (
    ServiceChildBindingError, capture_helper_children,
)

from test_python_runtime import settings_for


class LocalChildInputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rf-local-child-inputs-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.bin = self.root / "selected bin"
        self.bin.mkdir()
        self.cwd = self.root / "selected cwd"
        self.cwd.mkdir()
        self.home = self.root / "selected home"
        self.home.mkdir()
        self.executables = {}
        for name in ("git", "ssh", "pytest"):
            path = self.bin / (name + ".exe" if os.name == "nt" else name)
            path.write_bytes(b"synthetic executable observation\n")
            path.chmod(0o700)
            self.executables[name] = path
        self.settings = replace(
            settings_for(self.root), allowed_executables={"git", "pytest", "python3"},
            git_token="synthetic-local-child-credential", git_username="synthetic-user",
        )
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC"}}
        env.update({
            "PATH": str(self.bin), "PATHEXT": ".EXE;.CMD",
            "HOME": str(self.home), "USERPROFILE": str(self.home),
            "RF_CHILD_MARKER": "selected-value", "RF_PRIVATE_TOKEN": "synthetic-env-private",
            "SSH_AUTH_SOCK": "synthetic-auth-socket", "HTTP_PROXY": "http://proxy.invalid:9",
            "GIT_CONFIG_GLOBAL": "unselected-git-config", "PYTHONPATH": "unselected-python-path",
        })
        with patch.dict(os.environ, env, clear=True), chdir(self.cwd):
            self.context = capture_helper_children(self.settings)
        self.manager = workspace.WorkspaceManager(self.settings, child_context=self.context)
        self.wid = "123456abcdef"
        self.worktree = self.manager.worktrees_dir / self.wid
        self.worktree.mkdir()
        self.manager._save_state(workspace.WorkspaceState(
            workspace_id=self.wid, project="team/project", base_ref="main", base_sha="a" * 40,
            repo_path=str(self.root / "repo.git"), worktree_path=str(self.worktree),
            branch="chatgpt/synthetic", created_at="fixture",
        ))

    def completed(self, argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "synthetic output", "")

    def test_git_uses_absolute_selected_binary_and_role_specific_retained_environment(self):
        with patch.dict(os.environ, {"PATH": "changed-path", "RF_CHILD_MARKER": "changed"}), \
                patch.object(workspace.subprocess, "run", side_effect=self.completed) as run:
            self.manager._run_git(["status"], auth=True, auth_url="https://gitlab.example.invalid/team/project.git")
        args, kwargs = run.call_args
        self.assertEqual(args[0][0], str(self.executables["git"]))
        self.assertEqual(kwargs["cwd"], self.cwd)
        self.assertEqual(kwargs["env"]["RF_CHILD_MARKER"], "selected-value")
        self.assertNotIn("HTTP_PROXY", kwargs["env"])
        self.assertEqual(kwargs["env"]["GCM_INTERACTIVE"], "never")
        self.assertEqual(kwargs["env"]["GITLAB_AGENT_GIT_TOKEN"], self.settings.git_token)
        self.assertNotIn(self.settings.git_token, repr(args))
        self.assertFalse(Path(kwargs["env"]["GIT_ASKPASS"]).exists())

    def test_git_failure_does_not_reflect_child_output_or_launch_details(self):
        with patch.object(workspace.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 1, self.settings.git_token, "synthetic-env-private")):
            with self.assertRaisesRegex(RuntimeError, "^child_command_failed$"):
                self.manager._run_git(["status"])
        with patch.object(workspace.subprocess, "run", side_effect=OSError(self.settings.git_token)):
            with self.assertRaisesRegex(RuntimeError, "^child_launch_failed$"):
                self.manager._run_git(["status"])

    def test_manager_rejects_equal_value_settings_from_another_authority(self):
        with self.assertRaisesRegex(ServiceChildBindingError, "^invalid_child_context$"):
            workspace.WorkspaceManager(replace(self.settings), child_context=self.context)

    def test_validation_preserves_allowlist_then_uses_selected_absolute_executable(self):
        command_runner = runner.CommandRunner(self.settings, self.manager)
        with patch.dict(os.environ, {"PATH": "changed-path", "RF_CHILD_MARKER": "changed"}), \
                patch.object(runner.subprocess, "run", side_effect=self.completed) as run:
            result = command_runner.run(self.wid, ["pytest", "-q"])
        args, kwargs = run.call_args
        self.assertEqual(args[0], [str(self.executables["pytest"]), "-q"])
        self.assertEqual(result["argv"], ["pytest", "-q"])
        self.assertEqual(result["execution"]["resolved_argv"], args[0])
        self.assertEqual(kwargs["cwd"], self.worktree)
        self.assertEqual(kwargs["env"]["RF_CHILD_MARKER"], "selected-value")
        self.assertNotIn("RF_PRIVATE_TOKEN", kwargs["env"])
        self.assertNotIn("SSH_AUTH_SOCK", kwargs["env"])
        self.assertEqual(kwargs["env"]["HOME"], str(self.manager.home_dir / self.wid))
        with patch.object(runner.subprocess, "run") as forbidden:
            for argv in ([str(self.executables["pytest"])], ["unapproved"]):
                with self.subTest(argv=argv), self.assertRaises(RuntimeError):
                    command_runner.run(self.wid, argv)
            forbidden.assert_not_called()

    def test_existing_approved_python_keeps_its_invocation_and_retained_probe_environment(self):
        bindings = python_runtime.PythonBindings(self.manager)
        plan = bindings.plan(self.wid, "python3", sys.executable)
        bindings.bind(plan, approved=True)
        command = ["python3", "-I", "-B", "-c",
                   "import json,os,sys; print(json.dumps([sys.executable,os.getenv('RF_CHILD_MARKER'),"
                   "os.getenv('RF_PRIVATE_TOKEN'),os.getenv('HOME')]))"]
        with patch.dict(os.environ, {"PATH": "changed-path", "RF_CHILD_MARKER": "changed"}):
            result = runner.CommandRunner(self.settings, self.manager).run(self.wid, command)
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["execution"]["resolved_argv"], [sys.executable, *command[1:]])
        evidence = json.loads(result["stdout"])
        self.assertTrue(python_runtime._matches_invocation_path(evidence[0], Path(sys.executable)))
        self.assertEqual(evidence[1:], ["selected-value", None, str(self.manager.home_dir / self.wid)])
        env = python_runtime._safe_probe_env(child_context=self.context)
        self.assertEqual(env["RF_CHILD_MARKER"], "selected-value")
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("RF_PRIVATE_TOKEN", env)

    def test_history_scan_resolves_git_from_retained_inputs_and_keeps_its_config_isolation(self):
        process = SimpleNamespace(stdout=io.BytesIO(b"false\n"), returncode=0,
                                  wait=Mock(return_value=0), poll=Mock(return_value=0), kill=Mock())
        with patch.dict(os.environ, {"PATH": "changed-path", "RF_CHILD_MARKER": "changed"}), \
                patch.object(history_scan.subprocess, "Popen", return_value=process) as launch:
            result = history_scan._bounded_git(
                self.worktree, ["rev-parse", "--is-shallow-repository"],
                deadline=time.monotonic() + 5, max_bytes=1024, child_context=self.context,
            )
        self.assertEqual(result, b"false\n")
        args, kwargs = launch.call_args
        self.assertEqual(args[0][0], str(self.executables["git"]))
        self.assertEqual(kwargs["cwd"], self.worktree)
        self.assertEqual(kwargs["env"]["RF_CHILD_MARKER"], "selected-value")
        self.assertEqual(kwargs["env"]["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(kwargs["env"]["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertNotIn("RF_PRIVATE_TOKEN", kwargs["env"])

    def test_remote_manager_binds_only_the_local_ssh_launch(self):
        target = ExecutionTarget(type="ssh", name="selected", host="synthetic.invalid",
                                 repo="/remote/repo", codex_backend="remote-ssh", remote_codex="codex")
        manager = RemoteWorkspaceManager(target, child_context=self.context)
        with patch.dict(os.environ, {"PATH": "changed-path", "RF_CHILD_MARKER": "changed"}), \
                patch("gitlab_agent.bridge_preview.remote_workspace.subprocess.run",
                      side_effect=self.completed) as launch:
            manager._ssh("printf synthetic")
        args, kwargs = launch.call_args
        self.assertEqual(args[0][0], str(self.executables["ssh"]))
        self.assertEqual(args[0][-3:], ["--", "synthetic.invalid", "sh -s"])
        self.assertEqual(kwargs["input"], "printf synthetic\n")
        self.assertEqual(kwargs["cwd"], self.cwd)
        self.assertEqual(kwargs["env"]["RF_CHILD_MARKER"], "selected-value")
        with patch("gitlab_agent.bridge_preview.remote_workspace.subprocess.run", return_value=
                   subprocess.CompletedProcess([], 1, "private stdout", "private stderr")):
            self.assertEqual(manager.probe()["stderr"], "child_probe_failed")
            with self.assertRaisesRegex(RemoteWorkspaceError, "^child_command_failed$"):
                manager._ssh("printf synthetic")
            failed = manager._ssh("printf synthetic", check=False)
            self.assertEqual((failed.stdout, failed.stderr), ("", "child_command_failed"))

    def test_remote_credential_retry_never_exports_failed_fetch_output(self):
        target = ExecutionTarget(type="ssh", name="selected", host="synthetic.invalid",
                                 repo="/remote/repo", codex_backend="remote-ssh", remote_codex="codex")
        manager = RemoteWorkspaceManager(
            target, child_context=self.context, gitlab_host="gitlab.example.invalid",
            git_username=self.settings.git_username, git_password=self.settings.git_token,
        )
        replies = [subprocess.CompletedProcess([], 0, "https://gitlab.example.invalid/team/project.git\n", ""),
                   subprocess.CompletedProcess([], 1, "", "initial fetch failed"),
                   subprocess.CompletedProcess([], 1, self.settings.git_token, self.settings.git_token)]
        with patch("gitlab_agent.bridge_preview.remote_workspace.subprocess.run", side_effect=replies) as run:
            with self.assertRaisesRegex(RemoteWorkspaceError, "^child_command_failed$"):
                manager.create_workspace(project="team/project", base_ref="main", task="synthetic")
        self.assertEqual(run.call_count, 3)
        self.assertIn(self.settings.git_token, run.call_args.kwargs["input"])
        self.assertNotIn(self.settings.git_token, repr(run.call_args.args))

    def test_selected_executable_drift_stays_failed_before_later_git_or_validation(self):
        original = self.executables["git"].read_bytes()
        with patch.object(workspace.subprocess, "run", side_effect=self.completed) as launch:
            self.manager._run_git(["status"])
            self.executables["git"].write_bytes(b"changed executable\n")
            for restore in (False, True):
                if restore:
                    self.executables["git"].write_bytes(original)
                with self.assertRaisesRegex(ServiceChildBindingError, "^child_executable_changed$"):
                    self.manager._run_git(["status"])
            self.assertEqual(launch.call_count, 1)
            with self.assertRaises(ServiceChildBindingError):
                runner.CommandRunner(self.settings, self.manager).run(self.wid, ["pytest", "-q"])
            self.assertEqual(launch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
