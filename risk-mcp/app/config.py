"""Environment-driven settings, same convention as woo-mcp: field names match env vars case-insensitively."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # What the gateway sends as "Authorization: Bearer ...". Nothing else may call this server.
    mcp_auth_token: str = Field(min_length=32)
    # Host headers this server answers to: the name the gateway reaches it by. Anything else gets 421.
    allowed_hosts: list[str] = ["localhost:*", "127.0.0.1:*", "risk-mcp:*"]


settings = Settings()
