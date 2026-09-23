from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
from pathlib import Path
import unittest

from gitlab_agent.locking import WorkspaceBusyError, file_lock


class WorkspaceLockTests(unittest.TestCase):
    def test_same_thread_lock_is_reentrant(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "locks" / "workspace.lock"
            with file_lock(path, timeout_seconds=0.5):
                with file_lock(path, timeout_seconds=0.5):
                    self.assertTrue(path.exists())

    def test_other_thread_times_out(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "workspace.lock"
            results: list[str] = []
            with file_lock(path, timeout_seconds=0.5):
                def contend() -> None:
                    try:
                        with file_lock(path, timeout_seconds=0.1):
                            results.append("acquired")
                    except WorkspaceBusyError:
                        results.append("busy")

                worker = threading.Thread(target=contend)
                worker.start()
                worker.join(timeout=2)
                self.assertFalse(worker.is_alive())
            self.assertEqual(results, ["busy"])

    def test_other_process_cannot_take_live_lock_and_can_after_release(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "workspace.lock"
            script = (
                "from pathlib import Path\n"
                "from gitlab_agent.locking import file_lock, WorkspaceBusyError\n"
                "import sys\n"
                "try:\n"
                "  with file_lock(Path(sys.argv[1]), timeout_seconds=0.15):\n"
                "    print('ACQUIRED')\n"
                "except WorkspaceBusyError:\n"
                "  print('BUSY')\n"
            )
            with file_lock(path, timeout_seconds=0.5):
                blocked = subprocess.run(
                    [sys.executable, "-c", script, str(path)],
                    text=True,
                    capture_output=True,
                    check=True,
                    timeout=10,
                )
                self.assertEqual(blocked.stdout.strip(), "BUSY")

            acquired = subprocess.run(
                [sys.executable, "-c", script, str(path)],
                text=True,
                capture_output=True,
                check=True,
                timeout=10,
            )
            self.assertEqual(acquired.stdout.strip(), "ACQUIRED")


if __name__ == "__main__":
    unittest.main()
