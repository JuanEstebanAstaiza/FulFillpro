from __future__ import annotations

from fastapi import HTTPException, Request

from backend.app.redis_client import get_redis


def check_rate_limit(key: str, limit: int, window_seconds: int = 60) -> None:
    """Incrementa contador en Redis; lanza 429 si se supera el límite."""
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
    except HTTPException:
        raise
    except Exception:
        # Si Redis cae, no bloqueamos el negocio
        pass


def rate_limit_from_request(request: Request, scope: str, limit: int, window: int = 60) -> None:
    ip = request.client.host if request.client else "unknown"
    check_rate_limit(f"ratelimit:{scope}:{ip}", limit, window)
