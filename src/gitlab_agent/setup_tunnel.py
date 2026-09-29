from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable

from .setup_state import DEFAULT_SETUP_STATE_PATH, SetupState, load_setup_state, save_setup_state


DEFAULT_TUNNEL_ALIAS = "reasonfirst-gitlab"
RUNTIME_KEY_ENV = "CONTROL_PLANE_API_KEY"
RUNTIME_KEY_REF = f"env:{RUNTIME_KEY_ENV}"
PLATFORM_TUNNELS_URL = "https://platform.openai.com/settings/organization/tunnels"
CHATGPT_URL = "https://chatgpt.com/"

_TUNNEL_ID = re.compile(r"tunnel_[a-z0-9]{32}\Z")
_ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


class TunnelSetupError(RuntimeError):
    pass


def validate_tunnel_id(value: str) -> str:
    tunnel_id = value.strip()
    if not _TUNNEL_ID.fullmatch(tunnel_id):
        raise TunnelSetupError(
            "Tunnel ID must match tunnel_<32 lowercase letters or digits>"
        )
    return tunnel_id


def validate_alias(value: str) -> str:
    alias = value.strip()
    if not _ALIAS.fullmatch(alias):
        raise TunnelSetupError(
            "Tunnel alias must be 1-64 characters using letters, digits, dot, underscore or dash"
        )
    return alias


