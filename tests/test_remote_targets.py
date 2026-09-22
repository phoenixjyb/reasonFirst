from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gitlab_agent.remote_targets import (
    RemoteTargetError,
    load_remote_targets,
    resolve_remote_target,
    safe_target_summary,
)


class RemoteTargetRegistryTests(unittest.TestCase):
    def _write(self, root: Path, text: str) -> Path:
        path = root / "targets.yaml"
        path.write_text(text, encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o600)
        return path

    def test_valid_named_target_is_project_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = self._write(
                Path(td),
                """
version: 1
targets:
  gpu-a:
    type: ssh
    host: gpu-a
    repo: /srv/recomo/app
    workspace_root: /srv/reasonfirst/worktrees
    allowed_projects:
      - recomo/app
      - phoenixjyb/cloud-pipeline
    ssh_connect_timeout: 10
""",
            )
            targets = load_remote_targets(path)

        target = resolve_remote_target(
            "gpu-a",
            project="recomo/app",
            targets=targets,
        )
        self.assertEqual(target.host, "gpu-a")
        self.assertEqual(target.repo, "/srv/recomo/app")
        self.assertEqual(target.workspace_root, "/srv/reasonfirst/worktrees")
        self.assertEqual(target.ssh_connect_timeout, 10)

        with self.assertRaisesRegex(RemoteTargetError, "not allowlisted"):
            resolve_remote_target(
                "gpu-a",
                project="other/project",
                targets=targets,
            )

    def test_unknown_target_does_not_accept_inline_destination(self) -> None:
        with self.assertRaisesRegex(RemoteTargetError, "Unknown SSH target"):
            resolve_remote_target(
                "host:/srv/project",
                project="team/project",
                targets={},
            )

    def test_missing_config_grants_no_targets(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "missing.yaml"
            self.assertEqual(load_remote_targets(missing), {})

    def test_rejects_unsafe_host_repo_and_empty_project_grant(self) -> None:
        cases = [
            """
version: 1
targets:
  bad:
    host: --proxy-command
    repo: /srv/repo
    workspace_root: /srv/reasonfirst/worktrees
    allowed_projects: [team/project]
""",
            """
version: 1
targets:
  bad:
    host: gpu
    repo: ../repo
    allowed_projects: [team/project]
""",
            """
version: 1
targets:
  bad:
    host: gpu
    repo: /srv/repo
    workspace_root: /srv/reasonfirst/worktrees
    allowed_projects: []
""",
        ]
        for text in cases:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as td:
                path = self._write(Path(td), text)
                with self.assertRaises(RemoteTargetError):
                    load_remote_targets(path)

    def test_duplicate_yaml_keys_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = self._write(
                Path(td),
                """
version: 1
targets:
  gpu:
    host: one
    host: two
    repo: /srv/repo
    workspace_root: /srv/reasonfirst/worktrees
    allowed_projects: [team/project]
""",
            )
            with self.assertRaisesRegex(RemoteTargetError, "Duplicate YAML"):
                load_remote_targets(path)

    def test_group_or_other_writable_config_is_rejected(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX mode test")
        with tempfile.TemporaryDirectory() as td:
            path = self._write(
                Path(td),
                """
version: 1
targets:
  gpu:
    host: gpu
    repo: /srv/repo
    workspace_root: /srv/reasonfirst/worktrees
    allowed_projects: [team/project]
""",
            )
            path.chmod(0o666)
            with self.assertRaisesRegex(RemoteTargetError, "writable by group/others"):
                load_remote_targets(path)

    def test_env_selects_user_owned_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = self._write(
                Path(td),
                """
version: 1
targets:
  gpu:
    host: gpu
    repo: /srv/repo
    workspace_root: /srv/reasonfirst/worktrees
    allowed_projects: [team/project]
""",
            )
            with patch.dict(
                os.environ,
                {"REASONFIRST_TARGETS_FILE": str(path)},
                clear=False,
            ):
                summary = safe_target_summary()

        self.assertEqual(summary[0]["name"], "gpu")
        self.assertEqual(summary[0]["allowed_projects"], ["team/project"])


if __name__ == "__main__":
    unittest.main()
