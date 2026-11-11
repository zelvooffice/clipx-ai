"""Captions, viral discovery/templates and posting-time recommendations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.captions.generator import generate, normalize_tags, template_caption
from app.db import session_scope, utcnow
from app.models import AnalyticsSnapshot, Clip, EditStatus, MediaSource, Verification
from app.publishing import scheduler
from app.services.app_settings import PublishingSettings
from app.viral import templates
from app.viral.discovery import popularity


def test_template_caption_rules() -> None:
    r = template_caption(clip_id="1", title="t", category="funny", transcript="No way bro! That was insane.",
                         game="Just Chatting", recent=[])
    assert r.hashtags[0] == "#Lacy" and 3 <= len(r.hashtags) <= 6
    assert "lacy" in r.caption.lower()
    assert "#JustChatting" in r.hashtags
    assert not {"#fyp", "#viral", "#foryou"} & {t.lower() for t in r.hashtags}


def test_captions_are_not_reused() -> None:
    caps: list[str] = []
    for i in range(6):
        caps.append(template_caption(clip_id=str(i), title="t", category="reaction", transcript="", game=None,
                                     recent=caps).caption)
    assert len(set(caps)) == len(caps)


def test_tag_normalisation() -> None:
    assert normalize_tags(["fyp", "#lacy", "#Twitch!", "#twitch"], game=None) == ["#Lacy", "#Twitch"]


def test_llm_caption_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.captions import generator

    monkeypatch.setattr(generator.llm, "complete_json",
                        lambda *a, **k: {"caption": "She really said that 😭", "hashtags": ["#viral", "#storytime"]})
    r = generate(clip_id="1", title="t", category="storytelling", transcript="x", context=None, game=None, recent=[])
    assert r.source == "llm" and r.caption.startswith("Lacy:") and r.hashtags == ["#Lacy", "#storytime"]


def test_popularity_requires_real_numbers() -> None:
    now = datetime.now(UTC)
    assert popularity(None, now, now) == 0.0
    assert popularity(10000, now - timedelta(days=1), now) > popularity(10000, now - timedelta(days=20), now)


def test_viral_templates_refuse_unauthorized_media() -> None:
    with session_scope() as db:
        unauthorized = Clip(title="x", category="funny", verification=Verification.verified,
                            edit_status=EditStatus.rendered, rendered_key="k", media_source=MediaSource.none)
        db.add(unauthorized)
        db.flush()
        with pytest.raises(PermissionError):
            templates.create_edit(db, "pov", [unauthorized.id], "t", actor="o")
        with pytest.raises(ValueError):
            templates.create_edit(db, "how_me_and_bro_look", [unauthorized.id], "t", actor="o")


def test_suggestions_explain_evidence() -> None:
    with session_scope() as db:
        s = templates.suggest(db)
        assert {x["template"] for x in s} == set(templates.TEMPLATES)
        assert all(x["evidence"] for x in s) and not any(x["ready"] for x in s)


def test_recommendations_need_enough_data() -> None:
    cfg = PublishingSettings()
    with session_scope() as db:
        r = scheduler.recommendations(db, cfg)
        assert r["status"] == "insufficient_data" and r["windows"] == []


def test_recommendations_from_measured_posts() -> None:
    # FIXTURE metrics (synthetic, test only): evening posts do better.
    base = utcnow() - timedelta(days=10)
    with session_scope() as db:
        for i in range(12):
            evening = i % 2 == 0
            posted = base.replace(hour=19 if evening else 9, minute=0) - timedelta(days=i)
            db.add(AnalyticsSnapshot(tiktok_video_id=f"v{i}", posted_at=posted, views=5000 if evening else 500,
                                     likes=10, comments=1, shares=1))
    with session_scope() as db:
        r = scheduler.recommendations(db, PublishingSettings())
        assert r["status"] == "ok" and r["recommended"][0]["start_hour"] == 18
        slot, basis = scheduler.next_slot(db, PublishingSettings(), account_id=None)
        assert "Recommended block 18:00-21:00" in basis and slot.astimezone(UTC).hour == 18


@pytest.mark.media
@pytest.mark.parametrize("template,n", [("pov", 1), ("how_me_and_bro_look", 2), ("compilation", 3), ("reaction", 2),
                                        ("before_after", 2)])
def test_viral_templates_render_real_video(tmp_path, drain, template: str, n: int) -> None:  # type: ignore[no-untyped-def]
    """Every template's FFmpeg graph runs on real rendered clips and yields a 1080x1920 H.264/AAC file."""
    import json
    import subprocess

    from app.models import ViralEdit
    from app.services.storage import get_storage
    from app.video.render import RenderOptions, render
    from tests.conftest import make_video

    ids = []
    with session_scope() as db:
        for i in range(n):
            src = make_video(tmp_path / f"s{i}.mp4", seconds=6)
            res = render(src, tmp_path / f"r{i}.mp4", [{"w": "Hello", "start": 0.5, "end": 1.0, "p": 1.0}],
                         RenderOptions(clip_id=f"v{i}", category="funny", preset="ultrafast"))
            c = Clip(title=f"clip {i}", category="funny", quality_score=0.8, verification=Verification.verified,
                     edit_status=EditStatus.rendered, media_source=MediaSource.live_buffer, duration_s=res.duration)
            db.add(c)
            db.flush()
            c.rendered_key = f"clips/{c.id}/render_v1.mp4"
            get_storage().put_file(c.rendered_key, res.output, "video/mp4")
            ids.append(c.id)
        edit = templates.create_edit(db, template, ids, "when the plan {actually} works", actor="o")
        eid = edit.id
    out = drain.run(kinds={"render_viral_edit"})
    assert out == ["render_viral_edit:succeeded"], out
    with session_scope() as db:
        e = db.get(ViralEdit, eid)
        assert e.status == "rendered" and e.output_clip_id
        clip = db.get(Clip, e.output_clip_id)
        assert clip.origin.value == "viral_edit" and clip.media_source == MediaSource.composed
        path = get_storage().local_path(clip.rendered_key)  # type: ignore[attr-defined]
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
                                     capture_output=True, text=True, check=True).stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["codec_name"], v["width"], v["height"]) == ("h264", 1080, 1920)
    assert any(s["codec_type"] == "audio" for s in info["streams"])
