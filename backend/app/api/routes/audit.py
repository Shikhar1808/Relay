"""The audit log, for the dashboard (admin only). Two sources:

GET /audit-log           the gateway's log: tool calls, refused logins, registry
                         changes, upstream health. The rows live in the gateway's
                         own database; this route fetches them over the gateway's
                         private port and adds each user's email.
GET /audit-log/platform  team, API key and store changes, from this database's
                         admin_events table, in the same shape.
GET /audit-log/verify    is the gateway's log untouched (its hash chain intact)?

Lists are newest first; pass the last row's `seq` as `before` for the next page.
"""
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import common_rate_limit, require_admin
from app.db.models import AdminEvent, User
from app.db.session import get_db
from app.schemas.audit import AuditEventOut, ChainCheckOut
from app.services.gateway_audit import read_events, verify_chain

router = APIRouter(
    prefix="/audit-log",
    tags=["audit-log"],
    dependencies=[Depends(require_admin), Depends(common_rate_limit)],
)

_Word = Annotated[str | None, Query(pattern=r"^[A-Za-z_]{1,30}$")]


@router.get("", response_model=list[AuditEventOut])
async def list_audit_events(
    limit: int = Query(50, ge=1, le=200),
    before: int | None = Query(None, ge=1, le=2**62),
    category: _Word = None,
    severity: _Word = None,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    wanted = {"limit": limit, "before": before, "category": category, "severity": severity}
    events = await read_events("?" + urlencode({name: value for name, value in wanted.items() if value is not None}))

    emails: dict[str, str] = {}
    if user_ids := {event["user_id"] for event in events if event["user_id"]}:
        # Compared as text: the log's user_id is a plain string, and a row that is not a UUID must not break the page.
        user_id = cast(User.id, String)
        result = await db.execute(select(user_id, User.email).where(user_id.in_(user_ids)))
        emails = {row[0]: row[1] for row in result.all()}
    return [{**event, "user_email": emails.get(event["user_id"])} for event in events]


@router.get("/platform", response_model=list[AuditEventOut])
async def list_platform_events(
    limit: int = Query(50, ge=1, le=200),
    before: int | None = Query(None, ge=1, le=2**62),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    query = select(AdminEvent).order_by(AdminEvent.id.desc()).limit(limit)
    if before is not None:
        query = query.where(AdminEvent.id < before)
    return [
        {
            "seq": row.id,
            "ts": row.ts,
            "severity": "NOTICE",  # what the gateway's log gives an admin change
            "category": "platform",
            "action": row.action,
            "outcome": "ok",  # a row exists only if the change was committed
            "user_id": str(row.actor_id),
            "user_email": row.actor_email,
            "target": row.target,
            "client_ip": row.client_ip,
            "detail": row.detail,
        }
        for row in (await db.execute(query)).scalars()
    ]


@router.get("/verify", response_model=ChainCheckOut)
async def verify_audit_chain() -> dict[str, Any]:
    return await verify_chain()
