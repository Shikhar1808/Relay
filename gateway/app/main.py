"""Builds the two ASGI apps and runs them.

Public app (PORT): /mcp only. Private app (PRIVATE_PORT): health checks, for
the container runtime and the cluster, and the audit log for the backend
(service token required). Keeping them on separate ports means an Ingress
that exposes the gateway cannot expose anything but MCP.

Run with `python -m app`.
"""
import asyncio
import contextlib
import hmac
import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

import asyncpg
import httpx2
import uvicorn
from mcp.server import Server
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl, BaseModel, Field, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from app import limits
from app.audit import INFO, NOTICE, WARNING, audit, client_ip
from app.auth import BackendResolve, Verifier
from app.catalog import catalog
from app.config import settings
from app.guard import Guard
from app.tools import call_tool, list_tools

log = logging.getLogger(__name__)

# One HTTP client for token resolution, shared by every request.
_resolve_http = httpx2.AsyncClient(timeout=httpx2.Timeout(5))

server = Server("relay-gateway", on_list_tools=list_tools, on_call_tool=call_tool)
_mcp_app = server.streamable_http_app(
    # No session lives in this process, so any replica can answer any request.
    stateless_http=True,
    token_verifier=Verifier([BackendResolve(_resolve_http)]),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(settings.auth_issuer_url),
        resource_server_url=AnyHttpUrl(f"{settings.public_base_url}/mcp"),
        # False until OAuth tokens exist: API keys and chat tokens carry no audience.
        validate_token_resource=False,
    ),
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=settings.allowed_hosts,
        allowed_origins=settings.allowed_origins,
    ),
)


@contextlib.asynccontextmanager
async def _lifespan(app: Starlette) -> AsyncIterator[None]:
    pool = await asyncpg.create_pool(settings.platform_database_url, min_size=1, max_size=4)
    await audit.start()
    await audit.record(INFO, "system", "start", "ok")
    refresher = asyncio.create_task(catalog.run(pool, limits.redis))
    try:
        # A mounted app's own lifespan never runs, so its session manager is started here.
        async with server.session_manager.run():
            yield
    finally:
        refresher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await refresher
        await _resolve_http.aclose()
        await limits.redis.aclose()
        await audit.stop()
        await pool.close()


public_app = Starlette(routes=[Mount("/", app=Guard(_mcp_app))], lifespan=_lifespan)


async def _healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def _readyz(request: Request) -> JSONResponse:
    # Not ready until the registry has been read once: before that tools/list would be empty.
    if not catalog.loaded:
        return JSONResponse({"status": "starting"}, status_code=503)
    snapshot = catalog.snapshot
    return JSONResponse({"status": "ok", "upstreams": len(snapshot.upstreams), "reachable": len(snapshot.tools)})


def _service_token_missing(request: Request) -> JSONResponse | None:
    sent = request.headers.get("authorization", "")
    # compare_digest: a plain == would leak how many leading characters matched.
    if hmac.compare_digest(sent.encode(), f"Bearer {settings.internal_service_token}".encode()):
        return None
    return JSONResponse({"error": "service token required"}, status_code=401)


async def _audit_read(request: Request) -> JSONResponse:
    """The dashboard's view of the log, fetched by the backend. Newest first; `before` pages back."""
    if refusal := _service_token_missing(request):
        return refusal
    query = request.query_params
    try:
        limit = max(1, min(int(query.get("limit", 50)), 200))
        before = int(query["before"]) if "before" in query else None
    except ValueError:
        return JSONResponse({"error": "limit and before must be whole numbers"}, status_code=400)
    return JSONResponse(await audit.read(limit, before, query.get("category"), query.get("severity")))


async def _audit_verify(request: Request) -> JSONResponse:
    """Is the chain intact? Reads every row, so the dashboard asks only when someone presses the button."""
    if refusal := _service_token_missing(request):
        return refusal
    return JSONResponse(await audit.check())


class AdminEvent(BaseModel):
    """What the backend reports when an admin changes something the gateway acts on."""

    action: str = Field(min_length=1, max_length=100)
    # "failed": the backend recorded the change, then could not commit it. The first row stays; this corrects it.
    outcome: Literal["ok", "failed"] = "ok"
    user_id: str | None = Field(None, max_length=100)
    upstream: str | None = Field(None, max_length=100)
    client_ip: str | None = Field(None, max_length=100)
    changes: dict[str, Any] = {}


async def _audit_write(request: Request) -> JSONResponse:
    """Adds an `admin` event for the backend, which cannot write the log itself:
    only the gateway holds the key that signs it. The category is fixed here
    and the severity follows from the outcome, so nothing sent to this route
    can pass for a tool call."""
    if refusal := _service_token_missing(request):
        return refusal
    try:
        event = AdminEvent.model_validate_json(await request.body())
    except ValidationError:
        return JSONResponse({"error": "not a valid admin event"}, status_code=400)
    client_ip.set(event.client_ip)  # the admin's address, as the backend saw it
    detail = {"changes": event.changes} if event.changes else {}
    stored = await audit.record(
        NOTICE if event.outcome == "ok" else WARNING, "admin", event.action, event.outcome,
        user_id=event.user_id, upstream=event.upstream, **detail,
    )
    # 503 tells the backend not to go through with the change: nothing an admin does goes unrecorded.
    return JSONResponse({"stored": stored}, status_code=201 if stored else 503)


private_app = Starlette(
    routes=[
        Route("/healthz", _healthz),
        Route("/readyz", _readyz),
        Route("/internal/audit", _audit_read, methods=["GET"]),
        Route("/internal/audit", _audit_write, methods=["POST"]),
        Route("/internal/audit/verify", _audit_verify, methods=["GET"]),
    ]
)


async def serve() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    private = uvicorn.Server(
        uvicorn.Config(private_app, host="0.0.0.0", port=settings.private_port, log_level="warning", lifespan="off")
    )
    # proxy_headers: behind a TLS-terminating Ingress the client must see https:// URLs.
    # uvicorn only trusts those headers from 127.0.0.1 unless FORWARDED_ALLOW_IPS names the proxy.
    # access_log off: it prints the full URL, so a key sent as ?token=... (refused
    # with 401 anyway) would end up in the logs. The audit log records requests instead.
    public = uvicorn.Server(
        uvicorn.Config(
            public_app, host="0.0.0.0", port=settings.port, proxy_headers=True, log_level="info", access_log=False
        )
    )
    # Started first so the public server, started last, owns SIGTERM/SIGINT;
    # when it stops, the private one is told to stop too.
    private_task = asyncio.create_task(private.serve())
    await asyncio.sleep(0)
    try:
        await public.serve()
    finally:
        private.should_exit = True
        await private_task
