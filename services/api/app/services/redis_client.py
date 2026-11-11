from __future__ import annotations

from functools import lru_cache

from redis import Redis

from app.config import get_settings


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


def acquire_lock(name: str, owner: str, ttl_ms: int) -> bool:
    return bool(get_redis().set(f"lock:{name}", owner, nx=True, px=ttl_ms))


def refresh_lock(name: str, owner: str, ttl_ms: int) -> bool:
    script = (
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end"
    )
    return bool(get_redis().eval(script, 1, f"lock:{name}", owner, ttl_ms))


def release_lock(name: str, owner: str) -> None:
    script = (
        "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
    )
    get_redis().eval(script, 1, f"lock:{name}", owner)
