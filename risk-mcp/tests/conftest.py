"""Unit tests for the risk check MCP server. From risk-mcp/:  uv run pytest

The server calls nothing, so they need no stack and no fakes.
"""
import os

# app.config reads it at import time.
os.environ.setdefault("MCP_AUTH_TOKEN", "unit-test-mcp-token-0123456789abcdef")

import pytest  # noqa: E402


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
