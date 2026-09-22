from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .config import AgentSettings
from .workspace import WorkspaceManager, _clip


_SECRET_NAME_FRAGMENTS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "API_KEY",
    "PRIVATE_KEY",
)


class CommandRunner:
    """Structured host command runner for builds/tests inside one worktree.

    This is *not* a filesystem/container sandbox. It limits the executable,
    working directory, inherited secrets, runtime, and captured output.
    """

    def __init__(self, settings: AgentSettings, workspaces: WorkspaceManager) -> None:
        self.settings = settings
        self.workspaces = workspaces

    def _safe_env(self, workspace_id: str) -> dict[str, str]:
        env: dict[str, str] = {}
        for key, value in os.environ.items():
            upper = key.upper()
            if any(fragment in upper for fragment in _SECRET_NAME_FRAGMENTS):
                continue
            if key in {"SSH_AUTH_SOCK", "GIT_ASKPASS", "GIT_TERMINAL_PROMPT"}:
                continue
            env[key] = value

        home = self.settings.workspace_root / "runner-home" / workspace_id
        home.mkdir(parents=True, exist_ok=True)
        env["HOME"] = str(home)
        if os.name == "nt":
            # Many Windows-native tools prefer USERPROFILE over HOME.
            env["USERPROFILE"] = str(home)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def run(
        self,
        workspace_id: str,
        argv: list[str],
        *,
        timeout_seconds: int | None = None,
    ) -> dict[str, object]:
        if not argv:
            raise ValueError("argv must not be empty")

        executable = argv[0]
        if "/" in executable or "\\" in executable:
            raise RuntimeError(
                "Executable must be a bare command name resolved from PATH, not a path"
            )
        if executable not in self.settings.allowed_executables:
            allowed = ", ".join(sorted(self.settings.allowed_executables))
            raise RuntimeError(
                f"Executable {executable!r} is not allowed. Allowed: {allowed}"
            )

        state = self.workspaces.get_state(workspace_id)
        cwd = Path(state.worktree_path).resolve()
        self.workspaces._worktree(state)  # validates containment/existence

        requested_timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else self.settings.command_timeout_seconds
        )
        timeout = max(1, min(requested_timeout, self.settings.command_timeout_seconds))

        try:
            with self.workspaces.mutation_lock(workspace_id):
                proc = subprocess.run(
                    argv,
                    cwd=cwd,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=self._safe_env(workspace_id),
                    timeout=timeout,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            out, out_truncated, out_bytes = _clip(
                str(stdout), self.settings.max_output_bytes // 2
            )
            err, err_truncated, err_bytes = _clip(
                str(stderr), self.settings.max_output_bytes // 2
            )
            return {
                "workspace_id": workspace_id,
                "argv": argv,
                "timed_out": True,
                "timeout_seconds": timeout,
                "returncode": None,
                "stdout": out,
                "stderr": err,
                "stdout_truncated": out_truncated,
                "stderr_truncated": err_truncated,
                "stdout_original_bytes": out_bytes,
                "stderr_original_bytes": err_bytes,
            }

        stdout, stdout_truncated, stdout_bytes = _clip(
            proc.stdout, self.settings.max_output_bytes // 2
        )
        stderr, stderr_truncated, stderr_bytes = _clip(
            proc.stderr, self.settings.max_output_bytes // 2
        )
        return {
            "workspace_id": workspace_id,
            "argv": argv,
            "timed_out": False,
            "timeout_seconds": timeout,
            "returncode": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "stdout_original_bytes": stdout_bytes,
            "stderr_original_bytes": stderr_bytes,
        }
