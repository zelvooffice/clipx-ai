"""TikTok account connection, compliant publishing workflow and status tracking."""

from __future__ import annotations

import secrets
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import utcnow
from app.integrations.tiktok.client import TikTokClient, TikTokError, chunk_plan
from app.models import (
    Clip,
    OAuthState,
    Provider,
    PublishingHistory,
    PublishingJob,
    PublishMode,
    PublishStatus,
    TikTokAccount,
)
from app.publishing import policy
from app.services import app_settings, jobs
from app.services.audit import syslog
from app.services.storage import get_storage
from app.services.twitch_service import _decrypt, consume_state, store_token


class TikTokNotConfigured(Exception):
    pass


class NoTikTokAccount(Exception):
    pass


def account(db: Session) -> TikTokAccount | None:
    return db.scalar(select(TikTokAccount).order_by(TikTokAccount.created_at.desc()))


def history(db: Session, job: PublishingJob, event: str, **detail: Any) -> None:
    db.add(PublishingHistory(job_id=job.id, event=event, status=job.status.value, detail=detail))


# ------------------------------------------------------------------------------------- connect
def start_connect(db: Session, email: str) -> str:
    if not get_settings().tiktok_configured:
        raise TikTokNotConfigured("TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET are not configured")
    from app.integrations.tiktok.client import authorize_url

    state = secrets.token_urlsafe(32)
    db.add(OAuthState(state=state, provider=Provider.tiktok, created_by=email,
                      expires_at=utcnow() + timedelta(minutes=10)))
    return authorize_url(state)


async def complete_connect(db: Session, code: str, state: str, email: str) -> TikTokAccount:
    consume_state(db, Provider.tiktok, state, email)
    client = TikTokClient()
    try:
        tokens = await client.exchange_code(code)
        client.token = tokens.access_token
        info = await client.user_info()
        creator: dict[str, Any] | None = None
        if "video.publish" in tokens.scopes:
            creator = await client.creator_info()
    finally:
        await client.aclose()
    tok = store_token(db, Provider.tiktok, tokens.open_id, tokens, refresh_expires_in=tokens.refresh_expires_in)
    acct = db.scalar(select(TikTokAccount).where(TikTokAccount.open_id == tokens.open_id))
    if acct is None:
        acct = TikTokAccount(open_id=tokens.open_id)
        db.add(acct)
    acct.display_name = info.get("display_name")
    acct.avatar_url = info.get("avatar_url")
    acct.token_id = tok.id
    acct.connected_by = email
    if creator:
        acct.creator_info, acct.creator_info_at = creator, utcnow()
        acct.username = creator.get("creator_username")
    db.flush()
    return acct


async def access_token(db: Session, acct: TikTokAccount) -> str:
    tok = acct.token
    if tok is None or tok.revoked:
        raise NoTikTokAccount("TikTok account needs to be reconnected")
    if tok.expires_at and tok.expires_at - utcnow() < timedelta(minutes=5):
        if not tok.refresh_token_enc:
            raise NoTikTokAccount("TikTok token expired; reconnect")
        client = TikTokClient()
        try:
            fresh = await client.refresh(_decrypt(tok, "refresh_token_enc"))
        except TikTokError as exc:
            tok.revoked = True
            syslog(db, "ERROR", "tiktok", "token_revoked", f"TikTok refresh failed: {exc.message}")
            raise NoTikTokAccount("TikTok authorization expired or was revoked; reconnect") from exc
        finally:
            await client.aclose()
        # refresh tokens rotate: store_token persists the new one
        store_token(db, Provider.tiktok, tok.subject, fresh, refresh_expires_in=fresh.refresh_expires_in)
    return _decrypt(tok, "access_token_enc")


async def refresh_creator_info(db: Session, acct: TikTokAccount) -> dict[str, Any]:
    client = TikTokClient(await access_token(db, acct))
    try:
        info = await client.creator_info()
    finally:
        await client.aclose()
    acct.creator_info, acct.creator_info_at = info, utcnow()
    acct.username = info.get("creator_username") or acct.username
    return info


async def disconnect(db: Session, acct: TikTokAccount) -> None:
    if acct.token and not acct.token.revoked:
        client = TikTokClient(_decrypt(acct.token, "access_token_enc"))
        try:
            await client.revoke()
        except Exception as exc:
            syslog(db, "WARNING", "tiktok", "revoke_failed", str(exc))
        finally:
            await client.aclose()
        db.delete(acct.token)
    db.delete(acct)


