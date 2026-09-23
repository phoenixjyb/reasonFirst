"""Actual helper, real local subprocesses/HTTP; no live tunnel or credentials."""
from __future__ import annotations

from dataclasses import replace
import json
import os
import queue
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, call, patch

import yaml
from gitlab_agent import tunnel_lifecycle as tunnel


FAKE = r'''#!/usr/bin/env python3
import argparse, json, os, signal, socket, socketserver, sys, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import yaml
parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["doctor", "run"])
parser.add_argument("--profile", required=True)
parser.add_argument("--profile-dir", required=True)
parser.add_argument("--explain", action="store_true")
args = parser.parse_args()
root = Path.cwd()
mode = json.loads((root / "fake-mode.json").read_text())
profile = yaml.safe_load((Path(args.profile_dir) / (args.profile + ".yaml")).read_text())
cp = profile["control_plane"]
with (root / "events.jsonl").open("a") as stream:
    stream.write(json.dumps({"action": args.action, "pid": os.getpid(),
        "runtime_key_received": os.environ.get("CONTROL_PLANE_API_KEY") == "fixture-runtime",
        "override_removed": "CONTROL_PLANE_BASE_URL" not in os.environ,
        "gitlab_override_removed": "GITLAB_BASE_URL" not in os.environ,
        "env_file": os.environ.get("GITLAB_AGENT_ENV_FILE"),
        "argv": sys.argv[1:]}) + "\n")
print("provider-body-canary-do-not-export", flush=True)
print("provider-stderr-canary-do-not-export", file=sys.stderr, flush=True)
if args.action == "doctor":
    if mode.get("doctor_change_profile"):
        path = Path(args.profile_dir) / (args.profile + ".yaml")
        path.write_text(path.read_text() + "\n# changed during doctor\n")
    time.sleep(mode.get("doctor_delay", 0))
    sys.exit(mode.get("doctor_exit", 0))
if "run_exit" in mode:
    sys.exit(mode["run_exit"])
if mode.get("ignore_term"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if mode.get("stubborn_descendant"):
    def leader_term(signum, frame):
        (root / "leader-terminated").touch()
        os._exit(0)
    signal.signal(signal.SIGTERM, leader_term)
    ready_r, ready_w = os.pipe()
    if os.fork() == 0:
        os.close(ready_r)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(30)
            conn.connect(str(root / "descendant.sock"))
            conn.sendall(b"ready")
            os.write(ready_w, b"1")
            os.close(ready_w)
            # A live socket (not kill(pid, 0)) distinguishes a surviving process
            # from an orphan zombie. Closing it also cleans up a failing test.
            try: conn.recv(1)
            except OSError: pass
        os._exit(0)
    os.close(ready_w)
    if os.read(ready_r, 1) != b"1":
        sys.exit(9)
    os.close(ready_r)
started = time.monotonic()
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        if mode.get("slow_http"):
            time.sleep(10)
        status = 200
        body = b"{}"
        if self.path == "/readyz":
            status = mode.get("ready_status", 200)
        elif self.path == "/api/status":
            info = {"control_plane_base_url": cp["base_url"],
                    "control_plane_tunnel_id": cp["tunnel_id"],
                    "tunnel_metadata": {"id": cp["tunnel_id"]}}
            if mode.get("wrong_identity"): info["control_plane_tunnel_id"] = "tunnel_other"
            if mode.get("metadata_error"): info["tunnel_metadata_error"] = "provider-body-canary-do-not-export"
            if mode.get("metadata_pending"): info.pop("tunnel_metadata")
            if mode.get("malformed_base"): info["control_plane_base_url"] = ["provider-body-canary-do-not-export"]
            body = json.dumps(info).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try: self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError): pass
# HTTPServer.server_bind calls getfqdn even for a numeric loopback address.
# That can stall for >30s on hosted macOS (actions/setup-python#1223).
# This fixture needs local TCP/HTTP, not DNS or the host's resolver state.
class LoopbackHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]
if mode.get("slow_reverse_dns"):
    def slow_getfqdn(host):
        (root / "resolver-called").touch()
        time.sleep(20)
        return host
    socket.getfqdn = slow_getfqdn
host, port = profile["health"]["listen_addr"].rsplit(":", 1)
(root / "server-phase.json").write_text(json.dumps({"phase": "binding"}))
with LoopbackHTTPServer((host, int(port)), Handler) as server:
    (root / "server-phase.json").write_text(json.dumps({"phase": "serving"}))
    server.serve_forever()
'''


