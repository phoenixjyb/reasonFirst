from __future__ import annotations

import json
from pathlib import PurePosixPath
import shlex
import subprocess
from typing import Any, Callable

from .project_config import ProjectConfigResult
from .remote_targets import RemoteTarget


class RemoteValidationError(RuntimeError):
    pass


_REMOTE_VALIDATION_SCRIPT = r'''
import json
import os
import pathlib
import subprocess
import sys
import time

payload = json.loads(sys.stdin.read())
root = pathlib.Path(payload["workspace_root"]).resolve()
cwd = pathlib.Path(payload["worktree"]).resolve()
try:
    cwd.relative_to(root)
except ValueError:
    raise SystemExit("managed worktree is outside configured workspace_root")
if not cwd.is_dir():
    raise SystemExit("managed worktree does not exist")

top = subprocess.check_output(
    ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
    text=True,
).strip()
if pathlib.Path(top).resolve() != cwd:
    raise SystemExit("managed worktree path is not the Git worktree root")

argv = payload["argv"]
if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
    raise SystemExit("invalid argv")

timeout = int(payload["timeout_seconds"])
env = os.environ.copy()
for key in list(env):
    upper = key.upper()
    if (
        "TOKEN" in upper
        or "PASSWORD" in upper
        or "SECRET" in upper
        or upper in {"SSH_AUTH_SOCK", "GIT_ASKPASS", "GIT_SSH_COMMAND"}
    ):
        env.pop(key, None)
env["GIT_TERMINAL_PROMPT"] = "0"

started = time.monotonic()
try:
    proc = subprocess.run(
        argv,
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        shell=False,
    )
    result = {
        "returncode": proc.returncode,
        "timed_out": False,
        "stdout": proc.stdout[-60000:],
        "stderr": proc.stderr[-60000:],
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
except subprocess.TimeoutExpired as exc:
    stdout = exc.stdout or ""
    stderr = exc.stderr or ""
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    result = {
        "returncode": None,
        "timed_out": True,
        "stdout": str(stdout)[-60000:],
        "stderr": str(stderr)[-60000:],
        "duration_ms": int((time.monotonic() - started) * 1000),
    }

print(json.dumps(result))
'''


def _safe_remote_worktree(path: str) -> str:
    value = str(path or "").strip()
    if not value or "\n" in value or "\r" in value or "\x00" in value:
        raise RemoteValidationError("Remote worktree path is invalid")
    parsed = PurePosixPath(value)
    if not parsed.is_absolute() or ".." in parsed.parts:
        raise RemoteValidationError("Remote worktree path must be an absolute safe POSIX path")
    return value


def _validation_by_name(
    project_config: ProjectConfigResult,
    name: str,
) -> dict[str, object]:
    if not project_config.valid:
        raise RemoteValidationError(
            "Project contract is invalid; remote validation is blocked"
        )
    requested = str(name or "").strip()
    if not requested:
        raise RemoteValidationError("Validation name is required")

    commands = project_config.effective.get("validation_commands", [])
    if not isinstance(commands, list):
        raise RemoteValidationError("Project contract has no validation command list")
    matches = [
        item
        for item in commands
        if isinstance(item, dict) and str(item.get("name") or "") == requested
    ]
    if len(matches) != 1:
        raise RemoteValidationError(
            f"Validation {requested!r} is not an exact unique command in .actualcoder.yaml"
        )
    command = matches[0]
    argv = command.get("argv")
    if not isinstance(argv, list) or not argv:
        raise RemoteValidationError(f"Validation {requested!r} has invalid argv")
    return command


class RemoteValidationRunner:
    """Run only repository-approved validation argv on a named SSH target.

    This removes arbitrary agent-provided shell strings. It is still not a
    process/container sandbox: validation commands execute repository code on the
    user-granted target and should be treated accordingly.
    """

    def __init__(
        self,
        target: RemoteTarget,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.target = target
        self._runner = runner or subprocess.run

    def run(
        self,
        *,
        project: str,
        worktree_path: str,
        project_config: ProjectConfigResult,
        validation_name: str,
    ) -> dict[str, object]:
        self.target.assert_project_allowed(project)
        worktree = _safe_remote_worktree(worktree_path)
        command = _validation_by_name(project_config, validation_name)
        argv = [str(item) for item in command["argv"]]
        timeout_seconds = int(command.get("timeout_seconds") or 300)

        payload = json.dumps(
            {
                "workspace_root": self.target.workspace_root,
                "worktree": worktree,
                "argv": argv,
                "timeout_seconds": timeout_seconds,
            },
            ensure_ascii=False,
        )
        remote_command = "python3 -c " + shlex.quote(_REMOTE_VALIDATION_SCRIPT)
        ssh_argv = [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={self.target.ssh_connect_timeout}",
            "--",
            self.target.host,
            remote_command,
        ]

        try:
            proc = self._runner(
                ssh_argv,
                input=payload,
                text=True,
                capture_output=True,
                timeout=timeout_seconds + 30,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RemoteValidationError(
                f"SSH validation transport timed out for target {self.target.name!r}"
            ) from exc
        except OSError as exc:
            raise RemoteValidationError(
                f"Could not launch SSH validation transport for target {self.target.name!r}"
            ) from exc

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "remote validation transport failed")[-3000:]
            raise RemoteValidationError(
                f"Remote validation transport failed (exit {proc.returncode}): {detail}"
            )

        try:
            result = json.loads(proc.stdout.strip())
        except json.JSONDecodeError as exc:
            raise RemoteValidationError(
                "Remote validation returned invalid JSON"
            ) from exc
        if not isinstance(result, dict):
            raise RemoteValidationError("Remote validation returned a non-object result")

        return {
            "target": self.target.name,
            "project": project,
            "validation": validation_name,
            "argv": argv,
            "required": bool(command.get("required", True)),
            "timeout_seconds": timeout_seconds,
            **result,
        }
