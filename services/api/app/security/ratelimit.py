from __future__ import annotations

import time

from redis import Redis

from app.services.redis_client import get_redis


class RateLimiter:
    """Fixed-window limiter backed by Redis (works across API replicas)."""

    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis

    @property
    def redis(self) -> Redis:
        return self._redis or get_redis()

    def hit(self, key: str, limit: int, window_s: int = 60) -> tuple[bool, int]:
        bucket = int(time.time() // window_s)
        rkey = f"rl:{key}:{bucket}"
        pipe = self.redis.pipeline()
        pipe.incr(rkey)
        pipe.expire(rkey, window_s + 5)
        count = int(pipe.execute()[0])
        return count <= limit, max(0, limit - count)
