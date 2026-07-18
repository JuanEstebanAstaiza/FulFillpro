# FulfillPro v2

Plataforma de procesamiento de órdenes de dropshipping para bodega: sube un Excel, obtiene **Resumen**, **Reporte Ordenado** y **PRIORITARIAS**, con licencias flexibles, registro de equipos, almacenamiento por carpetas y panel de administración.

## Stack

| Capa | Tecnología |
|------|------------|
| API | Python · FastAPI |
| DB | PostgreSQL |
| Cache / rate limit | Redis |
| Excel | openpyxl (motor 14 fases) |
| Frontend | HTML/CSS/JS minimalista |

## Arranque rápido (Docker)

```bash
cp .env.example .env
docker compose up --build
```

- App: http://localhost:8000  
- Admin UI: http://localhost:8000/admin  
- API docs: http://localhost:8000/api/docs  

**Credenciales por defecto** (cámbialas en `.env`):

- Email: `admin@fulfillpro.com`
- Password: `AdminFulfillPro2026!`

Licencias demo sembradas:

- `DEMO-TRIAL` — plantilla trial (50 órdenes, 3/día, 7 días, 3 equipos)
- `DEMO-001` — plan estándar demo

## Desarrollo local (sin Docker de la API)

1. Levanta solo Postgres y Redis:

```bash
docker compose up db redis -d
```

2. Instala dependencias y arranca:

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
copy .env.example .env
set PYTHONPATH=.
uvicorn backend.app.main:app --reload --port 8000
```

## Licencias (modelo flexible)

Cada licencia soporta:

- **Límite global de órdenes** (`limit_uses`) — se descuenta por licencia, no por equipo
- **Límite diario** (`daily_limit`)
- **Duración / expiry**
- **Máx. equipos** (`max_devices`) — el ID de equipo es un identificador asignable al PC/laptop (no IMEI de celular obligatorio)
- **Flags**:
  - `count_toward_global` — si es `false`, las órdenes no descuentan cupo
  - `enforce_daily_limit` — si es `false`, ignora tope diario
  - `features.independent_upload` / `unlimited_orders` — reglas extra

Plantillas en admin: **trial**, **standard**, **pro**, **enterprise**, o **custom**.

Ejemplo trial (como en el brief):

> 50 órdenes totales · 3 órdenes/día · 7 días · 3 equipos registrados

Desde el panel admin puedes crear licencias al cerrar un trato, asignarlas a usuarios, liberar equipos, renovar, resetear usos y monitorear incidentes.

## Flujo de uso

1. Login (JWT)
2. Activar licencia + ID de equipo
3. Subir Excel → se crea una **orden** en PostgreSQL y se guarda en:

```
storage/{client_code}/{YYYY}/{MM}/{order_id}/
  input/
  output/
  prioritarias/
  meta.json
```

4. Procesar → Excel de 3 hojas enlazado a la orden
5. Descargar desde historial cuando quieras

## Columnas del Excel de entrada

Obligatorias: `PRODUCTO`, `NUMERO GUIA` (o sinónimos).  
Opcionales: `ID`, `VARIACION`, `CANTIDAD`, `TOTAL DE LA ORDEN`, `FECHA GUIA GENERADA`.

Hay un archivo de muestra en `samples/ordenes_muestra.xlsx` (generarlo con):

```bash
python scripts/generate_sample_xlsx.py
```

La especificación completa del motor está en `Documentacion/FulfillPro_Especificacion_Tecnica.md`.

## Panel admin — monitoreo

- Resumen: usuarios, licencias, órdenes hoy/semana, fallos, incidentes
- Logs operativos (login, activate, process, admin)
- Incidentes de seguridad/operación (login fallido, cupo lleno, errores de proceso)
- CRUD de licencias y equipos

## Logo de la empresa

Sustituye `frontend/assets/logo-placeholder.svg` por el logo corporativo (mismo path o actualiza `index.html` / `admin.html`).

## Estructura

```
backend/app/          # API FastAPI
frontend/             # UI estática
Documentacion/        # Spec del motor Excel
samples/              # Excel de muestra
storage/              # Archivos de órdenes (gitignored)
docker-compose.yml
```

## Seguridad

- Cambia `JWT_SECRET`, `ADMIN_PASSWORD` y credenciales de Postgres en producción
- Redis se usa para refresh tokens, cache de licencia y rate limiting
- Los secretos por defecto son solo para desarrollo
