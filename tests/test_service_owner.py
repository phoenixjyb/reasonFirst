from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from gitlab_agent.bridge_http import HTTPLaunch
from gitlab_agent.bridge_preview import controller as controller_module
from gitlab_agent.bridge_preview.admission import AdmissionError, ControllerAdmission, MaintenanceLease
from gitlab_agent.upgrade import service_managed as service


class FakeSocket:
    def __init__(self, launch):
        self.launch = launch
        self.family = socket.AF_INET
        self.closed = False
        self.close_calls = 0

    def getsockname(self):
        return self.launch.host, self.launch.port

    def fileno(self):
        return -1 if self.closed else 123

    def close(self):
        self.close_calls += 1
        self.closed = True


class FakeServer:
    def __init__(self, app, launch):
        self.app = app
        self.launch = launch
        self.started = False
        self.force_exit = False
        self.entered = asyncio.Event()
        self.allow_start = asyncio.Event()
        self.allow_start.set()
        self.exit_requested = asyncio.Event()
        self.allow_exit = asyncio.Event()
        self.allow_exit.set()
        self.exited = asyncio.Event()
        self.sockets = None
        self.start_error = None
        self.exit_error = None
        self._should_exit = False

    @property
    def should_exit(self):
        return self._should_exit

    @should_exit.setter
    def should_exit(self, value):
        self._should_exit = value
        if value:
            self.exit_requested.set()

    async def serve(self, *, sockets):
        self.sockets = sockets
        self.entered.set()
        try:
            await self.allow_start.wait()
            if self.start_error is not None:
                raise self.start_error
            self.started = True
            await self.exit_requested.wait()
            await self.allow_exit.wait()
            if self.exit_error is not None:
                raise self.exit_error
        finally:
            self.exited.set()


class PoisonLock:
    def __enter__(self):
        raise AssertionError("inherited owner lock must not be acquired")

    def __exit__(self, *args):
        raise AssertionError("inherited owner lock must not be acquired")


class ManagedServiceOwnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rf-service-owner-")
        self.addCleanup(temporary.cleanup)
        self.state_dir = Path(temporary.name) / "state"
        self.state_dir.mkdir()
        self.launch = HTTPLaunch("127.0.0.1", 41239, "/mcp", "read-only", "disabled")
        self.patch(controller_module, "_private_state_dir", return_value=self.state_dir)
        self.patch(controller_module, "load_bridge_config", return_value={
            "version": 4,
            "defaults": {"target": "local", "codex_backend": "standalone-local"},
            "targets": {"local": {"type": "local", "codex_backend": "standalone-local"}},
        })
        self.supported = self.patch(service, "_require_supported", return_value=None)
        self.sdk = self.patch(service, "_require_sdk", return_value=None)
        self.patch(service, "CLOSE_TIMEOUT_SECONDS", 0.2)
        self.controllers = []
        self.cores = []
        self.sockets = []
        self.servers = []
        original_controller_factory = service._create_controller

        def create_controller(admission):
            controller = original_controller_factory(admission)
            self.controllers.append(controller)
            return controller

        def build_core(launch, controller):
            core = Mock(name="owned-core")
            core._reasonfirst_controller = controller
            core.streamable_http_app.return_value = SimpleNamespace(routes=[
                SimpleNamespace(path=launch.path), SimpleNamespace(path="/healthz"),
            ])
            self.cores.append(core)
            return core

        def bind_socket(launch):
            listener = FakeSocket(launch)
            self.sockets.append(listener)
            return listener

        def create_server(app, launch):
            server = FakeServer(app, launch)
            self.servers.append(server)
            return server

        self.controller_factory = self.patch(service, "_create_controller", side_effect=create_controller)
        self.core_factory = self.patch(service, "_build_core", side_effect=build_core)
        self.socket_factory = self.patch(service, "_bind_socket", side_effect=bind_socket)
        self.server_factory = self.patch(service, "_create_server", side_effect=create_server)
        self.observer = self.patch(service, "_observe_listener", return_value=True)
        self.owner = service.ManagedBridgeService(self.launch)
        self.addAsyncCleanup(self.cleanup_owner)

    def patch(self, target, name, *args, **kwargs):
        patcher = patch.object(target, name, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    async def cleanup_owner(self):
        for server in self.servers:
            server.allow_start.set()
            server.allow_exit.set()
        try:
            await self.owner.aclose()
        except service.ServiceError:
            pass
        task = self.owner._server_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def wait(self, event):
        await asyncio.wait_for(event.wait(), timeout=2)

    async def starting_server(self, task):
        async def wait_for_factory():
            while not self.servers:
                if task.done():
                    await task
                await asyncio.sleep(0)
            return self.servers[0]
        server = await asyncio.wait_for(wait_for_factory(), timeout=2)
        await self.wait(server.entered)
        return server

    def assert_code(self, code, function, *args):
        with self.assertRaises(service.ServiceError) as raised:
            function(*args)
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    async def assert_async_code(self, code, awaitable):
        with self.assertRaises(service.ServiceError) as raised:
            await awaitable
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def assert_no_resource_factories(self):
        for factory in (self.controller_factory, self.core_factory, self.socket_factory, self.server_factory):
            factory.assert_not_called()

    async def test_constructor_is_lazy_and_rejects_non_launch_objects(self):
        self.assert_no_resource_factories()
        self.supported.assert_not_called()
        self.sdk.assert_not_called()
        self.assertEqual(self.owner.maintenance_snapshot()["lifecycle"], "new")
        self.assert_code("invalid_launch", service.ManagedBridgeService, object())
        self.assert_no_resource_factories()

    async def test_unsupported_platform_fails_before_config_or_socket_resources(self):
        self.supported.side_effect = service.ServiceError("unsupported_service_platform")
        await self.assert_async_code("unsupported_service_platform", self.owner.start())
        self.assert_no_resource_factories()
        self.sdk.assert_not_called()

    async def test_unsupported_sdk_fails_before_config_or_socket_resources(self):
        self.sdk.side_effect = service.ServiceError("unsupported_listener_sdk")
        await self.assert_async_code("unsupported_listener_sdk", self.owner.start())
        self.assert_no_resource_factories()

    async def test_start_binds_one_actual_controller_core_socket_and_live_task(self):
        await self.owner.start()
        self.assertEqual(len(self.controllers), 1)
        self.assertEqual(len(self.cores), 1)
        self.assertEqual(len(self.sockets), 1)
        self.assertEqual(len(self.servers), 1)
        controller, core, listener, server = (
            self.controllers[0], self.cores[0], self.sockets[0], self.servers[0],
        )
        self.assertIs(self.owner._controller, controller)
        self.assertIs(controller._admission, self.owner._admission)
        self.assertIs(core._reasonfirst_controller, controller)
        self.assertIs(self.owner._core, core)
        self.assertIs(self.owner._socket, listener)
        self.assertEqual(server.sockets, [listener])
        self.assertFalse(self.owner._server_task.done())
        self.observer.assert_called_with(server, listener, self.launch)
        snapshot = self.owner.maintenance_snapshot()
        self.assertEqual(snapshot["lifecycle"], "running")
        self.assertTrue(snapshot["listener_serving"])

    async def test_maintenance_cannot_enter_while_server_startup_is_pending(self):
        original_factory = self.server_factory.side_effect
        def create_server(app, launch):
            server = original_factory(app, launch)
            server.allow_start.clear()
            return server
        self.server_factory.side_effect = create_server
        task = asyncio.create_task(self.owner.start())
        server = await self.starting_server(task)
        self.assert_code("service_not_running", self.owner.try_enter_maintenance)
        self.assertEqual(self.owner.maintenance_snapshot()["lifecycle"], "starting")
        server.allow_start.set()
        await asyncio.wait_for(task, timeout=2)
        lease = self.owner.try_enter_maintenance()
        self.owner.leave_maintenance(lease)

    async def test_duplicate_start_never_constructs_a_second_owner(self):
        await self.owner.start()
        await self.assert_async_code("service_already_started", self.owner.start())
        self.assertEqual(len(self.controllers), 1)
        self.assertEqual(len(self.sockets), 1)
        self.assertEqual(len(self.servers), 1)

    async def test_close_before_start_is_terminal_without_creating_resources(self):
        await self.owner.aclose()
        self.assertEqual(self.owner.maintenance_snapshot()["lifecycle"], "closed")
        with self.assertRaises(service.ServiceError) as raised:
            await self.owner.start()
        self.assertIn(raised.exception.code, {"service_closed", "service_already_started"})
        self.assert_no_resource_factories()

    async def test_maintenance_returns_the_owned_controllers_original_lease(self):
        await self.owner.start()
        controller = self.controllers[0]
        lease = self.owner.try_enter_maintenance()
        self.assertIs(type(lease), MaintenanceLease)
        self.assertIs(lease, controller._admission._lease)
        self.assertTrue(self.owner.maintenance_snapshot()["maintenance_window_held"])
        with self.assertRaises(AdmissionError) as raised:
            controller._admission.reserve_operation()
        self.assertEqual(raised.exception.code, "maintenance_active")
        self.owner.leave_maintenance(lease)
        self.assertFalse(self.owner.maintenance_snapshot()["maintenance_window_held"])

    async def test_foreign_and_stale_leases_cannot_reopen_the_owned_controller(self):
        await self.owner.start()
        other = ControllerAdmission()
        other.claim_controller()
        foreign = other.try_enter_maintenance()
        original = self.owner.try_enter_maintenance()
        self.assert_code("invalid_maintenance_lease", self.owner.leave_maintenance, foreign)
        self.owner.leave_maintenance(original)
        current = self.owner.try_enter_maintenance()
        before = self.owner.maintenance_snapshot()
        self.assert_code("invalid_maintenance_lease", self.owner.leave_maintenance, original)
        self.assertEqual(self.owner.maintenance_snapshot(), before)
        self.assertTrue(other.snapshot()["maintenance_held"])
        self.owner.leave_maintenance(current)
        other.leave_maintenance(foreign)

    async def test_active_controller_work_blocks_owner_maintenance_without_closing_admission(self):
        await self.owner.start()
        gate = self.controllers[0]._admission
        reservation = gate.reserve_operation()
        self.assert_code("maintenance_busy", self.owner.try_enter_maintenance)
        self.assertTrue(self.owner.maintenance_snapshot()["admission"]["admission_open"])
        gate.finish_operation(reservation)
        lease = self.owner.try_enter_maintenance()
        self.owner.leave_maintenance(lease)

    async def test_existing_state_remains_unknown_through_service_enrollment(self):
        (self.state_dir / "state.json").write_text(json.dumps({
            "version": 3, "sessions": {}, "workspaces": {}, "finish_approvals": {},
        }), encoding="utf-8")
        await self.owner.start()
        snapshot = self.owner.maintenance_snapshot()
        self.assertIn("recovered_state_unverified", snapshot["admission"]["unknown_reasons"])
        self.assertFalse(snapshot["recovered_state_verified"])
        self.assert_code("maintenance_busy", self.owner.try_enter_maintenance)

    async def test_unknown_during_a_lease_does_not_become_a_known_maintenance_window(self):
        await self.owner.start()
        lease = self.owner.try_enter_maintenance()
        self.controllers[0]._admission.mark_unknown("transport_lost")
        snapshot = self.owner.maintenance_snapshot()
        self.assertFalse(snapshot["admission"]["idle_observed"])
        self.assertFalse(snapshot["admission"]["admission_open"])
        self.owner.leave_maintenance(lease)
        self.assertTrue(self.owner.maintenance_snapshot()["admission"]["admission_open"])
        self.assert_code("maintenance_busy", self.owner.try_enter_maintenance)

    async def test_server_task_exit_cannot_leave_a_maintenance_eligible_owner(self):
        await self.owner.start()
        self.servers[0].should_exit = True
        await self.wait(self.servers[0].exited)
        await asyncio.sleep(0)
        with self.assertRaises(service.ServiceError) as raised:
            self.owner.try_enter_maintenance()
        self.assertIn(raised.exception.code, {"service_exited", "service_not_running", "service_closed"})
        self.assertFalse(self.owner.maintenance_snapshot()["listener_serving"])

    async def test_listener_observation_is_required_at_maintenance_entry(self):
        await self.owner.start()
        self.observer.side_effect = service.ServiceError("listener_not_serving")
        self.assert_code("listener_not_serving", self.owner.try_enter_maintenance)
        self.assertFalse(self.controllers[0]._admission.snapshot()["maintenance_held"])

    async def test_close_denies_admission_before_waiting_for_server_and_retains_work(self):
        await self.owner.start()
        gate = self.controllers[0]._admission
        reservation = gate.reserve_operation()
        server = self.servers[0]
        server.allow_exit.clear()
        close_task = asyncio.create_task(self.owner.aclose())
        await self.wait(server.exit_requested)
        self.assertFalse(close_task.done())
        with self.assertRaises(AdmissionError) as raised:
            gate.reserve_operation()
        self.assertEqual(raised.exception.code, "admission_closed")
        self.assertEqual(gate.snapshot()["counts"]["operations"], 1)
        self.assertFalse(gate.snapshot()["idle_observed"])
        server.allow_exit.set()
        await asyncio.wait_for(close_task, timeout=2)
        self.assertEqual(gate.snapshot()["counts"]["operations"], 1)
        gate.finish_operation(reservation)
        self.assertFalse(gate.snapshot()["idle_observed"])

    async def test_close_invalidates_lease_and_only_closes_owned_resources(self):
        await self.owner.start()
        lease = self.owner.try_enter_maintenance()
        unrelated_socket = FakeSocket(self.launch)
        unrelated_ready = asyncio.Event()
        unrelated_task = asyncio.create_task(unrelated_ready.wait())
        try:
            await self.owner.aclose()
            self.assertFalse(unrelated_socket.closed)
            self.assertFalse(unrelated_task.done())
            self.assertTrue(self.sockets[0].closed)
            self.assertTrue(self.owner._server_task.done())
            self.assertEqual(self.controllers[0]._admission.snapshot()["state"], "closed")
            with self.assertRaises(service.ServiceError):
                self.owner.leave_maintenance(lease)
            self.assertFalse(self.owner.maintenance_snapshot()["admission"]["admission_open"])
            await self.owner.aclose()
        finally:
            unrelated_ready.set()
            await unrelated_task

    async def test_core_binding_failure_closes_owned_controller_without_closing_a_foreign_one(self):
        foreign = Mock(name="foreign-controller")
        self.core_factory.side_effect = lambda launch, controller: Mock(_reasonfirst_controller=foreign)
        await self.assert_async_code("core_binding_failed", self.owner.start())
        foreign.close.assert_not_called()
        self.assertEqual(self.controllers[0]._admission.snapshot()["state"], "closed")
        self.server_factory.assert_not_called()
        self.assertTrue(all(listener.closed for listener in self.sockets))

    async def test_controller_binding_failure_does_not_close_a_foreign_controller(self):
        foreign_gate = ControllerAdmission()
        foreign = controller_module.BridgeController(admission=foreign_gate)
        self.addCleanup(foreign.close)
        reservation = foreign_gate.reserve_operation()
        self.addCleanup(foreign_gate.finish_operation, reservation)
        self.controller_factory.side_effect = lambda admission: foreign
        with patch.object(foreign, "close", wraps=foreign.close) as close:
            await self.assert_async_code("controller_binding_failed", self.owner.start())
            close.assert_not_called()
        self.assertTrue(foreign_gate.snapshot()["admission_open"])
        self.assertEqual(foreign_gate.snapshot()["counts"]["operations"], 1)
        self.core_factory.assert_not_called()
        self.server_factory.assert_not_called()
        self.assertTrue(all(listener.closed for listener in self.sockets))

    async def test_close_while_starting_cannot_publish_a_running_service(self):
        original_factory = self.server_factory.side_effect
        def create_server(app, launch):
            server = original_factory(app, launch)
            server.allow_start.clear()
            server.allow_exit.clear()
            return server
        self.server_factory.side_effect = create_server
        start_task = asyncio.create_task(self.owner.start())
        server = await self.starting_server(start_task)
        close_task = asyncio.create_task(self.owner.aclose())
        await self.wait(server.exit_requested)
        self.assertFalse(self.owner._admission.snapshot()["admission_open"])
        server.allow_start.set()
        await asyncio.sleep(0.01)
        self.assertFalse(self.owner.maintenance_snapshot()["listener_serving"])
        server.allow_exit.set()
        await asyncio.wait_for(close_task, timeout=2)
        await self.assert_async_code("service_closed", start_task)
        self.assertTrue(self.owner._server_task.done())
        self.assertTrue(self.sockets[0].closed)
        self.assertFalse(self.owner.maintenance_snapshot()["admission"]["admission_open"])
        self.assertEqual(len(self.controllers), 1)

    async def test_cancelling_close_preserves_owned_cleanup_and_closed_admission(self):
        await self.owner.start()
        server = self.servers[0]
        server.allow_exit.clear()
        task = asyncio.create_task(self.owner.aclose())
        await self.wait(server.exit_requested)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.assertFalse(self.owner._admission.snapshot()["admission_open"])
        server.allow_exit.set()
        await self.assert_async_code("service_cancelled", task)
        self.assertTrue(self.owner._server_task.done())
        self.assertTrue(self.sockets[0].closed)
        self.assertTrue(self.owner.maintenance_snapshot()["cleanup_complete"])

    async def test_async_lifecycle_cannot_move_to_a_different_event_loop(self):
        await self.owner.start()
        def wrong_loop():
            async def check():
                await self.assert_async_code("service_wrong_loop", self.owner.start())
                await self.assert_async_code("service_wrong_loop", self.owner.aclose())
            asyncio.run(check())
        await asyncio.to_thread(wrong_loop)
        self.assertFalse(self.owner._server_task.done())
        lease = self.owner.try_enter_maintenance()
        self.owner.leave_maintenance(lease)

    async def test_startup_factory_failure_is_fixed_and_cleans_already_owned_resources(self):
        self.server_factory.side_effect = RuntimeError("private factory exception marker")
        error = await self.assert_async_code("service_startup_failed", self.owner.start())
        self.assertNotIn("private factory exception marker", str(error))
        self.assertEqual(self.controllers[0]._admission.snapshot()["state"], "closed")
        self.assertTrue(all(listener.closed for listener in self.sockets))
        self.assertNotIn("private factory exception marker", json.dumps(self.owner.maintenance_snapshot()))

    async def test_startup_timeout_cancels_owned_task_and_closes_its_resources(self):
        original_factory = self.server_factory.side_effect
        def create_server(app, launch):
            server = original_factory(app, launch)
            server.allow_start.clear()
            return server
        self.server_factory.side_effect = create_server
        with patch.object(service, "START_TIMEOUT_SECONDS", 0.02):
            await self.assert_async_code("service_startup_timeout", self.owner.start())
        self.assertTrue(self.owner._server_task.done())
        self.assertTrue(self.sockets[0].closed)
        self.assertEqual(self.controllers[0]._admission.snapshot()["state"], "closed")

    async def test_cancelled_start_cannot_leave_a_live_owned_task_or_open_gate(self):
        original_factory = self.server_factory.side_effect
        def create_server(app, launch):
            server = original_factory(app, launch)
            server.allow_start.clear()
            return server
        self.server_factory.side_effect = create_server
        task = asyncio.create_task(self.owner.start())
        await self.starting_server(task)
        task.cancel()
        await self.assert_async_code("service_cancelled", task)
        self.assertTrue(self.owner._server_task.done())
        self.assertTrue(self.sockets[0].closed)
        self.assertFalse(self.controllers[0]._admission.snapshot()["admission_open"])

    async def test_controller_cleanup_failure_is_fixed_and_never_reopens_admission(self):
        await self.owner.start()
        with patch.object(self.controllers[0], "close", side_effect=RuntimeError("private cleanup marker")):
            error = await self.assert_async_code("service_cleanup_failed", self.owner.aclose())
        self.assertEqual(error.cleanup_code, "controller_cleanup_failed")
        self.assertTrue(self.sockets[0].closed)
        self.assertTrue(self.owner._server_task.done())
        snapshot = self.owner.maintenance_snapshot()
        self.assertFalse(snapshot["cleanup_complete"])
        self.assertFalse(snapshot["admission"]["admission_open"])
        self.assertNotIn("private cleanup marker", str(error) + json.dumps(snapshot))

    async def test_wrong_process_is_rejected_before_the_owner_lifecycle_lock(self):
        await self.owner.start()
        pid = os.getpid()
        with patch.object(self.owner, "_lock", PoisonLock()), patch.object(service.os, "getpid", return_value=pid + 1):
            self.assert_code("service_wrong_process", self.owner.maintenance_snapshot)
            self.assert_code("service_wrong_process", self.owner.try_enter_maintenance)
            self.assert_code("service_wrong_process", self.owner.leave_maintenance, object())
            await self.assert_async_code("service_wrong_process", self.owner.start())
            await self.assert_async_code("service_wrong_process", self.owner.aclose())

    async def test_snapshot_preserves_unverified_scope_and_omits_private_objects(self):
        await self.owner.start()
        snapshot = self.owner.maintenance_snapshot()
        self.assertEqual(set(snapshot), {
            "schema_version", "scope", "lifecycle", "listener_serving", "admission",
            "maintenance_window_held", "error_code", "cleanup_error_code", "cleanup_complete",
            "effective_configuration_verified", "recovered_state_verified", "external_producers_quiesced",
            "global_idle_verified", "runtime_identity_verified", "activation_authorized",
            "ready_for_activation", "existing_service_adopted",
            "resolved_policy_bound", "current_process_bound", "configuration_digest", "runtime_observation",
            "child_inputs_bound", "child_observation",
            "api_trust_bound", "api_trust_observation",
        })
        self.assertEqual(snapshot["schema_version"], 1)
        self.assertEqual(snapshot["scope"], "owned-managed-bridge-service")
        for name in (
            "effective_configuration_verified", "recovered_state_verified", "external_producers_quiesced",
            "global_idle_verified", "runtime_identity_verified", "activation_authorized",
            "ready_for_activation", "existing_service_adopted",
            "resolved_policy_bound", "current_process_bound", "child_inputs_bound", "api_trust_bound",
        ):
            self.assertIs(snapshot[name], False)
        self.assertIsNone(snapshot["configuration_digest"])
        self.assertIsNone(snapshot["runtime_observation"])
        self.assertIsNone(snapshot["child_observation"])
        self.assertIsNone(snapshot["api_trust_observation"])
        text = json.dumps(snapshot)
        self.assertNotIn(str(self.state_dir), text)
        self.assertNotIn("owned-core", text)
        snapshot["admission"]["counts"]["operations"] = 999
        self.assertEqual(self.owner.maintenance_snapshot()["admission"]["counts"]["operations"], 0)


if __name__ == "__main__":
    unittest.main()
