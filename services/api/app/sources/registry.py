"""Probe every video source and persist an honest capability report.

The analysis mode tells the UI exactly what the system can see:
* ``full_av``   — authorized live feed active: audio, video, speech and (if connected) chat.
* ``chat_only`` — no video signal; only chat velocity can *nominate* moments, which must then be verified
                  on authorized clip media before they are accepted.
* ``none``      — nothing to analyse (status monitoring only).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import utcnow
from app.models import SourceKind, SourceStatus, VideoSource
from app.services import twitch_service
from app.sources.live_ingest import probe_url


def _upsert(db: Session, kind: SourceKind, status: SourceStatus, detail: str, caps: dict[str, Any]) -> None:
    row = db.scalar(select(VideoSource).where(VideoSource.kind == kind))
    if row is None:
        row = VideoSource(kind=kind)
        db.add(row)
    row.status, row.detail, row.capabilities, row.last_checked_at = status, detail, caps, utcnow()


def probe_all(db: Session, *, live: bool, probe_ingest: bool = True) -> dict[str, Any]:
    caps = twitch_service.capabilities(db)
    s = get_settings()

    _upsert(db, SourceKind.twitch_native,
            SourceStatus.available if caps.can_create_clips else SourceStatus.unavailable,
            "Create Clip (clips:edit) — captures around the request time; no media access by itself."
            if caps.can_create_clips else "Connect a Twitch account with clips:edit.",
            {"clip_window": "90s ending now", "duration_range": [5, 60], "media": False})
    _upsert(db, SourceKind.vod_clip,
            SourceStatus.available if caps.can_vod_clip else SourceStatus.unavailable,
            "Create Clip From VOD — precise start/end on the in-progress VOD."
            if caps.can_vod_clip else "Requires the broadcaster or a channel editor to authorize "
            "editor:manage:clips / channel:manage:clips.",
            {"precise": True, "duration_range": [5, 60]})
    _upsert(db, SourceKind.clip_media,
            SourceStatus.available if caps.can_download_clips else SourceStatus.unavailable,
            "Get Clips Download — official clip MP4 URLs." if caps.can_download_clips else
            "Clip media cannot be retrieved: Get Clips Download requires broadcaster/editor authorization. "
            "Provide an authorized media source.",
            {"media": caps.can_download_clips})

    ingest_ok, ingest_detail = False, "LIVE_INGEST_URL not configured"
    if s.live_ingest_url:
        if live and probe_ingest:
            ingest_ok, ingest_detail = probe_url(s.live_ingest_url)
            ingest_detail = ("receiving: " + ingest_detail) if ingest_ok else ("no feed: " + ingest_detail)
        else:
            ingest_detail = "configured; probed when the stream is live"
    _upsert(db, SourceKind.live_ingest,
            SourceStatus.active if ingest_ok else (SourceStatus.available if s.live_ingest_url else
                                                   SourceStatus.unavailable),
            ingest_detail, {"signals": ["video", "audio", "speech"], "rolling_buffer_s": s.buffer_seconds})

    if ingest_ok:
        mode = "full_av"
    elif caps.can_read_chat and caps.can_create_clips:
        mode = "chat_only"
    else:
        mode = "none"
    signals = []
    if ingest_ok:
        signals += ["video", "audio", "speech"]
    if caps.can_read_chat:
        signals.append("chat")
    return {"analysis_mode": mode, "signals": signals, "twitch": caps.as_dict(),
            "ingest": {"ok": ingest_ok, "detail": ingest_detail}}
