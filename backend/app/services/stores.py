import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Role, Store, StoreMember, User

# Same shape as an upstream server's slug (app/schemas/upstreams.py): no underscores,
# because a store's MCP server is registered as woo-<subdomain> and tools are slug__tool.
_SUBDOMAIN_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
# Short enough that woo-<subdomain>__update_order_status stays inside the 64 characters
# many MCP clients allow for a tool name.
_SUBDOMAIN_MAX = 30
# Host names the platform itself will use next to the stores.
_RESERVED_SUBDOMAINS = frozenset({"app", "api", "mcp", "www", "dev", "admin", "gateway", "dashboard"})


def check_subdomain(subdomain: str) -> str:
    """The one rule for a store's subdomain, which is also its slug. Raises ValueError."""
    if len(subdomain) > _SUBDOMAIN_MAX or not _SUBDOMAIN_RE.fullmatch(subdomain):
        raise ValueError(
            f"a store subdomain is lowercase letters, digits and single hyphens, at most {_SUBDOMAIN_MAX} characters"
        )
    if subdomain in _RESERVED_SUBDOMAINS:
        raise ValueError(f'the store subdomain "{subdomain}" is reserved for the platform')
    return subdomain


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
