"""Job handlers. Each is idempotent: it checks the current state before acting, so re-running after a
crash never duplicates work (docs/ARCHITECTURE.md §4.3)."""

from __future__ import annotations

import asyncio
import math
import shutil
import tempfile
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope, utcnow
from app.detection import engine
from app.detection.context import default_analyzer
from app.detection.signals import audio_features, load_audio, visual_features
from app.detection.transcribe import transcribe
from app.integrations.twitch import helix
from app.models import (
    CandidateStatus,
    Clip,
    EditConfig,
    EditStatus,
    JobStatus,
    MediaSource,
    ProcessingJob,
    PublishingJob,
    PublishStatus,
    SessionStatus,
    StreamSession,
    SubtitleTrack,
    TwitchClipStatus,
    Verification,
)
from app.services import app_settings, chat, jobs, twitch_service
from app.services.audit import syslog
from app.services.jobs import PermanentFailure, RetryLater, handler, on_dead
from app.services.storage import get_storage
from app.video import subtitles


def _clip(db: Any, payload: dict[str, Any]) -> Clip:
    clip = db.get(Clip, uuid.UUID(payload["clip_id"]))
    if clip is None:
        raise PermanentFailure("clip no longer exists")
    return clip


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ======================================================================= Twitch native clips
@handler("create_twitch_clip")
def create_twitch_clip(payload: dict[str, Any], job_id: uuid.UUID) -> dict[str, Any] | None:
    with session_scope() as db:
        clip = _clip(db, payload)
        if clip.twitch_status != TwitchClipStatus.not_requested:
            return {"skipped": clip.twitch_status.value}
        sess = db.get(StreamSession, clip.session_id) if clip.session_id else None
        if sess is None or sess.status != SessionStatus.live:
            clip.twitch_error = "stream is no longer live; Twitch clips can only be created while live"
            return {"skipped": "not live"}
        start = clip.start_offset_s or 0.0
        end = start + (clip.duration_s or 30.0)
        title = (clip.title or "Lacy moment")[:100]
        now_offset = time.time() - sess.started_at.timestamp()
        vod_acct = twitch_service.best_account(db, needs="vod_clip")
        clip_acct = twitch_service.best_account(db, needs="clips")

        async def go() -> dict[str, Any] | None:
            if vod_acct is not None and not payload.get("capture_now"):
                token = await twitch_service.get_user_access_token(db, vod_acct)
                api = helix.Helix(token)
                if not sess.vod_id:
                    vod = await api.get_current_vod(sess.broadcaster_id, sess.twitch_stream_id)
                    sess.vod_id = vod["id"] if vod else None
                if sess.vod_id:
                    duration = min(60.0, max(5.0, end - start))
                    return await api.create_clip_from_vod(
                        editor_id=vod_acct.twitch_user_id, broadcaster_id=sess.broadcaster_id, vod_id=sess.vod_id,
                        vod_offset=int(math.ceil(max(end, duration))), duration=duration, title=title)
            if clip_acct is None:
                clip.twitch_error = "no Twitch account with clips:edit connected"
                return None
            if payload.get("capture_now"):
                duration = min(60.0, max(5.0, clip.duration_s or 40.0))
            else:
                window = now_offset - start
                if window > 88:  # Create Clip only captures ~90 s ending now
                    clip.twitch_error = ("moment is older than Twitch's 90 s capture window "
                                         "(no editor auth for VOD clips)")
                    return None
                duration = min(60.0, max(5.0, window))
            token = await twitch_service.get_user_access_token(db, clip_acct)
            return await helix.Helix(token).create_clip(sess.broadcaster_id, title=title, duration=duration)

        try:
            created = _run(go())
        except (helix.ClipRestricted, helix.NotLive) as exc:
            clip.twitch_status = TwitchClipStatus.failed
            clip.twitch_error = exc.message
            syslog(db, "WARNING", "twitch", "clip_restricted", f"Twitch refused clip creation: {exc.message}",
                   session_id=clip.session_id)
            db.commit()  # keep the recorded failure: raising below would otherwise roll it back
            raise PermanentFailure(exc.message) from exc
        if created is None:
            return {"skipped": clip.twitch_error}
        clip.twitch_clip_id = created["id"]
        clip.twitch_edit_url = created.get("edit_url")
        clip.twitch_status = TwitchClipStatus.pending
        clip.render_meta = {**(clip.render_meta or {}), "twitch_requested_at": time.time()}
        jobs.enqueue(db, "poll_twitch_clip", f"twitchpoll:{clip.id}", ref_id=str(clip.id),
                     payload={"clip_id": str(clip.id)}, delay_s=5)
        return {"twitch_clip_id": created["id"]}


