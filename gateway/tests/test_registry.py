"""Reading the upstream registry and decrypting credentials (app/registry.py)."""
import base64
import os
import uuid

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings
from app.registry import decrypt, load_upstreams

pytestmark = pytest.mark.anyio


def backend_encrypt(plaintext: str, key: bytes | None = None) -> bytes:
    """What backend/app/core/crypto.py writes: a 12-byte nonce, then ciphertext and tag."""
    nonce = os.urandom(12)
    aead = AESGCM(key or base64.b64decode(settings.gateway_encryption_key))
    return nonce + aead.encrypt(nonce, plaintext.encode(), None)


class FakePool:
    def __init__(self, rows):
        self.rows, self.queries = rows, []

    async def fetch(self, query):
        self.queries.append(query)
        return self.rows


def row(slug, credential=None, header="Authorization", store_id=None):
    return {
        "id": uuid.uuid4(), "slug": slug, "url": f"http://{slug}:9000/mcp",
        "credential_header": header, "credential_ciphertext": credential, "store_id": store_id,
    }


def test_decrypt_reads_what_the_backend_writes():
    assert decrypt(backend_encrypt("Bearer ghp_token ünï")) == "Bearer ghp_token ünï"


def test_decrypt_refuses_a_damaged_blob_and_a_foreign_key():
    blob = bytearray(backend_encrypt("Bearer x"))
    blob[-1] ^= 1
    with pytest.raises(InvalidTag):
        decrypt(bytes(blob))
    with pytest.raises(InvalidTag):
        decrypt(backend_encrypt("Bearer x", key=bytes([7] * 32)))


async def test_load_maps_rows_to_upstreams_by_slug():
    store = uuid.uuid4()
    rows = [
        row("woo", backend_encrypt("Bearer woo-secret"), store_id=store),
        row("github", backend_encrypt("ghp_abc"), header="X-Api-Key"),
        row("open"),
    ]
    upstreams = await load_upstreams(FakePool(rows))

    assert list(upstreams) == ["woo", "github", "open"]
    assert upstreams["woo"].headers == {"Authorization": "Bearer woo-secret"}
    assert upstreams["woo"].store_id == str(store) and upstreams["woo"].url == "http://woo:9000/mcp"
    assert upstreams["github"].headers == {"X-Api-Key": "ghp_abc"} and upstreams["github"].store_id is None
    assert upstreams["open"].headers == {}, "a server with no credential gets no credential header"
    assert upstreams["woo"].id == str(rows[0]["id"])


async def test_load_only_asks_for_enabled_upstreams():
    pool = FakePool([])
    assert await load_upstreams(pool) == {}
    assert "WHERE enabled" in pool.queries[0]


async def test_an_undecryptable_credential_drops_that_upstream_only(caplog):
    rows = [row("good", backend_encrypt("Bearer ok")), row("bad", backend_encrypt("Bearer x", key=bytes([7] * 32)))]
    upstreams = await load_upstreams(FakePool(rows))
    # Calling it without its credential would only produce confusing upstream errors.
    assert list(upstreams) == ["good"]
    assert "bad" in caplog.text and "Bearer x" not in caplog.text
