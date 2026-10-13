"""SQLAlchemy ORM models for the Platform API, one module per area.

Nine tables cover this phase: users (admin + members), auth_tokens (emailed
invite / password-reset links), api_keys (gateway credentials issued by an
admin to a member), stores (provisioning stub), store_members (who can use
which store), chat_tokens (short-lived tokens the chat agent mints on behalf
of a logged-in user), conversations (one row per chat with the agent),
upstream_servers (the MCP servers the gateway forwards to, with encrypted
credentials), admin_events (an append-only record of team, key and store
changes).

Import models from this package (`from app.db.models import User`), not from
the modules inside it. A new model module MUST be imported below — that is
what registers its table on Base.metadata, which is all Alembic looks at.

The schema is managed by Alembic (backend/migrations). Changing anything
here does nothing to a database until you add a migration for it — see
"Database migrations" in IMPLEMENTATION.md.
"""
from app.db.models.admin_event import AdminEvent
from app.db.models.api_key import ApiKey
from app.db.models.base import Base
from app.db.models.conversation import Conversation
from app.db.models.store import Store, StoreMember, StoreStatus
from app.db.models.tokens import AuthToken, ChatToken, TokenPurpose
from app.db.models.upstream import UpstreamServer
from app.db.models.user import Role, User

__all__ = [
    "AdminEvent",
    "ApiKey",
    "AuthToken",
    "Base",
    "ChatToken",
    "Conversation",
    "Role",
    "Store",
    "StoreMember",
    "StoreStatus",
    "TokenPurpose",
    "UpstreamServer",
    "User",
]
