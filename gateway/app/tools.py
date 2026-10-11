"""The two MCP handlers: tools/list and tools/call.

Tool names are `slug__tool`, so two upstreams can both offer `list_orders`
and a client can tell which store a tool acts on. Slugs never contain an
underscore (the backend enforces it), so the first `__` always ends the slug.

Who may use an upstream: one with no store is shared by everyone; one that
belongs to a store is only for callers who have that store. The rule is
applied when listing and again when calling, because a client can call a
name it was never shown.
"""
import logging
from typing import Any

from mcp import types
from mcp.server import ServerRequestContext
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

from app import upstream as upstream_client
from app.catalog import catalog
from app.config import settings
from app.registry import Upstream

log = logging.getLogger(__name__)

SEPARATOR = "__"


def exposed_name(slug: str, tool: str) -> str:
    return f"{slug}{SEPARATOR}{tool}"


def split_name(name: str) -> tuple[str, str] | None:
    """`woo__list_orders` -> ("woo", "list_orders"); None if it is not a gateway name."""
    slug, separator, tool = name.partition(SEPARATOR)
    return (slug, tool) if separator and slug and tool else None


def may_use(upstream: Upstream, store_ids: list[str]) -> bool:
    return upstream.store_id is None or upstream.store_id in store_ids


def _caller() -> AccessToken:
    caller = get_access_token()
    if caller is None:  # the bearer middleware should have refused the request already
        raise PermissionError("unauthenticated request reached a handler")
    return caller


def _error(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=True)


async def list_tools(ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
    store_ids = _caller().claims["store_ids"]
    snapshot = catalog.snapshot
    return types.ListToolsResult(
        tools=[
            tool.model_copy(update={"name": exposed_name(slug, tool.name)})
            for slug, tools in snapshot.tools.items()
            if may_use(snapshot.upstreams[slug], store_ids)
            for tool in tools
        ]
    )


async def call_tool(ctx: ServerRequestContext[Any], params: types.CallToolRequestParams) -> types.CallToolResult:
    caller = _caller()
    parts = split_name(params.name)
    upstream = catalog.snapshot.upstreams.get(parts[0]) if parts else None
    if parts is None or upstream is None or not may_use(upstream, caller.claims["store_ids"]):
        # One answer for "no such tool" and "not yours", so a caller cannot map other stores' servers.
        return _error(f"Unknown tool: {params.name}")
    try:
        return await upstream_client.call_tool(upstream, parts[1], params.arguments or {})
    except TimeoutError:
        return _error(f"{upstream.slug} did not answer within {settings.call_timeout_seconds:g} seconds")
    except Exception as exc:  # connection refused, bad response, upstream crash: all "unreachable" to the caller
        log.warning("call to %s failed: %s", params.name, type(exc).__name__)
        return _error(f"{upstream.slug} is unreachable")
