"""The dashboard's chat with the agent (app/agent).

POST /chat/message        say something; opens a chat if none is open
POST /chat/confirm        yes or no to the changes the agent is waiting to make
GET  /chat                the open chat, for a page reload
POST /chat/end            end the open chat
GET  /chat/history        earlier chats, to read
GET  /chat/history/{id}   one of them

The agent acts as the signed-in user: every tool call goes through the MCP
gateway with a chat token minted for this user (app/services/conversations.py).
The token never leaves the server: not to the browser, not to the model.
"""
import functools
import logging
import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.types import Command
from mcp import types
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import gateway
from app.agent import graph as agent
from app.agent.nodes import SEPARATOR
from app.agent.state import ChatContext
from app.core.config import settings
from app.core.deps import common_rate_limit, get_current_user, get_redis, user_rate_limit
from app.db.models import Conversation, Role, UpstreamServer, User
from app.db.session import get_db
from app.schemas.chat import ChatConfirmRequest, ChatMessageRequest, ChatOut, ChatReplyOut, ChatSummaryOut
from app.services import conversations
from app.services.stores import stores_for_user

log = logging.getLogger("uvicorn.error")  # the logger uvicorn already prints

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(get_current_user), Depends(common_rate_limit)])

# On top of the common limit, for the two routes that run the agent: each one costs model calls.
_chat_rate_limit = user_rate_limit(capacity=10, refill_per_minute=10)


@functools.cache
def _llm() -> ChatOpenAI:
    """One client for every request: each ChatOpenAI opens its own connection pool."""
    return ChatOpenAI(
        base_url=settings.llm_base_url, api_key=settings.llm_api_key, model=settings.llm_model, timeout=30, max_retries=1
    )


def _model(tools: list[types.Tool]) -> Any:
    """The chat model with this user's tools, or None while no key is set."""
    if settings.llm_api_key is None:
        return None
    return _llm().bind_tools(
        [
            {"type": "function", "function": {"name": tool.name, "description": tool.description or "", "parameters": tool.input_schema}}
            for tool in tools
        ]
    )


async def _servers(db: AsyncSession, tools: list[types.Tool]) -> dict[str, str]:
    """slug -> the name people know the server by, for the servers these tools come from."""
    slugs = {tool.name.partition(SEPARATOR)[0] for tool in tools}
    named = await db.execute(select(UpstreamServer.slug, UpstreamServer.name).where(UpstreamServer.slug.in_(slugs)))
    return {slug: name for slug, name in named}


def _now() -> str:
    now = datetime.now(ZoneInfo(settings.store_timezone))
    return f"{now:%A} {now.day} {now:%B %Y, %H:%M} ({settings.store_timezone})"


async def _turn(
    request: Request, db: AsyncSession, conversation: Conversation, chat_token: str, turn: str, graph_input: dict[str, Any] | Command, store_ids: list[uuid.UUID]
) -> dict[str, Any]:
    """Runs the agent for one message, or one answer to a confirmation. Whatever goes wrong
    on the way, the chat gets a reply it can show: a failure here is not a bare 500."""
    about = {"conversation_id": conversation.id, "store_ids": store_ids}
    sent = gateway.headers(chat_token, str(conversation.id), turn)
    try:
        # Asked on every turn, never remembered: a server disabled a moment ago is gone from the agent's tools at once.
        tools = await gateway.list_tools(sent)
        servers = await _servers(db, tools)
    except Exception as exc:  # the gateway is down or slow
        log.warning("cannot list the gateway's tools: %s", type(exc).__name__)
        return agent.EMPTY | about | {"reply": "The tool gateway cannot be reached right now. Try again in a minute.", "status": "failed"}

    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        return await gateway.call_tool(sent, name, arguments)

    context = ChatContext(
        model=_model(tools), tools={tool.name: tool for tool in tools}, servers=servers, call_tool=call_tool, now=_now()
    )
    try:
        return await agent.run(request.app.state.agent, str(conversation.id), context, graph_input) | about
    except Exception:  # a bug or the database: logged in full, and the chat still gets an answer
        log.exception("the chat agent failed")
        return agent.EMPTY | about | {"reply": "Something went wrong on our side. Try again.", "status": "failed"}


@router.post("/message", response_model=ChatReplyOut, dependencies=[Depends(_chat_rate_limit)])
async def chat_message(
    body: ChatMessageRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    # The stores are decided here, server-side, from the logged-in user — never
    # from the message text. The agent's gateway calls are scoped the same way,
    # independently, by /internal/auth/resolve.
    stores = await stores_for_user(db, user)
    if user.role != Role.admin and not stores:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not assigned to a store yet")

    # shortcut: two messages sent to one chat in the same instant run side by side and can interleave
    # in the saved state. The page sends one at a time; take a per-chat lock in Redis if an API client needs it.
    conversation, chat_token = await conversations.open_chat(db, redis, user, body.message)
    turn = str(uuid.uuid4())  # one id per message: its tool calls carry it in the audit log
    graph_input = {"messages": [HumanMessage(body.message)], "turn": turn}
    return await _turn(request, db, conversation, chat_token, turn, graph_input, [store.id for store in stores])


@router.post("/confirm", response_model=ChatReplyOut, dependencies=[Depends(_chat_rate_limit)])
async def chat_confirm(
    body: ChatConfirmRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    nothing_waiting = HTTPException(status.HTTP_409_CONFLICT, "Nothing is waiting for your answer")
    found = await conversations.current_chat(db, user)
    # The id of the message these changes were asked in, so their tool calls are logged under it.
    turn = await agent.waiting(request.app.state.agent, str(found[0].id)) if found else None
    if turn is None:
        raise nothing_waiting
    stores = await stores_for_user(db, user)
    conversation, chat_token = await conversations.open_chat(db, redis, user, "")  # the same chat, its idle time pushed out
    if conversation.id != found[0].id:  # it ended in this very instant (ran out, or its token was lost): a new, empty chat came back
        raise nothing_waiting
    return await _turn(request, db, conversation, chat_token, turn, Command(resume=body.approve), [store.id for store in stores])


@router.get("", response_model=ChatOut)
async def open_chat(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    found = await conversations.current_chat(db, user)
    if found is None:
        return agent.EMPTY | {"conversation_id": None, "messages": []}
    return await agent.read(request.app.state.agent, str(found[0].id)) | {"conversation_id": found[0].id}


@router.post("/end", status_code=status.HTTP_204_NO_CONTENT)
async def end_chat(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db), redis: Redis = Depends(get_redis)
) -> Response:
    await conversations.end_chat(db, redis, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/history", response_model=list[ChatSummaryOut])
async def chat_history(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[ChatSummaryOut]:
    return [
        ChatSummaryOut(conversation_id=chat.id, title=chat.title, last_used_at=chat.last_used_at)
        for chat in await conversations.past_chats(db, user)
    ]


@router.get("/history/{conversation_id}", response_model=ChatOut)
async def past_chat(
    conversation_id: uuid.UUID, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    chat = await conversations.past_chat(db, user, conversation_id)
    if chat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such chat")
    saved = await agent.read(request.app.state.agent, str(chat.id))
    # A chat that ended while it waited for an answer has nothing to confirm any more.
    return saved | {"conversation_id": chat.id, "pending": []} | ({"status": "answered"} if saved["status"] == "needs_confirmation" else {})
