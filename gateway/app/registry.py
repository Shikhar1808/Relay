"""Reads the upstream registry the backend owns (table upstream_servers in the
Platform DB) through a read-only role, and decrypts each credential.

decrypt() mirrors backend/app/core/crypto.py: a 12-byte nonce, then the
AES-256-GCM ciphertext and tag. Change the format in both places or not at all.
"""
import base64
import logging
from dataclasses import dataclass

import asyncpg
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

log = logging.getLogger(__name__)

_NONCE_BYTES = 12
_aead = AESGCM(base64.b64decode(settings.gateway_encryption_key))


def decrypt(blob: bytes) -> str:
    return _aead.decrypt(blob[:_NONCE_BYTES], blob[_NONCE_BYTES:], None).decode()


@dataclass(frozen=True)
class Upstream:
    id: str
    slug: str
    url: str
    headers: dict[str, str]  # the credential header, if the server has one
    store_id: str | None  # None: shared by everyone; else only callers who have this store


async def load_upstreams(pool: asyncpg.Pool) -> dict[str, Upstream]:
    """Every enabled upstream, by slug."""
    rows = await pool.fetch(
        "SELECT id, slug, url, credential_header, credential_ciphertext, store_id"
        " FROM upstream_servers WHERE enabled ORDER BY created_at"
    )
    upstreams: dict[str, Upstream] = {}
    for row in rows:
        headers: dict[str, str] = {}
        if row["credential_ciphertext"] is not None:
            try:
                headers[row["credential_header"]] = decrypt(row["credential_ciphertext"])
            except InvalidTag:
                # Wrong key or a damaged row. Calling without the credential would
                # only produce confusing upstream errors, so leave the server out.
                log.error("cannot decrypt the credential of upstream %s; skipping it", row["slug"])
                continue
        upstreams[row["slug"]] = Upstream(
            id=str(row["id"]),
            slug=row["slug"],
            url=row["url"],
            headers=headers,
            store_id=str(row["store_id"]) if row["store_id"] else None,
        )
    return upstreams
