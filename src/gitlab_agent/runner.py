from __future__ import annotations

import os
from pathlib import Path

from .config import AgentSettings
from .workspace import WorkspaceManager
from .python_runtime import PythonBindings
from .validation_process import run_bounded_command


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
        self.child_context = getattr(workspaces, "child_context", None)

    def _safe_env(self, workspace_id: str) -> dict[str, str]:
        env: dict[str, str] = {}
        retained = (self.child_context.environment_copy()
                    if self.child_context is not None else os.environ)
        for key, value in retained.items():
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
                resolution = PythonBindings(self.workspaces).resolve(workspace_id, argv)
                effective_argv = resolution["resolved_argv"]
                if self.child_context is not None:
                    # An approved Python path stays the selected invocation;
                    # all other allowlisted commands resolve only from retained
                    # launch inputs. Never rely on Windows parent PATH lookup.
                    absolute = self.child_context.resolve_executable(effective_argv[0], cwd=cwd)
                    effective_argv = [absolute, *effective_argv[1:]]
                    resolution = {**resolution, "resolved_argv": effective_argv}
                env = self._safe_env(workspace_id)
                encoding = None
                if resolution["python_binding"]:
                    # Pin the chosen interpreter, not an inherited Python home or
                    # locale-dependent pipe encoding. Do not add flags to argv.
                    for key in list(env):
                        if key.upper() in {
                            "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV",
                            "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__",
                        }:
                            env.pop(key)
                    env["PYTHONIOENCODING"] = "utf-8"
                    env["PYTHONDONTWRITEBYTECODE"] = "1"
                    encoding = "utf-8"
                result = run_bounded_command(
                    effective_argv,
                    cwd=cwd,
                    env=env,
                    encoding=encoding,
                    timeout=timeout,
                    max_output_bytes=self.settings.max_output_bytes,
                )
        except OSError:
            if self.child_context is not None:
                raise RuntimeError("child_launch_failed") from None
            raise

        return {
            "workspace_id": workspace_id,
            "argv": argv,
            "execution": resolution,
            "timeout_seconds": timeout,
            **result,
        }