def resolve_tunnel_client(
    *,
    explicit: str | None = None,
    state: SetupState | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> str | None:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    if state is not None and state.tunnel_client_path:
        candidates.append(state.tunnel_client_path)
    found = which("tunnel-client") or which("tunnel-client.exe")
    if found:
        candidates.append(found)

    default_root = Path("~/.local/share/reasonfirst/bin").expanduser()
    candidates.extend(
        [
            str(default_root / "tunnel-client"),
            str(default_root / "tunnel-client.exe"),
        ]
    )

    for raw in candidates:
        path = Path(raw).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
    return None


def resolve_read_mcp(
    *,
    explicit: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> str | None:
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
        return None
    found = which("reasonfirst-gitlab-mcp")
    if found:
        return str(Path(found).resolve())
    return None


def _command_string(executable: str) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline([executable])
    return shlex.join([executable])


def _sanitize(text: str, secret: str | None) -> str:
    if secret:
        return text.replace(secret, "<redacted>")
    return text


def _parse_json_output(stdout: str, stderr: str, *, secret: str | None = None) -> dict[str, Any]:
    safe_stdout = _sanitize(stdout, secret).strip()
    safe_stderr = _sanitize(stderr, secret).strip()
    if safe_stdout:
        try:
            payload = json.loads(safe_stdout)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            return payload
    return {
        "ok": False,
        "error": safe_stderr or safe_stdout or "tunnel-client produced no JSON output",
    }


def _run_tunnel_json(
    tunnel_client: str,
    args: list[str],
    *,
    runtime_key: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout: int = 60,
) -> tuple[int, dict[str, Any]]:
    env = os.environ.copy()
    # A normal runtime operation must not accidentally consume a broad admin key.
    env.pop("OPENAI_ADMIN_KEY", None)
    if runtime_key is not None:
        env[RUNTIME_KEY_ENV] = runtime_key
    try:
        proc = runner(
            [tunnel_client, *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TunnelSetupError(f"Failed to run tunnel-client: {type(exc).__name__}") from exc

    payload = _parse_json_output(proc.stdout, proc.stderr, secret=runtime_key)
    payload.setdefault("returncode", proc.returncode)
    return proc.returncode, payload


def runtime_ready(payload: dict[str, Any]) -> bool:
    return (
        bool(payload.get("process_running"))
        and bool(payload.get("healthy"))
        and bool(payload.get("ready"))
    )


def runtime_status(
    *,
    alias: str,
    tunnel_client: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    name = validate_alias(alias)
    code, payload = _run_tunnel_json(
        tunnel_client,
        ["runtimes", "status", name, "--json"],
        runner=runner,
        timeout=30,
    )
    return {
        "ok": code == 0 and bool(payload.get("process_running")) and bool(payload.get("healthy")),
        "ready": runtime_ready(payload),
        "alias": name,
        "tunnel_id": payload.get("tunnel_id"),
        "runtime_state": payload.get("runtime_state"),
        "process_running": bool(payload.get("process_running")),
        "healthy": bool(payload.get("healthy")),
        "native_ready": bool(payload.get("ready")),
        "ui_url": payload.get("ui_url"),
        "health_url": payload.get("health_url"),
        "repair_actions": payload.get("repair_actions") or [],
        "native": payload,
    }


def connect_runtime(
    *,
    tunnel_id: str,
    runtime_key: str,
    alias: str = DEFAULT_TUNNEL_ALIAS,
    tunnel_client: str,
    mcp_executable: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    ready_timeout_seconds: int = 30,
) -> dict[str, Any]:
    tid = validate_tunnel_id(tunnel_id)
    name = validate_alias(alias)
    secret = runtime_key.strip()
    if not secret:
        raise TunnelSetupError("Runtime API key is required")
    mcp = Path(mcp_executable).expanduser()
    if not mcp.is_file() or not os.access(mcp, os.X_OK):
        raise TunnelSetupError(f"Packaged read MCP executable is unavailable: {mcp}")

    code, native = _run_tunnel_json(
        tunnel_client,
        [
            "runtimes",
            "connect",
            "--alias",
            name,
            "--tunnel-id",
            tid,
            "--runtime-api-key",
            RUNTIME_KEY_REF,
            "--mcp-command",
            _command_string(str(mcp.resolve())),
            "--json",
        ],
        runtime_key=secret,
        runner=runner,
        timeout=90,
    )
    if code != 0:
        return {
            "ok": False,
            "ready": False,
            "alias": name,
            "tunnel_id": tid,
            "stage": "connect",
            "native": native,
        }

    deadline = time.monotonic() + max(0, ready_timeout_seconds)
    status: dict[str, Any] | None = None
    while True:
        status_code, payload = _run_tunnel_json(
            tunnel_client,
            ["runtimes", "status", name, "--json"],
            runtime_key=secret,
            runner=runner,
            timeout=30,
        )
        status = payload
        if status_code == 0 and runtime_ready(payload):
            return {
                "ok": True,
                "ready": True,
                "alias": name,
                "tunnel_id": tid,
                "stage": "ready",
                "process_running": True,
                "healthy": True,
                "ui_url": payload.get("ui_url"),
                "health_url": payload.get("health_url"),
                "native": payload,
            }
        if time.monotonic() >= deadline:
            break
        sleep(1.0)

    return {
        "ok": False,
        "ready": False,
        "alias": name,
        "tunnel_id": tid,
        "stage": "readiness",
        "process_running": bool((status or {}).get("process_running")),
        "healthy": bool((status or {}).get("healthy")),
        "ui_url": (status or {}).get("ui_url"),
        "health_url": (status or {}).get("health_url"),
        "native": status or native,
        "message": "Managed tunnel runtime did not reach process_running + healthy + ready before timeout.",
    }


def stop_runtime(
    *,
    alias: str,
    tunnel_client: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    name = validate_alias(alias)
    code, payload = _run_tunnel_json(
        tunnel_client,
        ["runtimes", "stop", name, "--json"],
        runner=runner,
        timeout=30,
    )
    return {
        "ok": code == 0 and bool(payload.get("stopped")),
        "alias": name,
        "stopped": bool(payload.get("stopped")),
        "already_stopped": bool(payload.get("already_stopped")),
        "native": payload,
    }


def persist_tunnel_state(
    *,
    tunnel_id: str,
    alias: str,
    tunnel_client: str,
    state_path: Path = DEFAULT_SETUP_STATE_PATH,
) -> SetupState:
    previous = load_setup_state(state_path) or SetupState()
    phases = set(previous.completed_phases)
    phases.add("tunnel")
    order = ("system", "gitlab", "worker", "tunnel", "chatgpt-read", "bridge", "ready")
    state = SetupState(
        **{
            **previous.__dict__,
            "tunnel_id": validate_tunnel_id(tunnel_id),
            "tunnel_runtime": validate_alias(alias),
            "tunnel_client_path": str(Path(tunnel_client).resolve()),
            "completed_phases": tuple(phase for phase in order if phase in phases),
        }
    )
    save_setup_state(state, state_path)
    return state


def build_chatgpt_handoff(
    *,
    tunnel_id: str,
    project: str,
    ref: str = "main",
) -> dict[str, Any]:
    tid = validate_tunnel_id(tunnel_id)
    prompt = (
        "Use the connected ReasonFirst app only. "
        "Call gitlab_whoami, then check_project_access with "
        f'project="{project}", ref="{ref}", required_files=["README.md"]. '
        "If and only if that succeeds, read README.md at the resolved revision. "
        "Report the authenticated GitLab username and resolved commit SHA. "
        "Do not use web search, a GitHub copy, old conversation state, or guessed repository content."
    )
    return {
        "ok": True,
        "tunnel_id": tid,
        "platform_tunnels_url": PLATFORM_TUNNELS_URL,
        "chatgpt_url": CHATGPT_URL,
        "chatgpt_steps": [
            "Confirm the tunnel is associated with the intended ChatGPT workspace and your identity has Tunnels Read + Use.",
            "In ChatGPT Plugins, create a developer-mode app and choose Tunnel under Connection.",
            f"Select or paste tunnel ID {tid}.",
            "Scan tools and create/save the app.",
            "Run the acceptance prompt below in a normal ChatGPT conversation.",
        ],
        "acceptance_prompt": prompt,
        "chatgpt_ready": False,
        "ready_reason": (
            "Local ReasonFirst cannot truthfully mark CHATGPT_READY until the "
            "browser-side app is created and the live ChatGPT read probe succeeds."
        ),
    }


def open_handoff_pages(
    *,
    open_platform: bool,
    open_chatgpt: bool,
    opener: Callable[[str], bool] = webbrowser.open,
) -> dict[str, bool]:
    result = {"platform_opened": False, "chatgpt_opened": False}
    if open_platform:
        result["platform_opened"] = bool(opener(PLATFORM_TUNNELS_URL))
    if open_chatgpt:
        result["chatgpt_opened"] = bool(opener(CHATGPT_URL))
    return result
