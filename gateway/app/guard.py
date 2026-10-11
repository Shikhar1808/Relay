"""Wraps the MCP app: notes each caller's address for the audit log, records
every refused login, and stops an address that keeps failing.

After AUTH_FAILURES_PER_MINUTE refused requests in a minute, an address gets
429 until the minute is over, before its token is even looked at. That caps
how fast tokens can be guessed, and how many audit rows a stranger can cause.

shortcut: the block is by address, so callers behind one NAT (or behind a proxy
that FORWARDED_ALLOW_IPS does not name) share it; count per token prefix too if that bites.
"""
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import limits
from app.audit import NOTICE, WARNING, audit, client_ip
from app.config import settings


class Guard:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self._app(scope, receive, send)
        ip = scope["client"][0] if scope.get("client") else "unknown"
        client_ip.set(ip)
        key = f"ratelimit:gateway:authfail:{ip}"
        limit = settings.auth_failures_per_minute

        if await limits.count(key) >= limit:
            response = JSONResponse(
                {"error": "too_many_failed_attempts"}, status_code=429, headers={"Retry-After": str(limits.WINDOW_SECONDS)}
            )
            return await response(scope, receive, send)

        status = 0

        async def watch(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        await self._app(scope, receive, watch)
        if status == 401:
            failures = await limits.hit(key)
            # The token itself is never recorded.
            await audit.record(WARNING if failures >= limit else NOTICE, "auth", "authenticate", "rejected", failures=failures)
