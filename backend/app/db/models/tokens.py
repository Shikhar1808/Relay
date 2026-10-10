import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class TokenPurpose(str, enum.Enum):
    invite = "invite"
    reset = "reset"


class AuthToken(Base):
    """Single-use token behind an emailed set-password link (member invite or
    password reset). Only the sha256 hash is stored. A row existing means the
    token is unused: it is deleted when redeemed, when a newer one of the same
    purpose is issued, and when the user's password changes any other way —
    see app/services/auth_tokens.py.
    """

    __tablename__ = "auth_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[TokenPurpose] = mapped_column(SAEnum(TokenPurpose, name="auth_token_purpose"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatToken(Base):
    """Short-lived token the chat backend mints on behalf of a logged-in user
    so it can call the MCP Gateway without ever handing the user's real
    gateway API key to the LLM/browser layer. Never returned to the client —
    see app/api/routes/chat.py.
    """

    __tablename__ = "chat_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
