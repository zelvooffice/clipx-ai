from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    CandidateStatus,
    Clip,
    ClipOrigin,
    EditConfig,
    EditStatus,
    PublishingJob,
    SubtitleTrack,
    Verification,
)
from app.routers import serialize as ser
from app.security.auth import CurrentUser, get_current_user
from app.services import jobs
from app.services.audit import audit
from app.video.subtitles import DEFAULT_STYLE, Cue, to_srt

router = APIRouter(prefix="/v1/clips")


def _get(db: Session, clip_id: uuid.UUID) -> Clip:
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(404, "clip not found")
    return clip


@router.get("")
def list_clips(origin: ClipOrigin | None = None, status: EditStatus | None = None,
               verification: Verification | None = None, session_id: uuid.UUID | None = None,
               category: str | None = None, q: str | None = Query(None, max_length=100),
               limit: int = Query(30, le=100), offset: int = Query(0, ge=0),
               sort: Literal["new", "quality"] = "new",
               db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    stmt = select(Clip)
    if origin:
        stmt = stmt.where(Clip.origin == origin)
    if status:
        stmt = stmt.where(Clip.edit_status == status)
    if verification:
        stmt = stmt.where(Clip.verification == verification)
    if session_id:
        stmt = stmt.where(Clip.session_id == session_id)
    if category:
        stmt = stmt.where(Clip.category == category)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Clip.title.ilike(like), Clip.caption.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    order = Clip.quality_score.desc().nullslast() if sort == "quality" else Clip.created_at.desc()
    rows = db.scalars(stmt.order_by(order).limit(limit).offset(offset)).all()
    return {"total": total, "items": [ser.clip(c) for c in rows]}


@router.get("/{clip_id}")
def clip_detail(clip_id: uuid.UUID, db: Session = Depends(get_db),
                _: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    clip = _get(db, clip_id)
    ec = db.scalar(select(EditConfig).where(EditConfig.clip_id == clip.id))
    track = db.scalar(select(SubtitleTrack).where(SubtitleTrack.clip_id == clip.id, SubtitleTrack.is_active.is_(True)))
    pubs = db.scalars(select(PublishingJob).where(PublishingJob.clip_id == clip.id)
                      .order_by(PublishingJob.created_at.desc())).all()
    pj = db.scalars(select(jobs.ProcessingJob).where(jobs.ProcessingJob.ref_id == str(clip.id))
                    .order_by(jobs.ProcessingJob.created_at)).all()
    return {
        **ser.clip(clip, detail=True),
        "edit_config": None if ec is None else {
            "layout": ec.layout, "zoom_intensity": ec.zoom_intensity, "sfx_enabled": ec.sfx_enabled,
            "subtitle_style": {**DEFAULT_STYLE, **(ec.subtitle_style or {})}, "crop_override": ec.crop_override,
            "trim_start_s": ec.trim_start_s, "trim_end_s": ec.trim_end_s, "version": ec.version, "plan": ec.plan},
        "subtitles": None if track is None else {
            "id": str(track.id), "engine": track.engine, "language": track.language, "edited": track.edited,
            "version": track.version, "cues": track.cues, "word_count": len(track.words or [])},
        "publishing": [{"id": str(p.id), "status": p.status.value, "mode": p.mode.value,
                        "scheduled_for": ser.iso(p.scheduled_for), "fail_reason": p.fail_reason} for p in pubs],
        "processing": [{"kind": j.kind, "status": j.status.value, "attempts": j.attempts, "error": j.error,
                        "updated_at": ser.iso(j.updated_at)} for j in pj],
    }


class ClipPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    caption: str | None = Field(None, max_length=2000)
    hashtags: list[str] | None = Field(None, max_length=8)


@router.patch("/{clip_id}")
def patch_clip(clip_id: uuid.UUID, body: ClipPatch, db: Session = Depends(get_db),
               user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    from app.captions.generator import normalize_tags

    clip = _get(db, clip_id)
    if body.title is not None:
        clip.title = body.title
    if body.caption is not None:
        clip.caption, clip.caption_source = body.caption.strip(), "edited"
    if body.hashtags is not None:
        clip.hashtags = normalize_tags(body.hashtags, game=None)
    audit(db, user.email, "clip.update", str(clip.id), user.ip, fields=body.model_dump(exclude_none=True))
    return ser.clip(clip)


class EditConfigIn(BaseModel):
    layout: Literal["auto", "track", "stacked", "fit"] | None = None
    zoom_intensity: float | None = Field(None, ge=0, le=1.5)
    sfx_enabled: bool | None = None
    subtitle_style: dict[str, Any] | None = None
    crop_x_center: float | None = Field(None, ge=0, le=1)
    clear_crop: bool = False
    trim_start_s: float | None = Field(None, ge=0)
    trim_end_s: float | None = Field(None, gt=0)


ALLOWED_STYLE = {"size": (40, 140), "outline": (0, 14), "shadow": (0, 10), "max_chars": (8, 40),
                 "max_words": (1, 8)}


@router.put("/{clip_id}/edit-config")
def put_edit_config(clip_id: uuid.UUID, body: EditConfigIn, db: Session = Depends(get_db),
                    user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    clip = _get(db, clip_id)
    ec = db.scalar(select(EditConfig).where(EditConfig.clip_id == clip.id))
    if ec is None:
        ec = EditConfig(clip_id=clip.id, subtitle_style=dict(DEFAULT_STYLE))
        db.add(ec)
    if body.layout:
        ec.layout = body.layout
    if body.zoom_intensity is not None:
        ec.zoom_intensity = body.zoom_intensity
    if body.sfx_enabled is not None:
        ec.sfx_enabled = body.sfx_enabled
    if body.subtitle_style:
        style = dict(ec.subtitle_style or DEFAULT_STYLE)
        for k, v in body.subtitle_style.items():
            if k in ALLOWED_STYLE:
                lo, hi = ALLOWED_STYLE[k]
                if not isinstance(v, (int, float)) or not lo <= v <= hi:
                    raise HTTPException(422, f"{k} must be between {lo} and {hi}")
                style[k] = v
            elif k in ("primary", "highlight"):
                if not isinstance(v, str) or not (len(v) == 7 and v.startswith("#")):
                    raise HTTPException(422, f"{k} must be a #RRGGBB colour")
                style[k] = v
            elif k in ("uppercase", "emphasis"):
                style[k] = bool(v)
            else:
                raise HTTPException(422, f"unknown subtitle style field {k}")
        ec.subtitle_style = style
    if body.clear_crop:
        ec.crop_override = None
    elif body.crop_x_center is not None:
        ec.crop_override = {"x_center": body.crop_x_center}
    if body.trim_start_s is not None:
        ec.trim_start_s = body.trim_start_s
    if body.trim_end_s is not None:
        if body.trim_end_s <= ec.trim_start_s + 3:
            raise HTTPException(422, "trim end must be at least 3 s after trim start")
        ec.trim_end_s = body.trim_end_s
    audit(db, user.email, "clip.edit_config", str(clip.id), user.ip, **body.model_dump(exclude_none=True))
    return {"status": "saved"}


class CueIn(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(min_length=1, max_length=80)
    zone: Literal["lower", "middle", "upper"] = "lower"
    emphasis: list[int] = Field(default_factory=list)


class SubtitlesIn(BaseModel):
    cues: list[CueIn] = Field(max_length=400)


@router.put("/{clip_id}/subtitles")
def put_subtitles(clip_id: uuid.UUID, body: SubtitlesIn, db: Session = Depends(get_db),
                  user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    clip = _get(db, clip_id)
    track = db.scalar(select(SubtitleTrack).where(SubtitleTrack.clip_id == clip.id, SubtitleTrack.is_active.is_(True)))
    if track is None:
        raise HTTPException(409, "no subtitle track yet (clip not transcribed)")
    cues = []
    prev_end = 0.0
    for c in sorted(body.cues, key=lambda x: x.start):
        if c.end <= c.start:
            raise HTTPException(422, f"cue '{c.text}' ends before it starts")
        if c.start < prev_end - 0.01:
            raise HTTPException(422, f"cue '{c.text}' overlaps the previous cue")
        prev_end = c.end
        words = c.text.split()
        step = (c.end - c.start) / max(1, len(words))
        cues.append(Cue(c.start, c.end, [{"w": w, "start": round(c.start + i * step, 3),
                                          "end": round(c.start + (i + 1) * step, 3), "emph": i in c.emphasis}
                                         for i, w in enumerate(words)], c.zone).as_dict())
    track.cues, track.edited, track.version = cues, True, track.version + 1
    audit(db, user.email, "clip.subtitles.edit", str(clip.id), user.ip, cues=len(cues))
    return {"status": "saved", "version": track.version}


@router.get("/{clip_id}/subtitles.srt")
def subtitles_srt(clip_id: uuid.UUID, db: Session = Depends(get_db),
                  _: CurrentUser = Depends(get_current_user)) -> dict[str, str]:
    track = db.scalar(select(SubtitleTrack).where(SubtitleTrack.clip_id == clip_id, SubtitleTrack.is_active.is_(True)))
    if track is None:
        raise HTTPException(404)
    return {"srt": to_srt([Cue.from_dict(c) for c in track.cues or []])}


@router.post("/{clip_id}/render")
def rerender(clip_id: uuid.UUID, db: Session = Depends(get_db),
             user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    clip = _get(db, clip_id)
    if not clip.source_media_key:
        raise HTTPException(409, "this clip has no source media to render")
    if clip.edit_status in (EditStatus.rendering, EditStatus.transcribing):
        raise HTTPException(409, "a render is already running")
    ec = db.scalar(select(EditConfig).where(EditConfig.clip_id == clip.id))
    if ec is None:
        ec = EditConfig(clip_id=clip.id, subtitle_style=dict(DEFAULT_STYLE))
        db.add(ec)
        db.flush()
    ec.version += 1
    clip.edit_status = EditStatus.queued
    job_id = jobs.enqueue(db, "render_clip", f"render:{clip.id}:v{ec.version}", ref_id=str(clip.id),
                          payload={"clip_id": str(clip.id)})
    audit(db, user.email, "clip.render", str(clip.id), user.ip, version=ec.version)
    return {"status": "queued", "version": ec.version, "job_id": str(job_id)}


@router.post("/{clip_id}/reject")
def reject(clip_id: uuid.UUID, db: Session = Depends(get_db),
           user: CurrentUser = Depends(get_current_user)) -> dict[str, str]:
    clip = _get(db, clip_id)
    clip.edit_status = EditStatus.rejected
    clip.edit_error = f"rejected by {user.email}"
    if clip.candidate:
        clip.candidate.status = CandidateStatus.rejected
        clip.candidate.reject_reason = "rejected manually"
    audit(db, user.email, "clip.reject", str(clip.id), user.ip)
    return {"status": "rejected"}
