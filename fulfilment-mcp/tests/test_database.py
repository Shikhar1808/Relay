"""The three tools against the real Postgres, signed in as the role gateway/deploy/db-init.sh makes.

Skipped unless FULFILMENT_TEST_DATABASE_URL is set; `./run_tests.sh all` sets it once the compose
stack is up. Each test uses order ids of its own, far above a real store's, and deletes them
afterwards as the database owner: the fulfilment role itself cannot delete.
"""
import asyncio
import os
import secrets
import subprocess
from pathlib import Path

import asyncpg
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from app import main
from app.config import settings

URL = os.environ.get("FULFILMENT_TEST_DATABASE_URL", "")
REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = [pytest.mark.anyio, pytest.mark.skipif(not URL, reason="needs the stack: ./run_tests.sh all")]


def in_postgres(command: str) -> str:
    """A shell command in the postgres container, the way backend/scripts/smoke_test.py runs psql there."""
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "sh", "-c", command],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=20, check=True,
    ).stdout.strip()


def as_owner(sql: str) -> str:
    return in_postgres(f'psql -U "$POSTGRES_USER" -d fulfilment -At -v ON_ERROR_STOP=1 -c "{sql}"')


@pytest.fixture
def orders(monkeypatch):
    """Three order ids no real store has; their rows are deleted again afterwards."""
    monkeypatch.setattr(settings, "database_url", URL)
    first = 1_000_000_000 + secrets.randbelow(1_000_000) * 10
    yield [first, first + 1, first + 2]
    as_owner(f"DELETE FROM fulfilments WHERE order_id BETWEEN {first} AND {first + 9}")


async def call(tool: str, **arguments) -> dict:
    return (await main.mcp.call_tool(tool, arguments)).structured_content


def stages(result: dict) -> dict[int, str]:
    return {record["order_id"]: record["stage"] for record in result["fulfilments"]}


async def test_a_second_start_returns_the_first_record(orders):
    a = orders[0]
    first = await call("start_fulfilment", order_ids=[a])
    assert await call("start_fulfilment", order_ids=[a, a]) == first  # same tracking number, same dates


async def test_two_starts_at_once_make_one_record(orders):
    # Two copies of the server, or a retry that overtook the first call: the primary key decides.
    a = orders[0]
    one, other = await asyncio.gather(call("start_fulfilment", order_ids=[a]), call("start_fulfilment", order_ids=[a]))
    assert one == other
    assert as_owner(f"SELECT count(*) FROM fulfilments WHERE order_id = {a}") == "1"


async def test_an_order_moves_one_stage_at_a_time_and_never_back(orders):
    a = orders[0]
    await call("start_fulfilment", order_ids=[a])
    assert stages(await call("advance_fulfilment", order_ids=[a], stage="shipped")) == {a: "shipped"}
    assert stages(await call("advance_fulfilment", order_ids=[a], stage="delivered")) == {a: "delivered"}
    with pytest.raises(ToolError, match="cannot go back"):
        await call("advance_fulfilment", order_ids=[a], stage="shipped")


async def test_one_order_that_cannot_move_leaves_the_whole_batch_as_it_was(orders):
    a, b, _ = orders
    await call("start_fulfilment", order_ids=[a, b])
    await call("advance_fulfilment", order_ids=[a], stage="shipped")
    with pytest.raises(ToolError, match=f"order {b} is still packing"):
        await call("advance_fulfilment", order_ids=[a, b], stage="delivered")
    assert stages(await call("list_fulfilments", order_ids=[a, b])) == {a: "shipped", b: "packing"}


async def test_the_same_stage_again_changes_nothing(orders):
    a = orders[0]
    await call("start_fulfilment", order_ids=[a])
    await call("advance_fulfilment", order_ids=[a], stage="shipped")
    changed_at = as_owner(f"SELECT date_updated FROM fulfilments WHERE order_id = {a}")
    await call("advance_fulfilment", order_ids=[a], stage="shipped")
    assert as_owner(f"SELECT date_updated FROM fulfilments WHERE order_id = {a}") == changed_at


async def test_two_advances_at_once_both_succeed(orders):
    # The second waits for the first one's row lock, then finds the order already shipped.
    a = orders[0]
    await call("start_fulfilment", order_ids=[a])
    results = await asyncio.gather(*(call("advance_fulfilment", order_ids=[a], stage="shipped") for _ in range(2)))
    assert [stages(result) for result in results] == [{a: "shipped"}] * 2


async def test_the_list_filters_by_stage_and_by_order(orders):
    a, b, c = orders
    await call("start_fulfilment", order_ids=[a, b, c])
    await call("advance_fulfilment", order_ids=[b], stage="shipped")
    shipped = await call("list_fulfilments", stage="shipped", order_ids=[a, b, c])
    assert (stages(shipped), shipped["total"]) == ({b: "shipped"}, 1)
    picked = await call("list_fulfilments", order_ids=[a, c])
    assert (stages(picked), picked["total"]) == ({a: "packing", c: "packing"}, 2)


async def test_the_role_can_neither_delete_nor_open_another_database(orders):
    conn = await asyncpg.connect(URL)
    try:
        for statement in (f"DELETE FROM fulfilments WHERE order_id = {orders[0]}", "DROP TABLE fulfilments"):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(statement)
    finally:
        await conn.close()
    others = ["mcp_logs", in_postgres('printenv POSTGRES_DB')]  # the audit log, and the platform's own
    for database in others:
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="permission denied for database"):
            await asyncpg.connect(URL.rsplit("/", 1)[0] + "/" + database)
