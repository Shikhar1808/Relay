"""tools/list and tools/call: the naming scheme and the store rule (app/tools.py)."""
import pytest
from mcp import types

from app import tools
from app.audit import chat_ref
from app.catalog import Snapshot, catalog
from tests.conftest import make_caller, make_tool, make_upstream

pytestmark = pytest.mark.anyio

SHARED = make_upstream("github")
STORE_A = make_upstream("store-a", store_id="A")
STORE_B = make_upstream("store-b", store_id="B")
DOWN = make_upstream("down")  # enabled, but it did not answer the last probe


@pytest.fixture(autouse=True)
def world(monkeypatch):
    """Three reachable upstreams and one that is down; each test picks the caller."""
    snapshot = Snapshot(
        upstreams={u.slug: u for u in (SHARED, STORE_A, STORE_B, DOWN)},
        tools={
            "github": [make_tool("list_repos"), make_tool("list_orders")],
            "store-a": [make_tool("list_orders")],
            "store-b": [make_tool("list_orders")],
        },
    )
    monkeypatch.setattr(catalog, "snapshot", snapshot)


def as_caller(monkeypatch, store_ids):
    monkeypatch.setattr(tools, "get_access_token", lambda: make_caller(store_ids))


def text(result: types.CallToolResult) -> str:
    return result.content[0].text


# ---- names -------------------------------------------------------------------


def test_names_round_trip():
    assert tools.exposed_name("woo", "list_orders") == "woo__list_orders"
    assert tools.split_name("woo__list_orders") == ("woo", "list_orders")


def test_only_the_first_separator_ends_the_slug():
    assert tools.split_name(tools.exposed_name("store-a", "get__order")) == ("store-a", "get__order")


@pytest.mark.parametrize("name", ["list_orders", "woo__", "__list_orders", "", "woo_list", "__"])
def test_names_that_are_not_ours(name):
    assert tools.split_name(name) is None


# ---- the store rule ------------------------------------------------------------


def test_shared_upstream_is_for_everyone():
    assert tools.may_use(SHARED, []) and tools.may_use(SHARED, ["B"])


def test_store_upstream_is_only_for_callers_with_that_store():
    assert tools.may_use(STORE_A, ["A"]) and tools.may_use(STORE_A, ["B", "A"])
    assert not tools.may_use(STORE_A, []) and not tools.may_use(STORE_A, ["B"])


# ---- tools/list ----------------------------------------------------------------


async def names_for(monkeypatch, store_ids) -> list[str]:
    as_caller(monkeypatch, store_ids)
    return sorted(tool.name for tool in (await tools.list_tools(None, None)).tools)


async def test_list_for_a_member_with_no_store_is_the_shared_tools_only(monkeypatch):
    assert await names_for(monkeypatch, []) == ["github__list_orders", "github__list_repos"]


async def test_list_adds_the_callers_own_store_and_never_another(monkeypatch):
    assert await names_for(monkeypatch, ["A"]) == ["github__list_orders", "github__list_repos", "store-a__list_orders"]


async def test_list_for_a_member_in_two_stores_has_both_told_apart_by_prefix(monkeypatch):
    names = await names_for(monkeypatch, ["A", "B"])
    assert "store-a__list_orders" in names and "store-b__list_orders" in names and len(names) == 4


async def test_list_keeps_everything_but_the_name(monkeypatch):
    as_caller(monkeypatch, [])
    listed = {tool.name: tool for tool in (await tools.list_tools(None, None)).tools}
    assert listed["github__list_repos"].description == "list_repos (test)"
    assert listed["github__list_repos"].input_schema == {"type": "object", "properties": {}}
    # The snapshot's own Tool objects must not be renamed in place: the next caller reuses them.
    assert catalog.snapshot.tools["github"][0].name == "list_repos"


async def test_list_leaves_out_an_upstream_that_is_down(monkeypatch):
    assert not [name for name in await names_for(monkeypatch, ["A", "B"]) if name.startswith("down__")]


# ---- tools/call ----------------------------------------------------------------


@pytest.fixture
def upstream_calls(monkeypatch):
    """Records what would be sent upstream instead of sending it."""
    calls = []

    async def fake_call(upstream, name, arguments):
        calls.append((upstream.slug, name, arguments))
        return types.CallToolResult(content=[types.TextContent(type="text", text="from upstream")])

    monkeypatch.setattr(tools.upstream_client, "call_tool", fake_call)
    return calls


