"""Real SDK/HTTP regressions; also run with the clean wheel's interpreter.

No provider, coding worker, service manager, existing listener, or maintainer
configuration is used. Missing SDK dependencies are failures, not silent skips.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid


PROBE = r'''
import asyncio, json, os, sys
from pathlib import Path
from mcp import Client
from gitlab_agent import bridge_http, bridge_mcp
from gitlab_agent.bridge_preview import controller
async def check():
    versions = []
    for era in ("auto", "legacy"):
        async with Client(sys.argv[1], mode=era) as client:
            http_tools = await client.list_tools()
            versions.append(client.protocol_version)
        os.environ["RF_CODEX_BRIDGE_STATE_DIR"] = sys.argv[3]
        server = bridge_mcp.build_server(read_only_mode=sys.argv[2] == "read-only")
        try:
            async with Client(server, mode=era) as client:
                core_tools = await client.list_tools()
        finally:
            server._reasonfirst_controller.close()
        def tools(result):
            return {t.name: t.model_dump(mode="json") for t in result.tools}
        assert tools(http_tools) == tools(core_tools), "HTTP/core tool schema mismatch"
    print(json.dumps({
        "tools": {t.name: bool(t.annotations.read_only_hint) for t in http_tools.tools},
        "origins": [bridge_http.__file__, bridge_mcp.__file__, controller.__file__],
        "schema_parity": True,
        "protocol_versions": versions,
    }))
asyncio.run(asyncio.wait_for(check(), timeout=20))
'''


class HTTPIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rf-http-native-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "HTTP \u7a7a\u95f4's fixture"
        self.root.mkdir()
        self.env = os.environ.copy()
        for name in list(self.env):
            if (name.startswith(("RF_", "GITLAB_", "REASONFIRST_"))
                or name.upper().endswith("_PROXY")
                or name in {"PYTHONPATH", "PYTHONHOME", "CONTROL_PLANE_API_KEY", "OPENAI_ADMIN_KEY", "OPENAI_API_KEY"}):
                self.env.pop(name, None)
        home = self.root / "home"
        home.mkdir()
        self.env.update({
            "HOME": str(home), "USERPROFILE": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "RF_BRIDGE_CONFIG": str(self.root / "bridge.yaml"),
            "RF_CODEX_BRIDGE_STATE_DIR": str(self.root / "state"),
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        self.config = self.root / "bridge.yaml"
        self.config.write_text("# synthetic fixture\nversion: 4\n", encoding="utf-8")
        self.config_bytes = self.config.read_bytes()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def args(self, port, mode, path):
        return [sys.executable, "-I", "-B", "-m", "gitlab_agent.bridge_http",
                "--serve", "--host", "127.0.0.1", "--port", str(port),
                "--path", path, "--mode", mode, "--control-policy", "disabled"]

    def stop(self, proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    def roundtrip(self, mode):
        # Reserve an ephemeral test port, then release it for our foreground
        # child. A race fails this test; no other listener is ever stopped.
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        path = "/fixture/" + uuid.uuid4().hex
        base = f"http://127.0.0.1:{port}"
        with tempfile.TemporaryFile() as log:
            proc = subprocess.Popen(self.args(port, mode, path), cwd=self.root,
                                    env=self.env, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=log)
            try:
                health = None
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        break
                    try:
                        with self.opener.open(base + "/healthz", timeout=0.5) as response:
                            health = json.load(response)
                        break
                    except (OSError, urllib.error.URLError):
                        time.sleep(0.05)
                if health is None:
                    log.seek(0)
                    self.fail(f"Synthetic HTTP child failed; exit={proc.poll()}; log={log.read(6000)!r}")
                self.assertEqual(health["service"], "reasonfirst")
                self.assertIs(health["read_only_mode"], mode == "read-only")
                self.assertNotIn("ready", health)  # liveness, not deployment acceptance
                request = urllib.request.Request(base + "/control", data=b"{}", method="POST")
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    self.opener.open(request, timeout=2)
                self.assertEqual(failure.exception.code, 404)
                failure.exception.close()
                probe = subprocess.run(
                    [sys.executable, "-I", "-B", "-c", PROBE, base + path, mode,
                     str(self.root / "inventory-state")],
                    cwd=self.root, env=self.env, capture_output=True, text=True,
                    timeout=30, check=False,
                )
                self.assertEqual(probe.returncode, 0, probe.stderr[-6000:])
                data = json.loads(probe.stdout)
                self.assertTrue(data["schema_parity"])
                self.assertEqual(len(data["protocol_versions"]), 2)
                self.assertNotEqual(data["protocol_versions"][0], data["protocol_versions"][1])
                self.assertEqual(len(data["tools"]), 12 if mode == "read-only" else 22)
                self.assertNotIn("reasonfirst_authorize_push", data["tools"])
                if mode == "read-only":
                    self.assertTrue(all(data["tools"].values()))
                    for name in ("reasonfirst_dispatch", "reasonfirst_finish", "reasonfirst_evidence", "reasonfirst_finish_preview"):
                        self.assertNotIn(name, data["tools"])
                else:
                    self.assertIs(data["tools"]["reasonfirst_dispatch"], False)
                    self.assertIs(data["tools"]["reasonfirst_finish"], False)
                # The install harness explicitly supplies the clean uv-tool
                # environment root. Editable/source runs make no wheel claim.
                wheel_root = os.environ.get("RF_HTTP_TEST_WHEEL_ROOT")
                if wheel_root:
                    for origin in data["origins"]:
                        self.assertTrue(Path(origin).resolve().is_relative_to(Path(wheel_root).resolve()), origin)
                self.assertIsNone(proc.poll())
                self.assertEqual(self.config.read_bytes(), self.config_bytes)
                self.assertFalse((self.root / "state" / "control-token").exists())
                self.assertFalse((self.root / "state" / "state.json").exists())
                print(f"HTTP {mode}: MCP {data['protocol_versions']}; schema parity; no control/remote push; no worker")
            finally:
                self.stop(proc)

    def test_native_http_read_only_roundtrip(self):
        self.roundtrip("read-only")

    def test_native_http_full_chat_roundtrip(self):
        self.roundtrip("full-chat")

    def test_occupied_port_fails_without_stopping_existing_listener(self):
        with socket.socket() as listener:
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.settimeout(2)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            proc = subprocess.run(self.args(port, "read-only", "/fixture"), cwd=self.root,
                                  env=self.env, stdin=subprocess.DEVNULL,
                                  capture_output=True, timeout=20, check=False)
            self.assertNotEqual(proc.returncode, 0)
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                accepted, _ = listener.accept()
                accepted.close()
        self.assertEqual(self.config.read_bytes(), self.config_bytes)

    def test_inspect_does_not_import_controller_or_create_state(self):
        code = r'''
import sys
from gitlab_agent import bridge_http
assert "gitlab_agent.bridge_preview.controller" not in sys.modules
assert bridge_http.main(sys.argv[1:]) == 0
assert "gitlab_agent.bridge_preview.controller" not in sys.modules
assert "mcp" not in sys.modules
'''
        args = self.args(8765, "read-only", "/fixture")[5:]
        self.assertEqual(args[0], "--serve")
        args[0] = "--inspect"
        proc = subprocess.run([sys.executable, "-I", "-B", "-c", code, *args],
                              cwd=self.root, env=self.env, capture_output=True,
                              text=True, timeout=10, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr[-3000:])
        self.assertFalse(json.loads(proc.stdout)["server_started"])
        self.assertFalse((self.root / "state").exists())
        self.assertEqual(self.config.read_bytes(), self.config_bytes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
