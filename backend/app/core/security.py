"""Password hashing, JWT session tokens, and random secret generation for
gateway API keys and short chat tokens.

Three distinct credential types live in this module's vocabulary:
  - dashboard session JWT  -> create_access_token / decode_access_token
  - gateway API key        -> generate_api_key (admin issues, member uses in MCP clients)
  - short chat token       -> generate_chat_token (chat backend mints, never shown to caller)
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import settings

JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(user_id: uuid.UUID, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Raises jwt.PyJWTError on invalid/expired token — caller handles it."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGORITHM])


def sha256_hex(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """Returns (raw_key, key_hash, display_prefix). Only the hash is persisted."""
    raw = f"umk_{secrets.token_hex(24)}"
    return raw, sha256_hex(raw), raw[:12]


def generate_chat_token() -> tuple[str, str]:
    """Returns (raw_token, token_hash). The raw token stays server-side —
    it is used internally to call the gateway, never returned to the browser.
    """
    raw = f"chat_{secrets.token_urlsafe(32)}"
    return raw, sha256_hex(raw)


def generate_temp_password() -> str:
    """Used when an admin creates a member account. Returned once in the API
    response for the admin to share out-of-band; the member is also emailed
    a set-password link (app/services/mailer.py), so this is a fallback, kept while
    there is no dashboard to drive the invite flow.
    """
    return secrets.token_urlsafe(12)
