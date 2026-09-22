from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shlex
import shutil
import subprocess
import sys
import threading
from typing import Any, Callable

from . import __version__

from gitlab_agent.worker_policy import WorkerPolicy, default_worker_policy


class AppServerError(RuntimeError):
    pass


def resolve_codex_binary() -> str:
    explicit = os.getenv("CODEX_BRIDGE_CODEX_BIN", "").strip()
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    found = shutil.which("codex")
    if found:
        candidates.append(found)
    if sys.platform == "darwin":
        candidates.extend([
            "/Applications/ChatGPT.app/Contents/Resources/codex",
            "/Applications/Codex.app/Contents/Resources/codex",
            str(Path.home() / "Applications/ChatGPT.app/Contents/Resources/codex"),
            str(Path.home() / "Applications/Codex.app/Contents/Resources/codex"),
        ])
    for item in candidates:
        path = Path(item).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
    raise AppServerError(
        "Codex executable not found. Install/login Codex or set CODEX_BRIDGE_CODEX_BIN."
    )




def resolve_desktop_or_codex_binary() -> str:
    """Honor an explicit binary, then prefer Desktop-bundled Codex over PATH.

    The launched process still reads the normal CODEX_HOME and ~/.codex/config.toml.
    """
    candidates: list[str] = []
    explicit = os.getenv("CODEX_BRIDGE_CODEX_BIN", "").strip()
    if explicit:
        candidates.append(explicit)
    if sys.platform == "darwin":
        candidates.extend([
            "/Applications/ChatGPT.app/Contents/Resources/codex",
            "/Applications/Codex.app/Contents/Resources/codex",
            str(Path.home() / "Applications/ChatGPT.app/Contents/Resources/codex"),
            str(Path.home() / "Applications/Codex.app/Contents/Resources/codex"),
        ])
    found = shutil.which("codex")
    if found:
        candidates.append(found)
    for item in candidates:
        path = Path(item).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
    return resolve_codex_binary()

def managed_app_server_socket() -> Path:
    """Return the managed Desktop socket path without requiring a resolvable HOME.

    Diagnostic/test environments may intentionally clear HOME/USERPROFILE. That
    should make the Desktop backend unavailable, not crash Doctor or backend
    discovery.
    """
    configured = os.getenv("CODEX_HOME", "").strip()
    if configured:
        codex_home = Path(configured).expanduser()
    else:
        try:
            codex_home = Path.home() / ".codex"
        except RuntimeError:
            fallback = os.getenv("LOCALAPPDATA") or os.getenv("TEMP") or os.getcwd()
            codex_home = Path(fallback) / ".reasonfirst-unavailable-codex-home"
    return codex_home.resolve(strict=False) / "app-server-control" / "app-server-control.sock"


