"""Twitch contract tests: requests must match the official API reference; responses are labelled fixtures."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from app.integrations.twitch import helix
from app.integrations.twitch.eventsub import EventSubWebSocket, WebhookVerificationError, verify_webhook
from tests.fixtures import twitch as fx

H = "https://api.twitch.tv/helix"


# ------------------------------------------------------------------------------ OAuth
@pytest.mark.parametrize("role,scopes", [
    ("operator", {"clips:edit", "user:read:chat"}),
    ("editor", {"clips:edit", "user:read:chat", "editor:manage:clips"}),
    ("broadcaster", {"clips:edit", "user:read:chat", "channel:manage:clips"}),
])
def test_authorize_url_scopes_per_role(role: str, scopes: set[str]) -> None:
    q = parse_qs(urlparse(helix.authorize_url("state123", role)).query)
    assert set(q["scope"][0].split(" ")) == scopes
    assert q["response_type"] == ["code"] and q["state"] == ["state123"]
    assert q["redirect_uri"] == ["https://clipx.example.com/api/oauth/twitch/callback"]


@respx.mock
async def test_exchange_and_validate() -> None:
    route = respx.post("https://id.twitch.tv/oauth2/token").mock(return_value=httpx.Response(200, json=fx.TOKEN))
    ts = await helix.exchange_code("the-code")
    sent = parse_qs(route.calls[0].request.content.decode())
    assert sent["grant_type"] == ["authorization_code"] and sent["code"] == ["the-code"]
    assert sent["client_secret"] == ["test-client-secret"]
    assert ts.access_token == "fixture-access" and "clips:edit" in ts.scopes
    v = respx.get("https://id.twitch.tv/oauth2/validate").mock(return_value=httpx.Response(200, json=fx.VALIDATE))
    info = await helix.validate("tok")
    assert v.calls[0].request.headers["authorization"] == "OAuth tok" and info["user_id"] == "999"
    respx.get("https://id.twitch.tv/oauth2/validate").mock(return_value=httpx.Response(401, json={}))
    assert await helix.validate("bad") is None


@respx.mock
async def test_refresh_failure_marks_account_revoked() -> None:
    from app.db import session_scope
    from app.models import Provider, TwitchAccount, TwitchRole
    from app.services import twitch_service

    respx.post("https://id.twitch.tv/oauth2/token").mock(return_value=httpx.Response(400, json={"message": "Invalid refresh token"}))
    with session_scope() as db:
        tok = twitch_service.store_token(db, Provider.twitch, "999", helix.TokenSet("a", "r", 1, ["clips:edit"]))
        tok.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        acct = TwitchAccount(twitch_user_id="999", login="op", role=TwitchRole.operator, token_id=tok.id)
        db.add(acct)
        db.flush()
        db.refresh(acct)
        with pytest.raises(twitch_service.NoTwitchAccount):
            await twitch_service.get_user_access_token(db, acct)
    with session_scope() as db:
        assert db.query(TwitchAccount).one().token.revoked is True


# ------------------------------------------------------------------------------ Create Clip
@respx.mock
async def test_create_clip_request_shape() -> None:
    route = respx.post(f"{H}/clips").mock(return_value=httpx.Response(202, json=fx.CREATE_CLIP))
    out = await helix.Helix("tok").create_clip("141981764", title="t" * 150, duration=90)
    req = route.calls[0].request
    q = parse_qs(req.url.query.decode())
    assert q["broadcaster_id"] == ["141981764"] and q["duration"] == ["60.0"]  # clamped to Twitch max
    assert len(q["title"][0]) == 100
    assert "has_delay" not in q  # removed from the current API
    assert req.headers["client-id"] == "test-client-id" and req.headers["authorization"] == "Bearer tok"
    assert out["id"] == "FiveWordsForClipSlug"


@respx.mock
@pytest.mark.parametrize("status,exc", [(403, helix.ClipRestricted), (404, helix.NotLive)])
async def test_create_clip_errors_classified(status: int, exc: type) -> None:
    respx.post(f"{H}/clips").mock(return_value=httpx.Response(status, json={"message": "nope"}))
    with pytest.raises(exc):
        await helix.Helix("tok").create_clip("1", duration=3)


@respx.mock
async def test_vod_clip_and_downloads_shapes() -> None:
    vod = respx.post(f"{H}/videos/clips").mock(return_value=httpx.Response(202, json=fx.CREATE_CLIP))
    await helix.Helix("tok").create_clip_from_vod(editor_id="9", broadcaster_id="1", vod_id="v1", vod_offset=120,
                                                  duration=30, title="x")
    q = parse_qs(vod.calls[0].request.url.query.decode())
    assert q["vod_offset"] == ["120"] and q["duration"] == ["30.0"] and q["editor_id"] == ["9"]
    with pytest.raises(ValueError):
        await helix.Helix("tok").create_clip_from_vod(editor_id="9", broadcaster_id="1", vod_id="v1",
                                                      vod_offset=10, duration=30, title="x")
    dl = respx.get(f"{H}/clips/downloads").mock(return_value=httpx.Response(200, json=fx.CLIP_DOWNLOAD))
    data = await helix.Helix("tok").get_clip_downloads(editor_id="9", broadcaster_id="1", clip_ids=["a", "b"])
    assert parse_qs(dl.calls[0].request.url.query.decode())["clip_id"] == ["a", "b"]
    assert data[0]["portrait_download_url"] is None
    with pytest.raises(ValueError):
        await helix.Helix("tok").get_clip_downloads(editor_id="9", broadcaster_id="1", clip_ids=[str(i) for i in range(11)])


@respx.mock
async def test_helix_retries_on_429() -> None:
    route = respx.get(f"{H}/streams").mock(side_effect=[httpx.Response(429, headers={"Ratelimit-Reset": "0"}),
                                                       httpx.Response(200, json={"data": [fx.STREAM_LIVE]})])
    assert (await helix.Helix("t").get_stream("1"))["id"] == fx.STREAM_LIVE["id"]
    assert route.call_count == 2


# ------------------------------------------------------------------------------ EventSub webhook
def _sign(secret: str, mid: str, ts: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), mid.encode() + ts.encode() + body, hashlib.sha256).hexdigest()


def test_webhook_signature_verification() -> None:
    body = b'{"x":1}'
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")
    h = {"Twitch-Eventsub-Message-Id": "m1", "Twitch-Eventsub-Message-Timestamp": ts,
         "Twitch-Eventsub-Message-Type": "notification", "Twitch-Eventsub-Message-Signature": _sign("s3cret-value", "m1", ts, body)}
    assert verify_webhook("s3cret-value", h, body) == "notification"
    with pytest.raises(WebhookVerificationError):
        verify_webhook("s3cret-value", h, b'{"x":2}')
    old = (datetime.now(UTC) - timedelta(minutes=11)).strftime("%Y-%m-%dT%H:%M:%SZ")
    h_old = {**h, "Twitch-Eventsub-Message-Timestamp": old,
             "Twitch-Eventsub-Message-Signature": _sign("s3cret-value", "m1", old, body)}
    with pytest.raises(WebhookVerificationError, match="too old"):
        verify_webhook("s3cret-value", h_old, body)


# ------------------------------------------------------------------------------ EventSub WebSocket
class FakeWS:
    def __init__(self, messages: list[dict | None]) -> None:
        self.messages = messages

    async def __aenter__(self) -> FakeWS:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    async def recv(self) -> str:
        if not self.messages:
            await asyncio.sleep(3600)
        m = self.messages.pop(0)
        if m is None:
            await asyncio.sleep(3600)  # silence -> keepalive timeout
        return json.dumps(m)


def _msg(mtype: str, payload: dict, mid: str = "x") -> dict:
    return {"metadata": {"message_id": mid, "message_type": mtype}, "payload": payload}


async def test_websocket_welcome_subscribe_notify_dedupe_reconnect() -> None:
    welcome = _msg("session_welcome", {"session": {"id": "S1", "keepalive_timeout_seconds": 10}})
    online = _msg("notification", {"subscription": {"type": "stream.online"},
                                   "event": {"id": "st1", "broadcaster_user_id": "1"}}, mid="n1")
    reconnect = _msg("session_reconnect", {"session": {"id": "S1", "reconnect_url": "wss://reconnect"}})
    welcome2 = _msg("session_welcome", {"session": {"id": "S2", "keepalive_timeout_seconds": 10}})
    offline = _msg("notification", {"subscription": {"type": "stream.offline"}, "event": {}}, mid="n2")
    conns = {"wss://eventsub.wss.twitch.tv/ws?keepalive_timeout_seconds=10": FakeWS([welcome, online, online, reconnect]),
             "wss://reconnect": FakeWS([welcome2, offline])}
    subscribed, events = [], []

    async def subscribe(sid: str) -> None:
        subscribed.append(sid)

    async def on_event(t: str, e: dict, s: dict) -> None:
        events.append(t)
        if t == "stream.offline":
            ws.stop()

    ws = EventSubWebSocket(subscribe, on_event, keepalive_s=10, connect=lambda url, **k: conns[url])
    await asyncio.wait_for(ws._run_once(f"{ws.url}?keepalive_timeout_seconds=10", resubscribe=True), timeout=5)
    assert subscribed == ["S1"]  # not re-subscribed after reconnect: subscriptions carry over
    assert events == ["stream.online", "stream.offline"]  # duplicate n1 delivered once


async def test_websocket_keepalive_timeout_raises() -> None:
    ws = EventSubWebSocket(lambda s: asyncio.sleep(0), lambda *a: asyncio.sleep(0), keepalive_s=1,
                           connect=lambda url, **k: FakeWS([_msg("session_welcome", {"session": {"id": "S",
                                                            "keepalive_timeout_seconds": 1}}), None]))
    ws.keepalive_s = 1
    with pytest.raises(ConnectionError, match="keepalive"):
        await asyncio.wait_for(ws._run_once("u", resubscribe=True), timeout=15)
