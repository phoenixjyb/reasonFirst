from __future__ import annotations

import io
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from gitlab_agent.codex_app_server import AppServerClient, AppServerError


APPROVAL = {"id": 7, "method": "item/commandExecution/requestApproval",
            "params": {"threadId": "thread-1"}}
DYNAMIC = {"id": 8, "method": "item/tool/call",
           "params": {"threadId": "thread-1", "namespace": "reasonfirst_remote", "tool": "write"}}


class Wire:
    def __init__(self):
        self.sent = []
        self.before_send = None
        self.input = []
        self.closed = 0

    def send(self, payload):
        message = json.loads(payload)
        if self.before_send is not None:
            self.before_send(message)
        self.sent.append(message)

    def __iter__(self):
        return iter(self.input)

    def close(self):
        self.closed += 1


class Activity:
    def __init__(self):
        self.active = set()
        self.finished = []
        self.done = threading.Event()

    def track(self, message):
        identity = message["id"]
        self.active.add(identity)

        def finish(sent):
            self.active.remove(identity)
            self.finished.append(sent)
            self.done.set()

        return finish


class AppServerActivityTests(unittest.TestCase):
    def make_client(self, *, tracker=None, lost=None, approval=None, dynamic=None, event=None):
        wire = Wire()
        with patch.object(AppServerClient, "_connect_unix_socket",
                          lambda client, _path: setattr(client, "ws", wire)), \
                patch.object(AppServerClient, "_initialize"), \
                patch("gitlab_agent.codex_app_server.threading.Thread"):
            client = AppServerClient(
                codex_bin="synthetic", unix_socket="synthetic",
                server_request_tracker=tracker, transport_lost_handler=lost,
                approval_request_handler=approval, server_request_handler=dynamic,
                event_handler=event,
            )
        self.addCleanup(client.close)
        return client, wire

    def dispatch(self, client, message):
        workers = []
        real_thread = threading.Thread

        def create(**kwargs):
            worker = real_thread(**kwargs)
            workers.append(worker)
            return worker

        with patch("gitlab_agent.codex_app_server.threading.Thread", side_effect=create):
            client._handle_message(dict(message))
        return workers

    def join_workers(self, workers):
        for worker in workers:
            worker.join(2)
            self.assertFalse(worker.is_alive(), "owned test callback did not finish")

    def test_activity_is_reserved_before_callback_thread_is_started(self):
        for message in (APPROVAL, DYNAMIC):
            with self.subTest(method=message["method"]):
                activity = Activity()
                handler = Mock(return_value={"decision": "accept"})
                client, wire = self.make_client(tracker=activity.track, lost=Mock(),
                                                approval=handler, dynamic=handler)
                queued = []

                def create(**kwargs):
                    self.assertEqual(activity.active, {message["id"]})
                    handler.assert_not_called()
                    queued.append(kwargs)
                    return SimpleNamespace(start=lambda: None)

                with patch("gitlab_agent.codex_app_server.threading.Thread", side_effect=create):
                    client._handle_message(dict(message))
                self.assertEqual(activity.finished, [])
                self.assertEqual(wire.sent, [])
                handler.assert_not_called()
                queued[0]["target"](*queued[0]["args"])
                self.assertEqual(activity.finished, [True])
                self.assertEqual(len(wire.sent), 1)

    def assert_response_gap_counted(self, message):
        activity = Activity()
        handler_returned = threading.Event()
        write_entered = threading.Event()
        allow_write = threading.Event()

        def handler(_message):
            handler_returned.set()
            return {"decision": "accept"} if message is APPROVAL else {"success": True}

        client, wire = self.make_client(tracker=activity.track, lost=Mock(),
                                        approval=handler, dynamic=handler)

        def send(_message):
            write_entered.set()
            if not allow_write.wait(2):
                raise RuntimeError("test write barrier timed out")

        wire.before_send = send
        workers = self.dispatch(client, message)
        try:
            self.assertTrue(handler_returned.wait(2))
            self.assertTrue(write_entered.wait(2))
            self.assertEqual(activity.active, {message["id"]})
            self.assertFalse(activity.done.is_set())
            self.assertEqual(wire.sent, [])
        finally:
            allow_write.set()
            self.join_workers(workers)
        self.assertEqual(activity.finished, [True])
        self.assertEqual(activity.active, set())

    def test_approval_remains_counted_after_handler_returns_until_response_write(self):
        self.assert_response_gap_counted(APPROVAL)

    def test_dynamic_callback_remains_counted_until_response_write(self):
        self.assert_response_gap_counted(DYNAMIC)

    def test_failed_primary_write_stays_uncertain_even_if_fallback_write_succeeds(self):
        for message in (APPROVAL, DYNAMIC):
            with self.subTest(method=message["method"]):
                activity, lost = Activity(), Mock()
                client, wire = self.make_client(tracker=activity.track, lost=lost,
                    approval=lambda _msg: {"decision": "accept"}, dynamic=lambda _msg: {"success": True})
                calls = []

                def send(value):
                    calls.append(value)
                    if len(calls) == 1:
                        raise OSError("synthetic broken write")

                wire.before_send = send
                self.join_workers(self.dispatch(client, message))
                self.assertEqual(len(calls), 2)
                self.assertEqual(len(wire.sent), 1)
                self.assertEqual(activity.finished, [False])
                lost.assert_called_once_with()

    def test_handler_failure_can_deliver_existing_fallback_before_finalization(self):
        activity = Activity()
        client, wire = self.make_client(tracker=activity.track, lost=Mock(),
            approval=Mock(side_effect=ValueError("synthetic handler failure")))
        self.join_workers(self.dispatch(client, APPROVAL))
        self.assertEqual(wire.sent, [{"id": 7, "result": {"decision": "decline"}}])
        self.assertEqual(activity.finished, [True])

    def test_tracker_rejection_never_starts_handler_and_uses_existing_decline(self):
        for rejected in (RuntimeError("PRIVATE-HOOK-SENTINEL"), SystemExit("PRIVATE-HOOK-SENTINEL")):
            with self.subTest(exception=type(rejected).__name__):
                handler, lost = Mock(), Mock()
                client, wire = self.make_client(tracker=Mock(side_effect=rejected), lost=lost,
                                                approval=handler, dynamic=handler)
                with patch("gitlab_agent.codex_app_server.threading.Thread") as worker:
                    client._handle_message(dict(APPROVAL))
                worker.assert_not_called()
                handler.assert_not_called()
                self.assertEqual(wire.sent, [{"id": 7, "result": {"decision": "decline"}}])
                lost.assert_called_once_with()
                self.assertNotIn("PRIVATE-HOOK-SENTINEL", json.dumps(client._stderr_tail))

    def test_invalid_finalizer_rejects_before_handler(self):
        handler, lost = Mock(), Mock()
        client, wire = self.make_client(tracker=lambda _msg: None, lost=lost, dynamic=handler)
        client._handle_message(dict(DYNAMIC))
        handler.assert_not_called()
        self.assertEqual(wire.sent, [{"id": 8, "result": {"contentItems": [], "success": False}}])
        lost.assert_called_once_with()

    def test_thread_start_failure_finishes_uncertain_without_handler(self):
        activity, handler, lost = Activity(), Mock(), Mock()
        client, wire = self.make_client(tracker=activity.track, lost=lost, approval=handler)
        with patch("gitlab_agent.codex_app_server.threading.Thread") as worker:
            worker.return_value.start.side_effect = RuntimeError("PRIVATE-HOOK-SENTINEL")
            client._handle_message(dict(APPROVAL))
        handler.assert_not_called()
        self.assertEqual(activity.finished, [False])
        self.assertEqual(activity.active, set())
        self.assertEqual(wire.sent, [{"id": 7, "result": {"decision": "decline"}}])
        lost.assert_called_once_with()
        self.assertNotIn("PRIVATE-HOOK-SENTINEL", json.dumps(client._stderr_tail))

    def test_finalizer_error_notifies_unknown_without_exposing_hook_text(self):
        lost = Mock()
        finalizer = Mock(side_effect=SystemExit("PRIVATE-HOOK-SENTINEL"))
        client, wire = self.make_client(tracker=lambda _msg: finalizer, lost=lost,
            approval=lambda _msg: {"decision": "accept"})
        self.join_workers(self.dispatch(client, APPROVAL))
        finalizer.assert_called_once_with(True)
        lost.assert_called_once_with()
        self.assertEqual(wire.sent[0]["result"], {"decision": "accept"})
        self.assertNotIn("PRIVATE-HOOK-SENTINEL", json.dumps(client._stderr_tail))

    def test_eof_without_pending_rpc_and_explicit_close_notify_once(self):
        for transport in ("websocket", "stdio"):
            with self.subTest(transport=transport):
                lost = Mock()
                client, _wire = self.make_client(lost=lost)
                if transport == "websocket":
                    client._read_ws_loop()
                else:
                    client.ws = None
                    client.proc = SimpleNamespace(stdout=io.StringIO(""))
                    client._read_stdio_loop()
                self.assertEqual(client._pending, {})
                self.assertTrue(client._closed)
                client._mark_closed()
                client.close()
                lost.assert_called_once_with()

    def test_protocol_loss_is_notified_before_error_event(self):
        for transport in ("websocket", "stdio", "non_object"):
            with self.subTest(transport=transport):
                order = []
                client, wire = self.make_client(lost=lambda: order.append("lost"),
                                                event=lambda _event: order.append("event"))
                if transport == "non_object":
                    client._handle_message([])
                    self.assertEqual(order, ["lost"])
                    continue
                if transport == "websocket":
                    wire.input = ["not-json"]
                    client._read_ws_loop()
                else:
                    client.ws = None
                    client.proc = SimpleNamespace(stdout=io.StringIO("not-json\n"))
                    client._read_stdio_loop()
                self.assertEqual(order, ["lost", "event"])

    def test_loss_notification_finishes_before_pending_waiters_wake(self):
        entered, release, second_done = threading.Event(), threading.Event(), threading.Event()
        notifications = []

        def lost():
            entered.set()
            if not release.wait(2):
                raise RuntimeError("test observer barrier timed out")
            notifications.append("lost")

        client, _wire = self.make_client(lost=lost)
        pending = queue.Queue(maxsize=1)
        client._pending[1] = pending
        first = threading.Thread(target=client._mark_closed)
        second = threading.Thread(target=lambda: (client._mark_closed(), second_done.set()))
        first.start()
        try:
            self.assertTrue(entered.wait(2))
            second.start()
            self.assertFalse(second_done.wait(.05))
            self.assertTrue(pending.empty())
        finally:
            release.set()
            self.join_workers([first, second] if second.ident is not None else [first])
        self.assertEqual(notifications, ["lost"])
        self.assertEqual(pending.get_nowait()["error"]["code"], -32099)

    def test_loss_observer_error_closes_and_fails_pending_with_fixed_diagnostic(self):
        lost = Mock(side_effect=RuntimeError("PRIVATE-HOOK-SENTINEL"))
        client, _wire = self.make_client(lost=lost)
        pending = queue.Queue(maxsize=1)
        client._pending[1] = pending
        client._notify_transport_lost()
        self.assertTrue(client._closed)
        self.assertEqual(pending.get_nowait()["error"]["code"], -32099)
        client.close()
        lost.assert_called_once_with()
        self.assertEqual(client._stderr_tail, ["activity_hook_failed"])

    def test_rpc_timeout_latches_unknown_without_closing_usable_transport(self):
        lost = Mock()
        client, wire = self.make_client(lost=lost)
        with self.assertRaisesRegex(AppServerError, "Timed out"):
            client.request("turn/start", timeout=.001)
        lost.assert_called_once_with()
        self.assertFalse(client._closed)
        self.assertEqual(client._pending, {})
        self.assertEqual(wire.sent[0]["method"], "turn/start")

    def test_rpc_write_failure_notifies_loss_before_escaping(self):
        lost = Mock()
        client, wire = self.make_client(lost=lost)
        wire.before_send = Mock(side_effect=OSError("synthetic broken transport"))
        with self.assertRaises(OSError):
            client.request("turn/start", timeout=.01)
        lost.assert_called_once_with()
        self.assertEqual(client._pending, {})

    def test_managed_event_handler_failure_is_fixed_and_marks_unknown(self):
        lost = Mock()
        client, _wire = self.make_client(lost=lost,
            event=Mock(side_effect=KeyboardInterrupt("PRIVATE-HOOK-SENTINEL")))
        client._emit_event({"method": "turn/completed"})
        lost.assert_called_once_with()
        self.assertEqual(client._stderr_tail, ["activity_hook_failed"])

    def test_no_hook_response_and_thread_start_error_semantics_are_unchanged(self):
        client, wire = self.make_client(approval=lambda _msg: {"decision": "accept"})
        self.join_workers(self.dispatch(client, APPROVAL))
        self.assertEqual(wire.sent, [{"id": 7, "result": {"decision": "accept"}}])
        error = RuntimeError("original dispatch failure")
        with patch("gitlab_agent.codex_app_server.threading.Thread") as worker:
            worker.return_value.start.side_effect = error
            with self.assertRaises(RuntimeError) as caught:
                client._handle_message(dict(APPROVAL))
        self.assertIs(caught.exception, error)

    def test_invalid_hooks_fail_before_launch(self):
        with patch("gitlab_agent.codex_app_server.subprocess.Popen") as launch, \
                patch("gitlab_agent.codex_app_server.resolve_codex_binary") as resolve:
            with self.assertRaisesRegex(AppServerError, "^invalid_activity_hook$"):
                AppServerClient(server_request_tracker=object())
        launch.assert_not_called()
        resolve.assert_not_called()

    def test_all_factory_routes_forward_configured_hooks(self):
        tracker, lost = Mock(), Mock()
        socket_path = Mock()
        socket_path.exists.return_value = True
        factories = (
            (lambda: AppServerClient.global_config_local(server_request_tracker=tracker, transport_lost_handler=lost), {}),
            (lambda: AppServerClient.remote_ssh("synthetic", server_request_tracker=tracker, transport_lost_handler=lost), {}),
            (lambda: AppServerClient.desktop_preferred(server_request_tracker=tracker, transport_lost_handler=lost), {"socket": True}),
            (lambda: AppServerClient.desktop_preferred(server_request_tracker=tracker, transport_lost_handler=lost), {"bundled": True}),
            (lambda: AppServerClient.desktop_preferred(server_request_tracker=tracker, transport_lost_handler=lost), {}),
        )
        for factory, profile in factories:
            with self.subTest(profile=profile), \
                    patch.object(AppServerClient, "__init__", return_value=None) as create, \
                    patch("gitlab_agent.codex_app_server.resolve_desktop_or_codex_binary", return_value="synthetic"), \
                    patch("gitlab_agent.codex_app_server._reasonfirst_mcp_configured", return_value=False), \
                    patch("gitlab_agent.codex_app_server.managed_app_server_socket", return_value=socket_path), \
                    patch.object(Path, "is_file", return_value=profile.get("bundled", False)), \
                    patch.object(Path, "resolve", return_value=Path("/synthetic-codex")), \
                    patch("gitlab_agent.codex_app_server.os.access", return_value=True):
                socket_path.exists.return_value = profile.get("socket", False)
                factory()
                self.assertIs(create.call_args.kwargs["server_request_tracker"], tracker)
                self.assertIs(create.call_args.kwargs["transport_lost_handler"], lost)

    def test_uninstrumented_factory_omits_new_keyword_arguments(self):
        with patch.object(AppServerClient, "__init__", return_value=None) as create, \
                patch("gitlab_agent.codex_app_server.resolve_desktop_or_codex_binary", return_value="synthetic"), \
                patch("gitlab_agent.codex_app_server._reasonfirst_mcp_configured", return_value=False):
            AppServerClient.global_config_local()
        self.assertNotIn("server_request_tracker", create.call_args.kwargs)
        self.assertNotIn("transport_lost_handler", create.call_args.kwargs)

    def test_failed_managed_initialization_reaps_owned_process_despite_closed_flag(self):
        created, observed = [], []
        real_popen = subprocess.Popen
        primary = RuntimeError("original initialization failure")

        def launch(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            created.append(proc)
            return proc

        def initialize(client):
            client._closed = True
            raise primary

        try:
            with patch("gitlab_agent.codex_app_server.subprocess.Popen", side_effect=launch), \
                    patch.object(AppServerClient, "_initialize", new=initialize):
                with self.assertRaises(RuntimeError) as caught:
                    AppServerClient(
                        launch_argv=[sys._base_executable, "-I", "-B", "-c", "import time; time.sleep(30)"],
                        transport_lost_handler=lambda: observed.append(created[0].poll()),
                    )
            self.assertIs(caught.exception, primary)
            self.assertEqual(observed, [None], "unknown must be notified before owned termination")
            self.assertEqual(len(created), 1)
            self.assertIsNotNone(created[0].poll())
            self.assertTrue(all(stream.closed for stream in (
                created[0].stdin, created[0].stdout, created[0].stderr,
            )))
        finally:
            for proc in created:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=2)

    def test_failed_reader_start_reaps_process_that_constructor_cannot_return(self):
        created = []
        real_popen = subprocess.Popen
        primary = RuntimeError("original thread start failure")
        lost = Mock()

        def launch(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            created.append(proc)
            return proc

        try:
            with patch("gitlab_agent.codex_app_server.subprocess.Popen", side_effect=launch), \
                    patch("gitlab_agent.codex_app_server.threading.Thread.start", side_effect=primary):
                with self.assertRaises(RuntimeError) as caught:
                    AppServerClient(
                        launch_argv=[sys._base_executable, "-I", "-B", "-c", "import time; time.sleep(30)"],
                        transport_lost_handler=lost,
                    )
            self.assertIs(caught.exception, primary)
            lost.assert_called_once_with()
            self.assertEqual(len(created), 1)
            self.assertIsNotNone(created[0].poll())
            self.assertTrue(all(stream.closed for stream in (
                created[0].stdin, created[0].stdout, created[0].stderr,
            )))
        finally:
            for proc in created:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=2)

    def test_failed_constructor_disconnects_attached_socket_only_when_instrumented(self):
        for instrumented in (False, True):
            with self.subTest(instrumented=instrumented):
                wire, lost = Wire(), Mock()
                primary = KeyboardInterrupt("original initialization interruption")

                def initialize(client):
                    client._closed = True
                    raise primary

                reader = SimpleNamespace(ident=None, start=lambda: None, is_alive=lambda: False)
                with patch.object(AppServerClient, "_connect_unix_socket",
                                  lambda client, _path: setattr(client, "ws", wire)), \
                        patch.object(AppServerClient, "_initialize", new=initialize), \
                        patch("gitlab_agent.codex_app_server.threading.Thread", return_value=reader):
                    with self.assertRaises(KeyboardInterrupt) as caught:
                        AppServerClient(codex_bin="synthetic", unix_socket="synthetic",
                            transport_lost_handler=lost if instrumented else None)
                self.assertIs(caught.exception, primary)
                self.assertEqual(wire.closed, int(instrumented))
                self.assertEqual(lost.call_count, int(instrumented))

    def test_failed_constructor_escalates_owned_process_with_bounded_waits(self):
        order, clients = [], []
        primary = RuntimeError("original initialization failure")
        proc = Mock()
        proc.poll.return_value = None
        proc.terminate.side_effect = lambda: order.append("terminate")
        proc.kill.side_effect = lambda: order.append("kill")
        proc.stdin, proc.stdout, proc.stderr = io.StringIO(), io.StringIO(), io.StringIO()
        calls = []

        def wait(*, timeout):
            calls.append(timeout)
            order.append("wait")
            if len(calls) == 1:
                raise subprocess.TimeoutExpired("synthetic owned process", timeout)
            return -9

        proc.wait.side_effect = wait

        def open_transport(client, _socket, _argv):
            client.proc = proc
            clients.append(client)

        with patch.object(AppServerClient, "_open_transport", new=open_transport), \
                patch.object(AppServerClient, "_initialize", side_effect=primary):
            with self.assertRaises(RuntimeError) as caught:
                AppServerClient(codex_bin="synthetic", transport_lost_handler=lambda: order.append("lost"))
        self.assertIs(caught.exception, primary)
        self.assertEqual(order, ["lost", "terminate", "wait", "kill", "wait"])
        self.assertEqual(calls, [1, 1])
        self.assertTrue(all(stream.closed for stream in (proc.stdin, proc.stdout, proc.stderr)))
        self.assertEqual(clients[0]._stderr_tail, [])


if __name__ == "__main__":
    unittest.main()
