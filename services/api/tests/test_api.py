"""HTTP layer: dashboard endpoints with signed requests, validation and audit logging."""

from __future__ import annotations

from app.db import session_scope
from app.models import AuditLog, Clip, EditStatus, SubtitleTrack, Verification


def _clip(**kw) -> str:  # type: ignore[no-untyped-def]
    with session_scope() as db:
        fields = {"title": "Clip", "category": "funny", "quality_score": 0.8,
                  "verification": Verification.verified, "edit_status": EditStatus.rendered, **kw}
        c = Clip(**fields)
        db.add(c)
        db.flush()
        db.add(SubtitleTrack(clip_id=c.id, engine="faster-whisper:test", words=[], cues=[]))
        return str(c.id)


def test_overview_empty_state_is_honest(client) -> None:  # type: ignore[no-untyped-def]
    d = client.get("/v1/overview").json()
    assert d["live"] is None and d["counts"]["clips"] == 0 and d["clips_per_day"] == []
    assert not d["capabilities"]["can_download_clips"]


def test_live_status_and_timeline(client) -> None:  # type: ignore[no-untyped-def]
    s = client.get("/v1/live/status").json()
    assert s["stage"] == "idle" and s["monitor"]["alive"] is False
    assert client.get("/v1/live/timeline").json()["session"] is None


def test_clip_list_filters_and_detail(client) -> None:  # type: ignore[no-untyped-def]
    cid = _clip()
    _clip(title="Other", verification=Verification.unverified, edit_status=EditStatus.needs_media)
    assert client.get("/v1/clips").json()["total"] == 2
    assert client.get("/v1/clips?verification=verified").json()["total"] == 1
    assert client.get("/v1/clips?q=Other").json()["items"][0]["title"] == "Other"
    d = client.get(f"/v1/clips/{cid}").json()
    assert d["subtitles"]["engine"] == "faster-whisper:test" and d["edit_config"] is None
    assert client.get("/v1/clips/00000000-0000-0000-0000-000000000000").status_code == 404


def test_editor_validation_and_audit(client) -> None:  # type: ignore[no-untyped-def]
    cid = _clip()
    assert client.put(f"/v1/clips/{cid}/edit-config", {"subtitle_style": {"size": 500}}).status_code == 422
    assert client.put(f"/v1/clips/{cid}/edit-config", {"subtitle_style": {"primary": "red"}}).status_code == 422
    assert client.put(f"/v1/clips/{cid}/edit-config", {"layout": "spiral"}).status_code == 422
    ok = client.put(f"/v1/clips/{cid}/edit-config", {"layout": "stacked", "zoom_intensity": 0.5,
                                                    "subtitle_style": {"size": 96, "primary": "#FFE600"}})
    assert ok.status_code == 200
    d = client.get(f"/v1/clips/{cid}").json()["edit_config"]
    assert d["layout"] == "stacked" and d["subtitle_style"]["size"] == 96
    with session_scope() as db:
        assert db.query(AuditLog).filter_by(action="clip.edit_config").count() == 1


def test_subtitle_editing(client) -> None:  # type: ignore[no-untyped-def]
    cid = _clip()
    bad = client.put(f"/v1/clips/{cid}/subtitles", {"cues": [{"start": 1, "end": 2, "text": "a"},
                                                            {"start": 1.5, "end": 3, "text": "b"}]})
    assert bad.status_code == 422 and "overlaps" in bad.text
    r = client.put(f"/v1/clips/{cid}/subtitles", {"cues": [{"start": 0.5, "end": 2, "text": "Hello chat",
                                                           "emphasis": [1], "zone": "upper"}]})
    assert r.status_code == 200
    cues = client.get(f"/v1/clips/{cid}").json()["subtitles"]["cues"]
    assert cues[0]["zone"] == "upper" and cues[0]["words"][1]["emph"] is True
    assert "Hello chat" in client.get(f"/v1/clips/{cid}/subtitles.srt").json()["srt"]


def test_rerender_requires_media_and_bumps_version(client) -> None:  # type: ignore[no-untyped-def]
    cid = _clip()
    assert client.post(f"/v1/clips/{cid}/render").status_code == 409
    cid2 = _clip(source_media_key="clips/x/source.mp4")
    r = client.post(f"/v1/clips/{cid2}/render").json()
    assert r["status"] == "queued" and r["version"] == 2


def test_settings_validation(client) -> None:  # type: ignore[no-untyped-def]
    bad = client.put("/v1/settings/processing", {"weights": {"storytelling_context": 0.9}})
    assert bad.status_code == 422 and "sum to 1.0" in bad.text
    ok = client.put("/v1/settings/processing", {"weights": {"storytelling_context": 0.35, "visual_interest": 0.2},
                                                "min_quality": 0.7})
    assert ok.status_code == 200 and ok.json()["weights"]["storytelling_context"] == 0.35
    s = client.get("/v1/settings").json()
    assert s["processing"]["min_quality"] == 0.7
    assert "secret" not in str(s["system"]).lower()  # configuration status only


def test_queue_requires_tiktok_account(client) -> None:  # type: ignore[no-untyped-def]
    cid = _clip()
    r = client.post(f"/v1/publishing/clips/{cid}/queue", {"mode": "direct_post"})
    assert r.status_code == 409 and "TikTok" in r.text


def test_twitch_connect_returns_authorize_url_and_audits(client) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/v1/integrations/twitch/connect", {"role": "editor"})
    assert r.status_code == 200 and "editor%3Amanage%3Aclips" in r.json()["authorize_url"]
    bad = client.post("/v1/integrations/twitch/callback", {"code": "abcd", "state": "x" * 20})
    assert bad.status_code == 403


def test_concurrent_requests_do_not_deadlock(client) -> None:  # type: ignore[no-untyped-def]
    """Regression: per-request user row locks once deadlocked async endpoints under concurrent load."""
    from concurrent.futures import ThreadPoolExecutor

    paths = ["/v1/integrations/twitch", "/v1/integrations/tiktok", "/v1/settings", "/v1/overview",
             "/v1/live/status", "/v1/audit"] * 3
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(lambda p: client.get(p).status_code, paths))
    assert codes == [200] * len(paths)
