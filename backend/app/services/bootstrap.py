"""Single source of truth for creating the one and only admin account.

Two entry points share this, both enforcing "at most one admin, ever":
  - On startup, if ADMIN_EMAIL/ADMIN_PASSWORD are set (see app/core/config.py),
    create the admin automatically — convenient for docker-compose/local dev.
  - POST /auth/admin/signup (app/api/routes/auth.py) — for deployments that
    don't want credentials sitting in env vars; works once, then always 409s.

Both are followed by registering the first store (create_first_store_if_absent),
which needs that admin as the store's creator.
"""
import logging
import uuid

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import Role, Store, StoreStatus, UpstreamServer, User
from app.db.session import AsyncSessionLocal
from app.services.admin_audit import record
from app.services.stores import check_subdomain

log = logging.getLogger("uvicorn.error")  # the logger uvicorn already prints


async def create_admin_if_absent(db: AsyncSession, email: str, name: str, password: str) -> User | None:
    """Returns the created admin, or None if an admin already exists.

    The pre-check below is a fast path, not the real guard — two concurrent
    callers can both pass it. The `users.uq_users_single_admin` partial
    unique index (app/db/models/user.py) is the actual guard; a loser of that race
    hits IntegrityError here and gets treated the same as "already exists".
    """
    result = await db.execute(select(User).where(User.role == Role.admin))
    if result.scalar_one_or_none() is not None:
        return None

    admin = User(email=email, name=name, password_hash=hash_password(password), role=Role.admin)
    db.add(admin)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return None
    await db.refresh(admin)
    return admin


async def bootstrap_admin() -> None:
    if not settings.admin_email or not settings.admin_password:
        return  # no env-var admin configured — use POST /auth/admin/signup instead

    async with AsyncSessionLocal() as db:
        await create_admin_if_absent(db, settings.admin_email, "Admin", settings.admin_password)


async def create_first_store_if_absent(db: AsyncSession, admin: User, request: Request | None = None) -> Store | None:
    """Registers the first store and its WooCommerce MCP server, so the dashboard
    opens with one server already there. Returns the store, or None if there
    was nothing to do. `request` is the admin's signup when that is what
    registers it; at startup there is none.

    The rows are the ones any later store gets: a store whose slug is its
    subdomain, and an upstream `woo-<subdomain>` that belongs to it, so only the
    admin and that store's members see its tools. A store that already exists
    is never touched again: an admin who edits or removes its server keeps
    that change across restarts.
    """
    if not settings.woo_mcp_url or not settings.woo_mcp_token:
        return None
    subdomain = check_subdomain(settings.first_store_subdomain)  # a bad one stops the start, loudly
    existing = await db.execute(select(Store.id).where(Store.slug == subdomain))
    if existing.scalar_one_or_none() is not None:
        return None

    store = Store(
        id=uuid.uuid4(),  # set here, not by the database, so the upstream can point at it before anything is saved
        name=settings.first_store_name,
        slug=subdomain,
        status=StoreStatus.ready,
        url=settings.first_store_url,
        created_by=admin.id,
    )
    db.add(store)
    db.add(
        UpstreamServer(
            name=settings.first_store_name,
            slug=f"woo-{subdomain}",
            url=settings.woo_mcp_url,
            credential_header="Authorization",
            credential_ciphertext=encrypt(f"Bearer {settings.woo_mcp_token}"),
            store_id=store.id,
        )
    )
    record(db, request, admin, "store.create", subdomain, name=settings.first_store_name, by="startup" if request is None else "signup")
    try:
        await db.commit()
    except IntegrityError:
        # Another replica registered it first, or an upstream is already called woo-<subdomain>.
        await db.rollback()
        log.warning("the first store %r was not registered: the store or a server woo-%s already exists", subdomain, subdomain)
        return None
    log.info("registered the first store %r and its MCP server woo-%s", subdomain, subdomain)
    return store


async def bootstrap_first_store() -> None:
    """Needs the admin, as the store's creator. With no admin yet, POST /auth/admin/signup registers the store."""
    if not settings.woo_mcp_url or not settings.woo_mcp_token:
        return
    # Checked here too, admin or no admin: otherwise a bad subdomain would only show up later, as a failed signup.
    check_subdomain(settings.first_store_subdomain)
    async with AsyncSessionLocal() as db:
        admin = (await db.execute(select(User).where(User.role == Role.admin))).scalar_one_or_none()
        if admin is not None:
            await create_first_store_if_absent(db, admin)
