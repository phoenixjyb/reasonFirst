"""Managed adapter boundaries; native prepared-artifact success lives in its harness.

Fault actors intentionally replace only the Popen entry in these supervisor unit
tests. The production adapter has no arbitrary-actor or source-mode escape hatch.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import errno
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from gitlab_agent.bridge_http import HTTPLaunch
from gitlab_agent.upgrade import startup_managed as m
from gitlab_agent.upgrade.startup_state import create_disposable_configuration


FALSE_FLAGS = ("peer_identity_verified", "running_code_verified",
    "effective_configuration_verified", "managed_startup_confirmation_verified",
    "compatibility_verified", "activation_authorized", "ready_for_activation",
    "service_changed", "descendant_containment_verified")


class ManagedBoundaryTests(unittest.TestCase):
    def launch(self):
        return HTTPLaunch("127.0.0.1", 55123, "/fixture", "read-only", "disabled")

    def test_unsupported_platform_precedes_all_side_effects(self):
        for system in ("Windows", "Other"):
            with patch.object(m.platform, "system", return_value=system), \
                 patch.object(m.runtime, "status", side_effect=AssertionError), \
                 patch.object(m.socket, "socket", side_effect=AssertionError), \
                 patch.object(m.tempfile, "TemporaryDirectory", side_effect=AssertionError), \
                 patch.object(m.subprocess, "Popen", side_effect=AssertionError):
                report = m.probe_disposable(runtime_id="a" * 64, home="unread", launch=self.launch())
            self.assertEqual(report["error_code"], "unsupported_managed_startup_platform")
            self.assertFalse(report["ok"])
            self.assertTrue(report["cleanup_confirmed"])
            for name in FALSE_FLAGS:
                self.assertIs(report[name], False)

    def test_invalid_launch_and_cancel_do_not_select_a_runtime(self):
        cancelled = threading.Event(); cancelled.set()
        with patch.object(m, "_supported"), patch.object(m, "_select_runtime", side_effect=AssertionError):
            bad = m.probe_disposable(runtime_id="a" * 64, home="unread", launch=None)
            cancelled_report = m.probe_disposable(runtime_id="a" * 64, home="unread",
                                                launch=self.launch(), cancel=cancelled)
            invalid = m.probe_disposable(runtime_id="a" * 64, home="unread",
                                        launch=self.launch(), cancel="invalid")
        self.assertEqual(bad["error_code"], "invalid_launch")
        self.assertEqual(cancelled_report["error_code"], "startup_cancelled")
        self.assertEqual(invalid["error_code"], "invalid_cancel")

    def test_sdk_guard_requires_the_inspected_versions(self):
        m._sdk_supported({"mcp": "2.3.0", "uvicorn": "0.54.0"})
        for values in ({}, {"mcp": "2.2.0", "uvicorn": "0.54.0"},
                       {"mcp": "2.3.0", "uvicorn": "0.55.0"}):
            with self.assertRaisesRegex(m.ManagedStartupError, "unsupported_listener_sdk"):
                m._sdk_supported(values)

    def test_supervisor_sdk_guard_precedes_runtime_and_socket_access(self):
        with patch.object(m, "_supported"), \
             patch.object(m.importlib.metadata, "version", return_value="unreviewed"), \
             patch.object(m, "_select_runtime", side_effect=AssertionError), \
             patch.object(m.socket, "socket", side_effect=AssertionError):
            result = m.probe_disposable(runtime_id="a" * 64, home="unread", launch=self.launch())
        self.assertEqual(result["error_code"], "unsupported_listener_sdk")

    def test_child_environment_never_copies_parent_credentials_or_loader_overrides(self):
        sentinel = {"PATH": "private/bin", "LD_LIBRARY_PATH": "private/lib",
                    "DYLD_INSERT_LIBRARIES": "private", "GITLAB_TOKEN": "synthetic-secret",
                    "HTTP_PROXY": "socks5://synthetic-secret", "PYTHONPATH": "private",
                    "SOME_OTHER_SECRET": "synthetic-secret"}
        with patch.dict(os.environ, sentinel, clear=True):
            before = dict(os.environ)
            child = m._child_environment({"HOME": "/disposable", "RF_BRIDGE_CONFIG": "/disposable/bridge"})
            self.assertEqual(dict(os.environ), before)
        self.assertEqual(child["PATH"], os.defpath)
        self.assertEqual(set(child) & set(sentinel), {"PATH"})
        self.assertNotIn("synthetic-secret", json.dumps(child))

    def test_failure_reports_and_internal_cli_never_echo_values(self):
        with patch.object(m, "_supported"), \
             patch.object(m, "_select_runtime", side_effect=RuntimeError("synthetic-secret")):
            report = m.probe_disposable(runtime_id="synthetic-secret", home="synthetic-secret", launch=self.launch())
            with patch.object(sys, "stderr", io.StringIO()) as stderr:
                result = m.main(["--child", "--secret", "synthetic-secret"])
        self.assertNotEqual(result, 0)
        self.assertNotIn("synthetic-secret", json.dumps(report) + stderr.getvalue())
        for name in FALSE_FLAGS:
            self.assertIs(report[name], False)

    def test_current_editable_interpreter_is_not_a_prepared_runtime_fallback(self):
        with patch.object(m.sys, "prefix", "/synthetic/source-venv"), \
             patch.object(m.Path, "resolve", return_value=Path("/synthetic/source-venv")), \
             patch.object(m, "_select_runtime", side_effect=AssertionError):
            with self.assertRaisesRegex(m.ManagedStartupError, "runtime_identity_mismatch"):
                m._actual_runtime()

    def test_internal_entries_cannot_turn_system_exit_zero_into_success(self):
        ordinary = ["--deadline-ns", str(time.monotonic_ns() + m.LIFETIME_NS),
                    "--host", "127.0.0.1", "--port", "55123", "--path", "/fixture",
                    "--mode", "read-only"]
        for flag, entry in (("--catalog-probe", "_catalog_child"), ("--child", "_child")):
            for failure in (SystemExit(0), BaseExceptionGroup("synthetic-secret", [SystemExit(0)])):
                with self.subTest(flag=flag, failure=type(failure).__name__), \
                     patch.object(m, "_supported"), patch.object(m, entry, side_effect=failure), \
                     patch.object(sys, "stderr", io.StringIO()) as stderr:
                    code = m.main([flag, *ordinary])
                self.assertEqual(code, m._CHILD_CODES["child_startup_failed"])
                self.assertEqual(stderr.getvalue(), "")


ACTOR = r'''
import os,signal,socket,sys,time
from pathlib import Path
from gitlab_agent.bridge_http import HTTPLaunch
from gitlab_agent.config import AgentSettings
from gitlab_agent.bridge_preview.bridge_config import load_bridge_config
from gitlab_agent.upgrade.startup_state import capture_startup_state
from gitlab_agent.upgrade.startup_channel import read_frame,write_frame,mark_noninheritable
from gitlab_agent.upgrade.startup_protocol import StartupClaims,make_reply
kind=sys.argv[1]
def value(name):return sys.argv[sys.argv.index(name)+1]
rfd=int(value('--challenge-fd'));wfd=int(value('--reply-fd'));sfd=int(value('--listener-fd'))
deadline=int(value('--deadline-ns'))
mark_noninheritable(rfd,wfd);os.set_inheritable(sfd,False)
if kind=='exit':sys.exit(0)
if kind=='ignore-term':signal.signal(signal.SIGTERM,signal.SIG_IGN)
if kind=='listening':
    owned=socket.socket(fileno=sfd);owned.listen()
    signal.signal(signal.SIGTERM,lambda *args:sys.exit(0))
    signal.signal(signal.SIGUSR1,lambda *args:sys.exit(0))
challenge=read_frame(rfd,deadline_ns=deadline);os.close(rfd)
launch=HTTPLaunch(value('--host'),int(value('--port')),value('--path'),value('--mode'),'disabled')
snapshot=capture_startup_state(AgentSettings.load(),load_bridge_config(),
    bridge_config_path=Path(os.environ['RF_BRIDGE_CONFIG']),state_dir=Path(os.environ['RF_CODEX_BRIDGE_STATE_DIR']))
claims=StartupClaims('a'*64,'b'*64,'c'*64,snapshot.configuration_digest(launch),
    launch.host,launch.port,launch.path,launch.mode,launch.control_policy)
def reply():
    write_frame(wfd,make_reply(challenge,observed=claims),deadline_ns=deadline);os.close(wfd)
if kind=='relay':
    child=os.fork()
    if child==0:
        reply();os._exit(0)
    os.close(wfd);os.waitpid(child,0)
else:reply()
time.sleep(10)
'''


@unittest.skipUnless(os.name == "posix" and sys.platform in ("linux", "darwin"),
                     "managed socket/pipe adapter is POSIX only")
class ManagedSocketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rf-managed-unit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.selected = m._RuntimeSelection("a" * 64, self.root, self.root,
            Path(sys.prefix), Path(sys.executable), "b" * 64, {}, "c" * 64)
        self.real_popen = subprocess.Popen

    def launch(self, port=None):
        if port is None:
            with socket.socket() as reserve:
                reserve.bind(("127.0.0.1", 0));port = reserve.getsockname()[1]
        return HTTPLaunch("127.0.0.1", port, "/fixture", "read-only", "disabled")

    def run_actor(self, kind, *, delay=0, shorten_cleanup=False, probe=None, cancel=None,
                  cancel_at_final_status=False, exit_at_cleanup=False, interrupt_cleanup=False):
        spawned = []
        armed = [False]
        polls = [0]
        selections = [0]
        def start(argv, **kwargs):
            if delay:time.sleep(delay)
            proc = self.real_popen([sys.executable, "-I", "-B", "-c", ACTOR, kind, *argv[5:]], **kwargs)
            spawned.append(proc)
            if shorten_cleanup:
                wait = proc.wait
                proc.wait = lambda timeout=None: wait(timeout=0.1 if timeout == 4 else timeout)
            if interrupt_cleanup:
                wait = proc.wait
                interrupted = [False]
                def interrupt_once(timeout=None):
                    if timeout == 4 and not interrupted[0]:
                        interrupted[0] = True
                        raise KeyboardInterrupt
                    return wait(timeout=timeout)
                proc.wait = interrupt_once
            if exit_at_cleanup:
                actual_poll = proc.poll
                def observed_poll():
                    if armed[0]:
                        polls[0] += 1
                        if polls[0] == 2:
                            proc.send_signal(__import__('signal').SIGUSR1)
                            proc.wait(timeout=2)
                    return actual_poll()
                proc.poll = observed_poll
            return proc
        def catalog(*args, **kwargs):
            if probe is None:raise AssertionError("No catalog before listener proof")
            result = probe(*args, **kwargs)
            armed[0] = True
            return result
        def select_runtime(*args, **kwargs):
            selections[0] += 1
            if cancel_at_final_status and selections[0] == 2:cancel.set()
            return self.selected
        with patch.object(m, "_select_runtime", side_effect=select_runtime), \
             patch.object(m.subprocess, "Popen", side_effect=start), \
             patch.object(m, "_probe_catalog", side_effect=catalog):
            report = m.probe_disposable(runtime_id="a" * 64, home=self.root, launch=self.launch(), cancel=cancel)
        self.assertEqual(len(spawned), 1)
        self.assertIsNotNone(spawned[0].poll())
        self.assertTrue(report["child_reaped"])
        self.assertFalse(report["ok"])
        for flag in FALSE_FLAGS:
            self.assertFalse(report[flag])
        return report

    def test_parent_retains_nonlistening_exclusive_socket(self):
        launch = self.launch()
        with m._exclusive_socket(launch) as owned:
            m._listener_observation(owned, launch, listening=False)
            self.assertFalse(owned.get_inheritable())
            with self.assertRaisesRegex(m.ManagedStartupError, "listener_not_started"):
                m._listener_observation(owned, launch, listening=True)
            with socket.socket() as contender:
                with self.assertRaises(OSError) as error:contender.bind(owned.getsockname())
                self.assertEqual(error.exception.errno, errno.EADDRINUSE)

    def test_matching_early_reply_cannot_replace_listener_evidence(self):
        report = self.run_actor("early")
        self.assertTrue(report["fresh_reply_claims_match"])
        self.assertTrue(report["pid_claim_matches"])
        self.assertEqual(report["error_code"], "listener_not_started")
        self.assertFalse(report["endpoint_catalog_verified"])
        self.assertTrue(report["cleanup_confirmed"])

    def test_real_relay_pid_is_rejected_by_owned_process_expectation(self):
        report = self.run_actor("relay")
        self.assertEqual(report["error_code"], "startup_reply_failed")
        self.assertFalse(report["fresh_reply_claims_match"])
        self.assertTrue(report["cleanup_confirmed"])

    def test_child_exit_cannot_be_accepted(self):
        report = self.run_actor("exit")
        self.assertIn(report["error_code"], ("child_exited", "startup_reply_failed"))
        self.assertFalse(report["fresh_reply_claims_match"])

    def test_transport_deadline_starts_before_popen_and_is_not_renewed(self):
        with patch.object(m, "LIFETIME_NS", 20_000_000):
            report = self.run_actor("early", delay=0.05)
        self.assertEqual(report["error_code"], "startup_timeout")

    def test_forced_owned_child_termination_preserves_original_failure(self):
        report = self.run_actor("ignore-term", shorten_cleanup=True)
        self.assertEqual(report["error_code"], "listener_not_started")
        self.assertEqual(report["cleanup_error_code"], "child_forced_termination")
        self.assertFalse(report["cleanup_confirmed"])

    def test_cancellation_after_catalog_or_during_final_revalidation_refuses_success(self):
        for when in ("catalog", "final-status"):
            cancel = threading.Event()
            def catalog(*args, **kwargs):
                if when == "catalog":cancel.set()
                return {"catalog_helper_reaped": True, "catalog_helper_cleanup_confirmed": True}
            report = self.run_actor("listening", probe=catalog, cancel=cancel,
                                    cancel_at_final_status=when == "final-status")
            self.assertEqual(report["error_code"], "startup_cancelled")

    def test_missing_false_or_additional_helper_evidence_cannot_change_report_or_succeed(self):
        valid = {"catalog_helper_reaped": True, "catalog_helper_cleanup_confirmed": True}
        for evidence in ({}, {"catalog_helper_reaped": True},
                         dict(valid, catalog_helper_reaped=False),
                         dict(valid, catalog_helper_cleanup_confirmed=False),
                         dict(valid, catalog_helper_reaped=1),
                         dict(valid, activation_authorized=True)):
            with self.subTest(evidence=evidence):
                report = self.run_actor("listening", probe=lambda *args, **kwargs: evidence)
            self.assertEqual(report["error_code"], "catalog_probe_failed")
            self.assertFalse(report["endpoint_catalog_verified"])
            self.assertFalse(report["catalog_helper_reaped"])
            self.assertFalse(report["catalog_helper_cleanup_confirmed"])
            self.assertEqual(set(report), set(m._base_report()))

    def test_cancellation_during_runtime_selection_prevents_spawn(self):
        cancel = threading.Event()
        def selected(*args, **kwargs):cancel.set();return self.selected
        with patch.object(m, "_select_runtime", side_effect=selected), \
             patch.object(m.subprocess, "Popen", side_effect=AssertionError), \
             patch.object(m.socket, "socket", side_effect=AssertionError):
            result = m.probe_disposable(runtime_id="a" * 64, home=self.root,
                                       launch=HTTPLaunch("127.0.0.1", 55123, "/fixture", "read-only", "disabled"), cancel=cancel)
        self.assertEqual(result["error_code"], "startup_cancelled")
        self.assertFalse(result["private_channel_owned"])

    def test_unrequested_clean_child_exit_at_cleanup_cannot_be_success(self):
        report = self.run_actor("listening", exit_at_cleanup=True,
            probe=lambda *args, **kwargs: {"catalog_helper_reaped": True, "catalog_helper_cleanup_confirmed": True})
        self.assertEqual(report["error_code"], "child_exited")
        self.assertTrue(report["child_reaped"])

    def test_keyboard_interrupt_during_cleanup_reaps_owned_child_and_cannot_succeed(self):
        report = self.run_actor("listening", interrupt_cleanup=True,
            probe=lambda *args, **kwargs: {"catalog_helper_reaped": True, "catalog_helper_cleanup_confirmed": True})
        self.assertEqual(report["error_code"], "startup_cancelled")
        self.assertEqual(report["cleanup_error_code"], "child_cleanup_failed")
        self.assertTrue(report["child_reaped"])

    def test_live_health_decoy_is_preserved_and_never_probed_as_the_child(self):
        class Decoy(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{"ok":true,"service":"reasonfirst","read_only_mode":true}'
                self.send_response(200);self.send_header("Content-Length", str(len(body)));self.end_headers();self.wfile.write(body)
            def log_message(self, *args):pass
        server = HTTPServer(("127.0.0.1", 0), Decoy)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        try:
            with patch.object(m, "_select_runtime", return_value=self.selected), \
                 patch.object(m.subprocess, "Popen", side_effect=AssertionError), \
                 patch.object(m, "_probe_catalog", side_effect=AssertionError):
                report = m.probe_disposable(runtime_id="a" * 64, home=self.root,
                                           launch=self.launch(server.server_port))
            self.assertEqual(report["error_code"], "listener_bind_failed")
            self.assertTrue(report["cleanup_confirmed"])
            self.assertFalse(report["private_channel_owned"])
            with socket.create_connection(("127.0.0.1", server.server_port), timeout=1) as client:
                client.sendall(b"GET /healthz HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
                self.assertIn(b"200", client.recv(256))
        finally:
            server.shutdown();thread.join(timeout=2);server.server_close()

    def test_actual_asyncio_server_is_required_in_addition_to_a_listening_socket(self):
        launch = self.launch()
        with m._exclusive_socket(launch) as owned:
            owned.listen()
            class NotStarted:
                started = False
                should_exit = False
                servers = ()
            with self.assertRaisesRegex(m.ManagedStartupError, "listener_not_started"):
                m._observe_actual_server(NotStarted(), owned, launch)

    def test_env_file_cannot_introduce_a_launch_override_before_controller_creation(self):
        from gitlab_agent.bridge_preview import bridge_config
        for key, value in (("RF_MCP_READ_ONLY", "false"), ("PYTHONPATH", "/unreviewed"),
                           ("RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH", "true")):
            configuration = create_disposable_configuration(self.root / key)
            with configuration.snapshot.settings.config_file.open("a", encoding="utf-8") as stream:
                stream.write(f"{key}={value}\n")
            environment = m._child_environment(configuration.environment)
            environment.pop(key, None)  # Exercise a newly introduced field, not process precedence.
            with patch.dict(os.environ, environment, clear=True), \
                 patch.object(bridge_config, "load_bridge_config", side_effect=AssertionError):
                with self.assertRaises(Exception) as failure:
                    m._load_actual_state(self.launch())
            self.assertIsInstance(failure.exception, m.HTTPLaunchError)

    def test_manifest_read_is_rechecked_and_interpreter_drift_is_refused(self):
        base = {"resolved_file": "/base/python", "sha256": "a" * 64,
                "invocation_directory": "/base", "venv_config_sha256": None}
        record = {"runtime_id": "a" * 64, "input": {"python": {"fingerprint": base}},
                  "observation": {"packages": dict(m.SUPPORTED_LISTENER_SDK),
                                  "python_version": [3, 12, 10]}}
        signature = m.runtime.digest(record)
        record["manifest_digest"] = signature
        status = {"prepared": True, "runtime_status": "prepared_matches_record",
                  "runtime_path": str(self.root.joinpath(*m.runtime.PARTS, "a" * 64)),
                  "manifest_digest": signature, "observed": record["observation"]}
        @contextmanager
        def directory(*args, **kwargs):yield 100
        changed = dict(record, manifest_digest="d" * 64)
        with patch.object(m.runtime, "status", return_value=status), \
             patch.object(m.runtime.storage, "_directory", side_effect=directory), \
             patch.object(m.runtime.storage, "_read_at", return_value=m.runtime.canonical(changed)):
            with self.assertRaisesRegex(m.ManagedStartupError, "runtime_record_mismatch"):
                m._select_runtime("a" * 64, self.root)
        wrong = dict(base, sha256="e" * 64, venv_config_sha256="f" * 64)
        with patch.object(m.runtime, "status", return_value=status), \
             patch.object(m.runtime.storage, "_directory", side_effect=directory), \
             patch.object(m.runtime.storage, "_read_at", return_value=m.runtime.canonical(record)), \
             patch.object(m, "executable_fingerprint", return_value=wrong):
            with self.assertRaisesRegex(m.ManagedStartupError, "interpreter_identity_mismatch"):
                m._select_runtime("a" * 64, self.root)

    def test_actual_imports_outside_selected_prefix_cannot_be_accepted(self):
        import gitlab_agent
        venv = self.root.joinpath(*m.runtime.PARTS, "a" * 64, "venv")
        (venv / "bin").mkdir(parents=True)
        executable = venv / "bin/python"
        base = m.executable_fingerprint(sys._base_executable)
        fp = dict(base, invocation_directory=str(venv / "bin"), venv_config_sha256="d" * 64)
        digest = m.runtime.digest(m._interpreter_projection(executable, fp, base, sys.version_info[:3], venv))
        packages = {m.runtime.norm(dist.metadata["Name"]): dist.version for dist in m.importlib.metadata.distributions()}
        record = {"input": {"python": {"fingerprint": base}},
                  "observation": {"packages": packages, "version": gitlab_agent.__version__, "origins": []}}
        selected = m._RuntimeSelection("a" * 64, self.root, venv.parent, venv, executable,
                                       "b" * 64, record, digest)
        def fingerprint(path):return fp if path == str(executable) else base
        with patch.object(m.sys, "prefix", str(venv)), patch.object(m.sys, "executable", str(executable)), \
             patch.object(m, "_select_runtime", return_value=selected), \
             patch.object(m, "executable_fingerprint", side_effect=fingerprint):
            with self.assertRaisesRegex(m.ManagedStartupError, "runtime_import_mismatch"):
                m._actual_runtime()

    def test_parent_controller_cleanup_failure_remains_separate_from_catalog_failure(self):
        from gitlab_agent.bridge_preview.controller import BridgeController
        configuration = create_disposable_configuration(self.root / "cleanup")
        with patch.object(BridgeController, "close", side_effect=RuntimeError("synthetic-secret")):
            with self.assertRaises(m.ManagedStartupError) as failure:
                asyncio.run(m._probe_catalog_async(self.launch(), configuration.snapshot,
                    deadline_ns=time.monotonic_ns() + m.CATALOG_LIFETIME_NS,
                    cancel=None, is_alive=lambda: False))
        self.assertEqual(failure.exception.code, "child_exited")
        self.assertEqual(failure.exception.cleanup_code, "parent_controller_cleanup_failed")
        self.assertNotIn("synthetic-secret", str(failure.exception))

    def test_controller_system_exit_zero_is_a_cleanup_failure(self):
        from gitlab_agent.bridge_preview.controller import BridgeController
        configuration = create_disposable_configuration(self.root / "exit-cleanup")
        with patch.object(BridgeController, "close", side_effect=SystemExit(0)):
            with self.assertRaises(m.ManagedStartupError) as failure:
                asyncio.run(m._probe_catalog_async(self.launch(), configuration.snapshot,
                    deadline_ns=time.monotonic_ns() + m.CATALOG_LIFETIME_NS,
                    cancel=None, is_alive=lambda: False))
        self.assertEqual(failure.exception.code, "child_exited")
        self.assertEqual(failure.exception.cleanup_code, "parent_controller_cleanup_failed")

    def test_catalog_redirect_and_oversize_json_fail_without_global_environment_changes(self):
        for behavior in ("redirect", "oversize"):
            requests = []
            class InvalidPeer(BaseHTTPRequestHandler):
                def do_POST(self):
                    requests.append(self.path)
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    if behavior == "redirect":
                        self.send_response(307);self.send_header("Location", "/unexpected");body = b""
                    else:
                        self.send_response(200);body = b'{"synthetic-secret":"' + b"x" * 8192 + b'"}'
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)));self.end_headers()
                    try:self.wfile.write(body)
                    except (BrokenPipeError, ConnectionResetError):pass
                def log_message(self, *args):pass
            server = HTTPServer(("127.0.0.1", 0), InvalidPeer)
            thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
            configuration = create_disposable_configuration(self.root / behavior)
            try:
                before = dict(os.environ)
                with patch.object(m, "CATALOG_MAX_BYTES", 1024):
                    with self.assertRaises(Exception) as failure:
                        asyncio.run(m._probe_catalog_async(self.launch(server.server_port), configuration.snapshot,
                            deadline_ns=time.monotonic_ns() + m.CATALOG_LIFETIME_NS,
                            cancel=None, is_alive=lambda: True))
                def leaves(error):
                    nested = getattr(error, "exceptions", None)
                    return [item for child in nested for item in leaves(child)] if nested else [error]
                expected = "catalog_unexpected_endpoint" if behavior == "redirect" else "catalog_reply_limit"
                self.assertTrue(any(isinstance(error, m.ManagedStartupError) and error.code == expected
                                    for error in leaves(failure.exception)))
                self.assertEqual(requests, ["/fixture"])
                self.assertNotIn("synthetic-secret", str(failure.exception))
                self.assertEqual(dict(os.environ), before)
            finally:
                server.shutdown();thread.join(timeout=2);server.server_close()

    def test_catalog_helper_is_owned_quiet_and_inherits_no_startup_or_unrelated_descriptor(self):
        configuration = create_disposable_configuration(self.root / "helper")
        pipes = (*os.pipe(), *os.pipe(), *os.pipe())
        owned_socket = m._exclusive_socket(self.launch())
        inherited = []
        for fd in (*pipes, owned_socket.fileno()):
            os.set_inheritable(fd, True)
            observed = os.fstat(fd)
            inherited.append((fd, observed.st_dev, observed.st_ino))
        code = r'''
import json,os,sys
for fd,dev,ino in json.loads(sys.argv[1]):
    try:
        info=os.fstat(fd)
        assert (info.st_dev,info.st_ino)!=(dev,ino), 'owned descriptor inherited'
    except OSError:pass
print('synthetic-secret from stdout')
print('synthetic-secret from stderr',file=sys.stderr)
'''
        spawned = []
        def launch(argv, **kwargs):
            self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            self.assertIs(kwargs["close_fds"], True)
            self.assertNotIn("pass_fds", kwargs)
            child = self.real_popen([sys.executable, "-I", "-B", "-c", code,
                                    json.dumps(inherited)], **kwargs)
            spawned.append(child)
            return child
        try:
            with patch.object(m.subprocess, "Popen", side_effect=launch):
                result = m._probe_catalog(self.launch(), configuration, cancel=None, is_alive=lambda: True)
            self.assertEqual(result, {"catalog_helper_reaped": True, "catalog_helper_cleanup_confirmed": True})
            self.assertIsNotNone(spawned[0].poll())
            self.assertNotIn("synthetic-secret", json.dumps(result))
        finally:
            for fd in pipes:os.close(fd)
            owned_socket.close()

    def _failed_catalog_actor(self, action, *, ignores_term=False, interrupt_wait=False):
        configuration = create_disposable_configuration(self.root / (action + str(ignores_term) + str(interrupt_wait)))
        ready = configuration.root / "test-helper-ready"
        cancel = threading.Event()
        spawned = []
        terminations = []
        polls = [0]
        code = r'''
import signal,sys,time
from pathlib import Path
if sys.argv[1]=='cleanup-error':sys.exit(int(sys.argv[3]))
signal.signal(signal.SIGTERM, signal.SIG_IGN if sys.argv[1]=='ignore' else lambda *args:sys.exit(0))
Path(sys.argv[2]).write_text('ready',encoding='ascii')
while True:time.sleep(10)
'''
        def launch(argv, **kwargs):
            child = self.real_popen([sys.executable, "-I", "-B", "-c", code,
                "cleanup-error" if action == "cleanup-error" else "ignore" if ignores_term else "graceful",
                str(ready), str(m._CHILD_CODES["parent_controller_cleanup_failed"])], **kwargs)
            spawned.append(child)
            actual_wait = child.wait
            actual_terminate = child.terminate
            interrupted = [False]
            def wait(timeout=None):
                if timeout == 2 and interrupt_wait and not interrupted[0]:
                    interrupted[0] = True
                    raise KeyboardInterrupt
                return actual_wait(timeout=0.15 if timeout == 2 else timeout)
            def terminate():
                terminations.append(child.pid)
                actual_terminate()
            child.wait = wait
            child.terminate = terminate
            limit = time.monotonic() + 2
            if action != "cleanup-error":
                while not ready.exists() and time.monotonic() < limit and child.poll() is None:
                    time.sleep(0.005)
                self.assertTrue(ready.exists())
            if action == "cancel":cancel.set()
            return child
        def alive():
            polls[0] += 1
            if action == "interrupt" and polls[0] == 2:raise KeyboardInterrupt
            return True
        try:
            with patch.object(m.subprocess, "Popen", side_effect=launch), \
                 patch.object(m, "CATALOG_LIFETIME_NS", 250_000_000):
                with self.assertRaises(m.ManagedStartupError) as failure:
                    m._probe_catalog(self.launch(), configuration, cancel=cancel, is_alive=alive)
            self.assertEqual(len(spawned), 1)
            self.assertIsNotNone(spawned[0].poll())
            if action != "cleanup-error":self.assertEqual(terminations, [spawned[0].pid])
            return failure.exception
        finally:
            for child in spawned:
                if child.poll() is None:child.kill()
                child.wait(timeout=2)

    def test_catalog_helper_deadline_and_cancellation_reap_the_owned_helper(self):
        for action, expected in (("timeout", "startup_timeout"), ("cancel", "startup_cancelled")):
            with self.subTest(action=action):
                failure = self._failed_catalog_actor(action)
            self.assertEqual(failure.code, expected)
            self.assertIsNone(failure.cleanup_code)

    def test_catalog_helper_forced_cleanup_preserves_timeout_or_keyboard_cancellation(self):
        for action, expected in (("timeout", "startup_timeout"), ("interrupt", "startup_cancelled")):
            with self.subTest(action=action):
                failure = self._failed_catalog_actor(action, ignores_term=True)
            self.assertEqual(failure.code, expected)
            self.assertEqual(failure.cleanup_code, "catalog_helper_forced_termination")

    def test_catalog_helper_wait_interrupt_reaps_owned_child_and_preserves_primary_error(self):
        failure = self._failed_catalog_actor("timeout", ignores_term=True, interrupt_wait=True)
        self.assertEqual(failure.code, "startup_timeout")
        self.assertEqual(failure.cleanup_code, "catalog_helper_cleanup_failed")

    def test_catalog_helper_coarse_primary_failure_preserves_controller_cleanup_code(self):
        failure = self._failed_catalog_actor("cleanup-error")
        self.assertEqual(failure.code, "catalog_probe_failed")
        self.assertEqual(failure.cleanup_code, "parent_controller_cleanup_failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
