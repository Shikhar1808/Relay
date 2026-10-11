"""What each port serves, and that settings are refused when wrong (app/main.py, app/config.py)."""
import base64
import json

import pytest
from pydantic import ValidationError
from starlette.routing import Mount, Route

from app import main
from app.catalog import Snapshot, catalog
from app.config import Settings
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


def test_health_routes_are_only_on_the_private_app():
    private_paths = {route.path for route in main.private_app.routes if isinstance(route, Route)}
    assert private_paths == {"/healthz", "/readyz"}
    # The public app is nothing but the mounted MCP app: no route of our own that could skip authentication.
    assert all(isinstance(route, Mount) for route in main.public_app.routes) and len(main.public_app.routes) == 1


def good_settings(**overrides) -> Settings:
    values = {
        "platform_database_url": "postgresql://gateway:pw@postgres/platform",
        "resolve_url": "http://backend:8000/internal/auth/resolve",
        "internal_service_token": "t" * 32,
        "gateway_encryption_key": base64.b64encode(bytes(32)).decode(),
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
