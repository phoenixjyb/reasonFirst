from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
from urllib.parse import unquote, urlparse

from .app_server import AppServerClient, AppServerError, managed_app_server_socket, resolve_codex_binary
from .artifacts import artifact_file, scan_artifacts
from .bridge_config import ExecutionTarget, config_path, load_bridge_config, resolve_target
from .remote_workspace import RemoteWorkspaceManager
from gitlab_agent.config import AgentSettings
from gitlab_agent.worker_policy import WorkerPolicy, resolve_worker_policy


class BridgeError(RuntimeError):
    pass


SECRET_PATTERNS = [
    (re.compile(r"glpat-[A-Za-z0-9_-]{12,}"), "glpat-[REDACTED]"),
    (re.compile(r"(?i)(PRIVATE-TOKEN\s*[:=]\s*)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:GITLAB_TOKEN|GITLAB_GIT_TOKEN|GITLAB_GIT_PASSWORD|CONTROL_PLANE_API_KEY)\s*=\s*)\S+"), r"\1[REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), "sk-[REDACTED]"),
]


def redact(text: str, limit: int = 4000) -> str:
    out = text
    for pattern, replacement in SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out[:limit]


def _private_state_dir() -> Path:
    raw = os.getenv("RF_CODEX_BRIDGE_STATE_DIR", "~/.local/share/reasonfirst/codex-web-bridge")
    path = Path(raw).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise BridgeError(f"State path is not a directory: {path}")
    return path


def _module_command(module: str, *args: str) -> list[str]:
    return [sys.executable, "-m", module, *args]


def _run_json(argv: list[str], *, timeout: int = 360, allow_failure_json: bool = False) -> dict[str, Any]:
    try:
        proc = subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=False, env=os.environ.copy())
    except subprocess.TimeoutExpired as exc:
        raise BridgeError(f"Command timed out: {argv[0]} ...") from exc
    except OSError as exc:
        raise BridgeError(f"Could not launch command: {argv[0]}") from exc
    stdout = proc.stdout.strip()
    try:
        data = json.loads(stdout) if stdout else {}
    except json.JSONDecodeError as exc:
        raise BridgeError(f"Expected JSON from command, got invalid output. stderr={redact(proc.stderr)}") from exc
    if proc.returncode != 0 and not allow_failure_json:
        detail = data if data else redact(proc.stderr)
        raise BridgeError(f"Command failed with exit {proc.returncode}: {detail}")
    if not isinstance(data, dict):
        raise BridgeError("Expected a JSON object from ReasonFirst command")
    data.setdefault("_returncode", proc.returncode)
    return data