async def call(name: str, arguments: dict | None = None) -> types.CallToolResult:
    return await tools.call_tool(None, types.CallToolRequestParams(name=name, arguments=arguments))


async def test_call_strips_the_prefix_and_passes_the_arguments_through(monkeypatch, upstream_calls):
    as_caller(monkeypatch, ["A"])
    result = await call("store-a__list_orders", {"status": "processing"})
    assert not result.is_error and text(result) == "from upstream"
    assert upstream_calls == [("store-a", "list_orders", {"status": "processing"})]


async def test_call_without_arguments_sends_an_empty_object(monkeypatch, upstream_calls):
    as_caller(monkeypatch, [])
    await call("github__list_repos")
    assert upstream_calls == [("github", "list_repos", {})]


@pytest.mark.parametrize(
    "name",
    [
        "store-b__list_orders",  # another store's server
        "nope__list_orders",  # no such server
        "list_orders",  # not a gateway name at all
        "store-b__",
    ],
)
async def test_call_refuses_other_stores_and_unknown_names_identically(monkeypatch, upstream_calls, name):
    as_caller(monkeypatch, ["A"])
    result = await call(name)
    assert result.is_error and text(result) == f"Unknown tool: {name}"
    assert upstream_calls == [], "a refused call must never reach an upstream"


async def test_call_is_checked_even_for_a_name_the_caller_was_never_shown(monkeypatch, upstream_calls):
    as_caller(monkeypatch, [])
    assert (await call("store-a__list_orders")).is_error and upstream_calls == []


async def test_call_reports_a_timeout_clearly(monkeypatch):
    as_caller(monkeypatch, [])

    async def too_slow(upstream, name, arguments):
        raise TimeoutError

    monkeypatch.setattr(tools.upstream_client, "call_tool", too_slow)
    result = await call("github__list_repos")
    assert result.is_error and text(result) == "github did not answer within 30 seconds"


async def test_call_reports_any_other_upstream_failure_as_unreachable(monkeypatch):
    as_caller(monkeypatch, [])

    async def broken(upstream, name, arguments):
        raise ConnectionRefusedError("secret internal detail http://10.0.0.5")

    monkeypatch.setattr(tools.upstream_client, "call_tool", broken)
    result = await call("down__anything")
    assert result.is_error and text(result) == "down is unreachable"
    assert "10.0.0.5" not in text(result), "internal addresses must not leak to the caller"


async def test_an_upstream_tool_error_is_passed_back_unchanged(monkeypatch):
    as_caller(monkeypatch, [])

    async def tool_failed(upstream, name, arguments):
        return types.CallToolResult(content=[types.TextContent(type="text", text="order 9 not found")], is_error=True)

    monkeypatch.setattr(tools.upstream_client, "call_tool", tool_failed)
    result = await call("github__list_repos")
    assert result.is_error and text(result) == "order 9 not found"


async def test_handlers_refuse_to_run_without_a_caller(monkeypatch, upstream_calls):
    monkeypatch.setattr(tools, "get_access_token", lambda: None)
    with pytest.raises(PermissionError):
        await tools.list_tools(None, None)
    with pytest.raises(PermissionError):
        await call("github__list_repos")
    assert upstream_calls == []


# ---- the audit log ---------------------------------------------------------------


async def test_a_successful_call_is_recorded_with_who_what_and_how_long(monkeypatch, upstream_calls, audit_events):
    as_caller(monkeypatch, ["A"])
    await call("store-a__list_orders", {"status": "processing", "api_key": "sk-123"})
    (event,) = audit_events
    assert (event["severity"], event["category"], event["outcome"]) == ("INFO", "tool_call", "ok")
    assert (event["user_id"], event["upstream"], event["tool"], event["token_type"]) == ("user-1", "store-a", "list_orders", "api_key")
    assert isinstance(event["duration_ms"], int) and len(event["args_hash"]) == 64
    assert "sk-123" not in event["arguments"] and "processing" in event["arguments"]


@pytest.fixture
def chat_headers():
    """What app/guard.py notes when a request carries the chat's ids."""
    ids = {"chat_id": "7d1e0c7e-52c1-4c53-9a43-0d6f1f3a8b11", "chat_turn": "b83a4a0e-3f0b-4f8e-8f43-6a1f0c2d9e22"}
    noted = chat_ref.set(ids)
    yield ids
    chat_ref.reset(noted)


