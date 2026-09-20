from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from gitlab_agent.log_evidence import TraceReadError, read_trace_tail
from test_log_evidence import TraceStream, key_marker, token


class MCPLogEvidenceTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # Import the real MCP server using an empty explicit config path. Never
        # consume a developer's private .env or reach a real GitLab instance.
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            "GITLAB_AGENT_ENV_FILE": str(Path(temp) / "absent.env"),
            "GITLAB_BASE_URL": "http://gitlab.example.invalid",
            "GITLAB_TOKEN": "fixture-token", "GITLAB_ALLOWED_PROJECTS": "team/project",
        }):
            spec = importlib.util.spec_from_file_location(
                "reasonfirst_test_server", Path(__file__).resolve().parents[1] / "server.py",
            )
            cls.server = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.server)

    async def call_log(self, chunks, *, status=200, requested=1000):
        requests = []
        stream = TraceStream(chunks)
        def handler(request):
            requests.append(request)
            return httpx.Response(status, stream=stream)
        real_client = httpx.AsyncClient
        def client(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)
        with patch.object(self.server.httpx, "AsyncClient", side_effect=client):
            result = await self.server.get_job_log("team/project", 99, requested)
        self.assertTrue(stream.closed)
        return result, requests

    async def test_mcp_and_cli_share_identical_content_and_metadata(self):
        chunks = [("TOKEN=" + token() + "\n").encode(), b"failed\n"]
        result, requests = await self.call_log(chunks)
        with httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, stream=TraceStream(chunks)),
        )) as client:
            expected = read_trace_tail(client, "http://gitlab.example.invalid/trace", tail_bytes=1000)
        self.assertEqual(result, {"project": "team/project", "job_id": 99, **expected})
        self.assertEqual(requests[0].url.raw_path, b"/api/v4/projects/team%2Fproject/jobs/99/trace")
        self.assertEqual(requests[0].headers["private-token"], "fixture-token")
        self.assertNotIn(token(), json.dumps(result))

    async def test_project_allowlist_blocks_before_creating_http_client(self):
        with patch.object(self.server.httpx, "AsyncClient") as client:
            with self.assertRaisesRegex(RuntimeError, "GITLAB_ALLOWED_PROJECTS"):
                await self.server.get_job_log("other/project", 99)
            client.assert_not_called()

    async def test_missing_token_blocks_before_network(self):
        with patch.object(self.server.gitlab, "token", ""), patch.object(self.server.httpx, "AsyncClient") as client:
            with self.assertRaisesRegex(RuntimeError, "GITLAB_TOKEN"):
                await self.server.get_job_log("team/project", 99)
            client.assert_not_called()

    async def test_mcp_honors_smaller_configured_tail_and_timeout(self):
        with patch.object(self.server.gitlab, "max_text_bytes", 1500), patch.object(self.server.gitlab, "timeout", 2):
            result, requests = await self.call_log([b"x" * 4000], requested=80000)
        self.assertEqual(result["tail_bytes"], 1500)
        self.assertLessEqual(result["returned_text_bytes"], 1500)
        self.assertEqual(requests[0].extensions["timeout"]["read"], 2)

    async def test_mcp_does_not_use_legacy_full_trace_request(self):
        with patch.object(self.server.gitlab, "request_text", side_effect=AssertionError("unbounded path")):
            result, _ = await self.call_log([b"failed\n"])
        self.assertTrue(result["read_complete"])

    async def test_error_body_is_not_exposed_to_mcp(self):
        with self.assertRaisesRegex(TraceReadError, "HTTP 500") as cm:
            await self.call_log([token().encode()], status=500)
        self.assertNotIn(token(), str(cm.exception))

    async def test_private_key_body_is_suppressed_in_mcp(self):
        result, _ = await self.call_log([
            key_marker("BEGIN"), b"opaqueBody\n" * 500,
            key_marker("END"), b"failed\n",
        ])
        self.assertNotIn("opaqueBody", json.dumps(result))
        self.assertIn("failed", result["content"])

    async def test_invalid_job_id_blocks_before_network(self):
        with patch.object(self.server.httpx, "AsyncClient") as client:
            with self.assertRaises(ValueError):
                await self.server.get_job_log("team/project", -1)
            client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