class BridgeController:
    def __init__(self) -> None:
        self.state_dir = _private_state_dir()
        self.state_file = self.state_dir / "state.json"
        self._lock = threading.RLock()
        self._state = self._load_state()
        self.bridge_config = load_bridge_config()
        self._apps: dict[str, AppServerClient] = {}
        self._app_current_thread: dict[str, str] = {}

    def _load_state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {"version": 3, "sessions": {}, "workspaces": {}, "finish_approvals": {}}
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BridgeError(f"Could not read bridge state: {self.state_file}") from exc
        if not isinstance(data, dict) or int(data.get("version", 0)) not in {2, 3, 4}:
            raise BridgeError("Unsupported bridge state version")
        if int(data.get("version", 0)) == 2:
            data["version"] = 3
            for ws in data.get("workspaces", {}).values():
                if isinstance(ws, dict):
                    ws.setdefault("kind", "local")
                    ws.setdefault("target", {"type": "local", "name": "local", "codex_backend": "desktop-preferred"})
            for session in data.get("sessions", {}).values():
                if isinstance(session, dict):
                    session.setdefault("target", {"type": "local", "name": "local", "codex_backend": "desktop-preferred"})
        data["version"] = 3
        data.setdefault("sessions", {})
        data.setdefault("workspaces", {})
        data.setdefault("finish_approvals", {})
        return data

    def _save_state(self) -> None:
        payload = json.dumps(self._state, ensure_ascii=False, indent=2, sort_keys=True)
        fd, temp_name = tempfile.mkstemp(prefix="state.", suffix=".tmp", dir=self.state_dir)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload); f.flush(); os.fsync(f.fileno())
            os.replace(temp_name, self.state_file)
            try: self.state_file.chmod(0o600)
            except OSError: pass
        finally:
            if os.path.exists(temp_name): os.unlink(temp_name)

    def _session(self, thread_id: str) -> dict[str, Any]:
        session = self._state["sessions"].get(thread_id)
        if not isinstance(session, dict):
            raise BridgeError(f"Unknown thread_id: {thread_id}")
        return session

    def _workspace_record(self, wid: str) -> dict[str, Any]:
        item = self._state.get("workspaces", {}).get(wid)
        if not isinstance(item, dict):
            raise BridgeError(f"Unknown bridge workspace_id: {wid}")
        return item

    def _target_from_dict(self, data: Any) -> ExecutionTarget:
        return resolve_target(data, config=self.bridge_config)

    def _resolve_requested_target(self, execution: Any = None) -> ExecutionTarget:
        """Resolve only user-owned configured targets at the MCP trust boundary."""
        defaults = (
            self.bridge_config.get("defaults")
            if isinstance(self.bridge_config.get("defaults"), dict)
            else {}
        )
        requested = execution
        if requested in (None, "", {}):
            requested = str(defaults.get("target") or "local")
        if isinstance(requested, dict):
            raise BridgeError(
                "Inline execution target objects are not allowed from MCP requests. "
                "Configure the target in bridge.yaml and select it by name."
            )
        if not isinstance(requested, str):
            raise BridgeError("execution target must be a configured target name")
        name = requested.strip()
        if not name:
            name = str(defaults.get("target") or "local")
        if ":" in name:
            raise BridgeError(
                "Inline SSH shorthand is not allowed from MCP requests. "
                "Configure a named target in bridge.yaml."
            )
        targets = (
            self.bridge_config.get("targets")
            if isinstance(self.bridge_config.get("targets"), dict)
            else {}
        )
        if name != "local" and name not in targets:
            raise BridgeError(
                f"Unknown execution target {name!r}; only user-configured target names are allowed"
            )
        return resolve_target(name, config=self.bridge_config)

    def _remote_manager(self, target: ExecutionTarget) -> RemoteWorkspaceManager:
        # Reuse the already-configured ReasonFirst Git credential for remote HTTPS
        # fetches without persisting it on the target. The remote manager forwards
        # it only after a credential-less fetch fails and only to the configured
        # GitLab host.
        try:
            from gitlab_agent.config import AgentSettings
            settings = AgentSettings.load()
            host = (urlparse(settings.gitlab_base_url).hostname or "").lower()
            return RemoteWorkspaceManager(
                target,
                gitlab_host=host,
                git_username=settings.git_username,
                git_password=settings.git_token,
            )
        except Exception:
            return RemoteWorkspaceManager(target)

    def _is_remote_proxy_target(self, target: ExecutionTarget) -> bool:
        return target.type == "ssh" and target.codex_backend != "remote-ssh"

    @staticmethod
    def _probe_remote_codex_path(probe: dict[str, Any]) -> str:
        stdout = str(probe.get("stdout") or "")
        for line in stdout.splitlines():
            if line.startswith("codex="):
                return line.split("=", 1)[1].strip()
        return ""

    def _migrate_legacy_remote_target_if_needed(
        self, workspace_id: str, rec: dict[str, Any], target: ExecutionTarget
    ) -> tuple[ExecutionTarget, bool]:
        """Reuse v3.0.2 SSH workspaces when the remote host has no Codex.

        v3.0.2 stored SSH targets as ``remote-ssh``. v3.0.3 defaults to a
        local Desktop/app-server proxy. When an existing workspace still says
        ``remote-ssh`` but the target has no Codex binary, transparently migrate
        only the execution backend; the remote worktree, branch and base SHA stay
        untouched.
        """
        if rec.get("kind") != "ssh" or target.codex_backend != "remote-ssh":
            return target, False
        probe = self._remote_manager(target).probe()
        if self._probe_remote_codex_path(probe):
            return target, False
        migrated = ExecutionTarget(
            type="ssh",
            name=target.name,
            host=target.host,
            repo=target.repo,
            worker_backend=target.worker_backend,
            codex_backend="desktop-proxy",
            remote_codex=target.remote_codex,
            ssh_connect_timeout=target.ssh_connect_timeout,
            network_access=False,
        )
        with self._lock:
            rec["target"] = migrated.to_dict()
            rec["updated_at"] = int(time.time())
            rec["execution_migration"] = {
                "from": "remote-ssh",
                "to": "desktop-proxy",
                "reason": "remote Codex binary not found",
                "at": int(time.time()),
            }
            self._state["workspaces"][workspace_id] = rec
            self._save_state()
        return migrated, True

    def _resolved_worker_policy(self, target: ExecutionTarget) -> WorkerPolicy:
        """Resolve the user-owned worker policy for the configured execution backend."""
        backend = str(target.worker_backend or "codex-desktop")
        provider = {
            "codex-cli": "codex",
            "codex-desktop": "codex",
            "copilot-cli": "copilot",
        }.get(backend)
        if provider is None:
            raise BridgeError(f"Unsupported worker backend: {backend!r}")
        try:
            return resolve_worker_policy(AgentSettings.load(), provider)
        except Exception as exc:
            raise BridgeError(
                f"Could not resolve WorkerPolicy for {backend}: {redact(str(exc), 1200)}"
            ) from exc

    def _require_codex_desktop_backend(self, target: ExecutionTarget) -> WorkerPolicy:
        if target.worker_backend != "codex-desktop":
            raise BridgeError(
                "This App Server route requires worker_backend='codex-desktop'. "
                "Use ActualCoder's existing CLI launch path for codex-cli or copilot-cli."
            )
        policy = self._resolved_worker_policy(target)
        if policy.backend != "codex":
            raise BridgeError("codex-desktop resolved to a non-Codex WorkerPolicy")
        return policy

    def _app_key(self, target: ExecutionTarget) -> str:
        if target.type == "ssh" and target.codex_backend == "remote-ssh":
            return f"ssh:{target.host}:{target.remote_codex}"
        if target.type == "ssh":
            return f"proxy:{target.codex_backend}"
        return f"local:{target.codex_backend}"

    def _get_app(self, target: ExecutionTarget) -> tuple[str, AppServerClient]:
        key = self._app_key(target)
        existing = self._apps.get(key)
        if existing is not None:
            return key, existing
        handler = lambda event, app_key=key: self._on_event(event, app_key)
        request_handler = lambda msg, app_key=key: self._handle_dynamic_tool_request(app_key, msg)
        if target.type == "ssh" and target.codex_backend == "remote-ssh":
            app = AppServerClient.remote_ssh(
                target.host,
                remote_codex=target.remote_codex,
                event_handler=handler,
                server_request_handler=request_handler,
                connect_timeout=target.ssh_connect_timeout,
            )
        elif target.codex_backend in {"global-config-local", "desktop-proxy"}:
            app = AppServerClient.global_config_local(
                event_handler=handler, server_request_handler=request_handler
            )
        elif target.codex_backend == "desktop-required":
            app = AppServerClient.desktop_preferred(
                event_handler=handler, server_request_handler=request_handler, required=True
            )
        elif target.codex_backend == "desktop-managed":
            app = AppServerClient.desktop_preferred(
                event_handler=handler, server_request_handler=request_handler, required=True
            )
        elif target.codex_backend == "standalone-local":
            app = AppServerClient(
                event_handler=handler,
                server_request_handler=request_handler,
                backend_name="standalone-local",
            )
        else:
            # v3 compatibility. Prefer v4 dedicated global-config app-server.
            app = AppServerClient.global_config_local(
                event_handler=handler, server_request_handler=request_handler
            )
        self._apps[key] = app
        return key, app

    def _proxy_workspace(self, workspace_id: str, rec: dict[str, Any]) -> str:
        root = self.state_dir / "proxy" / workspace_id
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            root.chmod(0o700)
        except OSError:
            pass
        note = root / "REMOTE_WORKSPACE.md"
        note.write_text(
            "# ReasonFirst remote workspace proxy\n\n"
            "This local directory is a control surface only. The source of truth is remote.\n\n"
            f"Project: {rec.get('project')}\n"
            f"Remote host: {(rec.get('target') or {}).get('host')}\n"
            f"Remote worktree: {rec.get('worktree_path')}\n"
            f"Branch: {rec.get('branch')}\n"
            f"Base SHA: {rec.get('base_sha')}\n\n"
            "Use the ReasonFirst remote dynamic tools for all source reads, writes, commands and diffs.\n",
            encoding="utf-8",
        )
        return str(root.resolve())

    @staticmethod
    def _remote_dynamic_tools() -> list[dict[str, Any]]:
        def fn(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
            schema: dict[str, Any] = {
                "type": "object",
                "properties": properties,
                "additionalProperties": False,
            }
            if required:
                schema["required"] = required
            return {
                "type": "function",
                "name": name,
                "description": description,
                "inputSchema": schema,
            }
        return [{
            "type": "namespace",
            "name": "reasonfirst_remote",
            "description": "Operate only on the managed remote ReasonFirst worktree selected for this task.",
            "tools": [
                fn("status", "Read the remote Git worktree status and pinned branch/base information.", {}),
                fn("files", "List files inside the remote managed worktree.", {
                    "path": {"type": "string"},
                    "recursive": {"type": "boolean"},
                    "max_entries": {"type": "integer", "minimum": 1, "maximum": 500},
                }),
                fn("read", "Read one UTF-8 source/config/test file from the remote managed worktree.", {
                    "path": {"type": "string"},
                    "max_bytes": {"type": "integer", "minimum": 1, "maximum": 2097152},
                }, ["path"]),
                fn("write", "Replace one file inside the remote managed worktree. Parent directories may be created.", {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                }, ["path", "content"]),
                fn("apply_patch", "Apply a unified Git patch to the remote managed worktree.", {
                    "patch": {"type": "string"},
                }, ["patch"]),
                fn("run", "Run one locally-approved build/test executable on the remote host without a shell. The target defaults to no executable authority.", {
                    "argv": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 128
                    },
                    "cwd": {"type": "string"},
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 1800},
                }, ["argv"]),
                fn("snapshot", "Read the exact remote review snapshot digest. Use before requesting ChatGPT push approval.", {}),
                fn("commit_push", "Commit and push the exact ChatGPT-approved snapshot. This succeeds only after an explicit ReasonFirst push approval for the unchanged digest; force-push and protected branches are never allowed.", {}),
                fn("diff", "Read the real remote Git diff against the pinned base SHA.", {}),
            ],
        }]

    def _handle_dynamic_tool_request(self, app_key: str, msg: dict[str, Any]) -> dict[str, Any]:
        if str(msg.get("method") or "") != "item/tool/call":
            raise BridgeError("Unsupported dynamic server request")
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        thread_id = str(params.get("threadId") or "")
        namespace = str(params.get("namespace") or "")
        tool = str(params.get("tool") or "")
        args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if namespace != "reasonfirst_remote":
            raise BridgeError(f"Unsupported dynamic tool namespace: {namespace!r}")
        session = self._session(thread_id)
        if str(session.get("app_key") or "") != app_key:
            raise BridgeError("Dynamic tool request arrived on the wrong app-server")
        rec = self._workspace_record(str(session["workspace_id"]))
        if rec.get("kind") != "ssh":
            raise BridgeError("ReasonFirst remote tools require an SSH workspace")
        target = self._target_from_dict(rec.get("target") or {})
        manager = self._remote_manager(target)
        if tool == "status":
            result = manager.status(rec)
        elif tool == "files":
            result = manager.list_files(
                rec,
                str(args.get("path") or "."),
                recursive=bool(args.get("recursive", False)),
                max_entries=int(args.get("max_entries") or 200),
            )
        elif tool == "read":
            result = manager.read_file(
                rec,
                str(args.get("path") or ""),
                max_bytes=int(args.get("max_bytes") or 1024 * 1024),
            )
        elif tool == "write":
            result = manager.write_file(rec, str(args.get("path") or ""), str(args.get("content") or ""))
        elif tool == "apply_patch":
            result = manager.apply_patch(rec, str(args.get("patch") or ""))
        elif tool == "run":
            raw_argv = args.get("argv")
            if not isinstance(raw_argv, list):
                raise BridgeError("reasonfirst_remote.run requires argv as a list")
            result = manager.run_argv(
                rec,
                [str(item) for item in raw_argv],
                cwd=str(args.get("cwd") or "."),
                timeout_seconds=int(args.get("timeout_seconds") or 300),
            )
        elif tool == "snapshot":
            result = manager.snapshot(rec)
        elif tool == "commit_push":
            approval = session.get("push_approval") if isinstance(session.get("push_approval"), dict) else None
            if not approval:
                raise BridgeError("Push is not authorized. ChatGPT must review the current diff and call authorize_push first.")
            result = manager.commit_push(
                rec,
                expected_digest=str(approval.get("digest") or ""),
                expected_tree=str(approval.get("candidate_tree") or ""),
                expected_branch=str(approval.get("branch") or ""),
                expected_origin=str(approval.get("origin_url") or ""),
                expected_head=str(approval.get("head") or ""),
                message=str(approval.get("message") or ""),
            )
            with self._lock:
                session.pop("push_approval", None)
                session["last_push"] = {
                    "commit_sha": result.get("commit_sha"),
                    "branch": result.get("branch"),
                    "at": int(time.time()),
                }
                rec["pushed"] = True
                rec["last_commit"] = result.get("commit_sha")
                rec["remote_branch"] = result.get("branch")
                self._state["workspaces"][str(session["workspace_id"])] = rec
                self._save_state()
        elif tool == "diff":
            result = manager.diff(rec)
        else:
            raise BridgeError(f"Unknown ReasonFirst remote tool: {tool}")
        text = redact(json.dumps(result, ensure_ascii=False, default=str), 50000)
        return {"contentItems": [{"type": "inputText", "text": text}], "success": True}

    def _app_for_session(self, session: dict[str, Any]) -> tuple[str, AppServerClient]:
        target = self._target_from_dict(session.get("target") or "local")
        key, app = self._get_app(target)
        tid = str(session["thread_id"])
        if key not in self._app_current_thread or self._app_current_thread.get(key) != tid:
            try:
                app.resume_thread(tid)
            except Exception:
                # The thread may already be loaded by this app-server. A read verifies it.
                app.read_thread(tid, include_turns=False)
            self._app_current_thread[key] = tid
        return key, app

    def _allowed_workspace_root(self) -> Path:
        data = _run_json(_module_command("gitlab_agent.actual_coder_cli", "config"), timeout=60)
        root = data.get("workspace_root")
        if not isinstance(root, str) or not root:
            raise BridgeError("actual-coder config returned no workspace_root")
        return Path(root).expanduser().resolve()

    def _assert_worktree_allowed(self, worktree: str) -> str:
        path = Path(worktree).expanduser().resolve()
        root = self._allowed_workspace_root()
        try: path.relative_to(root)
        except ValueError as exc: raise BridgeError(f"Worktree {path} is outside ReasonFirst workspace_root {root}") from exc
        if not path.is_dir(): raise BridgeError(f"Worktree does not exist: {path}")
        return str(path)

    def _reasonfirst_config(self) -> dict[str, Any]:
        return _run_json(_module_command("gitlab_agent.actual_coder_cli", "config"), timeout=60)

    def _git_only_mode(self) -> bool:
        forced = os.getenv("RF_GITLAB_AUTH_MODE", "").strip().lower()
        if forced in {"git-only", "git_only", "password", "git"}: return True
        if forced in {"api", "token", "full"}: return False
        return not bool(self._reasonfirst_config().get("api_token_set"))

    def doctor(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": True,
            "bridge_version": "4.0.3",
            "config_file": str(config_path()),
            "codex_bin": None,
            "desktop_managed_socket": str(managed_app_server_socket()),
            "desktop_managed_socket_exists": managed_app_server_socket().exists(),
            "reasonfirst": {},
            "targets": {},
        }
        try: result["codex_bin"] = resolve_codex_binary()
        except Exception as exc: result["ok"] = False; result["codex_error"] = str(exc)
        try:
            cfg = self._reasonfirst_config()
            result["gitlab_auth_mode"] = "git-only" if not bool(cfg.get("api_token_set")) else "api+git"
            result["git_credential_configured"] = bool(cfg.get("git_token_set"))
            argv = _module_command("gitlab_agent.actual_coder_cli", "doctor", "--offline")
            if self._git_only_mode(): argv.append("--git-only")
            rf = _run_json(argv, timeout=60, allow_failure_json=True)
            result["reasonfirst"] = rf
            if not bool(rf.get("ok")): result["ok"] = False
        except Exception as exc:
            result["ok"] = False; result["reasonfirst_error"] = str(exc)
        targets = self.bridge_config.get("targets", {})
        if isinstance(targets, dict):
            for name in targets:
                try:
                    target = resolve_target(name, config=self.bridge_config)
                    if target.type == "ssh":
                        result["targets"][name] = self._remote_manager(target).probe()
                    else:
                        result["targets"][name] = {"ok": True, "target": target.to_dict()}
                except Exception as exc:
                    result["targets"][name] = {"ok": False, "error": redact(str(exc), 2000)}
        return result

    def target_probe(self, execution: Any = None) -> dict[str, Any]:
        target = self._resolve_requested_target(execution)
        if target.type == "ssh":
            result = self._remote_manager(target).probe()
            result["remote_codex_required"] = target.codex_backend == "remote-ssh"
            result["codex_execution"] = (
                "remote" if target.codex_backend == "remote-ssh" else "local-desktop-proxy"
            )
            if target.codex_backend != "remote-ssh":
                try:
                    result["local_codex_bin"] = resolve_codex_binary()
                except Exception as exc:
                    result["local_codex_error"] = str(exc)
                    result["ok"] = False
                result["managed_socket"] = str(managed_app_server_socket())
                result["managed_socket_exists"] = managed_app_server_socket().exists()
            return result
        return {
            "ok": True,
            "target": target.to_dict(),
            "codex_bin": resolve_codex_binary(),
            "managed_socket": str(managed_app_server_socket()),
            "managed_socket_exists": managed_app_server_socket().exists(),
            "desktop_preferred": target.codex_backend in {"desktop-preferred", "desktop-required"},
        }

    def parse_gitlab_url(self, gitlab_url: str) -> dict[str, str]:
        raw = str(gitlab_url).strip()
        cfg = self._reasonfirst_config(); base = str(cfg.get("gitlab_base_url") or "").rstrip("/")
        target = urlparse(raw); base_parts = urlparse(base)
        if target.scheme not in {"http", "https"} or target.netloc.lower() != base_parts.netloc.lower():
            raise BridgeError(f"GitLab URL host does not match configured instance: {base}")
        path = unquote(target.path).strip("/"); base_path = unquote(base_parts.path).strip("/")
        if base_path:
            if path == base_path: path = ""
            elif path.startswith(base_path + "/"): path = path[len(base_path)+1:]
            else: raise BridgeError("GitLab URL path is outside configured base path")
        project_part = path.split("/-/", 1)[0].rstrip("/")
        if project_part.endswith(".git"): project_part = project_part[:-4]
        if "/" not in project_part: raise BridgeError("GitLab URL must include namespace/project")
        hinted_path = ""
        if "/-/" in path:
            suffix = path.split("/-/",1)[1]; pieces = suffix.split("/")
            if pieces and pieces[0] in {"tree","blob"} and len(pieces)>=3:
                hinted_path = "/".join(pieces[2:]).strip("/")
        return {"project": project_part, "hinted_path": hinted_path, "gitlab_url": raw}

    def dispatch_request(self, *, gitlab_url: str, module: str = "", request: str = "", intent: str = "analyze-optimize", base_ref: str = "", execution: Any = None) -> dict[str, Any]:
        parsed = self.parse_gitlab_url(gitlab_url)
        focus = str(module or parsed.get("hinted_path") or ".").strip() or "."
        task = re.sub(r"[^a-zA-Z0-9._-]+", "-", f"{intent}-{focus}").strip("-._")[:48] or "chatgpt-analysis"
        target = self._resolve_requested_target(execution)
        goal = f"Prepare the real repository for ChatGPT analysis. Do not modify files. User request: {request or intent}. Focus: {focus}."
        prepared = self._prepare_resolved_target(
            project=parsed["project"],
            task=task,
            goal=goal,
            base_ref=base_ref,
            target=target,
        )
        record = self._workspace_record(str(prepared["workspace_id"])); record.update({"focus": focus, "request": request, "intent": intent, "gitlab_url": gitlab_url, "updated_at": int(time.time())}); self._save_state()
        try: listing = self.files(workspace_id=str(prepared["workspace_id"]), path=focus, max_entries=120)
        except Exception as exc: listing = {"ok": False, "error": redact(str(exc),1200), "path": focus}
        return {"ok": True, "auto_routed": True, "workflow": "chatgpt-first-v4-mcp", "project": parsed["project"], "focus": focus, "workspace_id": prepared["workspace_id"], "worktree_path": prepared["worktree_path"], "execution": target.to_dict(), "initial_listing": listing, "next": ["ChatGPT reads files and forms a plan.", "Call start_codex only after plan/acceptance criteria are reviewed.", "Use review_bundle after Codex tests."]}

    def project_preflight(self, project: str, *, ref: str = "") -> dict[str, Any]:
        if self._git_only_mode():
            argv = _module_command("gitlab_agent.actual_coder_cli", "project-config", project, "--validate")
            if ref: argv += ["--ref", ref]
            report = _run_json(argv, timeout=180)
            return {"ok": True, "mode": "git-only", "project": project, "project_config": report}
        argv = _module_command("gitlab_agent.project_access", project)
        if ref: argv += ["--ref", ref]
        report = _run_json(argv, timeout=60, allow_failure_json=True)
        if not bool(report.get("ok")): raise BridgeError(f"Project access preflight failed: {report}")
        return report

    def prepare(self, *, project: str, task: str = "chatgpt-analysis", goal: str = "Prepare repository for ChatGPT analysis only; do not modify files.", base_ref: str = "", execution: Any = None) -> dict[str, Any]:
        target = self._resolve_requested_target(execution)
        return self._prepare_resolved_target(
            project=project,
            task=task,
            goal=goal,
            base_ref=base_ref,
            target=target,
        )

    def _prepare_resolved_target(
        self,
        *,
        project: str,
        task: str,
        goal: str,
        base_ref: str,
        target: ExecutionTarget,
    ) -> dict[str, Any]:
        if target.type == "ssh":
            manager = self._remote_manager(target)
            probe = manager.probe()
            if not probe.get("ok"): raise BridgeError(f"SSH execution target is not ready: {probe}")
            state = manager.create_workspace(project=project, base_ref=base_ref or "main", task=task)
            wid = str(state["workspace_id"])
            record = {**state, "task": task, "goal": goal, "target": target.to_dict(), "kind": "ssh", "updated_at": int(time.time())}
            with self._lock: self._state["workspaces"][wid] = record; self._save_state()
            return {"ok": True, "workspace_id": wid, "worktree_path": state["worktree_path"], "project": project, "codex_started": False, "execution": target.to_dict(), "base_sha": state["base_sha"], "branch": state["branch"], "origin_url": state["origin_url"]}

        preflight = self.project_preflight(project, ref=base_ref)
        argv = _module_command("gitlab_agent.actual_coder_cli", "start", project, "--task", task, "--goal", goal, "--agent", "codex", "--no-launch")
        if self._git_only_mode(): argv.append("--git-only")
        if base_ref: argv += ["--base-ref", base_ref]
        prepared = _run_json(argv, timeout=180)
        workspace = prepared.get("workspace")
        if not isinstance(workspace, dict): raise BridgeError("actual-coder start returned no workspace")
        wid = str(workspace.get("workspace_id") or ""); worktree = self._assert_worktree_allowed(str(prepared.get("worktree_path") or ""))
        record = {"workspace_id": wid, "project": project, "task": task, "goal": goal, "worktree_path": worktree, "kind": "local", "target": target.to_dict(), "created_at": int(time.time()), "updated_at": int(time.time())}
        with self._lock: self._state["workspaces"][wid] = record; self._save_state()
        return {"ok": True, "project_preflight": preflight, "workspace_id": wid, "worktree_path": worktree, "project": project, "codex_started": False, "execution": target.to_dict()}

    def _workspace_id(self, *, workspace_id: str = "", thread_id: str = "") -> str:
        if workspace_id: self._workspace_record(workspace_id); return workspace_id
        if thread_id: return str(self._session(thread_id)["workspace_id"])
        raise BridgeError("workspace_id or thread_id is required")

    def workspace_status(self, *, workspace_id: str = "", thread_id: str = "") -> dict[str, Any]:
        wid = self._workspace_id(workspace_id=workspace_id, thread_id=thread_id); rec = self._workspace_record(wid)
        if rec.get("kind") == "ssh":
            target = self._target_from_dict(rec["target"]); return {"ok": True, "workspace": self._remote_manager(target).status(rec)}
        return {"ok": True, "workspace": _run_json(_module_command("gitlab_agent.actual_coder_cli", "status", wid), timeout=60)}

    def files(self, *, workspace_id: str = "", thread_id: str = "", path: str = ".", recursive: bool = False, max_entries: int = 300) -> dict[str, Any]:
        wid = self._workspace_id(workspace_id=workspace_id, thread_id=thread_id); rec = self._workspace_record(wid)
        if rec.get("kind") == "ssh":
            target = self._target_from_dict(rec["target"]); data = self._remote_manager(target).list_files(rec, path, recursive=recursive, max_entries=max_entries); return {"ok": True, "workspace_id": wid, **data}
        argv = _module_command("gitlab_agent.actual_coder_cli", "files", wid, path, "--max-entries", str(max(1,min(int(max_entries),500))))
        if recursive: argv.append("--recursive")
        return {"ok": True, **_run_json(argv, timeout=60)}

    def read(self, *, workspace_id: str = "", thread_id: str = "", path: str, start_line: int = 1, end_line: int = 0, max_chars: int = 32000) -> dict[str, Any]:
        wid = self._workspace_id(workspace_id=workspace_id, thread_id=thread_id); rec = self._workspace_record(wid)
        if rec.get("kind") == "ssh":
            target = self._target_from_dict(rec["target"]); result = self._remote_manager(target).read_file(rec, path)
        else:
            result = _run_json(_module_command("gitlab_agent.actual_coder_cli", "read", wid, path), timeout=60)
        content = str(result.get("content") or ""); lines = content.splitlines(); start=max(1,int(start_line)); end=int(end_line) if int(end_line)>0 else len(lines); end=max(start,min(end,len(lines))) if lines else 0
        numbered="\n".join(f"{idx}: {line}" for idx,line in enumerate(lines[start-1:end] if lines else [], start=start)); cap=max(1000,min(int(max_chars),40000)); clipped=redact(numbered,cap)
        return {"ok": True, "workspace_id": wid, "path": path, "total_lines": len(lines), "start_line": start if lines else 0, "end_line": end, "truncated": bool(result.get("truncated")) or len(numbered)>len(clipped), "content": clipped}

    def diff(self, *, workspace_id: str = "", thread_id: str = "") -> dict[str, Any]:
        wid=self._workspace_id(workspace_id=workspace_id, thread_id=thread_id); rec=self._workspace_record(wid)
        if rec.get("kind") == "ssh":
            target=self._target_from_dict(rec["target"]); result=self._remote_manager(target).diff(rec)
        else: result=_run_json(_module_command("gitlab_agent.actual_coder_cli","diff",wid),timeout=120)
        text=redact(str(result.get("diff") or ""),36000)
        return {"ok": True, "workspace_id": wid, "base_sha": result.get("base_sha") or rec.get("base_sha"), "truncated": bool(result.get("truncated")) or len(str(result.get("diff") or ""))>len(text), "diff": text, "untracked": result.get("untracked", [])}

    def _origin_url(self, project: str) -> str:
        cfg=self._reasonfirst_config(); return str(cfg.get("gitlab_base_url") or "").rstrip("/")+"/"+project+".git"

    def _decorate_thread(self, *, app: AppServerClient, thread_id: str, workspace: dict[str, Any], project: str, task: str, goal: str) -> dict[str, Any]:
        name=f"[ReasonFirst] {project} - {task}"[:200]; errors=[]
        try: app.set_thread_name(thread_id,name)
        except Exception as exc: errors.append(f"name:{exc}")
        try: app.set_thread_goal(thread_id,goal)
        except Exception as exc: errors.append(f"goal:{exc}")
        try: app.update_thread_metadata(thread_id,branch=str(workspace.get("branch") or ""),sha=str(workspace.get("head") or workspace.get("base_sha") or ""),origin_url=str(workspace.get("origin_url") or self._origin_url(project)),is_pinned=True)
        except Exception as exc: errors.append(f"metadata:{exc}")
        return {"name": name, "errors": errors}

    def _remote_prompt(self, rec: dict[str, Any], goal: str) -> str:
        return (
            "You are the Codex execution backend controlled by ReasonFirst v4. ChatGPT has already chosen the plan; implement it faithfully.\n"
            "You are running on the selected SSH execution target, inside an isolated Git worktree.\n\n"
            f"Project: {rec.get('project')}\nBase ref: {rec.get('base_ref')}\nBase SHA: {rec.get('base_sha')}\n"
            f"Feature branch: {rec.get('branch')}\nWorkspace: {rec.get('worktree_path')}\nGoal: {goal}\n\n"
            "Rules:\n- Work only inside the worktree above.\n- Do not modify the user's original checkout.\n"
            "- Do not push or force-push unless ReasonFirst explicitly instructs it.\n"
            "- Inspect relevant code before editing.\n- Run relevant build/tests on this same target and report exact commands, exit codes and failures.\n"
            "- Keep changes scoped to the reviewed ChatGPT plan and acceptance criteria.\n"
        )

    def _hybrid_remote_prompt(self, rec: dict[str, Any], goal: str) -> str:
        return (
            "You are Codex running locally on the user's Mac as the implementation/test backend for ReasonFirst v4. ChatGPT is the planner/reviewer; do not redesign the task.\n"
            "The authoritative source tree is NOT local. It is the managed SSH worktree below.\n"
            "Use the reasonfirst_remote dynamic tools for ALL source reads, edits, diffs, builds and tests.\n"
            "Do not copy the repository into this local proxy directory and do not treat local proxy files as source.\n\n"
            f"Project: {rec.get('project')}\nRemote host: {(rec.get('target') or {}).get('host')}\n"
            f"Remote worktree: {rec.get('worktree_path')}\nBase ref: {rec.get('base_ref')}\n"
            f"Base SHA: {rec.get('base_sha')}\nFeature branch: {rec.get('branch')}\nGoal: {goal}\n\n"
            "Rules:\n"
            "- Keep every change inside the managed remote worktree.\n"
            "- Do not modify the user's original remote checkout.\n"
            "- Do not push, merge, deploy, reset --hard, clean, stash, sudo, or open nested SSH sessions.\n"
            "- Inspect the real remote code before editing.\n"
            "- Use reasonfirst_remote.write or apply_patch for edits.\n"
            "- Use reasonfirst_remote.run only with the target's locally approved argv executables for build/tests; report exact argv and exit codes.\n"
            "- Use reasonfirst_remote.diff before finishing.\n"
            "- Keep changes scoped to the reviewed ChatGPT plan and acceptance criteria.\n"
        )

    def start_codex(self, *, workspace_id: str, goal: str) -> dict[str, Any]:
        wid = self._workspace_id(workspace_id=workspace_id)
        rec = self._workspace_record(wid)
        target = self._target_from_dict(rec.get("target") or "local")
        target, execution_migrated = self._migrate_legacy_remote_target_if_needed(wid, rec, target)
        dynamic_tools: list[dict[str, Any]] | None = None
        sandbox_mode = "workspace-write"
        if rec.get("kind") == "ssh":
            workspace = self._remote_manager(target).status(rec)
            remote_worktree = str(rec["worktree_path"])
            if self._is_remote_proxy_target(target):
                prompt = self._hybrid_remote_prompt(rec, goal)
                codex_cwd = self._proxy_workspace(wid, rec)
                dynamic_tools = self._remote_dynamic_tools()
                sandbox_mode = "read-only"
            else:
                prompt = self._remote_prompt(rec, goal)
                codex_cwd = remote_worktree
        else:
            workspace = _run_json(_module_command("gitlab_agent.actual_coder_cli", "status", wid), timeout=60)
            remote_worktree = self._assert_worktree_allowed(str(workspace.get("worktree_path") or ""))
            codex_cwd = remote_worktree
            handoff = _run_json(
                _module_command("gitlab_agent.actual_coder_cli", "resume", wid, "--agent", "codex", "--goal", goal),
                timeout=120,
            )
            prompt = str(handoff.get("agent_prompt") or "").strip()
        policy = self._require_codex_desktop_backend(target)
        app_key, app = self._get_app(target)
        thread_id = app.start_thread(
            cwd=codex_cwd,
            policy=policy,
            dynamic_tools=dynamic_tools,
            sandbox_mode=sandbox_mode,
        )
        with self._lock:
            self._state["sessions"][thread_id] = {
                "thread_id": thread_id,
                "workspace_id": wid,
                "project": rec.get("project"),
                "task": rec.get("task"),
                "worktree_path": remote_worktree,
                "codex_cwd": codex_cwd,
                "target": target.to_dict(),
                "app_key": app_key,
                "last_turn_id": "",
                "last_turn_status": "prepared",
                "last_agent_message": "",
                "events": [],
                "created_at": int(time.time()),
                "updated_at": int(time.time()),
            }
            self._app_current_thread[app_key] = thread_id
            self._save_state()
        turn_id = app.start_turn(
            thread_id=thread_id,
            cwd=codex_cwd,
            prompt=prompt,
            policy=policy,
            network_access=(
                False
                if self._is_remote_proxy_target(target)
                else policy.network_access
            ),
            sandbox_mode=sandbox_mode,
        )
        with self._lock:
            self._state["sessions"][thread_id]["last_turn_id"] = turn_id
            self._state["sessions"][thread_id]["last_turn_status"] = "inProgress"
            self._save_state()
        decorated = self._decorate_thread(
            app=app,
            thread_id=thread_id,
            workspace=workspace,
            project=str(rec.get("project") or ""),
            task=str(rec.get("task") or "task"),
            goal=goal,
        )
        return {
            "ok": True,
            "workspace_id": wid,
            "worktree_path": remote_worktree,
            "codex_cwd": codex_cwd,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "thread_name": decorated["name"],
            "thread_metadata_errors": decorated["errors"],
            "execution": target.to_dict(),
            "worker_backend": target.worker_backend,
            "worker_policy": policy.to_dict(),
            "codex_backend": app.backend_name,
            "remote_tools": bool(dynamic_tools),
            "execution_migrated": execution_migrated,
        }

    def start(self, *, project: str, task: str, goal: str, base_ref: str = "", execution: Any = None) -> dict[str, Any]:
        prepared=self.prepare(project=project,task=task,goal=goal,base_ref=base_ref,execution=execution)
        started=self.start_codex(workspace_id=str(prepared["workspace_id"]),goal=goal)
        return {**prepared, **started}

    def _ensure_loaded(self, thread_id: str) -> tuple[dict[str, Any], AppServerClient]:
        session=self._session(thread_id); key,app=self._app_for_session(session); self._app_current_thread[key]=thread_id; return session,app

    def continue_task(self, *, thread_id: str, goal: str, from_ci: bool = False) -> dict[str, Any]:
        if from_ci:
            raise BridgeError("resume_from_ci is unavailable for dynamic/SSH targets unless GitLab API mode is configured")
        session, app = self._ensure_loaded(thread_id)
        rec = self._workspace_record(str(session["workspace_id"]))
        target = self._target_from_dict(session["target"])
        sandbox_mode = "workspace-write"
        if rec.get("kind") == "ssh":
            if self._is_remote_proxy_target(target):
                prompt = self._hybrid_remote_prompt(rec, goal)
                sandbox_mode = "read-only"
            else:
                prompt = self._remote_prompt(rec, goal)
        else:
            handoff = _run_json(
                _module_command("gitlab_agent.actual_coder_cli", "resume", str(session["workspace_id"]), "--agent", "codex", "--goal", goal),
                timeout=120,
            )
            prompt = str(handoff.get("agent_prompt") or "")
        policy = self._require_codex_desktop_backend(target)
        cwd = str(session.get("codex_cwd") or session["worktree_path"])
        turn_id = app.start_turn(
            thread_id=thread_id,
            cwd=cwd,
            prompt=prompt,
            policy=policy,
            network_access=(
                False
                if self._is_remote_proxy_target(target)
                else policy.network_access
            ),
            sandbox_mode=sandbox_mode,
        )
        with self._lock:
            session["last_turn_id"] = turn_id
            session["last_turn_status"] = "inProgress"
            session["updated_at"] = int(time.time())
            self._save_state()
        return {"ok": True, "thread_id": thread_id, "turn_id": turn_id}

    def steer(self, *, thread_id: str, prompt: str, turn_id: str = "") -> dict[str, Any]:
        session,app=self._ensure_loaded(thread_id); tid=turn_id or str(session.get("last_turn_id") or ""); accepted=app.steer(thread_id=thread_id,turn_id=tid,prompt=prompt); return {"ok":True,"thread_id":thread_id,"turn_id":accepted}

    def interrupt(self, *, thread_id: str, turn_id: str = "") -> dict[str, Any]:
        session,app=self._ensure_loaded(thread_id); tid=turn_id or str(session.get("last_turn_id") or ""); app.interrupt(thread_id=thread_id,turn_id=tid); return {"ok":True,"thread_id":thread_id,"turn_id":tid}

    def status(self, *, thread_id: str) -> dict[str, Any]:
        session,app=self._ensure_loaded(thread_id); thread=app.read_thread(thread_id,include_turns=False); return {"ok":True,"session":dict(session),"thread":thread,"workspace":self.workspace_status(thread_id=thread_id).get("workspace"),"codex_backend":app.backend_name}

    def events(self, *, thread_id: str, limit: int = 30) -> dict[str, Any]:
        session=self._session(thread_id); events=list(session.get("events",[]))[-max(1,min(int(limit),100)):]; return {"ok":True,"thread_id":thread_id,"events":events,"last_agent_message":redact(str(session.get("last_agent_message") or ""),12000)}

    def compact_status(self, *, thread_id: str) -> dict[str, Any]:
        session = self._session(thread_id)
        workspace = self.workspace_status(thread_id=thread_id).get("workspace")
        return {
            "ok": True,
            "thread_id": thread_id,
            "workspace_id": session.get("workspace_id"),
            "turn_id": session.get("last_turn_id"),
            "turn_status": session.get("last_turn_status"),
            "last_agent_message": redact(str(session.get("last_agent_message") or ""), 6000),
            "codex_backend": self._app_for_session(session)[1].backend_name,
            "workspace": workspace,
            "push_approved": isinstance(session.get("push_approval"), dict),
            "last_push": session.get("last_push"),
        }

    def authorize_push(self, *, thread_id: str, commit_message: str) -> dict[str, Any]:
        """Authorize Codex to commit/push exactly the currently reviewed remote snapshot."""
        session = self._session(thread_id)
        rec = self._workspace_record(str(session["workspace_id"]))
        if rec.get("kind") != "ssh":
            raise BridgeError("v4 authorize_push currently supports managed SSH workspaces; use the local ActualCoder finish flow for local workspaces")
        target = self._target_from_dict(rec.get("target") or {})
        snap = self._remote_manager(target).snapshot(rec)
        if not snap.get("dirty"):
            raise BridgeError("workspace has no changes to push")
        message = str(commit_message or "").strip()
        if not message or len(message) > 240 or "\n" in message or "\r" in message:
            raise BridgeError("commit_message must be one non-empty line up to 240 characters")
        with self._lock:
            session["push_approval"] = {
                "digest": str(snap.get("digest") or ""),
                "candidate_tree": str(snap.get("candidate_tree") or ""),
                "branch": str(snap.get("branch") or ""),
                "origin_url": str(snap.get("origin_url") or ""),
                "base_sha": str(snap.get("base_sha") or ""),
                "head": str(snap.get("head") or ""),
                "target": target.to_dict(),
                "message": message,
                "approved_at": int(time.time()),
            }
            session["updated_at"] = int(time.time())
            self._save_state()
        return {
            "ok": True,
            "thread_id": thread_id,
            "workspace_id": session["workspace_id"],
            "branch": snap.get("branch"),
            "head": snap.get("head"),
            "digest": snap.get("digest"),
            "candidate_tree": snap.get("candidate_tree"),
            "origin_url": snap.get("origin_url"),
            "changed_paths": snap.get("changed_paths"),
            "commit_message": message,
            "next": "Ask Codex to call reasonfirst_remote.commit_push. Any code change after this approval invalidates the digest and blocks the push.",
        }

    def artifacts(self, *, workspace_id: str = "", thread_id: str = "", path: str = ".", changed_only: bool = True, max_entries: int = 80, max_text_chars: int = 20000, max_visual_previews: int = 2) -> dict[str, Any]:
        wid=self._workspace_id(workspace_id=workspace_id,thread_id=thread_id); rec=self._workspace_record(wid); since=int(rec.get("created_at") or 0)
        if rec.get("kind") != "ssh":
            status=_run_json(_module_command("gitlab_agent.actual_coder_cli","status",wid),timeout=60); worktree=Path(self._assert_worktree_allowed(str(status.get("worktree_path") or ""))); result=scan_artifacts(worktree,relative_path=path,since_epoch=since,changed_only=changed_only,max_entries=max_entries,max_text_chars=max_text_chars,max_visual_previews=max_visual_previews)
            for item in result.get("items",[]):
                if isinstance(item,dict) and isinstance(item.get("content"),str): item["content"]=redact(str(item["content"]),40000)
            return {"ok":True,"workspace_id":wid,**result}
        target=self._target_from_dict(rec["target"]); manager=self._remote_manager(target); candidates=manager.artifact_candidates(rec,path,since_epoch=since if changed_only else 0,max_entries=max_entries); items=[]; previews=0
        for candidate in candidates:
            try:
                payload=manager.read_bytes_b64(rec,str(candidate["path"]),max_bytes=16*1024*1024); raw=base64.b64decode(payload["base64"])
                with tempfile.TemporaryDirectory(prefix="rf-artifact-") as td:
                    root=Path(td); rel=Path(str(candidate["path"])); local=root/rel; local.parent.mkdir(parents=True,exist_ok=True); local.write_bytes(raw)
                    scan=scan_artifacts(root,relative_path=str(rel),since_epoch=0,changed_only=False,max_entries=1,max_text_chars=max_text_chars,max_visual_previews=1 if previews<max_visual_previews else 0)
                    entry=(scan.get("items") or [{}])[0]
                    if isinstance(entry.get("content"),str): entry["content"]=redact(str(entry["content"]),40000)
                    if entry.get("visual_preview"): previews+=1
                    items.append(entry)
            except Exception as exc:
                items.append({**candidate,"extract_error":redact(str(exc),500)})
        return {"ok":True,"workspace_id":wid,"path":path,"changed_only":changed_only,"since_epoch":since,"items":items,"truncated":len(candidates)>=max_entries,"visual_previews":previews}

    def artifact_descriptor(self, *, path: str, workspace_id: str = "", thread_id: str = "", max_bytes: int = 8*1024*1024) -> dict[str, Any]:
        wid=self._workspace_id(workspace_id=workspace_id,thread_id=thread_id); rec=self._workspace_record(wid)
        if rec.get("kind") == "ssh":
            target=self._target_from_dict(rec["target"]); data=self._remote_manager(target).read_bytes_b64(rec,path,max_bytes=max_bytes); return {"workspace_id":wid,"remote":True,"path":path,"size":data["size"],"base64":data["base64"],"target":target.to_dict()}
        status=_run_json(_module_command("gitlab_agent.actual_coder_cli","status",wid),timeout=60); worktree=Path(self._assert_worktree_allowed(str(status.get("worktree_path") or ""))); return {"workspace_id":wid,**artifact_file(worktree,path,max_bytes=max_bytes)}

    def review_bundle(self, *, thread_id: str, artifact_path: str = ".") -> dict[str, Any]:
        session=self._session(thread_id); ev=self.events(thread_id=thread_id,limit=24); bounded=[]
        for item in ev.get("events",[]):
            if isinstance(item,dict):
                copy=dict(item)
                if isinstance(copy.get("output"),str): copy["output"]=redact(str(copy["output"]),1800)
                bounded.append(copy)
        diff=self.diff(thread_id=thread_id); diff["diff"]=redact(str(diff.get("diff") or ""),16000)
        return {"ok":True,"thread_id":thread_id,"workspace_id":session["workspace_id"],"status":self.status(thread_id=thread_id),"events":{"events":bounded,"last_agent_message":redact(str(ev.get("last_agent_message") or ""),5000)},"diff":diff,"artifacts":self.artifacts(thread_id=thread_id,path=artifact_path,changed_only=True,max_entries=30,max_text_chars=12000,max_visual_previews=1)}

    def ci(self, *, thread_id: str) -> dict[str, Any]:
        rec=self._workspace_record(str(self._session(thread_id)["workspace_id"]))
        if rec.get("kind")=="ssh" or self._git_only_mode(): raise BridgeError("CI inspection requires GitLab API authentication and a local ActualCoder workspace")
        return _run_json(_module_command("gitlab_agent.actual_coder_cli","ci",str(rec["workspace_id"])),timeout=120,allow_failure_json=True)

    def finish_preview(self, *, thread_id: str, message: str) -> dict[str, Any]:
        session=self._session(thread_id); rec=self._workspace_record(str(session["workspace_id"]))
        if rec.get("kind") == "ssh":
            return {"ok":False,"remote_finish_supported":False,"workspace":self.workspace_status(thread_id=thread_id).get("workspace"),"diff":self.diff(thread_id=thread_id),"blockers":["v4 remote push is performed by Codex only after ChatGPT calls authorize_push for the exact reviewed snapshot; use that flow instead of finish."],"message":message}
        result=_run_json(_module_command("gitlab_agent.actual_coder_cli","finish",str(session["workspace_id"]),"--message",message,"--dry-run"),timeout=600,allow_failure_json=True)
        return {"ok":bool(result.get("ok")),"dry_run":True,"raw":result}

    def finish(self, *, thread_id: str, message: str, snapshot_digest: str) -> dict[str, Any]:
        session=self._session(thread_id); rec=self._workspace_record(str(session["workspace_id"]))
        if rec.get("kind") == "ssh": raise BridgeError("Use v4 authorize_push + Codex reasonfirst_remote.commit_push for remote workspaces; no push was performed")
        if os.getenv("RF_CODEX_REMOTE_FINISH","false").strip().lower() not in {"1","true","yes","on"}: raise BridgeError("Remote finish is disabled")
        return _run_json(_module_command("gitlab_agent.actual_coder_cli","finish",str(session["workspace_id"]),"--message",message,"--yes"),timeout=600,allow_failure_json=True)

    def _on_event(self, event: dict[str, Any], app_key: str) -> None:
        method=str(event.get("method") or ""); params=event.get("params") if isinstance(event.get("params"),dict) else {}; thread_id=params.get("threadId") if isinstance(params,dict) else None
        if not isinstance(thread_id,str) or not thread_id: thread_id=self._app_current_thread.get(app_key)
        if not thread_id: return
        with self._lock:
            session=self._state.get("sessions",{}).get(thread_id)
            if not isinstance(session,dict): return
            summary=None
            if method=="turn/started":
                turn=params.get("turn") if isinstance(params,dict) else None
                if isinstance(turn,dict): session["last_turn_id"]=turn.get("id") or session.get("last_turn_id"); session["last_turn_status"]=turn.get("status") or "inProgress"
                summary={"method":method,"turn_id":session.get("last_turn_id")}
            elif method=="turn/completed":
                turn=params.get("turn") if isinstance(params,dict) else None
                if isinstance(turn,dict): session["last_turn_status"]=turn.get("status"); session["last_turn_id"]=turn.get("id") or session.get("last_turn_id")
                summary={"method":method,"turn_id":session.get("last_turn_id"),"status":session.get("last_turn_status")}
            elif method=="item/agentMessage/delta":
                delta=params.get("delta")
                if isinstance(delta,str): session["last_agent_message"]=(str(session.get("last_agent_message") or "")+delta)[-20000:]
            elif method=="item/completed":
                item=params.get("item") if isinstance(params,dict) else None
                if isinstance(item,dict):
                    kind=item.get("type")
                    if kind=="commandExecution": summary={"method":method,"type":kind,"status":item.get("status"),"exit_code":item.get("exitCode"),"duration_ms":item.get("durationMs"),"command":redact(str(item.get("command") or ""),500),"output":redact(str(item.get("aggregatedOutput") or ""),8000)}
                    elif kind=="fileChange": summary={"method":method,"type":kind,"status":item.get("status")}
                    elif kind=="agentMessage" and isinstance(item.get("text"),str): session["last_agent_message"]=str(item["text"])[-20000:]
            elif method=="error": summary={"method":method,"message":redact(str(params.get("error")),2000)}
            elif method.startswith("bridge/"): summary={"method":method,"params":params}
            if summary is not None:
                events=session.setdefault("events",[]); events.append({"ts":int(time.time()),**summary}); del events[:-100]
            session["updated_at"]=int(time.time()); self._save_state()

    def close(self) -> None:
        for app in list(self._apps.values()):
            try: app.close()
            except Exception: pass
        self._apps.clear()
