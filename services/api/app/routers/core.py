from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db, utcnow
from app.models import (
    AuditLog,
    AuthorizedEmail,
    CandidateStatus,
    Clip,
    ClipOrigin,
    EditStatus,
    HighlightCandidate,
    JobStatus,
    ProcessingJob,
    PublishingJob,
    PublishStatus,
    StreamSession,
    SystemLog,
    VideoSource,
)
from app.routers import serialize as ser
from app.security.auth import CurrentUser, get_current_user
from app.services import app_settings, streams, twitch_service
from app.services.audit import audit
from app.services.redis_client import get_redis
from app.services.storage import LocalStorage, get_storage

public = APIRouter()
router = APIRouter(prefix="/v1")


# ------------------------------------------------------------------------------------- health
@public.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@public.get("/readyz")
def readyz(db: Session = Depends(get_db)) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    try:
        db.execute(text("select 1"))
        checks["database"] = "ok"
    except Exception as exc:  # pragma: no cover - surfaced in response
        checks["database"] = f"error: {type(exc).__name__}"
    try:
        get_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {type(exc).__name__}"
    ok = all(v == "ok" for v in checks.values())
    if not ok:
        raise HTTPException(503, detail=checks)
    return {"status": "ready", **checks}


# ------------------------------------------------------------------------------------- identity
@router.get("/me")
def me(user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    return {"email": user.email, "via": user.via}


class EmailIn(BaseModel):
    email: EmailStr


@router.get("/settings/authorized-emails")
def list_emails(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    rows = db.scalars(select(AuthorizedEmail).order_by(AuthorizedEmail.created_at)).all()
    return {"bootstrap": sorted(get_settings().authorized_email_set),
            "managed": [{"id": str(r.id), "email": r.email, "active": r.active, "added_by": r.added_by,
                         "created_at": ser.iso(r.created_at)} for r in rows],
            "note": "Cloudflare Access must allow the same emails; this list is enforced again by the API."}


@router.post("/settings/authorized-emails")
def add_email(body: EmailIn, request: Request, db: Session = Depends(get_db),
              user: CurrentUser = Depends(get_current_user)) -> dict[str, str]:
    email = body.email.lower()
    row = db.scalar(select(AuthorizedEmail).where(AuthorizedEmail.email == email))
    if row is None:
        db.add(AuthorizedEmail(email=email, added_by=user.email))
    else:
        row.active = True
    audit(db, user.email, "authorized_email.add", email, user.ip)
    return {"status": "added"}


@router.delete("/settings/authorized-emails/{email_id}")
def remove_email(email_id: uuid.UUID, db: Session = Depends(get_db),
                 user: CurrentUser = Depends(get_current_user)) -> dict[str, str]:
    row = db.get(AuthorizedEmail, email_id)
    if row is None:
        raise HTTPException(404)
    if row.email == user.email:
        raise HTTPException(400, "you cannot remove your own access")
    row.active = False
    audit(db, user.email, "authorized_email.remove", row.email, user.ip)
    return {"status": "removed"}


# ------------------------------------------------------------------------------------- per-visit password
class UnlockIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


@router.get("/session")
def session_status(request: Request, user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    from app.security import unlock

    required = unlock.password_required()
    return {"email": user.email, "password_required": required,
            "unlocked": (not required) or unlock.verify(request.headers.get("x-clipx-unlock"), user.email),
            "attempts_left": unlock.attempts_left(user.email)}


@router.post("/session/unlock")
def session_unlock(body: UnlockIn, db: Session = Depends(get_db),
                   user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    from app.security import unlock
    from app.security.password import verify_password

    if not unlock.password_required():
        return {"token": None, "expires_at": None}
    if unlock.attempts_left(user.email) <= 0:
        audit(db, user.email, "session.unlock_blocked", None, user.ip)
        raise HTTPException(429, "Too many wrong passwords. Try again in 15 minutes.")
    if not verify_password(body.password, get_settings().dashboard_password_hash):
        left = unlock.record_failure(user.email)
        audit(db, user.email, "session.unlock_failed", None, user.ip, attempts_left=left)
        raise HTTPException(401, f"Wrong password. {left} attempt{'s' if left != 1 else ''} left.")
    unlock.clear_failures(user.email)
    token, exp = unlock.issue(user.email)
    audit(db, user.email, "session.unlocked", None, user.ip)
    return {"token": token, "expires_at": exp}


@router.post("/session/lock")
def session_lock(request: Request, db: Session = Depends(get_db),
                 user: CurrentUser = Depends(get_current_user)) -> dict[str, str]:
    from app.security import unlock

    unlock.revoke(request.headers.get("x-clipx-unlock"))
    audit(db, user.email, "session.locked", None, user.ip)
    return {"status": "locked"}


# ------------------------------------------------------------------------------------- overview
@router.get("/overview")
def overview(db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    live = streams.live_session(db)
    last = db.scalar(select(StreamSession).order_by(StreamSession.started_at.desc()))
    clips_total = db.scalar(select(func.count()).select_from(Clip)) or 0
    rendered = db.scalar(select(func.count()).select_from(Clip).where(Clip.edit_status == EditStatus.rendered)) or 0
    published = db.scalar(select(func.count()).select_from(PublishingJob).where(
        PublishingJob.status.in_([PublishStatus.published, PublishStatus.sent_to_inbox]))) or 0
    awaiting = db.scalar(select(func.count()).select_from(PublishingJob).where(
        PublishingJob.status == PublishStatus.awaiting_confirmation)) or 0
    active_jobs = db.scalar(select(func.count()).select_from(ProcessingJob).where(
        ProcessingJob.status.in_([JobStatus.queued, JobStatus.running]))) or 0
    dead_jobs = db.scalar(select(func.count()).select_from(ProcessingJob).where(
        ProcessingJob.status == JobStatus.dead, ProcessingJob.updated_at > utcnow() - timedelta(days=7))) or 0
    since = utcnow() - timedelta(days=14)
    per_day = db.execute(select(func.date_trunc("day", Clip.created_at).label("d"), func.count())
                         .where(Clip.created_at >= since).group_by("d").order_by("d")).all()
    pub_day = db.execute(select(func.date_trunc("day", PublishingJob.published_at).label("d"), func.count())
                         .where(PublishingJob.published_at >= since).group_by("d").order_by("d")).all()
    recent = db.scalars(select(SystemLog).order_by(SystemLog.at.desc()).limit(12)).all()
    return {
        "live": ser.session(live), "last_session": ser.session(last),
        "counts": {"clips": clips_total, "rendered": rendered, "published": published,
                   "awaiting_confirmation": awaiting, "active_jobs": active_jobs, "dead_jobs_7d": dead_jobs},
        "clips_per_day": [{"date": d.date().isoformat(), "clips": n} for d, n in per_day],
        "published_per_day": [{"date": d.date().isoformat(), "published": n} for d, n in pub_day],
        "activity": [{"at": ser.iso(r.at), "level": r.level, "component": r.component, "event": r.event,
                      "message": r.message} for r in recent],
        "capabilities": twitch_service.capabilities(db).as_dict(),
    }


# ------------------------------------------------------------------------------------- live monitor
@router.get("/live/status")
def live_status(db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    live = streams.live_session(db)
    monitor = get_redis().hgetall("monitor:status") or {}
    hb = monitor.get("heartbeat")
    monitor_alive = bool(hb and hb.isdigit() and utcnow().timestamp() - int(hb) < 180)
    sources = db.scalars(select(VideoSource).order_by(VideoSource.kind)).all()
    stage = "idle"
    if live:
        stage = {"full_av": "analysing authorized audio/video feed",
                 "chat_only": "monitoring chat (no video signal) — moments need media verification",
                 "none": "live, but no analysable signal is configured"}.get(live.analysis_mode, live.analysis_mode)
    return {"live": ser.session(live), "stage": stage, "monitor": {"alive": monitor_alive, **monitor},
            "sources": [ser.source(s) for s in sources], "capabilities": twitch_service.capabilities(db).as_dict()}


@router.get("/live/timeline")
def live_timeline(session_id: uuid.UUID | None = None, db: Session = Depends(get_db),
                  _: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    sess = db.get(StreamSession, session_id) if session_id else (
        streams.live_session(db) or db.scalar(select(StreamSession).order_by(StreamSession.started_at.desc())))
    if sess is None:
        return {"session": None, "events": [], "highlights": [], "latency_ms": {"avg": None, "max": None},
                "counts": {s.value: 0 for s in CandidateStatus}}
    events = db.scalars(select(SystemLog).where(SystemLog.session_id == sess.id).order_by(SystemLog.at.desc())
                        .limit(200)).all()
    cands = db.scalars(select(HighlightCandidate).where(HighlightCandidate.session_id == sess.id)
                       .order_by(HighlightCandidate.peak_offset_s.desc())).all()
    lat = [c.latency_ms for c in cands if c.latency_ms]
    return {"session": ser.session(sess),
            "events": [{"at": ser.iso(e.at), "level": e.level, "event": e.event, "message": e.message} for e in events],
            "highlights": [ser.candidate(c) for c in cands],
            "latency_ms": {"avg": int(sum(lat) / len(lat)) if lat else None, "max": max(lat) if lat else None},
            "counts": {s.value: sum(1 for c in cands if c.status == s) for s in CandidateStatus}}


@router.get("/live/sessions")
def sessions(db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)) -> list[dict[str, Any]]:
    rows = db.scalars(select(StreamSession).order_by(StreamSession.started_at.desc()).limit(50)).all()
    return [ser.session(r) for r in rows if r]  # type: ignore[misc]


@router.post("/sources/probe")
def probe_sources(request: Request, db: Session = Depends(get_db),
                  user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    from app.sources import registry

    report = registry.probe_all(db, live=streams.live_session(db) is not None)
    audit(db, user.email, "sources.probe", None, user.ip, mode=report["analysis_mode"])
    return report


# ------------------------------------------------------------------------------------- settings
@router.get("/settings")
def get_all_settings(db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    s = get_settings()
    return {
        "processing": app_settings.get_section(db, "processing").model_dump(),
        "publishing": app_settings.get_section(db, "publishing").model_dump(),
        "notifications": app_settings.get_section(db, "notifications").model_dump(),
        "system": {  # configuration status only — never secret values
            "environment": s.environment, "twitch_app_configured": s.twitch_configured,
            "twitch_target_login": s.twitch_target_login, "eventsub_transport": s.twitch_eventsub_transport,
            "tiktok_app_configured": s.tiktok_configured, "tiktok_scopes": s.tiktok_scopes.split(","),
            "tiktok_pull_from_url": bool(s.tiktok_pull_url_prefix), "llm_configured": s.llm_configured,
            "llm_model": s.llm_model, "whisper_model": s.whisper_model, "storage_backend": s.storage_backend,
            "live_ingest_configured": bool(s.live_ingest_url), "buffer_seconds": s.buffer_seconds,
            "access_jwt_direct": bool(s.cf_access_team_domain and s.cf_access_aud),
            "twitch_redirect_uri": s.twitch_redirect_uri, "tiktok_redirect_uri": s.tiktok_redirect_uri,
            "youtube_discovery": bool(s.youtube_api_key),
        },
    }


@router.put("/settings/{section}")
def put_settings(section: str, patch: dict[str, Any], db: Session = Depends(get_db),
                 user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    if section not in app_settings.DEFAULTS:
        raise HTTPException(404, "unknown settings section")
    patch = dict(patch)
    if section == "publishing":
        patch.pop("automation_consent_by", None)
        patch.pop("automation_consent_at", None)
        consent = patch.pop("automation_consent", None)
        current = app_settings.get_section(db, "publishing")
        enabling = patch.get("auto_publish", current.auto_publish)
        if enabling and not current.automation_consent_at and consent is not True:
            raise HTTPException(422, "Enabling automatic publishing requires explicit consent "
                                     "(automation_consent: true) from the account owner.")
        if consent is True:
            patch["automation_consent_by"], patch["automation_consent_at"] = user.email, utcnow().isoformat()
        if patch.get("auto_publish") is False:
            patch["automation_consent_by"], patch["automation_consent_at"] = None, None
    try:
        value = app_settings.update_section(db, section, patch, user.email)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, user.email, f"settings.{section}.update", None, user.ip, patch=patch)
    return value.model_dump()


@router.get("/audit")
def audit_log(limit: int = 100, db: Session = Depends(get_db),
              _: CurrentUser = Depends(get_current_user)) -> list[dict[str, Any]]:
    rows = db.scalars(select(AuditLog).order_by(AuditLog.at.desc()).limit(min(limit, 500))).all()
    return [{"at": ser.iso(r.at), "actor": r.actor, "action": r.action, "target": r.target, "detail": r.detail,
             "ip": r.ip} for r in rows]


@router.get("/jobs")
def jobs_list(status: str | None = None, db: Session = Depends(get_db),
              _: CurrentUser = Depends(get_current_user)) -> list[dict[str, Any]]:
    q = select(ProcessingJob).order_by(ProcessingJob.updated_at.desc()).limit(100)
    if status:
        q = q.where(ProcessingJob.status == JobStatus(status))
    return [{"id": str(j.id), "kind": j.kind, "ref_id": j.ref_id, "status": j.status.value, "attempts": j.attempts,
             "error": j.error, "updated_at": ser.iso(j.updated_at), "result": j.result} for j in db.scalars(q).all()]


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: uuid.UUID, db: Session = Depends(get_db),
              user: CurrentUser = Depends(get_current_user)) -> dict[str, str]:
    from app.services import jobs

    job = db.get(ProcessingJob, job_id)
    if job is None or job.status != JobStatus.dead:
        raise HTTPException(400, "only dead jobs can be retried")
    job.attempts = 0
    jobs.requeue(db, job)
    audit(db, user.email, "job.retry", str(job_id), user.ip, kind=job.kind)
    return {"status": "requeued"}


# ------------------------------------------------------------------------------------- media (local dev)
@router.get("/media/{key:path}")
def media(key: str, _: CurrentUser = Depends(get_current_user)) -> FileResponse:
    storage = get_storage()
    if not isinstance(storage, LocalStorage):
        raise HTTPException(404, "media is served from R2 presigned URLs")
    try:
        path = storage.local_path(key)
    except ValueError as exc:
        raise HTTPException(400, "invalid key") from exc
    if not path.exists():
        raise HTTPException(404)
    media_type = "video/mp4" if key.endswith(".mp4") else "image/jpeg" if key.endswith(".jpg") else None
    return FileResponse(path, media_type=media_type)


@router.get("/stats/clips-by-origin")
def clips_by_origin(db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)) -> dict[str, int]:
    return {o.value: db.scalar(select(func.count()).select_from(Clip).where(Clip.origin == o)) or 0
            for o in ClipOrigin}
