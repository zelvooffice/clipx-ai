"""LIVE/offline detection, session lifecycle, native clip creation + polling."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.db import session_scope
from app.integrations.twitch.helix import TokenSet
from app.models import (
    CandidateStatus,
    Clip,
    HighlightCandidate,
    JobStatus,
    ProcessingJob,
    Provider,
    SessionStatus,
    StreamSession,
    TwitchAccount,
    TwitchClipStatus,
    TwitchRole,
    Verification,
)
from app.services import streams, twitch_service
from tests.fixtures import twitch as fx

H = "https://api.twitch.tv/helix"


def _online(db, stream_id: str = "s1", via: str = "eventsub") -> StreamSession:  # type: ignore[no-untyped-def]
    sess, _ = streams.handle_online(db, broadcaster_id="141981764", stream_id=stream_id,
                                    started_at=datetime.now(UTC).isoformat(), title="T", game="Just Chatting",
                                    detected_via=via, probe_ingest=False)
    return sess


def _operator(db, scopes=("clips:edit", "user:read:chat"), role=TwitchRole.operator, uid="999"):  # type: ignore[no-untyped-def]
    tok = twitch_service.store_token(db, Provider.twitch, uid, TokenSet("user-token", "r", 14000, list(scopes)))
    acct = TwitchAccount(twitch_user_id=uid, login="op", role=role, token_id=tok.id)
    db.add(acct)
    db.flush()
    return acct


def test_online_is_idempotent_and_records_capabilities() -> None:
    with session_scope() as db:
        s1 = _online(db)
        s2, created = streams.handle_online(db, broadcaster_id="141981764", stream_id="s1", started_at=None,
                                            title=None, game=None, detected_via="poll", probe_ingest=False)
        assert s1.id == s2.id and not created
        assert s1.analysis_mode == "none"  # nothing connected: honest mode
        assert s1.capabilities["signals"] == []


def test_new_stream_supersedes_stale_live_session(drain) -> None:  # type: ignore[no-untyped-def]
    with session_scope() as db:
        old = _online(db, "s1")
        _online(db, "s2")
        assert db.get(StreamSession, old.id).status == SessionStatus.finalizing


def test_offline_finalizes_session(drain) -> None:  # type: ignore[no-untyped-def]
    with session_scope() as db:
        sess = _online(db)
        sid = sess.id
    with session_scope() as db:
        streams.handle_offline(db)
    assert drain.run() == ["finalize_session:succeeded"]
    with session_scope() as db:
        s = db.get(StreamSession, sid)
        assert s.status == SessionStatus.complete and s.ended_at is not None


def test_chat_only_mode_when_operator_connected() -> None:
    with session_scope() as db:
        _operator(db)
        assert _online(db).analysis_mode == "chat_only"


# ------------------------------------------------------------------------------ webhook transport end to end
def _webhook(raw_client, mtype: str, payload: dict, mid: str):  # type: ignore[no-untyped-def]
    body = json.dumps(payload).encode()
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
    sig = "sha256=" + hmac.new(b"eventsub-secret-for-tests", mid.encode() + ts.encode() + body, hashlib.sha256).hexdigest()
    return raw_client.post("/webhooks/twitch/eventsub", content=body, headers={
        "Twitch-Eventsub-Message-Id": mid, "Twitch-Eventsub-Message-Timestamp": ts,
        "Twitch-Eventsub-Message-Type": mtype, "Twitch-Eventsub-Message-Signature": sig})


@respx.mock
def test_webhook_challenge_online_offline(raw_client, drain) -> None:  # type: ignore[no-untyped-def]
    respx.post("https://id.twitch.tv/oauth2/token").mock(return_value=httpx.Response(200, json=fx.APP_TOKEN))
    respx.get(f"{H}/streams").mock(return_value=httpx.Response(200, json={"data": [fx.STREAM_LIVE]}))
    r = _webhook(raw_client, "webhook_callback_verification", {"challenge": "pogchamp-kappa-360noscope"}, "c1")
    assert r.status_code == 200 and r.text == "pogchamp-kappa-360noscope"
    assert r.headers["content-type"].startswith("text/plain")
    ev = {"subscription": {"type": "stream.online"},
          "event": {"id": "40952121085", "broadcaster_user_id": "141981764", "type": "live",
                    "started_at": "2026-11-11T12:00:00Z"}}
    assert _webhook(raw_client, "notification", ev, "n1").status_code == 204
    assert _webhook(raw_client, "notification", ev, "n1").status_code == 204  # duplicate ignored
    with session_scope() as db:
        sessions = db.query(StreamSession).all()
        assert len(sessions) == 1 and sessions[0].title == "FIXTURE stream title"
        assert sessions[0].status == SessionStatus.live
    off = {"subscription": {"type": "stream.offline"}, "event": {"broadcaster_user_id": "141981764"}}
    assert _webhook(raw_client, "notification", off, "n2").status_code == 204
    with session_scope() as db:
        assert db.query(StreamSession).one().status == SessionStatus.finalizing


@respx.mock
async def test_poll_reconcile_detects_live_and_offline(drain) -> None:  # type: ignore[no-untyped-def]
    from app.workers.live_monitor import LiveMonitor

    respx.post("https://id.twitch.tv/oauth2/token").mock(return_value=httpx.Response(200, json=fx.APP_TOKEN))
    streams_route = respx.get(f"{H}/streams").mock(return_value=httpx.Response(200, json={"data": [fx.STREAM_LIVE]}))
    mon = LiveMonitor()
    mon.broadcaster = fx.USER_LACY
    await mon.reconcile()
    with session_scope() as db:
        s = db.query(StreamSession).one()
        assert s.detected_via == "poll" and s.status == SessionStatus.live
    streams_route.mock(return_value=httpx.Response(200, json={"data": []}))
    await mon.reconcile()
    with session_scope() as db:
        assert db.query(StreamSession).one().status == SessionStatus.finalizing


# ------------------------------------------------------------------------------ Create Clip + polling
def _provisional_clip(db, sess):  # type: ignore[no-untyped-def]
    from app.services import highlights

    cand = HighlightCandidate(session_id=sess.id, peak_offset_s=100, start_offset_s=60, end_offset_s=100,
                              category="funny", explanation="chat spike", quality_score=0.4, confidence=0.2,
                              status=CandidateStatus.provisional, dedupe_key="peak:12")
    db.add(cand)
    db.flush()
    return highlights.create_provisional_clip(db, sess, cand)


@respx.mock
def test_create_clip_and_poll_until_created(drain, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    respx.post("https://id.twitch.tv/oauth2/token").mock(return_value=httpx.Response(200, json=fx.APP_TOKEN))
    respx.get("https://id.twitch.tv/oauth2/validate").mock(return_value=httpx.Response(200, json=fx.VALIDATE))
    create = respx.post(f"{H}/clips").mock(return_value=httpx.Response(202, json=fx.CREATE_CLIP))
    get = respx.get(f"{H}/clips").mock(side_effect=[httpx.Response(200, json={"data": []}),
                                                    httpx.Response(200, json={"data": [fx.GET_CLIP]})])
    with session_scope() as db:
        _operator(db)
        sess = _online(db)
        clip = _provisional_clip(db, sess)
        cid = clip.id
    out = drain.run(kinds={"create_twitch_clip", "poll_twitch_clip"})
    assert out[:3] == ["create_twitch_clip:succeeded", "poll_twitch_clip:waiting", "poll_twitch_clip:succeeded"]
    assert create.call_count == 1 and get.call_count == 2
    q = create.calls[0].request.url.params
    assert q["broadcaster_id"] == "141981764" and float(q["duration"]) == 40.0
    with session_scope() as db:
        c = db.get(Clip, cid)
        assert c.twitch_status == TwitchClipStatus.created
        assert c.twitch_clip_url == fx.GET_CLIP["url"] and c.verification == Verification.unverified
        # media retrieval was attempted and honestly reported unavailable (no editor authorization)
        assert db.query(ProcessingJob).filter_by(kind="fetch_clip_media").count() == 1


@respx.mock
def test_clip_never_confirmed_is_failed_after_60s(drain) -> None:  # type: ignore[no-untyped-def]
    respx.post("https://id.twitch.tv/oauth2/token").mock(return_value=httpx.Response(200, json=fx.APP_TOKEN))
    respx.get(f"{H}/clips").mock(return_value=httpx.Response(200, json={"data": []}))
    with session_scope() as db:
        sess = _online(db)
        clip = _provisional_clip(db, sess)
        clip.twitch_clip_id, clip.twitch_status = "Pending1", TwitchClipStatus.pending
        clip.render_meta = {"twitch_requested_at": time.time() - 61}
        cid = clip.id
        from app.services import jobs

        jobs.enqueue(db, "poll_twitch_clip", "p1", payload={"clip_id": str(cid)})
    drain.run(kinds={"poll_twitch_clip"})
    with session_scope() as db:
        c = db.get(Clip, cid)
        assert c.twitch_status == TwitchClipStatus.failed and "60 seconds" in c.twitch_error


@respx.mock
def test_restricted_clipping_is_recorded_not_retried(drain) -> None:  # type: ignore[no-untyped-def]
    respx.get("https://id.twitch.tv/oauth2/validate").mock(return_value=httpx.Response(200, json=fx.VALIDATE))
    create = respx.post(f"{H}/clips").mock(return_value=httpx.Response(403, json={
        "message": "The broadcaster has restricted the ability to capture clips to followers and/or subscribers only."}))
    with session_scope() as db:
        _operator(db)
        sess = _online(db)
        cid = _provisional_clip(db, sess).id
    assert drain.run(kinds={"create_twitch_clip"}) == ["create_twitch_clip:dead"]
    assert create.call_count == 1
    with session_scope() as db:
        c = db.get(Clip, cid)
        assert c.twitch_status == TwitchClipStatus.failed and "restricted" in c.twitch_error
        assert db.query(ProcessingJob).filter_by(kind="create_twitch_clip").one().status == JobStatus.dead


def test_media_unavailable_without_editor_auth(drain) -> None:  # type: ignore[no-untyped-def]
    with session_scope() as db:
        _operator(db)
        sess = _online(db)
        clip = _provisional_clip(db, sess)
        clip.twitch_clip_id, clip.twitch_status = "C1", TwitchClipStatus.created
        cid = clip.id
        from app.services import jobs

        jobs.enqueue(db, "fetch_clip_media", "m1", payload={"clip_id": str(cid)})
    assert drain.run(kinds={"fetch_clip_media"}) == ["fetch_clip_media:succeeded"]
    with session_scope() as db:
        c = db.get(Clip, cid)
        assert c.edit_status.value == "needs_media" and "broadcaster or editor" in c.edit_error
        assert c.source_media_key is None
