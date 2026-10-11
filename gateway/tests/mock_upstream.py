"""A fake upstream MCP server for integration_test.py (compose profile `test`).

It refuses every request that does not carry `Authorization: Bearer mock-secret`,
so a successful call through the gateway proves the gateway decrypted and sent
the stored credential, and did not forward the caller's own token.

HANG=1 makes tools/list sleep for a minute, to stand in for a stuck server.
"""
import asyncio
import os

from mcp import types
from mcp.server import Server
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import PlainTextResponse

EXPECTED = b"Bearer mock-secret"
HANG = os.environ.get("HANG") == "1"


async def list_tools(ctx, params):
    if HANG:
        await asyncio.sleep(60)
    return types.ListToolsResult(
        tools=[
            types.Tool(name="list_orders", description="List orders (mock)", input_schema={"type": "object", "properties": {}}),
            types.Tool(name="slow", description="Sleeps for a minute", input_schema={"type": "object", "properties": {}}),
        ]
    )


async def call_tool(ctx, params):
    if params.name == "slow":
        await asyncio.sleep(60)
    return types.CallToolResult(content=[types.TextContent(type="text", text=f"{params.name} ok")])


class RequireCredential:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and dict(scope["headers"]).get(b"authorization") != EXPECTED:
            return await PlainTextResponse("unauthorized", 401)(scope, receive, send)
        await self.app(scope, receive, send)


server = Server("mock-upstream", on_list_tools=list_tools, on_call_tool=call_tool)
app = RequireCredential(
    server.streamable_http_app(
        stateless_http=True,
        # The SDK only answers to "localhost" unless told otherwise; in the
        # compose network this server is reached by its service name.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=["mock-upstream:*", "mock-hung:*", "mock-b:*"]
        ),
    )
)
