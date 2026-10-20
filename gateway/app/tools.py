"""The two MCP handlers: tools/list and tools/call.

Tool names are `slug__tool`, so two upstreams can both offer `list_orders`
and a client can tell which store a tool acts on. Slugs never contain an
underscore (the backend enforces it), so the first `__` always ends the slug.

Who may use an upstream: one with no store is shared by everyone; one that
belongs to a store is only for callers who have that store. The rule is
applied when listing and again when calling, because a client can call a
name it was never shown.

Which tools of that upstream: the admin may use all of them; a member not those
the upstream lists as admin-only. Same two places, same answer when refused.
"""
import logging
import time
from typing import Any

from mcp import types
from mcp.server import ServerRequestContext
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

from app import limits
from app import upstream as upstream_client
from app.audit import ERROR, INFO, WARNING, audit, chat_ref, describe_call
from app.catalog import catalog
from app.config import settings
from app.registry import Upstream

log = logging.getLogger(__name__)

SEPARATOR = "__"
_SEVERITY = {"ok": INFO, "error": WARNING, "denied": WARNING, "timeout": ERROR, "unreachable": ERROR}


def exposed_name(slug: str, tool: str) -> str:
    return f"{slug}{SEPARATOR}{tool}"


def split_name(name: str) -> tuple[str, str] | None:
    """`woo__list_orders` -> ("woo", "list_orders"); None if it is not a gateway name."""
    slug, separator, tool = name.partition(SEPARATOR)
    return (slug, tool) if separator and slug and tool else None


def may_use(upstream: Upstream, store_ids: list[str]) -> bool:
    return upstream.store_id is None or upstream.store_id in store_ids


def may_call(upstream: Upstream, tool: str, role: str) -> bool:
    return role == "admin" or tool not in upstream.admin_only_tools


def _caller() -> AccessToken:
    caller = get_access_token()
    if caller is None:  # the bearer middleware should have refused the request already
        raise PermissionError("unauthenticated request reached a handler")
    return caller


def _error(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=True)


async def list_tools(ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
    claims = _caller().claims
    snapshot = catalog.snapshot
    return types.ListToolsResult(
        tools=[
            tool.model_copy(update={"name": exposed_name(slug, tool.name)})
            for slug, tools in snapshot.tools.items()
            if may_use(snapshot.upstreams[slug], claims["store_ids"])
            for tool in tools
            if may_call(snapshot.upstreams[slug], tool.name, claims["role"])
        ]
    )


async def _route(caller: AccessToken, params: types.CallToolRequestParams) -> tuple[str, types.CallToolResult]:
    """Does the call. Returns how it ended (a key of _SEVERITY) and what the caller gets."""
    parts = split_name(params.name)
    upstream = catalog.snapshot.upstreams.get(parts[0]) if parts else None
    if (
        parts is None
        or upstream is None
        or not may_use(upstream, caller.claims["store_ids"])
        or not may_call(upstream, parts[1], caller.claims["role"])
    ):
        # One answer for "no such tool", "not yours" and "admins only", so a caller cannot map other stores' servers or locks.
        return "denied", _error(f"Unknown tool: {params.name}")
    try:
        result = await upstream_client.call_tool(upstream, parts[1], params.arguments or {})
    except TimeoutError:
        return "timeout", _error(f"{upstream.slug} did not answer within {settings.call_timeout_seconds:g} seconds")
    except Exception as exc:  # connection refused, bad response, upstream crash: all "unreachable" to the caller
        log.warning("call to %s failed: %s", params.name, type(exc).__name__)
        return "unreachable", _error(f"{upstream.slug} is unreachable")
    return ("error" if result.is_error else "ok"), result


async def call_tool(ctx: ServerRequestContext[Any], params: types.CallToolRequestParams) -> types.CallToolResult:
    caller = _caller()
    if not await limits.take(f"ratelimit:gateway:calls:{caller.subject}", settings.calls_per_minute):
        if await limits.once(f"ratelimit:gateway:calls-noted:{caller.subject}"):  # one row a minute, not one per refused call
            await audit.record(
                WARNING, "tool_call", "call", "rate_limited",
                user_id=caller.subject, token_type=caller.claims["token_type"], limit=settings.calls_per_minute,
            )
        return _error(f"Rate limit: at most {settings.calls_per_minute} tool calls a minute. Try again in a moment.")
    if not await audit.usable():
        return _error("The audit log is unavailable, so no tool can be called right now")
    started = time.monotonic()
    outcome, result = await _route(caller, params)
    slug, _, tool = params.name.partition(SEPARATOR)
    # The caller gets one answer for every refusal; the log tells them apart: a server that exists is named here.
    known = slug in catalog.snapshot.upstreams
    await audit.record(
        _SEVERITY[outcome],
        "tool_call",
        "call",
        outcome,
        user_id=caller.subject,
        upstream=slug if known else None,
        tool=(tool if known else params.name)[:200],
        duration_ms=round((time.monotonic() - started) * 1000),
        token_type=caller.claims["token_type"],
        # Only the backend's chat agent can vouch for a conversation; with an API key these headers mean nothing.
        **(chat_ref.get() if caller.claims["token_type"] == "chat_token" else {}),
        **describe_call(params.arguments or {}, outcome, getattr(result.content[0], "text", None) if result.content else None),
    )
    return result
