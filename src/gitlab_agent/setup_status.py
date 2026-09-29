from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .codex_app_server import managed_app_server_socket
from .config import AgentSettings, resolve_env_file
from .setup_state import (
    DEFAULT_SETUP_STATE_PATH,
    SetupState,
    load_setup_state,
)
from .setup_tunnel import resolve_tunnel_client


def _platform_name(system_name: str) -> str:
    key = system_name.strip().lower()
    if key == "darwin":
        return "macos"
    if key == "windows":
        return "windows"
    if key == "linux":
        return "linux"
    return key or "unknown"


def _linux_distribution() -> dict[str, str] | None:
    path = Path("/etc/os-release")
    if not path.is_file():
        return None
    values: dict[str, str] = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            if "=" not in raw or raw.lstrip().startswith("#"):
                continue
            key, value = raw.split("=", 1)
            value = value.strip().strip('"').strip("'")
            if key in {"ID", "VERSION_ID", "PRETTY_NAME"} and value:
                values[key.lower()] = value
    except OSError:
        return None
    return values or None


def _load_settings_without_env_leak(
    loader: Callable[[], AgentSettings],
) -> AgentSettings:
    before = dict(os.environ)
    try:
        return loader()
    finally:
        os.environ.clear()
        os.environ.update(before)


def _probe(
    name: str,
    *,
    available: bool,
    required: bool,
    message: str,
    **details: object,
) -> dict[str, object]:
    item: dict[str, object] = {
        "name": name,
        "available": available,
        "required": required,
        "status": "pass" if available else ("fail" if required else "missing"),
        "message": message,
    }
    if details:
        item["details"] = details
    return item


