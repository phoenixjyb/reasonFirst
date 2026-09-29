from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from mcp import Client

from .bridge_mcp import build_server
from .codex_app_server import managed_app_server_socket, resolve_codex_binary
from .setup_state import DEFAULT_SETUP_STATE_PATH, SetupState, load_setup_state, save_setup_state
from .setup_tunnel import (
    DEFAULT_TUNNEL_ALIAS,
    build_chatgpt_handoff,
    connect_runtime,
    open_handoff_pages,
    runtime_status,
    stop_runtime,
    validate_alias,
    validate_tunnel_id,
)


DEFAULT_BRIDGE_ALIAS = "reasonfirst-bridge"

EXPECTED_READ_TOOLS = {
    "reasonfirst_doctor",
    "reasonfirst_target_probe",
    "reasonfirst_workspace_status",
    "reasonfirst_files",
    "reasonfirst_read",
    "reasonfirst_diff",
    "reasonfirst_codex_status",
    "reasonfirst_codex_events",
    "reasonfirst_pending_approvals",
    "reasonfirst_finish_preview",
    "reasonfirst_ci",
    "reasonfirst_evidence",
    "reasonfirst_review_bundle",
    "reasonfirst_artifacts",
}

EXPECTED_WRITE_TOOLS = {
    "reasonfirst_dispatch",
    "reasonfirst_codex_start",
    "reasonfirst_codex_continue",
    "reasonfirst_codex_steer",
    "reasonfirst_codex_interrupt",
    "reasonfirst_approve",
    "reasonfirst_decline",
    "reasonfirst_finish",
}

FORBIDDEN_NORMAL_TOOLS = {
    "reasonfirst_authorize_push",
}


class BridgeSetupError(RuntimeError):
    pass


