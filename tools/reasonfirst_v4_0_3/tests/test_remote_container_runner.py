from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reasonfirst_codex_bridge.bridge_config import ExecutionTarget
from reasonfirst_codex_bridge.remote_workspace import (
    RemoteWorkspaceError,
    RemoteWorkspaceManager,
)


def run(*args, cwd=None):
    subprocess.run(list(args), cwd=cwd, check=True, stdout=subprocess.DEVNULL)


def main() -> None:
    old = dict(os.environ)
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        bare = root / "origin.git"
        repo = root / "repo"
        home = root / "remote-home"
        binp = root / "bin"
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
        docker = binp / "docker"
        docker.write_text(
            """#!/usr/bin/env python3
import sys
args=sys.argv[1:]
required=[
    "run","--rm","--read-only","--cap-drop=ALL",
    "--security-opt","no-new-privileges",
]
for item in required:
    if item not in args:
        print("missing:"+item,file=sys.stderr); raise SystemExit(42)
if "--network" not in args or args[args.index("--network")+1]!="none":
    print("network-not-disabled",file=sys.stderr); raise SystemExit(43)
volume=args[args.index("--volume")+1]
if not volume.endswith(":/workspace:rw"):
    print("bad-volume:"+volume,file=sys.stderr); raise SystemExit(44)
if "example/validator:1" not in args:
    print("bad-image",file=sys.stderr); raise SystemExit(45)
idx=args.index("example/validator:1")
command=args[idx+1:]
if command != ["python3","-m","pytest","-q"]:
    print("bad-command:"+repr(command),file=sys.stderr); raise SystemExit(46)
print("sandbox-ok")
"""
        )
        docker.chmod(0o755)

        os.environ["PATH"] = str(binp) + os.pathsep + old.get("PATH", "")
        os.environ["RF_FAKE_REMOTE_HOME"] = str(home)
        os.environ["RF_FAKE_CODEX"] = str(ROOT / "tests" / "fake_codex.py")

        try:
            target = ExecutionTarget(
                type="ssh",
                name="fake",
                host="fake-host",
                repo=str(repo),
                codex_backend="desktop-proxy",
                network_access=False,
                validation_engine="docker",
                validation_image="example/validator:1",
                validation_allowed_executables=("python3",),
                validation_network_access=True,
            )
            mgr = RemoteWorkspaceManager(
                target,
                allowed_executables={"python3", "pytest"},
                max_command_timeout_seconds=60,
                max_output_bytes=12000,
            )
            state = mgr.create_workspace(
                project="group/project",
                base_ref="main",
                task="container-test",
            )

            result = mgr.run_argv(
                state,
                ["python3", "-m", "pytest", "-q"],
                timeout_seconds=30,
            )
            assert result["returncode"] == 0, result
            assert result["network_access"] is False, result
            assert "sandbox-ok" in result["stdout"], result

            try:
                mgr.run_argv(state, ["bash", "-lc", "cat ~/.ssh/config"])
            except RemoteWorkspaceError as exc:
                assert "not allowed" in str(exc), exc
            else:
                raise AssertionError("disallowed executable must fail")

            try:
                mgr.run_command(state, "python3 -c 'print(1)'")
            except RemoteWorkspaceError as exc:
                assert "Arbitrary remote shell execution is disabled" in str(exc)
            else:
                raise AssertionError("legacy shell execution must stay disabled")
        finally:
            os.environ.clear()
            os.environ.update(old)

    print("remote container validation runner: OK")


if __name__ == "__main__":
    main()
