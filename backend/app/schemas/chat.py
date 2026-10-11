import uuid
from datetime import datetime

from pydantic import BaseModel


class ChatMessageRequest(BaseModel):
    message: str


class ChatReplyOut(BaseModel):
    reply: str
    chat_token_expires_at: datetime
    store_ids: list[uuid.UUID]  # the stores this conversation may use (every store for the admin)
