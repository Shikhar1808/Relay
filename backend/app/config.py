"""Environment-driven settings. Values come from process env vars (docker-compose)
or a local .env file (bare `uvicorn` runs). Field names match env vars case-insensitively.
"""
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str
    jwt_expire_minutes: int = 720  # 12h dashboard session. No refresh-token flow in this phase.

    # Optional: when set, the first admin is created automatically on startup
    # (see app/bootstrap.py). When unset, use POST /auth/admin/signup instead
    # — both paths enforce the same "exactly one admin, ever" rule.
    admin_email: str | None = None
    admin_password: str | None = None

    chat_token_ttl_minutes: int = 30

    cors_origins: list[str] = ["*"]  # dev-only default; restrict before any real deployment

    @field_validator("admin_email", "admin_password", mode="before")
    @classmethod
    def _blank_env_means_unset(cls, value: str | None) -> str | None:
        # docker-compose's ${VAR:-} substitutes "" (present-but-blank) when
        # the var is unset in .env, not an absent key — normalize that to
        # None so "unset" means the same thing everywhere in this module.
        return value or None

    @field_validator("admin_password")
    @classmethod
    def _admin_password_must_be_loggable(cls, value: str | None) -> str | None:
        # Must match LoginRequest's bounds in app/schemas.py — otherwise the
        # bootstrap admin gets created with a password nobody can ever submit.
        if value is not None and not (8 <= len(value) <= 20):
            raise ValueError("ADMIN_PASSWORD must be 8-20 characters (same bound as login)")
        return value


settings = Settings()
