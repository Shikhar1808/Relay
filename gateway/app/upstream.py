"""Talks to one upstream MCP server over Streamable HTTP.

A fresh connection per call: measured at about 16 ms through the gateway, and
it means a restarted upstream needs no reconnect logic. The upstream only ever
receives its own stored credential, never the caller's token.
"""
import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2
from mcp import Client, types
from mcp.client.streamable_http import streamable_http_client

from app.config import settings
from app.registry import Upstream


async def _with_client[T](upstream: Upstream, timeout: float, use: Callable[[Client], Awaitable[T]]) -> T:
    async def run() -> T:
        async with httpx2.AsyncClient(headers=upstream.headers, timeout=httpx2.Timeout(timeout)) as http:
            async with Client(streamable_http_client(upstream.url, http_client=http)) as client:
                return await use(client)

    # httpx's timeout is per read; wait_for caps the whole exchange.
    return await asyncio.wait_for(run(), timeout)


async def list_tools(upstream: Upstream) -> list[types.Tool]:
    result = await _with_client(upstream, settings.list_timeout_seconds, lambda client: client.list_tools())
    return list(result.tools)


async def call_tool(upstream: Upstream, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    return await _with_client(
        upstream, settings.call_timeout_seconds, lambda client: client.call_tool(name, arguments)
    )
