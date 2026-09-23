#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import secrets
import sys
from typing import Any

from gitlab_agent import __version__ as REASONFIRST_VERSION
from reasonfirst_codex_bridge.controller import BridgeController, BridgeError, redact


def _doctor() -> int:
    ctrl = BridgeController()
    try:
        data = ctrl.doctor()
        data["transport"] = "mcp-http"
        data["reasonfirst_v4"] = True
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return 0 if bool(data.get("ok", True)) else 1
    finally:
        ctrl.close()


def build_server():
    # MCP Python SDK v2 (2026-07-28 protocol line).
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations

    ctrl = BridgeController()
    server = MCPServer(
        "ReasonFirst",
        instructions=(
            "ReasonFirst v4 is the local code-work orchestration server. ChatGPT is the planner/reviewer; "
            "Codex is only the implementation/build/test/push executor. Prefer dispatch -> files/read -> ChatGPT analysis -> "
            "codex_start -> review_bundle/diff -> authorize_push only after review. Never send passwords/tokens/keys as tool arguments. "
            "Execution targets are task-scoped; SSH workspaces are isolated and preserve the user's original checkout."
        ),
    )
    token_path = ctrl.state_dir / "control-token"
    if not token_path.exists():
        token_path.write_text(secrets.token_hex(32), encoding="ascii")
        token_path.chmod(0o600)

    @server.custom_route("/control", methods=["POST"], include_in_schema=False)
    async def control(request):
        from starlette.responses import JSONResponse
        from github_control_relay import dispatch
        expected = token_path.read_text(encoding="ascii").strip()
        provided = request.headers.get("authorization", "").removeprefix("Bearer ")
        if not hmac.compare_digest(expected, provided):
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
        try:
            body = await request.json()
            if not isinstance(body, dict) or not isinstance(body.get("command"), dict):
                return JSONResponse({"ok": False, "error": "invalid command"}, status_code=400)
            result = await asyncio.to_thread(dispatch, ctrl, body["command"],
                                             control_repo=str(body.get("control_repo") or ""))
            return JSONResponse(result)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": redact(str(exc), 2000)}, status_code=400)
    @server.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(_request):
        from starlette.responses import JSONResponse
        return JSONResponse({"ok": True, "service": "reasonfirst", "version": REASONFIRST_VERSION, "bridge": "preview", "config_schema": 4})
    read = ToolAnnotations(read_only_hint=True, idempotent_hint=True)
    write = ToolAnnotations(read_only_hint=False, idempotent_hint=False)

    @server.tool(name="reasonfirst_doctor", annotations=read)
    def reasonfirst_doctor() -> dict[str, Any]:
        """Check ReasonFirst, GitLab Git-only auth, Codex availability, and configured execution targets."""
        return ctrl.doctor()

    @server.tool(name="reasonfirst_target_probe", annotations=read)
    def reasonfirst_target_probe(execution: str | None = None) -> dict[str, Any]:
        """Probe a local/SSH execution target without modifying source code."""
        return ctrl.target_probe(execution)

    @server.tool(name="reasonfirst_dispatch", annotations=write)
    def reasonfirst_dispatch(
        gitlab_url: str,
        module: str,
        request: str,
        intent: str = "analyze-optimize",
        base_ref: str = "main",
        execution: str | None = None,
    ) -> dict[str, Any]:
        """Prepare an isolated real-code workspace for ChatGPT analysis. Does not start Codex."""
        return ctrl.dispatch_request(
            gitlab_url=gitlab_url,
            module=module,
            request=request,
            intent=intent,
            base_ref=base_ref,
            execution=execution,
        )

    @server.tool(name="reasonfirst_workspace_status", annotations=read)
    def reasonfirst_workspace_status(workspace_id: str = "", thread_id: str = "") -> dict[str, Any]:
        """Read the managed workspace branch/base/dirty state."""
        return ctrl.workspace_status(workspace_id=workspace_id, thread_id=thread_id)

    @server.tool(name="reasonfirst_files", annotations=read)
    def reasonfirst_files(
        workspace_id: str = "",
        thread_id: str = "",
        path: str = ".",
        recursive: bool = False,
        max_entries: int = 300,
    ) -> dict[str, Any]:
        """List files in the managed source-of-truth workspace."""
        return ctrl.files(
            workspace_id=workspace_id,
            thread_id=thread_id,
            path=path,
            recursive=recursive,
            max_entries=max_entries,
        )

    @server.tool(name="reasonfirst_read", annotations=read)
    def reasonfirst_read(
        path: str,
        workspace_id: str = "",
        thread_id: str = "",
        start_line: int = 1,
        end_line: int = 0,
        max_chars: int = 32000,
    ) -> dict[str, Any]:
        """Read one source/config/test file from the managed workspace."""
        return ctrl.read(
            workspace_id=workspace_id,
            thread_id=thread_id,
            path=path,
            start_line=start_line,
            end_line=end_line,
            max_chars=max_chars,
        )

    @server.tool(name="reasonfirst_diff", annotations=read)
    def reasonfirst_diff(workspace_id: str = "", thread_id: str = "") -> dict[str, Any]:
        """Read the real Git diff against the pinned base SHA."""
        return ctrl.diff(workspace_id=workspace_id, thread_id=thread_id)

    @server.tool(name="reasonfirst_codex_start", annotations=write)
    def reasonfirst_codex_start(workspace_id: str, goal: str) -> dict[str, Any]:
        """Start Codex only after ChatGPT has reviewed source and defined a concrete implementation/test plan."""
        return ctrl.start_codex(workspace_id=workspace_id, goal=goal)

    @server.tool(name="reasonfirst_codex_continue", annotations=write)
    def reasonfirst_codex_continue(thread_id: str, goal: str) -> dict[str, Any]:
        """Give the existing Codex executor the next reviewed implementation/test instruction."""
        return ctrl.continue_task(thread_id=thread_id, goal=goal)

    @server.tool(name="reasonfirst_codex_steer", annotations=write)
    def reasonfirst_codex_steer(thread_id: str, prompt: str, turn_id: str = "") -> dict[str, Any]:
        """Steer an active Codex turn without creating a new workspace."""
        return ctrl.steer(thread_id=thread_id, prompt=prompt, turn_id=turn_id)

    @server.tool(name="reasonfirst_codex_interrupt", annotations=write)
    def reasonfirst_codex_interrupt(thread_id: str, turn_id: str = "") -> dict[str, Any]:
        """Interrupt the active Codex turn while preserving the workspace/thread."""
        return ctrl.interrupt(thread_id=thread_id, turn_id=turn_id)

    @server.tool(name="reasonfirst_codex_status", annotations=read)
    def reasonfirst_codex_status(thread_id: str) -> dict[str, Any]:
        """Return a compact Codex/workspace status without replaying full thread history."""
        return ctrl.compact_status(thread_id=thread_id)

    @server.tool(name="reasonfirst_codex_events", annotations=read)
    def reasonfirst_codex_events(thread_id: str, limit: int = 20) -> dict[str, Any]:
        """Return only recent compact Codex execution events."""
        return ctrl.events(thread_id=thread_id, limit=min(limit, 50))

    @server.tool(name="reasonfirst_pending_approvals", annotations=read)
    def reasonfirst_pending_approvals(thread_id: str) -> dict[str, Any]:
        """List pending Codex App Server approval requests for this thread."""
        return ctrl.pending_approvals(thread_id=thread_id)

    @server.tool(name="reasonfirst_approve", annotations=write)
    def reasonfirst_approve(
        thread_id: str,
        request_id: int,
        for_session: bool = False,
    ) -> dict[str, Any]:
        """Explicitly approve one pending Codex App Server request."""
        return ctrl.resolve_approval(
            thread_id=thread_id,
            request_id=request_id,
            approve=True,
            for_session=for_session,
        )

    @server.tool(name="reasonfirst_decline", annotations=write)
    def reasonfirst_decline(thread_id: str, request_id: int) -> dict[str, Any]:
        """Explicitly decline one pending Codex App Server approval request."""
        return ctrl.resolve_approval(
            thread_id=thread_id,
            request_id=request_id,
            approve=False,
            for_session=False,
        )

    @server.tool(name="reasonfirst_finish_preview", annotations=read)
    def reasonfirst_finish_preview(
        thread_id: str,
        commit_message: str,
        allow_protected: bool = False,
        allow_secret_match: bool = False,
    ) -> dict[str, Any]:
        """Run the complete local/SSH finish review without publishing."""
        return ctrl.finish_preview(
            thread_id=thread_id,
            message=commit_message,
            allow_protected=allow_protected,
            allow_secret_match=allow_secret_match,
        )

    @server.tool(name="reasonfirst_review_bundle", annotations=read)
    def reasonfirst_review_bundle(thread_id: str, artifact_path: str = ".") -> dict[str, Any]:
        """Return bounded status, recent evidence, real diff, and changed artifacts for ChatGPT review."""
        return ctrl.review_bundle(thread_id=thread_id, artifact_path=artifact_path)

    @server.tool(name="reasonfirst_artifacts", annotations=read)
    def reasonfirst_artifacts(
        workspace_id: str = "",
        thread_id: str = "",
        path: str = ".",
        changed_only: bool = True,
        max_entries: int = 30,
    ) -> dict[str, Any]:
        """Extract changed logs/reports/JSON/images/PDF/DOCX from the managed workspace."""
        return ctrl.artifacts(
            workspace_id=workspace_id,
            thread_id=thread_id,
            path=path,
            changed_only=changed_only,
            max_entries=max_entries,
            max_text_chars=12000,
            max_visual_previews=1,
        )

    remote_push_enabled = os.getenv(
        "RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH", "false"
    ).strip().lower() in {"1", "true", "yes", "on"}
    if remote_push_enabled:
        @server.tool(name="reasonfirst_authorize_push", annotations=write)
        def reasonfirst_authorize_push(
            thread_id: str,
            commit_message: str,
            allow_protected: bool = False,
            allow_secret_match: bool = False,
        ) -> dict[str, Any]:
            """Authorize only an unblocked fresh SSH finish plan and exact snapshot."""
            return ctrl.authorize_push(
                thread_id=thread_id,
                commit_message=commit_message,
                allow_protected=allow_protected,
                allow_secret_match=allow_secret_match,
            )

    # Keep controller alive for the process lifetime. MCPServer does not own it.
    setattr(server, "_reasonfirst_controller", ctrl)
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ReasonFirst v4 MCP server")
    parser.add_argument("--doctor", action="store_true", help="print local doctor JSON and exit")
    parser.add_argument("--stdio", action="store_true", help="legacy direct MCP transport")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--path", default="/mcp")
    args = parser.parse_args(argv)
    if args.doctor:
        return _doctor()
    server = build_server()
    try:
        if args.stdio:
            server.run(transport="stdio")
        else:
            server.run(transport="streamable-http", host=args.host, port=args.port,
                       streamable_http_path=args.path)
        return 0
    finally:
        ctrl = getattr(server, "_reasonfirst_controller", None)
        if ctrl is not None:
            ctrl.close()


if __name__ == "__main__":
    raise SystemExit(main())