@unittest.skipUnless(os.name == "posix", "Owned lifecycle uses POSIX groups, flock and Unix sockets")
class TunnelFixture(unittest.TestCase):
    def setUp(self):
        # /tmp is deliberately short enough for macOS sockaddr_un.
        self.temp = tempfile.TemporaryDirectory(prefix="rf-tun-", dir="/tmp")
        self.root = Path(self.temp.name)
        self.source = self.root / "source with spaces"
        self.source.mkdir()
        (self.source / "run_mcp.sh").write_text("#!/bin/bash\nexit 0\n")
        (self.source / "server.py").write_text("# synthetic source\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "tunnel-client").write_text(FAKE)
        (self.bin / "tunnel-client").chmod(0o700)
        (self.bin / "uv").write_text("#!/bin/sh\nexit 0\n")
        (self.bin / "uv").chmod(0o700)
        self.profiles = self.root / "profiles"
        self.profiles.mkdir()
        self.config = self.root / "settings"
        self.config.mkdir(mode=0o700)
        self.envfile = self.root / "private.env"
        self.envfile.write_text("# fixture only, no secrets\n")
        self.envfile.chmod(0o600)
        with socket.socket() as bound:
            bound.bind(("127.0.0.1", 0))
            self.port = bound.getsockname()[1]
        self.profile = {
            "config_version": 1,
            "control_plane": {"base_url": "https://api.openai.com", "tunnel_id": "tunnel_" + "a" * 32,
                              "api_key": "env:CONTROL_PLANE_API_KEY"},
            "health": {"listen_addr": f"127.0.0.1:{self.port}"},
            "admin_ui": {"open_browser": False}, "log": {"level": "info", "format": "json"},
            "mcp": {"commands": [{"channel": "main", "command": f'bash "{self.source}/run_mcp.sh"'}]},
        }
        self.profilepath = self.profiles / "lab.yaml"
        self.write_profile()
        self.settings = tunnel.Settings("lab", str(self.profiles), str(self.source), str(self.envfile),
                                        str(self.root / "state"), str(self.bin / "tunnel-client"))
        self.settingspath = self.config / "tunnel.json"
        self.save_settings()
        self.mode()
        self.children = []
        self.env = {"PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
                    "HOME": str(self.root), "LANG": "C.UTF-8",
                    "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                    "CONTROL_PLANE_API_KEY": "fixture-runtime"}

    def tearDown(self):
        for process in self.children:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if process.stdout: process.stdout.close()
            if process.stderr: process.stderr.close()
        self.temp.cleanup()

    def save_settings(self):
        self.settingspath.write_text(json.dumps(tunnel.asdict(self.settings)))
        self.settingspath.chmod(0o600)

    def write_profile(self):
        self.profilepath.write_text(yaml.safe_dump(self.profile))
        self.profilepath.chmod(0o600)

    def mode(self, **values):
        (self.source / "fake-mode.json").write_text(json.dumps(values))

    def command(self, action, *extra):
        return [sys.executable, "-m", "gitlab_agent.tunnel_lifecycle", action,
                "--settings", str(self.settingspath), *extra]

    def call(self, action, *extra, env=None):
        completed = subprocess.run(self.command(action, *extra), env=env or self.env,
                                   capture_output=True, text=True, timeout=20)
        self.assertNotIn("fixture-runtime", completed.stdout + completed.stderr)
        self.assertNotIn("provider-body-canary", completed.stdout + completed.stderr)
        self.assertNotIn("provider-stderr-canary", completed.stdout + completed.stderr)
        lines = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertTrue(lines, completed.stderr)
        return completed, lines[-1]

    def begin(self, *extra):
        process = subprocess.Popen(self.command("start", *extra), env=self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(process)
        return process

    def await_ready(self, process):
        deadline = time.monotonic() + 10
        last = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail("Owner exited: " + process.stdout.read() + process.stderr.read())
            last = tunnel.control(self.settings)
            if last and last.get("ok"):
                return last
            time.sleep(0.1)
        # Fixture-only phase/boolean evidence, not suppressed provider stderr.
        phase_path = self.source / "server-phase.json"
        phase = json.loads(phase_path.read_text()) if phase_path.exists() else {"phase": "not_bound"}
        self.fail(f"Owner never ready: {last}; synthetic server: {phase}; events: {self.events()}")

    def events(self):
        path = self.source / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_loopback_fixture_avoids_reverse_dns_delay(self):
        self.mode(slow_reverse_dns=True)
        process = self.begin()
        ready = self.await_ready(process)
        self.assertEqual(ready["health_http"], 200)
        self.assertEqual(ready["ready_http"], 200)
        self.assertEqual(json.loads((self.source / "server-phase.json").read_text())["phase"], "serving")
        self.assertFalse((self.source / "resolver-called").exists())
        self.call("stop")
        self.assertEqual(process.wait(timeout=10), 0)

    def test_existing_profile_validates_without_rewriting(self):
        before = self.profilepath.read_bytes()
        profile = tunnel.read_profile(self.settings)
        self.assertEqual(profile.port, self.port)
        self.assertEqual(self.profilepath.read_bytes(), before)

    def test_old_launcher_is_rejected_before_credentials_or_process(self):
        self.profile["mcp"]["commands"][0]["command"] = "bash /obsolete/run_mcp.sh"
        self.write_profile()
        completed, result = self.call("start")
        self.assertEqual(result["error"]["code"], "launcher_mismatch")
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(self.events(), [])

    def test_unsafe_endpoint_or_literal_key_is_rejected(self):
        for field, value, expected in (("base_url", "https://other.invalid", "control_plane_unsupported"),
                                       ("api_key", "fixture-secret-literal", "literal_key_forbidden")):
            with self.subTest(field=field):
                original = self.profile["control_plane"][field]
                self.profile["control_plane"][field] = value
                self.write_profile()
                _, result = self.call("start")
                self.assertEqual(result["error"]["code"], expected)
                self.assertNotIn(value, json.dumps(result))
                self.profile["control_plane"][field] = original
        self.assertEqual(self.events(), [])

    def test_nonloopback_and_dynamic_ports_fail_closed(self):
        for address in ("0.0.0.0:8080", "127.0.0.1:0", "localhost:8080", "example.com:8080"):
            with self.subTest(address=address):
                self.profile["health"]["listen_addr"] = address
                self.write_profile()
                with self.assertRaises(tunnel.LifecycleError):
                    tunnel.read_profile(self.settings)

    def test_duplicate_yaml_and_anchors_are_rejected(self):
        for suffix in ("\nconfig_version: 1\n", "\nextra: &name {}\n"):
            self.write_profile()
            with self.profilepath.open("a") as stream: stream.write(suffix)
            with self.assertRaises(tunnel.LifecycleError): tunnel.read_profile(self.settings)

    def test_key_missing_in_new_session_is_explicit(self):
        env = dict(self.env)
        env.pop("CONTROL_PLANE_API_KEY")
        _, result = self.call("start", env=env)
        self.assertEqual(result["error"]["code"], "runtime_key_missing")
        self.assertEqual(self.events(), [])

    def test_keychain_is_explicit_and_failed_read_never_falls_back(self):
        settings = replace(self.settings, keychain_service="test-service", keychain_account="test-account")
        profile = tunnel.read_profile(settings)
        with patch.dict(os.environ, {"CONTROL_PLANE_API_KEY": "old-key"}), patch.object(tunnel.sys, "platform", "darwin"), patch.object(tunnel.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, b"fixture-runtime\n", b"secret-stderr")
            env = tunnel.runtime_environment(settings, profile)
            self.assertEqual(env["CONTROL_PLANE_API_KEY"], "fixture-runtime")
            self.assertEqual(run.call_args.args[0], ["/usr/bin/security", "find-generic-password", "-a", "test-account", "-s", "test-service", "-w"])
            run.return_value = subprocess.CompletedProcess([], 1, b"", b"secret-stderr")
            with self.assertRaises(tunnel.LifecycleError) as error: tunnel.runtime_environment(settings, profile)
            self.assertEqual(error.exception.code, "keychain_unavailable")
            self.assertNotIn("secret-stderr", str(error.exception))

    def test_file_key_reference_not_read_by_helper(self):
        keyfile = self.root / "runtime-key"
        keyfile.write_text("fixture-only")
        keyfile.chmod(0o600)
        self.profile["control_plane"]["api_key"] = "file:" + str(keyfile)
        self.write_profile()
        profile = tunnel.read_profile(self.settings)
        with patch.object(tunnel, "read_regular", side_effect=AssertionError("no credential read")), patch.object(tunnel.subprocess, "run", side_effect=AssertionError("no keychain")):
            env = tunnel.runtime_environment(self.settings, profile)
        self.assertNotIn("CONTROL_PLANE_API_KEY", env)

    def test_selected_config_overrides_stale_child_environment_without_mutation(self):
        profile = tunnel.read_profile(self.settings)
        with patch.dict(os.environ, {"GITLAB_BASE_URL": "http://stale.invalid", "GITLAB_ALLOWED_PROJECTS": "other/project",
                                    "CONTROL_PLANE_BASE_URL": "https://other.invalid", "MCP_COMMAND": "untrusted",
                                    "CONTROL_PLANE_API_KEY": "fixture-runtime"}):
            env = tunnel.runtime_environment(self.settings, profile)
            for name in ("GITLAB_BASE_URL", "GITLAB_ALLOWED_PROJECTS", "CONTROL_PLANE_BASE_URL", "MCP_COMMAND"):
                self.assertNotIn(name, env)
                self.assertIn(name, os.environ)
            self.assertEqual(env["GITLAB_AGENT_ENV_FILE"], str(self.envfile))

    def test_profile_symlink_and_credential_permissions_are_rejected(self):
        real = self.root / "real.yaml"
        self.profilepath.rename(real)
        self.profilepath.symlink_to(real)
        with self.assertRaises(OSError): tunnel.read_profile(self.settings)
        self.profilepath.unlink()
        real.rename(self.profilepath)
        self.envfile.chmod(0o644)
        _, result = self.call("start")
        self.assertEqual(result["error"]["code"], "unsafe_credential_file")

    def test_status_never_loads_key_or_creates_state(self):
        with patch.object(tunnel, "runtime_environment", side_effect=AssertionError("no key loading")):
            result = tunnel.status(self.settings)
        self.assertEqual(result["state"], "not_running")
        self.assertFalse(Path(self.settings.state_dir).exists())

    def test_foreground_lifecycle_and_second_start_are_idempotent(self):
        original_profile = self.profilepath.read_bytes()
        original_env = self.envfile.read_bytes()
        process = self.begin()
        ready = self.await_ready(process)
        self.assertEqual(ready["state"], "ready_for_chatgpt_check")
        self.assertFalse(ready["chatgpt_connection_verified"])
        self.assertFalse(ready["project_access_checked"])
        _, second = self.call("start")
        self.assertTrue(second["already_running"])
        self.assertEqual(second["instance_id"], ready["instance_id"])
        self.assertEqual([e["action"] for e in self.events()], ["doctor", "run"])
        env = dict(self.env)
        env.pop("CONTROL_PLANE_API_KEY")
        _, result = self.call("stop", env=env)
        self.assertEqual(result["state"], "stopped")
        self.assertEqual(process.wait(timeout=10), 0)
        _, stopped = self.call("status", env=env)
        self.assertEqual(stopped["state"], "not_running")
        self.assertEqual(self.profilepath.read_bytes(), original_profile)
        self.assertEqual(self.envfile.read_bytes(), original_env)
        for item in Path(self.settings.state_dir).iterdir():
            if item.is_file(): self.assertNotIn(b"fixture-runtime", item.read_bytes())

    def test_unmanaged_listener_is_never_adopted_or_stopped(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", self.port))
            listener.listen()
            _, result = self.call("start")
            self.assertEqual(result["error"]["code"], "unmanaged_listener")
            _, result = self.call("stop")
            self.assertEqual(result["error"]["code"], "not_owned")
            self.assertEqual(self.events(), [])
            self.assertEqual(listener.getsockname()[1], self.port)

    def test_control_socket_is_private_at_bind_before_chmod(self):
        path = self.root / "birth-private.sock"
        observed = {}
        real_chmod = os.chmod

        def inspect_before_chmod(target, mode):
            if Path(target) == path:
                observed["mode"] = path.lstat().st_mode & 0o777
            return real_chmod(target, mode)

        with patch.object(tunnel.os, "chmod", side_effect=inspect_before_chmod):
            listener = tunnel.bind_private_control_socket(path)
        try:
            self.assertIn("mode", observed)
            self.assertEqual(observed["mode"] & 0o077, 0)
            self.assertEqual(path.lstat().st_mode & 0o777, 0o600)
        finally:
            listener.close()
            path.unlink(missing_ok=True)

    def test_stale_socket_recovery_never_uses_pid_files(self):
        state = Path(self.settings.state_dir)
        state.mkdir(mode=0o700)
        with socket.socket(socket.AF_UNIX) as old: old.bind(str(self.settings.socket_path))
        os.chmod(self.settings.socket_path, 0o600)
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        self.children.append(unrelated)
        (state / "untrusted.pid").write_text(str(unrelated.pid))
        process = self.begin()
        self.await_ready(process)
        self.call("stop")
        process.wait(timeout=10)
        self.assertIsNone(unrelated.poll())

    def test_wrong_instance_stop_request_is_rejected(self):
        process = self.begin()
        ready = self.await_ready(process)
        response = tunnel.control(self.settings, "stop", "not-this-instance")
        self.assertEqual(response["state"], "request_rejected")
        self.assertEqual(tunnel.control(self.settings)["instance_id"], ready["instance_id"])
        self.assertIsNone(process.poll())
        self.call("stop")

    def test_same_tunnel_alias_cannot_start_second_owned_runtime(self):
        process = self.begin()
        self.await_ready(process)
        second = replace(self.settings, profile="second")
        self.profile["health"]["listen_addr"] = f"127.0.0.1:{self.port + 1 if self.port < 65535 else self.port - 1}"
        (self.profiles / "second.yaml").write_text(yaml.safe_dump(self.profile))
        (self.profiles / "second.yaml").chmod(0o600)
        original = self.settings
        self.settings = second
        self.save_settings()
        _, result = self.call("start")
        self.assertEqual(result["error"]["code"], "runtime_busy")
        self.assertEqual([e["action"] for e in self.events()], ["doctor", "run"])
        self.settings = original
        self.save_settings()
        self.call("stop")

    def test_doctor_failure_never_starts_runtime_and_hides_provider_output(self):
        self.mode(doctor_exit=2)
        _, result = self.call("start")
        self.assertEqual(result["error"]["code"], "doctor_failed")
        self.assertEqual([e["action"] for e in self.events()], ["doctor"])

    def test_profile_change_during_doctor_aborts_start(self):
        self.mode(doctor_change_profile=True)
        _, result = self.call("start")
        self.assertEqual(result["error"]["code"], "profile_changed")
        self.assertEqual([e["action"] for e in self.events()], ["doctor"])

    def test_doctor_failure_releases_owner_locks(self):
        self.mode(doctor_exit=1)
        self.call("start")
        self.mode()
        process = self.begin()
        self.await_ready(process)
        self.call("stop")

    def test_startup_not_ready_stops_its_owned_runtime(self):
        self.mode(ready_status=503)
        completed, result = self.call("start", "--startup-timeout", "1")
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(result["state"], "startup_timeout")
        self.assertFalse(tunnel.lock_held(self.settings))
        self.assertFalse(tunnel.port_busy(tunnel.read_profile(self.settings)))

    def test_slow_probe_cannot_block_startup_deadline_or_control(self):
        self.mode(slow_http=True)
        began = time.monotonic()
        process = self.begin("--startup-timeout", "1")
        messages = queue.Queue()
        def collect():
            for line in process.stdout:
                messages.put((time.monotonic(), json.loads(line)))
        reader = threading.Thread(target=collect, daemon=True)
        reader.start()
        while True:
            remaining = 6 - (time.monotonic() - began)
            self.assertGreater(remaining, 0, "Startup notification missed its deadline")
            observed, result = messages.get(timeout=remaining)
            if result.get("state") == "startup_timeout":
                break
        # Preserve the existing notification deadline, separately from the
        # full five-second process-group cleanup that follows that notification.
        self.assertLess(observed - began, 6)
        self.assertEqual(process.wait(timeout=10), 1)
        reader.join(timeout=1)
        self.assertFalse(reader.is_alive())
        self.assertFalse(tunnel.lock_held(self.settings))
        self.assertFalse(tunnel.port_busy(tunnel.read_profile(self.settings)))

    def test_metadata_error_and_wrong_identity_do_not_report_ready(self):
        for mode in ({"metadata_error": True}, {"wrong_identity": True}, {"malformed_base": True}):
            with self.subTest(mode=mode):
                self.mode(**mode)
                completed, result = self.call("start", "--startup-timeout", "1")
                self.assertEqual(completed.returncode, 1)
                self.assertEqual(result["state"], "startup_timeout")
                self.assertFalse(result["ok"])
                self.assertFalse(result["chatgpt_connection_verified"])

    def test_runtime_exit_is_nonzero_not_a_successful_start(self):
        self.mode(run_exit=0)
        _, result = self.call("start")
        self.assertEqual(result["error"]["code"], "runtime_exited")

    def test_profile_edit_is_reported_but_stop_needs_no_valid_new_profile(self):
        process = self.begin()
        self.await_ready(process)
        self.profilepath.write_text("broken: [\n")
        _, result = self.call("status")
        self.assertEqual(result["state"], "configuration_changed")
        _, result = self.call("stop")
        self.assertEqual(result["state"], "stopped")
        process.wait(timeout=10)

    def test_restart_missing_key_preserves_running_owner(self):
        process = self.begin()
        ready = self.await_ready(process)
        env = dict(self.env)
        env.pop("CONTROL_PLANE_API_KEY")
        _, result = self.call("restart", env=env)
        self.assertEqual(result["error"]["code"], "runtime_key_missing")
        self.assertEqual(tunnel.control(self.settings)["instance_id"], ready["instance_id"])
        self.assertIsNone(process.poll())
        self.call("stop")

    def test_restart_replaces_only_owned_instance(self):
        old = self.begin()
        first = self.await_ready(old)
        new = subprocess.Popen(self.command("restart"), env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(new)
        old.wait(timeout=15)
        second = self.await_ready(new)
        self.assertNotEqual(first["instance_id"], second["instance_id"])
        self.assertEqual([e["action"] for e in self.events()], ["doctor", "run", "doctor", "run"])
        self.call("stop")
        self.assertEqual(new.wait(timeout=10), 0)

    def test_ctrl_c_stops_only_tracked_child(self):
        process = self.begin()
        self.await_ready(process)
        process.send_signal(signal.SIGINT)
        self.assertEqual(process.wait(timeout=10), 0)
        self.assertFalse(tunnel.port_busy(tunnel.read_profile(self.settings)))

    def test_private_state_rejects_symlink_and_insecure_mode(self):
        state = Path(self.settings.state_dir)
        state.mkdir(mode=0o755)
        with self.assertRaises(tunnel.LifecycleError): tunnel.status(self.settings)
        state.rmdir()
        state.symlink_to(self.config, target_is_directory=True)
        with self.assertRaises(tunnel.LifecycleError): tunnel.status(self.settings)

    def test_term_resistant_owned_runtime_is_reaped_without_other_signals(self):
        self.mode(ignore_term=True)
        process = self.begin()
        self.await_ready(process)
        _, result = self.call("stop")
        self.assertEqual(result["state"], "stopped")
        self.assertEqual(process.wait(timeout=10), 0)
        self.assertFalse(tunnel.port_busy(tunnel.read_profile(self.settings)))

    def check_descendant_shutdown(self, action):
        self.mode(stubborn_descendant=True)
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        self.children.append(unrelated)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(self.source / "descendant.sock"))
            listener.listen()
            listener.settimeout(10)
            process = self.begin()
            conn, _ = listener.accept()
            with conn:
                conn.settimeout(3)
                ready = bytearray()
                while len(ready) < 5:
                    chunk = conn.recv(5 - len(ready))
                    self.assertTrue(chunk)
                    ready.extend(chunk)
                self.assertEqual(ready, b"ready")
                first = self.await_ready(process)
                if action == "stop":
                    completed, result = self.call("stop")
                    self.assertEqual(completed.returncode, 0)
                    self.assertEqual(result["state"], "stopped")
                elif action == "interrupt":
                    process.send_signal(signal.SIGINT)
                else:
                    self.mode()  # the replacement has no synthetic descendant
                    replacement = subprocess.Popen(self.command("restart"), env=self.env,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    self.children.append(replacement)
                    process.wait(timeout=15)
                    second = self.await_ready(replacement)
                    self.assertNotEqual(first["instance_id"], second["instance_id"])
                self.assertEqual(process.wait(timeout=10), 0)
                self.assertTrue((self.source / "leader-terminated").exists())
                self.assertEqual(conn.recv(1), b"", "A TERM-resistant descendant survived leader exit")
                self.assertIsNone(unrelated.poll())
                if action == "restart":
                    self.call("stop")
                    self.assertEqual(replacement.wait(timeout=10), 0)
                self.assertFalse(tunnel.lock_held(self.settings))
                self.assertFalse(tunnel.port_busy(tunnel.read_profile(self.settings)))

    def test_stop_cleans_descendant_after_leader_exits(self):
        self.check_descendant_shutdown("stop")

    def test_interrupt_cleans_descendant_after_leader_exits(self):
        self.check_descendant_shutdown("interrupt")

    def test_restart_cleans_previous_descendant_after_leader_exits(self):
        self.check_descendant_shutdown("restart")

    def test_group_signals_finish_before_leader_is_reaped(self):
        child = Mock(pid=12345, returncode=None)
        child.poll.side_effect = AssertionError("poll would reap the group leader")
        events = []
        def send(pgid, sig):
            events.append((pgid, sig))
        def reap(**kwargs):
            self.assertEqual(events, [(child.pid, signal.SIGTERM), (child.pid, signal.SIGKILL)])
            return 0
        child.wait.side_effect = reap
        with patch.object(tunnel.os, "killpg", side_effect=send), \
             patch.object(tunnel.time, "monotonic", side_effect=[0, 5]):
            tunnel.terminate_owned(child)
        child.poll.assert_not_called()
        child.wait.assert_called_once_with(timeout=5)

    def test_reaped_leader_is_never_signalled(self):
        child = Mock(pid=12345, returncode=0)
        with patch.object(tunnel.os, "killpg") as send:
            tunnel.terminate_owned(child)
        send.assert_not_called()
        child.poll.assert_not_called()
        child.wait.assert_not_called()

    def test_absent_owned_group_is_reaped_without_retrying_signal(self):
        child = Mock(pid=12345, returncode=None)
        with patch.object(tunnel.os, "killpg", side_effect=ProcessLookupError) as send:
            tunnel.terminate_owned(child)
        self.assertEqual(send.call_args_list, [call(child.pid, signal.SIGTERM)])
        child.wait.assert_called_once_with(timeout=5)
        child.poll.assert_not_called()

    def test_spawn_refuses_automatic_or_external_child_reaping(self):
        for disposition in (signal.SIG_IGN, lambda signum, frame: None):
            with self.subTest(disposition=disposition), \
                 patch.object(tunnel.signal, "getsignal", return_value=disposition), \
                 patch.object(tunnel.subprocess, "Popen") as launch:
                with self.assertRaises(tunnel.LifecycleError) as error:
                    tunnel.spawn(["unused"], self.settings, self.env)
                self.assertEqual(error.exception.code, "child_reaping_unsupported")
                launch.assert_not_called()

    def test_darwin_zombie_only_group_allows_reap_after_eperm(self):
        for signals in ([PermissionError()], [None, PermissionError()]):
            with self.subTest(signals=len(signals)):
                child = Mock(pid=12345, returncode=None)
                child.poll.side_effect = AssertionError("Do not reap before group evidence")
                result = subprocess.CompletedProcess([], 0, b"0 0 Ss\n1 1 Ss\n12345 12345 Zs\n12346 12345 Z\n", b"")
                with patch.object(tunnel.sys, "platform", "darwin"), \
                     patch.object(tunnel.os, "killpg", side_effect=signals) as send, \
                     patch.object(tunnel.subprocess, "run", return_value=result) as inspect, \
                     patch.object(tunnel.time, "monotonic", side_effect=[0, 5]):
                    tunnel.terminate_owned(child)
                self.assertEqual(send.call_count, len(signals))
                inspect.assert_called_once_with(
                    ["/bin/ps", "-ax", "-o", "pid=,pgid=,stat="],
                    env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                    capture_output=True, timeout=2, check=False)
                child.poll.assert_not_called()
                child.wait.assert_called_once_with(timeout=5)

    def test_darwin_eperm_requires_complete_zombie_only_evidence(self):
        for body in (b"", b"12345 12345 S\n", b"12345 12345 Z\n12346 12345 S\n",
                     b"12346 12345 Z\n", b"12345 54321 Z\n", b"malformed\n",
                     b"12345 12345 Z\n12345 12345 Z\n", b"12345 12345 Z\n\xff",
                     b"x" * (tunnel.MAX_FILE + 1)):
            with self.subTest(body_length=len(body)), \
                 patch.object(tunnel.sys, "platform", "darwin"), \
                 patch.object(tunnel.os, "killpg", side_effect=PermissionError), \
                 patch.object(tunnel.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, body, b"not-exported")):
                child = Mock(pid=12345, returncode=None)
                with self.assertRaises(PermissionError): tunnel.terminate_owned(child)
                child.poll.assert_not_called()
                child.wait.assert_not_called()

    def test_darwin_process_inspection_failure_is_not_ignored(self):
        for result in (subprocess.CompletedProcess([], 1, b"12345 12345 Z\n", b"private-error"),
                       OSError("private-error"), subprocess.TimeoutExpired(["/bin/ps"], 2)):
            with self.subTest(result_type=type(result).__name__), \
                 patch.object(tunnel.sys, "platform", "darwin"), \
                 patch.object(tunnel.os, "killpg", side_effect=PermissionError), \
                 patch.object(tunnel.subprocess, "run") as inspect:
                if isinstance(result, Exception): inspect.side_effect = result
                else: inspect.return_value = result
                with self.assertRaises(PermissionError): tunnel.signal_owned_group(12345, signal.SIGTERM)

    def test_non_darwin_permission_failure_has_no_process_inspection(self):
        with patch.object(tunnel.sys, "platform", "linux"), \
             patch.object(tunnel.os, "killpg", side_effect=PermissionError), \
             patch.object(tunnel.subprocess, "run") as inspect:
            with self.assertRaises(PermissionError): tunnel.signal_owned_group(12345, signal.SIGTERM)
            inspect.assert_not_called()

    def test_stop_already_stopped_is_idempotent(self):
        _, result = self.call("stop")
        self.assertEqual(result["state"], "already_stopped")
        self.assertFalse(Path(self.settings.state_dir).exists())

    def test_arbitrary_control_state_entry_is_not_deleted(self):
        state = Path(self.settings.state_dir)
        state.mkdir(mode=0o700)
        self.settings.socket_path.write_text("preserve-me")
        _, result = self.call("start")
        self.assertEqual(result["error"]["code"], "unsafe_control_socket")
        self.assertEqual(self.settings.socket_path.read_text(), "preserve-me")
        self.assertEqual(self.events(), [])

    def test_configuration_is_private_idempotent_and_does_not_touch_profile(self):
        self.settingspath.unlink()
        before = self.profilepath.read_bytes()
        args = ["--profile", "lab", "--profile-dir", str(self.profiles), "--source-dir", str(self.source),
                "--env-file", str(self.envfile), "--state-dir", str(self.root / "state")]
        _, result = self.call("configure", *args)
        self.assertTrue(result["changed"])
        self.assertEqual(self.settingspath.stat().st_mode & 0o777, 0o600)
        _, result = self.call("configure", *args)
        self.assertFalse(result["changed"])
        self.assertEqual(self.profilepath.read_bytes(), before)
        self.assertNotIn("fixture-runtime", self.settingspath.read_text())
        self.assertEqual(self.events(), [])

    def test_configure_refuses_replacing_live_owner_settings(self):
        process = self.begin()
        self.await_ready(process)
        _, result = self.call("configure", "--profile", "lab", "--profile-dir", str(self.profiles),
                              "--source-dir", str(self.source), "--env-file", str(self.envfile),
                              "--state-dir", str(self.root / "state"), "--replace",
                              "--keychain-service", "new-service", "--keychain-account", "test-account")
        self.assertEqual(result["error"]["code"], "owner_running")
        self.assertIsNone(process.poll())
        self.call("stop")

    def test_profile_lock_hides_no_stale_pid_stop_shortcut(self):
        state = Path(self.settings.state_dir)
        state.mkdir(mode=0o700)
        with tunnel.acquire_lock(state / (self.settings.key + ".lock")):
            _, result = self.call("status")
            self.assertEqual(result["state"], "owner_unresponsive_or_starting")
            _, result = self.call("stop")
            self.assertEqual(result["error"]["code"], "not_owned")


class PortableTunnelTests(unittest.TestCase):
    def test_help_needs_no_config_keys_network_or_posix_runtime(self):
        result = subprocess.run([sys.executable, "-m", "gitlab_agent.tunnel_lifecycle", "--help"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        for action in ("configure", "start", "status", "stop", "restart"):
            self.assertIn(action, result.stdout)

    def test_unknown_secret_argument_is_not_echoed(self):
        result = subprocess.run([sys.executable, "-m", "gitlab_agent.tunnel_lifecycle", "start", "--api-key", "must-not-be-printed"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("must-not-be-printed", result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "arguments_invalid")

    def test_unsupported_platform_is_explicit_and_does_not_mutate(self):
        with patch.object(tunnel.os, "name", "nt"):
            with self.assertRaises(tunnel.LifecycleError) as exc: tunnel.supported()
        self.assertEqual(exc.exception.code, "platform_unsupported")


if __name__ == "__main__":
    unittest.main()
