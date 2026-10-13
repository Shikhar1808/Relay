"""The seven steps of the chat agent, and the two decisions between them.

    call_model -> check_calls -> (confirm) -> run_tools -> call_model ...   the loop
    call_model -> finish | stop -> present                                   the way out

The model only ever says which tool it wants. Every call is made here, through
the gateway, with the signed-in user's chat token (ChatContext.call_tool).
"""
import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any, Literal

import openai
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from app.agent.state import Block, ChatContext, ChatState, PendingCall, ToolCallRecord

log = logging.getLogger("uvicorn.error")  # the logger uvicorn already prints

SEPARATOR = "__"  # the gateway names tools <server>__<tool>
MAX_RESULT_CHARS = 2000  # of one tool result, as the model sees it; the page gets all of it
MAX_TURNS = 6  # earlier questions, each with its answer, that the model is reminded of
STEPS_PER_LOOP = 6  # check_calls, confirm, run_tools, call_model, finish, present

_DECLINED = "You declined this change."  # what the page shows
_DECLINED_FOR_MODEL = "The user declined this change, so it was not made. Do not try it again unless they ask."
_STOPPED = "I stopped after too many steps without reaching an answer. Try asking for less at once."
_MONEY = {"total", "subtotal", "price", "regular_price", "sale_price"}


def _system_prompt(context: ChatContext) -> str:
    servers = "\n".join(f"- {slug}: {name}" for slug, name in sorted(context.servers.items()))
    return (
        "You are the assistant in a store team's dashboard. You answer by calling the tools you are given.\n"
        f'Now: {context.now}. Use it for "today".\n'
        f"Each tool is named <server>{SEPARATOR}<tool>. The servers:\n{servers}\n"
        "Rules:\n"
        "- To read something, take the most likely meaning and say what you assumed.\n"
        "- To change something, never guess. Ask first if anything is unclear.\n"
        "- When only one server has the tool you need, use it without asking. When several fit and none was named, ask which, by name.\n"
        "- If no tool fits, say so. Do not invent an answer.\n"
        "- Write for someone who is not technical: plain words, short, no JSON and no field names.\n"
        "- Plain text only: no markdown, no asterisks, no tables.\n"
        "- The page already shows each result as a table, so do not repeat the rows. Give the summary."
    )


def _history(messages: list[AnyMessage]) -> list[AnyMessage]:
    """What the model is sent of the chat: the latest message with its own tool calls and their
    results in full, and before it only what was said, the last MAX_TURNS exchanges.

    Earlier turns go without their tool requests and results. Resending those on every call is
    what uses up a provider's tokens-a-minute limit in a long chat, and the model can look again
    when it needs the data. It also means a request that never got its result (the user wrote on
    while a confirmation was waiting, a run was cut off) is never sent: the model's API refuses those."""
    latest = max(index for index, message in enumerate(messages) if isinstance(message, HumanMessage))
    said = [
        message
        for message in messages[:latest]
        if isinstance(message, HumanMessage) or (isinstance(message, AIMessage) and not message.tool_calls)
    ]
    return said[-2 * MAX_TURNS :] + messages[latest:]


def _failed(text: str) -> dict[str, Any]:
    return {"messages": [AIMessage(text)], "status": "failed", "pending": []}


async def call_model(state: ChatState, runtime: Runtime[ChatContext]) -> dict[str, Any]:
    context = runtime.context
    if context.model is None:
        return _failed("The chat model is not set up yet. An admin has to add its key.")
    try:
        reply = await context.model.ainvoke([SystemMessage(_system_prompt(context)), *_history(state["messages"])])
    except openai.OpenAIError as exc:  # no key, rate limit, timeout, provider down
        log.warning("the chat model failed: %s", type(exc).__name__)
        return _failed("The chat model is not available right now. Try again in a minute.")
    # "pending" is emptied here because an unanswered confirmation ends when the conversation moves on.
    return {"messages": [reply], "pending": []} | ({} if reply.tool_calls else {"status": "answered"})


def after_model(state: ChatState) -> Literal["check_calls", "finish", "stop"]:
    if not state["messages"][-1].tool_calls:
        return "finish"
    return "check_calls" if state["remaining_steps"] > STEPS_PER_LOOP else "stop"


def _label(tool: str) -> str:
    return tool.replace("_", " ")


