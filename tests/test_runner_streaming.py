"""Real command processes; temporary state only, no coding provider or GitLab."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import os
from pathlib import Path
import sys
import tempfile
import time
import tracemalloc
import unittest
from unittest.mock import patch

from gitlab_agent import validation_process
from gitlab_agent.finish import run_project_validations

from gitlab_agent.python_runtime import PythonBindings
from gitlab_agent.runner import CommandRunner
from gitlab_agent.workspace import WorkspaceManager, WorkspaceState
from test_python_runtime import settings_for


class RunnerStreamingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rf-runner-stream-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = replace(settings_for(self.root), max_output_bytes=1024)
        self.manager = WorkspaceManager(self.settings)
        self.wid = "123456abcdef"
        self.worktree = self.manager.worktrees_dir / self.wid
        self.worktree.mkdir()
        self.manager._save_state(WorkspaceState(
            workspace_id=self.wid, project="team/project", base_ref="main",
            base_sha="a" * 40, repo_path=str(self.root / "cache.git"),
            worktree_path=str(self.worktree), branch="chatgpt/synthetic", created_at="fixture",
        ))
        bindings = PythonBindings(self.manager)
        bindings.bind(bindings.plan(self.wid, "python3", sys.executable), approved=True)
        self.runner = CommandRunner(self.settings, self.manager)

    def run_code(self, code, timeout=10):
        # These fixtures use only the standard library. Exclude installed site
        # startup hooks so output/deadline assertions measure the command itself.
        return self.runner.run(self.wid, ["python3", "-I", "-S", "-c", code], timeout_seconds=timeout)

    def test_large_dual_stream_output_is_bounded_during_capture(self):
        # The old subprocess.run/communicate path retained all 16 MiB before
        # clipping. Measure parent Python allocations, not the child's RSS.
        code = (
            "import os\n"
            "for _ in range(1024):\n"
            " os.write(1, b'a'*8192)\n"
            " os.write(2, b'b'*8192)\n"
        )
        tracemalloc.start()
        try:
            result = self.run_code(code)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["stdout"], "a" * 512)
        self.assertEqual(result["stderr"], "b" * 512)
        self.assertEqual(result["stdout_original_bytes"], 8 * 1024 * 1024)
        self.assertEqual(result["stderr_original_bytes"], 8 * 1024 * 1024)
        self.assertLess(peak, 4 * 1024 * 1024, f"parent capture peak was {peak} bytes")

    @unittest.skipUnless(os.name == "posix", "POSIX owned-process-group cleanup")
    def test_timeout_stops_pipe_holding_grandchild(self):
        marker = self.worktree / "descendant-finished"
        child = f"import time,pathlib;print('grandchild ready',flush=True);time.sleep(2);pathlib.Path({str(marker)!r}).write_text('still running')"
        result = self.run_code(
            "import subprocess,sys,time\n"
            f"subprocess.Popen([sys.executable, '-I', '-S', '-c', {child!r}])\n"
            "print('child launched', flush=True)\n"
            "time.sleep(3)\n", timeout=1,
        )
        self.assertTrue(result["timed_out"])
        self.assertIsNone(result["returncode"])
        self.assertIn("grandchild ready", result["stdout"])
        deadline = time.monotonic() + 4
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(marker.exists(), "descendant kept executing after validation timeout")

    def test_normal_exit_keeps_requested_argv_binding_and_counts(self):
        result = self.run_code("import sys;print('ok');print('detail', file=sys.stderr)")
        self.assertEqual(result["argv"][0], "python3")
        self.assertEqual(result["execution"]["resolved_argv"][0], sys.executable)
        self.assertEqual(result["stdout"], "ok\n")
        self.assertEqual(result["stderr"], "detail\n")
        self.assertTrue(result["output_complete"])
        self.assertTrue(result["cleanup"]["direct_child_reaped"])
        self.assertTrue(result["cleanup"]["output_pipes_closed"])
        self.assertFalse(result["cleanup"]["termination_requested"])
        self.assertFalse(result["cleanup"]["descendants_verified"])

    def test_truncation_does_not_hide_nonzero_exit(self):
        result = self.run_code("import sys;print('x'*10000);sys.exit(7)")
        self.assertEqual(result["returncode"], 7)
        self.assertFalse(result["timed_out"])
        self.assertTrue(result["stdout_truncated"])
        self.assertEqual(result["stdout_original_bytes"], 10001)
        self.assertTrue(result["output_complete"])

    def test_zero_and_odd_budgets(self):
        for budget in (0, 1, 5):
            with self.subTest(budget=budget):
                self.runner.settings = replace(self.settings, max_output_bytes=budget)
                result = self.run_code("import os;os.write(1,b'abcdef');os.write(2,b'uvwxyz')")
                self.assertEqual(result["stdout"], "abcdef"[:budget // 2])
                self.assertEqual(result["stderr"], "uvwxyz"[:budget // 2])
                self.assertEqual(result["stdout_original_bytes"], 6)
                self.assertTrue(result["stdout_truncated"])

    def test_invalid_utf8_and_newlines_are_counted_as_normalized_utf8(self):
        result = self.run_code("import os;os.write(1,b'a\\r\\nb\\rc\\xff')")
        self.assertEqual(result["stdout"], "a\nb\nc\ufffd")
        self.assertEqual(result["stdout_original_bytes"], 8)
        self.assertTrue(result["output_complete"])

    def test_timeout_preserves_bounded_partial_output(self):
        start = time.monotonic()
        result = self.run_code("import sys,time;print('partial',flush=True);print('error',file=sys.stderr,flush=True);time.sleep(3)", timeout=1)
        self.assertTrue(result["timed_out"])
        self.assertIsNone(result["returncode"])
        self.assertEqual(result["stdout"], "partial\n")
        self.assertEqual(result["stderr"], "error\n")
        self.assertLess(time.monotonic() - start, 4)
        self.assertTrue(result["cleanup"]["termination_requested"])
        self.assertTrue(result["cleanup"]["direct_child_reaped"])

    def test_timeout_still_applies_after_both_output_streams_close(self):
        start = time.monotonic()
        result = self.run_code("import os,time;os.close(1);os.close(2);time.sleep(3)", timeout=1)
        self.assertTrue(result["timed_out"])
        self.assertIsNone(result["returncode"])
        self.assertTrue(result["output_complete"])
        self.assertLess(time.monotonic() - start, 4)

    @unittest.skipUnless(os.name == "posix", "POSIX inherited output pipe")
    def test_parent_exit_does_not_remove_deadline_or_owned_group(self):
        child = "import time;time.sleep(3)"
        start = time.monotonic()
        result = self.run_code("import subprocess,sys\n" + f"subprocess.Popen([sys.executable,'-I','-S','-c',{child!r}])\n" + "print('parent exits',flush=True)", timeout=1)
        self.assertTrue(result["timed_out"])
        self.assertIsNone(result["returncode"])
        self.assertIn("parent exits", result["stdout"])
        self.assertTrue(result["output_complete"])
        self.assertLess(time.monotonic() - start, 4)

    def test_required_timeout_cannot_pass_finish_validation(self):
        results, blocked = run_project_validations(
            self.settings, self.runner, self.wid, {"validation_commands": [{
                "name": "synthetic-timeout", "required": True, "timeout_seconds": 1,
                "argv": ["python3", "-I", "-S", "-c", "import time;time.sleep(3)"],
            }]},
        )
        self.assertTrue(blocked)
        self.assertFalse(results[0]["passed"])
        self.assertTrue(results[0]["result"]["timed_out"])

    def test_workspace_lock_covers_termination_and_output_closure(self):
        original_lock = self.manager.mutation_lock
        terminate = validation_process._terminate
        close = validation_process._PipeReader.close
        depth = 0
        observations = []
        @contextmanager
        def locked(*args, **kwargs):
            nonlocal depth
            with original_lock(*args, **kwargs):
                depth += 1
                try:
                    yield
                finally:
                    depth -= 1
        def terminating(*args, **kwargs):
            observations.append(("terminate", depth))
            return terminate(*args, **kwargs)
        def closing(reader):
            observations.append(("close", depth))
            return close(reader)
        with patch.object(self.manager, "mutation_lock", locked), \
                patch.object(validation_process, "_terminate", terminating), \
                patch.object(validation_process._PipeReader, "close", closing):
            result = self.run_code("import time;time.sleep(3)", timeout=1)
        self.assertTrue(result["timed_out"])
        self.assertEqual([name for name, _ in observations], ["terminate", "close"])
        self.assertTrue(all(observed > 0 for _, observed in observations))
        self.assertEqual(depth, 0)

    def test_output_flood_does_not_disable_deadline(self):
        start = time.monotonic()
        result = self.run_code("import os\nwhile True:\n os.write(1,b'x'*4096)\n os.write(2,b'y'*4096)", timeout=1)
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["stdout"], "x" * 512)
        self.assertEqual(result["stderr"], "y" * 512)
        self.assertGreater(result["stdout_original_bytes"], 512)
        self.assertGreater(result["stderr_original_bytes"], 512)
        self.assertLess(time.monotonic() - start, 4)


if __name__ == "__main__":
    unittest.main()
