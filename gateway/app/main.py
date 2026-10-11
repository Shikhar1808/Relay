"""Builds the two ASGI apps and runs them.

Public app (PORT): /mcp only. Private app (PRIVATE_PORT): health checks, for
the container runtime and the cluster. Keeping them on separate ports means an
Ingress that exposes the gateway cannot expose anything but MCP.

Run with `python -m app`.
"""
import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

import asyncpg
import httpx2
import uvicorn
from mcp.server import Server
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from redis.asyncio import from_url
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from app.auth import BackendResolve, Verifier
from app.catalog import catalog
from app.config import settings
from app.tools import call_tool, list_tools

log = logging.getLogger(__name__)

# One HTTP client for token resolution, shared by every request.
_resolve_http = httpx2.AsyncClient(timeout=httpx2.Timeout(5))

server = Server("urumi-gateway", on_list_tools=list_tools, on_call_tool=call_tool)
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
    redis = from_url(settings.redis_url, decode_responses=True)
    refresher = asyncio.create_task(catalog.run(pool, redis))
    try:
        # A mounted app's own lifespan never runs, so its session manager is started here.
        async with server.session_manager.run():
            yield
    finally:
        refresher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await refresher
        await _resolve_http.aclose()
        await redis.aclose()
        await pool.close()


public_app = Starlette(routes=[Mount("/", app=_mcp_app)], lifespan=_lifespan)


async def _healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def _readyz(request: Request) -> JSONResponse:
    # Not ready until the registry has been read once: before that tools/list would be empty.
    if not catalog.loaded:
        return JSONResponse({"status": "starting"}, status_code=503)
    snapshot = catalog.snapshot
    return JSONResponse({"status": "ok", "upstreams": len(snapshot.upstreams), "reachable": len(snapshot.tools)})


private_app = Starlette(routes=[Route("/healthz", _healthz), Route("/readyz", _readyz)])


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
