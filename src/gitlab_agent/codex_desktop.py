from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import queue
from typing import Any, Callable

from .worker_policy import WorkerPolicy, normalize_backend


class CodexDesktopError(RuntimeError):
    pass


def resolve_desktop_codex_binary() -> str | None:
    """Resolve a Codex binary associated with the Desktop/App Server backend."""

    explicit = os.getenv("REASONFIRST_CODEX_DESKTOP_BIN", "").strip()
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    if sys.platform == "darwin":
        candidates.extend(
            [
                "/Applications/ChatGPT.app/Contents/Resources/codex",
                "/Applications/Codex.app/Contents/Resources/codex",
                str(Path.home() / "Applications/ChatGPT.app/Contents/Resources/codex"),
                str(Path.home() / "Applications/Codex.app/Contents/Resources/codex"),
            ]
        )
    # An explicit PATH codex can still host App Server, but only use it for the
    # desktop backend when the user opts in with REASONFIRST_CODEX_DESKTOP_BIN.
    for item in candidates:
        path = Path(item).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
    return None


def desktop_app_server_argv(binary: str) -> list[str]:
    return [
        binary,
        "--config",
        "mcp_servers.reasonfirst.enabled=false",
        "app-server",
    ]


def desktop_thread_params(policy: WorkerPolicy, cwd: str) -> dict[str, Any]:
    if normalize_backend(policy.backend) != "codex-desktop":
        raise ValueError("desktop thread policy must target codex-desktop")
    approval = policy.approval_policy or "on-request"
    return {
        "cwd": cwd,
        "model": policy.model,
        "effort": policy.reasoning_effort,
        "approvalPolicy": approval,
        "sandbox": policy.sandbox_mode or "workspace-write",
        "serviceName": "reasonfirst",
        "threadSource": "user",
    }


def desktop_turn_params(
    policy: WorkerPolicy,
    *,
    thread_id: str,
    cwd: str,
    prompt: str,
) -> dict[str, Any]:
    if normalize_backend(policy.backend) != "codex-desktop":
        raise ValueError("desktop turn policy must target codex-desktop")
    sandbox = policy.sandbox_mode or "workspace-write"
    network = bool(policy.network_access)
    sandbox_policy = (
        {"type": "readOnly", "networkAccess": network}
        if sandbox == "read-only"
        else {
            "type": "workspaceWrite",
            "writableRoots": [cwd],
            "networkAccess": network,
        }
    )
    return {
        "threadId": thread_id,
        "input": [{"type": "text", "text": prompt}],
        "cwd": cwd,
        "model": policy.model,
        "effort": policy.reasoning_effort,
        "approvalPolicy": policy.approval_policy or "on-request",
        "sandboxPolicy": sandbox_policy,
    }


class CodexDesktopAppServer:
    """Small synchronous stdio client for a Desktop-bundled Codex App Server."""

    def __init__(
        self,
        *,
        binary: str,
        popen: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
    ) -> None:
        self.proc = popen(
            desktop_app_server_argv(binary),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if self.proc.stdin is None or self.proc.stdout is None:
            raise CodexDesktopError("Codex App Server stdio pipes are unavailable")
        self._next_id = 1
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "reasonfirst",
                    "title": "ReasonFirst",
                    "version": "0.3",
                },
                "capabilities": {"experimentalApi": True},
            },
            timeout=30,
        )
        self.notify("initialized", {})

    def _write(self, payload: dict[str, Any]) -> None:
        if self.proc.poll() is not None:
            raise CodexDesktopError("Codex App Server exited")
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        self.proc.stdin.flush()

    def _read_loop(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            rid = msg.get("id")
            if isinstance(rid, int) and "method" not in msg:
                with self._pending_lock:
                    waiter = self._pending.get(rid)
                if waiter is not None:
                    waiter.put(msg)
            else:
                self._events.put(msg)

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float = 60,
    ) -> Any:
        with self._pending_lock:
            rid = self._next_id
            self._next_id += 1
            waiter: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[rid] = waiter
        try:
            self._write({"method": method, "id": rid, "params": params})
            try:
                msg = waiter.get(timeout=timeout)
            except queue.Empty as exc:
                raise CodexDesktopError(f"Timed out waiting for {method}") from exc
        finally:
            with self._pending_lock:
                self._pending.pop(rid, None)
        if "error" in msg:
            raise CodexDesktopError(f"App Server {method} failed: {msg['error']}")
        return msg.get("result")

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"method": method, "params": params})

    def start(
        self,
        *,
        policy: WorkerPolicy,
        cwd: str,
        prompt: str,
    ) -> dict[str, object]:
        thread_result = self.request(
            "thread/start",
            desktop_thread_params(policy, cwd),
            timeout=30,
        )
        thread = thread_result.get("thread") if isinstance(thread_result, dict) else None
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise CodexDesktopError("thread/start returned no thread id")
        turn_result = self.request(
            "turn/start",
            desktop_turn_params(
                policy,
                thread_id=thread_id,
                cwd=cwd,
                prompt=prompt,
            ),
            timeout=30,
        )
        turn = turn_result.get("turn") if isinstance(turn_result, dict) else None
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if not isinstance(turn_id, str) or not turn_id:
            raise CodexDesktopError("turn/start returned no turn id")
        return {
            "backend": "codex-desktop",
            "thread_id": thread_id,
            "turn_id": turn_id,
        }

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
