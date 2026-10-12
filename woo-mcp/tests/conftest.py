"""Unit tests for the WooCommerce MCP server. From woo-mcp/:  uv run pytest

They need no running stack: WooCommerce is replaced by a small fake that
records what it was asked.
"""
import os

# app.config reads these at import time.
os.environ.setdefault("WOOCOMMERCE_URL", "http://store.test")
os.environ.setdefault("WOOCOMMERCE_KEY", "ck_unit_test")
os.environ.setdefault("WOOCOMMERCE_SECRET", "cs_unit_test")
os.environ.setdefault("MCP_AUTH_TOKEN", "unit-test-mcp-token-0123456789abcdef")

import pytest  # noqa: E402


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
