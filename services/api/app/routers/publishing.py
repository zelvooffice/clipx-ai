from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import service as analytics
from app.db import get_db, utcnow
from app.models import Clip, PublishingHistory, PublishingJob, PublishMode, PublishStatus, ViralEdit, ViralSourceVideo
from app.publishing import policy, scheduler
from app.routers import serialize as ser
from app.security.auth import CurrentUser, get_current_user
from app.services import app_settings, jobs, tiktok_service
from app.services.audit import audit
from app.viral import templates

router = APIRouter(prefix="/v1")


# ------------------------------------------------------------------------------------- publishing
@router.get("/publishing/overview")
def publishing_overview(db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    acct = tiktok_service.account(db)
    cfg = app_settings.get_section(db, "publishing")
    cap = policy.capacity(db, cfg, acct.id) if acct else None
    return {"account": ser.tiktok_account(acct), "settings": cfg.model_dump(),
            "capacity": {**cap, "next_allowed_at": ser.iso(cap["next_allowed_at"])} if cap else None,
            "compliance": {
                "direct_post_requires_confirmation": policy.direct_post_requires_confirmation(cfg),
                "consent_text": policy.CONSENT_TEXT, "consent_text_branded": policy.CONSENT_TEXT_BRANDED,
                "inbox_automation_consent": policy.INBOX_AUTOMATION_CONSENT,
                "unaudited_restriction": None if cfg.client_audited else
                "TikTok app not audited: Direct Post is limited to SELF_ONLY (private) and private accounts; "
                "max 5 posting users per 24 h."}}


@router.get("/publishing/queue")
def queue(db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)) -> list[dict[str, Any]]:
    rows = db.scalars(select(PublishingJob).where(PublishingJob.status.in_([
        PublishStatus.awaiting_confirmation, PublishStatus.scheduled, PublishStatus.uploading,
        PublishStatus.processing])).order_by(PublishingJob.scheduled_for.asc().nullsfirst())).all()
    return [ser.publishing_job(j) for j in rows]


