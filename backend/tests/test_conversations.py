"""Chats and their tokens (app/services/conversations.py), with the database, Redis and
LangGraph's saver replaced by small fakes. The real thing, end to end, is scripts/smoke_test.py.
"""
import operator
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.sql import Delete, Select

from app.core.config import settings
from app.core.crypto import decrypt
from app.db.models import ChatToken, Conversation
from app.services import conversations

pytestmark = pytest.mark.anyio


class FakeDb:
    """A session that answers every select with `rows`, and keeps what was run, deleted and added, in order."""

    def __init__(self, rows=()) -> None:
        self.rows, self.log, self.added = list(rows), [], []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *error) -> None:
        return None

    async def execute(self, statement):
        self.log.append(statement)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: self.rows))

    async def delete(self, row) -> None:
        self.log.append(("row deleted", row.id))

    def add(self, row) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        for row in self.added:  # what the database does on insert
            row.id = row.id or uuid.uuid4()

    async def commit(self) -> None:
        return None


class FakeSaver:
    def __init__(self, log: list) -> None:
        self.log = log

    async def adelete_thread(self, thread_id: str) -> None:
        self.log.append(("state deleted", thread_id))


class FakeRedis:
    def __init__(self) -> None:
        self.kept: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.kept[key] = value


async def test_only_chats_past_their_retention_are_deleted_what_was_said_first(monkeypatch):
    old = SimpleNamespace(id=uuid.uuid4())
    db = FakeDb([old])
    monkeypatch.setattr(conversations, "AsyncSessionLocal", lambda: db)
    started = datetime.now(timezone.utc)

    assert await conversations.purge(FakeSaver(db.log)) == 1

    chosen, *after = db.log
    # Which chats go is this one comparison: last used BEFORE now minus the retention. Flipped, it deletes every fresh chat.
    assert isinstance(chosen, Select)
    clause = chosen.whereclause
    assert clause.left.name == "last_used_at" and clause.operator is operator.lt
    retention = timedelta(days=settings.chat_retention_days)
    assert started - retention <= clause.right.value <= datetime.now(timezone.utc) - retention
    # The saved state goes before the row: stopped in between, the row is still there for the next run to find.
    assert after[:2] == [("state deleted", str(old.id)), ("row deleted", old.id)]
    assert isinstance(after[2], Delete) and after[2].table.name == "chat_tokens"


async def test_a_chat_whose_token_cannot_be_decrypted_is_ended_and_a_new_one_opened(monkeypatch):
    """After GATEWAY_ENCRYPTION_KEY changes, the stored token of an open chat is lost for good."""
    user = SimpleNamespace(id=uuid.uuid4())
    unreadable = SimpleNamespace(ciphertext=bytes(40), token_hash="old", expires_at=None)
    ended = []

    async def current_chat(db, who):
        return SimpleNamespace(id=uuid.uuid4(), last_used_at=None), unreadable

    async def end_chat(db, redis, who):
        ended.append(who.id)

    monkeypatch.setattr(conversations, "current_chat", current_chat)
    monkeypatch.setattr(conversations, "end_chat", end_chat)
    db, redis = FakeDb(), FakeRedis()

    conversation, raw = await conversations.open_chat(db, redis, user, "  hello   there ")

    assert ended == [user.id], "the chat that cannot be continued is revoked"
    token, opened = db.added
    assert isinstance(token, ChatToken) and isinstance(opened, Conversation) and opened is conversation
    assert opened.chat_token_id == token.id and opened.title == "hello there"
    assert raw.startswith("chat_") and decrypt(token.ciphertext) == raw
    assert redis.kept == {f"chat_token:{token.token_hash}": str(user.id)}
