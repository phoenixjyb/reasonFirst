"""Real MCP/HTTP acceptance for the explicitly owned managed service.

The same fixture runs from source and from an installed wheel. It uses fresh
synthetic configuration, real TCP sockets, and a fake coding backend only for
the asynchronous turn scenario. It never selects an existing service or worker.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import gitlab_agent
from gitlab_agent import bridge_http, bridge_mcp
from gitlab_agent.bridge_preview import admission, controller
from gitlab_agent.bridge_preview.bridge_config import ExecutionTarget
from gitlab_agent.upgrade import service_managed
from gitlab_agent.worker_policy import default_worker_policy
from mcp import Client
import mcp
import uvicorn

PROTOCOL = "2026-07-28"
MODES = ("read-only", "full-chat")
FALSE_FLAGS = (
    "effective_configuration_verified", "recovered_state_verified",
    "external_producers_quiesced", "global_idle_verified",
    "runtime_identity_verified", "activation_authorized",
    "ready_for_activation", "existing_service_adopted",
)
SELECTED_ROUTE: str | None = None
SELECTED_ROOT: Path | None = None


def modern_message(payload):
    return {**payload, "params": {
        **payload.get("params", {}),
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": PROTOCOL,
            "io.modelcontextprotocol/clientCapabilities": {},
        },
    }}


def verify_origins(route: str, expected: Path) -> None:
    expected = expected.resolve(strict=True)
    modules = (gitlab_agent, bridge_http, bridge_mcp, admission, controller, service_managed)
    for module in modules:
        origin = Path(module.__file__).resolve(strict=True)
        if not origin.is_relative_to(expected):
            raise AssertionError("managed_fixture_origin_mismatch")
    if route == "packaged":
        if expected != Path(sys.prefix).resolve():
            raise AssertionError("managed_fixture_interpreter_mismatch")
    elif route != "source":
        raise AssertionError("managed_fixture_route_invalid")
    for module in (mcp, uvicorn):
        if not Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
            raise AssertionError("managed_fixture_sdk_origin_mismatch")


class FakeApp:
    """A deterministic worker stand-in; the production turn ledger is real."""

    def __init__(self, **kwargs):
        self.backend_name = "standalone-local"
        self.handler = kwargs["event_handler"]
        self.turns = 0
        self.interrupts = []
        self.closed = False

    def worker_policy_evidence(self, thread_id):
        return {"satisfied": True}

    def resume_thread(self, thread_id, **kwargs):
        return {"satisfied": True}

    def start_turn(self, **kwargs):
        self.turns += 1
        return "synthetic-turn-" + str(self.turns)

    def interrupt(self, **kwargs):
        self.interrupts.append(kwargs)

    def complete(self, thread_id, turn_id):
        self.handler({
            "method": "turn/completed",
            "params": {
                "threadId": thread_id,
                "turn": {"id": turn_id, "status": "interrupted"},
            },
        })

    def close(self):
        self.closed = True


class ManagedHTTPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rf-managed-http-")
        self.root = Path(self.temp.name).resolve() / "HTTP 空间's fixture"
        self.root.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.state = self.root / "state"
        self.worktree = self.root / "workspace"
        self.worktree.mkdir()
        self.env_file = self.root / "agent.env"
        self.bridge_file = self.root / "bridge.json"
        self.env_file.write_text(
            "GITLAB_BASE_URL=https://managed-service.invalid\n"
            "GITLAB_TOKEN=\nGITLAB_GIT_TOKEN=\nGITLAB_GIT_PASSWORD=\n"
            "GITLAB_ALLOWED_PROJECTS=synthetic/project\n"
            "GITLAB_TRUST_ENV=false\nGITLAB_GIT_TRUST_ENV=false\n"
            "REASONFIRST_CODEX_NETWORK_ACCESS=false\n"
            f"GITLAB_WORKSPACE_ROOT={self.worktree}\n",
            encoding="utf-8",
        )
        self.bridge_file.write_text(json.dumps({
            "version": 4,
            "defaults": {"target": "local", "codex_backend": "standalone-local"},
            "targets": {"local": {
                "type": "local", "codex_backend": "standalone-local",
                "network_access": False,
            }},
        }), encoding="utf-8")
        self.original_config = (self.env_file.read_bytes(), self.bridge_file.read_bytes())
        env = {
            name: value for name, value in os.environ.items()
            if not name.startswith(("RF_", "GITLAB_", "REASONFIRST_"))
            and not name.upper().endswith("_PROXY")
            and name not in {
                "PYTHONPATH", "PYTHONHOME", "CONTROL_PLANE_API_KEY",
                "OPENAI_ADMIN_KEY", "OPENAI_API_KEY",
            }
        }
        env.update({
            "HOME": str(self.home), "USERPROFILE": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_DATA_HOME": str(self.home / ".local/share"),
            "GITLAB_AGENT_ENV_FILE": str(self.env_file),
            "RF_BRIDGE_CONFIG": str(self.bridge_file),
            "RF_CODEX_BRIDGE_STATE_DIR": str(self.state),
            "RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH": "false",
            "RF_GITLAB_AUTH_MODE": "git-only",
        })
        self.patches = ExitStack()
        self.patches.enter_context(patch.dict(os.environ, env, clear=True))
        self.no_commands = self.patches.enter_context(patch.object(
            controller, "_run_json", side_effect=AssertionError("unexpected_fixture_command"),
        ))
        self.no_workers = self.patches.enter_context(patch.object(
            controller, "AppServerClient", side_effect=AssertionError("unexpected_fixture_worker"),
        ))
        self.services = []
        self.releases = []

    async def asyncTearDown(self):
        for release in self.releases:
            release.set()
        try:
            for service in reversed(self.services):
                await asyncio.wait_for(service.aclose(), timeout=8)
                snapshot = service.maintenance_snapshot()
                self.assertTrue(snapshot["cleanup_complete"])
                self.assertFalse(snapshot["listener_serving"])
            self.assertEqual(self.env_file.read_bytes(), self.original_config[0])
            self.assertEqual(self.bridge_file.read_bytes(), self.original_config[1])
        finally:
            self.patches.close()
            self.temp.cleanup()

    def unused_port(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    async def start_service(self, mode="read-only", *, port=None):
        launch = bridge_http.HTTPLaunch(
            "127.0.0.1", port or self.unused_port(),
            "/managed-fixture", mode, "disabled",
        )
        service = service_managed.ManagedBridgeService(launch)
        self.services.append(service)
        await asyncio.wait_for(service.start(), timeout=12)
        snapshot = service.maintenance_snapshot()
        self.assertEqual(snapshot["scope"], "owned-managed-bridge-service")
        self.assertEqual(snapshot["lifecycle"], "running")
        self.assertTrue(snapshot["listener_serving"])
        for name in FALSE_FLAGS:
            self.assertIs(snapshot[name], False)
        self.launch = launch
        self.url = f"http://127.0.0.1:{launch.port}{launch.path}"
        return service

    def seed_session(self, service):
        ctrl = service._controller
        ctrl._state["sessions"]["synthetic-thread"] = {
            "thread_id": "synthetic-thread",
            "workspace_id": "synthetic-workspace",
            "events": [{"method": "synthetic/completed", "ts": 1}],
            "last_agent_message": "synthetic fixture",
            "pending_approvals": {},
        }
        return ctrl

    async def raw(self, *, method="POST", path=None, payload=None, protocol=PROTOCOL,
                  extra_headers=(), send_body=True):
        payload = payload or {
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
        }
        body = json.dumps(modern_message(payload)).encode("utf-8")
        port = self.launch.port
        path = path or self.launch.path

        def perform():
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                conn.putrequest(method, path, skip_accept_encoding=True)
                conn.putheader("Content-Type", "application/json")
                conn.putheader("Accept", "application/json")
                conn.putheader("Content-Length", str(len(body)))
                if protocol is not None:
                    conn.putheader("MCP-Protocol-Version", protocol)
                conn.putheader("MCP-Method", payload["method"])
                if payload["method"] == "tools/call":
                    conn.putheader("MCP-Name", payload["params"]["name"])
                for name, value in extra_headers:
                    conn.putheader(name, value)
                conn.endheaders(body if send_body else None)
                response = conn.getresponse()
                data = response.read(65537)
                if len(data) > 65536:
                    raise AssertionError("managed_fixture_response_limit")
                return response.status, json.loads(data) if data else None
            finally:
                conn.close()

        return await asyncio.wait_for(asyncio.to_thread(perform), timeout=5)

    async def wait_until(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(wait(), timeout=4)

    def busy(self, service):
        with self.assertRaises(service_managed.ServiceError) as failure:
            service.try_enter_maintenance()
        self.assertEqual(failure.exception.code, "maintenance_busy")

    async def harmless_call(self, client):
        result = await client.call_tool(
            "reasonfirst_codex_events", {"thread_id": "synthetic-thread"},
        )
        self.assertFalse(result.is_error)
        self.assertIn("synthetic fixture", json.dumps(result.model_dump(mode="json")))

    async def catalog_cycle(self, mode):
        service = await self.start_service(mode)
        ctrl = self.seed_session(service)
        reference = bridge_mcp.build_server(read_only_mode=mode == "read-only", controller=ctrl)
        expected = {tool.name: tool.model_dump(mode="json") for tool in await reference.list_tools()}
        self.assertEqual(len(expected), 12 if mode == "read-only" else 22)

        async with Client(self.url, mode="auto", read_timeout_seconds=3) as client:
            self.assertEqual(client.protocol_version, PROTOCOL)
            catalog = await client.list_tools()
            self.assertEqual({tool.name: tool.model_dump(mode="json") for tool in catalog.tools}, expected)
            await self.harmless_call(client)
        await self.wait_until(lambda: service.maintenance_snapshot()["admission"]["idle_observed"])

        lease = service.try_enter_maintenance()
        try:
            snapshot = service.maintenance_snapshot()
            self.assertTrue(snapshot["maintenance_window_held"])
            self.assertFalse(snapshot["admission"]["admission_open"])
            status, payload = await self.raw(method="GET", path="/healthz")
            self.assertEqual(status, 200)
            self.assertEqual(payload["service"], "reasonfirst")
            self.assertNotIn("ready", payload)

            self.no_commands.reset_mock()
            self.no_workers.reset_mock()
            with patch.object(ctrl, "_session", side_effect=AssertionError("held_handler_entered")) as session:
                for name, tool in expected.items():
                    schema = tool["inputSchema"] if "inputSchema" in tool else tool["input_schema"]
                    arguments = {
                        arg: 1 if schema["properties"][arg].get("type") == "integer" else "synthetic"
                        for arg in schema.get("required", [])
                    }
                    status, payload = await self.raw(payload={
                        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": name, "arguments": arguments},
                    })
                    self.assertEqual((status, payload), (503, {"error": "maintenance_active"}))
                status, payload = await self.raw(send_body=False)
                self.assertEqual((status, payload), (503, {"error": "maintenance_active"}))
                session.assert_not_called()
            self.no_commands.assert_not_called()
            self.no_workers.assert_not_called()
        finally:
            service.leave_maintenance(lease)

        async with Client(self.url, mode="auto", read_timeout_seconds=3) as client:
            catalog = await client.list_tools()
            self.assertEqual({tool.name: tool.model_dump(mode="json") for tool in catalog.tools}, expected)
            await self.harmless_call(client)
        await self.wait_until(lambda: service.maintenance_snapshot()["admission"]["idle_observed"])
        await service.aclose()
        closed = service.maintenance_snapshot()
        self.assertEqual(closed["lifecycle"], "closed")
        self.assertTrue(closed["cleanup_complete"])
        self.assertIsNone(closed["error_code"])
        self.assertIsNone(closed["cleanup_error_code"])
        with self.assertRaises(OSError):
            await asyncio.wait_for(asyncio.open_connection("127.0.0.1", self.launch.port), 2)

    async def test_read_only_catalog_and_maintenance_cycle(self):
        await self.catalog_cycle("read-only")

    async def test_full_chat_catalog_and_maintenance_cycle(self):
        await self.catalog_cycle("full-chat")

    async def test_protocol_and_method_guards_on_real_listener(self):
        await self.start_service()
        for protocol in (None, "2025-11-25"):
            with self.subTest(protocol=protocol):
                status, payload = await self.raw(protocol=protocol, send_body=False)
                self.assertEqual((status, payload), (400, {"error": "unsupported_protocol"}))
        status, payload = await self.raw(extra_headers=(("MCP-Protocol-Version", PROTOCOL),))
        self.assertEqual((status, payload), (400, {"error": "unsupported_protocol"}))
        for method in ("GET", "DELETE"):
            status, payload = await self.raw(method=method, send_body=False)
            self.assertEqual((status, payload), (405, {"error": "method_not_allowed"}))
        status, payload = await self.raw(payload={
            "jsonrpc": "2.0", "id": 1, "method": "subscriptions/listen", "params": {},
        })
        self.assertEqual((status, payload), (405, {"error": "method_not_allowed"}))
        status, _ = await self.raw(path="/control")
        self.assertEqual(status, 404)
        self.no_commands.assert_not_called()
        self.no_workers.assert_not_called()

    async def blocked_events(self, *, disconnect=False):
        service = await self.start_service()
        ctrl = self.seed_session(service)
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = ctrl._session

        def blocked(thread_id):
            entered.set()
            if not release.wait(5):
                raise AssertionError("managed_fixture_interleaving_timeout")
            return original(thread_id)

        message = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "reasonfirst_codex_events",
                       "arguments": {"thread_id": "synthetic-thread"}},
        }
        with patch.object(ctrl, "_session", side_effect=blocked):
            writer = None
            request = None
            if disconnect:
                _, writer = await asyncio.open_connection("127.0.0.1", self.launch.port)
                body = json.dumps(modern_message(message)).encode()
                writer.write((
                    f"POST {self.launch.path} HTTP/1.1\r\nHost: 127.0.0.1:{self.launch.port}\r\n"
                    f"Content-Type: application/json\r\nAccept: application/json\r\n"
                    f"MCP-Protocol-Version: {PROTOCOL}\r\nMCP-Method: tools/call\r\n"
                    f"MCP-Name: reasonfirst_codex_events\r\n"
                    f"Content-Length: {len(body)}\r\n\r\n"
                ).encode() + body)
                await writer.drain()
            else:
                request = asyncio.create_task(self.raw(payload=message))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                self.assertGreater(service.maintenance_snapshot()["admission"]["counts"]["operations"], 0)
                self.busy(service)
                if disconnect:
                    writer.close()
                    await writer.wait_closed()
                    await self.wait_until(lambda: not service._server.server_state.connections)
                    self.assertGreater(service.maintenance_snapshot()["admission"]["counts"]["operations"], 0)
                    self.busy(service)
            finally:
                release.set()
                if writer is not None:
                    writer.close()
                    await writer.wait_closed()
                if request is not None and not request.done():
                    await request
            if request is not None:
                status, payload = await request
                self.assertEqual(status, 200)
                self.assertIn("synthetic fixture", json.dumps(payload))
        await self.wait_until(lambda: service.maintenance_snapshot()["admission"]["counts"]["operations"] == 0)
        if disconnect:
            self.assertTrue(service.maintenance_snapshot()["admission"]["unknown_reasons"])
            self.busy(service)
        else:
            await self.wait_until(lambda: service.maintenance_snapshot()["admission"]["idle_observed"])
            lease = service.try_enter_maintenance()
            service.leave_maintenance(lease)

    async def test_http_and_controller_work_block_maintenance_until_reply(self):
        await self.blocked_events()

    async def test_http_body_is_reserved_before_controller_dispatch(self):
        service = await self.start_service()
        ctrl = self.seed_session(service)
        body = json.dumps(modern_message({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "reasonfirst_codex_events",
                       "arguments": {"thread_id": "synthetic-thread"}},
        })).encode()
        reader, writer = await asyncio.open_connection("127.0.0.1", self.launch.port)
        with patch.object(ctrl, "_session", wraps=ctrl._session) as handler:
            try:
                writer.write((
                    f"POST {self.launch.path} HTTP/1.1\r\nHost: 127.0.0.1:{self.launch.port}\r\n"
                    f"Content-Type: application/json\r\nAccept: application/json\r\n"
                    f"MCP-Protocol-Version: {PROTOCOL}\r\nMCP-Method: tools/call\r\n"
                    f"MCP-Name: reasonfirst_codex_events\r\nContent-Length: {len(body)}\r\n"
                    f"Connection: close\r\n\r\n"
                ).encode() + body[:len(body) // 2])
                await writer.drain()
                await self.wait_until(lambda: service.maintenance_snapshot()["admission"]["counts"]["operations"] == 1)
                handler.assert_not_called()
                self.busy(service)
                writer.write(body[len(body) // 2:])
                await writer.drain()
                async def read_response():
                    received = bytearray()
                    while chunk := await reader.read(65537 - len(received)):
                        received.extend(chunk)
                        self.assertLessEqual(len(received), 65536)
                    return bytes(received)
                response = await asyncio.wait_for(read_response(), 4)
                self.assertTrue(response.startswith(b"HTTP/1.1 200 "))
                self.assertIn(b"synthetic fixture", response)
                handler.assert_called_once_with("synthetic-thread")
            finally:
                writer.close()
                await writer.wait_closed()
        await self.wait_until(lambda: service.maintenance_snapshot()["admission"]["idle_observed"])
        lease = service.try_enter_maintenance()
        service.leave_maintenance(lease)

    async def test_disconnect_keeps_work_counted_and_then_unknown(self):
        await self.blocked_events(disconnect=True)

    async def test_async_turn_survives_http_reply_and_interrupt_ack(self):
        service = await self.start_service("full-chat")
        ctrl = self.seed_session(service)
        target = ExecutionTarget(codex_backend="standalone-local")
        policy = default_worker_policy("codex")
        with patch.object(controller, "AppServerClient", FakeApp):
            app_key, app = ctrl._get_app(target, cwd=str(self.worktree))
        ctrl._state["workspaces"]["synthetic-workspace"] = {
            "kind": "local", "workspace_id": "synthetic-workspace",
            "worktree_path": str(self.worktree), "target": target.to_dict(),
        }
        ctrl._state["sessions"]["synthetic-thread"].update({
            "app_key": app_key, "target": target.to_dict(),
            "worktree_path": str(self.worktree), "codex_cwd": str(self.worktree),
            "worker_policy": policy.to_dict(), "worker_policy_evidence": {"satisfied": True},
        })
        ctrl._app_current_thread[app_key] = "synthetic-thread"
        with patch.object(controller, "_run_json", return_value={"agent_prompt": "synthetic fixture"}):
            async with Client(self.url, mode="auto", read_timeout_seconds=3) as client:
                result = await client.call_tool("reasonfirst_codex_continue", {
                    "thread_id": "synthetic-thread", "goal": "synthetic fixture",
                })
                self.assertFalse(result.is_error)
                self.assertEqual(service.maintenance_snapshot()["admission"]["counts"]["running_turns"], 1)
                self.busy(service)
                result = await client.call_tool("reasonfirst_codex_interrupt", {
                    "thread_id": "synthetic-thread",
                })
                self.assertFalse(result.is_error)
                self.assertEqual(service.maintenance_snapshot()["admission"]["counts"]["stopping_turns"], 1)
                self.busy(service)
        self.assertEqual(app.interrupts, [{"thread_id": "synthetic-thread", "turn_id": "synthetic-turn-1"}])
        app.complete("synthetic-thread", "synthetic-turn-1")
        await self.wait_until(lambda: service.maintenance_snapshot()["admission"]["idle_observed"])
        lease = service.try_enter_maintenance()
        service.leave_maintenance(lease)
        await service.aclose()
        self.assertTrue(app.closed)

    async def test_occupied_listener_survives_failed_startup(self):
        with socket.socket() as listener:
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(2)
            port = listener.getsockname()[1]
            service = service_managed.ManagedBridgeService(
                bridge_http.HTTPLaunch("127.0.0.1", port, "/fixture", "read-only", "disabled"),
            )
            self.services.append(service)
            with self.assertRaises(service_managed.ServiceError):
                await service.start()
            await service.aclose()
            self.assertTrue(service.maintenance_snapshot()["cleanup_complete"])
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                accepted, _ = listener.accept()
                accepted.close()
        self.no_commands.assert_not_called()
        self.no_workers.assert_not_called()

    async def test_invalid_configuration_cleans_failed_startup(self):
        self.bridge_file.write_text("version: [invalid\n", encoding="utf-8")
        self.original_config = (self.env_file.read_bytes(), self.bridge_file.read_bytes())
        service = service_managed.ManagedBridgeService(bridge_http.HTTPLaunch(
            "127.0.0.1", self.unused_port(), "/fixture", "read-only", "disabled",
        ))
        self.services.append(service)
        with self.assertRaises(service_managed.ServiceError):
            await service.start()
        await service.aclose()
        snapshot = service.maintenance_snapshot()
        self.assertFalse(snapshot["listener_serving"])
        self.assertTrue(snapshot["cleanup_complete"])

    async def test_import_origins_match_selected_route(self):
        route = SELECTED_ROUTE or "source"
        expected = SELECTED_ROOT or Path(__file__).resolve().parents[1] / "src"
        verify_origins(route, expected)


class NativeReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "scripts/install_e2e.py"
        spec = importlib.util.spec_from_file_location("managed_install_fixture", path)
        cls.install = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.install)

    def report(self):
        count = len(self.install.MANAGED_HTTP_CASES)
        return {
            "operation": "managed-http-native-acceptance",
            "route": "source", "ok": True, "tests_run": count, "expected_tests": count,
            "failures": 0, "errors": 0, "skipped": 0, "failed_cases": [],
            "modes": list(MODES), "module_origins_verified": True,
            "real_mcp_calls_exercised": True, "all_tools_denied_during_maintenance": True,
            "working_service_touched": False, "activation_tested": False,
        }

    def validate(self, value, *, returncode=0):
        return self.install._managed_http_report(
            json.dumps(value).encode(), route="source", returncode=returncode,
        )

    def test_native_case_gate_matches_real_acceptance_suite(self):
        names = {name for name in dir(ManagedHTTPIntegrationTests) if name.startswith("test_")}
        self.assertEqual(names, self.install.MANAGED_HTTP_CASES)
        self.assertEqual(self.validate(self.report()), self.report())

    def test_false_success_and_unknown_output_are_rejected(self):
        for change in (
            {"tests_run": True}, {"module_origins_verified": 1},
            {"tests_run": 1}, {"skipped": 1}, {"errors": 1},
            {"working_service_touched": True}, {"activation_tested": True},
            {"modes": ["full-chat"]}, {"route": "packaged"},
            {"failed_cases": ["secret-input"]}, {"unknown": "secret-input"},
        ):
            with self.subTest(field=next(iter(change))):
                with self.assertRaisesRegex(RuntimeError, "raw output withheld") as failure:
                    self.validate({**self.report(), **change})
                self.assertNotIn("secret-input", str(failure.exception))
        with self.assertRaises(RuntimeError):
            self.validate(self.report(), returncode=1)

    def test_bounded_failure_names_and_return_code_are_preserved(self):
        payload = self.report()
        payload.update({
            "ok": False, "errors": 1,
            "failed_cases": ["test_occupied_listener_survives_failed_startup"],
            "module_origins_verified": False, "real_mcp_calls_exercised": False,
            "all_tools_denied_during_maintenance": False,
        })
        self.assertEqual(self.validate(payload, returncode=1), payload)
        with self.assertRaises(RuntimeError):
            self.validate(payload)

    def test_report_byte_limit_and_schema_are_strict(self):
        raw = json.dumps(self.report()).encode()
        limit = self.install.MANAGED_HTTP_REPORT_LIMIT
        padded = raw + b" " * (limit - len(raw))
        self.assertEqual(self.install._managed_http_report(padded, route="source", returncode=0), self.report())
        for value in (padded + b" ", b"", b"not JSON", b"[]", raw + raw, b"\xff"):
            with self.subTest(length=len(value)):
                with self.assertRaisesRegex(RuntimeError, "raw output withheld"):
                    self.install._managed_http_report(value, route="source", returncode=0)


def native_main(argv):
    global SELECTED_ROUTE, SELECTED_ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-route", choices=("packaged", "source"), required=True)
    parser.add_argument("--expected-root", type=Path, required=True)
    args = parser.parse_args(argv)
    SELECTED_ROUTE, SELECTED_ROOT = args.native_route, args.expected_root
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ManagedHTTPIntegrationTests)
    expected_count = suite.countTestCases()
    transcript = io.StringIO()
    with redirect_stdout(transcript), redirect_stderr(transcript):
        result = unittest.TextTestRunner(stream=transcript, verbosity=0).run(suite)
    allowed_cases = {
        name for name in dir(ManagedHTTPIntegrationTests) if name.startswith("test_")
    }
    failures = sorted({
        getattr(case, "_testMethodName", "fixture_failed")
        for case, _ in [*result.failures, *result.errors]
    })
    failures = [name if name in allowed_cases else "fixture_failed" for name in failures]
    ok = result.wasSuccessful() and not result.skipped and result.testsRun == expected_count
    print(json.dumps({
        "operation": "managed-http-native-acceptance",
        "route": args.native_route, "ok": ok,
        "tests_run": result.testsRun, "expected_tests": expected_count,
        "failures": len(result.failures), "errors": len(result.errors),
        "skipped": len(result.skipped), "failed_cases": failures,
        "modes": list(MODES),
        "module_origins_verified": ok,
        "real_mcp_calls_exercised": ok,
        "all_tools_denied_during_maintenance": ok,
        "working_service_touched": False, "activation_tested": False,
    }, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--native-route" in sys.argv:
        raise SystemExit(native_main(sys.argv[1:]))
    unittest.main(verbosity=2)
