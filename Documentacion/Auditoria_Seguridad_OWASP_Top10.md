# Auditoría de seguridad — OWASP Top 10 (2021)

**Aplicación:** FulfillPro v2.2  
**Fecha de re-auditoría:** 2026-07-19  
**Perfil:** stack Docker 12 GB RAM / 100 GB storage  
**Método:** revisión de código + verificación en runtime tras remediación de críticos

> Actualizado **después** de remediar los hallazgos críticos/altos de la primera pasada.

---

## Resumen ejecutivo (post-remediación)

| Categoría OWASP | Estado anterior | Estado actual | Riesgo residual |
|-----------------|-----------------|---------------|-----------------|
| A01 Broken Access Control | Parcial | **Parcial → mejorado** | Medio |
| A02 Cryptographic Failures | Parcial | **Parcial → mejorado** | Medio (deps TLS en borde) |
| A03 Injection | Parcial | **Cumple (con residual bajo)** | Bajo |
| A04 Insecure Design | Parcial | **Parcial → mejorado** | Medio |
| A05 Security Misconfiguration | **No cumple** | **Parcial / Cumple en prod si se configuran secretos** | Medio |
| A06 Vulnerable Components | Parcial | Parcial | Medio |
| A07 Identification & Auth Failures | Parcial | **Parcial → mejorado** | Medio |
| A08 Software/Data Integrity | Parcial | Parcial | Medio |
| A09 Security Logging & Monitoring | Parcial | Parcial | Medio |
| A10 SSRF | Cumple | **Cumple** | Bajo |

**Veredicto actualizado:**  
- **Development / lab:** operativo con warnings de secretos débiles (no bloquea).  
- **Production:** **fail-fast** si JWT/admin/CORS inseguros; registro público off por defecto; demos no se siembran.  
- Aún se recomienda **TLS en reverse proxy** y MFA para ops antes de Internet abierto.

---

## Remediaciones implementadas (esta iteración)

| # | Hallazgo crítico/alto | Fix aplicado | Archivos clave |
|---|----------------------|--------------|----------------|
| 1 | Secretos/credenciales default en prod | `assert_production_secrets()` fail-fast | `core/security_hardening.py`, `main.py` |
| 2 | CORS `*` en prod | Prohibido en production; allowlist en compose | `main.py`, `docker-compose.yml` |
| 3 | Sin security headers | Middleware CSP, XFO, nosniff, HSTS (prod), etc. | `core/middleware.py` |
| 4 | Rate limit fail-open | Fallback en memoria; **nunca** se abre del todo | `core/rate_limit.py` |
| 5 | Consentimiento solo UI | `require_consent` en process/orders/analytics consolidate/company admin | `dependencies.py`, routers |
| 6 | `count_quota` del cliente | Eliminado; cupo solo por licencia en servidor | `order_service.py`, `process.py`, `orders.py` |
| 7 | Path traversal storage | `resolve()` + bloqueo `..` / absolutas | `storage_service.py` |
| 8 | Upload sin validación | Magic bytes Excel + máx 50 MB | `order_service.py` |
| 9 | Seeds demo en prod | `SEED_DEMO_USERS` solo si no production | `main.py` |
| 10 | Registro público en prod | `ALLOW_PUBLIC_REGISTER=false` por defecto en prod | `config.py`, `auth.py` |
| 11 | Password empleados débil | Mínimo 12 caracteres | `company.py` |

### Verificación runtime (2026-07-19)

| Control | Resultado |
|---------|-----------|
| Path `../../etc/passwd` | **HTTP 400** (bloqueado) |
| Rate limit local (límite 3) | 4.ª y 5.ª petición → **429** |
| Arranque development con JWT débil | **Warning** en logs, no crash |
| Headers middleware | Cargado en app |

---

## A01 — Broken Access Control

### Estado: **Parcial (mejorado)**

**Cumple ahora**
- JWT + roles + portales separados.
- `require_consent` en `POST /api/process`, upload/process de órdenes, descarga de archivos de orden, consolidado de analítica y gestión de empleados.
- Cupo de licencia ya no se controla desde el body del cliente.

**Residual**
- Multi-tenant por `client_code` string (colisión teórica).
- Empleados ven órdenes del mismo `client_code` (diseño).
- `/ops` sigue siendo ruta “oculta”, no secreta (usar red privada).

---

## A02 — Cryptographic Failures

