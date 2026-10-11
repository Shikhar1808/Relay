"""AES-256-GCM for upstream MCP server credentials.

The key lives in GATEWAY_ENCRYPTION_KEY, never in the database, so a stolen
database dump does not reveal the credentials. Stored form: a random 12-byte
nonce followed by the ciphertext and its 16-byte tag. The gateway keeps its
own copy of decrypt() — change the format in both places or not at all.
"""
import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings

_NONCE_BYTES = 12
_aead = AESGCM(base64.b64decode(settings.gateway_encryption_key))


def encrypt(plaintext: str) -> bytes:
    nonce = os.urandom(_NONCE_BYTES)
    return nonce + _aead.encrypt(nonce, plaintext.encode(), None)


def decrypt(blob: bytes) -> str:
    """Raises cryptography.exceptions.InvalidTag if the blob was altered or the key is wrong."""
    return _aead.decrypt(blob[:_NONCE_BYTES], blob[_NONCE_BYTES:], None).decode()
