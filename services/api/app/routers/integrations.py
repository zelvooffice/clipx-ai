from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.integrations.twitch.eventsub import WebhookVerificationError, verify_webhook
from app.models import TikTokAccount, TwitchAccount
from app.routers import serialize as ser
from app.security.auth import CurrentUser, get_current_user
from app.services import tiktok_service, twitch_service
from app.services.audit import audit, syslog
from app.services.redis_client import get_redis

router = APIRouter(prefix="/v1/integrations")
webhooks = APIRouter(prefix="/webhooks")


class ConnectIn(BaseModel):
    role: Literal["operator", "editor", "broadcaster"] = "operator"


class CallbackIn(BaseModel):
    code: str = Field(min_length=4, max_length=512)
    state: str = Field(min_length=16, max_length=128)


# ------------------------------------------------------------------------------------- Twitch
@router.get("/twitch")
async def twitch_status(db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    target = None
    if get_settings().twitch_configured:
        try:
            t = await twitch_service.target_broadcaster()
            target = {"id": t["id"], "login": t["login"], "display_name": t.get("display_name"),
                      "profile_image_url": t.get("profile_image_url")}
        except Exception as exc:
            target = {"error": str(exc)}
    return {"configured": get_settings().twitch_configured, "target": target,
            "accounts": [ser.twitch_account(a) for a in twitch_service.accounts(db)],
            "capabilities": twitch_service.capabilities(db).as_dict(),
            "redirect_uri": get_settings().twitch_redirect_uri}


@router.post("/twitch/connect")
def twitch_connect(body: ConnectIn, db: Session = Depends(get_db),
                   user: CurrentUser = Depends(get_current_user)) -> dict[str, str]:
    try:
        url = twitch_service.start_connect(db, body.role, user.email)
    except twitch_service.TwitchNotConfigured as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(db, user.email, "twitch.connect.start", body.role, user.ip)
    return {"authorize_url": url}


@router.post("/twitch/callback")
async def twitch_callback(body: CallbackIn, db: Session = Depends(get_db),
                          user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    try:
        acct = await twitch_service.complete_connect(db, body.code, body.state, user.email)
    except PermissionError as exc:
        db.commit()  # persist consumption of the state
        raise HTTPException(403, str(exc)) from exc
    audit(db, user.email, "twitch.connect", acct.login, user.ip, role=acct.role.value)
    syslog(db, "INFO", "twitch", "connected", f"Twitch account {acct.login} connected as {acct.role.value}")
    return ser.twitch_account(acct)


@router.delete("/twitch/accounts/{account_id}")
async def twitch_disconnect(account_id: uuid.UUID, db: Session = Depends(get_db),
                            user: CurrentUser = Depends(get_current_user)) -> dict[str, str]:
    acct = db.get(TwitchAccount, account_id)
    if acct is None:
        raise HTTPException(404)
    await twitch_service.disconnect(db, acct)
    audit(db, user.email, "twitch.disconnect", acct.login, user.ip)
    return {"status": "disconnected"}


# ------------------------------------------------------------------------------------- TikTok
@router.get("/tiktok")
def tiktok_status(db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    s = get_settings()
    return {"configured": s.tiktok_configured, "account": ser.tiktok_account(tiktok_service.account(db)),
            "scopes_requested": s.tiktok_scopes.split(","), "redirect_uri": s.tiktok_redirect_uri,
            "pull_from_url_prefix": s.tiktok_pull_url_prefix or None}


@router.post("/tiktok/connect")
def tiktok_connect(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)) -> dict[str, str]:
    try:
        url = tiktok_service.start_connect(db, user.email)
    except tiktok_service.TikTokNotConfigured as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(db, user.email, "tiktok.connect.start", None, user.ip)
    return {"authorize_url": url}


@router.post("/tiktok/callback")
async def tiktok_callback(body: CallbackIn, db: Session = Depends(get_db),
                          user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    try:
        acct = await tiktok_service.complete_connect(db, body.code, body.state, user.email)
    except PermissionError as exc:
        db.commit()
        raise HTTPException(403, str(exc)) from exc
    audit(db, user.email, "tiktok.connect", acct.display_name, user.ip)
    syslog(db, "INFO", "tiktok", "connected", f"TikTok account {acct.display_name} connected")
    return ser.tiktok_account(acct) or {}


@router.delete("/tiktok/accounts/{account_id}")
async def tiktok_disconnect(account_id: uuid.UUID, db: Session = Depends(get_db),
                            user: CurrentUser = Depends(get_current_user)) -> dict[str, str]:
    acct = db.get(TikTokAccount, account_id)
    if acct is None:
        raise HTTPException(404)
    await tiktok_service.disconnect(db, acct)
    audit(db, user.email, "tiktok.disconnect", acct.display_name, user.ip)
    return {"status": "disconnected"}


# ------------------------------------------------------------------------------------- EventSub webhook
@webhooks.post("/twitch/eventsub")
async def eventsub_webhook(request: Request) -> Response:
    """Public endpoint (expose only this path through Cloudflare); every message is HMAC-verified."""
    secret = get_settings().twitch_eventsub_secret
    if not secret:
        raise HTTPException(404)
    raw = await request.body()
    try:
        mtype = verify_webhook(secret, dict(request.headers), raw)
    except WebhookVerificationError as exc:
        raise HTTPException(403, str(exc)) from exc
    msg_id = request.headers.get("twitch-eventsub-message-id", "")
    if not get_redis().set(f"eventsub:{msg_id}", "1", nx=True, ex=900):
        return Response(status_code=204)  # duplicate delivery
    body = json.loads(raw)
    if mtype == "webhook_callback_verification":
        return Response(content=body["challenge"], media_type="text/plain", status_code=200)
    if mtype == "revocation":
        from app.db import session_scope

        with session_scope() as db:
            syslog(db, "ERROR", "eventsub", "revoked", f"Subscription revoked: {body.get('subscription')}")
        return Response(status_code=204)
    from app.workers.live_monitor import handle_event

    sub = body.get("subscription", {})
    await handle_event(None, sub.get("type", ""), body.get("event", {}))
    return Response(status_code=204)
