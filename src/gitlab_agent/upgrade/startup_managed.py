"""Disposable POSIX startup observation, never activation or a service updater.

The supervisor retains an exclusive bound, initially non-listening TCP socket.
Only its explicitly owned child receives that socket and the private pipe ends.
The SDK's ASGI application is hosted unchanged; the managed adapter observes the
actual Uvicorn/asyncio server after startup and the parent independently observes
its listening state on the same kernel socket. Linux uses SO_ACCEPTCONN; Darwin
uses the public TCP_CONNECTION_INFO state byte. An inherited handle is not attestation.

Only selected, revalidated prepared runtimes are supported. There is no editable
source fallback, arbitrary command launcher, expected-claims child argument,
service-manager integration, persistent configuration, or worker admission.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

from ..bridge_http import HTTPLaunch, HTTPLaunchError
from ..python_runtime import executable_fingerprint, _matches_invocation_path
from . import runtime
from .startup_channel import ChannelError, mark_noninheritable, read_frame, write_frame
from .startup_protocol import (
    LIFETIME_NS, PendingStartupChallenge, StartupClaims, StartupProtocolError, make_reply,
)

# This adapter observes lifecycle implementation details. Other SDK versions may
# remain usable by the unmanaged transports, but need a new reviewed adapter.
# MCP publishing commit 2118f14f8a19bc158d8a1cf90af58d85d187f849;
# Uvicorn publishing commit 3eb9a9ab9af69005c687728097461c1fd2a95db9.
SUPPORTED_LISTENER_SDK = {"mcp": "2.3.0", "uvicorn": "0.54.0"}
CATALOG_LIFETIME_NS = 8_000_000_000
CATALOG_MAX_BYTES = 512 * 1024
_CODES = frozenset({
    "unsupported_managed_startup_platform", "invalid_launch", "invalid_cancel",
    "runtime_not_ready", "runtime_record_mismatch", "runtime_identity_mismatch",
    "interpreter_identity_mismatch", "runtime_import_mismatch", "runtime_drifted",
    "unsupported_listener_sdk", "configuration_mismatch", "managed_state_required",
    "unsupported_listener_query", "listener_observation_failed",
    "invalid_child_arguments", "listener_bind_failed", "invalid_listener",
    "listener_not_started", "child_exited", "child_startup_failed", "startup_failed",
    "startup_cancelled", "startup_timeout", "startup_reply_failed",
    "catalog_probe_failed", "catalog_reply_limit", "catalog_unexpected_endpoint",
    "catalog_helper_forced_termination", "catalog_helper_cleanup_failed",
    "catalog_mismatch", "parent_controller_cleanup_failed", "child_controller_cleanup_failed", "child_forced_termination",
    "child_cleanup_failed", "fd_cleanup_failed", "fixture_cleanup_failed", "probe_failed",
})
_CHILD_CODES = {code: 64 + index for index, code in enumerate(sorted(_CODES))}
_CHILD_ERRORS = {value: key for key, value in _CHILD_CODES.items()}


class ManagedStartupError(RuntimeError):
    """A fixed code without input, peer, environment, or exception values."""

    def __init__(self, code: str):
        self.code = code if type(code) is str and code in _CODES else "probe_failed"
        self.cleanup_code = None
        super().__init__(self.code)


def _fail(code: str):
    raise ManagedStartupError(code) from None


def _supported():
    system = platform.system()
    if os.name != "posix" or system not in {"Darwin", "Linux"}:
        _fail("unsupported_managed_startup_platform")
    option = "TCP_CONNECTION_INFO" if system == "Darwin" else "SO_ACCEPTCONN"
    if type(getattr(socket, option, None)) is not int:
        _fail("unsupported_listener_query")


def _sdk_supported(packages):
    if any(packages.get(name) != version for name, version in SUPPORTED_LISTENER_SDK.items()):
        _fail("unsupported_listener_sdk")


def _supervisor_sdk_supported():
    try:
        packages = {name: importlib.metadata.version(name) for name in SUPPORTED_LISTENER_SDK}
    except Exception:
        _fail("unsupported_listener_sdk")
    _sdk_supported(packages)


@dataclass(frozen=True, repr=False)
class _RuntimeSelection:
    runtime_id: str
    home: Path
    root: Path
    venv: Path
    executable: Path
    manifest_digest: str
    record: dict
    interpreter_digest: str


def _interpreter_projection(invoked, fingerprint, base_fingerprint, version, prefix):
    # Preserve the venv invocation leaf. Resolving the executable itself would
    # silently select the external base interpreter instead of this venv.
    invoked = Path(invoked)
    return {"schema": "reasonfirst-startup-interpreter-v1",
            "invoked": str(invoked.parent.resolve(strict=True) / invoked.name),
            "fingerprint": fingerprint, "base_fingerprint": base_fingerprint,
            "python_version": list(version), "prefix": str(Path(prefix).resolve(strict=True)),
            "venv": True}


def _select_runtime(runtime_id, home):
    result = runtime.status(runtime_id=runtime_id, home=home)
    if (result.get("prepared") is not True
            or result.get("runtime_status") != "prepared_matches_record"):
        _fail("runtime_not_ready")
    root = Path(result["runtime_path"])
    actual_home = root.parents[len(runtime.PARTS)]
    with runtime.storage._directory(actual_home, runtime.PARTS + (runtime_id,)) as fd:
        raw = runtime.storage._read_at(fd, "runtime.json", limit=runtime.MAX_MANIFEST, private=True)
    if raw is None:
        _fail("runtime_record_mismatch")
    record = json.loads(raw, object_pairs_hook=runtime.unique)
    unsigned = dict(record)
    signature = unsigned.pop("manifest_digest", None)
    if (signature != result["manifest_digest"] or signature != runtime.digest(unsigned)
            or record["runtime_id"] != runtime_id
            or record["observation"] != result["observed"]):
        _fail("runtime_record_mismatch")
    _sdk_supported(record["observation"]["packages"])
    venv = root / "venv"
    executable = venv / "bin" / "python"
    fingerprint = executable_fingerprint(str(executable))
    base = record["input"]["python"]["fingerprint"]
    if (fingerprint["resolved_file"] != base["resolved_file"]
            or fingerprint["sha256"] != base["sha256"]
            or fingerprint["venv_config_sha256"] is None):
        _fail("interpreter_identity_mismatch")
    projection = _interpreter_projection(executable, fingerprint, base,
                                        record["observation"]["python_version"], venv)
    return _RuntimeSelection(runtime_id, actual_home, root, venv, executable,
                             signature, record, runtime.digest(projection))


def _actual_runtime():
    """Derive the selected runtime from this interpreter, never parent claims."""
    prefix = Path(sys.prefix).resolve(strict=True)
    if len(prefix.parents) < 6 or prefix.name != "venv" or sys.prefix == sys.base_prefix:
        _fail("runtime_identity_mismatch")
    runtime_id = prefix.parent.name
    if not runtime.HEX.fullmatch(runtime_id):
        _fail("runtime_identity_mismatch")
    home = prefix.parents[len(runtime.PARTS) + 1]
    if prefix != home.joinpath(*runtime.PARTS, runtime_id, "venv"):
        _fail("runtime_identity_mismatch")
    selected = _select_runtime(runtime_id, home)
    if not _matches_invocation_path(sys.executable, selected.executable):
        _fail("interpreter_identity_mismatch")
    fingerprint = executable_fingerprint(sys.executable)
    base = executable_fingerprint(sys._base_executable)
    if base != selected.record["input"]["python"]["fingerprint"]:
        _fail("interpreter_identity_mismatch")
    projection = _interpreter_projection(sys.executable, fingerprint, base,
                                        sys.version_info[:3], prefix)
    if runtime.digest(projection) != selected.interpreter_digest:
        _fail("interpreter_identity_mismatch")
    pairs = [(runtime.norm(dist.metadata["Name"]), dist.version)
             for dist in importlib.metadata.distributions()]
    packages = dict(pairs)
    if len(packages) != len(pairs) or packages != selected.record["observation"]["packages"]:
        _fail("runtime_identity_mismatch")
    _sdk_supported(packages)
    package = importlib.import_module("gitlab_agent")
    if package.__version__ != selected.record["observation"]["version"]:
        _fail("runtime_identity_mismatch")
    if not Path(__file__).resolve(strict=True).is_relative_to(prefix):
        _fail("runtime_import_mismatch")
    names = ("gitlab_agent", "gitlab_agent.bridge_http", "gitlab_agent.bridge_mcp",
             "gitlab_agent.bridge_preview.controller", "gitlab_agent.upgrade.startup_managed",
             "gitlab_agent.upgrade.startup_channel", "gitlab_agent.upgrade.startup_state",
             "mcp.server.mcpserver.server", "uvicorn.server")
    origins = []
    for name in names:
        origin = Path(importlib.import_module(name).__file__).resolve(strict=True)
        if not origin.is_relative_to(prefix):
            _fail("runtime_import_mismatch")
        origins.append(str(origin.relative_to(prefix)))
    if origins[:4] != selected.record["observation"]["origins"]:
        _fail("runtime_import_mismatch")
    return selected


def _claims(selected, launch, configuration_digest):
    return StartupClaims(selected.runtime_id, selected.manifest_digest,
                         selected.interpreter_digest, configuration_digest,
                         launch.host, launch.port, launch.path, launch.mode,
                         launch.control_policy)


def _socket_listening(sock):
    """Read the required kernel state of this owned socket, without fallback."""
    _supported()
    try:
        if platform.system() == "Darwin":
            # XNU f6217f891ac0bb64f3d375211650a4c1ff8ca1ea:
            # tcp.h exposes tcpi_state as the first u_int8_t field;
            # tcp_usrreq.c copies tp->t_state; tcp_fsm.h defines CLOSED=0,
            # LISTEN=1. sooptcopyout supports this bounded public prefix.
            # Darwin defines SO_ACCEPTCONN but does not implement its query.
            state = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_CONNECTION_INFO, 1)
            if type(state) is not bytes or len(state) != 1:
                _fail("listener_observation_failed")
            value = state[0]
        else:
            value = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
            if type(value) is not int:
                _fail("listener_observation_failed")
    except (OSError, ValueError):
        _fail("listener_observation_failed")
    if value not in (0, 1):
        _fail("invalid_listener")
    return value == 1


def _exclusive_socket(launch):
    family = socket.AF_INET6 if launch.host == "::1" else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.set_inheritable(False)
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        # Never set SO_REUSEPORT/SO_REUSEADDR; never release/reacquire the port.
        sock.bind((launch.host, launch.port))
        if _socket_listening(sock):
            _fail("invalid_listener")
        return sock
    except BaseException:
        sock.close()
        raise


def _listener_observation(sock, launch, *, listening):
    try:
        address = sock.getsockname()
        valid = (sock.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_STREAM
                 and address[:2] == (launch.host, launch.port)
                 and _socket_listening(sock) is listening)
    except (OSError, ValueError):
        valid = False
    if not valid:
        _fail("listener_not_started" if listening else "invalid_listener")


def _observe_actual_server(server, sock, launch):
    if not server.started or server.should_exit:
        _fail("listener_not_started")
    actual = server.servers
    if len(actual) != 1 or not actual[0].is_serving():
        _fail("listener_not_started")
    sockets = actual[0].sockets
    if not sockets or len(sockets) != 1 or sockets[0].fileno() != sock.fileno():
        _fail("listener_not_started")
    _listener_observation(sockets[0], launch, listening=True)


def _check_budget(deadline_ns, cancel=None, is_alive=None):
    if _cancelled(cancel):
        _fail("startup_cancelled")
    if is_alive is not None and not is_alive():
        _fail("child_exited")
    if time.monotonic_ns() >= deadline_ns:
        _fail("startup_timeout")


def _cancelled(cancel):
    if cancel is None:
        return False
    if not isinstance(cancel, threading.Event):
        _fail("invalid_cancel")
    try:
        value = cancel.is_set()
    except Exception:
        _fail("invalid_cancel")
    if type(value) is not bool:
        _fail("invalid_cancel")
    return value


def _child_environment(overrides):
    env = {"PATH": os.defpath, "LANG": "C", "LC_ALL": "C",
           "PYTHONDONTWRITEBYTECODE": "1", "UV_PYTHON_DOWNLOADS": "never"}
    env.update(overrides)
    return env


async def _serve_child(launch, sock, selected, snapshot, challenge, reply_fd, deadline_ns):
    import uvicorn
    from ..bridge_preview.controller import BridgeController
    from ..bridge_mcp import build_server

    ctrl = BridgeController(managed_startup=snapshot)
    cleanup_failed = False
    try:
        core = build_server(read_only_mode=launch.mode == "read-only", controller=ctrl)
        if (ctrl.managed_startup_state is not snapshot
                or core._reasonfirst_controller is not ctrl
                or ctrl.state_dir != snapshot.state_dir
                or ctrl.state_file != snapshot.state_dir / "state.json"):
            _fail("managed_state_required")
        app = core.streamable_http_app(host=launch.host, streamable_http_path=launch.path)
        # These are the very route objects served by this ASGI application.
        if sum(getattr(route, "path", None) == launch.path for route in app.routes) != 1:
            _fail("configuration_mismatch")

        class ObservedServer(uvicorn.Server):
            managed_shutdown_requested = False

            @contextmanager
            def capture_signals(self):
                # Uvicorn's default context re-raises SIGTERM after shutdown,
                # which could terminate before the controller finally block.
                previous = {}
                def request_shutdown(_signum, _frame):
                    self.managed_shutdown_requested = True
                    self.should_exit = True
                try:
                    for signum in (signal.SIGINT, signal.SIGTERM):
                        previous[signum] = signal.signal(signum, request_shutdown)
                    yield
                finally:
                    for signum, handler in previous.items():
                        signal.signal(signum, handler)

            async def startup(self, sockets=None):
                await super().startup(sockets=sockets)
                _observe_actual_server(self, sock, launch)
                _check_budget(deadline_ns)
                observed_launch = HTTPLaunch(sock.getsockname()[0], sock.getsockname()[1],
                                             launch.path, launch.mode, launch.control_policy)
                observed = _claims(selected, observed_launch,
                                   ctrl.managed_startup_state.configuration_digest(observed_launch))
                write_frame(reply_fd, make_reply(challenge, observed=observed), deadline_ns=deadline_ns)
                os.close(reply_fd)

        server = ObservedServer(uvicorn.Config(app, host=launch.host, port=launch.port,
            workers=1, reload=False, lifespan="on", timeout_graceful_shutdown=1,
            log_config=None, log_level="critical", access_log=False))
        try:
            await server.serve(sockets=[sock])
            if not server.managed_shutdown_requested:
                _fail("child_exited")
        finally:
            # startup() exceptions do not pass through Uvicorn's normal shutdown.
            for actual in getattr(server, "servers", ()):
                actual.close()
                await actual.wait_closed()
            lifespan = getattr(server, "lifespan", None)
            if lifespan is not None and not lifespan.shutdown_event.is_set():
                await asyncio.wait_for(lifespan.shutdown(), timeout=2)
            if lifespan is not None and lifespan.shutdown_failed:
                _fail("child_controller_cleanup_failed")
    finally:
        try:
            ctrl.close()
        except BaseException:
            cleanup_failed = True
        if cleanup_failed:
            _fail("child_controller_cleanup_failed")


def _load_actual_state(launch):
    from ..config import AgentSettings
    settings = AgentSettings.load()
    # The existing .env loader may introduce launch policy or Python overrides.
    # Revalidate before Bridge config/controller loading, never normalize drift.
    launch.validate(os.environ)
    from ..bridge_preview.bridge_config import load_bridge_config
    from .startup_state import capture_startup_state
    return capture_startup_state(settings, load_bridge_config(),
        bridge_config_path=Path(os.environ["RF_BRIDGE_CONFIG"]),
        state_dir=Path(os.environ["RF_CODEX_BRIDGE_STATE_DIR"]))


def _child(args):
    _supported()
    fds = (args.challenge_fd, args.reply_fd, args.listener_fd)
    if any(type(fd) is not int or fd < 3 for fd in fds) or len(set(fds)) != 3:
        _fail("invalid_child_arguments")
    mark_noninheritable(args.challenge_fd, args.reply_fd)
    os.set_inheritable(args.listener_fd, False)
    now = time.monotonic_ns()
    if not now < args.deadline_ns <= now + LIFETIME_NS:
        _fail("startup_timeout")
    launch = HTTPLaunch(args.host, args.port, args.path, args.mode, "disabled")
    launch.validate(os.environ)
    sock = socket.socket(fileno=args.listener_fd)
    try:
        _listener_observation(sock, launch, listening=False)
        challenge = read_frame(args.challenge_fd, deadline_ns=args.deadline_ns)
        os.close(args.challenge_fd)
        selected = _actual_runtime()
        snapshot = _load_actual_state(launch)
        _check_budget(args.deadline_ns)
        asyncio.run(_serve_child(launch, sock, selected, snapshot, challenge,
                                 args.reply_fd, args.deadline_ns))
    finally:
        sock.close()


def _base_report():
    return {"operation": "disposable-managed-startup", "ok": False,
        "error_code": None, "cleanup_error_code": None, "cleanup_confirmed": False,
        "child_reaped": False, "child_controller_cleanup_confirmed": False,
        "private_channel_owned": False, "fresh_reply_claims_match": False,
        "pid_claim_matches": False, "listener_reserved": False,
        "listener_startup_observed": False, "parent_socket_listening_observed": False,
        "endpoint_catalog_verified": False, "selected_runtime_revalidated": False,
        "catalog_helper_reaped": False, "catalog_helper_cleanup_confirmed": False,
        "configuration_projection_matches": False, "child_import_origins_verified": False,
        "codec_result": None, "peer_identity_verified": False,
        "running_code_verified": False, "effective_configuration_verified": False,
        "managed_startup_confirmation_verified": False, "compatibility_verified": False,
        "activation_authorized": False, "ready_for_activation": False,
        "service_changed": False, "descendant_containment_verified": False}


async def _probe_catalog_async(launch, snapshot, *, deadline_ns, cancel, is_alive):
    """Real SDK discovery/listing, with bounded HTTP bytes and no proxy/redirects."""
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client
    from ..bridge_mcp import build_server
    from ..bridge_preview.controller import BridgeController

    host = "[::1]" if launch.host == "::1" else launch.host
    endpoint = httpx2.URL(f"http://{host}:{launch.port}{launch.path}")
    remaining = CATALOG_MAX_BYTES

    class LimitedStream(httpx2.AsyncByteStream):
        def __init__(self, stream):
            self.stream = stream
        async def __aiter__(self):
            nonlocal remaining
            async for chunk in self.stream:
                _check_budget(deadline_ns, cancel, is_alive)
                if len(chunk) > remaining:
                    _fail("catalog_reply_limit")
                remaining -= len(chunk)
                yield chunk
        async def aclose(self):
            await self.stream.aclose()

    class LimitedTransport(httpx2.AsyncBaseTransport):
        def __init__(self):
            self.inner = httpx2.AsyncHTTPTransport(trust_env=False, retries=0,
                                                  limits=httpx2.Limits(max_connections=4))
        async def handle_async_request(self, request):
            _check_budget(deadline_ns, cancel, is_alive)
            if request.url != endpoint:
                _fail("catalog_unexpected_endpoint")
            response = await self.inner.handle_async_request(request)
            try:
                _check_budget(deadline_ns, cancel, is_alive)
                if 300 <= response.status_code < 400:
                    _fail("catalog_unexpected_endpoint")
                if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                    _fail("catalog_reply_limit")
                response.stream = LimitedStream(response.stream)
                return response
            except BaseException:
                await response.aclose()
                raise
        async def aclose(self):
            await self.inner.aclose()

    ctrl = BridgeController(managed_startup=snapshot)
    failure = None
    try:
        core = build_server(read_only_mode=launch.mode == "read-only", controller=ctrl)
        async with httpx2.AsyncClient(transport=LimitedTransport(), trust_env=False,
                timeout=httpx2.Timeout(2.0), limits=httpx2.Limits(max_connections=4),
                headers={"Accept-Encoding": "identity"}, follow_redirects=False) as http:
            for mode in ("auto", "legacy"):
                _check_budget(deadline_ns, cancel, is_alive)
                async with Client(core, mode=mode, cache=None) as expected_client:
                    expected = await expected_client.list_tools()
                transport = streamable_http_client(str(endpoint), http_client=http,
                                                   max_sse_event_size=CATALOG_MAX_BYTES)
                async with Client(transport, mode=mode, read_timeout_seconds=2.0, cache=None) as client:
                    actual = await client.list_tools()
                def catalog(result):
                    names = [tool.name for tool in result.tools]
                    if len(set(names)) != len(names) or getattr(result, "next_cursor", None) is not None:
                        _fail("catalog_mismatch")
                    return {tool.name: tool.model_dump(mode="json") for tool in result.tools}
                if catalog(actual) != catalog(expected) or len(actual.tools) != (12 if launch.mode == "read-only" else 22):
                    _fail("catalog_mismatch")
                _check_budget(deadline_ns, cancel, is_alive)
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            ctrl.close()
        except BaseException:
            error = ManagedStartupError(failure.code if isinstance(failure, ManagedStartupError)
                                        else "startup_cancelled" if isinstance(failure, KeyboardInterrupt)
                                        else "catalog_probe_failed")
            error.cleanup_code = "parent_controller_cleanup_failed"
            raise error from None


def _probe_catalog(launch, configuration, *, cancel, is_alive):
    """Isolate SDK logs/global logging configuration in one owned finite helper."""
    deadline_ns = time.monotonic_ns() + CATALOG_LIFETIME_NS
    helper = None
    failure = None
    try:
        _check_budget(deadline_ns, cancel, is_alive)
        helper = subprocess.Popen([sys.executable, "-I", "-B", "-m",
            "gitlab_agent.upgrade.startup_managed", "--catalog-probe",
            "--deadline-ns", str(deadline_ns), "--host", launch.host,
            "--port", str(launch.port), "--path", launch.path, "--mode", launch.mode],
            cwd=configuration.root, env=_child_environment(configuration.environment),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True)
        while True:
            _check_budget(deadline_ns, cancel, is_alive)
            code = helper.poll()
            if code is not None:
                helper.wait(timeout=0)
                if code == _CHILD_CODES["parent_controller_cleanup_failed"]:
                    failure = ManagedStartupError("catalog_probe_failed")
                    failure.cleanup_code = "parent_controller_cleanup_failed"
                    raise failure
                if code != 0:
                    _fail("catalog_probe_failed")
                break
            if cancel is not None:
                cancel.wait(0.05)
            else:
                time.sleep(0.05)
        _check_budget(deadline_ns, cancel, is_alive)
    except BaseException as exc:
        # Normalize before cleanup so forced termination cannot replace a
        # cancellation with the coarser catalog failure category.
        failure = (exc if isinstance(exc, ManagedStartupError) else
                   ManagedStartupError("startup_cancelled" if isinstance(exc, KeyboardInterrupt)
                                       else "catalog_probe_failed"))
    finally:
        if helper is not None:
            try:
                if helper.poll() is None:
                    helper.terminate()
                    try:
                        helper.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        helper.kill()
                        helper.wait(timeout=2)
                        if not isinstance(failure, ManagedStartupError):
                            failure = ManagedStartupError("catalog_probe_failed")
                        failure.cleanup_code = "catalog_helper_forced_termination"
                else:
                    helper.wait(timeout=0)
                if helper.returncode == _CHILD_CODES["parent_controller_cleanup_failed"]:
                    if not isinstance(failure, ManagedStartupError):
                        failure = ManagedStartupError("catalog_probe_failed")
                    failure.cleanup_code = "parent_controller_cleanup_failed"
                elif helper.returncode is not None and helper.returncode != 0 and helper.returncode not in _CHILD_ERRORS:
                    if not isinstance(failure, ManagedStartupError):
                        failure = ManagedStartupError("catalog_probe_failed")
                    failure.cleanup_code = failure.cleanup_code or "catalog_helper_cleanup_failed"
            except BaseException as exc:
                if not isinstance(failure, ManagedStartupError):
                    failure = ManagedStartupError("startup_cancelled" if isinstance(exc, KeyboardInterrupt)
                                                  else "catalog_probe_failed")
                failure.cleanup_code = "catalog_helper_cleanup_failed"
                # An interrupted wait must not abandon this owned process or
                # skip the caller's remaining descriptor/fixture cleanup.
                try:
                    if helper.poll() is None:
                        helper.kill()
                    helper.wait(timeout=2)
                except BaseException:
                    pass
    if failure is not None:
        if isinstance(failure, ManagedStartupError):
            raise failure
        _fail("catalog_probe_failed")
    return {"catalog_helper_reaped": True, "catalog_helper_cleanup_confirmed": True}


def _catalog_child(args):
    _supported()
    _supervisor_sdk_supported()
    prefix = Path(sys.prefix).resolve(strict=True)
    for name in ("gitlab_agent", "gitlab_agent.upgrade.startup_managed",
                 "gitlab_agent.upgrade.startup_state", "gitlab_agent.bridge_mcp",
                 "gitlab_agent.bridge_preview.controller"):
        if not Path(importlib.import_module(name).__file__).resolve(strict=True).is_relative_to(prefix):
            _fail("runtime_import_mismatch")
    if not Path(__file__).resolve(strict=True).is_relative_to(prefix):
        _fail("runtime_import_mismatch")
    launch = HTTPLaunch(args.host, args.port, args.path, args.mode, "disabled")
    launch.validate(os.environ)
    snapshot = _load_actual_state(launch)
    _check_budget(args.deadline_ns)
    async def bounded():
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        previous = {}
        try:
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous[signum] = signal.getsignal(signum)
                loop.add_signal_handler(signum, task.cancel)
            try:
                await asyncio.wait_for(_probe_catalog_async(launch, snapshot,
                    deadline_ns=args.deadline_ns, cancel=None, is_alive=None),
                    timeout=max(0, (args.deadline_ns - time.monotonic_ns()) / 1_000_000_000))
            except asyncio.CancelledError:
                _fail("startup_cancelled")
        finally:
            for signum, handler in previous.items():
                loop.remove_signal_handler(signum)
                signal.signal(signum, handler)
    asyncio.run(bounded())


def probe_disposable(*, runtime_id, home, launch, cancel=None):
    """Observe then clean up a new candidate; return only bounded evidence flags.

    The original protocol attempt gets at most eight seconds, measured before
    Popen. After its one reply is verified, catalog checks have their own bounded
    deadline; this never renews or reuses the consumed protocol challenge.
    """
    report = _base_report()
    proc = None
    sock = None
    fixture = None
    pending = None
    selected = None
    fds = set()
    try:
        _supported()
        if type(launch) is not HTTPLaunch:
            _fail("invalid_launch")
        launch.validate({})
        if cancel is not None and not isinstance(cancel, threading.Event):
            _fail("invalid_cancel")
        if _cancelled(cancel):
            _fail("startup_cancelled")
        _supervisor_sdk_supported()
        selected = _select_runtime(runtime_id, home)
        if _cancelled(cancel):
            _fail("startup_cancelled")
        try:
            sock = _exclusive_socket(launch)
        except OSError:
            _fail("listener_bind_failed")
        report["listener_reserved"] = True
        fixture = tempfile.TemporaryDirectory(prefix="rf-managed-startup-")
        from .startup_state import create_disposable_configuration
        configuration = create_disposable_configuration(Path(fixture.name) / "candidate")
        expected = _claims(selected, launch, configuration.snapshot.configuration_digest(launch))
        challenge_r, challenge_w = os.pipe()
        fds.update((challenge_r, challenge_w))
        reply_r, reply_w = os.pipe()
        fds.update((reply_r, reply_w))
        mark_noninheritable(*fds)
        deadline_ns = time.monotonic_ns() + LIFETIME_NS
        argv = [str(selected.executable), "-I", "-B", "-m",
                "gitlab_agent.upgrade.startup_managed", "--child",
                "--challenge-fd", str(challenge_r), "--reply-fd", str(reply_w),
                "--listener-fd", str(sock.fileno()), "--deadline-ns", str(deadline_ns),
                "--host", launch.host, "--port", str(launch.port),
                "--path", launch.path, "--mode", launch.mode]
        _check_budget(deadline_ns, cancel)
        proc = subprocess.Popen(argv, cwd=configuration.root,
            env=_child_environment(configuration.environment),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            pass_fds=(challenge_r, reply_w, sock.fileno()), close_fds=True)
        report["private_channel_owned"] = True
        for fd in (challenge_r, reply_w):
            os.close(fd)
            fds.remove(fd)
        is_alive = lambda: proc.poll() is None
        pending = PendingStartupChallenge(expected=expected, expected_pid=proc.pid)
        try:
            write_frame(challenge_w, pending.start(), deadline_ns=deadline_ns,
                        cancel=cancel, is_alive=is_alive)
        finally:
            os.close(challenge_w)
            fds.remove(challenge_w)
        reply = read_frame(reply_r, deadline_ns=deadline_ns, cancel=cancel, is_alive=is_alive)
        report["codec_result"] = pending.finish(reply)
        report["fresh_reply_claims_match"] = True
        report["pid_claim_matches"] = True
        _check_budget(deadline_ns, cancel, is_alive)
        _listener_observation(sock, launch, listening=True)
        report.update(listener_startup_observed=True, parent_socket_listening_observed=True,
                      configuration_projection_matches=True, child_import_origins_verified=True)
        helper_evidence = _probe_catalog(launch, configuration, cancel=cancel, is_alive=is_alive)
        helper_flags = {"catalog_helper_reaped", "catalog_helper_cleanup_confirmed"}
        if (type(helper_evidence) is not dict or set(helper_evidence) != helper_flags
                or any(helper_evidence[name] is not True for name in helper_flags)):
            _fail("catalog_probe_failed")
        report.update(helper_evidence)
        if _cancelled(cancel):
            _fail("startup_cancelled")
        _listener_observation(sock, launch, listening=True)
        if not is_alive():
            _fail("child_exited")
        report["endpoint_catalog_verified"] = True
    except ManagedStartupError as exc:
        report["error_code"] = exc.code
        report["cleanup_error_code"] = exc.cleanup_code
    except HTTPLaunchError:
        report["error_code"] = "invalid_launch"
    except StartupProtocolError:
        report["error_code"] = "startup_reply_failed"
    except ChannelError as exc:
        report["error_code"] = {"channel_timeout": "startup_timeout",
            "channel_cancelled": "startup_cancelled", "child_exited": "child_exited"}.get(exc.code, "startup_reply_failed")
    except KeyboardInterrupt:
        report["error_code"] = "startup_cancelled"
    except BaseException:
        report["error_code"] = "probe_failed"
    finally:
        if pending is not None:
            try:
                pending.cancel()
            except BaseException as exc:
                report["error_code"] = report["error_code"] or (
                    "startup_cancelled" if isinstance(exc, KeyboardInterrupt) else "probe_failed")
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=2)
                        report["cleanup_error_code"] = "child_forced_termination"
                else:
                    proc.wait(timeout=0)
                    if report["error_code"] is None:
                        report["error_code"] = "child_exited"
                report["child_reaped"] = True
                if proc.returncode == 0:
                    report["child_controller_cleanup_confirmed"] = True
                elif proc.returncode == _CHILD_CODES["child_controller_cleanup_failed"]:
                    report["cleanup_error_code"] = "child_controller_cleanup_failed"
                elif report["error_code"] is None:
                    report["error_code"] = _CHILD_ERRORS.get(proc.returncode, "child_startup_failed")
            except BaseException as exc:
                if isinstance(exc, KeyboardInterrupt) and report["error_code"] is None:
                    report["error_code"] = "startup_cancelled"
                report["cleanup_error_code"] = "child_cleanup_failed"
                try:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait(timeout=2)
                    report["child_reaped"] = True
                except BaseException:
                    pass
        for fd in fds:
            try:
                os.close(fd)
            except BaseException as exc:
                if isinstance(exc, KeyboardInterrupt) and report["error_code"] is None:
                    report["error_code"] = "startup_cancelled"
                report["cleanup_error_code"] = report["cleanup_error_code"] or "fd_cleanup_failed"
        if sock is not None:
            try:
                sock.close()
            except BaseException as exc:
                if isinstance(exc, KeyboardInterrupt) and report["error_code"] is None:
                    report["error_code"] = "startup_cancelled"
                report["cleanup_error_code"] = report["cleanup_error_code"] or "fd_cleanup_failed"
        if fixture is not None:
            try:
                fixture.cleanup()
            except BaseException as exc:
                if isinstance(exc, KeyboardInterrupt) and report["error_code"] is None:
                    report["error_code"] = "startup_cancelled"
                report["cleanup_error_code"] = report["cleanup_error_code"] or "fixture_cleanup_failed"
        if selected is not None:
            try:
                after = _select_runtime(selected.runtime_id, selected.home)
                if (after.manifest_digest != selected.manifest_digest
                        or after.interpreter_digest != selected.interpreter_digest):
                    _fail("runtime_drifted")
                report["selected_runtime_revalidated"] = True
            except BaseException as exc:
                report["error_code"] = report["error_code"] or (
                    "startup_cancelled" if isinstance(exc, KeyboardInterrupt) else "runtime_drifted")
        report["cleanup_confirmed"] = report["cleanup_error_code"] is None
        try:
            if _cancelled(cancel) and report["error_code"] is None:
                report["error_code"] = "startup_cancelled"
        except BaseException as exc:
            report["error_code"] = report["error_code"] or (
                "startup_cancelled" if isinstance(exc, KeyboardInterrupt) else "invalid_cancel")
        report["ok"] = (report["error_code"] is None and report["cleanup_confirmed"]
                        and report["child_controller_cleanup_confirmed"]
                        and report["catalog_helper_reaped"] is True
                        and report["catalog_helper_cleanup_confirmed"] is True
                        and report["endpoint_catalog_verified"])
    return report


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        _fail("invalid_child_arguments")


def main(argv=None):
    """Internal inherited-handle child entry; not a user service command."""
    try:
        _supported()
        parser = _Parser(add_help=False, allow_abbrev=False)
        action = parser.add_mutually_exclusive_group(required=True)
        action.add_argument("--child", action="store_true")
        action.add_argument("--catalog-probe", action="store_true")
        for name in ("challenge-fd", "reply-fd", "listener-fd"):
            parser.add_argument("--" + name, type=int)
        for name in ("deadline-ns", "port"):
            parser.add_argument("--" + name, type=int, required=True)
        for name in ("host", "path", "mode"):
            parser.add_argument("--" + name, required=True)
        args = parser.parse_args(argv)
        if args.catalog_probe:
            if any(getattr(args, name) is not None for name in ("challenge_fd", "reply_fd", "listener_fd")):
                _fail("invalid_child_arguments")
            _catalog_child(args)
        else:
            _child(args)
        return 0
    except ManagedStartupError as exc:
        return _CHILD_CODES[exc.cleanup_code or exc.code]
    except KeyboardInterrupt:
        return _CHILD_CODES["startup_cancelled"]
    except BaseException:
        # Only an explicit return after the selected entry completed may mean
        # success. SystemExit(0) from a dependency or cleanup is not completion.
        return _CHILD_CODES["child_startup_failed"]


if __name__ == "__main__":
    raise SystemExit(main())
