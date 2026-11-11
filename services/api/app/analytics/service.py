"""TikTok performance sync (Display API video.list) and dashboard aggregates.

Only fields TikTok actually returns are stored: views, likes, comments, shares, duration, create_time.
Retention/watch-time is not available through the TikTok API, and the dashboard says so.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.tiktok.client import TikTokClient
from app.models import AnalyticsSnapshot, PublishingJob, PublishStatus
from app.publishing.scheduler import latest_metrics
from app.services import tiktok_service
from app.services.audit import syslog


async def sync(db: Session, *, max_pages: int = 10) -> dict[str, Any]:
    acct = tiktok_service.account(db)
    if acct is None or not acct.token or "video.list" not in (acct.token.scopes or []):
        return {"skipped": "no TikTok account with video.list scope"}
    client = TikTokClient(await tiktok_service.access_token(db, acct))
    published = db.scalars(select(PublishingJob).where(PublishingJob.status == PublishStatus.published)).all()
    by_post = {pid: j for j in published for pid in (j.post_ids or [])}
    n = 0
    cursor = None
    try:
        for _ in range(max_pages):
            data = await client.list_videos(cursor)
            for v in data.get("videos") or []:
                job = by_post.get(str(v.get("id")))
                db.add(AnalyticsSnapshot(
                    tiktok_video_id=str(v["id"]), clip_id=job.clip_id if job else None,
                    publishing_job_id=job.id if job else None, title=v.get("title") or v.get("video_description"),
                    posted_at=datetime.fromtimestamp(int(v["create_time"]), UTC) if v.get("create_time") else None,
                    duration_s=v.get("duration"), views=v.get("view_count"), likes=v.get("like_count"),
                    comments=v.get("comment_count"), shares=v.get("share_count"), share_url=v.get("share_url")))
                n += 1
            if not data.get("has_more"):
                break
            cursor = data.get("cursor")
    finally:
        await client.aclose()
    syslog(db, "INFO", "analytics", "sync", f"Synced metrics for {n} TikTok videos")
    return {"videos": n}


def summary(db: Session) -> dict[str, Any]:
    rows = latest_metrics(db)
    measured = [r for r in rows if r.views is not None]
    tot = {k: sum(getattr(r, k) or 0 for r in measured) for k in ("views", "likes", "comments", "shares")}
    eng = (tot["likes"] + tot["comments"] + tot["shares"]) / tot["views"] if tot["views"] else None
    best = sorted(measured, key=lambda r: r.views or 0, reverse=True)[:10]
    by_day: dict[str, dict[str, int]] = {}
    for r in measured:
        if r.posted_at is None:
            continue
        d = by_day.setdefault(r.posted_at.date().isoformat(),
                              {"views": 0, "likes": 0, "shares": 0, "comments": 0, "posts": 0})
        d["views"] += r.views or 0
        d["likes"] += r.likes or 0
        d["shares"] += r.shares or 0
        d["comments"] += r.comments or 0
        d["posts"] += 1
    return {
        "videos": len(measured), "totals": tot, "engagement_rate": round(eng, 4) if eng is not None else None,
        "retention": {"available": False, "note": "TikTok's API does not expose retention or watch-time data."},
        "daily": [{"date": k, **v} for k, v in sorted(by_day.items())],
        "best": [{"tiktok_video_id": r.tiktok_video_id, "title": r.title, "views": r.views, "likes": r.likes,
                  "comments": r.comments, "shares": r.shares, "share_url": r.share_url,
                  "clip_id": str(r.clip_id) if r.clip_id else None,
                  "posted_at": r.posted_at.isoformat() if r.posted_at else None} for r in best],
        "last_synced": max((r.captured_at for r in rows), default=None),
    }
