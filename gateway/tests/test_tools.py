"""tools/list and tools/call: the naming scheme and the store rule (app/tools.py)."""
import pytest
from mcp import types

from app import tools
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
