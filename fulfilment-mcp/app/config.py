"""Environment-driven settings, same convention as woo-mcp: field names match env vars case-insensitively.
One instance of this service serves one store."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The role and database gateway/deploy/db-init.sh makes: postgresql://fulfilment:...@postgres:5432/fulfilment
    database_url: str
    # What the gateway sends as "Authorization: Bearer ...". Nothing else may call this server.
    mcp_auth_token: str = Field(min_length=32)
    # The store's time zone: dates go out in it, like the store's own (compose passes WP_TIMEZONE).
    timezone: str = "UTC"

    # Host headers this server answers to: the name the gateway reaches it by. Anything else gets 421.
    allowed_hosts: list[str] = ["localhost:*", "127.0.0.1:*", "fulfilment-mcp:*"]
    # Under the gateway's 30 s call timeout, so a slow database comes back as our error and not as its timeout.
    timeout_seconds: float = 20


settings = Settings()