@handler("poll_twitch_clip")
def poll_twitch_clip(payload: dict[str, Any], job_id: uuid.UUID) -> dict[str, Any] | None:
    with session_scope() as db:
        clip = _clip(db, payload)
        if clip.twitch_status != TwitchClipStatus.pending or not clip.twitch_clip_id:
            return {"skipped": clip.twitch_status.value}
        clip_id = clip.twitch_clip_id

        async def go() -> list[dict[str, Any]]:
            data, _ = await (await twitch_service.app_helix()).get_clips(ids=[clip_id])
            return data

        found = _run(go())
        if found:
            c = found[0]
            clip.twitch_status = TwitchClipStatus.created
            clip.twitch_clip_url = c.get("url")
            if clip.media_source == MediaSource.none:
                jobs.enqueue(db, "fetch_clip_media", f"media:{clip.id}", ref_id=str(clip.id),
                             payload={"clip_id": str(clip.id), "twitch_duration": c.get("duration")}, delay_s=10)
            return {"created": True, "url": c.get("url")}
        requested = float((clip.render_meta or {}).get("twitch_requested_at", time.time()))
        if time.time() - requested < 60:  # Twitch: assume failure if not returned within 60 s
            raise RetryLater(5, "waiting for Twitch to finish creating the clip", max_polls=14)
        clip.twitch_status = TwitchClipStatus.failed
        clip.twitch_error = "Twitch did not confirm the clip within 60 seconds"
        return {"created": False}


# ======================================================================= authorized media + verification
@handler("fetch_clip_media")
def fetch_clip_media(payload: dict[str, Any], job_id: uuid.UUID) -> dict[str, Any] | None:
    from app.sources.clip_media import MediaUnavailable, download_clip

    with session_scope() as db:
        clip = _clip(db, payload)
        if clip.source_media_key:
            return {"skipped": "media present"}
        sess = db.get(StreamSession, clip.session_id) if clip.session_id else None
        broadcaster = sess.broadcaster_id if sess else _run(twitch_service.target_broadcaster())["id"]
        with tempfile.TemporaryDirectory(prefix="clipx-dl-") as tmp:
            try:
                path, basis = _run(download_clip(db, broadcaster, clip.twitch_clip_id or "", Path(tmp) / "c.mp4"))
            except MediaUnavailable as exc:
                if "no download URL" in str(exc):
                    raise RetryLater(15, str(exc), max_polls=12) from exc
                clip.edit_status = EditStatus.needs_media
                clip.edit_error = str(exc)
                return {"media": False, "reason": str(exc)}
            key = f"clips/{clip.id}/source.mp4"
            get_storage().put_file(key, path, "video/mp4")
        clip.source_media_key, clip.media_source, clip.rights_basis = key, MediaSource.twitch_download, basis
        clip.edit_status = EditStatus.queued
        if clip.verification == Verification.unverified:
            jobs.enqueue(db, "verify_clip", f"verify:{clip.id}", ref_id=str(clip.id), payload={"clip_id": str(clip.id)})
        else:
            jobs.enqueue(db, "transcribe_clip", f"transcribe:{clip.id}:v1", ref_id=str(clip.id),
                         payload={"clip_id": str(clip.id)})
        return {"media": True}


