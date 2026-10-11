"""Refused logins: recorded, counted, and cut off after too many (app/guard.py, app/limits.py)."""
import pytest
from starlette.responses import PlainTextResponse

from app import audit as audit_module
from app import limits
from app.audit import NOTICE, WARNING
from app.config import settings
from app.guard import Guard

pytestmark = pytest.mark.anyio

LIMIT = 3


class Inner:
    """Stands in for the MCP app: answers with whatever status the test sets."""

    def __init__(self):
        self.status, self.calls, self.seen_ip = 200, 0, None

    async def __call__(self, scope, receive, send):
        self.calls += 1
        self.seen_ip = audit_module.client_ip.get()
        await PlainTextResponse("x", self.status)(scope, receive, send)


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(settings, "auth_failures_per_minute", LIMIT)
    inner = Inner()
    return inner, Guard(inner)


async def request(guard, ip="203.0.113.9") -> int:
    sent = []

    async def send(message):
        sent.append(message)

    await guard({"type": "http", "client": (ip, 5555), "method": "POST", "path": "/mcp", "headers": []}, None, send)
    return sent[0]["status"]


async def test_a_good_request_passes_and_is_not_counted(world, counters, audit_events):
    inner, guard = world
    assert await request(guard) == 200
    assert counters.counts == {} and audit_events == []
    assert inner.seen_ip == "203.0.113.9", "handlers need the caller's address for their own audit events"


async def test_each_refused_login_is_recorded_without_any_token(world, audit_events):
    inner, guard = world
    inner.status = 401
    assert await request(guard) == 401
    assert audit_events == [{"severity": NOTICE, "category": "auth", "action": "authenticate", "outcome": "rejected", "failures": 1}]


async def test_an_address_that_keeps_failing_is_cut_off_before_its_token_is_looked_at(world, audit_events):
    inner, guard = world
    inner.status = 401
    assert [await request(guard) for _ in range(LIMIT)] == [401] * LIMIT
    assert [event["severity"] for event in audit_events] == [NOTICE, NOTICE, WARNING], "the failure that trips the limit stands out"

    inner.status = 200  # even a request that would now succeed
    assert await request(guard) == 429
    assert inner.calls == LIMIT, "a blocked request must not reach the MCP app (or the backend behind it)"
    assert len(audit_events) == LIMIT, "blocked requests add no rows, so a flood cannot fill the log"


async def test_the_block_is_per_address(world):
    inner, guard = world
    inner.status = 401
    for _ in range(LIMIT):
        await request(guard, ip="198.51.100.1")
    inner.status = 200
    assert await request(guard, ip="198.51.100.1") == 429 and await request(guard, ip="198.51.100.2") == 200


async def test_without_redis_nobody_is_blocked_and_refusals_are_still_recorded(world, counters, audit_events):
    inner, guard = world
    counters.broken, inner.status = True, 401
    assert [await request(guard) for _ in range(LIMIT + 2)] == [401] * (LIMIT + 2)
    assert len(audit_events) == LIMIT + 2 and {event["severity"] for event in audit_events} == {NOTICE}


async def test_non_http_traffic_is_passed_straight_through():
    seen = []

    async def lifespan_app(scope, receive, send):
        seen.append(scope["type"])

    await Guard(lifespan_app)({"type": "lifespan"}, None, None)
    assert seen == ["lifespan"]


async def test_counters_count_per_key_and_read_zero_without_redis(counters):
    assert [await limits.hit("a"), await limits.hit("a"), await limits.hit("b")] == [1, 2, 1]
    assert await limits.count("a") == 2 and await limits.count("never") == 0
    counters.broken = True
    assert await limits.hit("a") == 0 and await limits.count("a") == 0, "limits are off without Redis, the gateway is not"
