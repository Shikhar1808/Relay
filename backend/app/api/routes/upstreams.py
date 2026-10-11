"""Admin-only registry of the MCP servers the gateway forwards to.

The gateway reads the upstream_servers table directly (read-only) and
decrypts credentials itself; nothing here ever returns a credential. Every
change publishes `upstreams:changed` on Redis so the gateway reloads at once
instead of waiting for its periodic refresh.

Every change is written to the gateway's audit log before it is committed
(who, which server, what was sent). If that fails, the change is refused.
"""
import contextlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt
from app.core.deps import get_redis, require_admin, user_rate_limit
from app.db.models import Store, UpstreamServer, User
from app.db.session import get_db
from app.schemas.upstreams import CreateUpstreamRequest, UpdateUpstreamRequest, UpstreamOut
from app.services.gateway_audit import record_admin_event

UPSTREAMS_CHANGED = "upstreams:changed"

router = APIRouter(
    prefix="/upstream-servers",
    tags=["upstream-servers"],
    dependencies=[Depends(require_admin), Depends(user_rate_limit(capacity=60, refill_per_minute=60))],
)


def _out(server: UpstreamServer) -> UpstreamOut:
    return UpstreamOut(
        id=server.id,
        name=server.name,
        slug=server.slug,
        url=server.url,
        enabled=server.enabled,
        credential_header=server.credential_header,
        has_credential=server.credential_ciphertext is not None,
        store_id=server.store_id,
        created_at=server.created_at,
    )


def _changes(body: BaseModel) -> dict:
    """What the admin sent, for the audit log. The credential itself never leaves this process."""
    sent = body.model_dump(mode="json", exclude_unset=True)
    if "credential" in sent:
        sent["credential"] = "set" if sent["credential"] else "none"
    return sent


async def _record_and_commit(db: AsyncSession, action: str, admin: User, request: Request, slug: str, changes: dict) -> None:
    """Records the change in the audit log, then commits it. Raises 503 if it cannot be recorded."""
    await record_admin_event(action, admin, request, slug, changes)
    try:
        await db.commit()
    except Exception:
        # The log now says this change was made, and its rows cannot be taken back: add the correction.
        # Best effort: if that fails too, the error the admin gets is still the commit's.
        with contextlib.suppress(HTTPException):
            await record_admin_event(action, admin, request, slug, changes, outcome="failed")
        raise


async def _require_store(db: AsyncSession, store_id: uuid.UUID | None) -> None:
    if store_id is not None and await db.get(Store, store_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")


async def _get(db: AsyncSession, server_id: uuid.UUID) -> UpstreamServer:
    server = await db.get(UpstreamServer, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upstream server not found")
    return server


@router.get("", response_model=list[UpstreamOut])
async def list_upstreams(db: AsyncSession = Depends(get_db)) -> list[UpstreamOut]:
    result = await db.execute(select(UpstreamServer).order_by(UpstreamServer.created_at))
    return [_out(server) for server in result.scalars().all()]


@router.post("", response_model=UpstreamOut, status_code=status.HTTP_201_CREATED)
async def create_upstream(
    body: CreateUpstreamRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> UpstreamOut:
    await _require_store(db, body.store_id)
    # shortcut: only the scheme is checked (HttpUrl). No SSRF filter, because
    # upstreams legitimately live on cluster-internal addresses and only the
    # admin can add one. Add an allowlist before anyone else can register servers.
    server = UpstreamServer(
        name=body.name,
        slug=body.slug,
        url=str(body.url),
        enabled=body.enabled,
        credential_header=body.credential_header,
        credential_ciphertext=encrypt(body.credential) if body.credential else None,
        store_id=body.store_id,
    )
    db.add(server)
    try:
        await db.flush()  # the duplicate-slug check, before anything is recorded
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "An upstream server with this slug already exists") from exc
    await _record_and_commit(db, "upstream.create", admin, request, server.slug, _changes(body))
    await db.refresh(server)
    await redis.publish(UPSTREAMS_CHANGED, server.slug)
    return _out(server)


@router.patch("/{server_id}", response_model=UpstreamOut)
async def update_upstream(
    server_id: uuid.UUID,
    body: UpdateUpstreamRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> UpstreamOut:
    server = await _get(db, server_id)
    changes = body.model_dump(exclude_unset=True)
    if "store_id" in changes:
        await _require_store(db, changes["store_id"])
    if changes.get("url") is not None:
        changes["url"] = str(changes["url"])
        # The gateway sends the stored credential to whatever the URL is. Without this,
        # anyone with an admin session could point a server at their own address and
        # collect a credential they were never shown.
        if changes["url"] != server.url and server.credential_ciphertext is not None and "credential" not in changes:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                'Changing the URL of a server that has a credential needs the credential again (or "" to remove it)',
            )
    if "credential" in changes:
        credential = changes.pop("credential")
        server.credential_ciphertext = encrypt(credential) if credential else None
    for field, value in changes.items():
        # name, url, enabled and credential_header are NOT NULL: an explicit null means "leave as is".
        if value is not None or field == "store_id":
            setattr(server, field, value)
    await _record_and_commit(db, "upstream.update", admin, request, server.slug, _changes(body))
    await db.refresh(server)
    await redis.publish(UPSTREAMS_CHANGED, server.slug)
    return _out(server)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_upstream(
    server_id: uuid.UUID,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> None:
    server = await _get(db, server_id)
    await db.delete(server)
    await _record_and_commit(db, "upstream.delete", admin, request, server.slug, {})
    await redis.publish(UPSTREAMS_CHANGED, server.slug)
