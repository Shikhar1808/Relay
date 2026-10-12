"""Settings must refuse a bad secret at startup, not on first use."""
import base64

import pytest
from pydantic import ValidationError

from app.core.config import Settings

GOOD = {
    "database_url": "postgresql+asyncpg://u:p@localhost/db",
    "jwt_secret": "x" * 40,
    "gateway_encryption_key": base64.b64encode(bytes(32)).decode(),
    "internal_service_token": "t" * 32,
}


def make(**overrides) -> Settings:
    return Settings(_env_file=None, **{**GOOD, **overrides})


def test_good_settings_load():
    assert make().chat_token_ttl_minutes == 30


@pytest.mark.parametrize(
    "key",
    [
        base64.b64encode(bytes(16)).decode(),  # AES-128 size
        base64.b64encode(bytes(33)).decode(),
        "not base64 at all!",
        "",
    ],
)
def test_encryption_key_must_be_32_bytes_of_base64(key):
    with pytest.raises(ValidationError, match="GATEWAY_ENCRYPTION_KEY"):
        make(gateway_encryption_key=key)


def test_service_token_must_be_long():
    with pytest.raises(ValidationError, match="INTERNAL_SERVICE_TOKEN"):
        make(internal_service_token="t" * 31)


@pytest.mark.parametrize("missing", ["jwt_secret", "gateway_encryption_key", "internal_service_token", "database_url"])
def test_required_secrets_have_no_default(missing, monkeypatch):
    monkeypatch.delenv(missing.upper(), raising=False)  # conftest sets it for the other tests
    values = {k: v for k, v in GOOD.items() if k != missing}
    with pytest.raises(ValidationError, match=missing):
        Settings(_env_file=None, **values)


def test_blank_optional_values_mean_unset():
    settings = make(admin_email="", admin_password="", smtp_password="")
    assert settings.admin_email is None and settings.admin_password is None and settings.smtp_password is None


def test_the_first_stores_mcp_server_is_not_set_unless_both_its_address_and_token_are_given():
    assert make().woo_mcp_url is None and make().woo_mcp_token is None
    # docker-compose passes "" for a variable that is not in .env
    blank = make(woo_mcp_url="", woo_mcp_token="")
    assert blank.woo_mcp_url is None and blank.woo_mcp_token is None
    assert (make().first_store_subdomain, make().first_store_url) == ("demo", "http://localhost:8081")


def test_admin_email_is_lowercased_like_every_login_email():
    assert make(admin_email="Admin@Example.COM").admin_email == "admin@example.com"


@pytest.mark.parametrize("password", ["short", "x" * 21, "pässword1"])
def test_admin_password_must_be_one_the_login_form_accepts(password):
    with pytest.raises(ValidationError, match="ADMIN_PASSWORD"):
        make(admin_password=password)


def test_only_the_frontend_may_call_the_api_with_the_session_cookie_by_default():
    assert make().allowed_origins == ["http://localhost:3000"]
    assert make(app_base_url="https://app.example.com/").allowed_origins == ["https://app.example.com"]
    assert make(cors_origins=["https://a.example", "https://b.example"]).allowed_origins == ["https://a.example", "https://b.example"]


def test_a_wildcard_origin_is_refused_because_sessions_are_cookies():
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        make(cors_origins=["*"])
