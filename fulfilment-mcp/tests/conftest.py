"""Unit tests for the fulfilment MCP server. From fulfilment-mcp/:  uv run pytest

They need no running stack: the stage rule is a plain function, and the tools
get a small fake database. The SQL itself is tested against the real Postgres
in test_database.py, which `./run_tests.sh all` runs once the stack is up.
"""
import os

# app.config reads these at import time. Port 1 refuses at once: nothing here may reach a real database.
os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@127.0.0.1:1/unused")
os.environ.setdefault("MCP_AUTH_TOKEN", "unit-test-mcp-token-0123456789abcdef")
os.environ.setdefault("TIMEZONE", "Asia/Kolkata")

import pytest  # noqa: E402


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