async def test_a_call_from_the_chat_agent_is_recorded_with_its_chat_and_message(monkeypatch, upstream_calls, audit_events, chat_headers):
    monkeypatch.setattr(tools, "get_access_token", lambda: make_caller(["A"], token_type="chat_token"))
    await call("store-a__list_orders")
    (event,) = audit_events
    assert (event["token_type"], event["chat_id"], event["chat_turn"]) == ("chat_token", chat_headers["chat_id"], chat_headers["chat_turn"])


async def test_an_api_key_caller_cannot_plant_chat_ids_in_the_log(monkeypatch, upstream_calls, audit_events, chat_headers):
    as_caller(monkeypatch, ["A"])  # an API key: only the backend's chat agent can vouch for a conversation
    await call("store-a__list_orders")
    (event,) = audit_events
    assert event["token_type"] == "api_key" and "chat_id" not in event and "chat_turn" not in event


async def test_a_refused_call_is_a_warning_and_the_log_tells_the_two_refusals_apart(monkeypatch, upstream_calls, audit_events):
    as_caller(monkeypatch, ["A"])
    await call("store-b__list_orders")  # exists, but not the caller's
    await call("nope__list_orders")  # does not exist
    assert [(e["severity"], e["outcome"], e["upstream"], e["tool"]) for e in audit_events] == [
        ("WARNING", "denied", "store-b", "list_orders"),
        ("WARNING", "denied", None, "nope__list_orders"),
    ]


async def test_upstream_failures_are_errors_in_the_log(monkeypatch, audit_events):
    as_caller(monkeypatch, [])

    async def too_slow(upstream, name, arguments):
        raise TimeoutError

    async def tool_failed(upstream, name, arguments):
        return types.CallToolResult(content=[types.TextContent(type="text", text="order 9 not found")], is_error=True)

    monkeypatch.setattr(tools.upstream_client, "call_tool", too_slow)
    await call("github__list_repos")
    monkeypatch.setattr(tools.upstream_client, "call_tool", tool_failed)
    await call("github__list_repos")
    assert [(e["severity"], e["outcome"]) for e in audit_events] == [("ERROR", "timeout"), ("WARNING", "error")]
    assert audit_events[1]["error"] == "order 9 not found"


async def test_no_tool_runs_while_the_audit_log_cannot_be_written(monkeypatch, upstream_calls):
    as_caller(monkeypatch, [])

    async def unusable():
        return False

    monkeypatch.setattr(tools.audit, "usable", unusable)
    result = await call("github__list_repos")
    assert result.is_error and "audit log is unavailable" in text(result)
    assert upstream_calls == [], "the upstream must not be called when the call cannot be recorded"


async def test_listing_tools_is_not_recorded(monkeypatch, audit_events):
    await names_for(monkeypatch, ["A"])
    assert audit_events == []


# ---- the per-user call limit -------------------------------------------------------


async def test_a_user_over_the_call_limit_is_refused_and_it_is_recorded_once(monkeypatch, upstream_calls, audit_events):
    monkeypatch.setattr(tools.settings, "calls_per_minute", 2)
    as_caller(monkeypatch, [])
    results = [await call("github__list_repos") for _ in range(5)]
    assert [result.is_error for result in results] == [False, False, True, True, True]
    assert "at most 2 tool calls a minute" in text(results[2])
    assert len(upstream_calls) == 2, "a refused call must not reach the upstream"
    limited = [event for event in audit_events if event["outcome"] == "rate_limited"]
    assert len(limited) == 1, "one row a minute, not one per refused call"
    assert (limited[0]["severity"], limited[0]["user_id"], limited[0]["limit"]) == ("WARNING", "user-1", 2)


async def test_the_call_limit_is_per_user(monkeypatch, upstream_calls):
    monkeypatch.setattr(tools.settings, "calls_per_minute", 1)
    monkeypatch.setattr(tools, "get_access_token", lambda: make_caller([], subject="user-1"))
    assert not (await call("github__list_repos")).is_error and (await call("github__list_repos")).is_error
    monkeypatch.setattr(tools, "get_access_token", lambda: make_caller([], subject="user-2"))
    assert not (await call("github__list_repos")).is_error


async def test_without_redis_calls_are_not_limited(monkeypatch, upstream_calls, counters):
    monkeypatch.setattr(tools.settings, "calls_per_minute", 1)
    as_caller(monkeypatch, [])
    counters.broken = True
    assert not any([(await call("github__list_repos")).is_error for _ in range(3)])
