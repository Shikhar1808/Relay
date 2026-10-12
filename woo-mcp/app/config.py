"""Environment-driven settings, same convention as the gateway: field names
match env vars case-insensitively. One instance of this service serves one store.
"""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The store as this service reaches it inside the network, without a path (http://wordpress).
    woocommerce_url: str
    # The store's REST key pair (ck_..., cs_...): deploy/wp-init.sh registers the same two in WordPress.
    woocommerce_key: str
    woocommerce_secret: str
    # What the gateway sends as "Authorization: Bearer ...". Nothing else may call this server.
    mcp_auth_token: str = Field(min_length=32)

    # Host headers this server answers to: the name the gateway reaches it by. Anything else gets 421.
    allowed_hosts: list[str] = ["localhost:*", "127.0.0.1:*", "woo-mcp:*"]
    # Under the gateway's 30 s call timeout, so a slow store comes back as our error and not as its timeout.
    timeout_seconds: float = 20


settings = Settings()
