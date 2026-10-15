"""The stage rule, the three tools against a fake database, and who may reach the server (app/main.py, app/config.py)."""
import contextlib
import re
from datetime import UTC, datetime

import httpx2
import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from pydantic import ValidationError
from starlette.responses import PlainTextResponse

from app import main
from app.config import Settings, settings

pytestmark = pytest.mark.anyio

# 09:30 in the store's zone (Asia/Kolkata, conftest.py); Postgres hands timestamptz back in UTC.
WHEN = datetime(2026, 10, 16, 4, 0, tzinfo=UTC)


class FakeDatabase:
    """Stands in for one connection: knows each started order's stage and records every write."""

    def __init__(self, stages: dict[int, str] | None = None) -> None:
        self.stages = stages or {}
        self.writes: list[tuple[str, tuple]] = []

    def _rows(self, ids: list[int] | None) -> list[dict]:
        rows = [
            {"order_id": order_id, "stage": stage, "tracking_number": f"TRK{order_id:010X}", "date_started": WHEN, "date_updated": WHEN}
            for order_id, stage in self.stages.items() if ids is None or order_id in ids
        ]
        return [{**row, "total": len(rows)} for row in rows]

    async def fetch(self, sql: str, *args) -> list[dict]:
        return self._rows(args[-1] if "order_id = ANY($1" in sql else None)

    async def execute(self, sql: str, *args) -> None:
        self.writes.append((sql, args))

    async def executemany(self, sql: str, rows: list[tuple]) -> None:
        self.writes.append((sql, tuple(rows)))

    def transaction(self):
        return contextlib.nullcontext()


@pytest.fixture
def database(monkeypatch) -> FakeDatabase:
    fake = FakeDatabase()

    @contextlib.asynccontextmanager
    async def connect():
        yield fake

    monkeypatch.setattr(main, "_db", connect)
    return fake


async def call(tool: str, **arguments):
    return (await main.mcp.call_tool(tool, arguments)).structured_content


async def test_the_server_offers_three_tools_that_say_what_they_do():
    hints = {tool.name: tool.annotations for tool in await main.mcp.list_tools()}
    assert hints.keys() == {"start_fulfilment", "advance_fulfilment", "list_fulfilments"}
    assert hints["list_fulfilments"].read_only_hint is True
    # Writes, so the chat asks first; both are safe to repeat after a timeout.
    assert (hints["start_fulfilment"].read_only_hint, hints["start_fulfilment"].destructive_hint, hints["start_fulfilment"].idempotent_hint) == (False, False, True)
    assert (hints["advance_fulfilment"].read_only_hint, hints["advance_fulfilment"].destructive_hint, hints["advance_fulfilment"].idempotent_hint) == (False, True, True)
    assert all(hint.open_world_hint is False for hint in hints.values())


# ---- the stage rule ----------------------------------------------------------------------

@pytest.mark.parametrize(("now", "to"), [("packing", "shipped"), ("shipped", "delivered"), ("shipped", "shipped"), ("delivered", "delivered")])
def test_one_step_forward_or_staying_put_is_allowed(now, to):
    assert main.refusals({7: now}, [7], to) == []


@pytest.mark.parametrize(("now", "to", "reason"), [
    ("packing", "delivered", "order 7 is still packing, so it cannot be delivered yet"),
    ("delivered", "shipped", "order 7 is already delivered and cannot go back to shipped"),
    ("shipped", "packing", "order 7 is already shipped and cannot go back to packing"),
])
def test_skipping_a_stage_or_going_back_is_refused(now, to, reason):
    assert main.refusals({7: now}, [7], to) == [reason]


def test_every_order_that_cannot_move_is_named():
    assert main.refusals({7: "packing", 8: "shipped"}, [7, 8, 9], "delivered") == [
        "order 7 is still packing, so it cannot be delivered yet",
        "order 9 was never started",
    ]


# ---- the tools ------------------------------------------------------------------------------

async def test_start_gives_each_new_order_a_tracking_number_once(database):
    database.stages = {7: "packing", 8: "packing"}
    started = await call("start_fulfilment", order_ids=[7, 8, 7])
    ((sql, rows),) = database.writes
    assert "ON CONFLICT (order_id) DO NOTHING" in sql  # an order started before keeps its record
    assert [order_id for order_id, _ in rows] == [7, 8]  # the repeated 7 is asked for once
    assert all(re.fullmatch(r"TRK[0-9A-F]{10}", number) for _, number in rows) and rows[0][1] != rows[1][1]
    assert [record["order_id"] for record in started["fulfilments"]] == [7, 8]


