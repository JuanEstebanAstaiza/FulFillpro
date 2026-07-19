"""
Prueba de estabilidad: 100+ encolados concurrentes de Excel.

No mide throughput de Excel (eso es WORKER_CONCURRENCY × réplicas),
sino que la API acepte 100+ envíos simultáneos sin colapsar (202 + cola).

Uso:
  python scripts/concurrent_enqueue_probe.py \\
    --base http://localhost:8000 \\
    --file samples/ordenes_muestra.xlsx \\
    --concurrency 100 \\
    --email empresa@demo.com \\
    --password DemoEmpresa2026!
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def login(base: str, email: str, password: str) -> str:
    data = json.dumps({"email": email, "password": password}).encode()
    req = urllib.request.Request(
        f"{base.rstrip('/')}/api/auth/login",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode())
    token = body.get("access_token")
    if not token:
        raise SystemExit(f"Login falló: {body}")
    return token


def enqueue_one(base: str, token: str, file_bytes: bytes, filename: str) -> tuple[bool, int, float, str]:
    boundary = "----FulfillProBoundary7MA4YWxk"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n"
    ).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        f"{base.rstrip('/')}/api/process",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            status = resp.status
            raw = resp.read().decode()
            ms = (time.perf_counter() - t0) * 1000
            ok = status in (200, 202)
            return ok, status, ms, raw[:200]
    except urllib.error.HTTPError as e:
        ms = (time.perf_counter() - t0) * 1000
        try:
            detail = e.read().decode()[:200]
        except Exception:
            detail = str(e)
        return False, e.code, ms, detail
    except Exception as e:
        ms = (time.perf_counter() - t0) * 1000
        return False, 0, ms, str(e)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://localhost:8000")
    p.add_argument("--file", default="samples/ordenes_muestra.xlsx")
    p.add_argument("--concurrency", type=int, default=100)
    p.add_argument("--email", default="empresa@demo.com")
    p.add_argument("--password", default="DemoEmpresa2026!")
    args = p.parse_args()

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"No existe {path}")
    file_bytes = path.read_bytes()
    print(f"Login {args.email} @ {args.base}…")
    token = login(args.base, args.email, args.password)
    print(f"Encolando {args.concurrency} jobs concurrentes ({path.name}, {len(file_bytes)} bytes)…")

    t0 = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = [
            ex.submit(enqueue_one, args.base, token, file_bytes, path.name)
            for _ in range(args.concurrency)
        ]
        for f in as_completed(futs):
            results.append(f.result())
    duration = time.perf_counter() - t0

    ok = sum(1 for r in results if r[0])
    fail = len(results) - ok
    lat = sorted(r[2] for r in results)
    codes: dict[int, int] = {}
    for r in results:
        codes[r[1]] = codes.get(r[1], 0) + 1

    def pct(p: float) -> float:
        if not lat:
            return 0.0
        return round(lat[min(int(len(lat) * p), len(lat) - 1)], 1)

    print("--- Resultado ---")
    print(f"total={len(results)} ok={ok} fail={fail} duration={duration:.2f}s")
    print(f"rps={len(results)/max(duration,1e-6):.1f}")
    print(f"latency_ms p50={pct(0.5)} p95={pct(0.95)} p99={pct(0.99)} max={round(lat[-1],1) if lat else 0}")
    print(f"status_codes={codes}")
    if fail:
        sample = [r for r in results if not r[0]][:3]
        print("sample_errors:", sample)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
