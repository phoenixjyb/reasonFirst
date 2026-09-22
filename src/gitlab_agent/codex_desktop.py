from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from typing import Any

from .worker_policy import WorkerPolicy


class CodexDesktopError(RuntimeError):
    pass


def resolve_codex_desktop_binary() -> str:
    """Resolve the Codex binary bundled with a desktop app.

    Explicit configuration wins. We intentionally do not fall back to a PATH
    Codex CLI here because the user selected the codex-desktop backend.
    """

    explicit = os.getenv("REASONFIRST_CODEX_DESKTOP_BIN", "").strip()
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if sys.platform == "darwin":
        candidates.extend(
            [
                Path("/Applications/Codex.app/Contents/Resources/codex"),
                Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
                Path.home() / "Applications/Codex.app/Contents/Resources/codex",
                Path.home() / "Applications/ChatGPT.app/Contents/Resources/codex",
            ]
        )

    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())

    raise CodexDesktopError(
        "Codex Desktop backend is unavailable. Start/install Codex or ChatGPT "
        "Desktop with a bundled Codex binary, or set REASONFIRST_CODEX_DESKTOP_BIN."
    )


def codex_desktop_available() -> tuple[bool, str | None]:
    try:
        return True, resolve_codex_desktop_binary()
    except CodexDesktopError:
        return False, None


def _sandbox_name(policy: WorkerPolicy) -> str:
    if policy.sandbox_mode == "read-only":
        return "readOnly"
    if policy.sandbox_mode == "workspace-write":
        return "workspaceWrite"
    raise CodexDesktopError(
        f"Unsupported codex-desktop sandbox mode {policy.sandbox_mode!r}"
    )


def _approval_name(policy: WorkerPolicy) -> str:
    if policy.approval_policy == "on-request":
        return "onRequest"
    if policy.approval_policy == "never":
        return "never"
    raise CodexDesktopError(
        f"Unsupported codex-desktop approval policy {policy.approval_policy!r}"
    )


