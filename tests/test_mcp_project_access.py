"""Use the real MCP client/registration boundary, not just direct Python calls."""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from mcp import Client


class MCPProjectAccessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.dict(os.environ, {
            "GITLAB_AGENT_ENV_FILE": str(Path(self.temp.name) / "absent.env"),
            "GITLAB_BASE_URL": "https://gitlab.example.invalid",
            "GITLAB_TOKEN": "fixture-token", "GITLAB_ALLOWED_PROJECTS": "team/project",
            "GITLAB_TRUST_ENV": "false", "GITLAB_VERIFY_SSL": "true",
            "GITLAB_CA_BUNDLE": "",
        }, clear=True):
            spec = importlib.util.spec_from_file_location(
                "access_test_server", Path(__file__).resolve().parents[1] / "server.py",
            )
            self.server = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.server)

    async def call(self, tool, args, handler):
        real = httpx.AsyncClient
        def factory(**kw):
            return real(transport=httpx.MockTransport(handler), **kw)
        with patch.object(self.server.httpx, "AsyncClient", side_effect=factory):
            async with Client(self.server.mcp) as client:
                result = await asyncio.wait_for(client.call_tool(tool, args), timeout=10)
        self.assertFalse(result.is_error, str(result))
        # A successfully delivered diagnostic is not a successfully read file.
        return result.structured_content, "\n".join(getattr(x, "text", "") for x in result.content)

    async def test_read_mcp_main_scrubs_unneeded_openai_credentials(self):
        with (
            patch.dict(
                os.environ,
                {
                    "CONTROL_PLANE_API_KEY": "runtime-secret",
                    "OPENAI_ADMIN_KEY": "admin-secret",
                    "OPENAI_API_KEY": "model-secret",
                },
                clear=False,
            ),
            patch.object(self.server.mcp, "run") as run,
        ):
            self.server.main()
            self.assertNotIn("CONTROL_PLANE_API_KEY", os.environ)
            self.assertNotIn("OPENAI_ADMIN_KEY", os.environ)
            self.assertNotIn("OPENAI_API_KEY", os.environ)
            run.assert_called_once_with(transport="stdio")

    async def test_local_denial_is_visible_for_all_project_tools_without_network(self):
        calls = {
            "check_project_access": {"project": "other/project", "ref": "main"},
            "get_file": {"project": "other/project", "file_path": "README.md", "ref": "main"},
            "get_repository_tree": {"project": "other/project", "ref": "main"},
            "search_code": {"project": "other/project", "query": "sample"},
            "get_merge_request": {"project": "other/project", "iid": 1},
            "get_merge_request_diff": {"project": "other/project", "iid": 1},
            "get_pipelines": {"project": "other/project"},
            "get_pipeline_jobs": {"project": "other/project", "pipeline_id": 1},
            "get_job_log": {"project": "other/project", "job_id": 1},
        }
        for name, args in calls.items():
            with self.subTest(tool=name), patch.object(self.server.gitlab, "_client") as factory:
                async with Client(self.server.mcp) as client:
                    result = await asyncio.wait_for(client.call_tool(name, args), timeout=10)
                factory.assert_not_called()
                self.assertFalse(result.is_error, str(result))
                data = result.structured_content
                self.assertFalse(data["ok"])
                self.assertEqual(data["error"]["code"], "project_not_allowlisted")
                text = "\n".join(getattr(x, "text", "") for x in result.content)
                self.assertIn("GITLAB_ALLOWED_PROJECTS", text)
                self.assertIn("next_steps", text)

    async def test_preflight_tool_is_read_only_and_describes_stop_rule(self):
        async with Client(self.server.mcp) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools.tools if t.name == "check_project_access")
            self.assertTrue(tool.annotations.read_only_hint)
            self.assertIn("wait for the user", tool.description)
            self.assertIn("STOP bulk reads", client.instructions)
            self.assertIn("required_files", tool.input_schema["properties"])

    async def test_whoami_does_not_claim_project_readiness(self):
        data, _ = await self.call("gitlab_whoami", {}, lambda r: httpx.Response(200, json={"id": 1, "username": "fixture"}))
        self.assertFalse(data["project_access_checked"])
        self.assertEqual(data["username"], "fixture")

    async def test_filtered_empty_page_has_pagination_and_no_nonexistence_claim(self):
        hidden = [{"id": 9, "path_with_namespace": "other/project"}]
        data, _ = await self.call("list_projects", {"per_page": 1}, lambda r: httpx.Response(200, json=hidden))
        self.assertEqual(data["items"], [])
        self.assertEqual(data["next_page"], 2)
        self.assertIn("does not prove nonexistence", data["notice"])
        self.assertNotIn("other/project", json.dumps(data))

    async def test_file_http_errors_return_safe_structured_details(self):
        for status, code in ((401, "gitlab_unauthorized"), (403, "gitlab_forbidden"),
                             (404, "resource_missing_or_inaccessible"), (500, "gitlab_unavailable")):
            data, text = await self.call("get_file", {"project": "team/project", "file_path": "README.md", "ref": "main"},
                                         lambda r: httpx.Response(status, text="PRIVATE_BODY_DO_NOT_RETURN"))
            self.assertFalse(data["ok"])
            self.assertEqual(data["error"]["code"], code)
            self.assertEqual(data["error"]["http_status"], status)
            self.assertNotIn("PRIVATE_BODY_DO_NOT_RETURN", text + json.dumps(data))
            self.assertNotIn("content", data)

    async def test_file_success_preserves_content_and_actual_revision_fields(self):
        payload = {"content": base64.b64encode(b"hello\n").decode(),
                   "blob_id": "b" * 40, "last_commit_id": "c" * 40, "commit_id": "d" * 40}
        data, _ = await self.call("get_file", {"project": "team/project", "file_path": "README.md", "ref": "main"},
                                  lambda r: httpx.Response(200, json=payload))
        self.assertEqual(data["content"], "hello\n")
        for key in ("blob_id", "last_commit_id", "commit_id"):
            self.assertEqual(data[key], payload[key])
        self.assertFalse(data["truncated"])

    async def test_preflight_success_through_registered_tool(self):
        seen = []
        def handler(req):
            seen.append(req)
            if len(seen) == 1:
                return httpx.Response(200, json={"id": 2, "path_with_namespace": "team/project", "default_branch": "main"})
            if len(seen) == 2:
                return httpx.Response(200, json={"id": "a" * 40})
            self.assertEqual(req.method, "HEAD")
            self.assertEqual(req.url.params["ref"], "a" * 40)
            return httpx.Response(200)
        data, _ = await self.call("check_project_access", {"project": "team/project", "ref": "main", "required_files": ["README.md"]}, handler)
        self.assertTrue(data["ok"])
        self.assertEqual(len(seen), 3)
        self.assertEqual(data["resolved_commit_sha"], "a" * 40)


if __name__ == "__main__":
    unittest.main()