### Estado: **Parcial (mejorado)**

**Cumple ahora**
- bcrypt para passwords.
- JWT HS256 con validación de secretos en production (longitud ≥ 32, no valores de ejemplo).
- Admin password no puede ser la demo en production.

**Residual**
- Tokens aún en `localStorage` (riesgo XSS → cookies HttpOnly recomendadas).
- TLS depende del reverse proxy (no incluido en compose base).
- Sin rotación automática de JWT secret.

---

## A03 — Injection

### Estado: **Cumple (residual bajo)**

**Cumple ahora**
- ORM parametrizado.
- Path sandbox estricto en storage.
- Validación de tipo Excel (magic bytes) y tamaño.

**Residual**
- Chart.js por CDN (ver A08).
- Excel de gran tamaño aún puede ser DoS de CPU (mitigado por workers y rate limit).

---

## A04 — Insecure Design

### Estado: **Parcial (mejorado)**

**Mejoras**
- Registro público deshabilitable y deshabilitado en production por defecto.
- Códigos de licencia cortos rechazados en production.
- Process sigue síncrono (limitación de capacidad, no solo seguridad).

**Residual**
- Sin cola asíncrona de Excel (agotamiento de workers).
- Firma digital = aceptación con nombre (no eIDAS avanzado).

---

## A05 — Security Misconfiguration

### Estado: **Parcial → Cumple si se despliega en production con secretos reales**

**Cumple ahora**
- Headers de seguridad (CSP, X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy; HSTS en prod).
- OpenAPI/docs desactivados en production.
- CORS restringido en compose de lab a localhost; `*` prohibido en production.
- Seeds demo solo en development.
- Warnings explícitos de secretos débiles en dev.

**Residual**
- Operador debe suministrar secretos fuertes al pasar a production (el fail-fast lo obliga).
- Falta reverse proxy TLS en el repo base.

---

## A06 — Vulnerable Components

### Estado: **Parcial** (sin cambio de proceso)

- Dependencias pineadas; sin SCA en CI todavía.
- Superficie matplotlib/reportlab/pillow presente.

---

## A07 — Identification & Authentication Failures

### Estado: **Parcial (mejorado)**

**Mejoras**
- Rate limit no se desactiva si cae Redis.
- Registro público controlado.
- Password mínima 12 en alta de empleados.
- Login platform separado.

**Residual**
- Sin MFA para admin de plataforma.
- Sin lockout por cuenta (solo por IP/ventana).
- JWT en localStorage.

---

## A08 — Software and Data Integrity

### Estado: **Parcial** (sin cambio mayor)

- CDN Chart.js sin SRI.
- Sin firmado de imágenes Docker.

---

## A09 — Logging & Monitoring

### Estado: **Parcial** (sin cambio mayor)

- AccessLog / SecurityEvent y panel ops siguen activos.
- Sin alertas automáticas externas.

---

## A10 — SSRF

### Estado: **Cumple**

Sin cambios; superficie mínima.

---

## Checklist pre-producción (actualizado)

- [x] Fail-fast secretos débiles en production  
- [x] CORS no `*` en production  
- [x] Security headers + CSP  
- [x] Rate limit con fallback (no fail-open)  
- [x] Consentimiento en backend  
- [x] Cupo solo en servidor  
- [x] Path sandbox storage  
- [x] Límite y magic bytes de upload  
- [x] Demo users solo en development  
- [x] Registro público deshabilitable / off en prod  
- [ ] TLS reverse proxy (ops)  
- [ ] MFA admin plataforma  
- [ ] SCA (pip-audit) en CI  
- [ ] Cookies HttpOnly para refresh  
- [ ] Retención automática de storage 100 GB  

---

## Conclusión post-fix

Los **errores críticos de la primera auditoría** (A05 misconfiguration de arranque, A02 secretos default en prod, A01 consentimiento y cupo cliente, A03 path traversal, A05 rate-limit fail-open) quedaron **corregidos en código**.

El riesgo residual principal para Internet abierto es ya **operacional**: desplegar con secretos fuertes, TLS en el borde, MFA ops y cola de process. Con eso, el estado general pasa de **“no exponer”** a **“piloto endurecido viable detrás de proxy”**.

---

*Re-auditoría completada tras remediación crítica. Complementa `Auditoria_Capacidad_Carga_12GB.md` y `CANVAS_Auditorias.md`.*
