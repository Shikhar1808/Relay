import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class UpstreamServer(Base):
    """An MCP server the gateway forwards to. The admin registers it here; the
    gateway reads this table (read-only) and prefixes every tool it exposes
    with the slug, as `slug__tool`.

    store_id decides who may use it: NULL is shared by everyone, a value
    limits it to callers who have that store (see /internal/auth/resolve).
    The credential is stored only as AES-256-GCM ciphertext (app/core/crypto.py)
    and is never returned by any API.
    """

    __tablename__ = "upstream_servers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    url: Mapped[str] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    credential_header: Mapped[str] = mapped_column(String(100), default="Authorization")
    credential_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
