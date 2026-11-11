"""Evidence-based posting-time recommendations and slot scheduling.

Only real TikTok metrics from this account are used. With too little data the recommender says so and the
scheduler falls back to even spacing under the cooldown — explicitly labelled as not data-driven.
Method: posts grouped into 3-hour blocks of local time; per block, mean log-views shrunk toward the global
mean (empirical Bayes, prior strength 4). Blocks need >= 3 posts and the account >= 10 measured posts.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import AnalyticsSnapshot, PublishingJob, PublishStatus
from app.publishing.policy import capacity
from app.services.app_settings import PublishingSettings

MIN_POSTS = 10
MIN_PER_BLOCK = 3
PRIOR = 4.0
BLOCK_H = 3


def latest_metrics(db: Session) -> list[AnalyticsSnapshot]:
    sub = (select(AnalyticsSnapshot.tiktok_video_id, func.max(AnalyticsSnapshot.captured_at).label("m"))
           .group_by(AnalyticsSnapshot.tiktok_video_id).subquery())
    return list(db.scalars(select(AnalyticsSnapshot).join(
        sub, (AnalyticsSnapshot.tiktok_video_id == sub.c.tiktok_video_id) & (AnalyticsSnapshot.captured_at == sub.c.m)
    )).all())


def recommendations(db: Session, cfg: PublishingSettings) -> dict[str, Any]:
    tz = ZoneInfo(cfg.timezone)
    rows = [r for r in latest_metrics(db) if r.views is not None and r.posted_at is not None]
    # Only posts older than 48 h: younger posts have not accumulated views yet.
    rows = [r for r in rows if r.posted_at is not None and utcnow() - r.posted_at > timedelta(hours=48)]
    base = {"timezone": cfg.timezone, "sample_size": len(rows), "min_required": MIN_POSTS,
            "method": "3-hour blocks, empirical-Bayes shrunk mean of log(views); posts older than 48 h"}
    if len(rows) < MIN_POSTS:
        return {**base, "status": "insufficient_data", "windows": [],
                "note": f"Only {len(rows)} measured posts (need {MIN_POSTS}). No posting-time claim is made yet."}
    values = [math.log1p(r.views or 0) for r in rows]
    gmean = sum(values) / len(values)
    blocks: dict[int, list[AnalyticsSnapshot]] = {}
    for r in rows:
        assert r.posted_at is not None
        blocks.setdefault(r.posted_at.astimezone(tz).hour // BLOCK_H, []).append(r)
    windows = []
    for b, items in blocks.items():
        n = len(items)
        mean = sum(math.log1p(i.views or 0) for i in items) / n
        shrunk = (n * mean + PRIOR * gmean) / (n + PRIOR)
        eng = [((i.likes or 0) + (i.comments or 0) + (i.shares or 0)) / max(1, i.views or 0) for i in items]
        windows.append({"start_hour": b * BLOCK_H, "end_hour": b * BLOCK_H + BLOCK_H, "posts": n,
                        "median_views": sorted(i.views or 0 for i in items)[n // 2],
                        "engagement_rate": round(sum(eng) / n, 4), "score": round(shrunk - gmean, 3),
                        "eligible": n >= MIN_PER_BLOCK})
    windows.sort(key=lambda w: w["score"], reverse=True)
    best = [w for w in windows if w["eligible"] and w["score"] > 0][:3]
    note = ("Blocks ranked by shrunk log-view lift vs. the account average." if best else
            "No time block outperforms the account average with enough posts yet.")
    return {**base, "status": "ok" if best else "no_clear_winner", "windows": windows, "recommended": best,
            "note": note}


def next_slot(db: Session, cfg: PublishingSettings, account_id: Any) -> tuple[datetime, str]:
    cap = capacity(db, cfg, account_id)
    earliest: datetime = cap["next_allowed_at"]
    last_sched = db.scalar(select(func.max(PublishingJob.scheduled_for)).where(
        PublishingJob.tiktok_account_id == account_id,
        PublishingJob.status.in_([PublishStatus.scheduled, PublishStatus.awaiting_confirmation])))
    if last_sched and last_sched + timedelta(minutes=cfg.cooldown_minutes) > earliest:
        earliest = last_sched + timedelta(minutes=cfg.cooldown_minutes)
    if cap["remaining_today"] <= 0:
        earliest = max(earliest, utcnow() + timedelta(hours=6))
    rec = recommendations(db, cfg)
    if rec["status"] != "ok":
        return earliest, ("Earliest time allowed by the cooldown and daily cap "
                          f"(no data-driven recommendation: {rec['note']})")
    tz = ZoneInfo(cfg.timezone)
    loc = earliest.astimezone(tz)
    for day in range(0, 3):
        for w in rec["recommended"]:
            cand = (loc + timedelta(days=day)).replace(hour=w["start_hour"], minute=7, second=0, microsecond=0)
            if cand >= loc:
                return cand.astimezone(earliest.tzinfo), (
                    f"Recommended block {w['start_hour']:02d}:00-{w['end_hour']:02d}:00 {cfg.timezone} "
                    f"(n={w['posts']}, median views {w['median_views']})")
    return earliest, "Earliest allowed time (recommended blocks are more than 3 days away)"
