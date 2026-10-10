"""Issue and redeem the single-use tokens behind emailed set-password links
(member invites and password resets). Only the sha256 hash is persisted —
the raw token exists in the email and nowhere else.
"""
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import sha256_hex
from app.db.models import AuthToken, TokenPurpose


async def issue_auth_token(db: AsyncSession, user_id: uuid.UUID, purpose: TokenPurpose) -> str:
    """Returns the raw token. Replaces any earlier unused token of the same
    purpose, so only the most recently emailed link works.
    """
    if purpose is TokenPurpose.invite:
        ttl = timedelta(hours=settings.invite_token_ttl_hours)
    else:
        ttl = timedelta(minutes=settings.reset_token_ttl_minutes)

    raw = secrets.token_urlsafe(32)
    await db.execute(delete(AuthToken).where(AuthToken.user_id == user_id, AuthToken.purpose == purpose))
    db.add(
        AuthToken(
            user_id=user_id,
            purpose=purpose,
            token_hash=sha256_hex(raw),
            expires_at=datetime.now(timezone.utc) + ttl,
        )
    )
    await db.commit()
    return raw


async def consume_auth_token(db: AsyncSession, raw: str) -> uuid.UUID | None:
    """Returns the owning user's id, or None if the token is unknown, already
    used, or expired. Does NOT commit — the caller commits it together with
    the password change, so a failure there leaves the token usable.

    A single DELETE ... RETURNING is what makes this single-use: two
    concurrent requests with the same token cannot both get a row back.
    """
    result = await db.execute(
        delete(AuthToken)
        .where(AuthToken.token_hash == sha256_hex(raw), AuthToken.expires_at > datetime.now(timezone.utc))
        .returning(AuthToken.user_id)
    )
    return result.scalar_one_or_none()
