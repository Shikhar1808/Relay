"""The chat agent's graph, and what the API does with it: run a turn, read a saved chat.

            +-------------------------------------------+
            v                                           | results
    START -> call_model -> check_calls -> run_tools ----+
            |               |             ^
            |               +-> confirm --+   (waits here for the user)
            |
            +-> finish -+
            +-> stop ---+-> present -> END
"""
import contextlib
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, StateSnapshot
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.agent import nodes
from app.agent.state import ChatContext, ChatInput, ChatOutput, ChatState
from app.core.config import settings

# One pass of the loop is up to four steps. Without a limit a confused model would loop until the provider's quota is gone.
RECURSION_LIMIT = 30
_ASK_FIRST = "I need your go-ahead before I change anything."
# What a chat has to show before anything was asked in it. Every reply is this, with some of it filled in.
EMPTY: dict[str, Any] = {"reply": "", "status": "answered", "blocks": [], "steps": [], "pending": []}


def build_graph(checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    graph = StateGraph(ChatState, ChatContext, input_schema=ChatInput, output_schema=ChatOutput)
    for node in (nodes.call_model, nodes.check_calls, nodes.confirm, nodes.run_tools, nodes.finish, nodes.stop, nodes.present):
        graph.add_node(node.__name__, node)
    graph.add_edge(START, "call_model")
    graph.add_conditional_edges("call_model", nodes.after_model)
    graph.add_conditional_edges("check_calls", nodes.after_check)
    graph.add_edge("confirm", "run_tools")
    graph.add_edge("run_tools", "call_model")
    graph.add_edge("finish", "present")
    graph.add_edge("stop", "present")
    graph.add_edge("present", END)
    return graph.compile(checkpointer=checkpointer)


def _config(thread_id: str, limit: int = RECURSION_LIMIT) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": limit}


def _output(snapshot: StateSnapshot) -> dict[str, Any]:
    values = snapshot.values
    if snapshot.interrupts:  # waiting at `confirm`: this turn's results are drawn once it is over
        return EMPTY | {"reply": _ASK_FIRST, "status": "needs_confirmation", "pending": values["pending"]}
    return EMPTY | {name: values[name] for name in ("reply", "status", "blocks", "steps") if name in values}


async def run(
    graph: CompiledStateGraph, thread_id: str, context: ChatContext, graph_input: dict[str, Any] | Command, *, limit: int = RECURSION_LIMIT
) -> dict[str, Any]:
    """One turn: a new message ({"messages": [...], "turn": id}) or the answer to a confirmation
    (Command(resume=True or False)). Returns what the agent has to show for it."""
    config = _config(thread_id, limit)
    await graph.ainvoke(graph_input, config, context=context)
    # Read from what was saved, not from what ainvoke returned: a run that stopped to ask is told apart there.
    return _output(await graph.aget_state(config))


async def read(graph: CompiledStateGraph, thread_id: str) -> dict[str, Any]:
    """A saved chat as the page shows it: what was said, and the results of the last answer."""
    snapshot = await graph.aget_state(_config(thread_id))
    said = []
    for message in snapshot.values.get("messages", []):
        if isinstance(message, HumanMessage):
            said.append({"from": "you", "text": message.text})
        elif isinstance(message, AIMessage) and message.text and not message.tool_calls:
            said.append({"from": "agent", "text": message.text})
    return {"messages": said} | _output(snapshot)


async def waiting(graph: CompiledStateGraph, thread_id: str) -> str | None:
    """The turn this chat is waiting on a yes or no for, or None when it is not waiting."""
    snapshot = await graph.aget_state(_config(thread_id))
    return snapshot.values["turn"] if snapshot.interrupts else None


@contextlib.asynccontextmanager
async def open_graph() -> AsyncIterator[tuple[CompiledStateGraph, BaseCheckpointSaver]]:
    """The graph with its state kept in Postgres, for as long as the app runs. LangGraph's
    tables live in their own schema (made by migration 0006), which keeps them out of
    Alembic's sight and away from the gateway's database role."""
    async with AsyncConnectionPool(
        settings.database_url.replace("+asyncpg", "", 1),  # the same database, through psycopg, which the saver needs
        max_size=5,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row, "options": "-c search_path=langgraph"},
    ) as pool:
        saver = AsyncPostgresSaver(pool)
        await saver.setup()  # creates or upgrades its tables; safe on every start
        yield build_graph(saver), saver
