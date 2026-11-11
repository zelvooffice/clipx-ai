"""Per-visit password gate ("unlock") on top of Cloudflare Access.

Every browser tab must unlock with the dashboard password. The API issues a short-lived token bound to the
user's email, signed with a key derived from INTERNAL_API_SECRET and registered server-side in Redis, so locking
revokes it immediately. Failed attempts are counted per email; after MAX attempts the account is locked out.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from app.config import get_settings
from app.services.redis_client import get_redis

LOCKOUT_WINDOW_S = 15 * 60


def password_required() -> bool:
    return bool(get_settings().dashboard_password_hash)


def _key() -> bytes:
    return hmac.new(get_settings().internal_api_secret.encode(), b"clipx-unlock-v1", hashlib.sha256).digest()


def _sig(payload: str) -> str:
    return base64.urlsafe_b64encode(hmac.new(_key(), payload.encode(), hashlib.sha256).digest()).decode().rstrip("=")


def issue(email: str) -> tuple[str, int]:
    ttl = get_settings().unlock_ttl_minutes * 60
    exp = int(time.time()) + ttl
    sid = secrets.token_urlsafe(18)
    payload = f"v1.{sid}.{exp}.{base64.urlsafe_b64encode(email.lower().encode()).decode().rstrip('=')}"
    get_redis().set(f"unlock:{sid}", email.lower(), ex=ttl)
    return f"{payload}.{_sig(payload)}", exp


def verify(token: str | None, email: str) -> bool:
    if not token:
        return False
    try:
        version, sid, exp, email_b64, sig = token.split(".")
    except ValueError:
        return False
    payload = f"{version}.{sid}.{exp}.{email_b64}"
    if version != "v1" or not hmac.compare_digest(_sig(payload), sig):
        return False
    if not exp.isdigit() or int(exp) < time.time():
        return False
    pad = "=" * (-len(email_b64) % 4)
    if base64.urlsafe_b64decode(email_b64 + pad).decode() != email.lower():
        return False
    return get_redis().get(f"unlock:{sid}") == email.lower()  # revoked on lock / expiry


def revoke(token: str | None) -> None:
    if token and token.count(".") == 4:
        get_redis().delete(f"unlock:{token.split('.')[1]}")


def attempts_left(email: str) -> int:
    used = int(get_redis().get(f"unlockfail:{email.lower()}") or 0)
    return max(0, get_settings().unlock_max_attempts - used)


def record_failure(email: str) -> int:
    r = get_redis()
    key = f"unlockfail:{email.lower()}"
    n = int(r.incr(key))
    if n == 1:
        r.expire(key, LOCKOUT_WINDOW_S)
    return max(0, get_settings().unlock_max_attempts - n)


def clear_failures(email: str) -> None:
    get_redis().delete(f"unlockfail:{email.lower()}")
