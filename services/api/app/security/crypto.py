"""AES-256-GCM encryption for OAuth tokens at rest, with key rotation.

Format: ``v1.<key_index>.<b64 nonce>.<b64 ciphertext+tag>``. The first configured key encrypts; every
configured key is tried for decryption, so keys can be rotated by prepending a new key.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings


class TokenCryptoError(RuntimeError):
    pass


def _keys() -> list[bytes]:
    raw = [k.strip() for k in get_settings().token_encryption_keys.split(",") if k.strip()]
    if not raw:
        raise TokenCryptoError("TOKEN_ENCRYPTION_KEYS is not configured")
    keys = []
    for k in raw:
        decoded = base64.b64decode(k)
        if len(decoded) != 32:
            raise TokenCryptoError("each TOKEN_ENCRYPTION_KEYS entry must be base64 of 32 bytes")
        keys.append(decoded)
    return keys


def _key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:8]


def encrypt(plaintext: str, *, associated_data: str = "") -> str:
    key = _keys()[0]
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode(), associated_data.encode())
    return f"v1.{_key_id(key)}.{base64.urlsafe_b64encode(nonce).decode()}.{base64.urlsafe_b64encode(ct).decode()}"


def decrypt(token: str, *, associated_data: str = "") -> str:
    try:
        version, kid, nonce_b64, ct_b64 = token.split(".")
    except ValueError as exc:
        raise TokenCryptoError("malformed ciphertext") from exc
    if version != "v1":
        raise TokenCryptoError("unsupported ciphertext version")
    for key in _keys():
        if _key_id(key) != kid:
            continue
        nonce = base64.urlsafe_b64decode(nonce_b64)
        ct = base64.urlsafe_b64decode(ct_b64)
        return AESGCM(key).decrypt(nonce, ct, associated_data.encode()).decode()
    raise TokenCryptoError("no configured key can decrypt this value (key rotated out?)")


def generate_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()
