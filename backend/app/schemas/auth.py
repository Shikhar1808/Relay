import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.db.models import Role

# Applies to every password a human types in (login, admin signup,
# change/set password). Printable ASCII only: bcrypt rejects anything over 72
# BYTES, and 20 emoji are 80 bytes, so a character-count limit alone is not
# enough. System-generated temp passwords (secrets.token_urlsafe(12) -> 16
# ASCII chars) always pass.
Password = Annotated[str, Field(min_length=8, max_length=20, pattern=r"^[\x20-\x7e]+$")]

# Lowercased on the way in, everywhere an email is accepted, so "Bob@x.com"
# and "bob@x.com" are one account. EmailStr alone only lowercases the domain.
Email = Annotated[EmailStr, AfterValidator(str.lower)]

# Bounded to the users.name column (String(255)); without the bound a longer
# value reaches the database and comes back as a 500.
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class LoginRequest(BaseModel):
    email: Email
    password: Password


class AdminSignupRequest(BaseModel):
    """Only succeeds while zero admins exist — see app/services/bootstrap.py."""

    email: Email
    name: Name
    password: Password


class ChangePasswordRequest(BaseModel):
    current_password: Password
    new_password: Password


class ForgotPasswordRequest(BaseModel):
    email: Email


class SetPasswordRequest(BaseModel):
    """Redeems an emailed invite or reset token — see app/services/auth_tokens.py."""

    token: str
    new_password: Password


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
