"""Shared FastAPI dependencies: DB session, Redis client, current-user
resolution from the dashboard JWT, admin gate, and rate-limit dependency
factories.

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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Role, User
from app.rate_limit import check_token_bucket
from app.security import decode_access_token

_bearer = HTTPBearer()
_redis_client: Redis = from_url(settings.redis_url, decode_responses=True)


async def get_redis() -> AsyncGenerator[Redis, None]:
    yield _redis_client


async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    try:
        payload = decode_access_token(creds.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token payload") from exc

    user = await db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    return user


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
        key = f"ratelimit:user:{user.id}"
        allowed, remaining = await check_token_bucket(redis, key, capacity, refill_per_sec)
        if not allowed:
            retry_after = _retry_after(remaining, 1, refill_per_sec)
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )

    return dependency