@handler("verify_clip")
def verify_clip(payload: dict[str, Any], job_id: uuid.UUID) -> dict[str, Any] | None:
    """Full multi-signal analysis of a chat-nominated clip on its real media."""
    with session_scope() as db:
        clip = _clip(db, payload)
        if clip.verification == Verification.verified or not clip.source_media_key:
            return {"skipped": True}
        cfg = app_settings.get_section(db, "processing")
        sess = db.get(StreamSession, clip.session_id) if clip.session_id else None
        with tempfile.TemporaryDirectory(prefix="clipx-verify-") as tmp:
            media = get_storage().get_file(clip.source_media_key, Path(tmp) / "c.mp4")
            t0 = clip.start_offset_s or 0.0
            tr = transcribe(media, offset=t0)
            af = audio_features(load_audio(media))
            vf = visual_features(media, sample_fps=4.0)
            tl = engine.Timeline(t0=t0, audio=af, visual=vf, words=tr.words)
            if sess is not None and chat.has_data(str(sess.id)):
                tl.chat = chat.window(str(sess.id), int(t0), int(t0) + af.seconds)
            tl.finalize()
            peaks = engine.find_peaks(tl, threshold=0.6, edge_guard_s=1) or [t0 + af.seconds * 0.6]
            from app.services import highlights

            res = engine.evaluate(
                tl, peaks[0], analyzer=default_analyzer(cfg.use_llm), weights=cfg.weights.model_dump(),
                min_quality=cfg.min_quality, min_confidence=cfg.min_confidence, min_clip_s=cfg.min_clip_s,
                max_clip_s=cfg.max_clip_s, accepted_so_far=highlights.accepted_count(db, clip.session_id),
                target_max=cfg.target_clips_max, existing=[], stream_title=sess.title if sess else None,
                game=sess.game_name if sess else None, lo=t0, hi=t0 + af.seconds)
        cand = clip.candidate
        if res.status != "accepted":
            clip.edit_status = EditStatus.rejected
            clip.edit_error = f"verification failed: {res.reason}"
            if cand:
                cand.status, cand.reject_reason = CandidateStatus.rejected, f"media verification: {res.reason}"
            return {"verified": False, "reason": res.reason}
        clip.verification = Verification.verified
        clip.title, clip.category = res.verdict.title, res.verdict.category
        clip.quality_score, clip.confidence = res.score.quality, res.score.confidence
        clip.render_meta = {**(clip.render_meta or {}), "keywords": res.verdict.keywords}
        clip.transcript_text = res.transcript_excerpt
        if cand:
            cand.status = CandidateStatus.clipped
            cand.category, cand.title = res.verdict.category, res.verdict.title
            cand.explanation = res.verdict.explanation
            cand.quality_score, cand.confidence = res.score.quality, res.score.confidence
            cand.component_scores = {**res.components.as_dict(), "_signals": res.signals,
                                     "_supporting_families": res.score.supporting_families}
            cand.signals_used = res.score.available
            cand.start_offset_s, cand.end_offset_s = res.start, res.end
        ec = _edit_config(db, clip)
        ec.trim_start_s, ec.trim_end_s = round(res.start - t0, 3), round(res.end - t0, 3)
        _save_track(db, clip, [w.as_dict() for w in tr.words], offset=t0, engine_name=tr.engine, lang=tr.language)
        clip.edit_status = EditStatus.queued
        jobs.enqueue(db, "render_clip", f"render:{clip.id}:v{ec.version}", ref_id=str(clip.id),
                     payload={"clip_id": str(clip.id)})
        return {"verified": True, "quality": res.score.quality}


# ======================================================================= transcription / render
def _edit_config(db: Any, clip: Clip) -> EditConfig:
    ec = db.scalar(select(EditConfig).where(EditConfig.clip_id == clip.id))
    if ec is None:
        ec = EditConfig(clip_id=clip.id, subtitle_style=dict(subtitles.DEFAULT_STYLE))
        db.add(ec)
        db.flush()
    return ec


