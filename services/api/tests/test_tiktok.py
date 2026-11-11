"""TikTok OAuth, compliance policy, Direct Post / inbox upload and status tracking (contract tests)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from app.db import session_scope, utcnow
from app.integrations.tiktok.client import TikTokClient, authorize_url
from app.models import (
    Clip,
    EditStatus,
    MediaSource,
    PublishingHistory,
    PublishingJob,
    PublishMode,
    PublishStatus,
    TikTokAccount,
    Verification,
)
from app.publishing import policy
from app.services import app_settings, tiktok_service
from app.services.storage import get_storage
from tests.fixtures import tiktok as fx

API = "https://open.tiktokapis.com"
CI = fx.CREATOR_INFO["data"]


# ------------------------------------------------------------------------------ OAuth
def test_authorize_url() -> None:
    q = parse_qs(urlparse(authorize_url("st")).query)
    assert urlparse(authorize_url("st")).netloc == "www.tiktok.com"
    assert q["scope"] == ["user.info.basic,video.publish,video.upload,video.list"]
    assert q["redirect_uri"] == ["https://clipx.example.com/api/oauth/tiktok/callback/"]
    assert q["response_type"] == ["code"] and q["client_key"] == ["test-client-key"]


@respx.mock
async def test_token_exchange_and_refresh_rotation() -> None:
    from app.services.twitch_service import _decrypt

    respx.post(f"{API}/v2/oauth/token/").mock(side_effect=[httpx.Response(200, json=fx.TOKEN),
                                                           httpx.Response(200, json=fx.TOKEN_REFRESHED)])
    respx.get(f"{API}/v2/user/info/").mock(return_value=httpx.Response(200, json=fx.USER_INFO))
    respx.post(f"{API}/v2/post/publish/creator_info/query/").mock(return_value=httpx.Response(200, json=fx.CREATOR_INFO))
    with session_scope() as db:
        url = tiktok_service.start_connect(db, "owner@example.com")
        state = parse_qs(urlparse(url).query)["state"][0]
    with session_scope() as db:
        acct = await tiktok_service.complete_connect(db, "code-123", state, "owner@example.com")
        assert acct.display_name == "FIXTURE Clips" and acct.creator_info["creator_nickname"] == "FIXTURE Clips"
        assert "act.fixture1" not in acct.token.access_token_enc  # encrypted at rest
        acct.token.expires_at = utcnow() + timedelta(minutes=1)  # about to expire
        token = await tiktok_service.access_token(db, acct)
        assert token == "act.fixture2"
        assert _decrypt(acct.token, "refresh_token_enc") == "rft.fixture2"  # rotated refresh token stored


def test_oauth_state_is_single_use_and_bound_to_user() -> None:
    from app.models import Provider
    from app.services.twitch_service import consume_state

    with session_scope() as db:
        state = parse_qs(urlparse(tiktok_service.start_connect(db, "owner@example.com")).query)["state"][0]
    with session_scope() as db, pytest.raises(PermissionError):
        consume_state(db, Provider.tiktok, state, "attacker@example.com")
    with session_scope() as db, pytest.raises(PermissionError):
        consume_state(db, Provider.tiktok, state, "owner@example.com")  # already consumed


# ------------------------------------------------------------------------------ compliance policy
def _choices(**kw):  # type: ignore[no-untyped-def]
    base = dict(privacy_level="SELF_ONLY", allow_comment=False, allow_duet=False, allow_stitch=False, consent=True,
                duration_s=30, brand_content=False, brand_organic=False, client_audited=False)
    base.update(kw)
    return policy.validate_direct_post_choices(CI, **base)


def test_direct_post_requires_manual_choices_and_consent() -> None:
    assert _choices() == []
    assert "selected manually" in _choices(privacy_level=None)[0]
    assert "not one of" in _choices(privacy_level="FOLLOWER_OF_CREATOR", client_audited=True)[0]
    assert "not audited" in _choices(privacy_level="PUBLIC_TO_EVERYONE")[0]
    assert _choices(privacy_level="PUBLIC_TO_EVERYONE", client_audited=True) == []
    assert "stitches are disabled" in _choices(allow_stitch=True)[0]
    assert "Music Usage Confirmation" in _choices(consent=False)[0]
    assert any("Branded content visibility" in e for e in _choices(brand_content=True))
    assert any("Branded Content Policy" in e for e in _choices(brand_content=True, consent=False,
                                                                 privacy_level="PUBLIC_TO_EVERYONE",
                                                                 client_audited=True))
    assert any("third party, or both" in e for e in _choices(disclosure_on=True))
    assert any("at most 300s" in e for e in _choices(duration_s=301))


def test_silent_direct_post_is_never_allowed() -> None:
    cfg = app_settings.PublishingSettings(auto_publish=True, auto_mode="direct_post", client_audited=True)
    assert policy.direct_post_requires_confirmation(cfg)
    assert policy.initial_job_status(PublishMode.direct_post, cfg) == PublishStatus.awaiting_confirmation
    assert policy.initial_job_status(PublishMode.inbox_draft, cfg) == PublishStatus.scheduled


# ------------------------------------------------------------------------------ publishing workflow
def _setup(db, tmp: Path, *, verified: bool = True, transcript: str = "a unique sentence here") -> tuple:  # type: ignore[no-untyped-def]
    from app.integrations.tiktok.client import TikTokTokens
    from app.models import Provider
    from app.services.twitch_service import store_token

    tok = store_token(db, Provider.tiktok, "fixture-open-id",
                      TikTokTokens("act.x", "rft.x", 7200, 15552000, "fixture-open-id", ["video.publish", "video.upload"]))
    acct = TikTokAccount(open_id="fixture-open-id", display_name="FIXTURE Clips", token_id=tok.id,
                         creator_info=CI, creator_info_at=utcnow())
    db.add(acct)
    video = tmp / "v.mp4"
    video.write_bytes(b"\x00" * 3000)
    clip = Clip(title="t", category="funny", quality_score=0.9, verification=Verification.verified if verified
                else Verification.unverified, duration_s=30, media_source=MediaSource.live_buffer,
                edit_status=EditStatus.rendered, caption="Lacy did it 😂", hashtags=["#Lacy"], transcript_text=transcript)
    db.add(clip)
    db.flush()
    clip.rendered_key = f"clips/{clip.id}/render_v1.mp4"
    get_storage().put_file(clip.rendered_key, video, "video/mp4")
    return acct, clip


def test_unverified_clip_not_eligible(tmp_path: Path) -> None:
    with session_scope() as db:
        acct, clip = _setup(db, tmp_path, verified=False)
        e = policy.clip_eligibility(db, clip, app_settings.PublishingSettings(), account_id=acct.id)
        assert not e.eligible and "unverified" in e.reasons[0]


def test_near_duplicate_blocked(tmp_path: Path) -> None:
    with session_scope() as db:
        acct, clip = _setup(db, tmp_path, transcript="no way bro did you see that that was insane right")
        job = tiktok_service.prepare(db, clip, mode=PublishMode.direct_post)
        assert job is not None
        twin = Clip(title="t2", category="funny", quality_score=0.9, verification=Verification.verified,
                    edit_status=EditStatus.rendered, rendered_key="x", caption="c",
                    transcript_text="no way bro did you see that that was insane right now")
        db.add(twin)
        db.flush()
        e = policy.clip_eligibility(db, twin, app_settings.PublishingSettings(), account_id=acct.id)
        assert not e.eligible and "nearly identical" in " ".join(e.reasons)
        assert tiktok_service.prepare(db, clip, mode=PublishMode.direct_post).id == job.id  # idempotent


@respx.mock
def test_direct_post_confirm_upload_and_status(tmp_path: Path, drain) -> None:  # type: ignore[no-untyped-def]
    respx.post(f"{API}/v2/post/publish/creator_info/query/").mock(return_value=httpx.Response(200, json=fx.CREATOR_INFO))
    init = respx.post(f"{API}/v2/post/publish/video/init/").mock(return_value=httpx.Response(200, json=fx.INIT))
    put = respx.put(fx.INIT["data"]["upload_url"]).mock(return_value=httpx.Response(201))
    status = respx.post(f"{API}/v2/post/publish/status/fetch/").mock(side_effect=[
        httpx.Response(200, json=fx.STATUS_PROCESSING), httpx.Response(200, json=fx.STATUS_COMPLETE)])
    with session_scope() as db:
        _, clip = _setup(db, tmp_path)
        job = tiktok_service.prepare(db, clip, mode=PublishMode.direct_post)
        assert job.status == PublishStatus.awaiting_confirmation and drain.queue == []
        errs = tiktok_service.confirm_direct_post(db, job, actor="owner@example.com", privacy_level=None or "",
                                                  allow_comment=False, allow_duet=False, allow_stitch=False,
                                                  disclosure_on=False, brand_content=False, brand_organic=False,
                                                  consent=True, title=job.title)
        assert errs  # no privacy level selected -> refused
        errs = tiktok_service.confirm_direct_post(db, job, actor="owner@example.com", privacy_level="SELF_ONLY",
                                                  allow_comment=True, allow_duet=False, allow_stitch=False,
                                                  disclosure_on=False, brand_content=False, brand_organic=False,
                                                  consent=True, title="Edited caption #Lacy")
        assert errs == []
        jid = job.id
    out = drain.run()
    assert out == ["publish_tiktok:succeeded", "poll_tiktok_status:waiting", "poll_tiktok_status:succeeded"]
    body = json.loads(init.calls[0].request.content)
    assert body["post_info"] == {"privacy_level": "SELF_ONLY", "title": "Edited caption #Lacy", "disable_comment": False,
                                 "disable_duet": True, "disable_stitch": True, "brand_content_toggle": False,
                                 "brand_organic_toggle": False, "is_aigc": False, "video_cover_timestamp_ms": 1000}
    assert body["source_info"] == {"source": "FILE_UPLOAD", "video_size": 3000, "chunk_size": 3000,
                                   "total_chunk_count": 1}
    req = put.calls[0].request
    assert req.headers["content-range"] == "bytes 0-2999/3000" and req.headers["content-type"] == "video/mp4"
    assert status.call_count == 2
    with session_scope() as db:
        j = db.get(PublishingJob, jid)
        assert j.status == PublishStatus.published and j.post_ids == ["7300000000000000001"]
        events = [h.event for h in db.query(PublishingHistory).order_by(PublishingHistory.at)]
        assert events[:2] == ["prepared", "confirmed"] and "published" in events


@respx.mock
def test_unaudited_error_is_reported_not_retried(tmp_path: Path, drain) -> None:  # type: ignore[no-untyped-def]
    respx.post(f"{API}/v2/post/publish/creator_info/query/").mock(return_value=httpx.Response(200, json=fx.CREATOR_INFO))
    init = respx.post(f"{API}/v2/post/publish/video/init/").mock(return_value=httpx.Response(403, json=fx.UNAUDITED))
    with session_scope() as db:
        _, clip = _setup(db, tmp_path)
        job = tiktok_service.prepare(db, clip, mode=PublishMode.direct_post)
        tiktok_service.confirm_direct_post(db, job, actor="o", privacy_level="SELF_ONLY", allow_comment=False,
                                           allow_duet=False, allow_stitch=False, disclosure_on=False,
                                           brand_content=False, brand_organic=False, consent=True, title="t")
        jid = job.id
    assert drain.run() == ["publish_tiktok:dead"] and init.call_count == 1
    with session_scope() as db:
        j = db.get(PublishingJob, jid)
        assert j.status == PublishStatus.failed and "unaudited_client" in j.fail_reason


@respx.mock
def test_auto_inbox_requires_recorded_consent(tmp_path: Path, drain, client) -> None:  # type: ignore[no-untyped-def]
    respx.post(f"{API}/v2/post/publish/inbox/video/init/").mock(return_value=httpx.Response(200, json=fx.INIT))
    respx.put(fx.INIT["data"]["upload_url"]).mock(return_value=httpx.Response(201))
    respx.post(f"{API}/v2/post/publish/status/fetch/").mock(return_value=httpx.Response(200, json=fx.STATUS_INBOX))
    r = client.put("/v1/settings/publishing", {"auto_publish": True, "auto_mode": "inbox_draft"})
    assert r.status_code == 422 and "consent" in r.text
    r = client.put("/v1/settings/publishing", {"auto_publish": True, "auto_mode": "inbox_draft",
                                               "automation_consent": True})
    assert r.status_code == 200 and r.json()["automation_consent_by"] == "owner@example.com"
    with session_scope() as db:
        _, clip = _setup(db, tmp_path)
        from app.services import jobs

        jobs.enqueue(db, "prepare_publish", "pp1", payload={"clip_id": str(clip.id)})
    out = drain.run()
    assert out == ["prepare_publish:succeeded", "publish_tiktok:succeeded", "poll_tiktok_status:succeeded"]
    with session_scope() as db:
        j = db.query(PublishingJob).one()
        assert j.mode == PublishMode.inbox_draft and j.status == PublishStatus.sent_to_inbox
        assert j.schedule_basis and "no data-driven recommendation" in j.schedule_basis


async def test_video_list_fields() -> None:
    with respx.mock:
        route = respx.post(f"{API}/v2/video/list/").mock(return_value=httpx.Response(200, json=fx.VIDEO_LIST))
        data = await TikTokClient("t").list_videos()
        q = parse_qs(route.calls[0].request.url.query.decode())
        assert {"view_count", "like_count", "comment_count", "share_count"} <= set(q["fields"][0].split(","))
        assert data["videos"][0]["view_count"] == 1200
