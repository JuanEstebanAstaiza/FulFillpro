"""Cliente Redis compartido con pool de conexiones (thread/process-safe)."""
from __future__ import annotations

import json
from typing import Any, Optional

import redis
from redis.connection import ConnectionPool

from backend.app.config import get_settings

_pool: Optional[ConnectionPool] = None
_client: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    """
    Cliente Redis reutilizable.

    - ConnectionPool con techo de conexiones (API multi-worker + workers Excel).
    - Timeouts cortos para no colgar hilos HTTP bajo carga.
    - decode_responses=True para jobs/rate-limit en strings.
    """
    global _pool, _client
    if _client is None:
        settings = get_settings()
        max_conn = int(getattr(settings, "redis_max_connections", 100) or 100)
        max_conn = max(10, min(max_conn, 500))
        _pool = ConnectionPool.from_url(
            settings.redis_url,
            max_connections=max_conn,
            socket_connect_timeout=2.0,
            socket_timeout=5.0,
            socket_keepalive=True,
            health_check_interval=30,
            retry_on_timeout=True,
            decode_responses=True,
        )
        _client = redis.Redis(connection_pool=_pool)
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
        for key in r.scan_iter(f"{prefix}*", count=100):
            r.delete(key)
    except Exception:
        pass