def _save_track(db: Any, clip: Clip, words: list[dict[str, Any]], *, offset: float, engine_name: str,
                lang: str | None) -> SubtitleTrack:
    """Store words relative to the clip's source media (t=0 at media start)."""
    rel = [{**w, "start": round(w["start"] - offset, 3), "end": round(w["end"] - offset, 3)} for w in words]
    for old in db.scalars(select(SubtitleTrack).where(SubtitleTrack.clip_id == clip.id,
                                                      SubtitleTrack.is_active.is_(True))).all():
        old.is_active = False
    track = SubtitleTrack(clip_id=clip.id, language=lang, engine=engine_name, words=rel, cues=[], version=1)
    db.add(track)
    clip.transcript_text = "".join(w["w"] for w in words).strip()
    return track


@handler("transcribe_clip")
def transcribe_clip(payload: dict[str, Any], job_id: uuid.UUID) -> dict[str, Any] | None:
    with session_scope() as db:
        clip = _clip(db, payload)
        if not clip.source_media_key:
            raise PermanentFailure("no source media")
        clip.edit_status = EditStatus.transcribing
        db.commit()
        with tempfile.TemporaryDirectory(prefix="clipx-asr-") as tmp:
            media = get_storage().get_file(clip.source_media_key, Path(tmp) / "c.mp4")
            tr = transcribe(media)
        _save_track(db, clip, [w.as_dict() for w in tr.words], offset=0.0, engine_name=tr.engine, lang=tr.language)
        if clip.candidate and "keywords" not in (clip.render_meta or {}):
            clip.render_meta = {**(clip.render_meta or {}), "keywords": []}
        ec = _edit_config(db, clip)
        jobs.enqueue(db, "render_clip", f"render:{clip.id}:v{ec.version}", ref_id=str(clip.id),
                     payload={"clip_id": str(clip.id)})
        return {"words": len(tr.words)}


@handler("render_clip")
def render_clip(payload: dict[str, Any], job_id: uuid.UUID) -> dict[str, Any] | None:
    from app.video.render import RenderOptions, render

    s = get_settings()
    with session_scope() as db:
        clip = _clip(db, payload)
        if not clip.source_media_key:
            raise PermanentFailure("no source media")
        ec = _edit_config(db, clip)
        track = db.scalar(select(SubtitleTrack).where(SubtitleTrack.clip_id == clip.id,
                                                      SubtitleTrack.is_active.is_(True)))
        version = ec.version
        out_key = f"clips/{clip.id}/render_v{version}.mp4"
        clip.edit_status = EditStatus.rendering
        db.commit()
        storage = get_storage()
        with tempfile.TemporaryDirectory(prefix="clipx-r-") as tmp:
            src = storage.get_file(clip.source_media_key, Path(tmp) / "src.mp4")
            opts = RenderOptions(
                clip_id=str(clip.id), category=clip.category, keywords=(clip.render_meta or {}).get("keywords") or [],
                layout=ec.layout, zoom_intensity=ec.zoom_intensity, sfx_enabled=ec.sfx_enabled,
                subtitle_style=ec.subtitle_style or None,
                crop_x=(ec.crop_override or {}).get("x_center"), trim_start=ec.trim_start_s, trim_end=ec.trim_end_s,
                version=version, preset=s.render_preset, crf=s.render_crf,
                cues_override=track.cues if (track and track.edited and track.cues) else None)
            result = render(src, Path(tmp) / "out.mp4", track.words if track else [], opts)
            storage.put_file(out_key, result.output, "video/mp4")
            thumb_key = f"clips/{clip.id}/thumb_v{version}.jpg"
            storage.put_file(thumb_key, result.thumbnail, "image/jpeg")
        old_keys = [k for k in (clip.rendered_key, clip.thumbnail_key) if k and k not in (out_key, thumb_key)]
        clip.rendered_key, clip.thumbnail_key = out_key, thumb_key
        clip.duration_s = result.duration
        clip.render_meta = {**(clip.render_meta or {}), "layout": result.layout, "version": version,
                            "rendered_at": utcnow().isoformat()}
        clip.edit_status, clip.edit_error = EditStatus.rendered, None
        ec.plan = result.plan
        if track and not track.edited:
            track.cues = result.cues
        for k in old_keys:
            storage.delete(k)
        nxt = "caption_clip" if not clip.caption else "prepare_publish"
        jobs.enqueue(db, nxt, f"{nxt}:{clip.id}:v{version}", ref_id=str(clip.id), payload={"clip_id": str(clip.id)})
        return {"key": out_key, "layout": result.layout["kind"], "zooms": len(result.plan["zooms"])}


