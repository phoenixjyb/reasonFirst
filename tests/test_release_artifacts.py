from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.check_release_artifacts import inspect


class ReleaseArtifactInspectionTests(unittest.TestCase):
    def test_clean_wheel_passes(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "pkg-0.5.0-py3-none-any.whl"
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("gitlab_agent/__init__.py", "__version__ = '0.5.0'\n")
                zf.writestr("pkg-0.5.0.dist-info/METADATA", "Name: pkg\n")
            self.assertEqual(inspect(path), [])

    def test_runtime_and_secret_named_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "pkg-0.5.0.tar.gz"
            with tarfile.open(path, "w:gz") as tf:
                for name in [
                    "pkg-0.5.0/src/gitlab_agent/__init__.py",
                    "pkg-0.5.0/.env",
                    "pkg-0.5.0/worktrees/task/file.py",
                    "pkg-0.5.0/debug.log",
                ]:
                    data = b"x"
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    tf.addfile(info, io.BytesIO(data))
            problems = inspect(path)
            self.assertTrue(any(".env" in item for item in problems))
            self.assertTrue(any("worktrees" in item for item in problems))
            self.assertTrue(any("debug.log" in item for item in problems))


if __name__ == "__main__":
    unittest.main()
