"""Publishing rules: eligibility, posting caps, cooldowns, near-duplicate prevention, TikTok UX compliance."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.detection.scoring import iou, text_similarity
from app.models import Clip, EditStatus, PublishingJob, PublishMode, PublishStatus, Verification
from app.services.app_settings import PublishingSettings

ACTIVE = [PublishStatus.awaiting_confirmation, PublishStatus.scheduled, PublishStatus.uploading,
          PublishStatus.processing, PublishStatus.sent_to_inbox, PublishStatus.published]
CONSENT_TEXT = "By posting, you agree to TikTok's Music Usage Confirmation"
CONSENT_TEXT_BRANDED = "By posting, you agree to TikTok's Branded Content Policy and Music Usage Confirmation"
INBOX_AUTOMATION_CONSENT = (
    "I allow CLIPX AI to automatically send eligible rendered clips as drafts to this TikTok account's inbox. "
    "Nothing is posted until I finish each post in the TikTok app."
)


def consent_text(brand_content: bool) -> str:
    """Declaration shown before the publish button (TikTok Content Sharing Guidelines §4)."""
    return CONSENT_TEXT_BRANDED if brand_content else CONSENT_TEXT


def disclosure_label(brand_organic: bool, brand_content: bool) -> str | None:
    if brand_content:
        return "Your video will be labeled as 'Paid partnership'"
    if brand_organic:
        return "Your video will be labeled as 'Promotional content'"
    return None


def direct_post_requires_confirmation(settings: PublishingSettings) -> bool:
    """TikTok's Content Sharing Guidelines require, for every Direct Post, that the creator manually selects
    the privacy level (no default), opts into interactions (unchecked by default), sees their nickname and
    agrees to the Music Usage Confirmation. Silent automatic Direct Post is therefore never allowed here.
    If TikTok approves a different flow for this app in writing, this is the single place to change."""
    return True


@dataclass
class Eligibility:
    eligible: bool
    reasons: list[str] = field(default_factory=list)


def clip_eligibility(db: Session, clip: Clip, cfg: PublishingSettings, *, account_id: Any) -> Eligibility:
    reasons = []
    if clip.edit_status != EditStatus.rendered or not clip.rendered_key:
        reasons.append("clip is not rendered yet")
    if cfg.require_verified_clips and clip.verification != Verification.verified:
        reasons.append("clip is unverified (chat signal only, media not analysed)")
    if clip.quality_score is not None and clip.quality_score < cfg.min_quality_to_publish:
        reasons.append(f"quality {clip.quality_score:.2f} below publishing threshold {cfg.min_quality_to_publish:.2f}")
    if not clip.caption:
        reasons.append("caption not generated yet")
    dup = near_duplicate(db, clip, account_id)
    if dup:
        reasons.append(dup)
    return Eligibility(not reasons, reasons)


def near_duplicate(db: Session, clip: Clip, account_id: Any) -> str | None:
    """Avoid publishing nearly identical videos (same moment or same words)."""
    others = db.scalars(
        select(PublishingJob).where(PublishingJob.tiktok_account_id == account_id,
                                    PublishingJob.status.in_(ACTIVE), PublishingJob.clip_id != clip.id)
    ).all()
    for job in others:
        o = job.clip
        if o.twitch_clip_id and o.twitch_clip_id == clip.twitch_clip_id:
            return "the same Twitch clip is already queued/published"
        if (o.session_id == clip.session_id and o.start_offset_s is not None and clip.start_offset_s is not None
                and iou((o.start_offset_s, o.start_offset_s + (o.duration_s or 0)),
                        (clip.start_offset_s, clip.start_offset_s + (clip.duration_s or 0))) > 0.3):
            return f"overlaps a clip already queued/published ({o.title})"
        if clip.transcript_text and o.transcript_text and text_similarity(clip.transcript_text,
                                                                          o.transcript_text) > 0.5:
            return f"nearly identical speech to a clip already queued/published ({o.title})"
    return None


def capacity(db: Session, cfg: PublishingSettings, account_id: Any) -> dict[str, Any]:
    """Posts in the last 24 h and the earliest time the next post is allowed (cooldown)."""
    since = utcnow() - timedelta(hours=24)
    rows = db.scalars(select(PublishingJob).where(
        PublishingJob.tiktok_account_id == account_id,
        PublishingJob.status.in_([PublishStatus.published, PublishStatus.processing, PublishStatus.uploading,
                                  PublishStatus.sent_to_inbox]),
        PublishingJob.updated_at >= since)).all()
    last = max((r.published_at or r.updated_at for r in rows), default=None)
    next_ok = (last + timedelta(minutes=cfg.cooldown_minutes)) if last else utcnow()
    return {"posts_24h": len(rows), "max_per_day": cfg.max_posts_per_day,
            "remaining_today": max(0, cfg.max_posts_per_day - len(rows)), "next_allowed_at": max(next_ok, utcnow())}


def validate_direct_post_choices(creator_info: dict[str, Any], *, privacy_level: str | None, allow_comment: bool,
                                 allow_duet: bool, allow_stitch: bool, consent: bool, duration_s: float,
                                 brand_content: bool, brand_organic: bool, client_audited: bool,
                                 disclosure_on: bool = False) -> list[str]:
    """Server-side enforcement of TikTok's required UX choices for one Direct Post."""
    errors = []
    options = creator_info.get("privacy_level_options") or []
    if not privacy_level:
        errors.append("privacy level must be selected manually")
    elif privacy_level not in options:
        errors.append("privacy level is not one of the creator's available options")
    elif not client_audited and privacy_level != "SELF_ONLY":
        errors.append("this TikTok app is not audited yet: TikTok only allows SELF_ONLY (private) posts")
    if allow_comment and creator_info.get("comment_disabled"):
        errors.append("comments are disabled in the creator's TikTok settings")
    if allow_duet and creator_info.get("duet_disabled"):
        errors.append("duets are disabled in the creator's TikTok settings")
    if allow_stitch and creator_info.get("stitch_disabled"):
        errors.append("stitches are disabled in the creator's TikTok settings")
    max_d = creator_info.get("max_video_post_duration_sec")
    if max_d and duration_s > float(max_d):
        errors.append(f"video is {duration_s:.0f}s; this creator can post at most {max_d}s")
    if disclosure_on and not (brand_content or brand_organic):
        errors.append("You need to indicate if your content promotes yourself, a third party, or both.")
    if brand_content and privacy_level == "SELF_ONLY":
        errors.append("Branded content visibility cannot be set to private.")
    if not consent:
        errors.append(f"consent is required: '{consent_text(brand_content)}'")
    return errors


def initial_job_status(mode: PublishMode, cfg: PublishingSettings) -> PublishStatus:
    if mode == PublishMode.direct_post and direct_post_requires_confirmation(cfg):
        return PublishStatus.awaiting_confirmation
    return PublishStatus.scheduled
