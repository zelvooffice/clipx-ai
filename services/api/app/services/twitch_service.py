"""Twitch account/token management and the capability matrix the rest of the app relies on."""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import utcnow
from app.integrations.twitch import helix
from app.models import OAuthState, OAuthToken, Provider, TwitchAccount, TwitchRole
from app.security import crypto
from app.services.audit import syslog
from app.services.redis_client import get_redis


class TwitchNotConfigured(Exception):
    pass


class NoTwitchAccount(Exception):
    pass


# ------------------------------------------------------------------------------------ tokens
def store_token(db: Session, provider: Provider, subject: str, ts: helix.TokenSet | Any,
                refresh_expires_in: int | None = None) -> OAuthToken:
    aad = f"{provider.value}:{subject}"
    row = db.scalar(select(OAuthToken).where(OAuthToken.provider == provider, OAuthToken.subject == subject))
    if row is None:
        row = OAuthToken(provider=provider, subject=subject, access_token_enc="")
        db.add(row)
    row.access_token_enc = crypto.encrypt(ts.access_token, associated_data=aad)
    if ts.refresh_token:
        row.refresh_token_enc = crypto.encrypt(ts.refresh_token, associated_data=aad)
    row.scopes = list(ts.scopes)
    row.expires_at = utcnow() + timedelta(seconds=ts.expires_in) if ts.expires_in else None
    if refresh_expires_in:
        row.refresh_expires_at = utcnow() + timedelta(seconds=refresh_expires_in)
    row.revoked = False
    row.last_validated_at = utcnow()
    db.flush()
    return row


def _decrypt(row: OAuthToken, field: str) -> str:
    value = getattr(row, field)
    return crypto.decrypt(value, associated_data=f"{row.provider.value}:{row.subject}")


async def get_user_access_token(db: Session, account: TwitchAccount) -> str:
    """A valid user access token: refreshed when close to expiry, validated at least hourly."""
    tok = account.token
    if tok is None or tok.revoked:
        raise NoTwitchAccount(f"Twitch account {account.login} needs to be reconnected")
    now = utcnow()
    if tok.expires_at and tok.expires_at - now < timedelta(minutes=5):
        await _refresh(db, account)
    elif not tok.last_validated_at or now - tok.last_validated_at > timedelta(hours=1):
        info = await helix.validate(_decrypt(tok, "access_token_enc"))
        if info is None:
            await _refresh(db, account)
        else:
            tok.last_validated_at = now
            tok.scopes = info.get("scopes", tok.scopes)
    return _decrypt(tok, "access_token_enc")


async def _refresh(db: Session, account: TwitchAccount) -> None:
    tok = account.token
    assert tok is not None
    if not tok.refresh_token_enc:
        tok.revoked = True
        raise NoTwitchAccount("token expired and no refresh token stored")
    try:
        ts = await helix.refresh(_decrypt(tok, "refresh_token_enc"))
    except helix.TwitchError as exc:
        if exc.status in (400, 401):
            tok.revoked = True
            syslog(db, "ERROR", "twitch", "token_revoked", f"Refresh failed for {account.login}: {exc.message}")
            db.commit()
            raise NoTwitchAccount(f"Twitch authorization for {account.login} was revoked; reconnect") from exc
        raise
    store_token(db, Provider.twitch, tok.subject, ts)


async def get_app_access_token() -> str:
    s = get_settings()
    if not s.twitch_configured:
        raise TwitchNotConfigured("TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET are not set")
    r = get_redis()
    cached = r.get("twitch:app_token")
    if cached:
        return crypto.decrypt(str(cached), associated_data="twitch:app")
    ts = await helix.app_token()
    ttl = max(60, int(ts.expires_in or 3600) - 300)
    r.set("twitch:app_token", crypto.encrypt(ts.access_token, associated_data="twitch:app"), ex=ttl)
    return ts.access_token


async def app_helix() -> helix.Helix:
    return helix.Helix(await get_app_access_token())


async def target_broadcaster() -> dict[str, Any]:
    """The configured target channel (Lacy). Cached for 6 h."""
    s = get_settings()
    r = get_redis()
    key = f"twitch:user:{s.twitch_target_login.lower()}"
    cached = r.get(key)
    if cached:
        return dict(json.loads(str(cached)))
    users = await (await app_helix()).get_users(logins=[s.twitch_target_login])
    if not users:
        raise TwitchNotConfigured(f"Twitch user '{s.twitch_target_login}' not found")
    r.set(key, json.dumps(users[0]), ex=6 * 3600)
    return dict(users[0])


# ------------------------------------------------------------------------------------ accounts
def accounts(db: Session) -> list[TwitchAccount]:
    return list(db.scalars(select(TwitchAccount).order_by(TwitchAccount.created_at)).all())


