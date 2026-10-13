"""The chat agent's graph (app/agent), run against a scripted model and a fake gateway.

No network and no database: the state is kept by LangGraph's in-memory saver.
"""
import json

import openai
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from mcp import types

from app.agent.graph import build_graph, read, run
from app.agent.nodes import MAX_RESULT_CHARS, MAX_TURNS, build_blocks
from app.agent.state import ChatContext

pytestmark = pytest.mark.anyio

READS = types.ToolAnnotations(read_only_hint=True)
WRITES = types.ToolAnnotations(read_only_hint=False, destructive_hint=False)
TOOLS = {
    "woo-demo__list_orders": types.Tool(name="woo-demo__list_orders", input_schema={"type": "object"}, annotations=READS),
    "woo-demo__get_product": types.Tool(name="woo-demo__get_product", input_schema={"type": "object"}, annotations=READS),
    "woo-demo__create_product": types.Tool(name="woo-demo__create_product", input_schema={"type": "object"}, annotations=WRITES),
}
ORDER = {
    "id": 17, "number": "17", "status": "on-hold", "total": "499.00", "currency": "INR",
    "date_created": "2026-10-14T10:42:11", "payment_method": "Cash on delivery",
    "customer": "Asha Rao", "email": "asha@example.com",
    "items": [{"name": "Blue Mug", "quantity": 1, "total": "499.00"}],
}
ORDERS = {"orders": [ORDER], "total": 1}
PRODUCT = {"id": 5, "name": "Test Mug", "price": "299.00", "status": "publish", "stock_quantity": None}


class FakeModel:
    """Answers with the scripted replies, in order, and remembers what it was sent."""

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.seen: list[list] = []

    async def ainvoke(self, messages, config=None):
        self.seen.append(list(messages))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeGateway:
    """Answers each tool from `results` (a value, or an exception to raise) and keeps the calls."""

    def __init__(self, **results) -> None:
        self.results = results
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, name: str, arguments: dict) -> types.CallToolResult:
        self.calls.append((name, arguments))
        result = self.results[name.partition("__")[2]]
        if isinstance(result, Exception):
            raise result
        if isinstance(result, types.CallToolResult):
            return result
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result))], structured_content=result)


def asks(*calls: tuple[str, dict]) -> AIMessage:
    """The model asking for tools."""
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{i}_{name}"} for i, (name, args) in enumerate(calls)])


def refusal(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=True)


def context(model, gateway=None) -> ChatContext:
    return ChatContext(
        model=model, tools=TOOLS, servers={"woo-demo": "Demo Store"},
        call_tool=gateway or FakeGateway(), now="Wednesday 14 October 2026, 10:45 (Asia/Kolkata)",
    )


async def ask(graph, ctx, text: str, turn: str = "turn-1", **options) -> dict:
    return await run(graph, "thread", ctx, {"messages": [HumanMessage(text)], "turn": turn}, **options)


async def answer(graph, ctx, approve: bool) -> dict:
    return await run(graph, "thread", ctx, Command(resume=approve))


def tool_messages(messages) -> list[ToolMessage]:
    return [message for message in messages if isinstance(message, ToolMessage)]


@pytest.fixture
def graph():
    return build_graph(InMemorySaver())


async def test_a_question_needing_no_tool_is_answered_from_the_model(graph):
    model = FakeModel(AIMessage("Hello!"))
    output = await ask(graph, context(model), "hi")
    assert output == {"reply": "Hello!", "status": "answered", "blocks": [], "steps": [], "pending": []}
    system = model.seen[0][0]
    assert isinstance(system, SystemMessage)
    assert "Wednesday 14 October 2026" in system.content and "woo-demo" in system.content and "Demo Store" in system.content


async def test_a_read_tool_runs_and_its_result_goes_back_to_the_model(graph):
    model = FakeModel(asks(("woo-demo__list_orders", {"after": "2026-10-14"})), AIMessage("One order came in today."))
    gateway = FakeGateway(list_orders=ORDERS)
    output = await ask(graph, context(model, gateway), "what orders came in today?")
    assert gateway.calls == [("woo-demo__list_orders", {"after": "2026-10-14"})]
    assert output["status"] == "answered" and output["reply"] == "One order came in today."
    assert json.loads(tool_messages(model.seen[1])[0].content) == ORDERS
    assert [block["kind"] for block in output["blocks"]] == ["table"]
    assert output["blocks"][0]["source"] == "Demo Store · list orders"
    assert output["steps"] == ["Demo Store: list orders, 1 found"]


