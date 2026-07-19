"""
Cola de procesamiento de Excel en Redis.

Diseño para 100+ envíos concurrentes sin tumbar la API:
- La API solo valida, guarda y encola (HTTP 202).
- Los workers consumen con BL POP y concurrencia acotada (RAM de Excel).
- Encolado atómico con Lua (backpressure por profundidad de cola).
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional
from uuid import UUID

from backend.app.redis_client import get_redis

QUEUE_KEY = "fulfillpro:queue:process"
JOB_KEY = "fulfillpro:job:{order_id}"
QUEUE_META = "fulfillpro:queue:meta"
PROCESSING_SET = "fulfillpro:queue:processing"
DEFAULT_MAX_QUEUE = 500

# Encolado atómico: comprueba profundidad, RPUSH y HSET job en un solo script
_ENQUEUE_LUA = """
local q = KEYS[1]
local job_key = KEYS[2]
local meta = KEYS[3]
local maxq = tonumber(ARGV[1])
local payload = ARGV[2]
local job_fields = ARGV[3]
local ttl = tonumber(ARGV[4])

local depth = redis.call('LLEN', q)
if maxq > 0 and depth >= maxq then
  return {-1, depth}
end

redis.call('RPUSH', q, payload)
local fields = cjson.decode(job_fields)
for k, v in pairs(fields) do
  redis.call('HSET', job_key, k, tostring(v))
end
redis.call('EXPIRE', job_key, ttl)
redis.call('HINCRBY', meta, 'enqueued_total', 1)
local new_depth = depth + 1
return {new_depth, new_depth}
"""


def _job_key(order_id: str | UUID) -> str:
    return JOB_KEY.format(order_id=str(order_id))


def enqueue_process_job(
    *,
    order_id: UUID,
    user_id: UUID,
    license_code: str = "",
    ip: str = "",
    max_queue: int = DEFAULT_MAX_QUEUE,
) -> dict[str, Any]:
    """
    Encola un job de proceso. Lanza HTTPException 503 si la cola está llena.
    """
    from fastapi import HTTPException

    r = get_redis()
    payload = {
        "order_id": str(order_id),
        "user_id": str(user_id),
        "license_code": license_code or "",
        "ip": ip or "",
        "enqueued_at": time.time(),
    }
    job = {
        "order_id": str(order_id),
        "status": "queued",
        "progress": "0",
        "stage": "En cola",
        "error": "",
        "user_id": str(user_id),
        "enqueued_at": str(time.time()),
    }
    ttl = 7 * 24 * 3600

    try:
        # Preferir Lua atómico (cjson disponible en Redis 7)
        result = r.eval(
            _ENQUEUE_LUA,
            3,
            QUEUE_KEY,
            _job_key(order_id),
            QUEUE_META,
            int(max_queue or 0),
            json.dumps(payload),
            json.dumps(job),
            ttl,
        )
        new_depth = int(result[0])
        if new_depth < 0:
            depth = int(result[1]) if len(result) > 1 else queue_depth()
            raise HTTPException(
                503,
                f"Cola de procesamiento llena ({depth}/{max_queue}). "
                "Reintenta en unos segundos; la plataforma sigue aceptando cuando haya cupo.",
            )
        return {
            "order_id": str(order_id),
            "status": "queued",
            "queue_position": new_depth,
            "queue_depth": new_depth,
        }
    except HTTPException:
        raise
    except Exception:
        # Fallback no atómico si EVAL/cjson falla
        return _enqueue_fallback(r, order_id, user_id, payload, job, max_queue, ttl)


def _enqueue_fallback(
    r,
    order_id: UUID,
    user_id: UUID,
    payload: dict,
    job: dict,
    max_queue: int,
    ttl: int,
) -> dict[str, Any]:
    from fastapi import HTTPException

    depth = int(r.llen(QUEUE_KEY) or 0)
    if max_queue > 0 and depth >= max_queue:
        raise HTTPException(
            503,
            f"Cola de procesamiento llena ({depth}/{max_queue}). "
            "Reintenta en unos segundos; la plataforma sigue aceptando cuando haya cupo.",
        )
    pipe = r.pipeline()
    pipe.hset(_job_key(order_id), mapping={k: str(v) for k, v in job.items()})
    pipe.expire(_job_key(order_id), ttl)
    pipe.rpush(QUEUE_KEY, json.dumps(payload))
    pipe.hincrby(QUEUE_META, "enqueued_total", 1)
    pipe.execute()
    return {
        "order_id": str(order_id),
        "status": "queued",
        "queue_position": depth + 1,
        "queue_depth": depth + 1,
    }


def update_job(order_id: str | UUID, **fields: Any) -> None:
    r = get_redis()
    key = _job_key(order_id)
    if not fields:
        return
    mapping = {k: str(v) if v is not None else "" for k, v in fields.items()}
    pipe = r.pipeline()
    pipe.hset(key, mapping=mapping)
    pipe.expire(key, 7 * 24 * 3600)
    pipe.execute()


def get_job(order_id: str | UUID) -> Optional[dict[str, str]]:
    try:
        data = get_redis().hgetall(_job_key(order_id))
        return data or None
    except Exception:
        return None


def queue_depth() -> int:
    try:
        return int(get_redis().llen(QUEUE_KEY) or 0)
    except Exception:
        return 0


def processing_count() -> int:
    try:
        return int(get_redis().scard(PROCESSING_SET) or 0)
    except Exception:
        return 0


def mark_processing(order_id: str | UUID) -> None:
    try:
        r = get_redis()
        pipe = r.pipeline()
        pipe.sadd(PROCESSING_SET, str(order_id))
        pipe.expire(PROCESSING_SET, 24 * 3600)
        pipe.execute()
    except Exception:
        pass


def clear_processing(order_id: str | UUID) -> None:
    try:
        get_redis().srem(PROCESSING_SET, str(order_id))
    except Exception:
        pass


def dequeue_blocking(timeout: int = 5) -> Optional[dict[str, Any]]:
    """Bloquea hasta timeout segundos; devuelve payload del job o None."""
    try:
        r = get_redis()
        item = r.blpop(QUEUE_KEY, timeout=timeout)
        if not item:
            return None
        _key, raw = item
        payload = json.loads(raw)
        oid = payload.get("order_id")
        if oid:
            mark_processing(oid)
        return payload
    except Exception:
        return None


def requeue(payload: dict[str, Any]) -> None:
    """Reencola al frente en caso de fallo recuperable del worker."""
    try:
        oid = payload.get("order_id")
        if oid:
            clear_processing(oid)
        get_redis().lpush(QUEUE_KEY, json.dumps(payload))
    except Exception:
        pass


def queue_stats() -> dict[str, Any]:
    try:
        r = get_redis()
        meta = r.hgetall(QUEUE_META) or {}
        return {
            "queue_depth": int(r.llen(QUEUE_KEY) or 0),
            "processing": int(r.scard(PROCESSING_SET) or 0),
            "enqueued_total": int(meta.get("enqueued_total") or 0),
            "completed_total": int(meta.get("completed_total") or 0),
            "failed_total": int(meta.get("failed_total") or 0),
        }
    except Exception:
        return {
            "queue_depth": 0,
            "processing": 0,
            "enqueued_total": 0,
            "completed_total": 0,
            "failed_total": 0,
        }


def incr_meta(field: str, amount: int = 1) -> None:
    try:
        get_redis().hincrby(QUEUE_META, field, amount)
    except Exception:
        pass
