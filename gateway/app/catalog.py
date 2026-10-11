"""The gateway's in-memory picture of the world: which upstreams are enabled,
whether each one answers, and the tools it offers.

One background loop rebuilds it every REFRESH_SECONDS, and at once when the
backend publishes `upstreams:changed`. Requests only ever read the finished
snapshot, so a slow or dead upstream cannot delay tools/list for anyone, and a
disabled upstream's tools are gone on the next refresh.
"""
import asyncio
import logging
from dataclasses import dataclass, field

import asyncpg
from mcp import types
from redis.asyncio import Redis

from app import upstream as upstream_client
from app.config import settings
from app.registry import Upstream, load_upstreams

log = logging.getLogger(__name__)

UPSTREAMS_CHANGED = "upstreams:changed"  # published by the backend's /upstream-servers routes


@dataclass(frozen=True)
class Snapshot:
    upstreams: dict[str, Upstream] = field(default_factory=dict)  # every enabled upstream, by slug
    tools: dict[str, list[types.Tool]] = field(default_factory=dict)  # only those that answered


class Catalog:
    def __init__(self) -> None:
        self.snapshot = Snapshot()
        self.loaded = False  # True once the registry has been read at least once

    async def refresh(self, pool: asyncpg.Pool, redis: Redis) -> None:
        upstreams = await load_upstreams(pool)

        async def probe(up: Upstream) -> list[types.Tool] | None:
            try:
                return await upstream_client.list_tools(up)
            except Exception as exc:  # any failure means "unreachable"; the loop must survive it
                log.warning("upstream %s is unreachable: %s", up.slug, type(exc).__name__)
                return None

        results = await asyncio.gather(*(probe(up) for up in upstreams.values()))
        tools = {slug: found for slug, found in zip(upstreams, results) if found is not None}
        self.snapshot = Snapshot(upstreams=upstreams, tools=tools)  # swapped in one step
        self.loaded = True

        # The dashboard reads these (through the backend) to show Connected / Unreachable.
        # They expire, so a stopped gateway does not leave stale "connected" marks.
        # Best effort: tools must keep being served while Redis is away.
        ttl = int(settings.refresh_seconds * 3)
        try:
            for up in upstreams.values():
                await redis.set(f"upstream:{up.id}:status", "connected" if up.slug in tools else "unreachable", ex=ttl)
        except Exception as exc:
            log.warning("could not publish upstream status to Redis: %s", type(exc).__name__)

    async def run(self, pool: asyncpg.Pool, redis: Redis) -> None:
        pubsub = redis.pubsub()
        while True:
            try:
                await self.refresh(pool, redis)
            except Exception:
                # Database hiccup: keep serving the last good snapshot and try again.
                log.exception("catalog refresh failed; keeping the previous snapshot")
            try:
                # Subscribing here, not before the loop: if Redis is down when the
                # gateway starts, this is retried instead of ending the loop for good.
                if not pubsub.subscribed:
                    await pubsub.subscribe(UPSTREAMS_CHANGED)
                # Wakes early on a registry change, otherwise after refresh_seconds.
                await pubsub.get_message(ignore_subscribe_messages=True, timeout=settings.refresh_seconds)
                # Several changes may have queued up while the last refresh ran
                # (a hung upstream makes one take list_timeout_seconds). One refresh covers them all.
                while await pubsub.get_message(ignore_subscribe_messages=True, timeout=0) is not None:
                    pass
            except Exception as exc:
                log.warning("no Redis subscription (%s); refreshing on the timer only", type(exc).__name__)
                await asyncio.sleep(settings.refresh_seconds)


catalog = Catalog()
