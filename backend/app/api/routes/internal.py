"""Service-to-service token resolution. This is what the MCP Gateway (a
later phase) calls to turn a bearer token — either a member's gateway API
key or a chat backend's short_chat_token — into a user identity, so it can
attribute a tool call and check the caller's role — and into the stores
that caller may touch (store_ids), which is how a member is kept out of
every other store's data whichever MCP client or chat they come through.

No dashboard-JWT auth on this router: it is called by other backend
services, not by browsers or MCP clients. Every request must carry the
shared INTERNAL_SERVICE_TOKEN as a bearer token, so someone who can merely
reach the backend cannot use this route to test stolen or guessed tokens.
In the cluster a NetworkPolicy limits callers as well (see system design notes).
"""
import hmac
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_redis
from app.core.security import sha256_hex
from app.db.models import ApiKey, ChatToken, User
from app.db.session import get_db
from app.schemas.internal import ResolveTokenOut, ResolveTokenRequest
from app.services.stores import stores_for_user

_bearer = HTTPBearer(auto_error=False)


async def require_service_token(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    # compare_digest: a plain == would leak how many leading characters matched.
    if creds is None or not hmac.compare_digest(creds.credentials, settings.internal_service_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Service token required")


router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(require_service_token)])


async def _resolved(db: AsyncSession, user: User, token_type: str) -> ResolveTokenOut:
    stores = await stores_for_user(db, user)
    return ResolveTokenOut(
        user_id=user.id, role=user.role, token_type=token_type, store_ids=[store.id for store in stores]
    )


@router.post("/auth/resolve", response_model=ResolveTokenOut)
async def resolve_token(
    body: ResolveTokenRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> ResolveTokenOut:
    token_hash = sha256_hex(body.token)

    result = await db.execute(select(ApiKey).where(ApiKey.key_hash == token_hash, ApiKey.revoked.is_(False)))
    api_key = result.scalar_one_or_none()
    if api_key is not None:
        user = await db.get(User, api_key.user_id)
        if user and user.is_active:
            api_key.last_used_at = datetime.now(timezone.utc)
            await db.commit()
            return await _resolved(db, user, "api_key")

    cached_user_id = await redis.get(f"chat_token:{token_hash}")
    if cached_user_id:
        user = await db.get(User, uuid.UUID(cached_user_id))
        if user and user.is_active:
            return await _resolved(db, user, "chat_token")

    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(ChatToken).where(ChatToken.token_hash == token_hash, ChatToken.revoked.is_(False))
    )
    chat_token = result.scalar_one_or_none()
    if chat_token is not None and chat_token.expires_at > now:
        user = await db.get(User, chat_token.user_id)
        if user and user.is_active:
            return await _resolved(db, user, "chat_token")

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid, expired, or revoked token")
