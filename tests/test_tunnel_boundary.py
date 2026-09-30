"""Regressions for issue #88; no real tunnel or credentials are used."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from gitlab_agent.setup_tunnel import (
    _command_string, _parse_json_output, _run_tunnel_json,
    connect_runtime, runtime_diagnostics, runtime_status, TunnelSetupError,
)


class TunnelBoundaryTests(unittest.TestCase):
    def test_command_preserves_windows_paths_for_receiving_parser(self):
        paths = [
            r"C:\Users\example\.local\bin\reasonfirst-gitlab-mcp.exe",
            r"C:\Program Files\ReasonFirst\reasonfirst-bridge-mcp.exe",
            "C:\\Users\\O'Brien 测试 & $name\\reasonfirst-gitlab-mcp.exe",
            r"\\server\share\tools\reasonfirst-bridge-mcp.exe",
            '/tmp/space name/quote\'and"double/worker',
        ]
        for path in paths:
            with self.subTest(path=path), patch("os.name", "nt"):
                command = _command_string(path)
                self.assertEqual(shlex.split(command), [path])
        # CI separately exercises the actual pinned upstream Go parser + exec.

    def test_command_rejects_control_characters(self):
        for bad in ("", "x\x00y", "x\ny", "x\ry"):
            with self.subTest(bad=bad), self.assertRaises(TunnelSetupError):
                _command_string(bad)

    def test_utf8_bytes_and_invalid_stderr_preserve_protocol_unicode(self):
        raw = json.dumps({"ready": False, "error": "启动失败 — café"}, ensure_ascii=False).encode()
        payload = _parse_json_output(raw, b"legacy stderr: \x94")
        self.assertEqual(payload["error"], "启动失败 — café")
        self.assertFalse(payload["ready"])

    def test_missing_streams_never_crash_or_claim_readiness(self):
        for stdout in (None, b"", "", b" "):
            with self.subTest(stdout=stdout):
                payload = _parse_json_output(stdout, None, secret="example-runtime-value")
                self.assertFalse(payload["ok"])
                self.assertTrue(payload["protocol_error"])

    def test_invalid_protocol_fails_closed_and_diagnostics_are_redacted(self):
        secret = "example-runtime-value"
        for raw in (
            b'{"ready":true,"label":"\xff"}', b'{"ready":"false"}',
            b'{"ready":1}', b'{"ready":true,"ready":false}',
            b'{"n":NaN}', b'[]', b'null', b'prefix {"ready":true}',
        ):
            with self.subTest(raw=raw):
                payload = _parse_json_output(raw, (secret + "\x1b[31m坏错误").encode(), secret=secret)
                self.assertFalse(payload["ok"])
                self.assertTrue(payload["protocol_error"])
                self.assertNotIn(secret, repr(payload))
                self.assertNotIn("\x1b", repr(payload))
                self.assertIn("<redacted>", payload["diagnostic"])

    def test_redaction_after_json_parse_handles_escaped_secret(self):
        secret = 'example"runtime\\value'
        raw = json.dumps({"nested": {secret: [secret]}, "api_key": "another value"}).encode()
        payload = _parse_json_output(raw, None, secret=secret)
        self.assertNotIn(secret, repr(payload))
        self.assertEqual(payload["nested"]["<redacted>"], ["<redacted>"])
        self.assertEqual(payload["api_key"], "<redacted>")

    def test_real_subprocess_ignores_forced_gbk_parent_text_default(self):
        payload = {"ready": False, "error": "错误 — ✓"}
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        with self.assertRaises(UnicodeDecodeError):
            raw.decode("gbk")
        program = f"import os; os.write(1, {raw!r}); os.write(2, b'\\x94')"
        with patch("subprocess._text_encoding", return_value="gbk"):
            code, result = _run_tunnel_json(sys.executable, ["-c", program])
        self.assertEqual(code, 0)
        self.assertEqual(result["error"], payload["error"])

    def test_invalid_output_zero_exit_is_error_but_native_exit_is_retained(self):
        code, payload = _run_tunnel_json(
            sys.executable, ["-c", "import os; os.write(1, b'\\xff')"],
        )
        self.assertNotEqual(code, 0)
        self.assertEqual(payload["returncode"], 0)
        self.assertTrue(payload["protocol_error"])

    def test_nonzero_status_cannot_claim_ready(self):
        def runner(argv, **kwargs):
            self.assertFalse(kwargs["text"])
            return subprocess.CompletedProcess(argv, 7, json.dumps({
                "process_running": True, "healthy": True, "ready": True,
            }).encode(), None)
        result = runtime_status(alias="test", tunnel_client="synthetic", runner=runner)
        self.assertFalse(result["ok"])
        self.assertFalse(result["ready"])
        self.assertFalse(result["native_ready"])
        self.assertFalse(result["status_query_ok"])
        self.assertEqual(result["native"]["returncode"], 7)

    def test_failed_connect_does_not_poll_after_invalid_json(self):
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, None, b"failed")
        with tempfile.TemporaryDirectory() as td:
            mcp = Path(td) / "fixture"
            mcp.write_text("not executed", encoding="utf-8")
            mcp.chmod(0o700)
            result = connect_runtime(
                tunnel_id="tunnel_" + "a" * 32, runtime_key="example-runtime-value",
                tunnel_client="synthetic", mcp_executable=str(mcp), runner=runner,
            )
        self.assertFalse(result["ok"])
        self.assertFalse(result["ready"])
        self.assertEqual(result["stage"], "connect")
        self.assertEqual(len(calls), 1)

    def test_stopped_status_exposes_bounded_scrubbed_launch_diagnostic(self):
        synthetic_token = "glpat-" + "x" * 24
        error = "mcpclient: start stdio command: file not found " + synthetic_token
        payload = {"local": {"log": {"tail": json.dumps({"level": "ERROR", "error": error})},
                             "issues": ["recorded process pid is not running"] * 100}}
        messages = runtime_diagnostics(payload)
        self.assertIn("file not found", messages[0])
        self.assertNotIn(synthetic_token, repr(messages))
        self.assertLessEqual(len(messages), 5)
        self.assertTrue(all(len(message) <= 600 for message in messages))


if __name__ == "__main__":
    unittest.main()
