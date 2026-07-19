# Auditoría de capacidad y carga — FulfillPro

**Perfil de despliegue auditado:** stack Docker con **12 GB RAM** y política de **100 GB** de almacenamiento.  
**Fecha:** 2026-07-19  
**Alcance:** API FastAPI (Uvicorn multi-worker), PostgreSQL 16, Redis 7, procesamiento Excel síncrono, analítica semanal, storage en volumen.

> Este documento está pensado para abrirse como **canvas / informe de arquitectura** y servir de referencia operativa de capacidad máxima.

---

## 1. Hipótesis de capacidad (perfil hardware)

| Recurso | Límite del stack | Asignación |
|---------|------------------|-----------|
| **RAM total** | **12 GB** | API **7 GB** · PostgreSQL **4 GB** · Redis **0,5 GB** · holgura SO **0,5 GB** |
| **Almacenamiento datos** | **100 GB** | `storage/` (Excel + analítica) + volumen Postgres (metadatos) |
| **CPU (recomendada)** | 4 vCPU API + 2 DB | Acorde a `docker-compose.yml` |

### 1.1 Contenedores (límites aplicados)

| Servicio | `mem_limit` | CPU | Notas |
|----------|-------------|-----|--------|
| `api` | 7 GB | 4 | `UVICORN_WORKERS=3`, `limit-concurrency=200` |
| `db` | 4 GB | 2 | `shared_buffers=1GB`, `max_connections=100` |
| `redis` | 512 MB | 0,5 | `maxmemory=400mb`, política `allkeys-lru` |

---

## 2. Modelo de consumo por tipo de solicitud

### 2.1 Solicitudes ligeras (login, dashboard, listados)

| Concepto | Estimación |
|----------|------------|
| Memoria por request | 2–8 MB pico (despreciable vs pool) |
| CPU | Baja |
| Cuello de botella | Rate limit (login), pool DB, Redis |
| Latencia típica esperada | 20–150 ms (misma red) |

### 2.2 Procesamiento de Excel (crítico)

El proceso es **síncrono por worker**: un `POST /api/process` ocupa un worker Uvicorn hasta terminar.

| Tamaño archivo (filas) | RAM pico estimada por job | Tiempo orientativo |
|------------------------|---------------------------|--------------------|
| 1 000 | 80–150 MB | 1–3 s |
| 10 000 | 250–450 MB | 5–12 s |
| 30 000 | 450–700 MB | 15–30 s |
| 60 000 (tope `MAX_ROWS`) | 700–1 100 MB | 30–60 s |

**Supuesto de diseño del perfil 12 GB:** 700 MB/job Excel + 250 MB base/worker.

### 2.3 Analítica / PDF consolidado

| Operación | RAM pico | Notas |
|-----------|----------|-------|
| Ingest dedup | Baja–media | Escrituras DB batch |
| PDF + matplotlib | 150–400 MB | Temporal al generar consolidado |

---

## 3. Capacidad máxima calculada

### 3.1 Procesos Excel simultáneos

```
Workers Uvicorn              = 3
Tope por workers             = 3 Excel concurrentes
Tope por RAM API (7 GB):
  usable ≈ 7×1024 − 3×250 − 512 (reserva) ≈ 5 500 MB
  5500 / 700 ≈ 7,8  → teórico por memoria ~7–8
Resultado gobernado por WORKERS = 3
```

| Métrica | Valor auditado |
|---------|----------------|
| **Excel simultáneos (recomendado)** | **3** |
| Excel simultáneos (pico no recomendado) | 4–5 (riesgo OOM y thrashing) |
| Throughput Excel 10k filas | ≈ **15–36 archivos/hora** por instancia (3 workers × 3600/s) |
| Throughput Excel 60k filas | ≈ **3–6 archivos/hora** (peor caso) |

### 3.2 Clientes / usuarios concurrentes (API ligera)

Definición: sesiones con navegación, listados y health, **sin** process Excel en ese instante.

