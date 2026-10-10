import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class ApiKey(Base):
    """A gateway API key an admin issues to a member. Only the sha256 hash is
    stored — the raw key is shown once in the create response and never again.
    This is what a member pastes into Claude Code / Cursor / MCP Inspector
    once the MCP Gateway exists.

    A partial unique index enforces "at most one active key per user" at the
    DB level (not just in the route's pre-check) — two concurrent issue
    requests for the same user cannot both succeed.
    """

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(16))  # short, safe-to-display fragment e.g. "umk_ab12cd34"
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("uq_api_keys_one_active_per_user", "user_id", unique=True, postgresql_where=text("NOT revoked")),
    )
