"""Environment-driven settings. Values come from process env vars (docker-compose)
or a local .env file (bare `uvicorn` runs). Field names match env vars case-insensitively.
"""
import base64

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str
    jwt_expire_minutes: int = 720  # 12h dashboard session. No refresh-token flow in this phase.

    # Optional: when set, the first admin is created automatically on startup
    # (see app/services/bootstrap.py). When unset, use POST /auth/admin/signup instead
    # — both paths enforce the same "exactly one admin, ever" rule.
    admin_email: str | None = None
    admin_password: str | None = None

    # A chat ends when it has been idle this long: every message pushes its token's expiry out again.
    chat_token_ttl_minutes: int = 30

    # AES-256-GCM key for upstream MCP server credentials (app/core/crypto.py):
    # 32 random bytes, base64. The gateway holds the same key to decrypt them.
    gateway_encryption_key: str
    # Shared secret the gateway sends to /internal/* (app/api/routes/internal.py).
    internal_service_token: str
    # The gateway's private port, where its audit log is written to and read
    # (app/services/gateway_audit.py). The same service token is sent there.
    gateway_internal_url: str = "http://gateway:9090"

    # Outbound email over SMTP (see app/services/mailer.py). Defaults are Resend's SMTP
    # relay with its shared test sender, which only delivers to the Resend
    # account owner's own address — set SMTP_FROM to an address on a verified
    # domain to mail anyone else. SMTP_PASSWORD is the Resend API key; while
    # it is unset nothing is sent and emails are written to the log instead.
    smtp_host: str = "smtp.resend.com"
    smtp_port: int = 587
    smtp_user: str = "resend"
    smtp_password: str | None = None
    smtp_from: str = "Urumi <onboarding@resend.dev>"

    # Base of the links in invite/reset emails: the frontend's public URL
    # (it serves /set-password).
    app_base_url: str = "http://localhost:3000"
    invite_token_ttl_hours: int = 72
    reset_token_ttl_minutes: int = 30

    # Sites whose pages may call this API with the signed-in user's session cookie.
    # Unset means the frontend's own address, APP_BASE_URL. Never "*": with a cookie
    # session that would let any site the user visits act as them.
    cors_origins: list[str] | None = None

    # The first store and its WooCommerce MCP server, registered on startup the way a
    # later store is (app/services/bootstrap.py). With the server's address or its token
    # unset, nothing is registered. The token is what the gateway presents to that server.
    first_store_subdomain: str = "demo"
    first_store_name: str = "Demo Store"
    first_store_url: str = "http://localhost:8081"
    woo_mcp_url: str | None = None
    woo_mcp_token: str | None = None

    # The chat agent (app/agent). The model is reached in the OpenAI chat format, so the
    # address can be a provider or an LLM gateway in front of several. With no key the
    # chat says it is not set up and everything else works.
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str | None = None
    llm_model: str = "openai/gpt-oss-20b"
    # The gateway's MCP endpoint as this backend reaches it: the agent calls tools there like any MCP client.
    gateway_mcp_url: str = "http://gateway:8080/mcp"
    # "Today" in the agent's prompt is the store's day (the store is set up with the same zone).
    store_timezone: str = "Asia/Kolkata"
    # A chat not used for this long is deleted, with everything said in it.
    chat_retention_days: int = 1

    @field_validator("admin_email", "admin_password", "smtp_password", "woo_mcp_url", "woo_mcp_token", "llm_api_key", mode="before")
    @classmethod
    def _blank_env_means_unset(cls, value: str | None) -> str | None:
        # docker-compose's ${VAR:-} substitutes "" (present-but-blank) when
        # the var is unset in .env, not an absent key — normalize that to
        # None so "unset" means the same thing everywhere in this module.
        return value or None

    @field_validator("gateway_encryption_key")
    @classmethod
    def _encryption_key_is_32_bytes(cls, value: str) -> str:
        # Fail at startup, not on the first save of an upstream credential.
        try:
            size = len(base64.b64decode(value, validate=True))
        except ValueError as exc:
            raise ValueError("GATEWAY_ENCRYPTION_KEY must be base64") from exc
        if size != 32:
            raise ValueError("GATEWAY_ENCRYPTION_KEY must decode to 32 bytes (AES-256)")
        return value

    @field_validator("internal_service_token")
    @classmethod
    def _service_token_is_long_enough(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("INTERNAL_SERVICE_TOKEN must be at least 32 characters")
        return value

    @field_validator("admin_email")
    @classmethod
    def _lowercase_like_every_other_email(cls, value: str | None) -> str | None:
        # Requests lowercase emails (app/schemas/auth.py: Email); an admin
        # stored as "Admin@x.com" could then never log in.
        return value.lower() if value else value

    @field_validator("admin_password")
    @classmethod
    def _admin_password_must_be_loggable(cls, value: str | None) -> str | None:
        # Must match the Password rule in app/schemas/auth.py — otherwise the
        # bootstrap admin gets created with a password nobody can ever submit.
        if value is not None and not (8 <= len(value) <= 20 and value.isascii() and value.isprintable()):
            raise ValueError("ADMIN_PASSWORD must be 8-20 printable ASCII characters (same rule as login)")
        return value

    @property
    def allowed_origins(self) -> list[str]:
        return self.cors_origins or [self.app_base_url.rstrip("/")]

    @field_validator("cors_origins")
    @classmethod
    def _no_wildcard_origin(cls, value: list[str] | None) -> list[str] | None:
        if value and "*" in value:
            raise ValueError('CORS_ORIGINS cannot contain "*": name the frontend\'s address instead')
        return value


settings = Settings()
