"""End-to-end (in-process): real media -> transcription -> render -> caption -> publishing queue."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db import session_scope
from app.models import Clip, EditStatus, MediaSource, PublishingJob, SubtitleTrack, Verification
from app.services import jobs
from app.services.storage import get_storage


@pytest.mark.media
def test_clip_pipeline_end_to_end(speech_video: Path, drain, client) -> None:  # type: ignore[no-untyped-def]
    with session_scope() as db:
        clip = Clip(title="Hello chat", category="funny", quality_score=0.9, verification=Verification.verified,
                    media_source=MediaSource.live_buffer, edit_status=EditStatus.queued,
                    rights_basis="test: authorized feed")
        db.add(clip)
        db.flush()
        clip.source_media_key = f"clips/{clip.id}/source.mp4"
        get_storage().put_file(clip.source_media_key, speech_video, "video/mp4")
        jobs.enqueue(db, "transcribe_clip", f"t:{clip.id}", ref_id=str(clip.id), payload={"clip_id": str(clip.id)})
        cid = clip.id
    out = drain.run()
    assert out[:4] == ["transcribe_clip:succeeded", "render_clip:succeeded", "caption_clip:succeeded",
                       "prepare_publish:succeeded"], out
    with session_scope() as db:
        c = db.get(Clip, cid)
        assert c.edit_status == EditStatus.rendered and get_storage().exists(c.rendered_key)
        assert "hello" in (c.transcript_text or "").lower()
        assert c.caption and c.hashtags[0] == "#Lacy" and c.caption_source == "template"
        track = db.query(SubtitleTrack).filter_by(clip_id=cid, is_active=True).one()
        assert track.cues and track.cues[0]["start"] < 1.5
        assert db.query(PublishingJob).count() == 0  # auto-publish off by default: nothing posted
    d = client.get(f"/v1/clips/{cid}").json()
    assert d["video_url"] and d["layout"] in ("fit", "track", "stacked")
    assert [p["kind"] for p in d["processing"]][:2] == ["transcribe_clip", "render_clip"]
