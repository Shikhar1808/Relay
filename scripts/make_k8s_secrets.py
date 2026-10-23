"""Writes .env.k8s-platform and .env.k8s-store-demo with new random values, for the two
Kubernetes Secrets (README: "Local setup", step 3). Run it from the repo root:

    GROQ_API_KEY=... SMTP_PASSWORD=... python scripts/make_k8s_secrets.py

Both keys are optional: without GROQ_API_KEY the chat says it is not set up, and without
SMTP_PASSWORD emails are written to the backend's log instead of being sent. The two files
are git-ignored (.env.*). It refuses to overwrite files that already exist.

GATEWAY_ENCRYPTION_KEY and AUDIT_HMAC_KEY must never change after the first start: a new
encryption key makes every stored server credential unreadable, and a new HMAC key makes every
earlier audit row fail the tamper check.
"""
import base64
import os
import secrets

url_safe = secrets.token_urlsafe  # letters, digits, - and _
hex_only = secrets.token_hex  # for values placed inside a connection URL

# In both Secrets, and the same in both: woo-mcp checks the token the backend stores for the
# gateway, and fulfilment-mcp signs in with the password db-init gives its database role.
shared = {"WOO_MCP_TOKEN": url_safe(48), "FULFILMENT_DB_PASSWORD": hex_only(24)}
platform = {
    "POSTGRES_PASSWORD": hex_only(24),
    "JWT_SECRET": url_safe(48),
    "GATEWAY_ENCRYPTION_KEY": base64.b64encode(os.urandom(32)).decode(),  # exactly 32 bytes: AES-256
    "INTERNAL_SERVICE_TOKEN": url_safe(48),
    "GATEWAY_DB_PASSWORD": hex_only(24),
    "AUDIT_HMAC_KEY": url_safe(48),
    "ADMIN_PASSWORD": url_safe(12),  # 16 characters; the sign-in rule allows 8 to 20
    "RISK_MCP_TOKEN": url_safe(48),
    "METRICS_TOKEN": url_safe(48),  # read-only: what Prometheus scrapes with, unlike INTERNAL_SERVICE_TOKEN
    "GRAFANA_ADMIN_PASSWORD": url_safe(12),
    "SMTP_PASSWORD": os.environ.get("SMTP_PASSWORD", ""),
    "GROQ_API_KEY": os.environ.get("GROQ_API_KEY", ""),
    **shared,
}
store = {
    "MYSQL_PASSWORD": url_safe(24),
    "MYSQL_ROOT_PASSWORD": url_safe(24),
    "WP_ADMIN_PASSWORD": url_safe(12),
    "WOOCOMMERCE_KEY": "ck_" + hex_only(20),  # WooCommerce's own shape: a prefix and 40 hex characters
    "WOOCOMMERCE_SECRET": "cs_" + hex_only(20),
    "FULFILMENT_MCP_TOKEN": url_safe(48),
    **shared,
}
for path, values in ((".env.k8s-platform", platform), (".env.k8s-store-demo", store)):
    with open(path, "x") as file:  # "x": stop rather than overwrite existing secrets
        file.write("".join(f"{key}={value}\n" for key, value in values.items()))
    os.chmod(path, 0o600)
print("Written. The dashboard's admin password is ADMIN_PASSWORD in .env.k8s-platform.")
