from __future__ import annotations

import subprocess
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reasonfirst_codex_bridge.bridge_config import ExecutionTarget
from reasonfirst_codex_bridge.remote_workspace import RemoteWorkspaceError, RemoteWorkspaceManager


class FakeRemoteManager(RemoteWorkspaceManager):
    def __init__(self, *, origin: str) -> None:
        target = ExecutionTarget(type="ssh", host="fake", repo="/repo", codex_backend="remote-ssh")
        super().__init__(
            target,
            gitlab_host="gitlab.example.com",
            git_username="user@example.com",
            git_password="secret-value",
        )
        self.origin = origin
        self.commands: list[str] = []

    def _ssh(self, command: str, *, timeout: int = 120, check: bool = True):  # type: ignore[override]
        self.commands.append(command)
        if "remote get-url origin" in command:
            return subprocess.CompletedProcess([], 0, self.origin + "\n", "")
        if "fetch --prune origin" in command:
            if "GIT_ASKPASS" in command:
                return subprocess.CompletedProcess([], 0, "", "")
            return subprocess.CompletedProcess([], 1, "", "fatal: could not read Username")
        if "worktree add" in command:
            return subprocess.CompletedProcess(
                [],
                0,
                '{"workspace_id":"ssh-abc","worktree_path":"/tmp/wt","base_sha":"deadbeef","branch":"chatgpt/t-abc","origin_url":"%s"}\n' % self.origin,
                "",
            )
        raise AssertionError(command)


def main() -> None:
    mgr = FakeRemoteManager(origin="https://gitlab.example.com/example/project.git")
    state = mgr.create_workspace(project="example/project", base_ref="main", task="test")
    assert state["git_auth_forwarded"] is True
    assert any("GIT_ASKPASS" in cmd for cmd in mgr.commands)
    assert "secret-value" not in str(state)

    other = FakeRemoteManager(origin="https://evil.example/example/project.git")
    try:
        other.create_workspace(project="example/project", base_ref="main", task="test")
    except RemoteWorkspaceError:
        pass
    else:
        raise AssertionError("credential host guard did not fail closed")
    assert not any("GIT_ASKPASS" in cmd for cmd in other.commands)
    print("remote Git credential forwarding guard: OK")


if __name__ == "__main__":
    main()
