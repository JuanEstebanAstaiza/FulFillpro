from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Optional
from uuid import UUID

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
      - fallback en memoria (nunca fail-open de abuso)
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
        _local_rate_limit(key, limit, window_seconds)


def rate_limit_from_request(request: Request, scope: str, limit: int, window: int = 60) -> None:
    ip = request.client.host if request.client else "unknown"
    check_rate_limit(f"ratelimit:{scope}:{ip}", limit, window)


def rate_limit_user_and_ip(
    request: Request,
    *,
    scope: str,
    user_id: Optional[UUID] = None,
    per_user: int = 30,
    per_ip: int = 200,
    window: int = 60,
) -> None:
    """
    Doble techo para alta concurrencia multi-perfil:
    - per_user: evita que un solo perfil inunde la cola
    - per_ip: techo de red (oficinas con NAT pueden tener muchos perfiles)
    """
    ip = request.client.host if request.client else "unknown"
    if user_id:
        check_rate_limit(f"ratelimit:{scope}:user:{user_id}", per_user, window)
    check_rate_limit(f"ratelimit:{scope}:ip:{ip}", per_ip, window)
