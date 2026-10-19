"""Synthetic proxy and lifecycle regressions; no real proxy discovery or I/O."""
import asyncio
from contextlib import asynccontextmanager, contextmanager
import os
import ssl
import unittest
from unittest.mock import patch

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
import http_client_fixture as fixture


URL = "http://127.0.0.1:43210/fixture"


class FixtureHTTPTests(unittest.IsolatedAsyncioTestCase):
    @contextmanager
    def synthetic_wire(self, *, fail=False, wait=None, redirect=None):
        self.http_clients, self.mcp_clients, self.transport_exits = [], [], []
        self.requests = []
        async def send(client, request):
            # Intercept before any connection, even for the negative control.
            self.http_clients.append(client)
            pool = client._transport_for_url(request.url)._pool
            if type(pool).__name__ != "AsyncConnectionPool":
                raise AssertionError("ambient_proxy_selected")
            self.requests.append(request)
            if wait is not None:
                wait.set()
                await asyncio.Event().wait()
            if fail:
                raise RuntimeError("synthetic_handshake_failure")
            if redirect is not None:
                return httpx2.Response(307, headers={"location": redirect}, request=request)
            import json
            data = json.loads(request.content)
            return httpx2.Response(200, json={
                "jsonrpc": "2.0", "id": data["id"],
                "result": {"supportedVersions": ["2026-07-28"], "capabilities": {}},
            }, request=request)
        @asynccontextmanager
        async def observed_transport(*args, **kwargs):
            try:
                async with streamable_http_client(*args, **kwargs) as streams:
                    yield streams
            finally:
                self.transport_exits.append(True)
        def observed_client(*args, **kwargs):
            client = Client(*args, **kwargs)
            self.mcp_clients.append(client)
            return client
        # Simulate urllib's system-proxy result with every *_PROXY variable
        # absent. The fake proxy address never receives an actual connection.
        env = {k:v for k,v in os.environ.items() if not k.upper().endswith("_PROXY")}
        with patch.dict(os.environ, env, clear=True), \
             patch("httpx2._utils.getproxies", return_value={"http": "http://127.0.0.1:1"}) as discovery, \
             patch.object(httpx2.AsyncClient, "_send_single_request", send), \
             patch.object(fixture, "streamable_http_client", observed_transport), \
             patch.object(fixture, "Client", observed_client):
            yield discovery

    def assert_closed(self):
        self.assertTrue(self.http_clients)
        self.assertTrue(all(client.is_closed for client in self.http_clients))
        self.assertEqual(len(self.transport_exits), 1)

    async def test_system_proxy_isolation_and_owned_lifetimes(self):
        with self.synthetic_wire() as discovery:
            async with fixture.loopback_mcp_client(URL, mode="auto", read_timeout_seconds=3) as client:
                self.assertEqual(client.protocol_version, "2026-07-28")
                self.assertFalse(self.http_clients[0].is_closed)
                self.assertEqual(client.read_timeout_seconds, 3)
            discovery.assert_not_called()
        self.assert_closed()
        self.assertIsNone(self.mcp_clients[0]._session)

    async def test_controlled_ambient_regression_is_detected_before_io(self):
        # Change only this test helper's factory, not the AsyncClient class.
        def regressed():
            return httpx2.AsyncClient(
                trust_env=True, verify=True, follow_redirects=False,
                timeout=httpx2.Timeout(fixture.MCP_DEFAULT_TIMEOUT,
                                       read=fixture.MCP_DEFAULT_SSE_READ_TIMEOUT),
            )
        with self.synthetic_wire() as discovery, patch.object(fixture, "_new_http_client", regressed):
            with self.assertRaises(BaseExceptionGroup) as caught:
                async with fixture.loopback_mcp_client(URL, mode="auto"):
                    self.fail("ambient route was accepted")
            self.assertIn("ambient_proxy_selected", str(caught.exception) + repr(caught.exception))
            discovery.assert_called()
        self.assert_closed()
        self.assertEqual(self.requests, [])

    async def test_timeout_and_tls_defaults_match_sdk_factory(self):
        # Both factories are constructed without reading actual proxy settings.
        with patch("httpx2._utils.getproxies", return_value={}):
            async with create_mcp_http_client() as sdk, fixture._new_http_client() as owned:
                self.assertEqual(owned.timeout, sdk.timeout)
                self.assertFalse(owned.follow_redirects)
                self.assertFalse(owned._trust_env)
                tls_context = owned._transport._pool._ssl_context
                self.assertEqual(tls_context.verify_mode, ssl.CERT_REQUIRED)
                self.assertTrue(tls_context.check_hostname)

    async def test_rejects_nonfixture_endpoints_before_construction(self):
        for url in ("https://example.invalid:443/x", "http://localhost:80/x",
                    "http://127.0.0.2:80/x", "http://127.0.0.1/x",
                    "http://127.0.0.1:80/x?q=1", "http://user@127.0.0.1:80/x",
                    "http://127.0.0.1:bad/x", "file:///tmp/fixture"):
            with self.subTest(url=url), patch.object(fixture, "_new_http_client") as factory:
                with self.assertRaisesRegex(ValueError, "^fixture_literal_loopback_endpoint_required$"):
                    async with fixture.loopback_mcp_client(url):
                        self.fail("invalid endpoint accepted")
                factory.assert_not_called()

    async def test_body_failure_closes_all_resources(self):
        with self.synthetic_wire():
            with self.assertRaisesRegex(RuntimeError, "synthetic_body_failure"):
                async with fixture.loopback_mcp_client(URL):
                    raise RuntimeError("synthetic_body_failure")
        self.assert_closed()
        self.assertIsNone(self.mcp_clients[0]._session)

    async def test_handshake_failure_closes_http_and_transport(self):
        with self.synthetic_wire(fail=True):
            with self.assertRaises(BaseExceptionGroup):
                async with fixture.loopback_mcp_client(URL):
                    self.fail("failed negotiation accepted")
        self.assert_closed()

    async def test_cancellation_closes_all_resources(self):
        entered = asyncio.Event()
        async def run():
            async with fixture.loopback_mcp_client(URL):
                entered.set()
                await asyncio.Event().wait()
        with self.synthetic_wire():
            task = asyncio.create_task(run())
            await asyncio.wait_for(entered.wait(), 3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assert_closed()
        self.assertIsNone(self.mcp_clients[0]._session)

    async def test_cancel_during_handshake_closes_http_and_transport(self):
        waiting = asyncio.Event()
        async def run():
            async with fixture.loopback_mcp_client(URL):
                self.fail("blocked handshake entered")
        with self.synthetic_wire(wait=waiting):
            task = asyncio.create_task(run())
            await asyncio.wait_for(waiting.wait(), 3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assert_closed()

    async def test_outer_anyio_cancellation_closes_all_resources(self):
        with self.synthetic_wire():
            with anyio.CancelScope() as scope:
                async with fixture.loopback_mcp_client(URL):
                    scope.cancel()
                    await anyio.sleep(0)
        self.assert_closed()
        self.assertIsNone(self.mcp_clients[0]._session)

    async def test_sdk_rejects_cross_origin_redirect_before_second_send(self):
        with self.synthetic_wire(redirect="http://127.0.0.1:43211/other"):
            with self.assertRaises(BaseExceptionGroup):
                async with fixture.loopback_mcp_client(URL):
                    self.fail("cross-origin redirect accepted")
        self.assert_closed()
        # Auto negotiation may try discover then fallback initialize, but the
        # SDK must never send either request to the different-origin target.
        self.assertTrue(self.requests)
        self.assertTrue(all(request.url.port == 43210 for request in self.requests))
