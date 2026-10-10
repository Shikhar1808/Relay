import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.auth import Email, Name, UserOut


class CreateMemberRequest(BaseModel):
    email: Email
    name: Name


class MemberCreatedOut(BaseModel):
    user: UserOut
    temporary_password: str  # shown once; the member is also emailed a set-password link


class CreateApiKeyRequest(BaseModel):
    name: Name | None = None


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
