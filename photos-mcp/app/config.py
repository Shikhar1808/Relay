"""Environment-driven settings, same convention as woo-mcp: field names match env vars case-insensitively."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # From https://www.pexels.com/api/ (free). Sent to Pexels only: the gateway and the model never see it.
    # Blank: the tool answers that it is not set up and the server still starts, like the chat without GROQ_API_KEY.
    pexels_api_key: str = ""
    # What the gateway sends as "Authorization: Bearer ...". Nothing else may call this server.
    mcp_auth_token: str = Field(min_length=32)

    # Host headers this server answers to: the name the gateway reaches it by. Anything else gets 421.
    allowed_hosts: list[str] = ["localhost:*", "127.0.0.1:*", "photos-mcp:*"]
    # Under the gateway's 30 s call timeout, so a slow Pexels comes back as our error and not as its timeout.
    timeout_seconds: float = 20


settings = Settings()