# ------------------------------------------------------------------------------------- prepare
def prepare(db: Session, clip: Clip, *, mode: PublishMode | None = None, scheduled_for: Any = None,
            schedule_basis: str | None = None) -> PublishingJob | None:
    """Create the publishing job for a rendered, eligible clip (idempotent per clip+account)."""
    acct = account(db)
    if acct is None:
        return None
    cfg = app_settings.get_section(db, "publishing")
    elig = policy.clip_eligibility(db, clip, cfg, account_id=acct.id)
    if not elig.eligible:
        return None
    existing = db.scalar(select(PublishingJob).where(
        PublishingJob.clip_id == clip.id, PublishingJob.tiktok_account_id == acct.id,
        PublishingJob.status.notin_([PublishStatus.failed, PublishStatus.cancelled])))
    if existing:
        return existing
    mode = mode or PublishMode(cfg.auto_mode)
    job = PublishingJob(clip_id=clip.id, tiktok_account_id=acct.id, mode=mode,
                        status=policy.initial_job_status(mode, cfg),
                        title=f"{clip.caption} {' '.join(clip.hashtags or [])}".strip()[:2200],
                        scheduled_for=scheduled_for, schedule_basis=schedule_basis)
    db.add(job)
    db.flush()
    history(db, job, "prepared", mode=mode.value, scheduled_for=str(scheduled_for) if scheduled_for else None)
    if job.status == PublishStatus.scheduled:
        delay = max(0.0, (scheduled_for - utcnow()).total_seconds()) if scheduled_for else 0.0
        jobs.enqueue(db, "publish_tiktok", f"publish:{job.id}", ref_id=str(job.id),
                     payload={"job_id": str(job.id)}, delay_s=delay, max_attempts=4)
    return job


def confirm_direct_post(db: Session, job: PublishingJob, *, actor: str, privacy_level: str, allow_comment: bool,
                        allow_duet: bool, allow_stitch: bool, disclosure_on: bool, brand_content: bool,
                        brand_organic: bool, consent: bool, title: str, scheduled_for: Any = None) -> list[str]:
    """The one-click, TikTok-compliant confirmation for a Direct Post."""
    if job.status != PublishStatus.awaiting_confirmation:
        return [f"job is {job.status.value}, not awaiting confirmation"]
    acct = db.get(TikTokAccount, job.tiktok_account_id)
    assert acct is not None
    cfg = app_settings.get_section(db, "publishing")
    if not acct.creator_info or not acct.creator_info_at or utcnow() - acct.creator_info_at > timedelta(minutes=10):
        return ["creator info is stale: reload the confirmation card"]
    errors = policy.validate_direct_post_choices(
        acct.creator_info, privacy_level=privacy_level, allow_comment=allow_comment, allow_duet=allow_duet,
        allow_stitch=allow_stitch, consent=consent, duration_s=job.clip.duration_s or 0,
        brand_content=brand_content, brand_organic=brand_organic, client_audited=cfg.client_audited,
        disclosure_on=disclosure_on)
    if len(title) > 2200:
        errors.append("title exceeds 2200 characters")
    if errors:
        return errors
    job.privacy_level = privacy_level
    job.allow_comment, job.allow_duet, job.allow_stitch = allow_comment, allow_duet, allow_stitch
    job.brand_content_toggle, job.brand_organic_toggle = brand_content, brand_organic
    job.title = title
    job.consent_at, job.confirmed_by = utcnow(), actor
    job.status = PublishStatus.scheduled
    job.scheduled_for = scheduled_for
    history(db, job, "confirmed", by=actor, privacy=privacy_level, consent=policy.consent_text(brand_content))
    delay = max(0.0, (scheduled_for - utcnow()).total_seconds()) if scheduled_for else 0.0
    jobs.enqueue(db, "publish_tiktok", f"publish:{job.id}", ref_id=str(job.id), payload={"job_id": str(job.id)},
                 delay_s=delay, max_attempts=4)
    return []


