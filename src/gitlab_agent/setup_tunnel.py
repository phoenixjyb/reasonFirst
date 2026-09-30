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

from .secret_scan import redact_sensitive_text
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
        # shutil.which already establishes executable PATH resolution. Trust the
        # injected resolver too so cross-platform tests do not need fake files.
        return str(Path(found).expanduser())

    # Current verified installer uses versioned bundle directories so the
    # pinned cloudflared companion stays adjacent. Retain the historical bin
    # lookup for older previews.
    bundle_root = Path("~/.local/share/reasonfirst/tunnel-client").expanduser()
    if bundle_root.is_dir() and not bundle_root.is_symlink():
        bundle_candidates = sorted(
            [
                path
                for name in ("tunnel-client", "tunnel-client.exe")
                for path in bundle_root.glob(f"*/{name}")
                if path.is_file() and not path.is_symlink() and os.access(path, os.X_OK)
            ],
            key=lambda path: path.parent.name,
            reverse=True,
        )
        if bundle_candidates:
            return str(bundle_candidates[0].resolve())

    legacy_root = Path("~/.local/share/reasonfirst/bin").expanduser()
    candidates.extend(
        [
            str(legacy_root / "tunnel-client"),
            str(legacy_root / "tunnel-client.exe"),
        ]
    )

    for raw in candidates:
        path = Path(raw).expanduser()
        if path.is_file() and not path.is_symlink() and os.access(path, os.X_OK):
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
    """Serialize for tunnel-client's parseCommandArgv, NOT cmd.exe/MSVCRT.

    The upstream parser uses single/double quotes and backslash escapes on all
    platforms. Windows list2cmdline therefore loses path separators. shlex.join
    protects backslashes, spaces and embedded apostrophes for that parser. The
    outer subprocess still receives an argv list; no shell is introduced.
    """
    if not executable or any(char in executable for char in ("\x00", "\r", "\n")):
        raise TunnelSetupError("MCP executable must be a nonempty single-line path")
    return shlex.join([executable])


def _sanitize(text: str, secret: str | None) -> str:
    if secret:
        text = text.replace(secret, "<redacted>")
    return redact_sensitive_text(text)[0]


def _redact_payload(value: Any, secret: str | None) -> Any:
    if isinstance(value, str):
        return _sanitize(value, secret)
    if isinstance(value, list):
        return [_redact_payload(item, secret) for item in value]
    if isinstance(value, dict):
        return {
            _sanitize(str(key), secret): (
                "<redacted>"
                if re.search(r"(?i)(token|secret|password|api[_-]?key)", str(key))
                else _redact_payload(item, secret)
            )
            for key, item in value.items()
        }
    return value


def _diagnostic_text(value: str | bytes | None, secret: str | None) -> str:
    # Replacement is allowed ONLY for diagnostics, never protocol/identity data.
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str):
        return ""
    # Redact before truncation so cutting a token cannot evade redaction.
    return _sanitize(value, secret).strip()[-2000:]


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON value")


def _parse_json_output(
    stdout: str | bytes | None, stderr: str | bytes | None, *, secret: str | None = None,
) -> dict[str, Any]:
    try:
        # Go emits UTF-8 JSON regardless of the host's locale. Capture bytes to
        # avoid Python's Windows reader thread decoding them as GBK/cp1252.
        text = stdout.decode("utf-8", errors="strict") if isinstance(stdout, bytes) else stdout
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Missing JSON output")
        payload = json.loads(text, object_pairs_hook=_json_object, parse_constant=_reject_constant)
        if not isinstance(payload, dict):
            raise ValueError("JSON output is not an object")
        for field in ("ok", "ready", "healthy", "process_running", "stopped", "already_stopped"):
            if field in payload and not isinstance(payload[field], bool):
                raise ValueError("Invalid JSON boolean")
    except (ValueError, UnicodeError, RecursionError):
        # Do not expose decoder exception strings, raw bytes, or a partially
        # decoded object; invalid protocol output must never count as readiness.
        return {
            "ok": False,
            "protocol_error": True,
            "error": "tunnel-client returned missing or invalid UTF-8 JSON",
            "diagnostic": _diagnostic_text(stderr, secret) or _diagnostic_text(stdout, secret),
        }
    return _redact_payload(payload, secret)


def runtime_diagnostics(payload: dict[str, Any]) -> list[str]:
    """Bounded, scrubbed failure explanation; never execute suggested repairs."""
    messages: list[str] = []

    def add(value: Any) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        text = _sanitize(value, None)
        text = re.sub(r"[\x00-\x1f\x7f]", " ", text).strip()
        text = text[:600]
        if text and text not in messages and len(messages) < 5:
            messages.append(text)

    add(payload.get("error"))
    add(payload.get("diagnostic"))
    local = payload.get("local")
    if isinstance(local, dict):
        log = local.get("log")
        tail = log.get("tail") if isinstance(log, dict) else None
        if isinstance(tail, str):
            for line in reversed(tail.splitlines()[-40:]):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and str(event.get("level", "")).upper() in {"ERROR", "FATAL"}:
                    add(event.get("error") or event.get("msg"))
        issues = local.get("issues")
        if isinstance(issues, list):
            for issue in issues:
                add(issue)
    return messages


def _run_tunnel_json(
    tunnel_client: str,
    args: list[str],
    *,
    runtime_key: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
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
            text=False,
            env=env,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TunnelSetupError(f"Failed to run tunnel-client: {type(exc).__name__}") from exc

    payload = _parse_json_output(
        proc.stdout, proc.stderr, secret=runtime_key or env.get(RUNTIME_KEY_ENV),
    )
    payload["returncode"] = proc.returncode
    # Keep the real exit status in evidence, but reject malformed output even
    # when the native process returned zero.
    code = proc.returncode or (1 if payload.get("protocol_error") else 0)
    return code, payload


def runtime_ready(payload: dict[str, Any]) -> bool:
    return (
        payload.get("ok") is not False
        and not payload.get("protocol_error")
        and payload.get("process_running") is True
        and payload.get("healthy") is True
        and payload.get("ready") is True
    )


def runtime_status(
    *,
    alias: str,
    tunnel_client: str,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> dict[str, Any]:
    name = validate_alias(alias)
    code, payload = _run_tunnel_json(
        tunnel_client,
        ["runtimes", "status", name, "--json"],
        runner=runner,
        timeout=30,
    )
    return {
        "ok": code == 0 and payload.get("ok") is not False
        and payload.get("process_running") is True and payload.get("healthy") is True,
        "ready": code == 0 and runtime_ready(payload),
        "status_query_ok": code == 0 and payload.get("ok") is not False,
        "diagnostics": runtime_diagnostics(payload),
        "alias": name,
        "tunnel_id": payload.get("tunnel_id"),
        "runtime_state": payload.get("runtime_state"),
        "process_running": bool(payload.get("process_running")),
        "healthy": bool(payload.get("healthy")),
        "native_ready": code == 0 and runtime_ready(payload),
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
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
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
    if code != 0 or native.get("ok") is False:
        return {
            "ok": False,
            "ready": False,
            "alias": name,
            "tunnel_id": tid,
            "stage": "connect",
            "diagnostics": runtime_diagnostics(native),
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
        "diagnostics": runtime_diagnostics(status or native),
        "message": "Managed tunnel runtime did not reach process_running + healthy + ready before timeout.",
    }


def stop_runtime(
    *,
    alias: str,
    tunnel_client: str,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
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