async def test_records_carry_dates_in_the_stores_time_zone(database):
    database.stages = {7: "packing"}
    (record,) = (await call("start_fulfilment", order_ids=[7]))["fulfilments"]
    assert record == {
        "order_id": 7, "stage": "packing", "tracking_number": "TRK0000000007",
        "date_started": "2026-10-16T09:30:00+05:30", "date_updated": "2026-10-16T09:30:00+05:30",
    }


async def test_advance_changes_the_stage_of_orders_that_may_move(database):
    database.stages = {7: "packing", 8: "shipped"}
    await call("advance_fulfilment", order_ids=[7, 8], stage="shipped")
    ((sql, args),) = database.writes
    assert sql.startswith("UPDATE") and args == ([7, 8], "shipped")


async def test_one_order_that_cannot_move_stops_the_whole_batch(database):
    database.stages = {7: "shipped", 8: "packing"}
    with pytest.raises(ToolError, match=r"Nothing was changed: order 8 is still packing, so it cannot be delivered yet\.$") as caught:
        await call("advance_fulfilment", order_ids=[7, 8], stage="delivered")
    assert not isinstance(caught.value, UnexpectedToolError)  # that one would reach the model as a bare "Error executing tool"
    assert database.writes == []  # order 7 could move, and did not


async def test_the_list_says_how_many_match_in_all(database):
    database.stages = {7: "packing", 8: "shipped"}
    listed = await call("list_fulfilments")
    assert listed["total"] == 2 and len(listed["fulfilments"]) == 2


async def test_an_empty_list_is_not_an_error(database):
    assert await call("list_fulfilments", stage="delivered") == {"fulfilments": [], "total": 0}


@pytest.mark.parametrize(("tool", "arguments"), [
    ("start_fulfilment", {"order_ids": []}),
    ("start_fulfilment", {"order_ids": list(range(1, 52))}),
    ("start_fulfilment", {"order_ids": [0]}),
    ("advance_fulfilment", {"order_ids": [7], "stage": "packing"}),  # starting is start_fulfilment's job
])
async def test_arguments_out_of_bounds_are_refused_before_the_database_is_asked(database, tool, arguments):
    with pytest.raises(ToolError):
        await call(tool, **arguments)
    assert database.writes == []


async def test_a_database_that_does_not_answer_is_a_tool_error_not_a_crash():
    # The real connect, to conftest's address: port 1 refuses at once.
    with pytest.raises(ToolError, match=r"database failed \(.+\)") as caught:
        await call("list_fulfilments")
    assert not isinstance(caught.value, UnexpectedToolError)


# ---- who may reach the server ---------------------------------------------------------

GATEWAY = f"Bearer {settings.mcp_auth_token}"


async def status_of(method: str, path: str, authorization: str | None = None) -> int:
    headers = {"authorization": authorization} if authorization else {}
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=main.app), base_url="http://localhost:8000") as client:
        return (await client.request(method, path, headers=headers)).status_code


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", settings.mcp_auth_token, GATEWAY + "x"])
@pytest.mark.parametrize("path", ["/mcp", "/", "/healthz/"])
async def test_everything_but_the_health_check_refuses_a_caller_without_the_gateways_token(path, authorization):
    assert await status_of("POST", path, authorization) == 401


async def test_the_health_check_answers_without_a_token():
    assert await status_of("GET", "/healthz") == 200


async def test_the_gateways_token_gets_through_to_the_server():
    reached: list[str] = []

    async def inner(scope, receive, send):
        reached.append(scope["path"])
        await PlainTextResponse("ok")(scope, receive, send)

    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=main.RequireCredential(inner)), base_url="http://localhost:8000") as client:
        assert (await client.post("/mcp", headers={"authorization": GATEWAY})).status_code == 200
    assert reached == ["/mcp"]


# ---- settings ---------------------------------------------------------------------------

def test_the_timeout_stays_under_the_gateways_thirty_seconds():
    assert Settings(_env_file=None, database_url="postgresql://x", mcp_auth_token="t" * 32).timeout_seconds < 30


def test_settings_refuse_a_token_too_short_to_be_a_secret():
    with pytest.raises(ValidationError, match="mcp_auth_token"):
        Settings(_env_file=None, database_url="postgresql://x", mcp_auth_token="short")


def test_the_database_address_has_no_default(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None, mcp_auth_token="t" * 32)