class CodexDesktopWorker:
    """Small synchronous Codex App Server client for one ReasonFirst handoff.

    The desktop backend uses the desktop-bundled Codex executable and the
    user's existing Codex/ChatGPT sign-in state. It does not use the OpenAI
    model API directly.
    """

    def __init__(self, *, binary: str | None = None) -> None:
        self.binary = binary or resolve_codex_desktop_binary()
        self._closed = False
        self.proc = subprocess.Popen(
            [self.binary, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if self.proc.stdin is None or self.proc.stdout is None or self.proc.stderr is None:
            self.close()
            raise CodexDesktopError("Failed to open Codex App Server pipes")

        self._next_id = 1
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._events: list[dict[str, Any]] = []
        self._turn_done = threading.Condition()
        self._turn_status: dict[str, str] = {}
        self._stderr_tail: list[str] = []
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._reader.start()
        self._stderr_reader.start()
        self._initialize()

    def _write(self, payload: dict[str, Any]) -> None:
        if self._closed or self.proc.poll() is not None:
            raise CodexDesktopError(
                "Codex App Server is not running; stderr="
                + "\n".join(self._stderr_tail[-5:])
            )
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            assert self.proc.stdin is not None
            self.proc.stdin.write(data + "\n")
            self.proc.stdin.flush()

    def _request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 60.0,
    ) -> Any:
        with self._pending_lock:
            request_id = self._next_id
            self._next_id += 1
            waiter: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[request_id] = waiter
        try:
            self._write({"method": method, "id": request_id, "params": params or {}})
            try:
                response = waiter.get(timeout=timeout)
            except queue.Empty as exc:
                raise CodexDesktopError(
                    f"Timed out waiting for Codex App Server method {method}"
                ) from exc
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)
        if "error" in response:
            raise CodexDesktopError(
                f"Codex App Server {method} failed: {response['error']}"
            )
        return response.get("result")

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._write({"method": method, "params": params or {}})

    def _initialize(self) -> None:
        self._request(
            "initialize",
            {
                "clientInfo": {
                    "name": "reasonfirst",
                    "title": "ReasonFirst",
                    "version": "0.4-worker-backend",
                }
            },
            timeout=30,
        )
        self._notify("initialized", {})

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            text = line.rstrip("\r\n")
            if text:
                self._stderr_tail.append(text[:2000])
                del self._stderr_tail[:-20]

    def _decline_server_request(self, msg: dict[str, Any]) -> None:
        request_id = msg.get("id")
        if not isinstance(request_id, int):
            return
        method = str(msg.get("method") or "")
        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            result: Any = "decline"
        elif method == "item/permissions/requestApproval":
            result = {"permissions": {}}
        elif method in {"mcpServer/elicitation/request", "tool/requestUserInput"}:
            result = {"action": "decline", "content": None}
        else:
            self._write(
                {
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": "ReasonFirst does not handle this App Server request",
                    },
                }
            )
            return
        self._write({"id": request_id, "result": result})

    def _handle_event(self, msg: dict[str, Any]) -> None:
        method = str(msg.get("method") or "")
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        if method in {
            "turn/started",
            "turn/completed",
            "item/completed",
            "error",
        }:
            self._events.append(msg)
            del self._events[:-100]
        if method == "turn/completed":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            turn_id = str(turn.get("id") or "")
            status = str(turn.get("status") or "completed")
            if turn_id:
                with self._turn_done:
                    self._turn_status[turn_id] = status
                    self._turn_done.notify_all()

    def _read_loop(self) -> None:
        assert self.proc.stdout is not None
        try:
            for line in self.proc.stdout:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue

                request_id = msg.get("id")
                method = msg.get("method")
                if isinstance(request_id, int) and isinstance(method, str):
                    self._decline_server_request(msg)
                    continue
                if isinstance(request_id, int):
                    with self._pending_lock:
                        waiter = self._pending.get(request_id)
                    if waiter is not None:
                        waiter.put(msg)
                    continue
                if isinstance(method, str):
                    self._handle_event(msg)
        finally:
            self._closed = True
            with self._turn_done:
                self._turn_done.notify_all()

    def _assert_policy_allowed(
        self,
        *,
        approval: str,
        sandbox: str,
    ) -> None:
        try:
            data = self._request("configRequirements/read", {}, timeout=15)
        except CodexDesktopError:
            # Older App Server builds may not expose managed requirements.
            return
        requirements = (
            data.get("requirements")
            if isinstance(data, dict) and isinstance(data.get("requirements"), dict)
            else {}
        )
        allowed_approvals = requirements.get("allowedApprovalPolicies")
        if (
            isinstance(allowed_approvals, list)
            and allowed_approvals
            and approval not in {str(item) for item in allowed_approvals}
        ):
            raise CodexDesktopError(
                f"Codex Desktop policy disallows approvalPolicy={approval!r}"
            )
        allowed_sandboxes = requirements.get("allowedSandboxModes")
        if (
            isinstance(allowed_sandboxes, list)
            and allowed_sandboxes
            and sandbox not in {str(item) for item in allowed_sandboxes}
        ):
            raise CodexDesktopError(
                f"Codex Desktop policy disallows sandbox={sandbox!r}"
            )

    def run(
        self,
        *,
        cwd: Path,
        prompt: str,
        policy: WorkerPolicy,
        timeout_seconds: int | None = None,
    ) -> dict[str, object]:
        if policy.backend not in {"codex", "codex-desktop"}:
            raise CodexDesktopError(
                f"codex-desktop requires a Codex WorkerPolicy, got {policy.backend!r}"
            )

        sandbox = _sandbox_name(policy)
        approval = _approval_name(policy)
        self._assert_policy_allowed(approval=approval, sandbox=sandbox)
        thread_params: dict[str, Any] = {
            "cwd": str(cwd),
            "approvalPolicy": approval,
            "sandbox": sandbox,
            "serviceName": "reasonfirst",
        }
        if policy.model:
            thread_params["model"] = policy.model

        thread_result = self._request("thread/start", thread_params, timeout=30)
        thread = (
            thread_result.get("thread")
            if isinstance(thread_result, dict)
            and isinstance(thread_result.get("thread"), dict)
            else {}
        )
        thread_id = str(thread.get("id") or "")
        if not thread_id:
            raise CodexDesktopError("thread/start returned no thread id")

        sandbox_policy: dict[str, Any]
        if sandbox == "readOnly":
            sandbox_policy = {
                "type": "readOnly",
                "networkAccess": bool(policy.network_access),
            }
        else:
            sandbox_policy = {
                "type": "workspaceWrite",
                "writableRoots": [str(cwd)],
                "networkAccess": bool(policy.network_access),
            }

        turn_params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
            "cwd": str(cwd),
            "approvalPolicy": approval,
            "sandboxPolicy": sandbox_policy,
        }
        if policy.model:
            turn_params["model"] = policy.model
        if policy.reasoning_effort:
            turn_params["effort"] = policy.reasoning_effort

        turn_result = self._request("turn/start", turn_params, timeout=30)
        turn = (
            turn_result.get("turn")
            if isinstance(turn_result, dict)
            and isinstance(turn_result.get("turn"), dict)
            else {}
        )
        turn_id = str(turn.get("id") or "")
        if not turn_id:
            raise CodexDesktopError("turn/start returned no turn id")

        deadline = time.monotonic() + float(timeout_seconds or 3600)
        with self._turn_done:
            while turn_id not in self._turn_status and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    try:
                        self._request(
                            "turn/interrupt",
                            {"threadId": thread_id, "turnId": turn_id},
                            timeout=10,
                        )
                    except Exception:
                        pass
                    raise CodexDesktopError(
                        f"Codex Desktop turn exceeded {timeout_seconds or 3600}s"
                    )
                self._turn_done.wait(timeout=min(remaining, 1.0))

        if self._closed and turn_id not in self._turn_status:
            raise CodexDesktopError(
                "Codex App Server exited before turn completion; stderr="
                + "\n".join(self._stderr_tail[-5:])
            )

        status = self._turn_status.get(turn_id, "unknown")
        return {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "turn_status": status,
            "events": list(self._events[-20:]),
            "backend_binary": self.binary,
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