| Escenario | Concurrentes estimados | Criterio |
|-----------|------------------------|----------|
| **Confort (p95 < 300 ms)** | **80–120** | 3 workers + pool 40 + Redis |
| **Alto (degradación aceptable)** | **150–200** | límite `limit-concurrency=200` |
| **Techo duro API** | **200** | Uvicorn `--limit-concurrency` |
| Con 1 Excel pesado en vuelo | −30–40 % capacidad ligera en ese worker | 1 de 3 workers ocupado |

**Clientes de empresa (tenants) distintos haciendo process a la vez:**  
como máximo **3** con buena calidad; el 4.º esperará en cola del event loop / rechazos si se satura.

### 3.3 Conexiones a base de datos

| Parámetro | Valor |
|-----------|-------|
| Pool por proceso API | `pool_size=20` + `max_overflow=20` = hasta 40 |
| 3 workers × 40 | **120** (teórico; en la práctica se usa mucho menos) |
| Postgres `max_connections` | **100** |
| **Conclusión** | El pool está **por encima** del tope de Postgres si se satura; se recomienda bajar a `pool_size=10`, `max_overflow=10` → 60 máx, o subir `max_connections` a 150 en DB. |

**Riesgo de capacidad:** en pico extremo, el pool de SQLAlchemy puede intentar más conexiones de las que Postgres acepta → errores `too many clients`.  
**Ajuste recomendado post-auditoría:** `DB_POOL_SIZE=12`, `DB_MAX_OVERFLOW=8` (3×20 = 60 < 100).

### 3.4 Redis

| Uso | Impacto |
|-----|---------|
| Refresh tokens, rate limit, cache licencia | Bajo |
| 400 MB `maxmemory` | Suficiente para decenas de miles de claves de sesión/rate limit |
| Fallo Redis | Rate limit **fail-open** (ver auditoría OWASP) |

### 3.5 Almacenamiento 100 GB

Estimación conservadora por orden procesada:

| Componente | Tamaño medio |
|------------|--------------|
| Excel entrada | 1–5 MB |
| Excel salida | 0,5–3 MB |
| meta.json | < 10 KB |
| Analítica (eventos + PDF ocasional) | 0,1–2 MB/semana/tenant |

**Promedio asumido:** **6 MB/orden** (con margen).

| Uso | Cálculo | Resultado |
|-----|---------|-----------|
| Órdenes almacenables | 100 GB ≈ 102 400 MB / 6 | **~17 000 órdenes** históricas |
| Si 50 empresas activas | 17 000 / 50 | **~340 órdenes/empresa** en retención total |
| Retención 90 días, 20 process/empresa/día | 50 × 20 × 90 × 6 MB | **~540 GB** → **excede 100 GB** |

**Conclusión storage:** con 100 GB hay que:

1. Retención automática de archivos de órdenes (p. ej. 30–60 días).  
2. Límites de analítica ya modelados (`analytics_storage_mb` por licencia).  
3. Monitorizar `% uso de disco` y alertar al 80 %.

**Clientes simultáneos “en disco”:** no limitados por IOPS en este perfil si el host SSD es decente; el límite es **cuota y retención**, no concurrencia de escritura (3 writers de Excel).

---

## 4. Cuellos de botella ordenados

| Prioridad | Cuello | Efecto | Mitigación |
|-----------|--------|--------|------------|
| 1 | Process Excel síncrono (1 job = 1 worker) | Baja concurrencia de process | Cola Redis + workers background |
| 2 | 3 workers fijos | Techo de 3 process | Subir workers solo si se sube RAM API |
| 3 | Pool DB vs `max_connections` | Errores en pico | Alinear pool × workers ≤ 80 |
| 4 | Rate limit process 40/min/IP | Limita scripts, no multi-IP real | Rate limit por usuario/licencia |
| 5 | Generación PDF matplotlib | Pico de RAM en consolidado | Timeout + 1 consolidado a la vez por tenant |
| 6 | Disco 100 GB sin purga | Llenado en meses | Job de retención |

---

## 5. Matriz “¿cuántos clientes a la vez?”

