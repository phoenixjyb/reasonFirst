from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import MethodType

from gitlab_agent.remote_publication import (
    RemotePublicationError,
    ReviewedRemotePublisher,
)
from gitlab_agent.remote_targets import RemoteTarget


def run(*args: str, cwd: Path | None = None, capture: bool = False) -> str:
    proc = subprocess.run(
        list(args),
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
    )
    return proc.stdout.strip() if capture else ""


class ReviewedRemotePublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.origin = self.root / "origin.git"
        self.workspace_root = self.root / "worktrees"
        self.worktree = self.workspace_root / "ws"
        self.workspace_root.mkdir()

        run("git", "init", "--bare", str(self.origin))
        run("git", "clone", str(self.origin), str(self.worktree))
        run("git", "config", "user.email", "test@example.com", cwd=self.worktree)
        run("git", "config", "user.name", "Test User", cwd=self.worktree)
        (self.worktree / "a.txt").write_text("one\n", encoding="utf-8")
        run("git", "add", "a.txt", cwd=self.worktree)
        run("git", "commit", "-m", "init", cwd=self.worktree)
        run("git", "branch", "-M", "main", cwd=self.worktree)
        run("git", "push", "-u", "origin", "main", cwd=self.worktree)
        self.base_sha = run(
            "git", "rev-parse", "HEAD", cwd=self.worktree, capture=True
        )
        run("git", "switch", "-c", "chatgpt/reviewed-test", cwd=self.worktree)

        self.target = RemoteTarget(
            name="local-test",
            host="fake-host",
            repo=str(self.worktree),
            workspace_root=str(self.workspace_root),
            allowed_projects=("team/project",),
            ssh_connect_timeout=5,
        )
        self.publisher = ReviewedRemotePublisher(
            self.target,
            max_candidate_age_seconds=60,
        )

        def local_ssh_json(this, script, payload, *, timeout):
            proc = subprocess.run(
                [sys.executable, "-c", script],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            if proc.returncode != 0:
                raise RemotePublicationError(
                    f"Remote publication operation failed (exit {proc.returncode}): "
                    f"{(proc.stderr or proc.stdout)[-4000:]}"
                )
            return json.loads(proc.stdout.strip())

        self.publisher._ssh_json = MethodType(local_ssh_json, self.publisher)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _capture(self):
        return self.publisher.capture(
            project="team/project",
            worktree_path=str(self.worktree),
            base_sha=self.base_sha,
            branch="chatgpt/reviewed-test",
            expected_origin_url=str(self.origin),
        )

    def test_exact_reviewed_tree_is_committed_and_pushed(self) -> None:
        (self.worktree / "a.txt").write_text("two\n", encoding="utf-8")
        (self.worktree / "new.txt").write_text("new\n", encoding="utf-8")

        candidate = self._capture()
        self.assertIn("a.txt", candidate.changed_paths)
        self.assertIn("new.txt", candidate.changed_paths)

        result = self.publisher.commit_and_push(
            candidate,
            message="test: reviewed publication",
        )

        remote_sha = run(
            "git",
            "--git-dir",
            str(self.origin),
            "rev-parse",
            "refs/heads/chatgpt/reviewed-test",
            capture=True,
        )
        remote_tree = run(
            "git",
            "--git-dir",
            str(self.origin),
            "rev-parse",
            f"{remote_sha}^{{tree}}",
            capture=True,
        )
        self.assertEqual(result["commit_sha"], remote_sha)
        self.assertEqual(candidate.candidate_tree, remote_tree)

    def test_file_change_after_review_blocks_publication(self) -> None:
        (self.worktree / "a.txt").write_text("reviewed\n", encoding="utf-8")
        candidate = self._capture()
        (self.worktree / "a.txt").write_text("changed later\n", encoding="utf-8")

        with self.assertRaisesRegex(RemotePublicationError, "candidate tree changed"):
            self.publisher.commit_and_push(
                candidate,
                message="test: must not publish",
            )

        proc = subprocess.run(
            [
                "git",
                "--git-dir",
                str(self.origin),
                "show-ref",
                "--verify",
                "--quiet",
                "refs/heads/chatgpt/reviewed-test",
            ],
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)

    def test_origin_change_after_review_blocks_publication(self) -> None:
        (self.worktree / "a.txt").write_text("reviewed\n", encoding="utf-8")
        candidate = self._capture()
        other = self.root / "other.git"
        run("git", "init", "--bare", str(other))
        run(
            "git",
            "remote",
            "set-url",
            "origin",
            str(other),
            cwd=self.worktree,
        )

        with self.assertRaisesRegex(
            RemotePublicationError,
            "origin/push destination changed",
        ):
            self.publisher.commit_and_push(
                candidate,
                message="test: must not redirect",
            )

    def test_branch_change_after_review_blocks_publication(self) -> None:
        (self.worktree / "a.txt").write_text("reviewed\n", encoding="utf-8")
        candidate = self._capture()
        run("git", "switch", "-c", "chatgpt/other", cwd=self.worktree)

        with self.assertRaisesRegex(RemotePublicationError, "branch changed"):
            self.publisher.commit_and_push(
                candidate,
                message="test: wrong branch",
            )

    def test_candidate_digest_and_age_fail_closed(self) -> None:
        (self.worktree / "a.txt").write_text("reviewed\n", encoding="utf-8")
        candidate = self._capture()

        tampered = candidate.__class__(
            **{
                **candidate.to_dict(),
                "branch": "chatgpt/tampered",
            }
        )
        with self.assertRaisesRegex(RemotePublicationError, "digest is invalid"):
            self.publisher.commit_and_push(
                tampered,
                message="test: tampered",
            )

        stale = candidate.__class__(
            **{
                **candidate.to_dict(),
                "captured_at": int(time.time()) - 3600,
            }
        )
        # Recompute is intentionally not available through the public object;
        # a stale object with the old digest fails before publication either way.
        with self.assertRaises(RemotePublicationError):
            self.publisher.commit_and_push(
                stale,
                message="test: stale",
            )

    def test_url_rewrite_is_rejected_at_review_capture(self) -> None:
        run(
            "git",
            "config",
            "url.https://example.invalid/.insteadOf",
            str(self.origin),
            cwd=self.worktree,
        )
        with self.assertRaisesRegex(RemotePublicationError, "URL rewrite"):
            self._capture()


if __name__ == "__main__":
    unittest.main()