def _pending(call: dict[str, Any], servers: dict[str, str]) -> PendingCall:
    slug, _, tool = call["name"].partition(SEPARATOR)
    details = ", ".join(f"{_label(name)} {value}" for name, value in call["args"].items() if value is not None)
    return PendingCall(
        call_id=call["id"], server=servers.get(slug, slug), tool=tool,
        summary=f"{_label(tool).capitalize()}: {details}" if details else _label(tool).capitalize(),
        arguments=call["args"],
    )


def check_calls(state: ChatState, runtime: Runtime[ChatContext]) -> dict[str, Any]:
    """Sorts what the model asked for: a tool that does not say it only reads is a write, and waits for the user."""
    tools = runtime.context.tools

    def reads(name: str) -> bool:
        hints = tools[name].annotations if name in tools else None
        return bool(hints and hints.read_only_hint)

    pending = [_pending(call, runtime.context.servers) for call in state["messages"][-1].tool_calls if not reads(call["name"])]
    return {"pending": pending} | ({"status": "needs_confirmation"} if pending else {})


def after_check(state: ChatState) -> Literal["confirm", "run_tools"]:
    return "confirm" if state["pending"] else "run_tools"


def confirm(state: ChatState) -> dict[str, Any]:
    # Nothing but the question: when the user answers, LangGraph runs this node again from its first line.
    return {"approved": bool(interrupt({"pending": state["pending"]}))}


def _data(result: Any) -> Any:
    """A tool's result as a record when it is one, else as text."""
    if result.structured_content is not None and not result.is_error:
        return result.structured_content
    text = "\n".join(part.text for part in result.content if hasattr(part, "text"))
    if not result.is_error and text[:1] in "{[":
        try:
            return json.loads(text)
        except ValueError:
            pass
    return text


def _for_model(data: Any) -> str:
    text = data if isinstance(data, str) else json.dumps(data, default=str)
    return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + " ...[cut]"


async def run_tools(state: ChatState, runtime: Runtime[ChatContext]) -> dict[str, Any]:
    context = runtime.context
    writes = {call["call_id"] for call in state["pending"]}
    declined = set() if state.get("approved") else writes

    async def run(call: dict[str, Any]) -> ToolCallRecord:
        outcome, data, started = "declined", _DECLINED, time.monotonic()
        if call["id"] not in declined:
            try:
                result = await context.call_tool(call["name"], call["args"])
                outcome, data = ("error" if result.is_error else "ok"), _data(result)
            except Exception as exc:  # the gateway is down or too slow: this call failed, the others go on
                log.warning("call to %s failed: %s", call["name"], type(exc).__name__)
                outcome, data = "error", "The gateway could not be reached."
        return ToolCallRecord(
            call_id=call["id"], turn=state["turn"], name=call["name"], arguments=call["args"],
            outcome=outcome, write=call["id"] in writes, ms=round((time.monotonic() - started) * 1000), result=data,
        )

    records = await asyncio.gather(*(run(call) for call in state["messages"][-1].tool_calls))
    return {
        "messages": [
            ToolMessage(
                _DECLINED_FOR_MODEL if record["outcome"] == "declined" else _for_model(record["result"]),
                tool_call_id=record["call_id"],
            )
            for record in records
        ],
        "calls": records,
        "pending": [],
        "approved": False,
    }


def finish(state: ChatState) -> dict[str, Any]:
    return {"reply": state["messages"][-1].text}


def stop(state: ChatState) -> dict[str, Any]:
    return {"messages": [AIMessage(_STOPPED)], "reply": _STOPPED, "status": "stopped"}


def present(state: ChatState, runtime: Runtime[ChatContext]) -> dict[str, Any]:
    servers = runtime.context.servers
    this_turn = [call for call in state["calls"] if call["turn"] == state["turn"]]
    blocks, steps = build_blocks(this_turn, servers)
    if len(state["calls"]) > len(this_turn):
        # Earlier answers used tools too. The whole chat's list goes in the results, so "which tools did you
        # use?" is answered there from the record, whatever the model says. (One turn's own calls are its steps.)
        blocks.append(tools_used(state["calls"], servers))
    return {"blocks": blocks, "steps": steps}


# ---- present: tool results in plain words ------------------------------------------