async def test_one_failing_tool_does_not_stop_the_others(graph):
    model = FakeModel(asks(("woo-demo__list_orders", {}), ("woo-demo__get_product", {"product_id": 5})), AIMessage("Partly."))
    gateway = FakeGateway(list_orders=ConnectionError("down"), get_product=PRODUCT)
    output = await ask(graph, context(model, gateway), "orders and product 5")
    assert len(gateway.calls) == 2
    results = tool_messages(model.seen[1])
    assert len(results) == 2 and "could not be reached" in results[0].content
    assert [block["kind"] for block in output["blocks"]] == ["notice", "card"]
    assert output["blocks"][0]["tone"] == "error"


async def test_a_write_waits_for_the_user(graph):
    asked = {"name": "Test Mug", "price": 299, "description": None}  # models often send the fields they leave empty
    model = FakeModel(asks(("woo-demo__create_product", asked)))
    gateway = FakeGateway(create_product=PRODUCT)
    output = await ask(graph, context(model, gateway), "create Test Mug at 299")
    assert output["status"] == "needs_confirmation" and gateway.calls == []
    assert output["pending"] == [
        {
            "call_id": "call_0_woo-demo__create_product", "server": "Demo Store", "tool": "create_product",
            "summary": "Create product: name Test Mug, price 299", "arguments": asked,
        }
    ]


async def test_an_approved_write_runs(graph):
    model = FakeModel(asks(("woo-demo__create_product", {"name": "Test Mug", "price": 299})), AIMessage("Created."))
    gateway = FakeGateway(create_product=PRODUCT)
    ctx = context(model, gateway)
    await ask(graph, ctx, "create Test Mug at 299")
    output = await answer(graph, ctx, approve=True)
    assert gateway.calls == [("woo-demo__create_product", {"name": "Test Mug", "price": 299})]
    assert output["status"] == "answered" and output["reply"] == "Created." and output["pending"] == []
    assert [(block["kind"], block.get("tone")) for block in output["blocks"]] == [("notice", "success"), ("card", None)]
    assert output["steps"] == ["Demo Store: create product, done"]


async def test_a_declined_write_does_not_run_and_the_model_is_told(graph):
    model = FakeModel(asks(("woo-demo__create_product", {"name": "Test Mug"})), AIMessage("Okay, I did not create it."))
    gateway = FakeGateway(create_product=PRODUCT)
    ctx = context(model, gateway)
    await ask(graph, ctx, "create Test Mug")
    output = await answer(graph, ctx, approve=False)
    assert gateway.calls == [] and output["status"] == "answered"
    assert "declined" in tool_messages(model.seen[1])[0].content
    assert output["blocks"][0]["kind"] == "notice" and output["blocks"][0]["text"] == "You declined this change."
    assert output["steps"] == ["Demo Store: create product, declined"]


async def test_a_new_message_while_a_write_is_pending_counts_as_declining_it(graph):
    model = FakeModel(asks(("woo-demo__create_product", {"name": "Test Mug"})), AIMessage("Sure, here is something else."))
    gateway = FakeGateway(create_product=PRODUCT)
    ctx = context(model, gateway)
    await ask(graph, ctx, "create Test Mug")
    output = await ask(graph, ctx, "never mind, say hi", turn="turn-2")
    assert gateway.calls == [] and output["status"] == "answered" and output["pending"] == []
    # The request that never got an answer is not sent again: a tool request without its result is one the model's API refuses.
    assert [type(message).__name__ for message in model.seen[1]] == ["SystemMessage", "HumanMessage", "HumanMessage"]


async def test_a_tool_the_gateway_refuses_is_reported_to_the_model(graph):
    model = FakeModel(asks(("woo-demo__made_up", {})), AIMessage("That tool does not exist."))
    gateway = FakeGateway(made_up=refusal("Unknown tool: woo-demo__made_up"))
    ctx = context(model, gateway)
    output = await ask(graph, ctx, "do the made-up thing")
    assert output["status"] == "needs_confirmation"  # no hints, so it is treated as a write
    output = await answer(graph, ctx, approve=True)
    assert "Unknown tool" in tool_messages(model.seen[1])[0].content
    assert output["blocks"][0]["tone"] == "error" and output["steps"] == ["Demo Store: made up, failed"]


