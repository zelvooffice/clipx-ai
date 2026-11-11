"""Per-second chat aggregation in Redis (counts only — message text is not stored)."""

from __future__ import annotations

from app.detection.signals import ChatSecond, classify_message
from app.services.redis_client import get_redis

TTL = 6 * 3600


def record(session_id: str, second: int, user_id: str, text: str) -> None:
    laugh, hype, clip = classify_message(text)
    r = get_redis()
    key = f"chat:{session_id}:{second}"
    pipe = r.pipeline()
    pipe.hincrby(key, "n", 1)
    if laugh:
        pipe.hincrby(key, "laugh", 1)
    if hype:
        pipe.hincrby(key, "hype", 1)
    if clip:
        pipe.hincrby(key, "clip", 1)
    pipe.expire(key, TTL)
    pipe.pfadd(f"chatu:{session_id}:{second}", user_id)
    pipe.expire(f"chatu:{session_id}:{second}", TTL)
    pipe.set(f"chat:{session_id}:last", second, ex=TTL)
    pipe.execute()


def window(session_id: str, start_s: int, end_s: int) -> list[ChatSecond]:
    r = get_redis()
    pipe = r.pipeline()
    secs = list(range(max(0, start_s), max(0, end_s)))
    for s in secs:
        pipe.hgetall(f"chat:{session_id}:{s}")
        pipe.pfcount(f"chatu:{session_id}:{s}")
    raw = pipe.execute()
    out = []
    for i in range(len(secs)):
        h, u = raw[2 * i] or {}, raw[2 * i + 1] or 0
        out.append(ChatSecond(int(h.get("n", 0)), int(h.get("laugh", 0)), int(h.get("hype", 0)),
                              int(h.get("clip", 0)), int(u)))
    return out


def has_data(session_id: str) -> bool:
    return get_redis().exists(f"chat:{session_id}:last") == 1