@on_dead("render_clip")
def _render_dead(payload: dict[str, Any], error: str) -> None:
    with session_scope() as db:
        clip = db.get(Clip, uuid.UUID(payload["clip_id"]))
        if clip:
            clip.edit_status, clip.edit_error = EditStatus.failed, error[:1000]


@on_dead("transcribe_clip")
def _asr_dead(payload: dict[str, Any], error: str) -> None:
    _render_dead(payload, f"transcription failed: {error}")


# ======================================================================= captions / publishing
@handler("caption_clip")
def caption_clip(payload: dict[str, Any], job_id: uuid.UUID) -> dict[str, Any] | None:
    from app.captions.generator import generate

    with session_scope() as db:
        clip = _clip(db, payload)
        if clip.caption:
            return {"skipped": True}
        cfg = app_settings.get_section(db, "processing")
        recent = [c for c in db.scalars(select(Clip.caption).where(Clip.caption.isnot(None))
                                        .order_by(Clip.created_at.desc()).limit(40)).all() if c]
        sess = db.get(StreamSession, clip.session_id) if clip.session_id else None
        res = generate(clip_id=str(clip.id), title=clip.title, category=clip.category,
                       transcript=clip.transcript_text or "",
                       context=clip.candidate.context_summary if clip.candidate else None,
                       game=sess.game_name if sess else None, recent=recent, use_llm=cfg.use_llm)
        clip.caption, clip.hashtags, clip.caption_source = res.caption, res.hashtags, res.source
        jobs.enqueue(db, "prepare_publish", f"prepare_publish:{clip.id}", ref_id=str(clip.id),
                     payload={"clip_id": str(clip.id)})
        return {"source": res.source}


@handler("prepare_publish")
def prepare_publish(payload: dict[str, Any], job_id: uuid.UUID) -> dict[str, Any] | None:
    from app.publishing.scheduler import next_slot
    from app.services import tiktok_service

    with session_scope() as db:
        clip = _clip(db, payload)
        cfg = app_settings.get_section(db, "publishing")
        if not cfg.auto_publish:
            return {"skipped": "auto-publish disabled"}
        if not cfg.automation_consent_at:
            return {"skipped": "no recorded automation consent"}
        acct = tiktok_service.account(db)
        if acct is None:
            return {"skipped": "no TikTok account connected"}
        slot, basis = next_slot(db, cfg, acct.id)
        job = tiktok_service.prepare(db, clip, scheduled_for=slot, schedule_basis=basis)
        return {"job": str(job.id) if job else None}


@handler("publish_tiktok")
def publish_tiktok(payload: dict[str, Any], job_id: uuid.UUID) -> dict[str, Any] | None:
    from app.services import tiktok_service

    with session_scope() as db:
        job = db.get(PublishingJob, uuid.UUID(payload["job_id"]))
        if job is None:
            raise PermanentFailure("publishing job deleted")
        _run(tiktok_service.execute(db, job))
        return {"status": job.status.value}


@on_dead("publish_tiktok")
def _publish_dead(payload: dict[str, Any], error: str) -> None:
    from app.services.tiktok_service import history

    with session_scope() as db:
        job = db.get(PublishingJob, uuid.UUID(payload["job_id"]))
        if job and job.status not in (PublishStatus.published, PublishStatus.sent_to_inbox):
            job.status, job.fail_reason = PublishStatus.failed, error[:1000]
            history(db, job, "failed", error=error[:500])