async def test_a_tool_error_reaches_the_model_as_text(graph):
    model = FakeModel(asks(("woo-demo__list_orders", {})), AIMessage("The store did not answer."))
    gateway = FakeGateway(list_orders=refusal("woo-demo is unreachable"))
    output = await ask(graph, context(model, gateway), "orders?")
    assert tool_messages(model.seen[1])[0].content == "woo-demo is unreachable"
    assert output["blocks"] == [
        {"kind": "notice", "tone": "error", "title": "List orders failed", "text": "woo-demo is unreachable",
         "source": "Demo Store · list orders", "call_id": "call_0_woo-demo__list_orders"}
    ]


async def test_a_long_result_is_cut_for_the_model_but_kept_whole_for_the_page(graph):
    long_text = "x" * (MAX_RESULT_CHARS * 3)
    model = FakeModel(asks(("woo-demo__get_product", {})), AIMessage("Done."))
    gateway = FakeGateway(get_product=types.CallToolResult(content=[types.TextContent(type="text", text=long_text)]))
    output = await ask(graph, context(model, gateway), "product?")
    assert len(tool_messages(model.seen[1])[0].content) <= MAX_RESULT_CHARS + 40
    assert output["blocks"][0] == {
        "kind": "raw", "title": "Get product", "text": long_text,
        "source": "Demo Store · get product", "call_id": "call_0_woo-demo__get_product",
    }


async def test_a_run_that_never_settles_is_stopped(graph):
    model = FakeModel(*[asks(("woo-demo__list_orders", {"page": page})) for page in range(50)])
    gateway = FakeGateway(list_orders=ORDERS)
    output = await ask(graph, context(model, gateway), "loop forever", limit=14)
    assert output["status"] == "stopped" and "too many steps" in output["reply"]
    assert 1 <= len(gateway.calls) < 10


async def test_an_unreachable_model_ends_the_run_with_a_plain_message(graph):
    output = await ask(graph, context(FakeModel(openai.OpenAIError("rate limited"))), "hi")
    assert output["status"] == "failed" and "not available" in output["reply"] and "rate limited" not in output["reply"]


async def test_without_a_model_the_chat_says_it_is_not_set_up(graph):
    output = await ask(graph, context(None), "hi")
    assert output["status"] == "failed" and "not set up" in output["reply"]


async def test_a_follow_up_sees_the_earlier_turn_and_only_its_own_results(graph):
    model = FakeModel(asks(("woo-demo__list_orders", {})), AIMessage("One order."), AIMessage("It is on hold."))
    ctx = context(model, FakeGateway(list_orders=ORDERS))
    first = await ask(graph, ctx, "orders today?")
    second = await ask(graph, ctx, "what is its status?", turn="turn-2")
    assert [block["kind"] for block in first["blocks"]] == ["table"] and first["blocks"][0]["title"] == "Orders (1)"
    assert second["steps"] == [] and [block["title"] for block in second["blocks"]] == ["Tools used in this chat (1)"]
    # Earlier turns go to the model as what was said, without their tool requests and results: it looks again when it needs the data.
    sent = model.seen[2]
    assert [(type(message).__name__, message.content) for message in sent[1:]] == [
        ("HumanMessage", "orders today?"), ("AIMessage", "One order."), ("HumanMessage", "what is its status?"),
    ]


async def test_only_the_latest_exchanges_are_repeated_to_the_model(graph):
    model = FakeModel(*[AIMessage(f"answer {number}") for number in range(MAX_TURNS + 2)])
    ctx = context(model)
    for number in range(MAX_TURNS + 2):
        await ask(graph, ctx, f"question {number}", turn=f"turn-{number}")
    said = [message.content for message in model.seen[-1][1:]]
    assert len(said) == 2 * MAX_TURNS + 1 and said[0] == "question 1" and said[-1] == f"question {MAX_TURNS + 1}"


