"""Unit tests for the gateway. From gateway/:  uv run pytest

They need no running stack: the database, Redis, the backend and the
upstreams are replaced by small fakes. The real thing, end to end, is
integration_test.py.
"""
import base64
import os

# app.config reads these at import time.
os.environ.setdefault("PLATFORM_DATABASE_URL", "postgresql://unused")
os.environ.setdefault("RESOLVE_URL", "http://backend.test/internal/auth/resolve")
os.environ.setdefault("INTERNAL_SERVICE_TOKEN", "unit-test-service-token-0123456789abcdef")
os.environ.setdefault("GATEWAY_ENCRYPTION_KEY", base64.b64encode(bytes(range(32))).decode())
os.environ.setdefault("LOGS_DATABASE_URL", "postgresql://unused")
os.environ.setdefault("AUDIT_HMAC_KEY", "unit-test-audit-hmac-key-0123456789abcdef")

import pytest  # noqa: E402
from mcp import types  # noqa: E402
from mcp.server.auth.provider import AccessToken  # noqa: E402

from app import limits  # noqa: E402
from app.audit import audit  # noqa: E402
from app.registry import Upstream  # noqa: E402


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def audit_events(monkeypatch) -> list[dict]:
    """Collects what would be written to the audit log instead of writing it."""
    events: list[dict] = []

    async def record(severity, category, action, outcome, **fields):
        events.append({"severity": severity, "category": category, "action": action, "outcome": outcome, **fields})

    async def usable():
        return True

    monkeypatch.setattr(audit, "record", record)
    monkeypatch.setattr(audit, "usable", usable)
    return events


class FakeCounters:
    """Stands in for Redis behind app/limits.py."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.buckets: dict[str, int] = {}
        self.flags: set[str] = set()
        self.broken = False

    async def get(self, key):
        if self.broken:
            raise ConnectionError("redis is down")
        return self.counts.get(key)

    async def evalsha(self, sha, numkeys, key, capacity, refill_rate, now, requested):
        """The token bucket without the refill (that part is the Lua script's: tests/test_limits.py)."""
        if self.broken:
            raise ConnectionError("redis is down")
        tokens = self.buckets.setdefault(key, capacity)
        allowed = tokens >= requested
        self.buckets[key] = tokens - requested if allowed else tokens
        return [int(allowed), self.buckets[key]]

    async def set(self, key, value, nx=False, ex=None):
        if self.broken:
            raise ConnectionError("redis is down")
        if key in self.flags:
            return None
        self.flags.add(key)
        return True

    def pipeline(self, transaction=True):
        return _FakePipe(self)


class _FakePipe:
    def __init__(self, redis: FakeCounters) -> None:
        self.redis, self.key, self.expiry = redis, None, None

    async def __aenter__(self):
        if self.redis.broken:
            raise ConnectionError("redis is down")
        return self

    async def __aexit__(self, *exc):
        pass

    def incr(self, key):
        self.key = key

    def expire(self, key, seconds, nx=False):
        self.expiry = (seconds, nx)

    async def execute(self):
        assert self.expiry == (60, True), "a counter must get its expiry in the same step, and never have it pushed back"
        self.redis.counts[self.key] = self.redis.counts.get(self.key, 0) + 1
        return [self.redis.counts[self.key], True]


@pytest.fixture(autouse=True)
def counters(monkeypatch) -> FakeCounters:
    """No unit test may touch a real Redis (the compose one listens on localhost)."""
    fake = FakeCounters()
    monkeypatch.setattr(limits, "redis", fake)
    return fake


def make_upstream(slug: str, store_id: str | None = None, headers: dict[str, str] | None = None) -> Upstream:
    return Upstream(id=f"id-{slug}", slug=slug, url=f"http://{slug}.test/mcp", headers=headers or {}, store_id=store_id)


def make_tool(name: str) -> types.Tool:
    return types.Tool(name=name, description=f"{name} (test)", input_schema={"type": "object", "properties": {}})


def make_caller(store_ids: list[str], subject: str = "user-1") -> AccessToken:
    return AccessToken(
        token="raw-token", client_id="api_key", scopes=[], subject=subject,
        claims={"role": "member", "store_ids": store_ids, "token_type": "api_key"},
    )