def build_setup_status(
    *,
    state_path: Path = DEFAULT_SETUP_STATE_PATH,
    which: Callable[[str], str | None] | None = None,
    settings_loader: Callable[[], AgentSettings] | None = None,
    setup_state_loader: Callable[[Path], SetupState | None] | None = None,
    system_name: str | None = None,
    machine: str | None = None,
    desktop_socket: Path | None = None,
) -> dict[str, object]:
    """Return a non-mutating local setup inventory and next-action plan.

    This intentionally performs no provider login, network request, file write,
    service start/stop, or setup-state mutation.
    """

    resolve = which or shutil.which
    load_settings = settings_loader or AgentSettings.load
    load_state = setup_state_loader or load_setup_state

    system_value = system_name or platform.system()
    platform_id = _platform_name(system_value)
    arch = machine or platform.machine() or "unknown"

    system: dict[str, object] = {
        "os": platform_id,
        "platform_system": system_value,
        "architecture": arch,
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
    }
    if platform_id == "linux":
        distro = _linux_distribution()
        if distro:
            system["distribution"] = distro

    probes: list[dict[str, object]] = []
    python_ok = sys.version_info >= (3, 10)
    probes.append(
        _probe(
            "python",
            available=python_ok,
            required=True,
            message=(
                f"Python {sys.version.split()[0]} satisfies >=3.10"
                if python_ok
                else f"Python {sys.version.split()[0]} is below required >=3.10"
            ),
            executable=sys.executable,
        )
    )

    executable_paths: dict[str, str | None] = {}
    for executable, required in (
        ("git", True),
        ("uv", True),
        ("tunnel-client", False),
        ("codex", False),
        ("copilot", False),
    ):
        path = (
            resolve_tunnel_client(which=resolve)
            if executable == "tunnel-client"
            else resolve(executable)
        )
        executable_paths[executable] = path
        probes.append(
            _probe(
                f"executable_{executable}",
                available=bool(path),
                required=required,
                message=(
                    f"{executable} is available"
                    if path
                    else f"{executable} is not available on PATH"
                ),
                path=path,
            )
        )

    socket = desktop_socket or managed_app_server_socket()
    desktop_available = socket.exists()
    probes.append(
        _probe(
            "codex_desktop",
            available=desktop_available,
            required=False,
            message=(
                "Codex Desktop managed App Server is available"
                if desktop_available
                else "Codex Desktop managed App Server is not detected"
            ),
            path=str(socket),
        )
    )

    workers: list[dict[str, object]] = [
        {
            "name": "codex-cli",
            "available": bool(executable_paths["codex"]),
            "path": executable_paths["codex"],
            "authentication_verified": False,
        },
        {
            "name": "copilot-cli",
            "available": bool(executable_paths["copilot"]),
            "path": executable_paths["copilot"],
            "authentication_verified": False,
        },
        {
            "name": "codex-desktop",
            "available": desktop_available,
            "path": str(socket),
            "authentication_verified": False,
        },
    ]
    worker_available = any(bool(item["available"]) for item in workers)

    config_path = resolve_env_file().expanduser()
    config: dict[str, object] = {
        "path": str(config_path),
        "exists": config_path.is_file(),
        "valid": False,
        "error": None,
    }
    try:
        settings = _load_settings_without_env_leak(load_settings)
        config.update(
            {
                "path": str(settings.config_file),
                "exists": settings.config_file.is_file(),
                "valid": True,
                "gitlab_base_url": settings.gitlab_base_url,
                "has_api_token": bool(settings.api_token),
                "has_git_credential": bool(settings.git_token),
                "allowed_projects": sorted(settings.allowed_projects),
                "require_write_allowlist": settings.require_write_allowlist,
                "default_backend": settings.default_backend,
                "workspace_root": str(settings.workspace_root),
            }
        )
    except Exception as exc:
        config["error"] = str(exc)

    setup_state: dict[str, object]
    state: SetupState | None = None
    try:
        state = load_state(state_path.expanduser())
        if state is None:
            setup_state = {
                "path": str(state_path.expanduser()),
                "exists": False,
                "valid": True,
                "state": None,
            }
        else:
            setup_state = {
                "path": str(state_path.expanduser()),
                "exists": True,
                "valid": True,
                "state": state.to_dict(),
            }
    except Exception as exc:
        setup_state = {
            "path": str(state_path.expanduser()),
            "exists": True,
            "valid": False,
            "error": str(exc),
            "state": None,
        }

    mode = state.mode if state is not None else "standard"
    machine_ready = python_ok and bool(executable_paths["git"]) and bool(
        executable_paths["uv"]
    )
    control_plane_ready = machine_ready and bool(config["valid"]) and worker_available
    tunnel_client_available = bool(executable_paths["tunnel-client"])
    tunnel_recorded = bool(
        state is not None
        and state.tunnel_id
        and state.tunnel_runtime
        and "tunnel" in state.completed_phases
    )

    next_actions: list[dict[str, str]] = []
    if not python_ok:
        next_actions.append(
            {
                "id": "install-python",
                "message": "Install Python >=3.10 before continuing.",
            }
        )
    if not executable_paths["git"]:
        next_actions.append(
            {"id": "install-git", "message": "Install Git before continuing."}
        )
    if not executable_paths["uv"]:
        next_actions.append(
            {
                "id": "install-uv",
                "message": "Install uv using a supported platform method.",
            }
        )
    if not bool(config["valid"]):
        next_actions.append(
            {
                "id": "configure-gitlab",
                "message": (
                    "GitLab configuration is missing or invalid; the guided "
                    "configuration phase will collect it without exposing secrets."
                ),
            }
        )
    if not worker_available:
        next_actions.append(
            {
                "id": "configure-worker",
                "message": (
                    "Install/sign in to Codex CLI, Copilot CLI, or start Codex Desktop."
                ),
            }
        )
    if mode != "cli-only" and not tunnel_client_available:
        next_actions.append(
            {
                "id": "install-tunnel-client",
                "message": (
                    "Install the official OpenAI tunnel-client to enable the "
                    "ChatGPT private MCP path."
                ),
            }
        )
    if mode != "cli-only" and tunnel_client_available and not tunnel_recorded:
        next_actions.append(
            {
                "id": "configure-tunnel",
                "message": (
                    "Attach the packaged ReasonFirst read MCP to an approved OpenAI "
                    "tunnel with 'reasonfirst tunnel connect'."
                ),
            }
        )
    if mode != "cli-only" and tunnel_recorded:
        next_actions.append(
            {
                "id": "verify-tunnel-live",
                "message": (
                    "A tunnel runtime is recorded; run 'reasonfirst tunnel status' "
                    "for live process/health/readiness evidence."
                ),
            }
        )
        next_actions.append(
            {
                "id": "verify-chatgpt",
                "message": (
                    "ChatGPT app authorization/live repository read is still not "
                    "proven by local setup state; run 'reasonfirst chatgpt handoff'."
                ),
            }
        )

    return {
        "ok": True,
        "command": "setup-status",
        "mutating": False,
        "reasonfirst_version": __version__,
        "system": system,
        "mode": mode,
        "probes": probes,
        "config": config,
        "workers": workers,
        "setup_state": setup_state,
        "readiness": {
            "machine_prerequisites": machine_ready,
            "control_plane_prerequisites": control_plane_ready,
            "tunnel_client_available": tunnel_client_available,
            "tunnel_recorded": tunnel_recorded,
            "chatgpt_connection": "not_verified",
            "ready": False,
            "ready_reason": (
                "setup --status is detect-only and never claims full readiness "
                "without live provider/project evidence"
            ),
        },
        "plan": {
            "actions": next_actions,
            "action_count": len(next_actions),
        },
    }
