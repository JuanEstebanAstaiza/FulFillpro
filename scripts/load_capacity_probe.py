"""
Sonda de capacidad de FulfillPro (perfil 12 GB RAM / 100 GB disco).

Mide:
  - RPS y latencia en endpoints ligeros (health, login)
  - Concurrencia sostenida
  - Estimación de clientes simultáneos y de procesos Excel

Uso (desde el host o el contenedor api):
  python scripts/load_capacity_probe.py --base http://localhost:8000 --concurrency 50
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass


@dataclass
class ProbeResult:
    name: str
    concurrency: int
    total_requests: int
    ok: int
    errors: int
    duration_s: float
    rps: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    latency_avg_ms: float


def _request(url: str, method: str = "GET", data: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            ms = (time.perf_counter() - t0) * 1000
            return True, ms, resp.status, len(body)
    except Exception as e:
        ms = (time.perf_counter() - t0) * 1000
        return False, ms, 0, str(e)


def run_batch(name: str, fn, concurrency: int, total: int) -> ProbeResult:
    latencies: list[float] = []
    ok = 0
    errors = 0
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = [ex.submit(fn) for _ in range(total)]
        for f in as_completed(futs):
            success, ms, *_ = f.result()
            latencies.append(ms)
            if success:
                ok += 1
            else:
                errors += 1
    duration = max(time.perf_counter() - t0, 1e-6)
    latencies.sort()

    def pct(p: float) -> float:
        if not latencies:
            return 0.0
        idx = min(int(len(latencies) * p), len(latencies) - 1)
        return round(latencies[idx], 2)

    return ProbeResult(
        name=name,
        concurrency=concurrency,
        total_requests=total,
        ok=ok,
        errors=errors,
        duration_s=round(duration, 3),
        rps=round(ok / duration, 2),
        latency_p50_ms=pct(0.50),
        latency_p95_ms=pct(0.95),
        latency_p99_ms=pct(0.99),
        latency_avg_ms=round(statistics.mean(latencies), 2) if latencies else 0.0,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--concurrency", type=int, default=40)
    ap.add_argument("--requests", type=int, default=200)
    args = ap.parse_args()
    base = args.base.rstrip("/")

    results: list[ProbeResult] = []

    # 1) Health concurrente
    results.append(
        run_batch(
            "GET /health",
            lambda: _request(f"{base}/health"),
            concurrency=args.concurrency,
            total=args.requests,
        )
    )

    # 2) Login (rate limited) — menos requests
    login_body = json.dumps(
        {"email": "empresa@demo.com", "password": "DemoEmpresa2026!"}
    ).encode()
    results.append(
        run_batch(
            "POST /api/auth/login (company)",
            lambda: _request(
                f"{base}/api/auth/login",
                method="POST",
                data=login_body,
                headers={"Content-Type": "application/json"},
            ),
            concurrency=min(10, args.concurrency),
            total=min(40, args.requests),
        )
    )

    # 3) Estimación de capacidad Excel (modelo, no ejecución pesada)
    # Medición de un process síncrono requiere archivo; aquí se documenta en el informe.
    model = {
        "stack_ram_gb": 12,
        "storage_gb": 100,
        "allocation": {"api_gb": 7, "postgres_gb": 4, "redis_gb": 0.5, "headroom_gb": 0.5},
        "uvicorn_workers": 3,
        "db_pool_size": 20,
        "db_max_overflow": 20,
        "max_db_connections_app": 40,
        "postgres_max_connections": 100,
        "assumptions": {
            "excel_process_peak_mb_per_job": 700,
            "excel_avg_seconds_10k_rows": 8,
            "excel_avg_seconds_60k_rows": 35,
            "worker_base_mb": 250,
            "login_rps_observed_hint": "ver probe login (rate limit)",
        },
        "derived": {},
    }
    # Capacidad concurrente de process Excel (peor caso 700 MB)
    api_usable_mb = 7 * 1024 - 3 * 250  # workers base
    concurrent_excel = max(1, int(api_usable_mb / 700))
    # Con 3 workers síncronos, el techo real de process concurrentes ≈ workers
    concurrent_excel_workers = 3
    model["derived"] = {
        "concurrent_excel_by_memory": concurrent_excel,
        "concurrent_excel_by_workers": concurrent_excel_workers,
        "recommended_simultaneous_process": min(concurrent_excel, concurrent_excel_workers),
        "light_api_clients_estimate": "ver RPS health × sesión activa 5–15 min",
        "storage_orders_estimate": "ver informe (100 GB)",
    }

    out = {
        "probes": [asdict(r) for r in results],
        "capacity_model": model,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
