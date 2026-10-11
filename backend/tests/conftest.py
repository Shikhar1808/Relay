"""Unit tests for the backend. From backend/:  uv run pytest

They need no running stack, except the ones marked `live`, which skip
themselves when the compose Redis is not reachable. End-to-end coverage of the
API against a real database is scripts/smoke_test.py.
"""
import base64
import os

# app.core.config reads these at import time. Set before any app module is
# imported, and only as defaults, so a real environment still wins.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost:5432/unused")
os.environ.setdefault("JWT_SECRET", "unit-test-jwt-secret-unit-test-jwt-secret")
os.environ.setdefault("GATEWAY_ENCRYPTION_KEY", base64.b64encode(bytes(range(32))).decode())
os.environ.setdefault("INTERNAL_SERVICE_TOKEN", "unit-test-service-token-0123456789abcdef")

import pytest  # noqa: E402


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
