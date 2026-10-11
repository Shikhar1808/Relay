"""The refresh loop and the snapshot requests read (app/catalog.py)."""
import asyncio

import pytest

from app import catalog as catalog_module
from app.catalog import UPSTREAMS_CHANGED, Catalog
from tests.conftest import make_tool, make_upstream

pytestmark = pytest.mark.anyio

POOL = object()  # never touched: load_upstreams is replaced in every test


class FakeRedis:
    def __init__(self, broken=False, pubsub=None):
        self.values, self.broken, self._pubsub = {}, broken, pubsub

    async def set(self, key, value, ex=None):
        if self.broken:
            raise ConnectionError("redis is down")
        self.values[key] = (value, ex)

    def pubsub(self):
        return self._pubsub


class FakePubSub:
    """Subscribing fails `failures` times, then works. Messages are handed out once each."""

    def __init__(self, failures=0, messages=0):
        self.failures, self.messages, self.subscribed, self.subscribe_calls = failures, messages, False, 0

    async def subscribe(self, channel):
        assert channel == UPSTREAMS_CHANGED
        self.subscribe_calls += 1
        if self.failures:
            self.failures -= 1
            raise ConnectionError("redis is down")
        self.subscribed = True

    async def get_message(self, ignore_subscribe_messages=True, timeout=0):
        if self.messages:
            self.messages -= 1
            return {"type": "message"}
        await asyncio.sleep(timeout)
        return None


@pytest.fixture
def world(monkeypatch):
    """Two healthy upstreams and one that fails its probe."""
    state = {"upstreams": {u.slug: u for u in (make_upstream("woo", "A"), make_upstream("github"), make_upstream("dead"))}, "loads": 0}

    async def fake_load(pool):
        state["loads"] += 1
        if state.get("db_down"):
            raise ConnectionError("database is down")
        return state["upstreams"]

    async def fake_list_tools(upstream):
        if upstream.slug == "dead":
            raise TimeoutError
        return [make_tool("list_orders")]

    monkeypatch.setattr(catalog_module, "load_upstreams", fake_load)
    monkeypatch.setattr(catalog_module.upstream_client, "list_tools", fake_list_tools)
    return state


async def test_refresh_separates_reachable_from_unreachable(world):
    cat, redis = Catalog(), FakeRedis()
    assert not cat.loaded and cat.snapshot.upstreams == {}

    await cat.refresh(POOL, redis)

    assert cat.loaded
    assert set(cat.snapshot.upstreams) == {"woo", "github", "dead"}, "a down upstream is still known, so calls get a clear error"
    assert set(cat.snapshot.tools) == {"woo", "github"}, "but it offers no tools"
    assert cat.snapshot.tools["woo"][0].name == "list_orders"


async def test_refresh_publishes_status_that_expires(world):
    cat, redis = Catalog(), FakeRedis()
    await cat.refresh(POOL, redis)
    assert redis.values["upstream:id-woo:status"][0] == "connected"
    assert redis.values["upstream:id-dead:status"][0] == "unreachable"
    assert all(ttl and ttl > 30 for _, ttl in redis.values.values()), "status must outlive one refresh and still expire"


async def test_refresh_serves_tools_even_when_redis_is_down(world):
    cat = Catalog()
    await cat.refresh(POOL, FakeRedis(broken=True))
    assert cat.loaded and set(cat.snapshot.tools) == {"woo", "github"}


async def test_a_failed_refresh_keeps_the_last_good_snapshot(world):
    cat, redis = Catalog(), FakeRedis()
    await cat.refresh(POOL, redis)
    before = cat.snapshot

    world["db_down"] = True
    with pytest.raises(ConnectionError):
        await cat.refresh(POOL, redis)
    assert cat.snapshot is before and cat.loaded


async def test_a_disabled_upstream_disappears_on_the_next_refresh(world):
    cat, redis = Catalog(), FakeRedis()
    await cat.refresh(POOL, redis)
    del world["upstreams"]["woo"]
    await cat.refresh(POOL, redis)
    assert "woo" not in cat.snapshot.upstreams and "woo" not in cat.snapshot.tools


async def test_one_slow_probe_does_not_hold_up_the_others(world, monkeypatch):
    started = []

    async def fake_list_tools(upstream):
        started.append(upstream.slug)
        await asyncio.sleep(0.05)
        return [make_tool("t")]

    monkeypatch.setattr(catalog_module.upstream_client, "list_tools", fake_list_tools)
    loop = asyncio.get_running_loop()
    began = loop.time()
    await Catalog().refresh(POOL, FakeRedis())
    assert len(started) == 3 and loop.time() - began < 0.12, "probes must run at the same time, not one after another"


async def run_briefly(cat, redis, seconds):
    task = asyncio.create_task(cat.run(POOL, redis))
    await asyncio.sleep(seconds)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_run_survives_redis_being_down_at_startup(world, monkeypatch):
    # Regression: subscribing used to happen before the loop, so this ended it for good.
    monkeypatch.setattr(catalog_module.settings, "refresh_seconds", 0.02)
    pubsub = FakePubSub(failures=2)
    cat = Catalog()
    await run_briefly(cat, FakeRedis(pubsub=pubsub), 0.2)
    assert cat.loaded, "tools must be served while Redis is away"
    assert pubsub.subscribe_calls == 3 and pubsub.subscribed, "it must keep trying and subscribe once Redis is back"
    assert world["loads"] >= 3


async def test_run_survives_the_database_being_down(world, monkeypatch):
    monkeypatch.setattr(catalog_module.settings, "refresh_seconds", 0.02)
    world["db_down"] = True
    cat = Catalog()
    task = asyncio.create_task(cat.run(POOL, FakeRedis(pubsub=FakePubSub())))
    await asyncio.sleep(0.08)
    assert not cat.loaded and not task.done(), "not ready, but still trying"
    world["db_down"] = False
    await asyncio.sleep(0.08)
    assert cat.loaded
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_run_refreshes_at_once_on_a_change_and_once_for_a_burst_of_changes(world, monkeypatch):
    monkeypatch.setattr(catalog_module.settings, "refresh_seconds", 10)  # the timer never fires in this test
    cat = Catalog()
    await run_briefly(cat, FakeRedis(pubsub=FakePubSub(messages=5)), 0.1)
    # The first refresh at startup, then one more for all five queued change messages.
    assert world["loads"] == 2