@handler("poll_tiktok_status")
def poll_tiktok_status(payload: dict[str, Any], job_id: uuid.UUID) -> dict[str, Any] | None:
    from app.services import tiktok_service

    with session_scope() as db:
        job = db.get(PublishingJob, uuid.UUID(payload["job_id"]))
        if job is None:
            raise PermanentFailure("publishing job deleted")
        _run(tiktok_service.poll_status(db, job))
        return {"status": job.status.value}


# ======================================================================= sessions & maintenance
@handler("finalize_session")
def finalize_session(payload: dict[str, Any], job_id: uuid.UUID) -> dict[str, Any] | None:
    from app.services.streams import refresh_stats

    with session_scope() as db:
        sess = db.get(StreamSession, uuid.UUID(payload["session_id"]))
        if sess is None or sess.status == SessionStatus.complete:
            return {"skipped": True}
        clip_ids = [str(c) for c in db.scalars(select(Clip.id).where(Clip.session_id == sess.id)).all()]
        pending = db.scalar(select(ProcessingJob.id).where(
            ProcessingJob.ref_id.in_(clip_ids),
            ProcessingJob.status.in_([JobStatus.queued, JobStatus.running, JobStatus.failed]),
            ProcessingJob.id != job_id).limit(1)) if clip_ids else None
        if pending is not None:
            raise RetryLater(60, "waiting for clip processing to finish", max_polls=180)
        shutil.rmtree(get_settings().work_dir / "buffer" / str(sess.id), ignore_errors=True)
        stats = refresh_stats(db, sess)
        sess.status = SessionStatus.complete
        syslog(db, "INFO", "live", "session_complete",
               f"Stream finalized: {stats['clips']} clips, {stats['rendered']} rendered, "
               f"{stats['rejected']} moments rejected", session_id=sess.id)
        return stats


@handler("probe_sources")
def probe_sources(payload: dict[str, Any], job_id: uuid.UUID) -> dict[str, Any] | None:
    from app.services.streams import live_session
    from app.sources import registry

    with session_scope() as db:
        live = live_session(db) is not None
        return registry.probe_all(db, live=live, probe_ingest=False)


@handler("cleanup_media")
def cleanup_media(payload: dict[str, Any], job_id: uuid.UUID) -> dict[str, Any] | None:
    """Delete raw source media of published or rejected clips after 14 days; temp dirs older than 1 day."""
    removed = 0
    with session_scope() as db:
        cutoff = utcnow() - timedelta(days=14)
        rows = db.scalars(select(Clip).where(Clip.updated_at < cutoff, Clip.source_media_key.isnot(None),
                                             Clip.edit_status.in_([EditStatus.rejected]))).all()
        for c in rows:
            get_storage().delete(c.source_media_key or "")
            c.source_media_key = None
            removed += 1
    work = get_settings().work_dir
    if work.exists():
        for p in work.glob("buffer/*"):
            if p.is_dir() and time.time() - p.stat().st_mtime > 86400:
                shutil.rmtree(p, ignore_errors=True)
    return {"removed": removed}


@handler("sync_analytics")
def sync_analytics(payload: dict[str, Any], job_id: uuid.UUID) -> dict[str, Any] | None:
    from app.analytics.service import sync

    with session_scope() as db:
        return _run(sync(db))


@handler("discover_viral")
def discover_viral(payload: dict[str, Any], job_id: uuid.UUID) -> dict[str, Any] | None:
    from app.viral.discovery import discover

    with session_scope() as db:
        return _run(discover(db))


@handler("render_viral_edit")
def render_viral_edit(payload: dict[str, Any], job_id: uuid.UUID) -> dict[str, Any] | None:
    from app.viral.templates import render_edit

    with session_scope() as db:
        return render_edit(db, uuid.UUID(payload["edit_id"]))
