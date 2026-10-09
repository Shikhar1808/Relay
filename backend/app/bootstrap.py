"""Single source of truth for creating the one and only admin account.

Two entry points share this, both enforcing "at most one admin, ever":
  - On startup, if ADMIN_EMAIL/ADMIN_PASSWORD are set (see app/config.py),
    create the admin automatically — convenient for docker-compose/local dev.
  - POST /auth/admin/signup (app/routers/auth.py) — for deployments that
    don't want credentials sitting in env vars; works once, then always 409s.
"""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Role, User
from app.security import hash_password


async def create_admin_if_absent(db: AsyncSession, email: str, name: str, password: str) -> User | None:
    """Returns the created admin, or None if an admin already exists.

    The pre-check below is a fast path, not the real guard — two concurrent
    callers can both pass it. The `users.uq_users_single_admin` partial
    unique index (app/models.py) is the actual guard; a loser of that race
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
