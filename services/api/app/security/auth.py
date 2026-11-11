"""Server-side authentication for every non-webhook API request (fail closed).

Accepted credentials, in order:
1. HMAC-signed request from the Cloudflare Worker (see app/security/signing.py). The Worker has already
   verified the Cloudflare Access JWT; the signature proves the request came from the Worker.
2. A Cloudflare Access JWT (``Cf-Access-Jwt-Assertion``) when the API itself is published behind Access
   and CF_ACCESS_TEAM_DOMAIN / CF_ACCESS_AUD are configured.

In both cases the email must be on the allowlist (env bootstrap list or active row in authorized_emails).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db, utcnow
from app.models import AuthorizedEmail, User
from app.security import signing
from app.security.ratelimit import RateLimiter
from app.services.redis_client import get_redis


@dataclass(frozen=True)
class CurrentUser:
    email: str
    via: str  # "worker-signature" | "access-jwt"
    ip: str | None


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


_JWKS_CACHE: dict[str, tuple[float, dict[str, jwt.PyJWK]]] = {}


def _signing_key(team_domain: str, kid: str) -> jwt.PyJWK:
    """Fetch Access signing keys (cached 1 h) and match the token's ``kid`` — never trust 'current' blindly."""
    import httpx

    url = f"{team_domain.rstrip('/')}/cdn-cgi/access/certs"
    cached = _JWKS_CACHE.get(url)
    if cached is None or time.time() - cached[0] > 3600 or kid not in cached[1]:
        r = httpx.get(url, timeout=10)
        r.raise_for_status()
        keys = {k["kid"]: jwt.PyJWK(k) for k in r.json().get("keys", []) if k.get("kid")}
        _JWKS_CACHE[url] = (time.time(), keys)
        cached = _JWKS_CACHE[url]
    if kid not in cached[1]:
        raise jwt.InvalidKeyError("unknown signing key id")
    return cached[1][kid]


def verify_access_jwt(token: str) -> str:
    s = get_settings()
    if not (s.cf_access_team_domain and s.cf_access_aud):
        raise _unauthorized("Access JWT verification not configured")
    try:
        kid = jwt.get_unverified_header(token).get("kid", "")
        key = _signing_key(s.cf_access_team_domain, kid)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            audience=s.cf_access_aud,
            issuer=s.cf_access_team_domain.rstrip("/"),
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
    except (jwt.PyJWTError, OSError, ValueError) as exc:
        raise _unauthorized("invalid Access token") from exc
    email = str(claims.get("email") or "").lower()
    if not email:
        raise _unauthorized("Access token has no email (service tokens are not accepted)")
    return email


async def _verify_worker_signature(request: Request) -> str | None:
    s = get_settings()
    sig = request.headers.get("x-clipx-signature")
    if not sig:
        return None
    if not s.internal_api_secret:
        raise _unauthorized("internal signing not configured")
    ts = request.headers.get("x-clipx-timestamp", "")
    nonce = request.headers.get("x-clipx-nonce", "")
    email = request.headers.get("x-clipx-email", "").lower()
    if not (ts.isdigit() and nonce and email and 16 <= len(nonce) <= 64):
        raise _unauthorized("incomplete signature headers")
    if abs(time.time() - int(ts)) > s.internal_signature_max_skew_s:
        raise _unauthorized("signature expired")
    body = await request.body()
    path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    if not signing.verify(s.internal_api_secret, sig, timestamp=ts, nonce=nonce, method=request.method,
                          path=path, email=email, body=body):
        raise _unauthorized("bad signature")
    # Replay protection: each nonce is accepted once within the skew window.
    if not get_redis().set(f"nonce:{nonce}", "1", nx=True, ex=s.internal_signature_max_skew_s * 2):
        raise _unauthorized("replayed request")
    return email


def is_email_authorized(db: Session, email: str) -> bool:
    email = email.lower()
    if email in get_settings().authorized_email_set:
        return True
    row = db.scalar(select(AuthorizedEmail).where(AuthorizedEmail.email == email))
    return bool(row and row.active)


def _touch_user(email: str) -> None:
    """Record last-seen at most every 5 minutes, in its own short transaction.

    Never inside the request's transaction: holding a row lock on `users` for a whole request serialises every
    concurrent request of the same user (and can deadlock async endpoints)."""
    from app.db import session_scope

    if not get_redis().set(f"seen:{email}", "1", nx=True, ex=300):
        return
    with session_scope() as s:
        user = s.scalar(select(User).where(User.email == email))
        if user is None:
            s.add(User(email=email, last_seen_at=utcnow()))
        else:
            user.last_seen_at = utcnow()


async def get_current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    email = await _verify_worker_signature(request)
    via = "worker-signature"
    if email is None:
        token = request.headers.get("cf-access-jwt-assertion")
        if not token:
            raise _unauthorized("authentication required")
        email = verify_access_jwt(token)
        via = "access-jwt"
    if not is_email_authorized(db, email):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="email not authorized")

    allowed, _ = RateLimiter().hit(f"user:{email}", get_settings().rate_limit_per_minute)
    if not allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="rate limit exceeded")

    _touch_user(email)
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else None)
    return CurrentUser(email=email, via=via, ip=ip)