async def test_the_results_list_every_tool_the_chat_has_used_once_there_are_earlier_ones(graph):
    """So "which tools did you use?" has its answer in the results panel, whatever the model says."""
    model = FakeModel(
        asks(("woo-demo__list_orders", {})), AIMessage("One order."),
        asks(("woo-demo__create_product", {"name": "Stapler"})), AIMessage("Created."),
        AIMessage("I used list orders and create product."),
    )
    ctx = context(model, FakeGateway(list_orders=ORDERS, create_product=PRODUCT))
    first = await ask(graph, ctx, "orders today?")
    assert all("Tools used" not in block["title"] for block in first["blocks"]), "one turn's calls are already its steps"
    await ask(graph, ctx, "create a Stapler", turn="turn-2")
    await answer(graph, ctx, approve=True)
    asked = await ask(graph, ctx, "which tools did you use in this conversation?", turn="turn-3")
    (used,) = asked["blocks"]
    assert (used["kind"], used["title"], used["source"]) == ("table", "Tools used in this chat (2)", "this chat")
    assert used["columns"] == ["Server", "Tool", "Outcome", "Took"]
    assert [row[:3] for row in used["rows"]] == [["Demo Store", "list orders", "1 found"], ["Demo Store", "create product", "done"]]
    assert all(row[3].endswith(" ms") for row in used["rows"])


async def test_a_saved_chat_reads_back_as_messages_and_its_last_results(graph):
    model = FakeModel(asks(("woo-demo__list_orders", {})), AIMessage("One order."))
    await ask(graph, context(model, FakeGateway(list_orders=ORDERS)), "orders today?")
    saved = await read(graph, "thread")
    assert saved["messages"] == [{"from": "you", "text": "orders today?"}, {"from": "agent", "text": "One order."}]
    assert saved["status"] == "answered" and saved["blocks"][0]["kind"] == "table" and saved["pending"] == []
    assert await read(graph, "another-thread") == {"messages": [], "reply": "", "status": "answered", "blocks": [], "steps": [], "pending": []}


# ---- build_blocks: what the results panel shows -----------------------------------

def record(result, *, name="woo-demo__list_orders", outcome="ok", write=False):
    return {"call_id": "c1", "turn": "t", "name": name, "arguments": {}, "outcome": outcome, "write": write, "ms": 12, "result": result}


SERVERS = {"woo-demo": "Demo Store"}


def test_a_list_of_records_becomes_a_table_in_plain_words():
    blocks, steps = build_blocks([record(ORDERS)], SERVERS)
    assert blocks == [
        {
            "kind": "table", "title": "Orders (1)", "source": "Demo Store · list orders", "call_id": "c1",
            "columns": ["Id", "Number", "Status", "Total", "Date created", "Payment method", "Customer", "Email", "Items"],
            "rows": [[17, "17", "On hold", "499.00 INR", "14 Oct 2026, 10:42", "Cash on delivery", "Asha Rao", "asha@example.com", "1 item"]],
        }
    ]
    assert steps == ["Demo Store: list orders, 1 found"]


def test_the_table_title_counts_every_match_not_only_the_rows_returned():
    blocks, steps = build_blocks([record({"orders": [ORDER], "total": 40})], SERVERS)
    assert blocks[0]["title"] == "Orders (40)" and steps == ["Demo Store: list orders, 40 found"]


def test_one_record_becomes_a_card():
    blocks, steps = build_blocks([record(PRODUCT, name="woo-demo__get_product")], SERVERS)
    assert blocks == [
        {
            "kind": "card", "title": "Get product", "source": "Demo Store · get product", "call_id": "c1",
            "fields": [["Id", 5], ["Name", "Test Mug"], ["Price", "299.00"], ["Status", "Publish"], ["Stock quantity", ""]],
        }
    ]
    assert steps == ["Demo Store: get product, done"]


def test_an_empty_list_becomes_a_sentence():
    blocks, steps = build_blocks([record({"orders": [], "total": 0})], SERVERS)
    assert blocks == [
        {"kind": "notice", "tone": "info", "title": "List orders", "text": "No orders found.", "source": "Demo Store · list orders", "call_id": "c1"}
    ]
    assert steps == ["Demo Store: list orders, 0 found"]


def test_a_finished_write_is_announced_and_its_record_shown():
    blocks, _ = build_blocks([record(PRODUCT, name="woo-demo__create_product", write=True)], SERVERS)
    assert blocks[0] == {
        "kind": "notice", "tone": "success", "title": "Create product", "text": "Done.",
        "source": "Demo Store · create product", "call_id": "c1",
    }
    assert blocks[1]["kind"] == "card"


def test_a_declined_write_and_an_unknown_server_are_still_readable():
    blocks, steps = build_blocks([record("The user declined this change.", name="other__do_thing", outcome="declined", write=True)], SERVERS)
    assert blocks == [
        {"kind": "notice", "tone": "warning", "title": "Do thing was not run", "text": "The user declined this change.",
         "source": "other · do thing", "call_id": "c1"}
    ]
    assert steps == ["other: do thing, declined"]
