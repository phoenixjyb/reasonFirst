from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
import time
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reasonfirst_codex_bridge.bridge_config import ExecutionTarget
from reasonfirst_codex_bridge.controller import BridgeController
from reasonfirst_codex_bridge.remote_workspace import RemoteWorkspaceManager


def run(*args, cwd=None):
    subprocess.run(list(args), cwd=cwd, check=True, stdout=subprocess.DEVNULL)


def policy(*, validations=None, protected=None):
    return {
        "found": True,
        "effective": {
            "base_branch": "main",
            "preferred_agents": [],
            "validation_commands": list(validations or []),
            "protected_paths": list(protected or []),
            "instructions": [],
            "required_executables": [],
            "mr": {"target_branch": "main", "title_prefix": ""},
        },
        "warnings": [],
    }


def main() -> None:
    old = dict(os.environ)
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        bare = root / "origin.git"
        repo = root / "repo"
        home = root / "remote-home"
        binp = root / "bin"
        state_dir = root / "state"
        home.mkdir()
        binp.mkdir()

        run("git", "init", "--bare", str(bare))
        run("git", "clone", str(bare), str(repo))
        run("git", "config", "user.email", "test@example.com", cwd=repo)
        run("git", "config", "user.name", "Test", cwd=repo)
        (repo / "a.txt").write_text("one\n")
        run("git", "add", ".", cwd=repo)
        run("git", "commit", "-m", "init", cwd=repo)
        run("git", "branch", "-M", "main", cwd=repo)
        run("git", "push", "-u", "origin", "main", cwd=repo)

        (binp / "ssh").symlink_to(ROOT / "tests" / "fake_ssh.py")
        os.environ["PATH"] = str(binp) + os.pathsep + old.get("PATH", "")
        os.environ["RF_FAKE_REMOTE_HOME"] = str(home)
        os.environ["RF_FAKE_CODEX"] = str(ROOT / "tests" / "fake_codex.py")
        os.environ["RF_CODEX_BRIDGE_STATE_DIR"] = str(state_dir)
        os.environ["GITLAB_BASE_URL"] = "https://gitlab.example.test"
        os.environ["GITLAB_ALLOWED_PROJECTS"] = "g/p"
        os.environ["GITLAB_WORKSPACE_ROOT"] = str(root / "local-workspaces")

        try:
            target = ExecutionTarget(
                type="ssh",
                name="fake",
                host="fake-host",
                repo=str(repo),
                codex_backend="desktop-proxy",
            )
            mgr = RemoteWorkspaceManager(
                target,
                allowed_executables={"python3"},
            )
            ws = mgr.create_workspace(
                project="g/p",
                base_ref="main",
                task="finish-gates",
            )
            rec = {
                **ws,
                "task": "finish-gates",
                "goal": "g",
                "target": target.to_dict(),
                "kind": "ssh",
                "project_config": policy(),
                "updated_at": int(time.time()),
            }
            ctrl = BridgeController()
            ctrl._remote_manager = lambda target: mgr
            ctrl._state["workspaces"][ws["workspace_id"]] = rec
            thread_id = "thr_finish_gates"
            ctrl._state["sessions"][thread_id] = {
                "thread_id": thread_id,
                "workspace_id": ws["workspace_id"],
                "target": target.to_dict(),
                "app_key": "test",
                "pending_approvals": {},
                "approval_history": [],
                "updated_at": int(time.time()),
            }
            ctrl._save_state()

            mgr.write_file(rec, "a.txt", "two\n")

            rec["project_config"] = policy(
                validations=[{
                    "name": "required-tests",
                    "argv": ["python3", "-m", "pytest", "-q"],
                    "required": True,
                    "timeout_seconds": 30,
                }]
            )
            blocked_validation = ctrl.finish_preview(
                thread_id=thread_id,
                message="test: validation gate",
            )
            assert blocked_validation["ok"] is False, blocked_validation
            assert any(
                "required project validation" in item
                for item in blocked_validation["blockers"]
            ), blocked_validation

            rec["project_config"] = policy(protected=["a.txt"])
            blocked_protected = ctrl.finish_preview(
                thread_id=thread_id,
                message="test: protected gate",
            )
            assert blocked_protected["ok"] is False, blocked_protected
            assert blocked_protected["protected_path_changes"] == ["a.txt"]
            allowed_protected = ctrl.finish_preview(
                thread_id=thread_id,
                message="test: protected gate",
                allow_protected=True,
            )
            assert allowed_protected["ok"] is True, allowed_protected

            rec["project_config"] = policy()
            mgr.write_file(
                rec,
                "a.txt",
                "token=" + "glpat-" + ("a" * 26) + "\n",
            )
            blocked_secret = ctrl.finish_preview(
                thread_id=thread_id,
                message="test: secret gate",
            )
            assert blocked_secret["ok"] is False, blocked_secret
            assert blocked_secret["secret_scan"]["findings"], blocked_secret

            allowed_secret = ctrl.finish_preview(
                thread_id=thread_id,
                message="test: secret gate",
                allow_secret_match=True,
            )
            assert allowed_secret["ok"] is True, allowed_secret
            ctrl.close()
        finally:
            os.environ.clear()
            os.environ.update(old)

    print("remote finish shared review gates: OK")


if __name__ == "__main__":
    main()
