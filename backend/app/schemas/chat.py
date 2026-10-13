import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Status = Literal["answered", "needs_confirmation", "stopped", "failed"]


class ChatMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ChatConfirmRequest(BaseModel):
    approve: bool  # yes or no to every change the agent is waiting to make


class ChatResult(BaseModel):
    """What the agent has to show for its last answer. `reply` belongs in the chat;
    `blocks` (tables, cards, notices, each naming the tool it came from) and `steps`
    (the calls, in words) belong in the results panel. Their shapes: app/agent/state.py."""

    reply: str
    status: Status
    blocks: list[dict[str, Any]]
    steps: list[str]
    pending: list[dict[str, Any]]  # changes waiting for a yes or no, when status is needs_confirmation


class ChatReplyOut(ChatResult):
    conversation_id: uuid.UUID
    store_ids: list[uuid.UUID]  # the stores this conversation may use (every store for the admin)


class ChatOut(ChatResult):
    """A whole chat, as the page shows it after a reload or from the list of earlier ones."""

    conversation_id: uuid.UUID | None  # None: no chat is open
    messages: list[dict[str, str]]  # {"from": "you" | "agent", "text": ...}, oldest first


class ChatSummaryOut(BaseModel):
    conversation_id: uuid.UUID
    title: str
    last_used_at: datetime
