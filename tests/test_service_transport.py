"""Request ownership and kernel-listener tests for the internal service adapter."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import json
import logging
import platform
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from gitlab_agent.bridge_http import HTTPLaunch
from gitlab_agent.upgrade import service_managed as managed


def launch(port=43219):
    return HTTPLaunch("127.0.0.1", port, "/mcp", "read-only", "disabled")


def scope(*, path="/mcp", method="POST", headers=None):
    return {"type": "http", "method": method, "path": path,
            "headers": headers if headers is not None else [
                (b"mcp-protocol-version", b"2026-07-28"), (b"mcp-method", b"tools/list")],
            "http_version": "1.1", "scheme": "http"}


async def reply(send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"{}"})


class ServiceRequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.object(managed, "_observe_listener"))
        self.owner = managed.ManagedBridgeService(launch())
        self.owner._admission.claim_controller()
        self.owner._state = "running"
        self.owner._server_task = SimpleNamespace(done=lambda: False)
        self.messages = []
        self.input = asyncio.Queue()

    async def send(self, message):
        self.messages.append(message)

    def counts(self):
        return self.owner.maintenance_snapshot()["admission"]["counts"]

    def assert_busy(self):
        with self.assertRaises(managed.ServiceError) as raised:
            self.owner.try_enter_maintenance()
        self.assertEqual(raised.exception.code, "maintenance_busy")

    async def test_reserves_before_body_or_handler_and_through_final_send(self):
        entered, final_send, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def app(_scope, receive, send):
            entered.set()
            await receive()
            await reply(send)

        async def blocked_send(message):
            if message["type"] == "http.response.body":
                final_send.set()
                await release.wait()
            await self.send(message)

        task = asyncio.create_task(managed._AdmissionApp(self.owner, app)(
            scope(), self.input.get, blocked_send))
        await entered.wait()
        self.assertEqual(self.counts()["operations"], 1)
        self.assert_busy()
        self.input.put_nowait({"type": "http.request", "body": b"{}"})
        await final_send.wait()
        self.assertEqual(self.counts()["operations"], 1)
        self.assert_busy()
        release.set()
        await task
        self.assertEqual(self.counts()["operations"], 0)
        lease = self.owner.try_enter_maintenance()
        self.assertTrue(self.owner.maintenance_snapshot()["maintenance_window_held"])
        self.owner.leave_maintenance(lease)

    async def test_held_lease_refuses_post_before_body_and_handler_then_reopens(self):
        invoked = []

        async def app(_scope, receive, send):
            invoked.append(True)
            await receive()
            await reply(send)

        receive = Mock(side_effect=AssertionError("must not read a refused body"))
        guarded = managed._AdmissionApp(self.owner, app)
        lease = self.owner.try_enter_maintenance()
        await guarded(scope(), receive, self.send)
        receive.assert_not_called()
        self.assertEqual(invoked, [])
        self.assertEqual(self.messages[0]["status"], 503)
        self.assertEqual(json.loads(self.messages[1]["body"]), {"error": "maintenance_active"})
        self.assertTrue(self.owner.maintenance_snapshot()["maintenance_window_held"])
        self.owner.leave_maintenance(lease)
        self.input.put_nowait({"type": "http.request", "body": b"{}"})
        await guarded(scope(), self.input.get, self.send)
        self.assertEqual(invoked, [True])
        self.assertEqual(self.counts()["operations"], 0)

    async def test_disconnect_does_not_release_inflight_work_and_latches_unknown(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def app(_scope, receive, send):
            await receive()
            entered.set()
            await release.wait()
            await reply(send)

        self.input.put_nowait({"type": "http.request", "body": b"{}"})
        task = asyncio.create_task(managed._AdmissionApp(self.owner, app)(
            scope(), self.input.get, self.send))
        await entered.wait()
        self.input.put_nowait({"type": "http.disconnect"})
        await asyncio.sleep(0)
        self.assertEqual(self.counts()["operations"], 1)
        self.assert_busy()
        release.set()
        await task
        self.assertEqual(self.counts()["operations"], 0)
        self.assertIn("transport_lost", self.owner.maintenance_snapshot()["admission"]["unknown_reasons"])
        self.assert_busy()

    async def test_cancelled_request_retires_reservation_with_uncertainty(self):
        entered = asyncio.Event()

        async def app(_scope, receive, _send):
            await receive()
            entered.set()
            await asyncio.Event().wait()

        self.input.put_nowait({"type": "http.request", "body": b"{}"})
        task = asyncio.create_task(managed._AdmissionApp(self.owner, app)(
            scope(), self.input.get, self.send))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.counts()["operations"], 0)
        self.assert_busy()

    async def test_failed_final_write_is_unknown_even_after_handler_finishes(self):
        async def app(_scope, receive, send):
            await receive()
            await reply(send)

        async def failed_send(message):
            if message["type"] == "http.response.body":
                raise OSError("private-wire-sentinel")

        self.input.put_nowait({"type": "http.request", "body": b"{}"})
        with self.assertRaises(OSError):
            await managed._AdmissionApp(self.owner, app)(scope(), self.input.get, failed_send)
        self.assertEqual(self.counts()["operations"], 0)
        self.assert_busy()
        self.assertNotIn("private-wire-sentinel", json.dumps(self.owner.maintenance_snapshot()))

    async def test_no_final_response_is_not_successful_request_cleanup(self):
        async def app(_scope, _receive, _send):
            return

        await managed._AdmissionApp(self.owner, app)(scope(), self.input.get, self.send)
        self.assert_busy()
        self.assertEqual(self.counts()["operations"], 0)

    async def test_protocol_and_session_guards_do_not_dispatch_or_read_body(self):
        base = [(b"mcp-protocol-version", b"2026-07-28"), (b"mcp-method", b"tools/list")]
        cases = [
            ([], 400, "unsupported_protocol"),
            ([(b"mcp-protocol-version", b"2025-11-25"), base[1]], 400, "unsupported_protocol"),
            (base + [base[0]], 400, "unsupported_protocol"),
            (base + [(b"MCP-PROTOCOL-VERSION", b"2026-07-28")], 400, "unsupported_protocol"),
            ([base[0]], 400, "invalid_request"),
            (base + [base[1]], 400, "invalid_request"),
            ([base[0], (b"mcp-method", b"subscriptions/listen")], 405, "method_not_allowed"),
            (base + [(b"mcp-session-id", b"caller-secret")], 400, "unsupported_protocol"),
        ]
        for headers, status, code in cases:
            with self.subTest(headers=headers):
                self.messages.clear()
                app = Mock(side_effect=AssertionError("must not dispatch"))
                receive = Mock(side_effect=AssertionError("must not read"))
                await managed._AdmissionApp(self.owner, app)(scope(headers=headers), receive, self.send)
                app.assert_not_called()
                receive.assert_not_called()
                self.assertEqual(self.messages[0]["status"], status)
                self.assertEqual(json.loads(self.messages[1]["body"]), {"error": code})
                self.assertNotIn("caller-secret", str(self.messages))
                self.assertEqual(self.counts()["operations"], 0)
                self.assertEqual(self.owner.maintenance_snapshot()["admission"]["unknown_reasons"], [])

    async def test_only_exact_health_get_bypasses_post_admission(self):
        called = []

        async def app(request_scope, _receive, send):
            called.append(request_scope["path"])
            await reply(send)

        guarded = managed._AdmissionApp(self.owner, app)
        lease = self.owner.try_enter_maintenance()
        await guarded(scope(path="/healthz", method="GET"), self.input.get, self.send)
        self.assertEqual(called, ["/healthz"])
        for path, method, status in [("/mcp", "GET", 405), ("/mcp", "DELETE", 405),
                                     ("/mcp/", "POST", 404), ("/control", "POST", 404),
                                     ("/healthz", "POST", 404)]:
            self.messages.clear()
            await guarded(scope(path=path, method=method), self.input.get, self.send)
            self.assertEqual(self.messages[0]["status"], status)
        self.assertEqual(called, ["/healthz"])
        self.assertTrue(self.owner.maintenance_snapshot()["maintenance_window_held"])
        self.assertEqual(self.counts()["operations"], 0)
        self.owner.leave_maintenance(lease)

    async def test_body_chunks_remain_owned_until_last_chunk_and_response(self):
        first, release = asyncio.Event(), asyncio.Event()

        async def app(_scope, receive, send):
            message = await receive()
            self.assertTrue(message["more_body"])
            first.set()
            await release.wait()
            self.assertFalse((await receive()).get("more_body", False))
            await reply(send)

        self.input.put_nowait({"type": "http.request", "body": b"{", "more_body": True})
        self.input.put_nowait({"type": "http.request", "body": b"}"})
        task = asyncio.create_task(managed._AdmissionApp(self.owner, app)(scope(), self.input.get, self.send))
        await first.wait()
        self.assert_busy()
        release.set()
        await task
        self.assertEqual(self.counts()["operations"], 0)
        self.assertEqual(self.owner.maintenance_snapshot()["admission"]["unknown_reasons"], [])


class ServiceSocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_h11_disconnect_before_queued_watcher_cannot_report_idle(self):
        owner = managed.ManagedBridgeService(launch())
        owner._admission.claim_controller()
        owner._state = "running"
        owner._server_task = SimpleNamespace(done=lambda: False)
        resume_handler, watcher_waiting = asyncio.Event(), asyncio.Event()

        async def app(_scope, receive, send):
            await receive()
            await resume_handler.wait()
            await reply(send)

        class ObservedAdmissionApp(managed._AdmissionApp):
            async def __call__(self, request_scope, receive, send):
                calls = 0

                async def observed_receive():
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        # The handler read the complete body; this second
                        # read is the watcher entering actual h11.receive.
                        watcher_waiting.set()
                    return await receive()

                await super().__call__(request_scope, observed_receive, send)

        server = managed._create_server(ObservedAdmissionApp(owner, app), launch())
        server.config.load()
        protocol = server.config.http_protocol_class(
            config=server.config, server_state=server.server_state,
            app_state={}, _loop=asyncio.get_running_loop())
        protocol.logger = Mock(level=logging.CRITICAL)
        transport = Mock()
        transport.get_extra_info.return_value = None
        transport.is_closing.return_value = False
        protocol.connection_made(transport)
        request_task = None
        try:
            with patch.object(managed, "_observe_listener"):
                protocol.data_received(
                    b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 2\r\n"
                    b"MCP-Protocol-Version: 2026-07-28\r\nMCP-Method: tools/list\r\n\r\n{}")
                request_task = next(iter(server.server_state.tasks))
                await asyncio.wait_for(watcher_waiting.wait(), timeout=2)
                cycle = protocol.cycle
                self.assertFalse(cycle.response_complete)
                # Put the handler continuation first in the ready queue, then
                # record disconnect and wake the watcher behind it. No sleeps
                # or guessed network timing determine this ordering.
                resume_handler.set()
                cycle.disconnected = True
                cycle.message_event.set()
                await request_task
                transport.write.assert_not_called()
                self.assertFalse(cycle.response_complete)
                admission = owner.maintenance_snapshot()["admission"]
                self.assertEqual(admission["counts"]["operations"], 0)
                self.assertFalse(admission["idle_observed"])
                self.assertIn("transport_lost", admission["unknown_reasons"])
                with self.assertRaises(managed.ServiceError) as raised:
                    owner.try_enter_maintenance()
                self.assertEqual(raised.exception.code, "maintenance_busy")
        finally:
            if request_task is not None and not request_task.done():
                request_task.cancel()
                await asyncio.gather(request_task, return_exceptions=True)
            protocol.connection_lost(None)
            owner._admission.close()

    async def test_failed_server_cleanup_still_closes_owned_connections_controller_and_socket(self):
        owner = managed.ManagedBridgeService(launch())
        owner._admission.claim_controller()
        owner._state = "closing"
        owner._error_code = "service_startup_failed"
        owner._socket = Mock()
        owner._controller = Mock()
        listener = SimpleNamespace(close=Mock(), wait_closed=AsyncMock())
        connection = Mock()
        connection.shutdown.side_effect = KeyboardInterrupt()
        owner._server = SimpleNamespace(should_exit=False, servers=[listener],
            server_state=SimpleNamespace(connections={connection}, tasks=set()))
        owner._server_task = asyncio.get_running_loop().create_future()
        owner._server_task.set_exception(managed.ServiceError("service_exited"))
        await owner._cleanup()
        connection.transport.close.assert_called_once()
        listener.wait_closed.assert_awaited_once()
        owner._controller.close.assert_called_once()
        owner._socket.close.assert_called_once()
        report = owner.maintenance_snapshot()
        self.assertEqual(report["error_code"], "service_startup_failed")
        self.assertEqual(report["cleanup_error_code"], "server_cleanup_failed")
        self.assertFalse(report["cleanup_complete"])

    async def test_known_server_failure_does_not_manufacture_cleanup_failure(self):
        owner = managed.ManagedBridgeService(launch())
        owner._admission.claim_controller()
        owner._state = "closing"
        owner._error_code = "service_startup_failed"
        owner._socket = Mock()
        owner._controller = Mock()
        listener = SimpleNamespace(close=Mock(), wait_closed=AsyncMock())
        owner._server = SimpleNamespace(should_exit=False, servers=[listener],
            server_state=SimpleNamespace(connections=set(), tasks=set()))
        owner._server_task = asyncio.get_running_loop().create_future()
        owner._server_task.set_exception(managed.ServiceError("service_exited"))
        await owner._cleanup()
        listener.wait_closed.assert_awaited_once()
        owner._controller.close.assert_called_once()
        report = owner.maintenance_snapshot()
        self.assertEqual(report["error_code"], "service_startup_failed")
        self.assertIsNone(report["cleanup_error_code"])
        self.assertTrue(report["cleanup_complete"])

    async def test_real_owned_socket_changes_only_after_actual_server_binding(self):
        managed._require_supported()
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        selected = launch(probe.getsockname()[1])
        probe.close()
        sock = managed._bind_socket(selected)
        server = None
        try:
            self.assertFalse(sock.get_inheritable())
            self.assertFalse(managed._socket_listening(sock))
            server = await asyncio.get_running_loop().create_server(asyncio.Protocol, sock=sock)
            self.assertTrue(managed._socket_listening(sock))
            observed = SimpleNamespace(started=True, should_exit=False, servers=[server])
            managed._observe_listener(observed, sock, selected)
            observed.should_exit = True
            with self.assertRaises(managed.ServiceError) as raised:
                managed._observe_listener(observed, sock, selected)
            self.assertEqual(raised.exception.code, "listener_not_serving")
        finally:
            if server is not None:
                server.close()
                await server.wait_closed()
            sock.close()

    async def test_occupied_and_listening_decoys_remain_owned_and_open(self):
        managed._require_supported()
        for listening in (False, True):
            with self.subTest(listening=listening):
                with socket.socket() as decoy:
                    if platform.system() == "Windows":
                        decoy.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    decoy.bind(("127.0.0.1", 0))
                    if listening:
                        decoy.listen()
                    selected = launch(decoy.getsockname()[1])
                    original = decoy.fileno()
                    with self.assertRaises(managed.ServiceError) as raised:
                        managed._bind_socket(selected)
                    self.assertEqual(raised.exception.code, "listener_bind_failed")
                    self.assertEqual(decoy.fileno(), original)
                    self.assertEqual(managed._socket_listening(decoy), listening)

    def test_darwin_query_uses_one_public_state_byte_and_fails_closed(self):
        with patch.object(managed.platform, "system", return_value="Darwin"), \
                patch.object(managed.socket, "TCP_CONNECTION_INFO", 0x106, create=True):
            for value, expected in [(b"\x00", False), (b"\x01", True)]:
                sock = Mock()
                sock.getsockopt.return_value = value
                self.assertIs(managed._socket_listening(sock), expected)
                sock.getsockopt.assert_called_once_with(socket.IPPROTO_TCP, 0x106, 1)
            for value in (b"", b"\x01\x00", b"\x04", 1, True):
                sock = Mock()
                sock.getsockopt.return_value = value
                with self.assertRaises(managed.ServiceError) as raised:
                    managed._socket_listening(sock)
                self.assertEqual(raised.exception.code, "listener_observation_failed")

    def test_windows_requires_exclusive_bind_and_accept_query_capability(self):
        with patch.object(managed.platform, "system", return_value="Windows"), \
                patch.object(managed.os, "name", "nt"), \
                patch.object(managed.socket, "SO_EXCLUSIVEADDRUSE", None, create=True):
            with self.assertRaises(managed.ServiceError) as raised:
                managed._require_supported()
            self.assertEqual(raised.exception.code, "unsupported_listener_query")
        with patch.object(managed.platform, "system", return_value="Windows"), \
                patch.object(managed.socket, "SO_EXCLUSIVEADDRUSE", 0xFFFF, create=True), \
                patch.object(managed.socket, "socket") as factory:
            factory.return_value.getsockopt.return_value = 0
            managed._bind_socket(launch())
            calls = factory.return_value.mock_calls
            exclusive = unittest.mock.call.setsockopt(socket.SOL_SOCKET, 0xFFFF, 1)
            self.assertLess(calls.index(exclusive), calls.index(unittest.mock.call.bind(("127.0.0.1", 43219))))

    def test_socket_query_errors_never_become_nonlistening_evidence(self):
        sock = Mock()
        sock.getsockopt.side_effect = OSError("private-kernel-sentinel")
        with self.assertRaises(managed.ServiceError) as raised:
            managed._socket_listening(sock)
        self.assertEqual(str(raised.exception), "listener_observation_failed")

    def test_internal_server_never_changes_signal_handlers(self):
        server = managed._create_server(Mock(), launch())
        with patch("signal.signal") as change:
            with server.capture_signals():
                pass
        change.assert_not_called()
        self.assertEqual(server.config.http, "h11")
        self.assertEqual(server.config.ws, "none")

    async def test_internal_server_leaves_host_logger_configuration_untouched(self):
        loggers = [logging.getLogger(name) for name in
                   ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi")]
        saved = [(logger.level, logger.handlers[:], logger.propagate, logger.disabled)
                 for logger in loggers]
        try:
            for logger in loggers:
                logger.setLevel(logging.DEBUG)
                logger.handlers = [logging.NullHandler()]
                logger.propagate = True
                logger.disabled = False
            configured = [(logger.level, logger.handlers[:], logger.propagate, logger.disabled)
                          for logger in loggers]
            server = managed._create_server(Mock(), launch())
            server.config.load()
            protocol = server.config.http_protocol_class(
                config=server.config, server_state=server.server_state,
                app_state={}, _loop=asyncio.get_running_loop())
            self.assertFalse(protocol.access_log)
            self.assertEqual([(logger.level, logger.handlers[:], logger.propagate, logger.disabled)
                              for logger in loggers], configured)
        finally:
            for logger, (level, handlers, propagate, disabled) in zip(loggers, saved):
                logger.setLevel(level)
                logger.handlers = handlers
                logger.propagate = propagate
                logger.disabled = disabled


if __name__ == "__main__":
    unittest.main()
