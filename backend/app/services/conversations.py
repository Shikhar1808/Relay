"""A user's chats with the agent: the one that is open, and the earlier ones to read.

A chat is open while its chat token is live. The token is what the agent shows
the gateway to act as this user, so ending a chat and revoking its token are
the same thing: the member ends it, signs out, or leaves it idle for
CHAT_TOKEN_TTL_MINUTES (every message pushes that out again). Until then no
second chat is opened.

An ended chat can be read but not continued. It is deleted, with everything
said in it, once it has not been used for CHAT_RETENTION_DAYS.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone

from cryptography.exceptions import InvalidTag
from langgraph.checkpoint.base import BaseCheckpointSaver
from redis.asyncio import Redis
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt, encrypt
from app.core.security import generate_chat_token
from app.db.models import ChatToken, Conversation, User
from app.db.session import AsyncSessionLocal

log = logging.getLogger("uvicorn.error")  # the logger uvicorn already prints

_TITLE_CHARS = 120


async def current_chat(db: AsyncSession, user: User) -> tuple[Conversation, ChatToken] | None:
    """The user's open chat and its token, if they have one."""
    found = await db.execute(
        select(Conversation, ChatToken)
        .join(ChatToken, Conversation.chat_token_id == ChatToken.id)
        .where(
            Conversation.user_id == user.id,
            ChatToken.revoked.is_(False),
            ChatToken.expires_at > datetime.now(timezone.utc),
            ChatToken.ciphertext.is_not(None),
        )
        .order_by(ChatToken.created_at.desc())
    )
    row = found.first()
    return (row[0], row[1]) if row else None


async def open_chat(db: AsyncSession, redis: Redis, user: User, message: str) -> tuple[Conversation, str]:
    """The user's open chat, or a new one named after `message`, with the chat token to
    present to the gateway. Either way the chat now has the full idle time ahead of it again."""
    # shortcut: this is check-then-insert, so two first messages sent in the same instant can
    # each open a chat (both the user's own; the newer one is the one found afterwards). Add a
    # partial unique index on the live token per user if "one open chat" ever has to be exact.
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.chat_token_ttl_minutes)
    found = await current_chat(db, user)
    if found is not None:
        try:
            raw = decrypt(found[1].ciphertext)
        except InvalidTag:  # GATEWAY_ENCRYPTION_KEY was changed: this chat's token is lost, so the chat is over
            found = None
    if found is not None:
        conversation, token = found
        token.expires_at = expires_at
    else:
        await end_chat(db, redis, user)  # a token from before chats existed must not outlive this one
        raw, token_hash = generate_chat_token()
        token = ChatToken(user_id=user.id, token_hash=token_hash, ciphertext=encrypt(raw), expires_at=expires_at)
        db.add(token)
        await db.flush()  # the conversation needs the token's id
        conversation = Conversation(user_id=user.id, chat_token_id=token.id, title=" ".join(message.split())[:_TITLE_CHARS])
        db.add(conversation)
    conversation.last_used_at = now
    await db.commit()
    # /internal/auth/resolve looks in Redis first; the row above stays the source of truth.
    await redis.set(f"chat_token:{token.token_hash}", str(user.id), ex=settings.chat_token_ttl_minutes * 60)
    return conversation, raw


async def end_chat(db: AsyncSession, redis: Redis, user: User) -> None:
    """Revokes the user's chat tokens, which ends their open chat. Sign-out does this too."""
    revoked = await db.execute(
        update(ChatToken)
        .where(ChatToken.user_id == user.id, ChatToken.revoked.is_(False))
        .values(revoked=True)
        .returning(ChatToken.token_hash)
    )
    hashes = revoked.scalars().all()
    await db.commit()
    if hashes:
        # /internal/auth/resolve looks here first, so the cached copies must go too.
        await redis.delete(*(f"chat_token:{token_hash}" for token_hash in hashes))


async def past_chats(db: AsyncSession, user: User) -> list[Conversation]:
    """The user's chats that are over, newest first."""
    found = await current_chat(db, user)
    query = select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.last_used_at.desc())
    if found is not None:
        query = query.where(Conversation.id != found[0].id)
    return list((await db.execute(query)).scalars())


async def past_chat(db: AsyncSession, user: User, conversation_id: uuid.UUID) -> Conversation | None:
    """One of the user's own chats, or None: somebody else's chat is as absent as one that never existed."""
    conversation = await db.get(Conversation, conversation_id)
    return conversation if conversation is not None and conversation.user_id == user.id else None


async def purge(checkpointer: BaseCheckpointSaver) -> int:
    """Deletes every chat not used for CHAT_RETENTION_DAYS: first what was said (the agent's saved
    state), then its row, so a failure in between leaves a row that the next run finds again."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.chat_retention_days)
    async with AsyncSessionLocal() as db:
        old = (await db.execute(select(Conversation).where(Conversation.last_used_at < cutoff))).scalars().all()
        for conversation in old:
            await checkpointer.adelete_thread(str(conversation.id))
            await db.delete(conversation)
            await db.commit()
        # The tokens of chats that are over open nothing any more, so their rows (and the encrypted tokens in them) go too.
        await db.execute(delete(ChatToken).where(or_(ChatToken.revoked.is_(True), ChatToken.expires_at < now)))
        await db.commit()
    if old:
        log.info("deleted %d chats not used since %s", len(old), cutoff.isoformat(timespec="minutes"))
    return len(old)
