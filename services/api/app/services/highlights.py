"""Persist highlight decisions and kick off the clip pipeline."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.detection.engine import MomentResult
from app.detection.scoring import dedupe_key
from app.models import (
    CandidateStatus,
    Clip,
    EditStatus,
    HighlightCandidate,
    MediaSource,
    StreamSession,
    Verification,
)
from app.services import jobs
from app.services.storage import get_storage

STATUS = {"accepted": CandidateStatus.accepted, "provisional": CandidateStatus.provisional,
          "rejected": CandidateStatus.rejected, "duplicate": CandidateStatus.duplicate}


def existing_ranges(db: Session, session_id: object) -> list[tuple[float, float, str]]:
    rows = db.scalars(select(HighlightCandidate).where(
        HighlightCandidate.session_id == session_id,
        HighlightCandidate.status.in_([CandidateStatus.accepted, CandidateStatus.clipped,
                                       CandidateStatus.provisional]))).all()
    return [(r.start_offset_s, r.end_offset_s, r.transcript_excerpt or "") for r in rows]


def accepted_count(db: Session, session_id: object) -> int:
    return len(db.scalars(select(HighlightCandidate.id).where(
        HighlightCandidate.session_id == session_id,
        HighlightCandidate.status.in_([CandidateStatus.accepted, CandidateStatus.clipped]))).all())


def save_candidate(db: Session, sess: StreamSession, r: MomentResult) -> HighlightCandidate | None:
    cand = HighlightCandidate(
        session_id=sess.id, peak_offset_s=r.peak, start_offset_s=r.start, end_offset_s=r.end,
        category=r.verdict.category, title=r.verdict.title, explanation=r.verdict.explanation,
        context_summary=r.verdict.context_summary, quality_score=r.score.quality, confidence=r.score.confidence,
        component_scores={**r.components.as_dict(), "_signals": r.signals,
                          "_supporting_families": r.score.supporting_families,
                          "_coverage": r.score.coverage},
        signals_used=r.score.available, transcript_excerpt=r.transcript_excerpt or None,
        analyzer=r.verdict.analyzer, status=STATUS[r.status], reject_reason=r.reason,
        dedupe_key=dedupe_key(r.peak), latency_ms=r.latency_ms,
    )
    try:
        with db.begin_nested():
            db.add(cand)
            db.flush()
    except IntegrityError:
        return None  # same peak bucket already evaluated (idempotent re-analysis)
    return cand


def create_clip_from_buffer(db: Session, sess: StreamSession, cand: HighlightCandidate, media: Path,
                            *, mirror_twitch: bool) -> Clip:
    """Accepted candidate with media cut from the authorized live feed buffer."""
    clip = Clip(session_id=sess.id, candidate_id=cand.id, title=cand.title or "Lacy moment",
                category=cand.category, quality_score=cand.quality_score, confidence=cand.confidence,
                verification=Verification.verified, start_offset_s=cand.start_offset_s,
                duration_s=round(cand.end_offset_s - cand.start_offset_s, 2), media_source=MediaSource.live_buffer,
                rights_basis="Authorized live feed pushed by the channel to the CLIPX ingest",
                edit_status=EditStatus.queued)
    db.add(clip)
    db.flush()
    key = f"clips/{clip.id}/source.mp4"
    get_storage().put_file(key, media, "video/mp4")  # object first, then the DB pointer
    clip.source_media_key = key
    cand.status = CandidateStatus.clipped
    jobs.enqueue(db, "transcribe_clip", f"transcribe:{clip.id}:v1", ref_id=str(clip.id),
                 payload={"clip_id": str(clip.id)})
    if mirror_twitch:
        jobs.enqueue(db, "create_twitch_clip", f"twitchclip:{clip.id}", ref_id=str(clip.id),
                     payload={"clip_id": str(clip.id)}, max_attempts=3)
    return clip


def create_provisional_clip(db: Session, sess: StreamSession, cand: HighlightCandidate) -> Clip:
    """Chat-nominated moment: create a native Twitch clip, then verify on its media (if obtainable)."""
    clip = Clip(session_id=sess.id, candidate_id=cand.id, title=cand.title or "Chat-nominated moment",
                category=cand.category, quality_score=cand.quality_score, confidence=cand.confidence,
                verification=Verification.unverified, start_offset_s=cand.start_offset_s,
                duration_s=round(cand.end_offset_s - cand.start_offset_s, 2), media_source=MediaSource.none,
                edit_status=EditStatus.needs_media)
    db.add(clip)
    db.flush()
    jobs.enqueue(db, "create_twitch_clip", f"twitchclip:{clip.id}", ref_id=str(clip.id),
                 payload={"clip_id": str(clip.id), "capture_now": True}, max_attempts=3)
    return clip
