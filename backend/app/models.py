"""SQLAlchemy ORM models for the Platform API.

Five tables cover this phase: users (admin + members), api_keys (gateway
credentials issued by an admin to a member), stores (provisioning stub),
store_members (who can use which store), chat_tokens (short-lived tokens
the chat agent mints on behalf of a logged-in user).

Tables are created via Base.metadata.create_all() on app startup (see
app/main.py). No Alembic yet — see IMPLEMENTATION.md for when to add it.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint, func, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Role(str, enum.Enum):
    admin = "admin"
    member = "member"


class StoreStatus(str, enum.Enum):
    pending = "pending"
    provisioning = "provisioning"
    ready = "ready"
    failed = "failed"


class User(Base):
    """A partial unique index enforces "at most one admin row, ever" at the
    DB level — two concurrent POST /auth/admin/signup requests (or one
    racing the env-var startup bootstrap) cannot both create an admin, even
    with different emails.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(SAEnum(Role, name="user_role"), default=Role.member)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("uq_users_single_admin", "role", unique=True, postgresql_where=text("role = 'admin'::user_role")),
    )


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

    user: Mapped["User"] = relationship()


class Store(Base):
    """Provisioning stub. Real Kubernetes/Helm provisioning (namespace,
    WordPress+WooCommerce, MCP server registration) is a later phase — this
    just records the intent and hands back a fake URL so the dashboard flow
    can be built and demoed end-to-end now.
    """

    __tablename__ = "stores"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    status: Mapped[StoreStatus] = mapped_column(SAEnum(StoreStatus, name="store_status"), default=StoreStatus.ready)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StoreMember(Base):
    __tablename__ = "store_members"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("store_id", "user_id", name="uq_store_member"),)


class ChatToken(Base):
    """Short-lived token the chat backend mints on behalf of a logged-in user
    so it can call the MCP Gateway without ever handing the user's real
    gateway API key to the LLM/browser layer. Never returned to the client —
    see app/routers/chat.py.
    """

    __tablename__ = "chat_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