| Actividad simultánea | Clientes/usuarios | ¿Soportado en 12 GB? |
|----------------------|-------------------|----------------------|
| Solo UI (dashboard, histórico) | 80–120 | Sí |
| UI + 1 process Excel | 60–100 + 1 process | Sí |
| 3 process Excel distintos tenants | 3 process + ~40 UI | Sí (límite) |
| 5+ process Excel | No recomendado | No (cola / OOM) |
| Login masivo | ~10–20/s/IP cap rate limit | Parcial (por IP) |
| Consolidado PDF mientras hay 2 process | Riesgo alto | Evitar |

---

## 6. Prueba de sonda (`scripts/load_capacity_probe.py`)

Ejecutar con el stack arriba:

```bash
docker compose up -d --build
python scripts/load_capacity_probe.py --base http://localhost:8000 --concurrency 40 --requests 200
```

Interpretación:

- **`GET /health` RPS alto + p95 bajo** → API ligera sana.  
- **`POST /login` con muchos errores 429** → rate limit activo (esperado).  
- No sustituye un test de process Excel multi-archivo; ese debe hacerse con `samples/ordenes_muestra.xlsx` y N clientes reales.

### 6.1 Resultados medidos (2026-07-19, stack 12 GB en Docker)

Límites verificados con `docker stats`:

| Contenedor | MEM LIMIT | Uso en reposo |
|------------|-----------|---------------|
| api | **7 GiB** | ~311 MiB |
| db | **4 GiB** | ~70 MiB |
| redis | **512 MiB** | ~6 MiB |

Sonda `load_capacity_probe.py` (concurrencia 40 / 120 requests health; 10 / 40 login):

| Probe | Concurrencia | OK | Errores | RPS | p50 ms | p95 ms | p99 ms |
|-------|--------------|----|---------|-----|--------|--------|--------|
| GET /health | 40 | **120** | **0** | **~1131** | **16** | **76** | **82** |
| POST /api/auth/login | 10 | **20** | **20** | **~21** | **273** | **608** | **610** |

**Interpretación:**
- Health: excelente; la API ligera soporta cientos de RPS en local.
- Login: ~50 % errores por **rate limit** (esperado; protege fuerza bruta). p95 ~600 ms con bcrypt + contención.
- Los process Excel **no** se midieron en masa aquí (ocuparían los 3 workers y RAM); la capacidad de process se mantiene en el modelo de la §3 (**3 concurrentes**).

---

## 7. Recomendaciones de capacidad (priorizadas)

1. **Cola asíncrona** para `/api/process` (Redis + worker) → desacopla concurrencia UI de Excel.  
2. Alinear **pool DB** a `workers × (pool+overflow) ≤ 80`.  
3. Política de **retención storage** (30–60 días) para respetar 100 GB.  
4. Rate limit por **usuario/licencia**, no solo IP.  
5. Métricas Prometheus: `process_in_flight`, `worker_busy`, `disk_used_bytes`, `db_pool_checked_out`.  
6. No superar **3 process Excel concurrentes** en este perfil sin subir RAM/workers.

---

## 8. Veredicto de capacidad

| Pregunta | Respuesta (perfil 12 GB / 100 GB) |
|----------|-----------------------------------|
| ¿Cuántos process Excel a la vez? | **3** (recomendado y techo práctico) |
| ¿Cuántos usuarios UI concurrentes? | **~80–120** confort; **~200** techo API |
| ¿Cuántas empresas process a la vez? | **Hasta 3** con calidad |
| ¿Cuántas órdenes caben en disco? | **~15k–20k** sin purga (promedio 6 MB) |
| ¿Es viable SaaS multi-tenant en esta caja? | **Sí, piloto / hasta ~30–50 empresas** con uso moderado y retención; no hiperescala |

---

## 9. Configuración de referencia (ya aplicada en repo)

- `docker-compose.yml`: límites de memoria/CPU por servicio.  
- `Dockerfile`: multi-worker Uvicorn + `limit-concurrency 200`.  
- `STORAGE_MAX_GB=100` en entorno API (política).  
- `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` configurables.

---

*Fin del informe de capacidad. Continuar con `Auditoria_Seguridad_OWASP_Top10.md`.*
