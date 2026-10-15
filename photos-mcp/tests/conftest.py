"""Unit tests for the photos MCP server. From photos-mcp/:  uv run pytest

They need no running stack and no internet: Pexels is replaced by a small fake
that records what it was asked.
"""
import os

# app.config reads these at import time.
os.environ.setdefault("PEXELS_API_KEY", "pexels-unit-test-key")
os.environ.setdefault("MCP_AUTH_TOKEN", "unit-test-mcp-token-0123456789abcdef")

import pytest  # noqa: E402


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
