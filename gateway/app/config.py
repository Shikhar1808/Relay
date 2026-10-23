"""Environment-driven settings, same convention as the backend: field names
match env vars case-insensitively. This service imports nothing from the
backend; what the two share is listed here as plain configuration.
"""
import base64
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Read-only connection to the Platform DB, for the upstream_servers table only.
    platform_database_url: str
    redis_url: str = "redis://localhost:6379/0"

    # The audit log's own database (deploy/db-init.sh creates it): rows can be added and read, nothing else.
    logs_database_url: str
    # Links the audit rows into a chain that shows tampering. Only the gateway holds it.
    audit_hmac_key: str = Field(min_length=32)
    # How much of a tool call is kept: see app/audit.py.
    audit_detail: Literal["minimal", "standard", "full"] = "standard"
    # Refused logins one address may make in a minute before it gets 429.
    auth_failures_per_minute: int = 20
    # Tool calls one user may make in a minute, as a token bucket: also the size of a burst (app/limits.py).
    calls_per_minute: int = 120

    # Where a bearer token is turned into a user (the backend's /internal/auth/resolve),
    # and the shared secret that route demands.
    resolve_url: str
    internal_service_token: str
    # What Prometheus presents on /metrics. Read-only, unlike the service token, which can also write
    # the audit log. Empty turns /metrics off.
    metrics_token: str = ""
    # Same key the backend encrypts upstream credentials with (32 bytes, base64).
    gateway_encryption_key: str

    # This gateway's address as clients reach it, without a path. It is the
    # OAuth "resource" identity once members can log in with OAuth.
    public_base_url: str = "http://localhost:8080"
    # Who would issue OAuth tokens for this gateway (the backend). Only
    # advertised in the 401 challenge today; nothing issues tokens there yet.
    auth_issuer_url: str = "http://localhost:8000"
    # Host headers this gateway answers to (add the Ingress hostname). Anything
    # else gets 421. "name:*" allows any port.
    allowed_hosts: list[str] = ["localhost:*", "127.0.0.1:*", "gateway:*"]
    allowed_origins: list[str] = []

    port: int = 8080  # public: /mcp only
    private_port: int = 9090  # cluster-only: health, and the audit log for the backend (never put this behind the Ingress)

    refresh_seconds: float = 30  # how often the registry and every upstream's tools are re-read
    list_timeout_seconds: float = 5
    call_timeout_seconds: float = 30

    @field_validator("gateway_encryption_key")
    @classmethod
    def _encryption_key_is_32_bytes(cls, value: str) -> str:
        try:
            size = len(base64.b64decode(value, validate=True))
        except ValueError as exc:
            raise ValueError("GATEWAY_ENCRYPTION_KEY must be base64") from exc
        if size != 32:
            raise ValueError("GATEWAY_ENCRYPTION_KEY must decode to 32 bytes (AES-256)")
        return value

    @field_validator("metrics_token")
    @classmethod
    def _metrics_token_is_long_enough_when_set(cls, value: str) -> str:
        if value and len(value) < 32:
            raise ValueError("METRICS_TOKEN must be at least 32 characters, or empty")
        return value


settings = Settings()
