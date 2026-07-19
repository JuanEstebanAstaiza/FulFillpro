from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from backend.app.config import get_settings
from backend.app.redis_client import get_redis

# Fallback en memoria si Redis no está disponible (fail-closed con techo local)
_lock = threading.Lock()
_local_buckets: dict[str, deque[float]] = defaultdict(deque)


def _local_rate_limit(key: str, limit: int, window_seconds: int) -> None:
    now = time.time()
    with _lock:
        bucket = _local_buckets[key]
        # purgar fuera de ventana
        while bucket and bucket[0] <= now - window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"Demasiadas solicitudes. Intenta de nuevo en {window_seconds}s.",
            )
        bucket.append(now)


def check_rate_limit(key: str, limit: int, window_seconds: int = 60) -> None:
    """
    Rate limit con Redis; si Redis falla:
      - production: fallback en memoria (sigue limitando; fail-closed de abuso)
      - development: también usa fallback en memoria (ya no se deja abierto)
    """
    if limit <= 0:
        return

    try:
        r = get_redis()
        current = r.incr(key)
        if current == 1:
            r.expire(key, window_seconds)
        if current > limit:
            raise HTTPException(
                status_code=429,
                detail=f"Demasiadas solicitudes. Intenta de nuevo en {window_seconds}s.",
            )
        return
    except HTTPException:
        raise
    except Exception:
        # Redis caído → fallback local (nunca fail-open)
        _local_rate_limit(key, limit, window_seconds)


def rate_limit_from_request(request: Request, scope: str, limit: int, window: int = 60) -> None:
    ip = request.client.host if request.client else "unknown"
    # En production no confiar ciegamente en X-Forwarded-For sin proxy trusted
    settings = get_settings()
    if not settings.is_production:
        # opcional: permitir X-Real-IP solo en dev detrás de proxy local
        pass
    check_rate_limit(f"ratelimit:{scope}:{ip}", limit, window)
