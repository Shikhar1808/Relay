"""Rate limits in Redis, shared by every gateway replica.

Tool calls per user (tools.py): a token bucket. A user holds up to
CALLS_PER_MINUTE tokens, a call spends one, and tokens come back at
CALLS_PER_MINUTE a minute. A short burst is fine, the average rate holds, and
someone who ran dry can call again a moment later instead of waiting out a
whole minute. A refused call spends nothing. Same algorithm and script as the
backend's app/core/rate_limit.py; change both or neither.

Refused logins per address (guard.py): a plain counter that starts over each
minute. It only has to say "this address keeps failing".
shortcut: so a burst across the minute boundary can reach twice the limit;
use the bucket there too if guessing ever becomes worth slowing further.

Without Redis nothing is limited: the limits are off then, the gateway is not.
"""
import logging
import time

from redis.asyncio import from_url

from app.config import settings

log = logging.getLogger(__name__)

WINDOW_SECONDS = 60
redis = from_url(settings.redis_url, decode_responses=True)  # connects on first use

# One atomic step: two calls at once must not both spend the last token.
_TOKEN_BUCKET = """
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
# Called by its id (EVALSHA), not sent whole on every call. Registering only works out the id.
# If Redis does not know it, after a restart for example, the helper sends the script once and retries.
_bucket = redis.register_script(_TOKEN_BUCKET)


async def take(key: str, per_minute: int) -> bool:
    """Spends one token from this key's bucket. False if the bucket is empty."""
    try:
        # client=: tests swap this module's `redis`, and the script must run on whichever is current.
        allowed, _ = await _bucket(keys=[key], args=[per_minute, per_minute / 60, time.time(), 1], client=redis)
        return bool(int(allowed))
    except Exception as exc:
        log.warning("cannot check a rate limit (%s)", type(exc).__name__)
        return True


async def once(key: str) -> bool:
    """True the first time in a minute, False after. For saying something once, not on every refusal."""
    try:
        return bool(await redis.set(key, 1, nx=True, ex=WINDOW_SECONDS))
    except Exception as exc:
        log.warning("cannot check a rate limit (%s)", type(exc).__name__)
        return False


async def count(key: str) -> int:
    """How many so far in this key's minute."""
    try:
        return int(await redis.get(key) or 0)
    except Exception as exc:
        log.warning("cannot read a rate-limit counter (%s)", type(exc).__name__)
        return 0


async def hit(key: str) -> int:
    """Counts one more and returns the total for this key's minute."""
    try:
        # In one step, so a counter can never be left behind without an expiry.
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, WINDOW_SECONDS, nx=True)
            return (await pipe.execute())[0]
    except Exception as exc:
        log.warning("cannot count for a rate limit (%s)", type(exc).__name__)
        return 0
