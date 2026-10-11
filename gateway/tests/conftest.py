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

import pytest  # noqa: E402
from mcp import types  # noqa: E402
from mcp.server.auth.provider import AccessToken  # noqa: E402

from app.registry import Upstream  # noqa: E402


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def make_upstream(slug: str, store_id: str | None = None, headers: dict[str, str] | None = None) -> Upstream:
    return Upstream(id=f"id-{slug}", slug=slug, url=f"http://{slug}.test/mcp", headers=headers or {}, store_id=store_id)


def make_tool(name: str) -> types.Tool:
    return types.Tool(name=name, description=f"{name} (test)", input_schema={"type": "object", "properties": {}})


def make_caller(store_ids: list[str], subject: str = "user-1") -> AccessToken:
    return AccessToken(
        token="raw-token", client_id="api_key", scopes=[], subject=subject,
        claims={"role": "member", "store_ids": store_ids, "token_type": "api_key"},
    )
