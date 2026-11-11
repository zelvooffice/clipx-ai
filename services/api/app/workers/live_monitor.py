"""Live monitor process: ``python -m app.workers.live_monitor``.

* EventSub (WebSocket by default) for stream.online / stream.offline / channel.chat.message.
* Get Streams polling every TWITCH_POLL_INTERVAL_S reconciles missed events (and is the only detector
  when no user token exists for the WebSocket transport).
* Starts the full A/V analyzer when the authorized feed is live, otherwise the chat-spike nominator.
* A Redis lock guarantees a single monitor instance.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
import uuid
from typing import Any

from app.config import get_settings
from app.db import session_scope
from app.integrations.twitch import helix
from app.integrations.twitch.eventsub import EventSubWebSocket
from app.models import SessionStatus, StreamSession
from app.services import chat, streams, twitch_service
from app.services.audit import syslog
from app.services.redis_client import acquire_lock, get_redis, refresh_lock, release_lock
from app.workers.chat_detector import ChatSpikeDetector
from app.workers.live_analyzer import LiveAnalyzer

log = logging.getLogger("clipx.monitor")
LOCK = "live_monitor"


class LiveMonitor:
    def __init__(self) -> None:
        self.owner = uuid.uuid4().hex
        self.broadcaster: dict[str, Any] | None = None
        self.tasks: dict[str, asyncio.Task[Any]] = {}
        self.workers: dict[str, Any] = {}
        self.ws: EventSubWebSocket | None = None
        self._stop = asyncio.Event()
        self.status: dict[str, Any] = {"eventsub": "idle", "poller": "idle"}

    # ------------------------------------------------------------------ status (read by the API)
    def publish_status(self, **kw: Any) -> None:
        self.status.update(kw, heartbeat=int(time.time()))
        get_redis().hset("monitor:status", mapping={k: str(v) for k, v in self.status.items()})
        get_redis().expire("monitor:status", 300)

    # ------------------------------------------------------------------ lifecycle
    async def run(self) -> None:
        if not acquire_lock(LOCK, self.owner, 60_000):
            log.error("another live monitor holds the lock; exiting")
            return
        try:
            self.broadcaster = await twitch_service.target_broadcaster()
            log.info("monitoring %s (%s)", self.broadcaster["login"], self.broadcaster["id"])
            await self._resume_existing()
            self.tasks["poller"] = asyncio.create_task(self._poll_loop())
            self.tasks["lock"] = asyncio.create_task(self._lock_loop())
            if get_settings().twitch_eventsub_transport == "websocket":
                self.tasks["eventsub"] = asyncio.create_task(self._eventsub_loop())
            await self._stop.wait()
        finally:
            for t in self.tasks.values():
                t.cancel()
            await self._stop_workers()
            release_lock(LOCK, self.owner)

    def stop(self) -> None:
        if self.ws:
            self.ws.stop()
        self._stop.set()

    async def _lock_loop(self) -> None:
        while True:
            await asyncio.sleep(20)
            if not refresh_lock(LOCK, self.owner, 60_000):
                log.error("lost the live monitor lock; stopping")
                self.stop()
                return

    async def _resume_existing(self) -> None:
        with session_scope() as db:
            sess = streams.live_session(db)
            sid = sess.id if sess else None
            mode = sess.analysis_mode if sess else None
        if sid:
            await self._start_workers(sid, mode or "none")

    # ------------------------------------------------------------------ EventSub
    async def _eventsub_loop(self) -> None:
        while not self._stop.is_set():
            with session_scope() as db:
                acct = twitch_service.best_account(db, needs="chat") or twitch_service.best_account(db, needs="clips")
                acct_id = acct.id if acct else None
            if acct_id is None:
                self.publish_status(eventsub="no user token: connect a Twitch account (polling only)")
                await asyncio.sleep(60)
                continue
            self.ws = EventSubWebSocket(self._subscribe, self._on_event,
                                        on_status=lambda s, d: self.publish_status(eventsub=f"{s}: {d}"[:200]))
            await self.ws.run_forever()

    async def _subscribe(self, session_id: str) -> None:
        assert self.broadcaster is not None
        bid = self.broadcaster["id"]
        with session_scope() as db:
            chat_acct = twitch_service.best_account(db, needs="chat")
            acct = chat_acct or twitch_service.best_account(db, needs="clips")
            assert acct is not None
            token = await twitch_service.get_user_access_token(db, acct)
            chat_user = chat_acct.twitch_user_id if chat_acct else None
        api = helix.Helix(token)
        transport = {"method": "websocket", "session_id": session_id}
        subs: list[tuple[str, dict[str, str]]] = [("stream.online", {"broadcaster_user_id": bid}),
                                                  ("stream.offline", {"broadcaster_user_id": bid})]
        if chat_user:
            subs.append(("channel.chat.message", {"broadcaster_user_id": bid, "user_id": chat_user}))
        await asyncio.gather(*(api.create_subscription(t, "1", c, transport) for t, c in subs))
        self.publish_status(eventsub=f"subscribed: {', '.join(t for t, _ in subs)}")

    async def _on_event(self, type_: str, event: dict[str, Any], _sub: dict[str, Any]) -> None:
        await handle_event(self, type_, event)

    # ------------------------------------------------------------------ polling
    async def _poll_loop(self) -> None:
        interval = get_settings().twitch_poll_interval_s
        while not self._stop.is_set():
            try:
                await self.reconcile()
                self.publish_status(poller=f"ok @ {time.strftime('%H:%M:%S', time.gmtime())} UTC")
            except Exception as exc:
                log.exception("poll failed")
                self.publish_status(poller=f"error: {exc}"[:200])
            await asyncio.sleep(interval)

    async def reconcile(self) -> None:
        assert self.broadcaster is not None
        stream = await (await twitch_service.app_helix()).get_stream(self.broadcaster["id"])
        with session_scope() as db:
            current = streams.live_session(db)
            if stream and (current is None or current.twitch_stream_id != stream["id"]):
                sess, created = streams.handle_online(
                    db, broadcaster_id=self.broadcaster["id"], stream_id=stream["id"],
                    started_at=stream.get("started_at"), title=stream.get("title"), game=stream.get("game_name"),
                    detected_via="poll")
                sid, mode = sess.id, sess.analysis_mode
            elif not stream and current is not None:
                streams.handle_offline(db, session=current, reason="poll: stream no longer live")
                sid, mode, created = current.id, None, False
            else:
                if current and stream and not current.title:
                    current.title = stream.get("title")
                return
        if mode is not None and created:
            await self._start_workers(sid, mode)
        elif mode is None:
            await self._stop_workers()

    # ------------------------------------------------------------------ analyzers
    async def _start_workers(self, session_id: uuid.UUID, mode: str) -> None:
        await self._stop_workers()
        if mode == "full_av":
            w: Any = LiveAnalyzer(session_id)
        elif mode == "chat_only":
            w = ChatSpikeDetector(session_id)
        else:
            self.publish_status(analysis="none: no analysable signal (see Sources)")
            return
        self.workers["analyzer"] = w
        self.tasks["analyzer"] = asyncio.create_task(w.run())
        self.publish_status(analysis=mode, session=str(session_id))

    async def _stop_workers(self) -> None:
        w = self.workers.pop("analyzer", None)
        t = self.tasks.pop("analyzer", None)
        if w is not None:
            w.stop()
        if t is not None:
            try:
                await asyncio.wait_for(t, timeout=180)
            except (TimeoutError, asyncio.CancelledError):
                t.cancel()
        self.publish_status(analysis="idle")


async def handle_event(monitor: LiveMonitor | None, type_: str, event: dict[str, Any]) -> None:
    """Shared by the WebSocket client and the webhook endpoint."""
    if type_ == "stream.online":
        stream: dict[str, Any] = {}
        try:
            stream = await (await twitch_service.app_helix()).get_stream(event["broadcaster_user_id"]) or {}
        except Exception:
            log.warning("could not fetch stream details for online event", exc_info=True)
        with session_scope() as db:
            sess, created = streams.handle_online(
                db, broadcaster_id=event["broadcaster_user_id"], stream_id=event["id"],
                started_at=event.get("started_at"), title=stream.get("title"), game=stream.get("game_name"),
                detected_via="eventsub")
            sid, mode = sess.id, sess.analysis_mode
        if monitor and created:
            await monitor._start_workers(sid, mode)
    elif type_ == "stream.offline":
        with session_scope() as db:
            streams.handle_offline(db)
        if monitor:
            await monitor._stop_workers()
    elif type_ == "channel.chat.message":
        with session_scope() as db:
            live = db.query(StreamSession).filter(StreamSession.status == SessionStatus.live).first()
            if live is None:
                return
            live_id, started = str(live.id), live.started_at.timestamp()
        text = (event.get("message") or {}).get("text") or ""
        chat.record(live_id, int(time.time() - started), event.get("chatter_user_id", ""), text)


def main() -> None:
    logging.basicConfig(level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    mon = LiveMonitor()
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, mon.stop)
    with session_scope() as db:
        syslog(db, "INFO", "monitor", "start", "Live monitor starting")
    loop.run_until_complete(mon.run())


if __name__ == "__main__":
    main()
