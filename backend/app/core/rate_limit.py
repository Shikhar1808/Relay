"""Token-bucket rate limiting backed by Redis.

Why token bucket (and not fixed-window or sliding-log):
  - Fixed window lets a client burst 2x the limit across a window boundary
    (e.g. N requests at 0:59 + N more at 1:00). Token bucket doesn't have
    this edge.
  - Sliding-window log (a sorted set of timestamps per key) is accurate but
    costs O(requests-in-window) memory per key. Token bucket is a fixed
    2-field hash per key regardless of traffic.
  - Token bucket also naturally allows a legitimate burst (e.g. a dashboard
    loading several panels at once) while still enforcing a steady average
    rate — which is exactly the traffic shape this API sees. This is the
    same family of algorithm Stripe/GitHub/AWS API gateways use.

The check-and-decrement has to be atomic or two concurrent requests can both
read "1 token left" and both proceed. A Lua script makes the read-refill-
decrement sequence a single atomic Redis operation.
"""
import functools
import time

from redis.asyncio import Redis
from redis.commands.core import AsyncScript

_TOKEN_BUCKET_SCRIPT = """
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local bucket = redis.call("HMGET", KEYS[1], "tokens", "timestamp")
local tokens = tonumber(bucket[1])
local timestamp = tonumber(bucket[2])

if tokens == nil then
  tokens = capacity
  timestamp = now
end

local delta = math.max(0, now - timestamp)
tokens = math.min(capacity, tokens + delta * refill_rate)

local allowed = 0
if tokens >= requested then
  tokens = tokens - requested
  allowed = 1
end

redis.call("HMSET", KEYS[1], "tokens", tokens, "timestamp", now)
redis.call("EXPIRE", KEYS[1], math.ceil(capacity / refill_rate) + 1)

return {allowed, tokens}
"""


@functools.cache  # one per client. Registering only works out the script's id; nothing is sent yet.
def _bucket(redis: Redis) -> AsyncScript:
    """The script, called by its id (EVALSHA) instead of being sent whole on every check.
    If Redis does not know the id, after a restart for example, the helper sends the script once and retries."""
    return redis.register_script(_TOKEN_BUCKET_SCRIPT)


async def check_token_bucket(
    redis: Redis, key: str, capacity: int, refill_per_sec: float, cost: int = 1
) -> tuple[bool, float]:
    """Returns (allowed, tokens_remaining)."""
    now = time.time()
    allowed, remaining = await _bucket(redis)(keys=[key], args=[capacity, refill_per_sec, now, cost])
    return bool(int(allowed)), float(remaining)
