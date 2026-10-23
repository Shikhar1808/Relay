"""The Redis token bucket (app/core/rate_limit.py), against a real Redis: the
logic is a Lua script, so a fake would test nothing. Skipped when the compose
Redis is not running.
"""
import asyncio
import uuid

import pytest
from redis.asyncio import from_url
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.rate_limit import check_token_bucket

pytestmark = [pytest.mark.anyio, pytest.mark.live]


@pytest.fixture
async def redis():
    client = from_url("redis://localhost:6379/0", decode_responses=True)
    try:
        await client.ping()
    except (RedisConnectionError, OSError):
        await client.aclose()
        pytest.skip("Redis is not running on localhost:6379 (docker compose up -d)")
    yield client
    await client.aclose()


@pytest.fixture
def key():
    return f"ratelimit:test:{uuid.uuid4()}"


async def test_allows_a_burst_up_to_capacity_then_refuses(redis, key):
    results = [await check_token_bucket(redis, key, capacity=5, refill_per_sec=0.001) for _ in range(7)]
    assert [allowed for allowed, _ in results] == [True] * 5 + [False] * 2
    assert results[4][1] < 1, "after the fifth request less than one token is left"


async def test_refills_over_time(redis, key):
    for _ in range(3):
        assert (await check_token_bucket(redis, key, capacity=3, refill_per_sec=20))[0]
    # 20 tokens per second: a refused request is allowed again a moment later.
    await asyncio.sleep(0.15)
    assert (await check_token_bucket(redis, key, capacity=3, refill_per_sec=20))[0]


async def test_never_holds_more_than_capacity(redis, key):
    await check_token_bucket(redis, key, capacity=2, refill_per_sec=1000)
    await asyncio.sleep(0.05)  # 50 tokens' worth of time, but the bucket only holds 2
    allowed, remaining = await check_token_bucket(redis, key, capacity=2, refill_per_sec=1000)
    assert allowed and remaining == 1


async def test_keys_are_independent(redis, key):
    for _ in range(2):
        await check_token_bucket(redis, key, capacity=2, refill_per_sec=0.001)
    assert not (await check_token_bucket(redis, key, capacity=2, refill_per_sec=0.001))[0]
    assert (await check_token_bucket(redis, key + ":other", capacity=2, refill_per_sec=0.001))[0]


async def test_still_works_after_redis_forgets_its_scripts(redis, key):
    assert (await check_token_bucket(redis, key, capacity=3, refill_per_sec=0.001))[0]
    await redis.script_flush()  # what a Redis restart does: the script's id is no longer known
    allowed, remaining = await check_token_bucket(redis, key, capacity=3, refill_per_sec=0.001)
    assert allowed and 0.9 < remaining <= 1.1, "the script was sent again, and the bucket carried on where it was"
