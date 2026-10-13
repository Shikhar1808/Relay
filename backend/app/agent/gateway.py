"""The agent's two calls to the MCP gateway, made the way any MCP client makes them.

The agent presents the signed-in user's chat token, so the gateway lists and
allows exactly what that user may use and records each call under their name.
A fresh connection per call, as the gateway itself does towards its upstreams.
"""
import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2
from mcp import Client, types
from mcp.client.streamable_http import streamable_http_client

from app.core.config import settings

# Above the gateway's own 30 s limit on a tool call, so its clear error ("did not answer within...") arrives first.
TIMEOUT_SECONDS = 40


def headers(chat_token: str, chat_id: str, turn: str) -> dict[str, str]:
    """Who is calling, and which chat and message the call belongs to (the gateway notes both in its audit log)."""
    return {"Authorization": f"Bearer {chat_token}", "X-Chat-Id": chat_id, "X-Chat-Turn": turn}


async def _with_client[T](sent: dict[str, str], use: Callable[[Client], Awaitable[T]]) -> T:
    async def run() -> T:
        async with httpx2.AsyncClient(headers=sent, timeout=httpx2.Timeout(TIMEOUT_SECONDS)) as http:
            async with Client(streamable_http_client(settings.gateway_mcp_url, http_client=http)) as client:
                return await use(client)

    # httpx's timeout is per read; wait_for caps the whole exchange.
    return await asyncio.wait_for(run(), TIMEOUT_SECONDS)


async def list_tools(sent: dict[str, str]) -> list[types.Tool]:
    return list((await _with_client(sent, lambda client: client.list_tools())).tools)


async def call_tool(sent: dict[str, str], name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    return await _with_client(sent, lambda client: client.call_tool(name, arguments))