# ------------------------------------------------------------------------------------- execute
async def execute(db: Session, job: PublishingJob) -> None:
    """Send the media to TikTok (called from the publish_tiktok job)."""
    if job.status not in (PublishStatus.scheduled, PublishStatus.uploading):
        return
    acct = db.get(TikTokAccount, job.tiktok_account_id)
    if acct is None:
        raise NoTikTokAccount("TikTok account disconnected")
    cfg = app_settings.get_section(db, "publishing")
    cap = policy.capacity(db, cfg, acct.id)
    if cap["remaining_today"] <= 0 or cap["next_allowed_at"] > utcnow() + timedelta(seconds=30):
        raise jobs.RetryLater(max(60.0, (cap["next_allowed_at"] - utcnow()).total_seconds()),
                              "posting cap / cooldown", max_polls=200)
    if job.mode == PublishMode.inbox_draft and not cfg.auto_publish and job.confirmed_by is None:
        job.status = PublishStatus.cancelled
        history(db, job, "cancelled", reason="auto-publish disabled before upload")
        return
    token = await access_token(db, acct)
    client = TikTokClient(token)
    clip = job.clip
    storage = get_storage()
    s = get_settings()
    try:
        if job.mode == PublishMode.direct_post:
            info = await client.creator_info()  # latest info right before posting
            acct.creator_info, acct.creator_info_at = info, utcnow()
            if job.privacy_level not in (info.get("privacy_level_options") or []):
                raise jobs.PermanentFailure("privacy level no longer available for this creator; re-confirm")
        size = storage.size(clip.rendered_key or "") or 0
        public = storage.public_url(clip.rendered_key or "")
        use_pull = bool(s.tiktok_pull_url_prefix and public and public.startswith(s.tiktok_pull_url_prefix))
        source: dict[str, Any]
        if use_pull:
            source = {"source": "PULL_FROM_URL", "video_url": public}
        else:
            chunk, count = chunk_plan(size)
            source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}
        job.transfer_method = source["source"]
        job.status = PublishStatus.uploading
        job.attempts += 1
        if job.mode == PublishMode.direct_post:
            post_info = {"privacy_level": job.privacy_level, "title": job.title,
                         "disable_comment": not job.allow_comment, "disable_duet": not job.allow_duet,
                         "disable_stitch": not job.allow_stitch,
                         "brand_content_toggle": job.brand_content_toggle,
                         "brand_organic_toggle": job.brand_organic_toggle, "is_aigc": job.is_aigc,
                         "video_cover_timestamp_ms": 1000}
            data = await client.init_direct_post(post_info, source)
        else:
            data = await client.init_inbox_upload(source)
        job.publish_id = data["publish_id"]
        history(db, job, "initialized", publish_id=job.publish_id, transfer=job.transfer_method)
        db.commit()
        if not use_pull:
            with tempfile.TemporaryDirectory(prefix="clipx-tt-") as tmp:
                local = storage.get_file(clip.rendered_key or "", Path(tmp) / "video.mp4")
                chunk, count = source["chunk_size"], source["total_chunk_count"]
                await client.upload_file(data["upload_url"], local, int(chunk), int(count))
            history(db, job, "uploaded", bytes=size)
        job.status = PublishStatus.processing
        jobs.enqueue(db, "poll_tiktok_status", f"ttstatus:{job.id}", ref_id=str(job.id),
                     payload={"job_id": str(job.id)}, delay_s=10, max_attempts=3)
    except TikTokError as exc:
        job.fail_reason = f"{exc.code}: {exc.message}"
        history(db, job, "error", code=exc.code, message=exc.message, log_id=exc.log_id)
        if exc.retryable:
            job.status = PublishStatus.scheduled
            db.commit()  # persist the history entry before the job runner retries
            raise
        job.status = PublishStatus.failed
        db.commit()
        raise jobs.PermanentFailure(job.fail_reason) from exc
    finally:
        await client.aclose()


async def poll_status(db: Session, job: PublishingJob) -> None:
    if job.status != PublishStatus.processing or not job.publish_id:
        return
    acct = db.get(TikTokAccount, job.tiktok_account_id)
    if acct is None:
        raise NoTikTokAccount("TikTok account disconnected")
    client = TikTokClient(await access_token(db, acct))
    try:
        data = await client.fetch_status(job.publish_id)
    finally:
        await client.aclose()
    status = data.get("status")
    if status == "PUBLISH_COMPLETE":
        job.status = PublishStatus.published
        job.published_at = utcnow()
        job.post_ids = [str(x) for x in data.get("publicaly_available_post_id") or []]
        history(db, job, "published", post_ids=job.post_ids)
        return
    if status == "SEND_TO_USER_INBOX":
        job.status = PublishStatus.sent_to_inbox
        history(db, job, "sent_to_inbox", note="Open the TikTok app inbox notification to finish the post.")
        return
    if status == "FAILED":
        job.status = PublishStatus.failed
        job.fail_reason = data.get("fail_reason")
        history(db, job, "failed", fail_reason=job.fail_reason)
        return
    history(db, job, "processing", status=status, uploaded_bytes=data.get("uploaded_bytes"))
    raise jobs.RetryLater(15, f"TikTok status {status}", max_polls=240)
