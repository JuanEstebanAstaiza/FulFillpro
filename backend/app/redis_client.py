from __future__ import annotations

import json
from typing import Any, Optional

import redis

from backend.app.config import get_settings

_client: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        settings = get_settings()
        _client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    return _client


def redis_ping() -> bool:
    try:
        return bool(get_redis().ping())
    except Exception:
        return False


def cache_get(key: str) -> Optional[Any]:
    try:
        raw = get_redis().get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        return None


def cache_set(key: str, value: Any, ttl: int = 60) -> None:
    try:
        get_redis().setex(key, ttl, json.dumps(value, default=str))
    except Exception:
        pass


def cache_delete(key: str) -> None:
    try:
        get_redis().delete(key)
    except Exception:
        pass


def cache_delete_prefix(prefix: str) -> None:
    try:
        r = get_redis()
        for key in r.scan_iter(f"{prefix}*"):
            r.delete(key)
    except Exception:
        pass
