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
    # (see app/services/bootstrap.py). When unset, use POST /auth/admin/signup instead
    # — both paths enforce the same "exactly one admin, ever" rule.
    admin_email: str | None = None
    admin_password: str | None = None

    chat_token_ttl_minutes: int = 30

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

    # Base of the links in invite/reset emails. Points at this API, which
    # serves the set-password page itself until the dashboard exists.
    app_base_url: str = "http://localhost:8000"
    invite_token_ttl_hours: int = 72
    reset_token_ttl_minutes: int = 30

    cors_origins: list[str] = ["*"]  # dev-only default; restrict before any real deployment

    @field_validator("admin_email", "admin_password", "smtp_password", mode="before")
    @classmethod
    def _blank_env_means_unset(cls, value: str | None) -> str | None:
        # docker-compose's ${VAR:-} substitutes "" (present-but-blank) when
        # the var is unset in .env, not an absent key — normalize that to
        # None so "unset" means the same thing everywhere in this module.
        return value or None

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


settings = Settings()
