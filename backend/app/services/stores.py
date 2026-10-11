import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Role, Store, StoreMember, User

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "store"


async def unique_slug(db: AsyncSession, name: str) -> str:
    base = slugify(name)
    candidate = base
    suffix = 2
    while True:
        existing = await db.execute(select(Store.id).where(Store.slug == candidate))
        if existing.scalar_one_or_none() is None:
            return candidate
        candidate = f"{base}-{suffix}"
        suffix += 1


async def stores_for_user(db: AsyncSession, user: User) -> list[Store]:
    """The stores this user may touch, oldest first: every store for the admin,
    only their own for a member (empty if unassigned). Always a fresh DB read,
    never cached, so adding or removing a member takes effect on their very
    next request — including for tokens issued earlier.
    """
    query = select(Store).order_by(Store.created_at)
    if user.role != Role.admin:
        query = query.join(StoreMember, StoreMember.store_id == Store.id).where(StoreMember.user_id == user.id)
    return list((await db.execute(query)).scalars().all())
