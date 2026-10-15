"""Shipping one store's orders as MCP tools, over Streamable HTTP at /mcp.

An order moves packing → shipped → delivered, one stage at a time, and gets a
tracking number when it starts. The records are in Postgres, in a database of
their own that gateway/deploy/db-init.sh makes, so this process keeps nothing:
it can be killed, or run as two copies.

This server never talks to the store. The chat agent reads orders with the
store's own MCP server and passes their ids here, and changes the store there,
so every change is made as the signed-in user and is in the gateway's audit
log. Only the gateway may call this server: every request but the health check
must carry MCP_AUTH_TOKEN, and the service has no published port.

Run with `uvicorn app.main:app`.
"""
import contextlib
import hmac
import secrets
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import asyncpg
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from app.config import settings

mcp = MCPServer("fulfilment-mcp", instructions="Shipping for one store's orders: packing, shipped, delivered, with tracking numbers. Order ids are the store's.")

STAGES = ("packing", "shipped", "delivered")
Stage = Literal["packing", "shipped", "delivered"]
Id = Annotated[int, Field(ge=1)]
# A batch per call, not a call per order: every call resends the tools and the chat to the model.
OrderIds = Annotated[list[Id], Field(min_length=1, max_length=50)]

_ZONE = ZoneInfo(settings.timezone)  # a zone that does not exist stops the start, loudly
_COLUMNS = "order_id, stage, tracking_number, date_started, date_updated"

# What each tool says about itself, so the chat asks before a write and knows what is safe to repeat.
_READS = ToolAnnotations(read_only_hint=True, open_world_hint=False)
# Only adds, and a repeat is safe: an order started before keeps its record.
_ADDS = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
# Overwrites the stage; the same stage again changes nothing more.
_OVERWRITES = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)


@contextlib.asynccontextmanager
async def _db() -> AsyncIterator[asyncpg.Connection]:
    """One connection for one tool call. A database that fails is a ToolError, so the model reads why."""
    # shortcut: a connection per call, move to a pool if calls stop being human-paced
    try:
        conn = await asyncpg.connect(settings.database_url, timeout=settings.timeout_seconds, command_timeout=settings.timeout_seconds)
        try:
            yield conn
        finally:
            await conn.close()
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:  # OSError covers refused and timed out
        raise ToolError(f"The fulfilment database failed ({type(exc).__name__})") from exc


def refusals(current: dict[int, str], order_ids: list[int], stage: str) -> list[str]:
    """Why these orders cannot be moved to `stage`; empty when all of them can. One step forward at a
    time; an order already at `stage` stays as it is, so a repeated call changes nothing."""
    reasons = []
    for order_id in order_ids:
        now = current.get(order_id)
        if now is None:
            reasons.append(f"order {order_id} was never started")
        elif STAGES.index(stage) < STAGES.index(now):
            reasons.append(f"order {order_id} is already {now} and cannot go back to {stage}")
        elif STAGES.index(stage) > STAGES.index(now) + 1:
            reasons.append(f"order {order_id} is still {now}, so it cannot be {stage} yet")
    return reasons


def _tracking_number() -> str:
    return "TRK" + secrets.token_hex(5).upper()


def _record(row: Any) -> dict[str, Any]:
    return {
        "order_id": row["order_id"],
        "stage": row["stage"],
        "tracking_number": row["tracking_number"],
        "date_started": row["date_started"].astimezone(_ZONE).isoformat(timespec="seconds"),
        "date_updated": row["date_updated"].astimezone(_ZONE).isoformat(timespec="seconds"),
    }


@mcp.tool(annotations=_ADDS)
async def start_fulfilment(order_ids: OrderIds) -> dict[str, Any]:
    """Start shipping orders, by their ids in the store: each gets a tracking number and the stage "packing".
    Start only orders that are paid and ready to ship (status "processing" in the store).
    An order started before keeps its record, so asking twice never makes two."""
    ids = list(dict.fromkeys(order_ids))
    async with _db() as conn:
        # The primary key settles a repeat, also from two copies of this server at once.
        await conn.executemany(
            "INSERT INTO fulfilments (order_id, tracking_number) VALUES ($1, $2) ON CONFLICT (order_id) DO NOTHING",
            [(order_id, _tracking_number()) for order_id in ids],
        )
        rows = await conn.fetch(f"SELECT {_COLUMNS} FROM fulfilments WHERE order_id = ANY($1::bigint[]) ORDER BY order_id", ids)
    return {"fulfilments": [_record(row) for row in rows]}


@mcp.tool(annotations=_OVERWRITES)
async def advance_fulfilment(order_ids: OrderIds, stage: Literal["shipped", "delivered"]) -> dict[str, Any]:
    """Move started orders on to "shipped", then to "delivered": one step at a time, never back.
    An order already at that stage stays as it is. If any order cannot move, nothing is changed.
    Once an order is delivered, also set it to "completed" in the store."""
    ids = list(dict.fromkeys(order_ids))
    async with _db() as conn, conn.transaction():
        # FOR UPDATE: a second call for the same orders waits here, so both see the stage they change.
        rows = await conn.fetch("SELECT order_id, stage FROM fulfilments WHERE order_id = ANY($1::bigint[]) FOR UPDATE", ids)
        reasons = refusals({row["order_id"]: row["stage"] for row in rows}, ids, stage)
        if reasons:
            raise ToolError("Nothing was changed: " + "; ".join(reasons) + ".")
        await conn.execute(
            "UPDATE fulfilments SET stage = $2, date_updated = now() WHERE order_id = ANY($1::bigint[]) AND stage <> $2", ids, stage
        )
        rows = await conn.fetch(f"SELECT {_COLUMNS} FROM fulfilments WHERE order_id = ANY($1::bigint[]) ORDER BY order_id", ids)
    return {"fulfilments": [_record(row) for row in rows]}


@mcp.tool(annotations=_READS)
async def list_fulfilments(stage: Stage | None = None, order_ids: OrderIds | None = None) -> dict[str, Any]:
    """Orders being shipped, the most recently changed first, 50 at most. Filter by `stage`, by `order_ids`, or both.
    `total` is how many match in all."""
    async with _db() as conn:
        rows = await conn.fetch(
            f"SELECT {_COLUMNS}, count(*) OVER () AS total FROM fulfilments"
            " WHERE ($1::text IS NULL OR stage = $1) AND ($2::bigint[] IS NULL OR order_id = ANY($2))"
            " ORDER BY date_updated DESC, order_id LIMIT 50",
            stage, order_ids,
        )
    return {"fulfilments": [_record(row) for row in rows], "total": rows[0]["total"] if rows else 0}


# ---- the HTTP app ------------------------------------------------------------------

@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


class RequireCredential:
    """Refuses every request but the health check unless it carries the gateway's token."""

    def __init__(self, app) -> None:
        self.app = app
        self.expected = f"Bearer {settings.mcp_auth_token}".encode()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["path"] != "/healthz":
            sent = dict(scope["headers"]).get(b"authorization", b"")
            # compare_digest: a plain == would leak how many leading characters matched.
            if not hmac.compare_digest(sent, self.expected):
                return await PlainTextResponse("unauthorized", 401)(scope, receive, send)
        await self.app(scope, receive, send)


app = RequireCredential(
    mcp.streamable_http_app(
        # No session lives in this process: the gateway opens a fresh connection per call.
        stateless_http=True,
        # The SDK only answers to "localhost" unless told otherwise; the gateway reaches this by its service name.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=settings.allowed_hosts),
    )
)