def resolve_bridge_mcp(
    *,
    explicit: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> str | None:
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
        return None
    found = which("reasonfirst-bridge-mcp")
    if found:
        return str(Path(found).resolve())
    return None


async def _bridge_tool_inventory_async() -> dict[str, Any]:
    managed_keys = (
        "RF_MCP_READ_ONLY",
        "RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH",
        "RF_CODEX_BRIDGE_STATE_DIR",
        "RF_BRIDGE_CONFIG",
    )
    previous = {key: os.environ.get(key) for key in managed_keys}

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        os.environ["RF_MCP_READ_ONLY"] = "false"
        os.environ["RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH"] = "false"
        os.environ["RF_CODEX_BRIDGE_STATE_DIR"] = str(root / "state")
        os.environ["RF_BRIDGE_CONFIG"] = str(root / "bridge.yaml")

        server = build_server()
        ctrl = getattr(server, "_reasonfirst_controller", None)
        try:
            async with Client(server) as client:
                listed = await client.list_tools()
        finally:
            if ctrl is not None:
                ctrl.close()
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    tools: dict[str, dict[str, Any]] = {}
    for tool in listed.tools:
        annotations = tool.annotations
        tools[tool.name] = {
            "read_only": bool(annotations.read_only_hint) if annotations else False,
            "destructive": bool(annotations.destructive_hint) if annotations else False,
        }

    names = set(tools)
    missing_read = sorted(EXPECTED_READ_TOOLS - names)
    missing_write = sorted(EXPECTED_WRITE_TOOLS - names)
    forbidden = sorted(FORBIDDEN_NORMAL_TOOLS & names)
    wrongly_read_only = sorted(
        name for name in EXPECTED_WRITE_TOOLS if name in tools and tools[name]["read_only"]
    )
    ok = not (missing_read or missing_write or forbidden or wrongly_read_only)

    return {
        "ok": ok,
        "tool_count": len(tools),
        "tools": tools,
        "missing_read_tools": missing_read,
        "missing_write_tools": missing_write,
        "forbidden_tools": forbidden,
        "write_tools_marked_read_only": wrongly_read_only,
        "remote_push_exposed": "reasonfirst_authorize_push" in names,
    }


def bridge_tool_inventory() -> dict[str, Any]:
    try:
        return asyncio.run(_bridge_tool_inventory_async())
    except Exception as exc:
        raise BridgeSetupError(
            f"Could not verify packaged Bridge tool inventory: {type(exc).__name__}: {exc}"
        ) from exc


def bridge_execution_prerequisites() -> dict[str, Any]:
    socket = managed_app_server_socket()
    codex_bin: str | None = None
    error: str | None = None
    try:
        codex_bin = resolve_codex_binary()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    available = bool(codex_bin) or socket.exists()
    return {
        "ok": available,
        "codex_app_server_capable": available,
        "codex_bin": codex_bin,
        "managed_socket": str(socket),
        "managed_socket_exists": socket.exists(),
        "error": error,
        "note": (
            "The current full-chat Bridge worker-control surface uses Codex App Server. "
            "Copilot CLI remains supported through the standard terminal ActualCoder path, "
            "not this chat-controlled worker surface."
        ),
    }


def connect_bridge_runtime(
    *,
    tunnel_id: str,
    runtime_key: str,
    tunnel_client: str,
    bridge_executable: str,
    alias: str = DEFAULT_BRIDGE_ALIAS,
    read_tunnel_id: str | None = None,
    runner=None,
    sleep=None,
) -> dict[str, Any]:
    bridge_tunnel_id = validate_tunnel_id(tunnel_id)
    if read_tunnel_id is not None and bridge_tunnel_id == validate_tunnel_id(read_tunnel_id):
        raise BridgeSetupError(
            "The privileged Bridge must use a separate tunnel ID from the read-only GitLab app."
        )

    prerequisites = bridge_execution_prerequisites()
    if not prerequisites["ok"]:
        return {
            "ok": False,
            "ready": False,
            "stage": "worker-prerequisite",
            "prerequisites": prerequisites,
        }

    inventory = bridge_tool_inventory()
    if not inventory["ok"]:
        return {
            "ok": False,
            "ready": False,
            "stage": "inventory",
            "inventory": inventory,
            "prerequisites": prerequisites,
        }

    kwargs: dict[str, Any] = {}
    if runner is not None:
        kwargs["runner"] = runner
    if sleep is not None:
        kwargs["sleep"] = sleep

    result = connect_runtime(
        tunnel_id=bridge_tunnel_id,
        runtime_key=runtime_key,
        alias=validate_alias(alias),
        tunnel_client=tunnel_client,
        mcp_executable=bridge_executable,
        **kwargs,
    )
    return {
        **result,
        "inventory": inventory,
        "prerequisites": prerequisites,
        "bridge_remote_push_enabled": False,
        "chatgpt_write_capability_verified": False,
    }


def persist_bridge_state(
    *,
    tunnel_id: str,
    alias: str,
    state_path: Path = DEFAULT_SETUP_STATE_PATH,
) -> SetupState:
    previous = load_setup_state(state_path) or SetupState()
    phases = set(previous.completed_phases)
    phases.add("bridge")
    order = ("system", "gitlab", "worker", "tunnel", "chatgpt-read", "bridge", "ready")
    state = replace(
        previous,
        bridge_tunnel_id=validate_tunnel_id(tunnel_id),
        bridge_runtime=validate_alias(alias),
        completed_phases=tuple(phase for phase in order if phase in phases),
    )
    save_setup_state(state, state_path)
    return state


def bridge_runtime_status(
    *,
    alias: str,
    tunnel_client: str,
    runner=None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if runner is not None:
        kwargs["runner"] = runner
    result = runtime_status(
        alias=validate_alias(alias),
        tunnel_client=tunnel_client,
        **kwargs,
    )
    return {
        **result,
        "bridge_remote_push_enabled": False,
        "chatgpt_write_capability_verified": False,
    }


def stop_bridge_runtime(
    *,
    alias: str,
    tunnel_client: str,
    runner=None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if runner is not None:
        kwargs["runner"] = runner
    return stop_runtime(
        alias=validate_alias(alias),
        tunnel_client=tunnel_client,
        **kwargs,
    )


def build_bridge_handoff(
    *,
    tunnel_id: str,
) -> dict[str, Any]:
    tid = validate_tunnel_id(tunnel_id)
    acceptance_prompt = (
        "Use only the connected ReasonFirst Bridge app. "
        "Call reasonfirst_doctor and report its non-secret status. "
        "Then confirm the connected app tool catalog exposes "
        "reasonfirst_dispatch, reasonfirst_codex_start, reasonfirst_finish_preview, "
        "and reasonfirst_finish. Do not dispatch a task, start a worker, approve a "
        "request, publish, merge, push, or modify any file during this acceptance check. "
        "Also confirm reasonfirst_authorize_push is not exposed."
    )
    return {
        "ok": True,
        "tunnel_id": tid,
        "chatgpt_steps": [
            "Use a separate developer-mode ChatGPT app for the privileged ReasonFirst Bridge; do not replace the read-only GitLab app.",
            "The current chat-controlled worker operations use Codex App Server; Copilot CLI remains a standard terminal ActualCoder backend.",
            "Confirm this ChatGPT workspace/client supports write/modify custom-MCP actions before continuing.",
            "In ChatGPT Plugins, create the Bridge app with Connection = Tunnel.",
            f"Select or paste Bridge tunnel ID {tid}.",
            "Scan/save the app, then run the non-mutating acceptance prompt below.",
        ],
        "acceptance_prompt": acceptance_prompt,
        "full_chat_ready": False,
        "ready_reason": (
            "ReasonFirst cannot mark FULL_CHAT_READY until ChatGPT actually accepts "
            "the Bridge tool catalog in an eligible workspace/client."
        ),
    }


def require_eligibility_acknowledgement(
    *,
    acknowledged: bool,
) -> None:
    if not acknowledged:
        raise BridgeSetupError(
            "Full-chat Bridge setup requires explicit acknowledgement that the "
            "selected ChatGPT workspace/client supports write-capable custom MCP actions."
        )
