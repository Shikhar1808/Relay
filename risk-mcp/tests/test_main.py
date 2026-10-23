"""The rules, the tool, and who may reach the server (app/main.py, app/config.py)."""
import httpx2
import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from pydantic import ValidationError
from starlette.responses import PlainTextResponse

from app import main
from app.config import Settings, settings

pytestmark = pytest.mark.anyio

# An order as woo-mcp's list_orders returns it: the tool must take it whole.
ORDER = {
    "id": 7, "number": "7", "status": "processing", "total": "38.00", "currency": "INR",
    "date_created": "2026-10-12T10:15:00", "payment_method": "Cash on delivery", "payment_code": "cod",
    "customer": "Asha Rao", "email": "asha@example.com",
    "items": [{"name": "Blue Mug", "quantity": 2, "total": "38.00"}],
}


async def score(*orders: dict) -> list[dict]:
    return (await main.mcp.call_tool("score_orders", {"orders": list(orders)})).structured_content["scores"]


async def test_the_one_tool_only_reads():
    (tool,) = await main.mcp.list_tools()
    assert tool.name == "score_orders"
    assert (tool.annotations.read_only_hint, tool.annotations.open_world_hint) == (True, False)  # so the chat runs it without asking


async def test_the_input_format_is_flat():
    # Every chat message sends it to the model; Groq does not document $ref, which nested models would bring.
    (tool,) = await main.mcp.list_tools()
    assert "$defs" not in tool.input_schema and "$ref" not in str(tool.input_schema)


async def test_an_ordinary_order_is_low_risk_and_says_why_not():
    assert await score(ORDER) == [{"order_id": 7, "score": 0, "level": "low", "advice": "ship it", "reasons": "none"}]


BANK = {"payment_method": "Direct bank transfer", "payment_code": "bacs"}


@pytest.mark.parametrize(("change", "points", "reason"), [
    ({"total": "24500.00", **BANK}, 40, "total 24500.00 is over 20000"),
    ({"items": [{"name": "Blue Mug", "quantity": 12}]}, 30, "12 of Blue Mug in one order"),
    ({"email": "Someone@MAILINATOR.com"}, 40, "mailinator.com is a throwaway email domain"),
    ({"total": "6000.00"}, 20, "cash on delivery for 6000.00"),
    # The code decides, so a title renamed in wp-admin changes nothing; the title counts only without a code.
    ({"total": "6000.00", "payment_method": "Pay when it arrives"}, 20, "cash on delivery for 6000.00"),
    ({"total": "6000.00", "payment_code": None}, 20, "cash on delivery for 6000.00"),
])
async def test_each_rule_adds_its_points_and_its_reason(change, points, reason):
    (scored,) = await score({**ORDER, **change})
    assert (scored["score"], scored["reasons"]) == (points, reason)


async def test_rules_add_up_to_a_level_and_never_past_100():
    risky = {**ORDER, "total": "24500.00", "email": "x@yopmail.com", "items": [{"name": "Blue Mug", "quantity": 40}]}
    (scored,) = await score(risky)  # 40 + 40 + 30 + 20 for cash on delivery
    assert (scored["score"], scored["level"], scored["advice"]) == (100, "high", "put it on hold")
    assert scored["reasons"].count(";") == 3


@pytest.mark.parametrize(("change", "score_", "level"), [
    ({"total": "6000.00"}, 20, "low"),                                            # cash on delivery alone
    ({"items": [{"name": "Blue Mug", "quantity": 10}]}, 30, "medium"),           # exactly 30
    ({"total": "20000.00", **BANK}, 0, "low"),                                    # a total of exactly 20,000 is not over it
    ({"total": "6000.00", "email": "x@yopmail.com"}, 60, "high"),                # exactly 60
])
async def test_the_levels_start_at_30_and_60(change, score_, level):
    (scored,) = await score({**ORDER, **change})
    assert (scored["score"], scored["level"]) == (score_, level)


async def test_one_email_on_three_orders_of_a_batch_marks_each_of_them():
    batch = [{**ORDER, "id": n, "email": "Asha@Example.com " if n == 2 else "asha@example.com"} for n in (1, 2, 3)]
    assert [scored["score"] for scored in await score(*batch)] == [30, 30, 30]
    assert [scored["score"] for scored in await score(*batch[:2])] == [0, 0]   # two is not yet a pattern


async def test_orders_without_an_email_are_not_counted_as_one_customer():
    assert [scored["score"] for scored in await score(*({**ORDER, "id": n, "email": None} for n in (1, 2, 3)))] == [0, 0, 0]


@pytest.mark.parametrize("orders", [[], [ORDER] * 51])
async def test_a_batch_out_of_bounds_is_refused(orders):
    with pytest.raises(ToolError):
        await main.mcp.call_tool("score_orders", {"orders": orders})


@pytest.mark.parametrize(("bad", "field"), [
    ({"total": "-1"}, "total"), ({"total": "abc"}, "total"), ({"id": None}, "id"),
    ({"items": [{"name": "Blue Mug", "quantity": -2}]}, "items.0.quantity"),
])
async def test_a_bad_order_is_refused_naming_the_order_and_the_field(bad, field):
    with pytest.raises(ToolError, match=rf"Order 2 of the batch: {field}: ") as caught:
        await main.mcp.call_tool("score_orders", {"orders": [ORDER, {**ORDER, **bad}]})
    assert not isinstance(caught.value, UnexpectedToolError)  # that one would reach the model as a bare "Error executing tool"


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

def test_settings_refuse_a_token_too_short_to_be_a_secret():
    with pytest.raises(ValidationError, match="mcp_auth_token"):
        Settings(_env_file=None, mcp_auth_token="short")


