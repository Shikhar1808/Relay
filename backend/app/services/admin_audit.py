"""Records changes to the team, the API keys and the stores in the Platform
DB's admin_events table (app/db/models/admin_event.py).

record() only adds the row to the caller's session. The caller's own commit
then saves the change and its record together, or neither. So call it before
that commit, and never commit here.
"""
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdminEvent, User


def record(db: AsyncSession, request: Request, actor: User, action: str, target: str | None = None, **detail: Any) -> None:
    """`detail` must never hold a secret: no raw key, no password, no token."""
    db.add(
        AdminEvent(
            actor_id=actor.id,
            actor_email=actor.email,
            action=action,
            target=target,
            detail=detail or None,
            client_ip=request.client.host if request.client else None,
        )
    )
