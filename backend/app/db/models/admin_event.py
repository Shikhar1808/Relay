import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Identity, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class AdminEvent(Base):
    """One change to the team, the API keys or the stores: who made it, to
    what, from where. Written in the same transaction as the change (see
    app/services/admin_audit.py), so the two are saved together or not at all.

    Append-only: a trigger refuses UPDATE, DELETE and TRUNCATE (migration 0005).
    Changes to the upstream registry are not here; they go to the gateway's
    audit log, next to the tool calls they affect.
    """

    __tablename__ = "admin_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Not foreign keys, and the email is copied: a row must read the same whatever later happens to the user.
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    actor_email: Mapped[str] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(100))  # member.create, api_key.revoke, store.member_add, ...
    target: Mapped[str | None] = mapped_column(String(320), nullable=True)  # a member's email or a store's slug
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
