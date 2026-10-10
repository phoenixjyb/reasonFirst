"""One process-local owner for an enrolled Bridge and its HTTP listener.

This internal API starts a new foreground service. An explicit configuration
can bind selected parent policy and a current-process runtime observation. It
does not adopt a service, verify all effective configuration or a prepared
runtime, or authorize deployment or activation.
The disposable startup protocol and ordinary HTTP entry point are independent.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import importlib.metadata
import os
import platform
import socket
import threading
import time

from ..bridge_http import HTTPLaunch
from ..bridge_preview.admission import AdmissionError, ControllerAdmission, _ERROR_CODES
from .service_configuration import ManagedServiceConfiguration
from .service_children import ServiceChildContext, capture_service_children
from .service_runtime import SCOPE as _RUNTIME_SCOPE, ServiceRuntimeObservation, capture_service_runtime


START_TIMEOUT_SECONDS = 8.0
CLOSE_TIMEOUT_SECONDS = 5.0
_PROTOCOL = b"2026-07-28"
_FALSE_FLAGS = (
    "effective_configuration_verified", "recovered_state_verified",
    "external_producers_quiesced", "global_idle_verified", "runtime_identity_verified",
    "activation_authorized", "ready_for_activation", "existing_service_adopted",
)
_CODES = _ERROR_CODES | frozenset({
    "invalid_launch", "service_wrong_process", "service_wrong_loop",
    "service_already_started", "service_not_running", "service_closed",
    "service_startup_failed", "service_startup_timeout", "service_cancelled",
    "service_exited", "unsupported_service_platform", "unsupported_listener_query",
    "unsupported_listener_sdk", "listener_bind_failed", "listener_observation_failed",
    "listener_not_serving", "controller_binding_failed", "core_binding_failed",
    "service_failed", "service_cleanup_failed", "server_cleanup_failed",
    "controller_cleanup_failed", "listener_cleanup_failed", "request_cleanup_failed",
    "lifespan_cleanup_failed",
    "invalid_service_configuration", "configuration_binding_failed", "runtime_binding_failed",
    "child_binding_failed",
})


class ServiceError(RuntimeError):
    """Only finite, non-reflective codes leave the owner boundary."""

    def __init__(self, code, *, cleanup_code=None):
        self.code = code if type(code) is str and code in _CODES else "service_failed"
        self.cleanup_code = (cleanup_code if type(cleanup_code) is str and cleanup_code in _CODES
                             else None if cleanup_code is None else "service_cleanup_failed")
        super().__init__(self.code)


def _require_supported():
    system = platform.system()
    if (os.name, system) not in {("posix", "Linux"), ("posix", "Darwin"), ("nt", "Windows")}:
        raise ServiceError("unsupported_service_platform")
    options = ["TCP_CONNECTION_INFO"] if system == "Darwin" else ["SO_ACCEPTCONN"]
    if system == "Windows":
        options.append("SO_EXCLUSIVEADDRUSE")
    if any(type(getattr(socket, name, None)) is not int for name in options):
        raise ServiceError("unsupported_listener_query")


def _require_sdk():
    try:
        valid = all(importlib.metadata.version(name) == version
                    for name, version in {"mcp": "2.3.0", "uvicorn": "0.54.0"}.items())
    except Exception:
        valid = False
    if not valid:
        raise ServiceError("unsupported_listener_sdk")


def _socket_listening(sock):
    try:
        if platform.system() == "Darwin":
            value = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_CONNECTION_INFO, 1)
            if type(value) is not bytes or value not in (b"\x00", b"\x01"):
                raise ServiceError("listener_observation_failed")
            return value == b"\x01"
        value = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
        if type(value) is not int or value not in (0, 1):
            raise ServiceError("listener_observation_failed")
        return value == 1
    except (OSError, ValueError, AttributeError):
        raise ServiceError("listener_observation_failed") from None


def _bind_socket(launch):
    sock = None
    try:
        family = socket.AF_INET6 if launch.host == "::1" else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.set_inheritable(False)
        if platform.system() == "Windows":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        # Never reuse a listener or release/reacquire its port.
        sock.bind((launch.host, launch.port))
        if _socket_listening(sock):
            raise ServiceError("listener_observation_failed")
        return sock
    except BaseException as exc:
        cleanup_code = None
        if sock is not None:
            try:
                sock.close()
            except BaseException:
                cleanup_code = "listener_cleanup_failed"
        if isinstance(exc, ServiceError):
            raise ServiceError(exc.code, cleanup_code=cleanup_code) from None
        if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
            raise ServiceError("service_cancelled", cleanup_code=cleanup_code) from None
        raise ServiceError("listener_bind_failed", cleanup_code=cleanup_code) from None


def _observe_listener(server, sock, launch):
    try:
        actual = server.servers
        if (not server.started or server.should_exit or len(actual) != 1
                or not actual[0].is_serving()):
            raise ServiceError("listener_not_serving")
        sockets = actual[0].sockets
        if (not sockets or len(sockets) != 1 or sock.fileno() < 0
                or sockets[0].fileno() != sock.fileno()
                or sock.getsockname()[:2] != (launch.host, launch.port)
                or sock.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM
                or not _socket_listening(sock)):
            raise ServiceError("listener_not_serving")
    except ServiceError:
        raise
    except Exception:
        raise ServiceError("listener_observation_failed") from None


def _create_controller(admission, *, configuration=None, child_context=None):
    from ..bridge_preview.controller import BridgeController
    options = {"child_context": child_context} if configuration is not None else {}
    return BridgeController(admission=admission, service_configuration=configuration, **options)


def _child_summary(context):
    """Only cached, finite launch-input evidence may enter owner snapshots."""
    summary = context.summary()
    positive = {"environment_retained", "working_directory_retained",
                "python_invocation_observed", "current_process_only"}
    negative = {"child_runtime_verified", "provider_configuration_verified",
                "remote_runtime_verified", "desktop_daemon_verified", "activation_authorized"}
    if (type(summary) is not dict
            or set(summary) != positive | negative | {"schema_version", "scope", "selected_executable_count"}
            or type(summary["schema_version"]) is not int or summary["schema_version"] != 1
            or type(summary["scope"]) is not str or summary["scope"] != "owned-local-child-inputs"
            or type(summary["selected_executable_count"]) is not int
            or not 0 <= summary["selected_executable_count"] <= 256
            or any(summary[name] is not True for name in positive)
            or any(summary[name] is not False for name in negative)):
        raise ServiceError("child_binding_failed")
    return dict(summary)


def _api_trust_summary(context):
    """Validate only cached, non-identifying TLS material evidence."""
    summary = context.api_trust_summary()
    if summary is None:
        return None
    positive = {"material_retained", "current_process_only"}
    negative = {"tls_peer_verified", "provider_configuration_verified",
                "native_git_trust_verified", "native_ssh_trust_verified", "activation_authorized"}
    if (type(summary) is not dict
            or set(summary) != positive | negative | {"schema_version", "scope", "selected_bundle_count"}
            or type(summary["schema_version"]) is not int or summary["schema_version"] != 1
            or type(summary["scope"]) is not str or summary["scope"] != "selected-gitlab-api-trust-v1"
            or type(summary["selected_bundle_count"]) is not int
            or not 1 <= summary["selected_bundle_count"] <= 2
            or any(summary[name] is not True for name in positive)
            or any(summary[name] is not False for name in negative)):
        raise ServiceError("child_binding_failed")
    return dict(summary)


def _runtime_summary(observation):
    """Validate bounded public evidence before adopting any service resources."""
    digest, summary = observation.digest, observation.summary()
    positive = {"interpreter_files_observed", "selected_module_origins_observed",
                "selected_module_files_observed", "current_process_only"}
    negative = {"runtime_identity_verified", "running_code_verified", "activation_authorized"}
    if (type(digest) is not str or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or type(summary) is not dict
            or set(summary) != positive | negative | {"scope", "digest", "selected_module_count"}
            or type(summary["scope"]) is not str or summary["scope"] != _RUNTIME_SCOPE
            or type(summary["digest"]) is not str or summary["digest"] != digest
            or type(summary["selected_module_count"]) is not int
            or not 1 <= summary["selected_module_count"] <= 256
            or any(summary[name] is not True for name in positive)
            or any(summary[name] is not False for name in negative)):
        raise ServiceError("runtime_binding_failed")
    return dict(summary)


def _build_core(launch, controller):
    from ..bridge_mcp import build_server
    return build_server(read_only_mode=launch.mode == "read-only", controller=controller,
                        subscriptions=False)


def _create_server(app, launch):
    import uvicorn
    from uvicorn.protocols.http.h11_impl import H11Protocol

    class OwnedH11Protocol(H11Protocol):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.access_log = False
            underlying_app = self.app

            async def observed_app(scope, receive, send):
                # Capture this request's cycle once. on_response_complete can
                # advance self.cycle to a pipelined request during final send.
                cycle = self.cycle
                if cycle is None or cycle.scope is not scope:
                    raise ServiceError("service_failed")

                async def observed_send(message):
                    # h11 silently returns from send after a disconnect. Its
                    # exact cycle state, not watcher scheduling, determines
                    # whether the local write boundary completed.
                    if cycle.disconnected:
                        raise ServiceError("service_failed")
                    await send(message)
                    if cycle.disconnected or (
                        message["type"] == "http.response.body"
                        and not message.get("more_body", False)
                        and not cycle.response_complete
                    ):
                        raise ServiceError("service_failed")

                await underlying_app(scope, receive, observed_send)

            self.app = observed_app

    class OwnedConfig(uvicorn.Config):
        def configure_logging(self):
            # The ordinary Config mutates global logger levels/handlers even
            # with log_config=None. Logging configuration belongs to the host.
            pass

        def load(self):
            super().load()
            self.http_protocol_class = OwnedH11Protocol

    class OwnedServer(uvicorn.Server):
        @contextmanager
        def capture_signals(self):
            # An embedded async service never changes its host's signal policy.
            yield

    return OwnedServer(OwnedConfig(
        app, host=launch.host, port=launch.port, http="h11", ws="none",
        workers=1, reload=False, lifespan="on", timeout_graceful_shutdown=1,
        log_config=None, log_level=None, access_log=False,
    ))


async def _response(send, status, code):
    # These values are literals selected by this module, never caller data.
    body = ('{"error":"' + code + '"}').encode("ascii")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode("ascii")),
                            (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": body})


class _AdmissionApp:
    """Reserve before POST dispatch, through response and request-local cleanup."""

    def __init__(self, owner, app):
        self.owner, self.app = owner, app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            return
        if scope.get("path") == "/healthz" and scope.get("method") == "GET":
            # Shared /healthz is liveness only. It never calls the controller.
            await self.app(scope, receive, send)
            return
        if scope.get("path") != self.owner._launch.path:
            await _response(send, 404, "not_found")
            return
        if scope.get("method") != "POST":
            await _response(send, 405, "method_not_allowed")
            return
        try:
            token = self.owner._begin_request()
        except ServiceError as exc:
            code = "maintenance_active" if exc.code == "maintenance_active" else "service_unavailable"
            await _response(send, 503, code)
            return

        uncertain = False
        response_complete = False
        body_complete = asyncio.Event()

        async def receive_body():
            nonlocal uncertain
            message = await receive()
            if message["type"] == "http.disconnect":
                uncertain = True
            elif message["type"] == "http.request" and not message.get("more_body", False):
                body_complete.set()
            return message

        async def send_response(message):
            nonlocal uncertain, response_complete
            try:
                await send(message)
            except BaseException:
                uncertain = True
                raise
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                response_complete = True

        async def watch_disconnect():
            nonlocal uncertain
            await body_complete.wait()
            # Only this task reads the receive channel after the complete JSON
            # body. This adapter has no SDK GET/subscription/SSE receive loop.
            message = await receive()
            if not response_complete:
                uncertain = True
            if message["type"] != "http.disconnect":
                uncertain = True

        watcher = asyncio.create_task(watch_disconnect())
        try:
            headers = scope.get("headers", ())
            versions = [v for k, v in headers if k.lower() == b"mcp-protocol-version"]
            methods = [v for k, v in headers if k.lower() == b"mcp-method"]
            if versions != [_PROTOCOL] or any(k.lower() == b"mcp-session-id" for k, _ in headers):
                await _response(send_response, 400, "unsupported_protocol")
            elif len(methods) != 1 or not methods[0] or len(methods[0]) > 256:
                await _response(send_response, 400, "invalid_request")
            elif methods[0] == b"subscriptions/listen":
                await _response(send_response, 405, "method_not_allowed")
            else:
                # Modern SDK classification validates method/header/body
                # equality and envelope fields before any handler executes.
                await self.app(scope, receive_body, send_response)
        except BaseException:
            uncertain = True
            raise
        finally:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
            except BaseException:
                uncertain = True
            self.owner._finish_request(token, uncertain=uncertain or not response_complete)


class ManagedBridgeService:
    """One-shot async service owner; private leases are valid only in this PID."""

    def __init__(self, launch: HTTPLaunch, *, configuration: ManagedServiceConfiguration | None = None):
        if type(launch) is not HTTPLaunch:
            raise ServiceError("invalid_launch")
        if configuration is not None and type(configuration) is not ManagedServiceConfiguration:
            raise ServiceError("invalid_service_configuration")
        self._launch = launch
        self._configuration = configuration
        self._configuration_digest = None
        self._runtime_observation = None
        self._runtime_summary = None
        self._child_context = None
        self._child_context_anchor = None
        self._child_summary = None
        self._api_trust_summary = None
        self._pid = os.getpid()
        self._lock = threading.RLock()
        self._loop = None
        self._state = "new"
        self._admission = ControllerAdmission()
        self._controller = self._core = self._app = self._socket = self._server = None
        self._server_task = self._cleanup_task = self._controller_close_task = None
        self._lifespan_shutdown_task = None
        self._listener_close_tasks = []
        self._request_tasks = {}
        self._error_code = self._cleanup_error_code = None
        self._cleanup_complete = False

    def _check_pid(self):
        if os.getpid() != self._pid:
            raise ServiceError("service_wrong_process")

    def _check_loop(self):
        self._check_pid()
        loop = asyncio.get_running_loop()
        if self._loop is not None and loop is not self._loop:
            raise ServiceError("service_wrong_loop")
        return loop

    def _failed_locked(self, code):
        self._error_code = self._error_code or code
        self._state = "failed"
        self._admission.mark_unknown("transport_lost")
        self._admission.close()

    def _verify_configuration_locked(self):
        if self._configuration is None:
            return
        controller, core = self._controller, self._core
        try:
            if (type(self._child_context) is not ServiceChildContext
                    or self._child_context is not self._child_context_anchor
                    or self._child_context.configuration is not self._configuration
                    or getattr(controller, "child_context", None) is not self._child_context
                    or getattr(core, "_reasonfirst_child_context", None) is not self._child_context):
                raise ServiceError("child_binding_failed")
            self._child_summary = _child_summary(self._child_context)
            self._api_trust_summary = _api_trust_summary(self._child_context)
        except BaseException:
            self._failed_locked("child_binding_failed")
            raise ServiceError("child_binding_failed") from None
        if (controller is None or core is None
                or getattr(controller, "_admission", None) is not self._admission
                or getattr(controller, "service_configuration", None) is not self._configuration
                or getattr(controller, "_controller_closed", False)
                or self._admission.snapshot()["state"] == "closed"
                or getattr(core, "_reasonfirst_controller", None) is not controller
                or getattr(core, "_reasonfirst_service_configuration", None) is not self._configuration
                or controller.state_dir != self._configuration.state_dir
                or controller.state_file != self._configuration.state_dir / "state.json"):
            self._failed_locked("configuration_binding_failed")
            raise ServiceError("configuration_binding_failed")

    def _revalidate_runtime_locked(self):
        if self._configuration is None:
            return
        try:
            if (type(self._child_context) is not ServiceChildContext
                    or self._child_context is not self._child_context_anchor):
                raise ServiceError("child_binding_failed")
            self._child_context.revalidate()
            self._child_summary = _child_summary(self._child_context)
            self._api_trust_summary = _api_trust_summary(self._child_context)
        except BaseException:
            self._failed_locked("child_binding_failed")
            raise ServiceError("child_binding_failed") from None
        try:
            if type(self._runtime_observation) is not ServiceRuntimeObservation:
                raise ServiceError("runtime_binding_failed")
            self._runtime_observation.revalidate()
        except BaseException:
            self._failed_locked("runtime_binding_failed")
            raise ServiceError("runtime_binding_failed") from None
        try:
            if self._configuration.configuration_digest(self._launch) != self._configuration_digest:
                raise ServiceError("configuration_binding_failed")
        except BaseException:
            self._failed_locked("configuration_binding_failed")
            raise ServiceError("configuration_binding_failed") from None

    def _running_locked(self):
        if self._state in {"closing", "closed"}:
            raise ServiceError("service_closed")
        if self._state != "running":
            raise ServiceError("service_not_running")
        if self._server_task is None or self._server_task.done():
            self._failed_locked("service_exited")
            raise ServiceError("service_not_running")
        try:
            _observe_listener(self._server, self._socket, self._launch)
        except ServiceError as exc:
            self._failed_locked(exc.code)
            raise
        self._verify_configuration_locked()

    def maintenance_snapshot(self):
        self._check_pid()
        with self._lock:
            # Selection is lazy and can precede a failed helper. Keep its
            # original bounded summary even if failure/close follows before
            # the first successful live snapshot. No files are read here.
            if type(self._child_context_anchor) is ServiceChildContext:
                try:
                    self._api_trust_summary = _api_trust_summary(self._child_context_anchor)
                except Exception:
                    pass
            listener = False
            if self._state == "running":
                try:
                    self._running_locked()
                    listener = True
                except ServiceError:
                    pass
            admission = self._admission.snapshot()
            bound = listener and self._configuration is not None
            return {
                "schema_version": 1, "scope": "owned-managed-bridge-service",
                "lifecycle": self._state, "listener_serving": listener,
                "admission": admission,
                "maintenance_window_held": listener and admission["maintenance_held"]
                    and not admission["unknown_reasons"],
                "error_code": self._error_code, "cleanup_error_code": self._cleanup_error_code,
                "cleanup_complete": self._cleanup_complete,
                "resolved_policy_bound": bound,
                "current_process_bound": bound and self._runtime_observation is not None,
                "child_inputs_bound": bound and self._child_context is not None,
                "child_observation": (dict(self._child_summary)
                                      if self._child_summary is not None else None),
                "api_trust_bound": bound and self._api_trust_summary is not None,
                "api_trust_observation": (dict(self._api_trust_summary)
                                          if self._api_trust_summary is not None else None),
                "configuration_digest": self._configuration_digest,
                # Historical point-in-time file observation; no disk reads in
                # this diagnostic snapshot and no loaded-code attestation.
                "runtime_observation": (dict(self._runtime_summary)
                                        if self._runtime_summary is not None else None),
                **dict.fromkeys(_FALSE_FLAGS, False),
            }

    def try_enter_maintenance(self):
        self._check_pid()
        with self._lock:
            self._running_locked()
            self._revalidate_runtime_locked()
            try:
                return self._admission.try_enter_maintenance()
            except AdmissionError as exc:
                raise ServiceError(exc.code) from None

    def leave_maintenance(self, lease):
        self._check_pid()
        with self._lock:
            self._running_locked()
            self._revalidate_runtime_locked()
            try:
                self._admission.leave_maintenance(lease)
            except AdmissionError as exc:
                raise ServiceError(exc.code) from None

    def _begin_request(self):
        self._check_pid()
        with self._lock:
            self._running_locked()
            try:
                token = self._admission.reserve_operation()
            except AdmissionError as exc:
                raise ServiceError(exc.code) from None
            self._request_tasks[token] = asyncio.current_task()
            return token

    def _finish_request(self, token, *, uncertain):
        self._check_pid()
        with self._lock:
            try:
                if uncertain:
                    self._admission.mark_unknown("transport_lost")
                self._admission.finish_operation(token, uncertain=uncertain)
            except BaseException:
                self._failed_locked("service_failed")
                raise ServiceError("service_failed") from None
            finally:
                self._request_tasks.pop(token, None)

    async def _run_server(self):
        try:
            await self._server.serve(sockets=[self._socket])
        except BaseException:
            # SystemExit from an internal server must not exit its host process.
            with self._lock:
                if self._state not in {"closing", "closed"}:
                    self._failed_locked("service_startup_failed" if self._state == "starting" else "service_exited")
                else:
                    self._cleanup_failed("server_cleanup_failed")
            raise ServiceError("service_exited") from None
        finally:
            with self._lock:
                if self._state not in {"closing", "closed", "failed"}:
                    self._failed_locked("service_exited")

    async def start(self):
        loop = self._check_loop()
        with self._lock:
            if self._state != "new":
                raise ServiceError("service_already_started")
            self._loop, self._state = loop, "starting"
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        try:
            _require_supported()
            _require_sdk()
            try:
                self._launch.validate(os.environ)
            except Exception:
                raise ServiceError("invalid_launch") from None
            if self._configuration is not None:
                try:
                    self._configuration_digest = self._configuration.configuration_digest(self._launch)
                except Exception:
                    raise ServiceError("configuration_binding_failed") from None
                try:
                    context = capture_service_children(self._configuration)
                    if (type(context) is not ServiceChildContext
                            or context.configuration is not self._configuration):
                        raise ServiceError("child_binding_failed")
                    self._child_summary = _child_summary(context)
                    self._child_context = context
                    self._child_context_anchor = context
                except Exception:
                    raise ServiceError("child_binding_failed") from None
                try:
                    observation = capture_service_runtime()
                    if type(observation) is not ServiceRuntimeObservation:
                        raise ServiceError("runtime_binding_failed")
                    summary = _runtime_summary(observation)
                    self._runtime_observation = observation
                    self._runtime_summary = summary
                except Exception:
                    raise ServiceError("runtime_binding_failed") from None
            self._socket = _bind_socket(self._launch)
            candidate = (_create_controller(self._admission) if self._configuration is None else
                         _create_controller(self._admission, configuration=self._configuration,
                                            child_context=self._child_context))
            if getattr(candidate, "_admission", None) is not self._admission:
                # A miswired factory is not authority to close someone else's
                # controller. Adopt only the candidate enrolled in our gate.
                raise ServiceError("controller_binding_failed")
            self._controller = candidate
            if (getattr(candidate, "service_configuration", None) is not self._configuration
                    or (self._configuration is not None
                        and (candidate.state_dir != self._configuration.state_dir
                             or candidate.state_file != self._configuration.state_dir / "state.json"))
                    or self._controller.managed_startup_state is not None
                    or self._admission.snapshot()["state"] not in {"open", "unknown"}):
                raise ServiceError("controller_binding_failed")
            if (self._configuration is not None
                    and getattr(candidate, "child_context", None) is not self._child_context):
                raise ServiceError("child_binding_failed")
            try:
                self._launch.validate(os.environ)
            except Exception:
                raise ServiceError("invalid_launch") from None
            self._core = _build_core(self._launch, self._controller)
            if self._core._reasonfirst_controller is not self._controller:
                raise ServiceError("core_binding_failed")
            if (self._configuration is not None
                    and getattr(self._core, "_reasonfirst_service_configuration", None) is not self._configuration):
                raise ServiceError("core_binding_failed")
            if (self._configuration is not None
                    and getattr(self._core, "_reasonfirst_child_context", None) is not self._child_context):
                raise ServiceError("child_binding_failed")
            raw_app = self._core.streamable_http_app(
                host=self._launch.host, streamable_http_path=self._launch.path,
                stateless_http=True, json_response=True, max_request_body_size=4 * 1024 * 1024,
            )
            if (sum(getattr(route, "path", None) == self._launch.path for route in raw_app.routes) != 1
                    or sum(getattr(route, "path", None) == "/healthz" for route in raw_app.routes) != 1):
                raise ServiceError("core_binding_failed")
            self._app = _AdmissionApp(self, raw_app)
            self._server = _create_server(self._app, self._launch)
            self._server_task = asyncio.create_task(self._run_server())
            # Retrieve fixed task failures even when the host never calls close.
            self._server_task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
            while not self._server.started:
                if self._server_task.done():
                    raise ServiceError(self._error_code or "service_startup_failed")
                if time.monotonic() >= deadline:
                    raise ServiceError("service_startup_timeout")
                await asyncio.sleep(0.005)
            with self._lock:
                if self._state != "starting":
                    raise ServiceError("service_closed" if self._state == "closing" else "service_startup_failed")
                if self._server_task.done():
                    raise ServiceError("service_exited")
                _observe_listener(self._server, self._socket, self._launch)
                self._verify_configuration_locked()
                self._revalidate_runtime_locked()
                if time.monotonic() >= deadline:
                    raise ServiceError("service_startup_timeout")
                self._state = "running"
        except BaseException as exc:
            code = (exc.code if isinstance(exc, ServiceError) else "service_cancelled"
                    if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)) else "service_startup_failed")
            with self._lock:
                self._error_code = self._error_code or code
                self._admission.close()
                if isinstance(exc, ServiceError) and exc.cleanup_code is not None:
                    self._cleanup_error_code = self._cleanup_error_code or exc.cleanup_code
            try:
                await self.aclose()
            except ServiceError:
                pass
            raise ServiceError(code, cleanup_code=self._cleanup_error_code) from None

    def _cleanup_failed(self, code):
        with self._lock:
            self._cleanup_error_code = self._cleanup_error_code or code
            self._admission.mark_unknown("transport_lost")

    async def _wait_task(self, task, deadline, *, allow_error=False):
        if task is None:
            return True
        if not task.done():
            await asyncio.wait({task}, timeout=max(0.0, deadline - time.monotonic()))
        if not task.done():
            return False
        try:
            task.result()
        except BaseException:
            return allow_error
        return True

    async def _close_controller(self):
        try:
            await asyncio.to_thread(self._controller.close)
        except BaseException:
            raise ServiceError("controller_cleanup_failed") from None

    async def _shutdown_lifespan(self, lifespan):
        try:
            await lifespan.shutdown()
        except BaseException:
            raise ServiceError("lifespan_cleanup_failed") from None

    async def _cleanup(self):
        deadline = time.monotonic() + CLOSE_TIMEOUT_SECONDS
        try:
            if self._server is not None:
                self._server.should_exit = True
                # Stop accepting immediately, including startup-failure paths
                # where Uvicorn does not enter its normal shutdown routine.
                for actual in getattr(self._server, "servers", ()):
                    try:
                        actual.close()
                    except BaseException:
                        self._cleanup_failed("listener_cleanup_failed")
                server_state = getattr(self._server, "server_state", None)
                for connection in tuple(getattr(server_state, "connections", ())):
                    try:
                        connection.shutdown()
                    except BaseException:
                        self._cleanup_failed("server_cleanup_failed")
                server_budget = min(deadline, time.monotonic() + CLOSE_TIMEOUT_SECONDS / 2)
                if not await self._wait_task(self._server_task, server_budget, allow_error=True):
                    if self._server_task is not None and not self._server_task.done():
                        self._server_task.cancel()
                    self._cleanup_failed("server_cleanup_failed")
                    await self._wait_task(self._server_task, min(deadline, time.monotonic() + 0.25),
                                          allow_error=True)
                lifespan = getattr(self._server, "lifespan", None)
                if lifespan is not None and not lifespan.shutdown_event.is_set():
                    self._lifespan_shutdown_task = asyncio.create_task(self._shutdown_lifespan(lifespan))
                    self._lifespan_shutdown_task.add_done_callback(
                        lambda task: None if task.cancelled() else task.exception())
                    if not await self._wait_task(self._lifespan_shutdown_task, deadline):
                        self._lifespan_shutdown_task.cancel()
                        self._cleanup_failed("lifespan_cleanup_failed")
                if lifespan is not None and lifespan.shutdown_failed:
                    self._cleanup_failed("lifespan_cleanup_failed")
                with self._lock:
                    requests = set(self._request_tasks.values())
                requests.update(getattr(getattr(self._server, "server_state", None), "tasks", ()))
                requests.discard(None)
                if requests:
                    _, pending = await asyncio.wait(requests, timeout=max(0.0, deadline - time.monotonic()))
                    if pending:
                        for task in pending:
                            task.cancel()
                        self._cleanup_failed("request_cleanup_failed")
        except BaseException:
            self._cleanup_failed("service_cleanup_failed")
        finally:
            if self._server_task is not None and not self._server_task.done():
                try:
                    self._server_task.cancel()
                    if not await self._wait_task(self._server_task, deadline, allow_error=True):
                        self._cleanup_failed("server_cleanup_failed")
                except BaseException:
                    self._cleanup_failed("server_cleanup_failed")
            # Uvicorn startup exceptions bypass its shutdown method. Retained
            # protocol objects are ours too; do not leave idle connections open.
            if self._server is not None:
                server_state = getattr(self._server, "server_state", None)
                for connection in tuple(getattr(server_state, "connections", ())):
                    try:
                        connection.transport.close()
                    except BaseException:
                        self._cleanup_failed("server_cleanup_failed")
                for actual in getattr(self._server, "servers", ()):
                    try:
                        task = asyncio.create_task(actual.wait_closed())
                        self._listener_close_tasks.append(task)
                        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
                        if not await self._wait_task(task, deadline):
                            self._cleanup_failed("listener_cleanup_failed")
                    except BaseException:
                        self._cleanup_failed("listener_cleanup_failed")
            # A failed/interrupted server phase cannot skip controller or FD
            # cleanup. A timed-out controller-close thread remains owned by the
            # retained task; there is no claim that Python can forcibly stop it.
            if self._controller is not None:
                try:
                    self._controller_close_task = asyncio.create_task(self._close_controller())
                    self._controller_close_task.add_done_callback(
                        lambda task: None if task.cancelled() else task.exception())
                    if not await self._wait_task(self._controller_close_task, deadline):
                        self._cleanup_failed("controller_cleanup_failed")
                except BaseException:
                    self._cleanup_failed("controller_cleanup_failed")
            if self._socket is not None:
                try:
                    self._socket.close()
                except BaseException:
                    self._cleanup_failed("listener_cleanup_failed")
            with self._lock:
                self._cleanup_complete = self._cleanup_error_code is None
                self._state = "failed" if self._error_code or self._cleanup_error_code else "closed"

    async def aclose(self):
        loop = self._check_loop()
        with self._lock:
            if self._loop is None:
                self._loop = loop
            self._admission.close()
            if self._cleanup_task is None:
                self._state = "closing"
                self._cleanup_task = asyncio.create_task(self._cleanup())
        cancelled = False
        while not self._cleanup_task.done():
            try:
                await asyncio.shield(self._cleanup_task)
            except (asyncio.CancelledError, KeyboardInterrupt):
                cancelled = True
            except BaseException:
                self._cleanup_failed("service_cleanup_failed")
        if cancelled:
            with self._lock:
                self._error_code = self._error_code or "service_cancelled"
                self._state = "failed"
            raise ServiceError("service_cancelled", cleanup_code=self._cleanup_error_code)
        if self._cleanup_error_code is not None:
            raise ServiceError("service_cleanup_failed", cleanup_code=self._cleanup_error_code)
