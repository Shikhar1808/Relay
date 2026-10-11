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


def test_access_token_round_trip():
    user_id = uuid.uuid4()
    payload = security.decode_access_token(security.create_access_token(user_id, "member"))
    assert payload["sub"] == str(user_id) and payload["role"] == "member"


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


def test_api_key_shape_and_that_only_its_hash_is_derivable():
    raw, key_hash, prefix = security.generate_api_key()
    assert raw.startswith("umk_") and len(raw) == 4 + 48
    assert key_hash == security.sha256_hex(raw) and len(key_hash) == 64
    assert prefix == raw[:12] and raw not in key_hash
    assert security.generate_api_key()[0] != raw


def test_chat_token_shape():
    raw, token_hash = security.generate_chat_token()
    assert raw.startswith("chat_") and token_hash == security.sha256_hex(raw)
    assert security.generate_chat_token()[0] != raw


def test_temp_password_passes_the_login_password_rule():
    password = security.generate_temp_password()
    assert 8 <= len(password) <= 20 and password.isascii() and password.isprintable()
