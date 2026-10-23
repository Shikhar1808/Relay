"""Passwords, dashboard JWTs, and the generated API keys and chat tokens."""
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core import security
from app.core.config import settings


def test_password_hash_verifies_and_is_salted():
    first, second = security.hash_password("correct horse"), security.hash_password("correct horse")
    assert first != second, "the same password must not hash to the same value twice"
    assert "correct horse" not in first
    assert security.verify_password("correct horse", first)
    assert not security.verify_password("correct horsf", first)


def test_access_token_rejects_tampering_a_foreign_secret_and_expiry():
    token = security.create_access_token(uuid.uuid4(), "member")
    with pytest.raises(jwt.PyJWTError):
        security.decode_access_token(token[:-2] + ("AA" if not token.endswith("AA") else "BB"))

    forged = jwt.encode({"sub": str(uuid.uuid4()), "role": "admin"}, "some-other-secret-some-other-secret", algorithm="HS256")
    with pytest.raises(jwt.PyJWTError):
        security.decode_access_token(forged)

    expired = jwt.encode(
        {"sub": str(uuid.uuid4()), "role": "admin", "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        security.decode_access_token(expired)


def test_access_token_rejects_the_none_algorithm():
    unsigned = jwt.encode({"sub": str(uuid.uuid4()), "role": "admin"}, key=None, algorithm="none")
    with pytest.raises(jwt.PyJWTError):
        security.decode_access_token(unsigned)


