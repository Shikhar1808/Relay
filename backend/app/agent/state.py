"""What the agent remembers, and what it is handed.

ChatState is saved by the checkpointer after every node, so it holds nothing
secret. ChatContext is passed in with each request and never saved: the model,
the tool list as the gateway gives it to this user right now, and the function
that calls the gateway with the user's chat token inside it.
"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from operator import add
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from langgraph.managed import RemainingSteps
from mcp import types

Status = Literal["answered", "needs_confirmation", "stopped", "failed"]


class ToolCallRecord(TypedDict):
    """One tool call and its whole result. The model gets the result cut short; this is what the page draws from."""

    call_id: str
    turn: str  # the user message it was made for
    name: str  # as the gateway names it: <server>__<tool>
    arguments: dict[str, Any]
    outcome: Literal["ok", "error", "declined"]
    write: bool
    ms: int
    result: Any


class PendingCall(TypedDict):
    """A write the model asked for, waiting for the user's yes or no."""

    call_id: str
    server: str
    tool: str
    summary: str
    arguments: dict[str, Any]


class Block(TypedDict, total=False):
    """One piece of the results panel. `kind` says which of the other fields are there."""

    kind: Literal["table", "card", "notice", "raw"]
    title: str
    source: str  # which server and tool the data came from
    call_id: str
    columns: list[str]  # table
    rows: list[list[Any]]  # table
    fields: list[list[Any]]  # card: [label, value] pairs
    tone: Literal["info", "success", "warning", "error"]  # notice
    text: str  # notice, raw


class ChatInput(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    turn: str


class ChatOutput(TypedDict):
    reply: str
    status: Status
    blocks: list[Block]
    steps: list[str]
    pending: list[PendingCall]


class ChatState(ChatInput, ChatOutput):
    calls: Annotated[list[ToolCallRecord], add]
    approved: bool  # the answer to the last confirmation
    remaining_steps: RemainingSteps


@dataclass(frozen=True)
class ChatContext:
    model: Any  # a chat model with this user's tools bound, or None when no key is set
    tools: dict[str, types.Tool]  # by gateway name; the hints say which ones write
    servers: dict[str, str]  # slug -> the name people know it by
    call_tool: Callable[[str, dict[str, Any]], Awaitable[types.CallToolResult]]
    now: str  # the date and time in the store's time zone, in words
