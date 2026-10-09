"""Demo chat agent. This is a stub for the real LiteLLM + MCP Gateway tool
loop that comes in a later phase — it proves the short_chat_token mechanics
end to end without a real LLM or gateway behind it.

On every message, the backend mints-or-reuses a short_chat_token for the
logged-in user. The raw token is never returned to the caller — it is held
server-side and is what the (future) MCP Gateway call would use, so the
browser and the LLM provider never see a credential that identifies the
user to the gateway.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.deps import get_current_user, get_redis, user_rate_limit
from app.models import ChatToken, User
from app.schemas import ChatMessageRequest, ChatReplyOut
from app.security import generate_chat_token

router = APIRouter(prefix="/chat", tags=["chat"])

# Stricter than general dashboard traffic: each message would eventually cost
# an LLM call, so it gets its own bucket.
_chat_rate_limit = user_rate_limit(capacity=10, refill_per_minute=10)


async def _get_or_create_chat_token(user: User, db: AsyncSession, redis: Redis) -> ChatToken:
    # shortcut: this is check-then-insert, so two /chat/message calls from the
    # same user within the same instant could each mint a token (both valid,
    # both belong to the same user — no security impact, just a brief window
    # where "one session at a time" isn't exactly true). Unlike the API-key
    # table, a simple partial-unique-index can't fix this cleanly here: reuse
    # depends on expires_at (a moving target), not just revoked. Upgrade to a
    # DB constraint if this ever needs to be exact, not just "effectively one."
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(ChatToken)
        .where(ChatToken.user_id == user.id, ChatToken.revoked.is_(False), ChatToken.expires_at > now)
        .order_by(ChatToken.created_at.desc())
    )
    token = result.scalars().first()
    if token is not None:
        # Re-assert the Redis-side mapping in case Redis was restarted/flushed
        # since this token was minted — DB stays the source of truth.
        ttl = int((token.expires_at - now).total_seconds())
        if ttl > 0:
            await redis.set(f"chat_token:{token.token_hash}", str(user.id), ex=ttl)
        return token

    raw, token_hash = generate_chat_token()
    expires_at = now + timedelta(minutes=settings.chat_token_ttl_minutes)
    token = ChatToken(user_id=user.id, token_hash=token_hash, expires_at=expires_at)
    db.add(token)
    await db.commit()
    await db.refresh(token)

    await redis.set(f"chat_token:{token_hash}", str(user.id), ex=settings.chat_token_ttl_minutes * 60)
    return token


def _demo_agent_reply(message: str) -> str:
    return (
        f'[demo agent] Received: "{message}". '
        "This stub does not call a real LLM or the MCP Gateway yet — it only "
        "proves the short_chat_token is minted and attached to your session. "
        "LiteLLM + MCP Gateway tool-calling replaces this in a later phase."
    )


@router.post("/message", response_model=ChatReplyOut, dependencies=[Depends(_chat_rate_limit)])
async def chat_message(
    body: ChatMessageRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> ChatReplyOut:
    token = await _get_or_create_chat_token(user, db, redis)
    reply = _demo_agent_reply(body.message)
    return ChatReplyOut(reply=reply, chat_token_expires_at=token.expires_at)
