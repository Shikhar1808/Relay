import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.db.models import StoreStatus


class CreateStoreRequest(BaseModel):
    # 200, not 255: the slug built from the name shares a String(255)
    # column and may get a numeric suffix appended.
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


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
