"""Talking to the gateway's audit log (app/services/gateway_audit.py), and what
the registry routes tell it (app/api/routes/upstreams.py: _changes)."""
import io
import json
import urllib.error
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes.upstreams import _changes
from app.core.config import settings
from app.schemas.upstreams import CreateUpstreamRequest, UpdateUpstreamRequest
from app.services import gateway_audit

pytestmark = pytest.mark.anyio

ADMIN = SimpleNamespace(id=uuid.UUID("11111111-1111-1111-1111-111111111111"))
REQUEST = SimpleNamespace(client=SimpleNamespace(host="203.0.113.9"))


@pytest.fixture
def gateway(monkeypatch):
    """Records what would be sent to the gateway; `answer` is what it replies (or an exception to raise)."""
    state = SimpleNamespace(sent=[], answer=b'{"stored": true}')

    def urlopen(request, timeout):
        state.sent.append(request)
        if isinstance(state.answer, Exception):
            raise state.answer
        return io.BytesIO(state.answer)

    monkeypatch.setattr(gateway_audit.urllib.request, "urlopen", urlopen)
    return state


async def test_an_admin_event_goes_to_the_gateway_with_the_service_token(gateway):
    await gateway_audit.record_admin_event("upstream.update", ADMIN, REQUEST, "woo", {"enabled": False})
    (request,) = gateway.sent
    assert request.full_url == f"{settings.gateway_internal_url}/internal/audit" and request.get_method() == "POST"
    assert request.get_header("Authorization") == f"Bearer {settings.internal_service_token}"
    assert json.loads(request.data) == {
        "action": "upstream.update", "outcome": "ok", "user_id": str(ADMIN.id), "upstream": "woo",
        "client_ip": "203.0.113.9", "changes": {"enabled": False},
    }


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError("connection refused"),
        urllib.error.HTTPError("http://gateway", 503, "not stored", {}, io.BytesIO(b"")),
        urllib.error.HTTPError("http://gateway", 401, "wrong token", {}, io.BytesIO(b"")),
        TimeoutError("timed out"),
        b"<html>not json</html>",
    ],
)
async def test_an_event_that_was_not_stored_stops_the_change_with_503(gateway, failure):
    gateway.answer = failure
    with pytest.raises(HTTPException) as refused:
        await gateway_audit.record_admin_event("upstream.delete", ADMIN, REQUEST, "woo", {})
    assert refused.value.status_code == 503 and "not made" in refused.value.detail


async def test_a_change_the_store_worker_makes_has_no_client_address(gateway):
    await gateway_audit.record_admin_event("upstream.create", ADMIN, None, "woo-shop", {})
    assert json.loads(gateway.sent[0].data)["client_ip"] is None


async def test_reading_passes_the_query_on_and_fails_with_503(gateway):
    gateway.answer = b'[{"seq": 3}]'
    assert await gateway_audit.read_events("?limit=5&category=admin") == [{"seq": 3}]
    assert gateway.sent[0].full_url.endswith("/internal/audit?limit=5&category=admin") and gateway.sent[0].get_method() == "GET"
    gateway.answer = urllib.error.URLError("connection refused")
    with pytest.raises(HTTPException) as refused:
        await gateway_audit.read_events("")
    assert refused.value.status_code == 503


def test_the_audit_log_is_told_what_was_sent_but_never_the_credential():
    created = _changes(CreateUpstreamRequest(name="Woo", slug="woo", url="http://woo:9000/mcp", credential="Bearer top-secret"))
    assert created["credential"] == "set" and created["url"] == "http://woo:9000/mcp" and "top-secret" not in json.dumps(created)
    assert _changes(UpdateUpstreamRequest(credential="")) == {"credential": "none"}
    store = uuid.uuid4()
    assert _changes(UpdateUpstreamRequest(enabled=False, store_id=store)) == {"enabled": False, "store_id": str(store)}


# ---- record first, commit second, correct the log if the commit fails ---------------


class FakeDb:
    def __init__(self, log: list, commit_fails: bool = False) -> None:
        self.log, self.commit_fails = log, commit_fails

    async def commit(self) -> None:
        self.log.append("commit")
        if self.commit_fails:
            raise ConnectionError("database went away")


@pytest.fixture
def steps(monkeypatch) -> list:
    """What happened, in order: ("event", outcome) and "commit"."""
    log: list = []

    async def record(action, admin, request, upstream, changes, outcome="ok"):
        log.append(("event", outcome))

    monkeypatch.setattr(gateway_audit, "record_admin_event", record)
    return log


async def test_a_change_is_recorded_before_it_is_committed(steps):
    await gateway_audit.record_and_commit(FakeDb(steps), "upstream.update", ADMIN, REQUEST, "woo", {})
    assert steps == [("event", "ok"), "commit"]


async def test_a_change_that_cannot_be_recorded_is_never_committed(steps, monkeypatch):
    async def unavailable(*args, **kwargs):
        raise HTTPException(503, "The audit log is unavailable, so this change was not made")

    monkeypatch.setattr(gateway_audit, "record_admin_event", unavailable)
    with pytest.raises(HTTPException):
        await gateway_audit.record_and_commit(FakeDb(steps), "upstream.update", ADMIN, REQUEST, "woo", {})
    assert steps == []


async def test_a_failed_commit_adds_a_correction_to_the_log_and_keeps_its_own_error(steps):
    with pytest.raises(ConnectionError):
        await gateway_audit.record_and_commit(FakeDb(steps, commit_fails=True), "upstream.update", ADMIN, REQUEST, "woo", {})
    assert steps == [("event", "ok"), "commit", ("event", "failed")]


async def test_a_correction_that_cannot_be_sent_does_not_hide_the_commit_error(monkeypatch):
    sent = []

    async def record(action, admin, request, upstream, changes, outcome="ok"):
        sent.append(outcome)
        if outcome == "failed":
            raise HTTPException(503, "The audit log is unavailable")

    monkeypatch.setattr(gateway_audit, "record_admin_event", record)
    with pytest.raises(ConnectionError):
        await gateway_audit.record_and_commit(FakeDb([], commit_fails=True), "upstream.update", ADMIN, REQUEST, "woo", {})
    assert sent == ["ok", "failed"]
