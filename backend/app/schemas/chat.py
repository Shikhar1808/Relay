import uuid
from datetime import datetime

from pydantic import BaseModel


class ChatMessageRequest(BaseModel):
    message: str


class ChatReplyOut(BaseModel):
    reply: str
    chat_token_expires_at: datetime
    store_id: uuid.UUID | None  # the store this conversation is scoped to; None only for the admin
