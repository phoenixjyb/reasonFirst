"""Owned HTTP/MCP clients for disposable literal-loopback test listeners only."""
from contextlib import AsyncExitStack, asynccontextmanager
from urllib.parse import urlsplit

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import MCP_DEFAULT_SSE_READ_TIMEOUT, MCP_DEFAULT_TIMEOUT


def _validate_endpoint(url):
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname in {"127.0.0.1", "::1"}
            and parsed.port is not None and 1 <= parsed.port <= 65535
            and parsed.username is None and parsed.password is None
            and not parsed.query and not parsed.fragment
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("fixture_literal_loopback_endpoint_required")


def _new_http_client():
    # Match the installed SDK factory, including its long transport read limit.
    # Each MCP Client's request timeout remains a separate caller option.
    return httpx2.AsyncClient(
        trust_env=False, verify=True, follow_redirects=False,
        timeout=httpx2.Timeout(MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT),
    )


@asynccontextmanager
async def loopback_mcp_client(url, **client_options):
    _validate_endpoint(url)
    # Establish this outer scope before the SDK enters its own task-group
    # scopes. Shield only cleanup, preserving their required LIFO exit order.
    with anyio.CancelScope() as cleanup:
        stack = AsyncExitStack()
        try:
            http = await stack.enter_async_context(_new_http_client())
            transport = streamable_http_client(url, http_client=http)
            # Client owns/enters/closes the supplied transport; the outer stack
            # closes HTTP last, including when negotiation fails or is cancelled.
            client = await stack.enter_async_context(Client(transport, **client_options))
            yield client
        finally:
            cleanup.shield = True
            await stack.aclose()
