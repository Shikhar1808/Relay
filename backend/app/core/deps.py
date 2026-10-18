"""Shared FastAPI dependencies: DB session, Redis client, current-user
resolution from the dashboard JWT, admin gate, and rate-limit dependency
factories.

The dashboard JWT reaches this API in one of two ways. A browser sends it as
a cookie that page scripts cannot read (set by /auth/login). Scripts and tests
send it as an `Authorization: Bearer` header.

FastAPI caches a dependency's result per request by default (same callable +
params), so get_current_user only hits the DB once per request even though
several routes/dependencies depend on it.
"""
import uuid
from collections.abc import AsyncGenerator

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis, from_url
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.rate_limit import check_token_bucket
from app.core.security import decode_access_token, sha256_hex
from app.db.models import Role, User
from app.db.session import get_db

SESSION_COOKIE = "urumi_session"
_READ_ONLY = {"GET", "HEAD", "OPTIONS"}
_bearer = HTTPBearer(auto_error=False)
# Connecting is retried for about two seconds: right after Redis restarts, its new pod takes a
# moment to be reachable, and without this the first request in that moment fails with a 500.
_redis_client: Redis = from_url(
    settings.redis_url,
    decode_responses=True,
    retry=Retry(ExponentialBackoff(cap=0.5, base=0.05), 6),
    retry_on_error=[RedisConnectionError, RedisTimeoutError, OSError],  # OSError: a refused connection
)


async def get_redis() -> AsyncGenerator[Redis, None]:
    yield _redis_client


def session_token(request: Request, creds: HTTPAuthorizationCredentials | None) -> str:
    """The caller's dashboard JWT, from the header or the cookie."""
    if creds is not None:
        return creds.credentials  # no other site can make a browser send this header
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    # A browser attaches the cookie by itself, so a change must be shown to come from our own
    # pages (CSRF). Browsers name the sending page in Origin on every request that is not a plain
    # read; a caller that sends no Origin is not a browser. SameSite=Strict on the cookie is the
    # first defence, this is the second.
    origin = request.headers.get("origin")
    if request.method not in _READ_ONLY and origin is not None and origin not in settings.allowed_origins:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This request did not come from the dashboard")
    return token


def revoked_session_key(token: str) -> str:
    """Where Redis remembers a signed-out session, until its token would have expired anyway."""
    return f"revoked_session:{sha256_hex(token)}"


async def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> User:
    token = session_token(request, creds)
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc
    if await redis.exists(revoked_session_key(token)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Signed out")

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token payload") from exc

    user = await db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    request.state.session = (token, payload["exp"])  # sign out revokes exactly this token
    return user


async def get_optional_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> User | None:
    """The signed-in user, or None when nobody is. For the few routes that answer either way."""
    try:
        return await get_current_user(request, creds, db, redis)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_401_UNAUTHORIZED:
            raise  # a change refused for coming from another site stays refused
        return None


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return user


def _retry_after(remaining: float, cost: float, refill_per_sec: float) -> int:
    deficit = max(0.0, cost - remaining)
    return max(1, int(deficit / refill_per_sec) + 1)


def ip_rate_limit(capacity: int, refill_per_minute: float):
    """Gate by client IP. Use on public, pre-auth endpoints (login)."""
    refill_per_sec = refill_per_minute / 60

    async def dependency(request: Request, redis: Redis = Depends(get_redis)) -> None:
        client_ip = request.client.host if request.client else "unknown"
        key = f"ratelimit:ip:{client_ip}:{request.url.path}"
        allowed, remaining = await check_token_bucket(redis, key, capacity, refill_per_sec)
        if not allowed:
            retry_after = _retry_after(remaining, 1, refill_per_sec)
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )

    return dependency


def user_rate_limit(capacity: int, refill_per_minute: float):
    """Gate by authenticated user id. Use on dashboard/API endpoints."""
    refill_per_sec = refill_per_minute / 60

    async def dependency(
        user: User = Depends(get_current_user),
        redis: Redis = Depends(get_redis),
    ) -> None:
        # Capacity and rate are in the key so each limiter (chat vs dashboard) has its own bucket.
        key = f"ratelimit:user:{user.id}:{capacity}:{refill_per_minute}"
        allowed, remaining = await check_token_bucket(redis, key, capacity, refill_per_sec)
        if not allowed:
            retry_after = _retry_after(remaining, 1, refill_per_sec)
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )

    return dependency


# The one limit every signed-in route shares: 60 requests a minute for each user, across the whole API.
# Routes that cost more (the chat, for its model calls) add a stricter one of their own on top.
common_rate_limit = user_rate_limit(capacity=60, refill_per_minute=60)
