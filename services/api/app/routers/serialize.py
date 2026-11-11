"""JSON shapes returned to the dashboard. Never include secrets or tokens."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.models import (
    Clip,
    HighlightCandidate,
    PublishingHistory,
    PublishingJob,
    StreamSession,
    TikTokAccount,
    TwitchAccount,
    VideoSource,
    ViralEdit,
    ViralSourceVideo,
)
from app.services.storage import get_storage


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def media_url(key: str | None) -> str | None:
    return get_storage().presigned_url(key, 3600) if key else None


def session(s: StreamSession | None) -> dict[str, Any] | None:
    if s is None:
        return None
    return {"id": str(s.id), "twitch_stream_id": s.twitch_stream_id, "title": s.title, "game": s.game_name,
            "status": s.status.value, "started_at": iso(s.started_at), "ended_at": iso(s.ended_at),
            "analysis_mode": s.analysis_mode, "detected_via": s.detected_via, "stats": s.stats or {},
            "signals": (s.capabilities or {}).get("signals", [])}


def candidate(c: HighlightCandidate) -> dict[str, Any]:
    comps = dict(c.component_scores or {})
    return {"id": str(c.id), "session_id": str(c.session_id), "detected_at": iso(c.detected_at),
            "peak_offset_s": c.peak_offset_s, "start_offset_s": c.start_offset_s, "end_offset_s": c.end_offset_s,
            "category": c.category, "title": c.title, "explanation": c.explanation,
            "context_summary": c.context_summary, "quality_score": c.quality_score, "confidence": c.confidence,
            "components": {k: v for k, v in comps.items() if not k.startswith("_")},
            "signals": comps.get("_signals", {}), "supporting_families": comps.get("_supporting_families", []),
            "signals_used": c.signals_used, "analyzer": c.analyzer, "status": c.status.value,
            "reject_reason": c.reject_reason, "latency_ms": c.latency_ms, "transcript_excerpt": c.transcript_excerpt}


def clip(c: Clip, *, detail: bool = False) -> dict[str, Any]:
    out = {"id": str(c.id), "origin": c.origin.value, "session_id": str(c.session_id) if c.session_id else None,
           "title": c.title, "category": c.category, "quality_score": c.quality_score, "confidence": c.confidence,
           "verification": c.verification.value, "duration_s": c.duration_s, "edit_status": c.edit_status.value,
           "edit_error": c.edit_error, "twitch_status": c.twitch_status.value, "twitch_clip_url": c.twitch_clip_url,
           "twitch_error": c.twitch_error, "media_source": c.media_source.value, "caption": c.caption,
           "hashtags": c.hashtags or [], "caption_source": c.caption_source, "created_at": iso(c.created_at),
           "thumbnail_url": media_url(c.thumbnail_key), "video_url": media_url(c.rendered_key),
           "layout": (c.render_meta or {}).get("layout", {}).get("kind")}
    if detail:
        out.update({"rights_basis": c.rights_basis, "source_url": media_url(c.source_media_key),
                    "transcript": c.transcript_text, "twitch_edit_url": c.twitch_edit_url,
                    "render_meta": c.render_meta or {}, "start_offset_s": c.start_offset_s,
                    "candidate": candidate(c.candidate) if c.candidate else None})
    return out


def source(v: VideoSource) -> dict[str, Any]:
    return {"kind": v.kind.value, "status": v.status.value, "detail": v.detail, "capabilities": v.capabilities,
            "last_checked_at": iso(v.last_checked_at)}


def twitch_account(a: TwitchAccount) -> dict[str, Any]:
    return {"id": str(a.id), "login": a.login, "display_name": a.display_name, "role": a.role.value,
            "twitch_user_id": a.twitch_user_id, "editor_verified": a.is_editor_verified,
            "scopes": a.token.scopes if a.token else [], "revoked": bool(a.token and a.token.revoked),
            "connected_by": a.connected_by, "connected_at": iso(a.created_at)}


def tiktok_account(a: TikTokAccount | None) -> dict[str, Any] | None:
    if a is None:
        return None
    return {"id": str(a.id), "display_name": a.display_name, "username": a.username, "avatar_url": a.avatar_url,
            "creator_info": a.creator_info, "creator_info_at": iso(a.creator_info_at),
            "scopes": a.token.scopes if a.token else [], "revoked": bool(a.token and a.token.revoked),
            "token_expires_at": iso(a.token.expires_at) if a.token else None, "connected_by": a.connected_by}


def publishing_job(j: PublishingJob) -> dict[str, Any]:
    return {"id": str(j.id), "clip": clip(j.clip), "mode": j.mode.value, "status": j.status.value, "title": j.title,
            "privacy_level": j.privacy_level, "allow_comment": j.allow_comment, "allow_duet": j.allow_duet,
            "allow_stitch": j.allow_stitch, "brand_content": j.brand_content_toggle,
            "brand_organic": j.brand_organic_toggle, "scheduled_for": iso(j.scheduled_for),
            "schedule_basis": j.schedule_basis, "transfer_method": j.transfer_method, "publish_id": j.publish_id,
            "post_ids": j.post_ids, "fail_reason": j.fail_reason, "confirmed_by": j.confirmed_by,
            "consent_at": iso(j.consent_at), "published_at": iso(j.published_at), "created_at": iso(j.created_at)}


def history(h: PublishingHistory) -> dict[str, Any]:
    return {"id": str(h.id), "job_id": str(h.job_id), "event": h.event, "status": h.status, "detail": h.detail,
            "at": iso(h.at)}


def viral_source(v: ViralSourceVideo) -> dict[str, Any]:
    return {"id": str(v.id), "platform": v.platform, "url": v.source_url, "title": v.title, "creator": v.creator,
            "published_at": iso(v.published_at), "view_count": v.view_count, "engagement": v.engagement,
            "duration_s": v.duration_s, "popularity_score": v.popularity_score, "rights_status": v.rights_status.value,
            "discovered_via": v.discovered_via, "fetched_at": iso(v.fetched_at)}


def viral_edit(e: ViralEdit, out_clip: Clip | None) -> dict[str, Any]:
    return {"id": str(e.id), "template": e.template_key, "params": e.params, "input_clip_ids": e.input_clip_ids,
            "status": e.status, "error": e.error, "created_at": iso(e.created_at),
            "output": clip(out_clip) if out_clip else None}
