"""What each port serves, and that settings are refused when wrong (app/main.py, app/config.py)."""
import base64
import json

import pytest
from pydantic import ValidationError
from starlette.requests import Request
from starlette.routing import Mount, Route

from app import main
from app.catalog import Snapshot, catalog
from app.audit import audit
from app.config import Settings, settings
from tests.conftest import make_tool, make_upstream

pytestmark = pytest.mark.anyio


async def test_readyz_is_not_ready_until_the_registry_has_been_read(monkeypatch):
    monkeypatch.setattr(catalog, "loaded", False)
    response = await main._readyz(None)
    assert response.status_code == 503 and json.loads(response.body) == {"status": "starting"}


async def test_readyz_reports_how_many_upstreams_answer(monkeypatch):
    monkeypatch.setattr(catalog, "loaded", True)
    monkeypatch.setattr(
        catalog,
        "snapshot",
        Snapshot(upstreams={"a": make_upstream("a"), "b": make_upstream("b")}, tools={"a": [make_tool("t")]}),
    )
    response = await main._readyz(None)
    assert response.status_code == 200 and json.loads(response.body) == {"status": "ok", "upstreams": 2, "reachable": 1}


async def test_healthz_is_always_ok():
    assert (await main._healthz(None)).status_code == 200


def test_health_and_audit_routes_are_only_on_the_private_app():
    private_paths = {route.path for route in main.private_app.routes if isinstance(route, Route)}
    assert private_paths == {"/healthz", "/readyz", "/internal/audit", "/internal/audit/verify"}
    # The public app is nothing but the mounted MCP app: no route of our own that could skip authentication.
    assert all(isinstance(route, Mount) for route in main.public_app.routes) and len(main.public_app.routes) == 1


def good_settings(**overrides) -> Settings:
    values = {
        "platform_database_url": "postgresql://gateway:pw@postgres/platform",
        "resolve_url": "http://backend:8000/internal/auth/resolve",
        "internal_service_token": "t" * 32,
        "gateway_encryption_key": base64.b64encode(bytes(32)).decode(),
        "logs_database_url": "postgresql://gateway:pw@postgres/mcp_logs",
        "audit_hmac_key": "k" * 32,
    }
    return Settings(_env_file=None, **{**values, **overrides})


def test_settings_defaults_match_the_documented_timeouts():
    settings = good_settings()
    assert (settings.refresh_seconds, settings.list_timeout_seconds, settings.call_timeout_seconds) == (30, 5, 30)
    assert (settings.port, settings.private_port) == (8080, 9090)


@pytest.mark.parametrize("key", [base64.b64encode(bytes(16)).decode(), "not base64!", ""])
def test_settings_refuse_a_bad_encryption_key(key):
    with pytest.raises(ValidationError, match="GATEWAY_ENCRYPTION_KEY"):
        good_settings(gateway_encryption_key=key)


def test_allowed_hosts_can_be_set_as_a_json_list(monkeypatch):
    monkeypatch.setenv("ALLOWED_HOSTS", '["mcp.example.com", "gateway:*"]')
    assert good_settings().allowed_hosts == ["mcp.example.com", "gateway:*"]


# ---- the audit log, as the backend reaches it on the private port ------------------

SERVICE = {"authorization": f"Bearer {settings.internal_service_token}"}


def private_request(method: str, query: str = "", body: bytes = b"", headers: dict | None = None) -> Request:
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    raw_headers = [(name.encode(), value.encode()) for name, value in (SERVICE if headers is None else headers).items()]
    return Request({"type": "http", "method": method, "path": "/internal/audit", "query_string": query.encode(), "headers": raw_headers}, receive)


@pytest.fixture
def reads(monkeypatch) -> list[tuple]:
    asked: list[tuple] = []

    async def read(*args):
        asked.append(args)
        return [{"seq": 2, "category": "tool_call"}]

    monkeypatch.setattr(audit, "read", read)
    return asked


