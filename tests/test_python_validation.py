from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from gitlab_agent.cli import main, _handoff
from gitlab_agent.finish import build_finish_plan, execute_finish, _load_base_contract
from gitlab_agent.python_runtime import PythonBindings, PythonBindingError
from gitlab_agent.runner import CommandRunner
from gitlab_agent.workspace import WorkspaceManager
from test_python_runtime import settings_for

CONTRACT = '''version: 1
validation:
  commands:
    - name: unit-tests
      argv: [python3, -m, unittest, discover, -s, tests, -v]
      required: true
      timeout_seconds: 10
executables:
  required: [python3]
protected_paths: [.actualcoder.yaml]
'''


class PythonValidationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rf-validation-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        seed = self.root / "seed"
        seed.mkdir()
        self.settings = settings_for(self.root)
        self.remote = self.root / "remote.git"
        self.git("init", "-b", "main", cwd=seed)
        self.git("config", "user.name", "Example", cwd=seed)
        self.git("config", "user.email", "example@example.invalid", cwd=seed)
        (seed / ".actualcoder.yaml").write_text(CONTRACT, encoding="utf-8")
        (seed / "README.md").write_text("Example synthetic Python project\n", encoding="utf-8")
        (seed / "tests").mkdir()
        (seed / "tests/test_example.py").write_text(
            "import unittest\nclass Example(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(1+1, 2)\n", encoding="utf-8",
        )
        self.git("add", ".", cwd=seed)
        self.git("commit", "-m", "synthetic baseline", cwd=seed)
        self.git("clone", "--bare", str(seed), str(self.remote))
        self.manager = WorkspaceManager(self.settings, url_resolver=lambda _: str(self.remote))
        self.state = self.manager.create_workspace("team/project", task_slug="python-test")
        self.wid = self.state["workspace_id"]
        self.worktree = Path(self.state["worktree_path"])
        self.runner = CommandRunner(self.settings, self.manager)
        self.bindings = PythonBindings(self.manager)

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                              text=True, encoding="utf-8").stdout.strip()

    def invoke(self, *argv, stdin=None):
        out = io.StringIO()
        with patch("gitlab_agent.cli.AgentSettings.load", return_value=self.settings), \
             patch("gitlab_agent.cli.WorkspaceManager", return_value=self.manager), \
             contextlib.redirect_stdout(out), patch("sys.stdin", stdin or io.StringIO()):
            code = main(list(argv))
        return code, json.loads(out.getvalue())

    def bind(self):
        return self.bindings.bind(self.bindings.plan(self.wid, "python3", sys.executable), approved=True)

    def test_cli_bind_then_validate_original_contract_without_repo_edits(self):
        code, report = self.invoke("python", "bind", self.wid, "--executable", sys.executable, "--yes")
        self.assertEqual(code, 0, report)
        self.assertTrue(report["saved"])
        code, plan = self.invoke("validate", self.wid, "--plan")
        self.assertEqual(code, 0, plan)
        self.assertFalse(plan["tests_executed"])
        self.assertEqual(plan["commands"][0]["requested_argv"][0], "python3")
        self.assertEqual(plan["commands"][0]["resolved_argv"][0], sys.executable)
        code, result = self.invoke("validate", self.wid)
        self.assertEqual(code, 0, result)
        self.assertTrue(result["validations"][0]["passed"])
        self.assertIn("Ran 1 test", result["validations"][0]["result"]["stderr"])
        self.assertFalse(result["publication_attempted"])
        self.assertFalse(result["worker_started"])
        self.assertFalse(result["workspace"]["dirty"])
        self.assertEqual((self.worktree / ".actualcoder.yaml").read_text(), CONTRACT)

    def test_decline_never_probes_or_writes_binding(self):
        class TTY(io.StringIO):
            def isatty(self): return True
        with patch("gitlab_agent.python_runtime.probe_python") as probe, contextlib.redirect_stderr(io.StringIO()):
            code, report = self.invoke("python", "bind", self.wid, "--executable", sys.executable, stdin=TTY("n\n"))
        self.assertEqual(code, 1)
        self.assertTrue(report["cancelled"])
        probe.assert_not_called()
        self.assertFalse(self.bindings.root.exists())

    def test_noninteractive_bind_requires_yes_and_exposes_review_plan(self):
        with patch("gitlab_agent.python_runtime.probe_python") as probe:
            code, report = self.invoke("python", "bind", self.wid, "--executable", sys.executable)
        self.assertEqual(code, 1)
        self.assertTrue(report["approval_required"])
        probe.assert_not_called()

    def test_handoff_and_validation_use_identical_mapping_without_rewriting_goal(self):
        record = self.bind()
        context, _ = _load_base_contract(self.settings, self.manager, self.wid)
        goal = "Inspect only; stop rather than change permissions"
        handoff = _handoff(self.manager, self.wid, goal, agent="codex-cli", project_context=context)
        evidence = handoff["python_runtime_evidence"][0]
        result = self.runner.run(self.wid, context["validation_commands"][0]["argv"])
        self.assertEqual(evidence["resolved_argv"], result["execution"]["resolved_argv"])
        self.assertEqual(evidence["python_binding"], record)
        self.assertIn(goal, handoff["agent_prompt"])
        self.assertIn("Do not fall back", handoff["agent_prompt"])
        self.assertEqual(handoff["codex_prompt"], handoff["agent_prompt"])
        self.assertEqual(handoff["worker_policy"]["approval_policy"], "on-request")

    def test_finish_and_validate_share_validation_and_binding_evidence(self):
        self.bind()
        (self.worktree / "README.md").write_text("Synthetic change for finish planning\n", encoding="utf-8")
        code, report = self.invoke("validate", self.wid)
        self.assertEqual(code, 0)
        plan = build_finish_plan(settings=self.settings, manager=self.manager, runner=self.runner,
                                 workspace_id=self.wid, commit_message="test: synthetic")
        self.assertTrue(plan["ok"], plan["blockers"])
        self.assertEqual(plan["validations"][0]["result"]["execution"], report["validations"][0]["result"]["execution"])
        self.assertIn("python_runtime_bindings", plan["snapshot"])
        # A new approval invalidates the previous finish snapshot before writes.
        self.bindings.bind(self.bindings.plan(self.wid, "python3", sys.executable), approved=True, replace=True)
        with self.assertRaises(RuntimeError), patch.object(self.manager, "commit") as commit:
            execute_finish(manager=self.manager, workspace_id=self.wid, plan=plan)
        commit.assert_not_called()

    def test_failed_tests_stay_nonzero_and_cannot_be_a_finish_pass(self):
        self.bind()
        test = self.worktree / "tests/test_example.py"
        test.write_text("import unittest\nclass Example(unittest.TestCase):\n    def test_add(self):\n        self.fail('expected failure fixture')\n", encoding="utf-8")
        code, result = self.invoke("validate", self.wid)
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        self.assertTrue(result["validations"][0]["blocking"])
        plan = build_finish_plan(settings=self.settings, manager=self.manager, runner=self.runner,
                                 workspace_id=self.wid, commit_message="test: fixture")
        self.assertFalse(plan["ok"])
        self.assertFalse(plan["validations"][0]["passed"])

    def test_cli_missing_command_plan_does_not_claim_tests_ran(self):
        # Keep Git available for the pinned-contract read, hide only python3.
        original = __import__("shutil").which
        with patch("gitlab_agent.python_runtime.shutil.which", side_effect=lambda name: None if name == "python3" else original(name)):
            code, result = self.invoke("validate", self.wid)
        self.assertEqual(code, 1)
        self.assertFalse(result["tests_executed"])
        self.assertFalse(self.bindings.root.exists())


if __name__ == "__main__":
    unittest.main()
