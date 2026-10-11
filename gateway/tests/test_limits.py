"""The per-user call limit's token bucket (app/limits.py), against a real Redis:
the logic is a Lua script, so a fake would test nothing. Skipped when the
compose Redis is not running.
"""
import asyncio
import uuid

import pytest
from redis.asyncio import from_url
from redis.exceptions import ConnectionError as RedisConnectionError

from app import limits

pytestmark = [pytest.mark.anyio, pytest.mark.live]


@pytest.fixture
async def key(monkeypatch):
    client = from_url("redis://localhost:6379/0", decode_responses=True)
    try:
        await client.ping()
    except (RedisConnectionError, OSError):
        await client.aclose()
        pytest.skip("Redis is not running on localhost:6379 (docker compose up -d)")
    monkeypatch.setattr(limits, "redis", client)  # conftest put a fake there
    name = f"ratelimit:test:{uuid.uuid4()}"
    yield name
    await client.delete(name, name + ":other", name + ":noted")
    await client.aclose()


async def test_a_burst_up_to_the_limit_passes_and_the_next_call_is_refused(key):
    results = [await limits.take(key, 120) for _ in range(121)]
    assert results == [True] * 120 + [False]


async def test_a_user_who_ran_dry_can_call_again_a_moment_later_not_a_minute_later(key):
    for _ in range(120):
        await limits.take(key, 120)
    assert not await limits.take(key, 120)
    await asyncio.sleep(0.6)  # 120 a minute is one token every half second
    assert await limits.take(key, 120)
    assert not await limits.take(key, 120), "and only that one token came back"


async def test_a_refused_call_spends_nothing(key):
    for _ in range(120):
        await limits.take(key, 120)
    for _ in range(50):  # hammering while empty must not push the recovery further away
        await limits.take(key, 120)
    await asyncio.sleep(0.6)
    assert await limits.take(key, 120)


async def test_each_key_has_its_own_bucket_and_an_idle_one_expires(key):
    for _ in range(2):
        await limits.take(key, 2)
    assert not await limits.take(key, 2) and await limits.take(key + ":other", 2)
    assert 0 < await limits.redis.ttl(key) <= 61, "an idle bucket must not live in Redis forever"


async def test_once_is_true_only_the_first_time(key):
    assert [await limits.once(key + ":noted") for _ in range(3)] == [True, False, False]


async def test_the_bucket_still_works_after_redis_forgets_its_scripts(key):
    assert await limits.take(key, 2)
    await limits.redis.script_flush()  # what a Redis restart does: the script's id is no longer known
    assert await limits.take(key, 2), "the script was sent again"
    assert not await limits.take(key, 2), "and the bucket carried on where it was"
