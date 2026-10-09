import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Store

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
