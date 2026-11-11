"""HMAC request signing between the Cloudflare Worker (web) and the API.

The Worker verifies the Cloudflare Access JWT, then signs every proxied request:

    X-ClipX-Email, X-ClipX-Timestamp (unix seconds), X-ClipX-Nonce,
    X-ClipX-Signature = hex(HMAC-SHA256(secret, canonical))

    canonical = "v1\n{timestamp}\n{nonce}\n{METHOD}\n{path?query}\n{email}\n{sha256_hex(body)}"

The same algorithm is implemented in apps/web/lib/signing.ts.
"""

from __future__ import annotations

import hashlib
import hmac


def canonical_string(*, timestamp: str, nonce: str, method: str, path: str, email: str, body: bytes) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    return "\n".join(["v1", timestamp, nonce, method.upper(), path, email.lower(), body_hash])


def sign(secret: str, **kwargs: object) -> str:
    msg = canonical_string(**kwargs)  # type: ignore[arg-type]
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def verify(secret: str, signature: str, **kwargs: object) -> bool:
    expected = sign(secret, **kwargs)
    return hmac.compare_digest(expected, signature)
