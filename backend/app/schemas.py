import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import Role, StoreStatus

# ---- auth ----------------------------------------------------------------

# Bounds apply to every password a human types in (login, admin signup).
# System-generated temp passwords (secrets.token_urlsafe(12) -> 16 chars)
# always fall inside this range, so they stay loggable after this change.
_PASSWORD_MIN = 8
_PASSWORD_MAX = 20


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=_PASSWORD_MIN, max_length=_PASSWORD_MAX)


class AdminSignupRequest(BaseModel):
    """Only succeeds while zero admins exist — see app/bootstrap.py."""

    email: EmailStr
    name: str
    password: str = Field(min_length=_PASSWORD_MIN, max_length=_PASSWORD_MAX)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str
    role: Role
    is_active: bool
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---- team ------------------------------------------------------------------


class CreateMemberRequest(BaseModel):
    email: EmailStr
    name: str


class MemberCreatedOut(BaseModel):
    user: UserOut
    temporary_password: str  # shown once; no email delivery in this phase


class CreateApiKeyRequest(BaseModel):
    name: str | None = None


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    prefix: str
    name: str | None
    revoked: bool
    created_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None


class ApiKeyCreatedOut(BaseModel):
    id: uuid.UUID
    raw_key: str  # shown once; only the hash is persisted
    prefix: str
    name: str | None
    created_at: datetime


# ---- stores ----------------------------------------------------------------


class CreateStoreRequest(BaseModel):
    name: str


class StoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    status: StoreStatus
    url: str | None
    created_by: uuid.UUID
    created_at: datetime


class AssignMemberRequest(BaseModel):
    user_id: uuid.UUID


class StoreMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    store_id: uuid.UUID
    user_id: uuid.UUID
    assigned_at: datetime


# ---- chat --------------------------------------------------------------------


class ChatMessageRequest(BaseModel):
    message: str


class ChatReplyOut(BaseModel):
    reply: str
    chat_token_expires_at: datetime


# ---- internal (service-to-service, used by the future MCP Gateway) -----------


class ResolveTokenRequest(BaseModel):
    token: str


class ResolveTokenOut(BaseModel):
    user_id: uuid.UUID
    role: Role
    token_type: str  # "api_key" | "chat_token"
