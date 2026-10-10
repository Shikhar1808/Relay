"""Service-to-service schemas, used by the future MCP Gateway."""
import uuid

from pydantic import BaseModel

from app.db.models import Role


class ResolveTokenRequest(BaseModel):
    token: str


class ResolveTokenOut(BaseModel):
    user_id: uuid.UUID
    role: Role
    token_type: str  # "api_key" | "chat_token"
    # The ONLY store this caller may touch. The gateway must take the store
    # from here, never from the request or the LLM. None means: for a member,
    # no store at all (deny every store tool call); for the admin, no single
    # store (the admin may act on any).
    store_id: uuid.UUID | None
