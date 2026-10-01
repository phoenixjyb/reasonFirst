from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import venv

from gitlab_agent.config import AgentSettings
from gitlab_agent.python_runtime import (
    PythonBindingError, PythonBindings, discover_python, executable_fingerprint,
    probe_python, _capture_probe, _safe_probe_env,
)
from gitlab_agent.runner import CommandRunner
from gitlab_agent.workspace import WorkspaceManager, WorkspaceState


def settings_for(root: Path) -> AgentSettings:
    return AgentSettings(
        config_file=root / "unused.env", gitlab_base_url="https://gitlab.example.invalid",
        api_token="", api_verify_ssl=True, api_trust_env=False,
        git_token="", git_username="oauth2", git_trust_env=False,
        allowed_projects={"team/project"}, require_write_allowlist=True,
        workspace_root=root / "manager", branch_prefix="chatgpt/", default_base_ref="main",
        allowed_executables={"python", "python3"}, command_timeout_seconds=30,
        max_output_bytes=120000, max_file_bytes=1000000,
        git_author_name="Example", git_author_email="example@example.invalid",
    )


class PythonRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="rf-python-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = settings_for(self.root)
        self.manager = WorkspaceManager(self.settings)
        self.wid = "123456abcdef"
        self.worktree = self.manager.worktrees_dir / self.wid
        self.worktree.mkdir()
        self.state = WorkspaceState(
            workspace_id=self.wid, project="team/project", base_ref="main",
            base_sha="a" * 40, repo_path=str(self.root / "cache.git"),
            worktree_path=str(self.worktree), branch="chatgpt/smoke", created_at="fixture",
        )
        self.manager._save_state(self.state)
        self.bindings = PythonBindings(self.manager)
        self.runner = CommandRunner(self.settings, self.manager)

    def bind(self, executable: str | None = None):
        plan = self.bindings.plan(self.wid, "python3", executable or sys.executable)
        return self.bindings.bind(plan, approved=True)

    def test_explicit_approval_required_without_probing_or_saving(self):
        plan = self.bindings.plan(self.wid, "python3", sys.executable)
        with patch("gitlab_agent.python_runtime.probe_python") as probe:
            with self.assertRaises(PythonBindingError):
                self.bindings.bind(plan)
        probe.assert_not_called()
        self.assertFalse(self.bindings.root.exists())

    def test_binding_is_outside_worktree_and_preserves_task_state(self):
        before = self.manager._state_path(self.wid).read_bytes()
        record = self.bind()
        self.assertEqual(record["runtime"]["version"], list(sys.version_info[:3]))
        self.assertEqual(self.manager._state_path(self.wid).read_bytes(), before)
        self.assertEqual(list(self.worktree.iterdir()), [])
        self.assertEqual(self.bindings.read(self.wid, "python3"), record)

    def test_requested_command_allowlist_cannot_be_bypassed(self):
        self.bind()
        self.manager.settings = replace(self.settings, allowed_executables={"python"})
        with self.assertRaises(PythonBindingError):
            PythonBindings(self.manager).read(self.wid, "python3")
        with self.assertRaises(RuntimeError):
            self.runner.run(self.wid, [sys.executable, "-c", "pass"])

    def test_project_authorization_precedes_probe(self):
        self.manager.settings = replace(self.settings, allowed_projects={"other/project"})
        with patch("gitlab_agent.python_runtime.probe_python") as probe:
            with self.assertRaises(RuntimeError):
                PythonBindings(self.manager).plan(self.wid, "python3", sys.executable)
        probe.assert_not_called()

    def test_unbound_commands_are_not_implicitly_substituted(self):
        request = ["python3", "-m", "unittest"]
        with patch("gitlab_agent.python_runtime.probe_python") as probe:
            self.assertEqual(self.bindings.resolve(self.wid, request)["resolved_argv"], request)
        probe.assert_not_called()
        self.assertFalse(self.bindings.root.exists())

    def test_only_selected_alias_changes_and_arguments_are_preserved(self):
        self.bind()
        original = ["python3", "-c", "print('a b')", "quotes'and spaces", "中文"]
        result = self.bindings.resolve(self.wid, original)
        self.assertEqual(result["requested_argv"], original)
        self.assertEqual(result["resolved_argv"], [sys.executable, *original[1:]])
        self.assertEqual(self.bindings.resolve(self.wid, ["python", "-V"])["resolved_argv"], ["python", "-V"])
        for command in ("py", "bash", "../python3"):
            with self.assertRaises(PythonBindingError):
                self.bindings.plan(self.wid, command, sys.executable)

    def test_relative_path_launcher_and_script_rejected(self):
        for executable in ("python3", "./python", "C:python.exe", str(self.root / "py.exe"), str(self.root / "python.bat")):
            with self.subTest(executable=executable), self.assertRaises(PythonBindingError):
                self.bindings.plan(self.wid, "python3", executable)

    def test_existing_binding_requires_explicit_replace(self):
        first = self.bind()
        plan = self.bindings.plan(self.wid, "python3", sys.executable)
        with self.assertRaises(PythonBindingError):
            self.bindings.bind(plan, approved=True)
        self.assertEqual(self.bindings.read(self.wid, "python3"), first)
        self.bindings.bind(plan, approved=True, replace=True)

    def test_scope_change_or_record_copy_fails_closed(self):
        self.bind()
        self.state.base_sha = "b" * 40
        self.manager._save_state(self.state)
        with patch("gitlab_agent.python_runtime.probe_python") as probe:
            with self.assertRaises(PythonBindingError):
                self.bindings.resolve(self.wid, ["python3", "-V"])
        probe.assert_not_called()

    def test_binary_change_rejected_before_execution(self):
        executable = self.root / "python.exe"
        executable.write_bytes(b"synthetic runtime not executable")
        fingerprint = executable_fingerprint(str(executable))
        runtime = {"executable": str(executable), "version": [3, 12, 0],
                   "implementation": "cpython", "fingerprint": fingerprint}
        plan = self.bindings.plan(self.wid, "python3", str(executable))
        with patch("gitlab_agent.python_runtime.probe_python", return_value=runtime):
            self.bindings.bind(plan, approved=True)
        executable.write_bytes(b"changed untrusted bytes")
        with patch("gitlab_agent.python_runtime.probe_python") as probe:
            with self.assertRaises(PythonBindingError):
                self.bindings.resolve(self.wid, ["python3", "-V"])
        probe.assert_not_called()

    def test_binding_plan_detects_changes_after_review(self):
        plan = self.bindings.plan(self.wid, "python3", sys.executable)
        self.state.base_sha = "b" * 40
        self.manager._save_state(self.state)
        with self.assertRaises(PythonBindingError):
            self.bindings.bind(plan, approved=True)
        self.assertFalse(self.bindings.root.exists())

    def test_malformed_record_does_not_fall_back_to_path(self):
        self.bind()
        path = self.bindings._path(self.wid, "python3")
        for content in ('{"version":1,"version":1}', '[]', '{"version":true}', 'x' * 32769):
            path.write_text(content, encoding="utf-8")
            with self.subTest(content=content[:30]), self.assertRaises(PythonBindingError):
                self.bindings.resolve(self.wid, ["python3", "-V"])

    def test_symlinked_binding_storage_rejected(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        try:
            self.bindings.root.symlink_to(elsewhere, target_is_directory=True)
        except OSError:
            self.skipTest("Host does not permit unprivileged directory symlinks")
        with self.assertRaises(PythonBindingError):
            self.bind()
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_probe_invalid_identity_and_failure_do_not_save(self):
        invalid = ['[]', '{}', '{"executable":"x","version":[true,12,0],"implementation":"cpython"}', '{bad']
        for text in invalid:
            with self.subTest(text=text), patch("gitlab_agent.python_runtime._capture_probe", return_value=text):
                with self.assertRaises(PythonBindingError):
                    self.bind()
        self.assertFalse(self.bindings.root.exists())

    def test_probe_failure_and_output_limits_hide_raw_output(self):
        for argv in ([sys.executable, "-c", "raise SystemExit(4)"],
                     [sys.executable, "-c", "print('x'*40000)"],
                     [sys.executable, "-c", "import sys;sys.stdout.buffer.write(bytes([255]))"]):
            with self.assertRaises(PythonBindingError):
                _capture_probe(argv)
        with patch("gitlab_agent.python_runtime.subprocess.run", side_effect=subprocess.TimeoutExpired("probe", 10)):
            with self.assertRaises(PythonBindingError):
                self.bind()
        self.assertFalse(self.bindings.root.exists())

    def test_version_selection_and_probe_mismatch_are_rejected(self):
        for version in ("", "latest", "-3.12", "3.12 && command"):
            with self.assertRaises(PythonBindingError):
                discover_python(version)
        with self.assertRaises(PythonBindingError):
            probe_python(sys.executable, "3.99")

    def test_discovery_uses_uv_offline_not_py_or_tool_python(self):
        with patch("gitlab_agent.python_runtime.shutil.which", return_value="uv"), \
             patch("gitlab_agent.python_runtime._capture_probe", return_value=sys.executable) as capture:
            self.assertEqual(discover_python("3.12"), sys.executable)
        argv = capture.call_args.args[0]
        for flag in ("--offline", "--no-config", "--system", "--no-python-downloads", "--no-project"):
            self.assertIn(flag, argv)
        self.assertNotIn("py", argv)
        self.assertNotIn("install", argv)

    def test_probe_environment_does_not_inherit_credentials_or_python_overrides(self):
        with patch.dict(os.environ, {
            "EXAMPLE_TOKEN": "synthetic", "PYTHONPATH": "untrusted",
            "VIRTUAL_ENV": "other", "GIT_ASKPASS": "unexpected",
            "PYTHONEXECUTABLE": "other", "__PYVENV_LAUNCHER__": "other",
        }):
            env = _safe_probe_env()
        self.assertNotIn("EXAMPLE_TOKEN", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("VIRTUAL_ENV", env)
        self.assertNotIn("GIT_ASKPASS", env)
        self.assertNotIn("PYTHONEXECUTABLE", env)
        self.assertNotIn("__PYVENV_LAUNCHER__", env)

    def test_real_execution_works_without_python3_or_launcher_on_path(self):
        self.bind()
        with patch.dict(os.environ, {"PATH": "", "USERPROFILE": str(self.root / "no-user"), "HOME": str(self.root / "no-user")}):
            result = self.runner.run(self.wid, ["python3", "-c", "import sys;print(sys.version_info.major)"])
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["stdout"].strip(), "3")
        self.assertEqual(result["execution"]["resolved_argv"][0], sys.executable)
        self.assertEqual(result["argv"][0], "python3")

    def test_bound_execution_survives_gbk_default_and_preserves_failure(self):
        self.bind()
        with patch("subprocess._text_encoding", return_value="gbk"):
            result = self.runner.run(self.wid, ["python3", "-c", "import sys;print('中文');sys.exit(7)"])
        self.assertEqual(result["returncode"], 7)
        self.assertEqual(result["stdout"].strip(), "中文")

    def test_timeout_is_not_a_pass_and_keeps_resolution(self):
        self.bind()
        result = self.runner.run(self.wid, ["python3", "-c", "import time;time.sleep(5)"], timeout_seconds=1)
        self.assertTrue(result["timed_out"])
        self.assertIsNone(result["returncode"])
        self.assertEqual(result["execution"]["resolved_argv"][0], sys.executable)

    def make_venv(self, name: str) -> Path:
        envdir = self.root / name
        venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(envdir)
        return envdir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def directory_alias(self, name: str, target: Path) -> Path:
        alias = self.root / name
        try:
            alias.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                self.skipTest("Host does not permit unprivileged directory symlinks")
            raise
        return alias

    def identity_text(self, executable: str | Path) -> str:
        return json.dumps({"executable": str(executable),
                           "version": list(sys.version_info[:3]),
                           "implementation": sys.implementation.name})

    def test_probe_accepts_canonical_parent_but_preserves_invocation(self):
        executable = self.make_venv("Python 空间's env")
        envdir = executable.parent.parent
        alias = self.directory_alias("Alias 空间's env", envdir)
        selected = alias / executable.relative_to(envdir)
        # Model the macOS framework launcher: realpath(dirname), NOT realpath
        # of the final python symlink. All filesystem identities are real here.
        reported = selected.parent.resolve(strict=True) / selected.name
        with patch("gitlab_agent.python_runtime._capture_probe",
                   return_value=self.identity_text(reported)):
            record = self.bind(str(selected))
        self.assertEqual(record["runtime"]["executable"], str(selected))
        result = self.runner.run(self.wid, ["python3", "-c", "import sys;print(sys.prefix)"])
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["execution"]["resolved_argv"][0], str(selected))
        self.assertEqual(Path(result["stdout"].strip()).resolve(strict=True),
                         envdir.resolve(strict=True))

    def test_probe_rejects_other_venv_and_base_even_with_shared_binary(self):
        selected = self.make_venv("first env")
        other = self.make_venv("second env")
        self.assertEqual(executable_fingerprint(str(selected))["sha256"],
                         executable_fingerprint(str(other))["sha256"])
        other_leaf = selected.with_name("python3.exe" if os.name == "nt" else "python3")
        for reported in (other, Path(sys.executable).resolve(strict=True), other_leaf):
            with self.subTest(reported=reported), patch(
                "gitlab_agent.python_runtime._capture_probe",
                return_value=self.identity_text(reported),
            ):
                with self.assertRaises(PythonBindingError):
                    self.bind(str(selected))
        self.assertFalse(self.bindings.root.exists())

    def test_probe_rejects_relative_and_invalid_identity_paths(self):
        for reported in (os.path.relpath(sys.executable), "", "python3",
                         str(self.root / "missing" / "python"), "x" * 4097,
                         sys.executable + "\x00"):
            with self.subTest(reported=reported[:80]), patch(
                "gitlab_agent.python_runtime._capture_probe",
                return_value=self.identity_text(reported),
            ):
                with self.assertRaises(PythonBindingError):
                    self.bind()
        self.assertFalse(self.bindings.root.exists())

    def test_probe_retains_isolation_no_site_and_no_bytecode_flags(self):
        with patch("gitlab_agent.python_runtime._capture_probe",
                   return_value=self.identity_text(sys.executable)) as capture:
            probe_python(sys.executable)
        self.assertEqual(capture.call_args.args[0][:5],
                         [sys.executable, "-I", "-S", "-B", "-c"])

    def test_retargeted_parent_alias_rejected_before_reprobe(self):
        first = self.make_venv("first env")
        second = self.make_venv("second env")
        first_dir, second_dir = first.parent.parent, second.parent.parent
        # Equal executable bytes AND cfg bytes must not hide a directory change.
        (second_dir / "pyvenv.cfg").write_bytes((first_dir / "pyvenv.cfg").read_bytes())
        alias = self.directory_alias("selected env", first_dir)
        selected = alias / first.relative_to(first_dir)
        self.bind(str(selected))
        alias.unlink()
        alias.symlink_to(second_dir, target_is_directory=True)
        with patch("gitlab_agent.python_runtime.probe_python") as probe:
            with self.assertRaises(PythonBindingError):
                self.bindings.read(self.wid, "python3")
        probe.assert_not_called()

    def test_bound_execution_ignores_inherited_launcher_identity(self):
        selected = self.make_venv("selected env")
        self.bind(str(selected))
        for key in ("PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__"):
            with self.subTest(key=key), patch.dict(os.environ, {key: sys.executable}):
                result = self.runner.run(
                    self.wid, ["python3", "-c", "import sys;print(sys.prefix)"],
                )
                self.assertEqual(result["returncode"], 0)
                self.assertEqual(Path(result["stdout"].strip()).resolve(strict=True),
                                 selected.parent.parent.resolve(strict=True))

    def test_real_venv_preserves_invocation_path_with_unicode_spaces_quotes(self):
        envdir = self.root / "Python 空间's env"
        venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(envdir)
        executable = envdir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self.bind(str(executable))
        result = self.runner.run(self.wid, ["python3", "-c", "import sys;print(sys.prefix)"])
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["execution"]["resolved_argv"][0], str(executable))
        self.assertEqual(Path(result["stdout"].strip()).resolve(strict=True),
                         envdir.resolve(strict=True))
        (envdir / "pyvenv.cfg").write_text("changed\n", encoding="utf-8")
        with self.assertRaises(PythonBindingError):
            self.bindings.resolve(self.wid, ["python3", "-V"])

    def test_optional_missing_command_does_not_block_required_validation(self):
        self.bind()
        context = {
            "required_executables": ["python3"],
            "validation_commands": [
                {"name": "tests", "argv": ["python3", "-V"], "required": True},
                {"name": "optional", "argv": ["optional-check"], "required": False},
            ],
        }
        with patch("gitlab_agent.python_runtime.shutil.which", return_value=None):
            plan = self.bindings.validation_plan(self.wid, context)
        self.assertTrue(plan["ok"])
        self.assertEqual(len(plan["commands"]), 2)

    def test_preflight_reports_missing_unbound_command_without_install(self):
        context = {"required_executables": ["python3"], "validation_commands": [{"name": "tests", "argv": ["python3", "-V"]}]}
        with patch("gitlab_agent.python_runtime.shutil.which", return_value=None):
            plan = self.bindings.validation_plan(self.wid, context)
            self.assertFalse(plan["ok"])
            self.bind()
            plan = self.bindings.validation_plan(self.wid, context)
            self.assertTrue(plan["ok"])
        self.assertEqual(plan["commands"][0]["requested_argv"], ["python3", "-V"])


if __name__ == "__main__":
    unittest.main()
