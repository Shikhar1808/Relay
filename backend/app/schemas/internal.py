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
    # The ONLY stores this caller may touch (every store for the admin). The
    # gateway must take them from here, never from the request or the LLM, and
    # filter by them for everyone: an empty list means no store at all (deny
    # every store tool call), with no special case for the admin's role.
    store_ids: list[uuid.UUID]
