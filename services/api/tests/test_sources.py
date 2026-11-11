"""Video source validation: capability matrix, analysis modes, rolling buffer with real FFmpeg."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from app.db import session_scope
from app.integrations.twitch.helix import TokenSet
from app.models import Provider, SourceKind, SourceStatus, TwitchAccount, TwitchRole, VideoSource
from app.services import twitch_service
from app.sources import registry
from app.sources.live_ingest import RollingBuffer
from tests.conftest import make_video


def _acct(db, role: TwitchRole, scopes: list[str], uid: str) -> None:  # type: ignore[no-untyped-def]
    tok = twitch_service.store_token(db, Provider.twitch, uid, TokenSet("t", "r", 1000, scopes))
    db.add(TwitchAccount(twitch_user_id=uid, login=f"u{uid}", role=role, token_id=tok.id))
    db.flush()


def test_nothing_connected_reports_limitations() -> None:
    with session_scope() as db:
        rep = registry.probe_all(db, live=False)
        assert rep["analysis_mode"] == "none" and rep["signals"] == []
        notes = " ".join(rep["twitch"]["notes"])
        assert "cannot be downloaded" in notes and "video feed" in notes
        cm = db.query(VideoSource).filter_by(kind=SourceKind.clip_media).one()
        assert cm.status == SourceStatus.unavailable and "authorized media source" in cm.detail


def test_editor_unlocks_media_and_vod_clips() -> None:
    with session_scope() as db:
        _acct(db, TwitchRole.operator, ["clips:edit", "user:read:chat"], "1")
        assert not twitch_service.capabilities(db).can_download_clips
        _acct(db, TwitchRole.editor, ["clips:edit", "user:read:chat", "editor:manage:clips"], "2")
        caps = twitch_service.capabilities(db)
        assert caps.can_download_clips and caps.can_vod_clip and caps.can_read_chat
        acct = twitch_service.best_account(db, needs="download")
        acct.is_editor_verified = False  # Twitch said 403 "not an editor"
        assert not twitch_service.capabilities(db).can_download_clips


def test_full_av_mode_requires_a_live_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("LIVE_INGEST_URL", "rtmp://ingest.invalid/live/key")
    get_settings.cache_clear()
    monkeypatch.setattr(registry, "probe_url", lambda url: (False, "connection refused"))
    with session_scope() as db:
        _acct(db, TwitchRole.operator, ["clips:edit", "user:read:chat"], "1")
        assert registry.probe_all(db, live=True)["analysis_mode"] == "chat_only"
    monkeypatch.setattr(registry, "probe_url", lambda url: (True, "video,1920,1080"))
    with session_scope() as db:
        rep = registry.probe_all(db, live=True)
        assert rep["analysis_mode"] == "full_av" and {"video", "audio", "speech"} <= set(rep["signals"])


@pytest.mark.media
def test_rolling_buffer_segments_prunes_and_extracts(tmp_path: Path) -> None:
    src = make_video(tmp_path / "feed.mp4", seconds=14)
    buf = RollingBuffer(str(src), tmp_path / "buf", segment_seconds=2, buffer_seconds=6)
    buf.start()
    deadline = time.time() + 60
    while buf.alive() and time.time() < deadline:
        time.sleep(0.2)
    segs = buf.segments()
    assert len(segs) >= 6 and buf.head() == pytest.approx(14, abs=1.5)
    buf.pin(9.0, 13.0)
    removed = buf.prune()
    remaining = buf.segments()
    assert removed > 0 and remaining[0].start >= 14 - 6 - 4  # old segments deleted (bounded buffer)
    out = buf.extract(9.0, 13.0, tmp_path / "cut.mp4")
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(out)], capture_output=True, text=True).stdout)
    assert abs(dur - 4.0) < 0.3
    with pytest.raises(LookupError):
        buf.extract(0.0, 3.0, tmp_path / "gone.mp4")  # rolled off: never permanently recorded
    buf.stop(cleanup=True)
    assert not (tmp_path / "buf").exists()
