"""Twitch EventSub: webhook signature verification and the WebSocket transport client.

Verified against https://dev.twitch.tv/docs/eventsub/ (2026-11-11):
* Webhook: HMAC-SHA256(secret, message_id + timestamp + raw_body), header value prefixed ``sha256=``;
  message types ``notification``, ``webhook_callback_verification`` (reply with the raw challenge,
  text/plain), ``revocation``. Reject messages older than 10 minutes and duplicate message ids.
* WebSocket: ``wss://eventsub.wss.twitch.tv/ws``; ``session_welcome`` carries the session id and we must
  subscribe within 10 s; ``session_keepalive`` resets the keepalive timer; ``session_reconnect`` gives a
  ``reconnect_url`` (30 s grace, subscriptions carry over); clients must not send messages (4001).
  WebSocket subscriptions require *user* access tokens.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger("clipx.eventsub")

WS_URL = "wss://eventsub.wss.twitch.tv/ws"
MAX_MESSAGE_AGE = timedelta(minutes=10)


# ------------------------------------------------------------------------------ webhook transport
class WebhookVerificationError(Exception):
    pass


def verify_webhook(secret: str, headers: dict[str, str], raw_body: bytes, *,
                   now: datetime | None = None) -> str:
    """Validate a webhook delivery. Returns the message type. Raises WebhookVerificationError."""
    h = {k.lower(): v for k, v in headers.items()}
    msg_id = h.get("twitch-eventsub-message-id")
    ts = h.get("twitch-eventsub-message-timestamp")
    sig = h.get("twitch-eventsub-message-signature")
    mtype = h.get("twitch-eventsub-message-type")
    if not (msg_id and ts and sig and mtype):
        raise WebhookVerificationError("missing EventSub headers")
    expected = "sha256=" + hmac.new(secret.encode(), msg_id.encode() + ts.encode() + raw_body,
                                    hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise WebhookVerificationError("signature mismatch")
    sent = _parse_ts(ts)
    if (now or datetime.now(UTC)) - sent > MAX_MESSAGE_AGE:
        raise WebhookVerificationError("message too old (possible replay)")
    return mtype


def _parse_ts(ts: str) -> datetime:
    # Twitch sends RFC3339 with nanoseconds, e.g. 2026-11-11T12:00:00.123456789Z
    base, _, frac = ts.rstrip("Z").partition(".")
    dt = datetime.fromisoformat(base).replace(tzinfo=UTC)
    if frac:
        dt += timedelta(microseconds=int(frac[:6].ljust(6, "0")))
    return dt


# ------------------------------------------------------------------------------ websocket transport
EventHandler = Callable[[str, dict[str, Any], dict[str, Any]], Awaitable[None]]
Subscriber = Callable[[str], Awaitable[None]]  # receives the session id, creates subscriptions


class _SeenIds:
    def __init__(self, size: int = 2000) -> None:
        self._d: OrderedDict[str, None] = OrderedDict()
        self._size = size

    def add(self, key: str) -> bool:
        """Returns False if already seen."""
        if key in self._d:
            return False
        self._d[key] = None
        if len(self._d) > self._size:
            self._d.popitem(last=False)
        return True


class EventSubWebSocket:
    """Resilient EventSub WebSocket consumer."""

    def __init__(self, subscribe: Subscriber, on_event: EventHandler, *, url: str = WS_URL,
                 keepalive_s: int = 30, connect: Callable[..., Any] | None = None,
                 on_status: Callable[[str, str], None] | None = None) -> None:
        self.subscribe = subscribe
        self.on_event = on_event
        self.url = url
        self.keepalive_s = keepalive_s
        self._connect = connect
        self.on_status = on_status or (lambda status, detail: None)
        self._seen = _SeenIds()
        self._stop = asyncio.Event()
        self.session_id: str | None = None

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        backoff = 1.0
        url = f"{self.url}?keepalive_timeout_seconds={self.keepalive_s}"
        while not self._stop.is_set():
            try:
                next_url = await self._run_once(url, resubscribe=True)
                backoff = 1.0
                url = next_url or f"{self.url}?keepalive_timeout_seconds={self.keepalive_s}"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.on_status("disconnected", f"{type(exc).__name__}: {exc}")
                log.warning("EventSub websocket error: %s; reconnecting in %.0fs", exc, backoff)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                except TimeoutError:
                    pass
                backoff = min(backoff * 2, 60)
                url = f"{self.url}?keepalive_timeout_seconds={self.keepalive_s}"

    async def _run_once(self, url: str, *, resubscribe: bool) -> str | None:
        connect = self._connect
        if connect is None:
            import websockets

            connect = websockets.connect
        async with connect(url, max_size=2**20, open_timeout=15) as ws:
            keepalive = self.keepalive_s
            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=keepalive + 5)
                except TimeoutError as exc:
                    raise ConnectionError("keepalive timeout") from exc
                msg = json.loads(raw)
                meta = msg.get("metadata", {})
                payload = msg.get("payload", {})
                mtype = meta.get("message_type")
                if mtype == "session_welcome":
                    session = payload["session"]
                    self.session_id = session["id"]
                    keepalive = int(session.get("keepalive_timeout_seconds") or keepalive)
                    self.on_status("connected", self.session_id or "")
                    if resubscribe:
                        await self.subscribe(self.session_id)  # must happen within 10 s
                elif mtype == "session_keepalive":
                    continue
                elif mtype == "notification":
                    if not self._seen.add(meta.get("message_id", "")):
                        continue
                    sub = payload.get("subscription", {})
                    try:
                        await self.on_event(sub.get("type", ""), payload.get("event", {}), sub)
                    except Exception:
                        log.exception("EventSub handler failed for %s", sub.get("type"))
                elif mtype == "session_reconnect":
                    reconnect_url = payload["session"]["reconnect_url"]
                    self.on_status("reconnecting", "server requested reconnect")
                    # Subscriptions carry over: connect without resubscribing.
                    return await self._run_once(reconnect_url, resubscribe=False)
                elif mtype == "revocation":
                    sub = payload.get("subscription", {})
                    self.on_status("revoked", f"{sub.get('type')}: {sub.get('status')}")
                    log.error("EventSub subscription revoked: %s", sub)
        return None