def _cell(name: str, value: Any, row: dict[str, Any]) -> Any:
    if value is None:
        return ""
    if isinstance(value, list):
        return f"{len(value)} item" + ("" if len(value) == 1 else "s")
    if isinstance(value, dict):
        return json.dumps(value, default=str)
    if isinstance(value, str):
        if name == "status":
            return value.replace("-", " ").replace("_", " ").capitalize()
        if name in _MONEY and row.get("currency"):
            return f"{value} {row['currency']}"
        if name.startswith("date"):
            try:
                moment = datetime.fromisoformat(value)
            except ValueError:
                return value
            return f"{moment.day} {moment:%b %Y, %H:%M}"
    return value


def _fields(row: dict[str, Any]) -> list[str]:
    """The fields to show, in the tool's own order. The currency is written beside each amount instead."""
    shown_with_amounts = "currency" in row and any(name in _MONEY for name in row)
    return [name for name in row if not (name == "currency" and shown_with_amounts)]


def _listing(result: Any) -> tuple[list[dict[str, Any]], str, int] | None:
    """The rows, what they are called and how many match in all, when the result is a list of records:
    either a bare list, or an object holding one list beside nothing but counts ({"orders": [...], "total": 3})."""

    def records(value: Any) -> bool:
        return isinstance(value, list) and all(isinstance(item, dict) for item in value)

    if records(result):
        return result, "results", len(result)
    if isinstance(result, dict):
        lists = [name for name, value in result.items() if records(value)]
        others = [value for name, value in result.items() if name not in lists]
        if len(lists) == 1 and all(isinstance(value, int | float) and not isinstance(value, bool) for value in others):
            rows = result[lists[0]]
            return rows, _label(lists[0]), result["total"] if isinstance(result.get("total"), int) else len(rows)
    return None


def _ended(call: ToolCallRecord) -> str:
    """How a call ended, in a word or two."""
    if call["outcome"] != "ok":
        return "declined" if call["outcome"] == "declined" else "failed"
    listing = _listing(call["result"])
    return f"{listing[2]} found" if listing is not None else "done"


def tools_used(calls: list[ToolCallRecord], servers: dict[str, str]) -> Block:
    """Every tool call of a chat, oldest first, as one table."""
    rows = []
    for call in calls:
        slug, _, tool = call["name"].partition(SEPARATOR)
        rows.append([servers.get(slug, slug), _label(tool), _ended(call), f"{call['ms']} ms"])
    return Block(
        kind="table", title=f"Tools used in this chat ({len(calls)})", source="this chat", call_id="chat",
        columns=["Server", "Tool", "Outcome", "Took"], rows=rows,
    )


def build_blocks(calls: list[ToolCallRecord], servers: dict[str, str]) -> tuple[list[Block], list[str]]:
    """What the results panel shows for one turn, and the calls behind it in words. The block is
    chosen by the shape of each result, never by the tool's name, so any server's tools are covered."""
    blocks: list[Block] = []
    steps: list[str] = []
    for call in calls:
        slug, _, tool = call["name"].partition(SEPARATOR)
        server, label, result = servers.get(slug, slug), _label(tool), call["result"]
        title = label.capitalize()
        origin = {"source": f"{server} · {label}", "call_id": call["call_id"]}
        if call["outcome"] != "ok":
            declined = call["outcome"] == "declined"
            blocks.append(
                Block(kind="notice", tone="warning" if declined else "error",
                      title=f"{title} was not run" if declined else f"{title} failed", text=str(result), **origin)
            )
        else:
            if call["write"]:
                blocks.append(Block(kind="notice", tone="success", title=title, text="Done.", **origin))
            listing = _listing(result)
            if listing is not None:
                rows, plural, total = listing
                if rows:
                    names = _fields(rows[0])
                    blocks.append(
                        Block(kind="table", title=f"{plural.capitalize()} ({total})",
                              columns=[_label(name).capitalize() for name in names],
                              rows=[[_cell(name, row.get(name), row) for name in names] for row in rows], **origin)
                    )
                else:
                    blocks.append(Block(kind="notice", tone="info", title=title, text=f"No {plural} found.", **origin))
            elif isinstance(result, dict):
                fields = [[_label(name).capitalize(), _cell(name, result[name], result)] for name in _fields(result)]
                blocks.append(Block(kind="card", title=title, fields=fields, **origin))
            else:
                blocks.append(Block(kind="raw", title=title, text=str(result), **origin))
        steps.append(f"{server}: {label}, {_ended(call)}")
    return blocks, steps