class AppServerClient:
    """Synchronous Codex app-server client.

    It supports three execution topologies with one protocol surface:
    - local stdio: start `codex app-server` on this machine;
    - remote stdio: start `codex app-server` through SSH on a selected host;
    - managed Unix socket: attach to the user-private app-server socket used by
      Codex/ChatGPT Desktop when that managed daemon is available.
    """

    def __init__(
        self,
        *,
        codex_bin: str | None = None,
        launch_argv: list[str] | None = None,
        unix_socket: str | None = None,
        backend_name: str = "standalone-local",
        event_handler: Callable[[dict[str, Any]], None] | None = None,
        server_request_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        approval_request_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        request_timeout: float = 60.0,
    ) -> None:
        self.codex_bin = codex_bin or (resolve_codex_binary() if launch_argv is None and unix_socket is None else "")
        self.event_handler = event_handler
        self.server_request_handler = server_request_handler
        self.approval_request_handler = approval_request_handler
        self.request_timeout = request_timeout
        self.backend_name = backend_name
        self._next_id = 1
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._closed = False
        self._stderr_tail: list[str] = []
        self._thread_policy_evidence: dict[str, dict[str, Any]] = {}
        self._thread_policy_violations: dict[str, list[dict[str, Any]]] = {}
        self.proc: subprocess.Popen[str] | None = None
        self.ws: Any = None

        if unix_socket:
            self._connect_unix_socket(unix_socket)
            self._reader = threading.Thread(
                target=self._read_ws_loop,
                name="codex-app-server-ws-reader",
                daemon=True,
            )
            self._stderr_reader = None
            self._reader.start()
        else:
            argv = list(launch_argv or [self.codex_bin or resolve_codex_binary(), "app-server"])
            try:
                self.proc = subprocess.Popen(
                    argv,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
            except OSError as exc:
                raise AppServerError(f"Failed to launch Codex app-server: {argv!r}: {exc}") from exc
            if self.proc.stdin is None or self.proc.stdout is None or self.proc.stderr is None:
                raise AppServerError("Failed to open codex app-server stdio pipes")
            self._reader = threading.Thread(
                target=self._read_stdio_loop,
                name="codex-app-server-reader",
                daemon=True,
            )
            self._stderr_reader = threading.Thread(
                target=self._read_stderr,
                name="codex-app-server-stderr",
                daemon=True,
            )
            self._reader.start()
            self._stderr_reader.start()
        self._initialize()

    @classmethod
    def global_config_local(
        cls,
        *,
        event_handler: Callable[[dict[str, Any]], None] | None = None,
        server_request_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        approval_request_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> "AppServerClient":
        """Launch a dedicated local app-server using the user's normal Codex config.

        v4 intentionally does not attach the execution backend to the ChatGPT
        Desktop managed socket. ChatGPT chat remains the planner/orchestrator,
        while a dedicated Codex app-server performs implementation/testing.

        The only one-shot config override disables the ReasonFirst MCP server in
        this child process to prevent recursive self-invocation. All other global
        config (model/provider, skills, rules, other MCP servers, login state,
        approval/sandbox defaults, project config) remains inherited normally.
        """
        codex_bin = resolve_desktop_or_codex_binary()
        argv = [
            codex_bin,
            "--config",
            "mcp_servers.reasonfirst.enabled=false",
            "app-server",
        ]
        return cls(
            launch_argv=argv,
            backend_name="global-config-local",
            event_handler=event_handler,
            server_request_handler=server_request_handler,
            approval_request_handler=approval_request_handler,
        )

    @classmethod
    def desktop_preferred(
        cls,
        *,
        event_handler: Callable[[dict[str, Any]], None] | None = None,
        server_request_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        approval_request_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        required: bool = False,
    ) -> "AppServerClient":
        sock = managed_app_server_socket()
        if sock.exists():
            try:
                return cls(
                    unix_socket=str(sock),
                    backend_name="desktop-managed",
                    event_handler=event_handler,
                    server_request_handler=server_request_handler,
                    approval_request_handler=approval_request_handler,
                )
            except Exception as exc:
                if required:
                    raise AppServerError(
                        f"Managed Desktop app-server socket exists but could not be used: {sock}: {exc}"
                    ) from exc
        if required:
            raise AppServerError(
                f"Managed Desktop app-server is unavailable at {sock}. "
                "Start/enable ChatGPT/Codex Desktop remote control or use desktop-preferred mode."
            )
        desktop_candidates = [
            "/Applications/ChatGPT.app/Contents/Resources/codex",
            "/Applications/Codex.app/Contents/Resources/codex",
            str(Path.home() / "Applications/ChatGPT.app/Contents/Resources/codex"),
            str(Path.home() / "Applications/Codex.app/Contents/Resources/codex"),
        ]
        for candidate in desktop_candidates:
            path = Path(candidate).expanduser()
            if path.is_file() and os.access(path, os.X_OK):
                return cls(
                    codex_bin=str(path.resolve()),
                    backend_name="desktop-bundled",
                    event_handler=event_handler,
                    server_request_handler=server_request_handler,
                    approval_request_handler=approval_request_handler,
                )
        return cls(
            event_handler=event_handler,
            server_request_handler=server_request_handler,
            approval_request_handler=approval_request_handler,
            backend_name="standalone-local",
        )

    @classmethod
    def remote_ssh(
        cls,
        host: str,
        *,
        remote_codex: str = "codex",
        event_handler: Callable[[dict[str, Any]], None] | None = None,
        server_request_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        approval_request_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        connect_timeout: int = 8,
    ) -> "AppServerClient":
        host = str(host).strip()
        if not host or host.startswith("-"):
            raise AppServerError("Invalid SSH host")
        remote_codex = str(remote_codex).strip() or "codex"
        if any(ch.isspace() for ch in remote_codex):
            raise AppServerError("remote_codex must be a single executable name/path")
        remote_command = "sh -lc " + shlex.quote(
            "exec " + shlex.quote(remote_codex) + " app-server"
        )
        argv = [
            "ssh",
            "-T",
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={max(1, min(int(connect_timeout), 30))}",
            "--",
            host,
            remote_command,
        ]
        return cls(
            launch_argv=argv,
            backend_name=f"ssh:{host}",
            event_handler=event_handler,
            server_request_handler=server_request_handler,
            approval_request_handler=approval_request_handler,
        )

    def _connect_unix_socket(self, socket_path: str) -> None:
        try:
            from websockets.sync.client import unix_connect
        except Exception as exc:  # pragma: no cover - dependency checked by launcher
            raise AppServerError(
                "Desktop-managed mode requires the 'websockets' Python package"
            ) from exc
        try:
            self.ws = unix_connect(
                path=socket_path,
                uri="ws://localhost/rpc",
                open_timeout=5,
                close_timeout=2,
                max_size=16 * 1024 * 1024,
            )
        except Exception as exc:
            raise AppServerError(f"Could not connect to managed app-server socket {socket_path}: {exc}") from exc

    def _emit_event(self, event: dict[str, Any]) -> None:
        if not self.event_handler:
            return
        try:
            self.event_handler(event)
        except Exception as exc:
            self._stderr_tail.append(
                f"bridge event handler error: {type(exc).__name__}: {str(exc)[:500]}"
            )
            del self._stderr_tail[:-20]

    def _read_stderr(self) -> None:
        if self.proc is None or self.proc.stderr is None:
            return
        for line in self.proc.stderr:
            text = line.rstrip("\r\n")
            if text:
                self._stderr_tail.append(text[:2000])
                del self._stderr_tail[:-20]

    def _is_running(self) -> bool:
        if self._closed:
            return False
        if self.ws is not None:
            return True
        return self.proc is not None and self.proc.poll() is None

    def _write(self, message: dict[str, Any]) -> None:
        if not self._is_running():
            raise AppServerError(
                f"codex app-server is not running (backend={self.backend_name}, stderr_tail={self._stderr_tail[-5:]})"
            )
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self._send_lock:
            if self.ws is not None:
                self.ws.send(payload)
            else:
                assert self.proc is not None and self.proc.stdin is not None
                self.proc.stdin.write(payload + "\n")
                self.proc.stdin.flush()

    @staticmethod
    def _is_approval_method(method: str) -> bool:
        return method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
            "item/permissions/requestApproval",
        }

    @staticmethod
    def _decline_approval_result(method: str) -> dict[str, Any]:
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            return {"decision": "decline"}
        if method == "item/permissions/requestApproval":
            return {"permissions": {}}
        return {}

    @staticmethod
    def _validate_approval_result(method: str, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise AppServerError("approval handler must return a JSON object")
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            decision = result.get("decision")
            simple = {"accept", "acceptForSession", "decline", "cancel"}
            if isinstance(decision, str) and decision in simple:
                return result
            if isinstance(decision, dict) and len(decision) == 1:
                return result
            raise AppServerError(
                f"invalid approval decision for {method}: {decision!r}"
            )
        if method == "item/permissions/requestApproval":
            permissions = result.get("permissions")
            if not isinstance(permissions, dict):
                raise AppServerError(
                    "permissions approval response must contain a permissions object"
                )
            scope = result.get("scope")
            if scope is not None and scope not in {"turn", "session"}:
                raise AppServerError("permissions approval scope must be turn or session")
            return result
        raise AppServerError(f"unsupported approval request method {method!r}")

    def _reject_server_request(self, msg: dict[str, Any]) -> None:
        method = str(msg.get("method") or "")
        rid = msg.get("id")
        if not isinstance(rid, int):
            return
        if self._is_approval_method(method):
            response: dict[str, Any] = {
                "id": rid,
                "result": self._decline_approval_result(method),
            }
        elif method in {"mcpServer/elicitation/request", "tool/requestUserInput"}:
            response = {"id": rid, "result": {"action": "decline", "content": None}}
        elif method == "item/tool/call":
            response = {"id": rid, "result": {"contentItems": [], "success": False}}
        else:
            response = {
                "id": rid,
                "error": {
                    "code": -32601,
                    "message": "ReasonFirst declines unsupported server request",
                },
            }
        try:
            self._write(response)
        except Exception:
            pass
        self._emit_event({
            "method": "bridge/serverRequestDeclined",
            "params": {"method": method},
        })

    def _handle_approval_request_async(self, msg: dict[str, Any]) -> None:
        rid = msg.get("id")
        method = str(msg.get("method") or "")
        if not isinstance(rid, int):
            return
        try:
            if self.approval_request_handler is None:
                result = self._decline_approval_result(method)
            else:
                result = self._validate_approval_result(
                    method,
                    self.approval_request_handler(msg),
                )
            self._write({"id": rid, "result": result})
            self._emit_event({
                "method": "bridge/approvalResponded",
                "params": {
                    "requestId": rid,
                    "method": method,
                    "result": result,
                },
            })
        except Exception as exc:
            try:
                self._write({
                    "id": rid,
                    "result": self._decline_approval_result(method),
                })
            except Exception:
                pass
            self._emit_event({
                "method": "bridge/approvalHandlerError",
                "params": {
                    "requestId": rid,
                    "method": method,
                    "error": f"{type(exc).__name__}: {str(exc)[:1000]}",
                },
            })

    def _handle_server_request_async(self, msg: dict[str, Any]) -> None:
        rid = msg.get("id")
        if not isinstance(rid, int):
            return
        try:
            if self.server_request_handler is None:
                self._reject_server_request(msg)
                return
            result = self.server_request_handler(msg)
            if not isinstance(result, dict):
                result = {
                    "contentItems": [
                        {"type": "inputText", "text": str(result)}
                    ],
                    "success": True,
                }
            self._write({"id": rid, "result": result})
        except Exception as exc:
            try:
                self._write({
                    "id": rid,
                    "result": {
                        "contentItems": [{
                            "type": "inputText",
                            "text": (
                                "ReasonFirst tool error: "
                                f"{type(exc).__name__}: {str(exc)[:2000]}"
                            ),
                        }],
                        "success": False,
                    },
                })
            except Exception:
                pass

    def _handle_message(self, msg: Any) -> None:
        if not isinstance(msg, dict):
            return
        rid = msg.get("id")
        method = msg.get("method")
        if isinstance(rid, int) and isinstance(method, str):
            if self._is_approval_method(method):
                threading.Thread(
                    target=self._handle_approval_request_async,
                    args=(msg,),
                    name="codex-approval-request",
                    daemon=True,
                ).start()
            elif method == "item/tool/call" and self.server_request_handler is not None:
                threading.Thread(
                    target=self._handle_server_request_async,
                    args=(msg,),
                    name="codex-dynamic-tool",
                    daemon=True,
                ).start()
            else:
                self._reject_server_request(msg)
            return
        if isinstance(rid, int):
            with self._pending_lock:
                waiter = self._pending.get(rid)
            if waiter is not None:
                waiter.put(msg)
            return
        if isinstance(method, str):
            if method == "model/rerouted":
                params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
                thread_id = str(params.get("threadId") or "")
                if thread_id:
                    self._thread_policy_violations.setdefault(thread_id, []).append({
                        "type": "model_rerouted",
                        "params": params,
                    })
            self._emit_event(msg)

    def _mark_closed(self) -> None:
        self._closed = True
        error = {
            "error": {
                "code": -32099,
                "message": "codex app-server exited",
                "backend": self.backend_name,
                "stderr_tail": self._stderr_tail[-5:],
            }
        }
        with self._pending_lock:
            waiters = list(self._pending.values())
        for waiter in waiters:
            try:
                waiter.put_nowait(error)
            except queue.Full:
                pass

    def _read_stdio_loop(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        try:
            for line in self.proc.stdout:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    self._handle_message(json.loads(raw))
                except json.JSONDecodeError:
                    self._emit_event({"method": "bridge/protocolError", "params": {"message": "invalid JSON from app-server"}})
        finally:
            self._mark_closed()

    def _read_ws_loop(self) -> None:
        try:
            assert self.ws is not None
            for raw in self.ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                try:
                    self._handle_message(json.loads(str(raw)))
                except json.JSONDecodeError:
                    self._emit_event({"method": "bridge/protocolError", "params": {"message": "invalid JSON from managed app-server"}})
        except Exception as exc:
            self._stderr_tail.append(f"managed socket reader error: {type(exc).__name__}: {str(exc)[:1000]}")
        finally:
            self._mark_closed()

    def request(self, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None) -> Any:
        with self._pending_lock:
            rid = self._next_id
            self._next_id += 1
            waiter: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[rid] = waiter
        try:
            self._write({"method": method, "id": rid, "params": params or {}})
            try:
                msg = waiter.get(timeout=timeout or self.request_timeout)
            except queue.Empty as exc:
                raise AppServerError(f"Timed out waiting for app-server method {method}") from exc
        finally:
            with self._pending_lock:
                self._pending.pop(rid, None)
        if "error" in msg:
            detail = msg["error"]
            if isinstance(detail, dict) and detail.get("code") == -32099:
                raise AppServerError(
                    f"app-server exited while handling {method}; backend={self.backend_name}; "
                    f"stderr_tail={detail.get('stderr_tail') or self._stderr_tail[-5:]}"
                )
            raise AppServerError(f"app-server {method} failed: {detail}")
        return msg.get("result")

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._write({"method": method, "params": params or {}})

    def _initialize(self) -> None:
        self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "reasonfirst_codex_web_bridge",
                    "title": "ReasonFirst Codex Web Bridge",
                    "version": __version__,
                },
                "capabilities": {"experimentalApi": True},
            },
            timeout=30,
        )
        self.notify("initialized", {})

    def admin_requirements(self) -> dict[str, Any]:
        try:
            result = self.request("configRequirements/read", {}, timeout=15)
        except AppServerError:
            return {}
        return result if isinstance(result, dict) else {}

    def assert_noninteractive_policy_allowed(
        self,
        *,
        sandbox_mode: str = "workspace-write",
        approval_policy: str = "unlessTrusted",
    ) -> None:
        data = self.admin_requirements()
        req = data.get("requirements") if isinstance(data, dict) else None
        if not isinstance(req, dict):
            return
        allowed = req.get("allowedApprovalPolicies")
        if isinstance(allowed, list) and allowed:
            normalized = {str(item).replace("-", "").lower() for item in allowed}
            wanted = str(approval_policy).replace("-", "").lower()
            if wanted not in normalized:
                raise AppServerError(
                    f"Codex admin requirements do not allow approvalPolicy={approval_policy}."
                )
        sandbox_modes = req.get("allowedSandboxModes")
        if isinstance(sandbox_modes, list) and sandbox_modes:
            normalized = {str(item).replace("_", "-").replace("workspaceWrite", "workspace-write").replace("readOnly", "read-only").lower() for item in sandbox_modes}
            wanted = sandbox_mode.replace("_", "-").replace("workspaceWrite", "workspace-write").replace("readOnly", "read-only").lower()
            if wanted not in normalized:
                raise AppServerError(
                    f"Codex admin requirements do not allow {sandbox_mode} sandbox mode."
                )

    @staticmethod
    def _approval_policy(policy: WorkerPolicy) -> str:
        # App Server names the interactive approval mode "unlessTrusted".
        return "never" if policy.approval_policy == "never" else "unlessTrusted"

    @staticmethod
    def _sandbox_mode(policy: WorkerPolicy, override: str | None = None) -> str:
        value = override or policy.sandbox_mode or "workspace-write"
        if value not in {"read-only", "workspace-write"}:
            raise AppServerError(f"Unsupported WorkerPolicy sandbox for App Server: {value!r}")
        return value

    def model_catalog(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(10):
            params: dict[str, Any] = {"limit": 100, "includeHidden": True}
            if cursor:
                params["cursor"] = cursor
            result = self.request("model/list", params, timeout=30)
            if not isinstance(result, dict):
                raise AppServerError("model/list returned an invalid response")
            data = result.get("data")
            if not isinstance(data, list):
                raise AppServerError("model/list returned no model catalog")
            items.extend(item for item in data if isinstance(item, dict))
            next_cursor = result.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            cursor = next_cursor
        return items

    def assert_worker_policy_supported(
        self,
        policy: WorkerPolicy,
        *,
        sandbox_mode: str | None = None,
    ) -> dict[str, Any]:
        mode = self._sandbox_mode(policy, sandbox_mode)
        approval = self._approval_policy(policy)
        self.assert_noninteractive_policy_allowed(
            sandbox_mode=mode,
            approval_policy=approval,
        )

        if not policy.model:
            raise AppServerError(
                "WORKER_POLICY_UNSATISFIED: Codex Desktop requires an explicit model"
            )
        if not policy.reasoning_effort:
            raise AppServerError(
                "WORKER_POLICY_UNSATISFIED: Codex Desktop requires explicit reasoning effort"
            )

        catalog = self.model_catalog()
        selected = next(
            (
                item for item in catalog
                if str(item.get("id") or "") == policy.model
                or str(item.get("model") or "") == policy.model
            ),
            None,
        )
        if selected is None:
            raise AppServerError(
                "WORKER_POLICY_UNSATISFIED: requested model "
                f"{policy.model!r} is not in the App Server model catalog"
            )

        efforts = selected.get("supportedReasoningEfforts")
        supported_efforts = {
            str(item.get("reasoningEffort"))
            for item in efforts
            if isinstance(item, dict) and item.get("reasoningEffort")
        } if isinstance(efforts, list) else set()
        if policy.reasoning_effort not in supported_efforts:
            raise AppServerError(
                "WORKER_POLICY_UNSATISFIED: requested reasoning effort "
                f"{policy.reasoning_effort!r} is not supported by {policy.model!r}; "
                f"supported={sorted(supported_efforts)}"
            )

        return {
            "verification_scope": "app-server-catalog-and-resolved-thread",
            "requested": policy.to_dict(),
            "catalog_model_id": selected.get("id"),
            "catalog_model": selected.get("model"),
            "supported_reasoning_efforts": sorted(supported_efforts),
            "sandbox_mode": mode,
            "approval_policy": approval,
        }

    @staticmethod
    def _sandbox_response_mode(value: Any) -> str | None:
        if isinstance(value, str):
            raw = value
        elif isinstance(value, dict):
            raw = str(value.get("type") or "")
        else:
            return None
        normalized = raw.replace("_", "-")
        normalized = normalized.replace("workspaceWrite", "workspace-write")
        normalized = normalized.replace("readOnly", "read-only")
        return normalized.lower()

    def _verify_thread_resolution(
        self,
        *,
        result: dict[str, Any],
        policy: WorkerPolicy,
        expected_sandbox: str,
        expected_approval: str,
        evidence: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
        tid = thread.get("id")
        if not isinstance(tid, str) or not tid:
            raise AppServerError("thread/start returned no thread id")

        resolved_model = (
            thread.get("model")
            or result.get("model")
        )
        resolved_effort = (
            thread.get("reasoningEffort")
            or result.get("reasoningEffort")
        )
        resolved_approval = result.get("approvalPolicy")
        resolved_sandbox = self._sandbox_response_mode(result.get("sandbox"))

        mismatches: list[str] = []
        if str(resolved_model or "") != str(policy.model or ""):
            mismatches.append(
                f"model requested={policy.model!r} resolved={resolved_model!r}"
            )
        if str(resolved_effort or "") != str(policy.reasoning_effort or ""):
            mismatches.append(
                "reasoning_effort "
                f"requested={policy.reasoning_effort!r} resolved={resolved_effort!r}"
            )
        if resolved_approval is not None:
            normalized = str(resolved_approval).replace("-", "").lower()
            wanted = expected_approval.replace("-", "").lower()
            if normalized != wanted:
                mismatches.append(
                    f"approval requested={expected_approval!r} resolved={resolved_approval!r}"
                )
        if resolved_sandbox is not None and resolved_sandbox != expected_sandbox:
            mismatches.append(
                f"sandbox requested={expected_sandbox!r} resolved={resolved_sandbox!r}"
            )
        if mismatches:
            raise AppServerError(
                "WORKER_POLICY_UNSATISFIED: " + "; ".join(mismatches)
            )

        verified = {
            **evidence,
            "satisfied": True,
            "resolved": {
                "model": resolved_model,
                "reasoning_effort": resolved_effort,
                "approval_policy": resolved_approval,
                "sandbox_mode": resolved_sandbox,
            },
            "provider_reported_model_verified": False,
            "provider_reported_model_note": (
                "Current App Server protocol exposes configured/resolved thread model, "
                "not the provider response envelope model."
            ),
        }
        self._thread_policy_evidence[tid] = verified
        self._thread_policy_violations.setdefault(tid, [])
        return tid, verified

    def worker_policy_evidence(self, thread_id: str) -> dict[str, Any]:
        evidence = dict(self._thread_policy_evidence.get(thread_id) or {})
        violations = list(self._thread_policy_violations.get(thread_id) or [])
        evidence["runtime_violations"] = violations
        evidence["satisfied"] = bool(evidence.get("satisfied")) and not violations
        return evidence

    def start_thread(
        self,
        *,
        cwd: str,
        policy: WorkerPolicy | None = None,
        dynamic_tools: list[dict[str, Any]] | None = None,
        sandbox_mode: str | None = None,
    ) -> str:
        resolved = policy or default_worker_policy("codex")
        mode = self._sandbox_mode(resolved, sandbox_mode)
        approval = self._approval_policy(resolved)
        evidence = self.assert_worker_policy_supported(
            resolved,
            sandbox_mode=mode,
        )
        params: dict[str, Any] = {
            "cwd": cwd,
            "approvalPolicy": approval,
            "sandbox": "readOnly" if mode == "read-only" else "workspaceWrite",
            "serviceName": "reasonfirst_codex_desktop",
            "threadSource": "user",
        }
        if resolved.model:
            params["model"] = resolved.model
        if dynamic_tools:
            params["dynamicTools"] = dynamic_tools
        result = self.request(
            "thread/start",
            params,
            timeout=30,
        )
        if not isinstance(result, dict):
            raise AppServerError("thread/start returned an invalid response")
        tid, _verified = self._verify_thread_resolution(
            result=result,
            policy=resolved,
            expected_sandbox=mode,
            expected_approval=approval,
            evidence=evidence,
        )
        return tid

    def resume_thread(self, thread_id: str) -> None:
        self.request("thread/resume", {"threadId": thread_id}, timeout=30)

    def start_turn(
        self,
        *,
        thread_id: str,
        cwd: str,
        prompt: str,
        policy: WorkerPolicy | None = None,
        network_access: bool | None = None,
        sandbox_mode: str | None = None,
    ) -> str:
        resolved = policy or default_worker_policy("codex")
        mode = self._sandbox_mode(resolved, sandbox_mode)
        effective_network = (
            bool(resolved.network_access)
            if network_access is None
            else bool(network_access)
        )
        params = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
            "cwd": cwd,
            "approvalPolicy": self._approval_policy(resolved),
            "sandboxPolicy": (
                {"type": "readOnly", "networkAccess": effective_network}
                if mode == "read-only"
                else {"type": "workspaceWrite", "writableRoots": [cwd], "networkAccess": effective_network}
            ),
        }
        if resolved.model:
            params["model"] = resolved.model
        if resolved.reasoning_effort:
            params["effort"] = resolved.reasoning_effort
        result = self.request("turn/start", params, timeout=30)
        turn = result.get("turn") if isinstance(result, dict) else None
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if not isinstance(turn_id, str) or not turn_id:
            raise AppServerError("turn/start returned no turn id")
        return turn_id

    def steer(self, *, thread_id: str, turn_id: str, prompt: str) -> str:
        result = self.request(
            "turn/steer",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": prompt}],
                "expectedTurnId": turn_id,
            },
            timeout=30,
        )
        accepted = result.get("turnId") if isinstance(result, dict) else None
        return str(accepted or turn_id)

    def interrupt(self, *, thread_id: str, turn_id: str) -> None:
        self.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=30)

    def read_thread(self, thread_id: str, *, include_turns: bool = False) -> dict[str, Any]:
        result = self.request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": bool(include_turns)},
            timeout=30,
        )
        return result if isinstance(result, dict) else {}

    def set_thread_name(self, thread_id: str, name: str) -> None:
        value = str(name).strip()[:200]
        if value:
            self.request("thread/name/set", {"threadId": thread_id, "name": value}, timeout=30)

    def set_thread_goal(self, thread_id: str, objective: str) -> dict[str, Any]:
        value = str(objective).strip()[:4000]
        if not value:
            return {}
        result = self.request(
            "thread/goal/set",
            {"threadId": thread_id, "objective": value, "status": "active"},
            timeout=30,
        )
        return result if isinstance(result, dict) else {}

    def update_thread_metadata(
        self,
        thread_id: str,
        *,
        branch: str = "",
        sha: str = "",
        origin_url: str = "",
        is_pinned: bool = True,
    ) -> dict[str, Any]:
        git_info: dict[str, Any] = {}
        if branch:
            git_info["branch"] = branch
        if sha:
            git_info["sha"] = sha
        if origin_url:
            git_info["originUrl"] = origin_url
        params: dict[str, Any] = {"threadId": thread_id, "isPinned": bool(is_pinned)}
        if git_info:
            params["gitInfo"] = git_info
        result = self.request("thread/metadata/update", params, timeout=30)
        return result if isinstance(result, dict) else {}

    def list_threads(self, *, search_term: str = "", limit: int = 50) -> dict[str, Any]:
        params: dict[str, Any] = {
            "limit": max(1, min(int(limit), 100)),
            "sourceKinds": ["appServer", "cli", "vscode", "user"],
            "sortKey": "updated_at",
            "sortDirection": "desc",
        }
        if search_term:
            params["searchTerm"] = search_term
        result = self.request("thread/list", params, timeout=30)
        return result if isinstance(result, dict) else {}

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
            return
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