@router.get("/publishing/history")
def pub_history(db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    done = db.scalars(select(PublishingJob).where(PublishingJob.status.in_([
        PublishStatus.published, PublishStatus.sent_to_inbox, PublishStatus.failed, PublishStatus.cancelled]))
        .order_by(PublishingJob.updated_at.desc()).limit(100)).all()
    events = db.scalars(select(PublishingHistory).order_by(PublishingHistory.at.desc()).limit(200)).all()
    return {"jobs": [ser.publishing_job(j) for j in done], "events": [ser.history(e) for e in events]}


@router.post("/publishing/creator-info/refresh")
async def creator_info(db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    acct = tiktok_service.account(db)
    if acct is None:
        raise HTTPException(409, "no TikTok account connected")
    try:
        info = await tiktok_service.refresh_creator_info(db, acct)
    except tiktok_service.NoTikTokAccount as exc:
        raise HTTPException(409, str(exc)) from exc
    return info


class QueueIn(BaseModel):
    mode: Literal["direct_post", "inbox_draft"]
    scheduled_for: datetime | None = None


@router.post("/publishing/clips/{clip_id}/queue")
def queue_clip(clip_id: uuid.UUID, body: QueueIn, db: Session = Depends(get_db),
               user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(404)
    acct = tiktok_service.account(db)
    if acct is None:
        raise HTTPException(409, "connect a TikTok account first")
    cfg = app_settings.get_section(db, "publishing")
    elig = policy.clip_eligibility(db, clip, cfg, account_id=acct.id)
    if not elig.eligible:
        raise HTTPException(409, "; ".join(elig.reasons))
    if body.scheduled_for:
        slot, basis = body.scheduled_for, "chosen manually"
    else:
        slot, basis = scheduler.next_slot(db, cfg, acct.id)
    job = tiktok_service.prepare(db, clip, mode=PublishMode(body.mode), scheduled_for=slot, schedule_basis=basis)
    if job is None:
        raise HTTPException(409, "clip could not be queued")
    if body.mode == "inbox_draft" and job.confirmed_by is None:
        job.confirmed_by, job.consent_at = user.email, utcnow()  # explicit manual consent for this upload
    audit(db, user.email, "publishing.queue", str(clip.id), user.ip, mode=body.mode)
    return ser.publishing_job(job)


class ConfirmIn(BaseModel):
    privacy_level: str = Field(min_length=3, max_length=40)
    allow_comment: bool = False
    allow_duet: bool = False
    allow_stitch: bool = False
    disclosure_on: bool = False
    brand_content: bool = False
    brand_organic: bool = False
    consent: bool
    title: str = Field(max_length=2200)
    scheduled_for: datetime | None = None


@router.get("/publishing/jobs/{job_id}/confirmation")
async def confirmation_card(job_id: uuid.UUID, db: Session = Depends(get_db),
                            _: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Everything the TikTok-compliant confirmation card must show, with *fresh* creator info."""
    job = db.get(PublishingJob, job_id)
    if job is None:
        raise HTTPException(404)
    acct = tiktok_service.account(db)
    if acct is None:
        raise HTTPException(409, "no TikTok account connected")
    try:
        info = await tiktok_service.refresh_creator_info(db, acct)
    except tiktok_service.NoTikTokAccount as exc:
        raise HTTPException(409, str(exc)) from exc
    cfg = app_settings.get_section(db, "publishing")
    max_d = info.get("max_video_post_duration_sec")
    return {"job": ser.publishing_job(job), "creator": {
                "nickname": info.get("creator_nickname"), "username": info.get("creator_username"),
                "avatar_url": info.get("creator_avatar_url"),
                "privacy_level_options": info.get("privacy_level_options") or [],
                "comment_disabled": info.get("comment_disabled"), "duet_disabled": info.get("duet_disabled"),
                "stitch_disabled": info.get("stitch_disabled"), "max_video_post_duration_sec": max_d},
            "duration_ok": not max_d or (job.clip.duration_s or 0) <= max_d,
            "client_audited": cfg.client_audited, "consent_text": policy.CONSENT_TEXT,
            "consent_text_branded": policy.CONSENT_TEXT_BRANDED,
            "processing_notice": "After publishing, it may take a few minutes for the video to process and appear "
                                 "on the TikTok profile."}


@router.post("/publishing/jobs/{job_id}/confirm")
def confirm(job_id: uuid.UUID, body: ConfirmIn, db: Session = Depends(get_db),
            user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    job = db.get(PublishingJob, job_id)
    if job is None:
        raise HTTPException(404)
    errors = tiktok_service.confirm_direct_post(
        db, job, actor=user.email, privacy_level=body.privacy_level, allow_comment=body.allow_comment,
        allow_duet=body.allow_duet, allow_stitch=body.allow_stitch, disclosure_on=body.disclosure_on,
        brand_content=body.brand_content, brand_organic=body.brand_organic, consent=body.consent,
        title=body.title, scheduled_for=body.scheduled_for)
    if errors:
        raise HTTPException(422, {"errors": errors})
    audit(db, user.email, "publishing.confirm", str(job.id), user.ip, privacy=body.privacy_level)
    return ser.publishing_job(job)


@router.post("/publishing/jobs/{job_id}/cancel")
def cancel(job_id: uuid.UUID, db: Session = Depends(get_db),
           user: CurrentUser = Depends(get_current_user)) -> dict[str, str]:
    job = db.get(PublishingJob, job_id)
    if job is None:
        raise HTTPException(404)
    if job.status not in (PublishStatus.awaiting_confirmation, PublishStatus.scheduled):
        raise HTTPException(409, f"cannot cancel a job that is {job.status.value}")
    job.status = PublishStatus.cancelled
    tiktok_service.history(db, job, "cancelled", by=user.email)
    audit(db, user.email, "publishing.cancel", str(job.id), user.ip)
    return {"status": "cancelled"}


@router.get("/publishing/recommendations")
def recommendations(db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    return scheduler.recommendations(db, app_settings.get_section(db, "publishing"))


# ------------------------------------------------------------------------------------- analytics
@router.get("/analytics/summary")
def analytics_summary(db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    s = analytics.summary(db)
    s["last_synced"] = ser.iso(s["last_synced"])
    s["posting_times"] = scheduler.recommendations(db, app_settings.get_section(db, "publishing"))
    return s


@router.post("/analytics/sync")
def analytics_sync(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)) -> dict[str, str]:
    jid = jobs.enqueue(db, "sync_analytics", f"sync_analytics:manual:{int(utcnow().timestamp() // 60)}",
                       max_attempts=2)
    audit(db, user.email, "analytics.sync", None, user.ip)
    return {"status": "queued", "job_id": str(jid)}


# ------------------------------------------------------------------------------------- viral edits
@router.get("/viral/sources")
def viral_sources(platform: str | None = None, db: Session = Depends(get_db),
                  _: CurrentUser = Depends(get_current_user)) -> list[dict[str, Any]]:
    q = select(ViralSourceVideo).order_by(ViralSourceVideo.popularity_score.desc()).limit(100)
    if platform:
        q = q.where(ViralSourceVideo.platform == platform)
    return [ser.viral_source(v) for v in db.scalars(q).all()]


@router.post("/viral/discover")
def viral_discover(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)) -> dict[str, str]:
    jid = jobs.enqueue(db, "discover_viral", f"discover_viral:manual:{int(utcnow().timestamp() // 60)}",
                       max_attempts=2)
    audit(db, user.email, "viral.discover", None, user.ip)
    return {"status": "queued", "job_id": str(jid)}


@router.get("/viral/templates")
def viral_templates(db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)) -> list[dict[str, Any]]:
    return templates.suggest(db)


@router.get("/viral/edits")
def viral_edits(db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)) -> list[dict[str, Any]]:
    rows = db.scalars(select(ViralEdit).order_by(ViralEdit.created_at.desc()).limit(100)).all()
    return [ser.viral_edit(e, db.get(Clip, e.output_clip_id) if e.output_clip_id else None) for e in rows]


class ViralEditIn(BaseModel):
    template: str
    clip_ids: list[uuid.UUID] = Field(min_length=1, max_length=5)
    text: str = Field("", max_length=120)
    viral_source_id: uuid.UUID | None = None


@router.post("/viral/edits")
def create_viral_edit(body: ViralEditIn, db: Session = Depends(get_db),
                      user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    try:
        edit = templates.create_edit(db, body.template, body.clip_ids, body.text, actor=user.email,
                                     viral_source_id=body.viral_source_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, user.email, "viral.create", str(edit.id), user.ip, template=body.template)
    return ser.viral_edit(edit, None)
