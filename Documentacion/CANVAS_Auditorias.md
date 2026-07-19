# Canvas — Auditorías FulfillPro (carga + OWASP)

Abrir cada informe como canvas / documento de trabajo:

| # | Canvas / informe | Ruta | Objetivo |
|---|------------------|------|----------|
| 1 | **Capacidad y carga** | [`Auditoria_Capacidad_Carga_12GB.md`](./Auditoria_Capacidad_Carga_12GB.md) | Máxima carga con **12 GB RAM** y **100 GB** disco; clientes simultáneos; Excel concurrente |
| 2 | **Seguridad OWASP Top 10** | [`Auditoria_Seguridad_OWASP_Top10.md`](./Auditoria_Seguridad_OWASP_Top10.md) | Cumplimiento OWASP 2021 y plan de endurecimiento |

## Perfil de infraestructura aplicado

```
RAM total stack : 12 GB
  ├─ api        : 7 GB  (3 workers Uvicorn)
  ├─ postgres   : 4 GB  (max_connections=100)
  ├─ redis      : 0.5 GB (maxmemory 400 MB)
  └─ holgura    : 0.5 GB

Disco datos     : 100 GB (política STORAGE_MAX_GB + volúmenes host)
```

Configurado en `docker-compose.yml`, `Dockerfile` y `.env.example`.

## Cifras clave (carga)

| Métrica | Valor |
|---------|--------|
| Process Excel simultáneos | **3** |
| Usuarios UI concurrentes (confort) | **80–120** |
| Techo API ligera | **~200** (`limit-concurrency`) |
| Health RPS medido | **~1130** (p95 ~76 ms) |
| Órdenes en 100 GB (≈6 MB c/u) | **~15k–20k** sin purga |

## Cifras clave (seguridad OWASP) — post-remediación

| Estado | Categorías |
|--------|------------|
| Cumple | A03 (residual bajo), A10 SSRF |
| Parcial mejorado | A01, A02, A04, A05*, A07 |
| Parcial | A06, A08, A09 |

\*A05: **fail-fast** en production si secretos/CORS inseguros; headers CSP/XFO activos.

**Críticos ya corregidos en código:** secretos default (prod), CORS `*`, headers, rate-limit fail-open, consentimiento backend, cupo solo servidor, path sandbox, upload 50 MB + magic bytes, demos solo en dev.

**Pendiente ops pre-Internet:** TLS en reverse proxy, MFA admin, SCA en CI, cookies HttpOnly.

## Cómo regenerar la sonda de carga

```bash
docker compose up -d --build
docker compose cp scripts/load_capacity_probe.py api:/tmp/load_capacity_probe.py
docker compose exec -T api python /tmp/load_capacity_probe.py --base http://127.0.0.1:8000 --concurrency 40 --requests 120
```
