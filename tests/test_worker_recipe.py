from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
import venv

import test_python_runtime as fixtures
from gitlab_agent.python_runtime import handoff_runtime_guidance, probe_python
from gitlab_agent.worker_recipe import build_worker_recipe


class WorkerRecipeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PythonRuntimeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.binding = self.fixture.bind()
        self.cwd = self.fixture.worktree

    def recipe(self, args=None, *, timeout=10, output_limit=120000, binding=None):
        binding = binding or self.binding
        args = args if args is not None else ["-c", "print('executed')"]
        resolution = {"requested_argv": ["python3", *args],
                      "resolved_argv": [binding["runtime"]["executable"], *args],
                      "python_binding": binding}
        return build_worker_recipe(resolution, timeout_seconds=timeout,
                                   output_limit_bytes=output_limit)

    def invoke(self, recipe, *, cwd=None, env=None):
        process = subprocess.run(
            [recipe["payload"]["runtime"]["executable"], "-I", "-S", "-B", "-"],
            input=recipe["source"].encode("ascii"), capture_output=True,
            cwd=cwd or self.cwd, env=env, timeout=20,
        )
        self.assertTrue(process.stdout, process.stderr)
        return process.returncode, json.loads(process.stdout)

    def test_real_success_records_child_not_test_semantics(self):
        code, report = self.invoke(self.recipe())
        self.assertEqual(code, 0, report)
        self.assertTrue(report["identity_verified"])
        self.assertTrue(report["child_started"])
        self.assertTrue(report["command_succeeded"])
        self.assertTrue(report["output_complete"])
        self.assertEqual(report["stdout"].strip(), "executed")
        self.assertEqual(report["child_returncode"], 0)
        self.assertNotIn("passed", report)
        self.assertEqual(list(self.cwd.iterdir()), [])

    def test_real_failure_nonzero_with_complete_output(self):
        code, report = self.invoke(self.recipe(["-c", "import sys;print('failure');sys.exit(7)"]))
        self.assertEqual(code, 7)
        self.assertEqual(report["child_returncode"], 7)
        self.assertTrue(report["output_complete"])
        self.assertFalse(report["command_succeeded"])

    def test_real_timeout_cannot_be_success(self):
        code, report = self.invoke(self.recipe(["-c", "import time;time.sleep(4)"], timeout=1))
        self.assertEqual(code, 124)
        self.assertTrue(report["timed_out"])
        self.assertFalse(report["command_succeeded"])

    def test_missing_or_changed_arguments_fail_before_child(self):
        for argv in ([sys.executable], [sys.executable, "-V"]):
            recipe = self.recipe()
            payload = copy.deepcopy(recipe["payload"])
            payload["resolved_argv"] = argv
            recipe["source"] = "RECIPE_JSON = " + repr(json.dumps(payload)) + "\n" + recipe["source"].split("\n", 1)[1]
            code, report = self.invoke(recipe)
            self.assertNotEqual(code, 0)
            self.assertFalse(report["child_started"])

    def test_wrong_version_or_fingerprint_refuses_child(self):
        for part in ("version", "fingerprint"):
            binding = copy.deepcopy(self.binding)
            if part == "version":
                binding["runtime"][part] = [3, 99, 0]
            else:
                binding["runtime"][part]["sha256"] = "0" * 64
            code, report = self.invoke(self.recipe(binding=binding))
            self.assertEqual(code, 2)
            self.assertFalse(report["child_started"])

    def test_wrong_worktree_refuses_child(self):
        code, report = self.invoke(self.recipe(), cwd=self.fixture.root)
        self.assertEqual(code, 2)
        self.assertFalse(report["child_started"])

    def test_output_limit_does_not_claim_complete_success(self):
        code, report = self.invoke(self.recipe(["-c", "print('x'*20000)"], output_limit=64))
        self.assertNotEqual(code, 0)
        self.assertLessEqual(len(report["stdout"].encode()), 64)
        self.assertFalse(report["output_complete"])
        self.assertFalse(report["command_succeeded"])

    def test_invalid_utf8_is_diagnostic_not_success(self):
        code, report = self.invoke(self.recipe(["-c", "import sys;sys.stdout.buffer.write(bytes([255]))"]))
        self.assertNotEqual(code, 0)
        self.assertFalse(report["output_complete"])
        self.assertFalse(report["command_succeeded"])

    def test_zero_output_zero_exit_is_not_test_pass(self):
        code, report = self.invoke(self.recipe(["-c", "pass"]))
        self.assertEqual(code, 0)
        self.assertTrue(report["command_succeeded"])
        self.assertNotIn("test_count", report)
        self.assertNotIn("tests_passed", report)
        self.assertIn("not-session-exit", report["test_semantics"])

    def test_exact_arbitrary_arguments_and_child_stdin(self):
        args = ["", "space here", "中文", "a\"b", "apostrophe'", "trailing\\",
                "$HOME;$(bad)", "\n'@\ninvalid shell command\n", "RF_WORKER_PYTHON"]
        recipe = self.recipe(["-c", "import sys,json;print(json.dumps([sys.argv[1:],sys.stdin.read()]))", *args])
        code, report = self.invoke(recipe)
        self.assertEqual(code, 0, report)
        self.assertEqual(json.loads(report["stdout"]), [args, ""])
        self.assertTrue(recipe["source"].isascii())
        self.assertEqual(report["resolved_argv"][1:], recipe["payload"]["requested_argv"][1:])

    def test_child_only_environment_without_parent_mutation(self):
        with patch.dict(os.environ, {"EXAMPLE_TOKEN": "fixture", "PYTHONDONTWRITEBYTECODE": "0"}):
            code, report = self.invoke(self.recipe([
                "-c", "import os,json;print(json.dumps([os.getenv('EXAMPLE_TOKEN'),os.getenv('PYTHONDONTWRITEBYTECODE')]))",
            ]))
            self.assertEqual(os.environ["EXAMPLE_TOKEN"], "fixture")
            self.assertEqual(os.environ["PYTHONDONTWRITEBYTECODE"], "0")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(report["stdout"]), [None, "1"])

    def test_real_unittest_suite_is_not_hardcoded_to_ten(self):
        tests = self.cwd / "tests"
        tests.mkdir()
        (tests / "test_small.py").write_text(
            "import unittest\nclass Small(unittest.TestCase):\n    def test_one(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        code, report = self.invoke(self.recipe(["-m", "unittest", "discover", "-s", "tests", "-v"]))
        self.assertEqual(code, 0, report)
        self.assertIn("Ran 1 test", report["stderr"])
        self.assertTrue(report["stderr"].strip().endswith("OK"))
        self.assertFalse((tests / "__pycache__").exists())

    def test_handoff_contains_recipe_without_state_writes_or_discovery(self):
        context = {"validation_commands": [{"name": "unit-tests", "argv": ["python3", "-V"],
                                            "timeout_seconds": 999}]}
        before = self.fixture.manager._state_path(self.fixture.wid).read_bytes()
        with patch("gitlab_agent.python_runtime.discover_python") as discovery:
            text, evidence = handoff_runtime_guidance(self.fixture.manager, self.fixture.wid, context)
        discovery.assert_not_called()
        self.assertIn("Worker-side execution recipes", text)
        self.assertIn("do not", text.lower())
        self.assertIn('"timeout_seconds":30', text.replace('\\"', '"'))
        self.assertEqual(evidence[0]["python_binding"], self.binding)
        self.assertEqual(self.fixture.manager._state_path(self.fixture.wid).read_bytes(), before)
        self.assertEqual(list(self.cwd.iterdir()), [])

    def test_absent_binding_has_no_recipe(self):
        other_id = self.fixture.wid
        self.fixture.bindings._path(other_id, "python3").unlink()
        text, evidence = handoff_runtime_guidance(self.fixture.manager, other_id,
            {"validation_commands": [{"name": "unit-tests", "argv": ["python3", "-V"]}]})
        self.assertEqual(evidence, [])
        self.assertNotIn("Worker-side execution recipes", text)

    def test_launch_error_is_explicit_and_never_success(self):
        from gitlab_agent._worker_python_recipe import execute_recipe
        with patch("gitlab_agent._worker_python_recipe._verify"), patch(
            "gitlab_agent._worker_python_recipe.subprocess.Popen", side_effect=OSError("fixture")
        ):
            report, code = execute_recipe(self.recipe()["payload"])
        self.assertEqual(code, 126)
        self.assertFalse(report["child_started"])
        self.assertFalse(report["command_succeeded"])
        self.assertEqual(report["error"], "child_launch_failed")

    def test_multiple_commands_share_supervisor_and_select_exact_index(self):
        context = {"validation_commands": [
            {"name": "one", "argv": ["python3", "-c", "print('one')"]},
            {"name": "two", "argv": ["python3", "-c", "print('two')"]},
        ]}
        text, _ = handoff_runtime_guidance(self.fixture.manager, self.fixture.wid, context)
        self.assertEqual(text.count("def execute_recipe(payload):"), 1)
        self.assertIn('"1": "two"', text)
        start = text.index("RECIPE_JSON = ")
        end = text.index("'@\n", start) if os.name == "nt" else text.index("RF_WORKER_PYTHON\n", start)
        source = text[start:end]
        for index, expected in (("1", "two"), ("0", "one"), ("99", None), ("-1", None)):
            process = subprocess.run([sys.executable, "-I", "-S", "-B", "-", index],
                input=source.encode("ascii"), cwd=self.cwd, capture_output=True, timeout=20)
            report = json.loads(process.stdout)
            if expected is None:
                self.assertNotEqual(process.returncode, 0)
                self.assertFalse(report["child_started"])
            else:
                self.assertEqual(process.returncode, 0, report)
                self.assertEqual(report["stdout"].strip(), expected)

    def test_windows_script_has_no_modern_dotnet_or_global_changes(self):
        binding = copy.deepcopy(self.binding)
        binding["identity"]["platform"] = "win32"
        recipe = self.recipe(binding=binding)
        self.assertEqual(recipe["shell"], "powershell-5.1-or-newer")
        self.assertIn("$ErrorActionPreference = 'Stop'", recipe["script"])
        for forbidden in ("ProcessStartInfo", "ArgumentList", "ExecutionPolicy", "Start-Process", "actual-coder"):
            self.assertNotIn(forbidden, recipe["script"])

    @unittest.skipIf(os.name == "nt", "POSIX shell test runs on macOS/Linux")
    def test_native_posix_shell_preserves_special_arguments(self):
        recipe = self.recipe(["-c", "import sys;print(repr(sys.argv[1:]))", "", "quote' space", "中文"])
        process = subprocess.run(["sh", "-c", recipe["script"]], cwd=self.cwd,
                                 capture_output=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertIn("quote' space", report["stdout"])
        self.assertIn("中文", report["stdout"])

    def powershell_cases(self, shell_name, expected_major=None):
        shell = shutil.which(shell_name)
        self.assertIsNotNone(shell, "Native Windows shell required by this regression")
        if expected_major is not None:
            version = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command",
                                      "$PSVersionTable.PSVersion.Major"], capture_output=True, check=True)
            self.assertEqual(version.stdout.strip(), str(expected_major).encode())
        envdir = self.fixture.root / "Python 空间's env"
        venv.EnvBuilder(with_pip=False).create(envdir)
        binding = copy.deepcopy(self.binding)
        binding["runtime"] = probe_python(str(envdir / "Scripts/python.exe"))
        args = ["", "quote\"", "apostrophe'", "trailing\\", "中文", "$HOME;$(bad)"]
        recipe = self.recipe(["-c", "import sys,json;print(json.dumps(sys.argv[1:]))", *args], binding=binding)
        process = subprocess.run([shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", recipe["script"]],
                                 cwd=self.cwd, capture_output=True, timeout=25)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(json.loads(report["stdout"]), args)
        failure = self.recipe(["-c", "raise SystemExit(7)"], binding=binding)
        process = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", failure["script"]],
                                 cwd=self.cwd, capture_output=True, timeout=25)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(json.loads(process.stdout)["child_returncode"], 7)

    @unittest.skipUnless(os.name == "nt", "Native Windows PowerShell 5.1 regression")
    def test_native_windows_powershell_51(self):
        self.powershell_cases("powershell.exe", 5)

    @unittest.skipUnless(os.name == "nt", "Native Windows PowerShell 7 regression")
    def test_native_windows_powershell_7(self):
        self.powershell_cases("pwsh.exe", 7)


if __name__ == "__main__":
    unittest.main()