@pytest.mark.parametrize("headers", [{}, {"authorization": "Bearer wrong"}, {"authorization": settings.internal_service_token}, {"authorization": "Bearer caf\u00e9"}])
async def test_audit_routes_refuse_anyone_without_the_service_token(headers, reads, audit_events):
    assert (await main._audit_read(private_request("GET", headers=headers))).status_code == 401
    assert (await main._audit_write(private_request("POST", body=b'{"action": "x"}', headers=headers))).status_code == 401
    assert reads == [] and audit_events == []


async def test_the_chain_check_needs_the_service_token_and_returns_what_the_audit_says(monkeypatch):
    async def check():
        return {"intact": False, "verified": 86, "last_hash": "ab", "broken_seq": 87}

    monkeypatch.setattr(audit, "check", check)
    assert (await main._audit_verify(private_request("GET", headers={}))).status_code == 401
    response = await main._audit_verify(private_request("GET"))
    assert response.status_code == 200 and json.loads(response.body)["broken_seq"] == 87


async def test_audit_read_passes_paging_and_filters_and_caps_the_page_size(reads):
    response = await main._audit_read(private_request("GET", "limit=5000&before=90&category=admin&severity=NOTICE"))
    assert response.status_code == 200 and json.loads(response.body) == [{"seq": 2, "category": "tool_call"}]
    await main._audit_read(private_request("GET"))
    assert reads == [(200, 90, "admin", "NOTICE"), (50, None, None, None)]


@pytest.mark.parametrize("query", ["limit=ten", "before=1;DROP"])
async def test_audit_read_refuses_numbers_that_are_not_numbers(query, reads):
    assert (await main._audit_read(private_request("GET", query))).status_code == 400 and reads == []


async def test_an_admin_event_is_always_an_admin_notice_whatever_is_sent(monkeypatch, audit_events):
    seen_ip = []

    async def record(severity, category, action, outcome, **fields):
        seen_ip.append(main.client_ip.get())
        audit_events.append({"severity": severity, "category": category, "action": action, "outcome": outcome, **fields})
        return True

    monkeypatch.setattr(audit, "record", record)
    body = {
        "action": "upstream.update", "user_id": "admin-1", "upstream": "woo", "client_ip": "203.0.113.9",
        "changes": {"enabled": False, "credential": "changed", "user_id": "not-a-column", "tool": "not-a-column"},
        "category": "tool_call", "severity": "INFO", "tool": "list_orders",  # not fields of an admin event: ignored
    }
    response = await main._audit_write(private_request("POST", body=json.dumps(body).encode()))
    assert response.status_code == 201 and seen_ip == ["203.0.113.9"]
    assert audit_events == [{
        "severity": "NOTICE", "category": "admin", "action": "upstream.update", "outcome": "ok",
        "user_id": "admin-1", "upstream": "woo", "changes": body["changes"],
    }]


async def test_an_admin_event_that_could_not_be_stored_is_reported_so_the_backend_can_refuse_the_change(monkeypatch):
    async def not_stored(*args, **fields):
        return False

    monkeypatch.setattr(audit, "record", not_stored)
    assert (await main._audit_write(private_request("POST", body=b'{"action": "upstream.delete"}'))).status_code == 503


async def test_a_correction_for_a_change_that_failed_to_commit_is_a_warning(monkeypatch, audit_events):
    async def record(severity, category, action, outcome, **fields):
        audit_events.append((severity, category, action, outcome))
        return True

    monkeypatch.setattr(audit, "record", record)
    body = b'{"action": "upstream.update", "outcome": "failed", "upstream": "woo"}'
    assert (await main._audit_write(private_request("POST", body=body))).status_code == 201
    assert audit_events == [("WARNING", "admin", "upstream.update", "failed")]


@pytest.mark.parametrize(
    "body",
    [b"", b"not json", b"{}", b'{"action": ""}', b'{"action": "x", "changes": "not an object"}', b'{"action": "x", "outcome": "denied"}'],
)
async def test_a_malformed_admin_event_is_refused(body, audit_events):
    assert (await main._audit_write(private_request("POST", body=body))).status_code == 400 and audit_events == []
