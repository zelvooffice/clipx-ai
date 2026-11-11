"""Stream session lifecycle (LIVE / OFFLINE)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import CandidateStatus, Clip, HighlightCandidate, SessionStatus, StreamSession
from app.services import jobs
from app.services.audit import syslog
from app.sources import registry


def live_session(db: Session) -> StreamSession | None:
    return db.scalar(select(StreamSession).where(StreamSession.status == SessionStatus.live)
                     .order_by(StreamSession.started_at.desc()))


def _parse(ts: str | None) -> datetime:
    if not ts:
        return utcnow()
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def handle_online(db: Session, *, broadcaster_id: str, stream_id: str, started_at: str | None,
                  title: str | None, game: str | None, detected_via: str, probe_ingest: bool = True
                  ) -> tuple[StreamSession, bool]:
    """Idempotent: returns (session, created)."""
    existing = db.scalar(select(StreamSession).where(StreamSession.twitch_stream_id == stream_id))
    if existing:
        if title and not existing.title:
            existing.title = title
        return existing, False
    # Close any stale live session (missed offline event).
    for stale in db.scalars(select(StreamSession).where(StreamSession.status == SessionStatus.live)).all():
        handle_offline(db, session=stale, reason="superseded by a new stream")
    report = registry.probe_all(db, live=True, probe_ingest=probe_ingest)
    sess = StreamSession(broadcaster_id=broadcaster_id, twitch_stream_id=stream_id, title=title, game_name=game,
                         started_at=_parse(started_at), status=SessionStatus.live, detected_via=detected_via,
                         analysis_mode=report["analysis_mode"], capabilities=report,
                         stats={"candidates": 0, "accepted": 0, "rejected": 0, "clips": 0})
    db.add(sess)
    db.flush()
    syslog(db, "INFO", "live", "stream_online",
           f"Stream went live ({detected_via}); analysis mode: {report['analysis_mode']}", session_id=sess.id,
           signals=report["signals"])
    return sess, True


def handle_offline(db: Session, *, session: StreamSession | None = None, reason: str = "stream.offline"
                   ) -> StreamSession | None:
    sess = session or live_session(db)
    if sess is None or sess.status != SessionStatus.live:
        return None
    sess.status = SessionStatus.finalizing
    sess.ended_at = utcnow()
    syslog(db, "INFO", "live", "stream_offline", f"Stream ended ({reason}); finalizing", session_id=sess.id)
    jobs.enqueue(db, "finalize_session", f"finalize:{sess.id}", ref_id=str(sess.id),
                 payload={"session_id": str(sess.id)}, delay_s=30, max_attempts=20)
    return sess


def refresh_stats(db: Session, sess: StreamSession) -> dict[str, Any]:
    cands = db.scalars(select(HighlightCandidate).where(HighlightCandidate.session_id == sess.id)).all()
    clips = db.scalars(select(Clip).where(Clip.session_id == sess.id)).all()
    lat = [c.latency_ms for c in cands if c.latency_ms is not None]
    stats = {
        "candidates": len(cands),
        "accepted": sum(c.status in (CandidateStatus.accepted, CandidateStatus.clipped) for c in cands),
        "provisional": sum(c.status == CandidateStatus.provisional for c in cands),
        "rejected": sum(c.status == CandidateStatus.rejected for c in cands),
        "duplicates": sum(c.status == CandidateStatus.duplicate for c in cands),
        "clips": len(clips),
        "rendered": sum(c.edit_status.value == "rendered" for c in clips),
        "avg_detection_latency_ms": int(sum(lat) / len(lat)) if lat else None,
    }
    sess.stats = stats
    return stats
