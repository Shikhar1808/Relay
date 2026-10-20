import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from app.db.models import StoreStatus
from app.services.stores import check_subdomain


class CreateStoreRequest(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    # Also the store's slug, its namespace store-<subdomain> and its server woo-<subdomain>.
    subdomain: str

    @field_validator("subdomain")
    @classmethod
    def _one_subdomain_rule(cls, value: str) -> str:
        return check_subdomain(value)


class DeleteStoreRequest(BaseModel):
    confirm_name: str  # compared with the store's name by the route
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class StoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    status: StoreStatus
    step: str | None
    error: str | None
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
