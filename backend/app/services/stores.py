import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Store, StoreMember

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


async def store_for_member(db: AsyncSession, user_id: uuid.UUID) -> Store | None:
    """The one store a member belongs to, or None if unassigned. Always a
    fresh DB read, never cached, so moving or removing a member takes effect
    on their very next request — including for tokens issued earlier.
    """
    result = await db.execute(
        select(Store).join(StoreMember, StoreMember.store_id == Store.id).where(StoreMember.user_id == user_id)
    )
    return result.scalar_one_or_none()