def best_account(db: Session, *, needs: str) -> TwitchAccount | None:
    """needs: 'clips' (clips:edit), 'chat' (user:read:chat), 'download' / 'vod_clip' (editor/broadcaster)."""
    usable = [a for a in accounts(db) if a.token and not a.token.revoked]
    if needs in ("download", "vod_clip"):
        usable = [a for a in usable if a.role in (TwitchRole.broadcaster, TwitchRole.editor)
                  and a.is_editor_verified is not False
                  and ({"editor:manage:clips", "channel:manage:clips"} & set(a.token.scopes if a.token else []))]
        usable.sort(key=lambda a: a.role != TwitchRole.broadcaster)
    else:
        scope = {"clips": "clips:edit", "chat": "user:read:chat"}[needs]
        usable = [a for a in usable if a.token and scope in a.token.scopes]
    return usable[0] if usable else None


@dataclass
class TwitchCapabilities:
    app_configured: bool
    accounts_connected: int
    can_detect_live: bool
    can_create_clips: bool
    can_read_chat: bool
    can_download_clips: bool
    can_vod_clip: bool
    live_ingest_configured: bool
    notes: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def capabilities(db: Session) -> TwitchCapabilities:
    s = get_settings()
    notes: list[str] = []
    accts = [a for a in accounts(db) if a.token and not a.token.revoked]
    can_dl = best_account(db, needs="download") is not None
    if not s.twitch_configured:
        notes.append("Twitch app credentials are not configured (TWITCH_CLIENT_ID/SECRET).")
    if not accts:
        notes.append("No Twitch account connected: native clipping and chat signals are unavailable.")
    if not can_dl:
        notes.append(
            "No broadcaster/editor authorization for the target channel: Twitch clip media cannot be "
            "downloaded through an authorized method, and precise VOD clipping is unavailable."
        )
    if not s.live_ingest_url:
        notes.append("No authorized live video feed configured: real-time audio/video analysis is unavailable.")
    return TwitchCapabilities(
        app_configured=s.twitch_configured,
        accounts_connected=len(accts),
        can_detect_live=s.twitch_configured,
        can_create_clips=best_account(db, needs="clips") is not None,
        can_read_chat=best_account(db, needs="chat") is not None,
        can_download_clips=can_dl,
        can_vod_clip=can_dl,
        live_ingest_configured=bool(s.live_ingest_url),
        notes=notes,
    )


# ------------------------------------------------------------------------------------ OAuth connect
def start_connect(db: Session, role: str, email: str) -> str:
    if role not in helix.SCOPES_BY_ROLE:
        raise ValueError("role must be operator, editor or broadcaster")
    if not get_settings().twitch_configured:
        raise TwitchNotConfigured("Twitch app credentials are not configured")
    state = secrets.token_urlsafe(32)
    db.add(OAuthState(state=state, provider=Provider.twitch, requested_role=role, created_by=email,
                      expires_at=utcnow() + timedelta(minutes=10)))
    return helix.authorize_url(state, role)


def consume_state(db: Session, provider: Provider, state: str, email: str) -> OAuthState:
    row = db.get(OAuthState, state)
    if row is None or row.provider != provider:
        raise PermissionError("unknown OAuth state")
    db.delete(row)  # single use
    if row.expires_at < utcnow():
        raise PermissionError("OAuth state expired, start again")
    if row.created_by.lower() != email.lower():
        raise PermissionError("OAuth state belongs to a different user")
    return row


async def complete_connect(db: Session, code: str, state: str, email: str) -> TwitchAccount:
    st = consume_state(db, Provider.twitch, state, email)
    ts = await helix.exchange_code(code)
    info = await helix.validate(ts.access_token)
    if not info:
        raise PermissionError("Twitch returned an invalid token")
    user_id, login = str(info["user_id"]), str(info["login"])
    target = await target_broadcaster()
    requested = st.requested_role or "operator"
    if user_id == target["id"]:
        role = TwitchRole.broadcaster
    elif requested == "editor":
        role = TwitchRole.editor  # editor status is verified on first clip-download call
    else:
        role = TwitchRole.operator
    tok = store_token(db, Provider.twitch, user_id, ts)
    acct = db.scalar(select(TwitchAccount).where(TwitchAccount.twitch_user_id == user_id))
    if acct is None:
        acct = TwitchAccount(twitch_user_id=user_id, login=login, role=role)
        db.add(acct)
    acct.login, acct.role, acct.token_id, acct.connected_by = login, role, tok.id, email
    acct.display_name = login
    acct.is_editor_verified = True if role == TwitchRole.broadcaster else None
    db.flush()
    return acct


async def disconnect(db: Session, account: TwitchAccount) -> None:
    if account.token and not account.token.revoked:
        try:
            await helix.revoke(_decrypt(account.token, "access_token_enc"))
        except Exception as exc:  # best effort; local deletion still happens
            syslog(db, "WARNING", "twitch", "revoke_failed", f"Token revoke failed for {account.login}: {exc}")
        db.delete(account.token)
    db.delete(account)
