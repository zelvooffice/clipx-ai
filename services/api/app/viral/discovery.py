"""Discover popular Lacy content from official, permitted sources (metadata only unless rights allow).

* Twitch Get Clips for the channel (official): title, view_count, created_at, clipper, duration, URL.
  Media rights: only via Get Clips Download with broadcaster/editor authorization.
* YouTube Data API v3 (optional, YOUTUBE_API_KEY): public statistics. Never downloaded.
No statistic is ever invented: a missing field stays null.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import RightsStatus, ViralSourceVideo
from app.services import twitch_service
from app.services.audit import syslog


def popularity(views: int | None, published: datetime | None, now: datetime) -> float:
    """Views per day with mild age decay (log-scaled); 0 when views are unknown."""
    if views is None or published is None:
        return 0.0
    age_days = max(0.5, (now - published).total_seconds() / 86400)
    return round(math.log10(1 + views / (age_days**0.8)), 4)


def _upsert(db: Session, platform: str, external_id: str, **fields: Any) -> ViralSourceVideo:
    row = db.scalar(select(ViralSourceVideo).where(ViralSourceVideo.platform == platform,
                                                   ViralSourceVideo.external_id == external_id))
    if row is None:
        row = ViralSourceVideo(platform=platform, external_id=external_id, **fields)
        db.add(row)
    else:
        for k, v in fields.items():
            setattr(row, k, v)
    return row


async def discover_twitch(db: Session, *, days: int = 30, pages: int = 3) -> int:
    target = await twitch_service.target_broadcaster()
    api = await twitch_service.app_helix()
    now = datetime.now(UTC)
    can_download = twitch_service.best_account(db, needs="download") is not None
    after, n = None, 0
    for _ in range(pages):
        clips, after = await api.get_clips(broadcaster_id=target["id"],
                                           started_at=(now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                           ended_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"), first=100, after=after)
        for c in clips:
            published = datetime.fromisoformat(c["created_at"].replace("Z", "+00:00"))
            _upsert(db, "twitch", c["id"], source_url=c["url"], title=c.get("title"),
                    creator=c.get("creator_name"), published_at=published, view_count=c.get("view_count"),
                    engagement=None, duration_s=c.get("duration"),
                    popularity_score=popularity(c.get("view_count"), published, now),
                    rights_status=RightsStatus.authorized if can_download else RightsStatus.metadata_only,
                    discovered_via="Twitch Get Clips (official API)", fetched_at=now)
            n += 1
        if not after:
            break
    return n


async def discover_youtube(db: Session, *, query: str = "Lacy", days: int = 30) -> int:
    key = get_settings().youtube_api_key
    if not key:
        return 0
    now = datetime.now(UTC)
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get("https://www.googleapis.com/youtube/v3/search", params={
            "part": "snippet", "q": query, "type": "video", "order": "viewCount", "maxResults": 25,
            "publishedAfter": (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"), "key": key})
        r.raise_for_status()
        ids = [i["id"]["videoId"] for i in r.json().get("items", [])]
        if not ids:
            return 0
        r = await c.get("https://www.googleapis.com/youtube/v3/videos", params={
            "part": "snippet,statistics", "id": ",".join(ids), "key": key})
        r.raise_for_status()
    n = 0
    for v in r.json().get("items", []):
        st, sn = v.get("statistics", {}), v.get("snippet", {})
        views = int(st["viewCount"]) if "viewCount" in st else None
        published = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")) if sn.get("publishedAt") else None
        eng = {k: int(st[k]) for k in ("likeCount", "commentCount") if k in st} or None
        _upsert(db, "youtube", v["id"], source_url=f"https://www.youtube.com/watch?v={v['id']}",
                title=sn.get("title"), creator=sn.get("channelTitle"), published_at=published, view_count=views,
                engagement=eng, duration_s=None, popularity_score=popularity(views, published, now),
                rights_status=RightsStatus.metadata_only, discovered_via="YouTube Data API v3 (metadata only)",
                fetched_at=now)
        n += 1
    return n


async def discover(db: Session) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        out["twitch"] = await discover_twitch(db)
    except Exception as exc:
        out["twitch_error"] = str(exc)
    try:
        out["youtube"] = await discover_youtube(db)
    except Exception as exc:
        out["youtube_error"] = str(exc)
    syslog(db, "INFO", "viral", "discovery", f"Viral discovery: {out}")
    return out
