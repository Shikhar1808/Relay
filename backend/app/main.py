import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agent.graph import open_graph
from app.api.routes import audit, auth, chat, internal, stores, team, upstreams
from app.core.config import settings
from app.services.bootstrap import bootstrap_admin, bootstrap_first_store
from app.services.conversations import purge

log = logging.getLogger("uvicorn.error")  # the logger uvicorn already prints


async def _purge_old_chats(checkpointer: BaseCheckpointSaver) -> None:
    """Once at startup and then once a day. A failed round is logged and tried again at the next."""
    while True:
        try:
            await purge(checkpointer)
        except Exception:
            log.exception("could not delete old chats")
        await asyncio.sleep(24 * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # The schema is NOT created here: `alembic upgrade head` runs before
    # uvicorn starts (see the Dockerfile CMD).
    await bootstrap_admin()
    await bootstrap_first_store()
    async with open_graph() as (app.state.agent, checkpointer):
        cleaner = asyncio.create_task(_purge_old_chats(checkpointer))
        try:
            yield
        finally:
            cleaner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cleaner


app = FastAPI(title="Relay Platform API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,  # the dashboard's session is a cookie (app/core/deps.py: session_token)
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(team.router)
app.include_router(stores.router)
app.include_router(upstreams.router)
app.include_router(audit.router)
app.include_router(chat.router)
app.include_router(internal.router)
app.include_router(internal.metrics_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
