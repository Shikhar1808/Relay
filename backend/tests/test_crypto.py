"""AES-256-GCM for upstream credentials (app/core/crypto.py)."""
import base64

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings
from app.core.crypto import decrypt, encrypt

SECRET = "Bearer ghp_example-token ünïcode"


def test_ciphertext_hides_the_plaintext_and_changes_every_time():
    first, second = encrypt(SECRET), encrypt(SECRET)
    assert SECRET.encode() not in first
    assert first != second, "every encryption must use a fresh nonce"


@pytest.mark.parametrize("position", [0, 5, 12, -1])  # in the nonce, at the start of the ciphertext, in the tag
def test_any_changed_byte_is_detected(position):
    blob = bytearray(encrypt(SECRET))
    blob[position] ^= 1
    with pytest.raises(InvalidTag):
        decrypt(bytes(blob))


def test_a_different_key_cannot_decrypt():
    other = AESGCM(bytes(32) if base64.b64decode(settings.gateway_encryption_key) != bytes(32) else bytes([1] * 32))
    blob = encrypt(SECRET)
    with pytest.raises(InvalidTag):
        other.decrypt(blob[:12], blob[12:], None)


