---
name: AdminProp — Requisitos No Funcionales
description: Performance, workers y retries, caché, seguridad (JWT, anti-enumeration, RLS, cifrado, rate limits, headers), observabilidad y compliance
type: project
version: 1.0
fecha: 2026-08-05
---
# AdminProp — Requisitos No Funcionales (RNF)

**Versión:** 1.0
**Estado:** Borrador para revisión
**Fecha:** 2026-08-05

---

## 1. Performance y Procesamiento

### 1.1 Latencia de API

| Tipo de endpoint | Target |
|---|---|
| Lectura (GET) | P95 < 500ms |
| Escritura (POST/PATCH) | P95 < 1.000ms |
| Generación de liquidación (cálculo + Excel + PDF) | < 15 segundos P90 (async, 202 + polling) |

### 1.2 Escala de referencia

10–200 propiedades por organización, 1–5 usuarios, ~1.000–5.000 cobros/año por org. Ninguna tabla supera el millón de filas en años: sin particionado en MVP.

### 1.3 Workers, colas y retries

**Lista canónica de workers Celery (MVP):**

| Worker | Responsabilidad |
|---|---|
| `notification_worker` | Emails (Resend) + notificaciones in-app |
| `documents_worker` | Generación de Excel (openpyxl) y PDF (WeasyPrint) de liquidaciones |

**Celery Beat (tareas programadas):**

| Tarea | Cuándo | Qué hace |
|---|---|---|
| `generate_rent_periods` | 1° de cada mes, 00:30 (tz de la org) | Genera el rent_period de cada contrato activo (RN-P01); idempotente |
| `detect_due_adjustments` | Diaria, 01:00 | Crea ajustes `pending` cuando llega el mes que corresponde + notifica (RN-C03) |
| `detect_expiring_contracts` | Diaria, 01:30 | Notifica contratos que vencen en `contract_expiry_notice_days` |

**Política de retry (todos los workers):** `max_retries=3`, backoff exponencial 30s → 90s → 270s, con jitter. `RetryableError` (5xx, timeout, rate limit del proveedor) reintenta; `NonRetryableError` (4xx, datos inválidos) marca fallo inmediato y notifica. Los jobs de Beat son idempotentes (re-ejecutar no duplica: constraints UNIQUE de `rent_periods` y `contract_adjustments`).

**Nota:** no hay workers de IA en MVP → **no existe `ANTHROPIC_API_KEY` ni equivalente en la configuración** (resuelve el pendiente #3 del handoff: quitar esa variable del runbook).

### 1.4 Caché (Redis)

| Dato | TTL | Invalidación |
|---|---|---|
| Listados (propiedades, contratos, panel de cobranzas) | 5 min | Write-through al mutar |
| Estado de deuda | 5 min | Al registrar/anular un cobro |
| Badge de notificaciones no leídas | 5 min | Al crear/leer notificación |

Los `staleTime` de TanStack Query en el frontend se alinean con estos TTLs.

---

## 2. Seguridad

### 2.1 Modelo de amenazas (resumen)

| Amenaza | Mitigación |
|---|---|
| Acceso cross-tenant | RLS + FORCE en todas las tablas (§2.3) + filtro explícito en repositorios + tests de aislamiento obligatorios; respuesta 404 (RN-D01) |
| Robo de sesión (XSS) | JWT en HttpOnly cookies (nunca localStorage) + CSP (§2.7) |
| CSRF | `SameSite=Lax` + `Secure` + HttpOnly (§2.4); sin header custom |
| Fuerza bruta de login | Lockout (5 intentos/10 min → 30 min) + rate limit (§2.5) |
| Enumeración de usuarios | Mensajes genéricos literales (§2.2a) |
| Escalamiento del rol maintenance | Permisos atómicos verificados en API (RN-A01) + audit de accesos denegados (RN-A04) |
| Manipulación de datos financieros | Anulación lógica con autor, auditoría append-only (RN-D03/D04), correcciones siempre trazadas |

### 2.2 Autenticación y sesiones

- JWT **RS256** (clave asimétrica). Access token 8h; refresh token 30 días **rotativo single-use** (reuso de un refresh ya rotado → revoca la familia completa).
- Cookies: `HttpOnly` + `Secure` + `SameSite=Lax`, server-set. El header `Authorization: Bearer` solo para testing/server-to-server.
- Passwords: bcrypt cost 12. Política: ≥ 10 caracteres, ≥ 1 mayúscula, ≥ 1 número.
- Refresh tokens server-side en Redis (revocables).

### 2.2a Anti-enumeration (textos literales — no traducir ni "mejorar")

- Login fallido: **"Credenciales incorrectas."** (idéntico para email inexistente y password incorrecta).
- Forgot-password: **"Si el email está registrado, recibirás instrucciones para restablecer tu contraseña en los próximos minutos."** — siempre HTTP 200.

### 2.2b MFA

**Post-MVP** (decisión 2026-08-05). Cuando se implemente: TOTP RFC 6238 obligatorio para `owner` y `admin` + 8 recovery codes hasheados, de un solo uso, mostrados una única vez. El modelo de datos no lo bloquea (columnas se agregan por migración).

### 2.3 Aislamiento multi-tenant

- RLS activo + `FORCE ROW LEVEL SECURITY` en toda tabla con `organization_id` (ver `spec_data_model` §Principios).
- Roles PostgreSQL: **`adminprop_app`** (default, sujeto a RLS) y **`adminprop_superadmin`** (`BYPASSRLS`, solo para `/superadmin/*` con JWT `is_super_admin=true`).
- Middleware: `SET LOCAL app.current_tenant_id = <jwt.org>` antes de cualquier query; verificación de membresía activa (no basta JWT válido).
- Defense in depth: todo repositorio filtra `organization_id` explícitamente.

### 2.4 Cifrado y CSRF

- **En tránsito:** TLS en todos los ambientes públicos.
- **Columnar (pgcrypto AES-256):** `landlords.bank_info` (datos bancarios de terceros). Clave de cifrado en variable de entorno local en MVP; migra a un gestor de secretos con la infra cloud. `users.password_hash` va con bcrypt (no requiere pgcrypto).
- **CSRF:** cubierto por `SameSite=Lax` + `Secure` + HttpOnly. Sin token CSRF custom.
- **Scrubbing en logs:** nunca aparecen `password`, `password_hash`, tokens, cookies, `bank_info`.

### 2.5 Rate limiting (Redis token bucket)

| Endpoint | Límite | Respuesta al exceder |
|---|---|---|
| `POST /auth/login` | 10 req / IP / 10 min | 429 + `Retry-After` |
| `POST /auth/forgot-password` | 5 req / IP / hora | 429 + `Retry-After` |
| `POST /auth/refresh` | 60 req / usuario / hora | 429 |
| Escrituras generales | 120 req / usuario / min | 429 |
| Lecturas generales | 600 req / usuario / min | 429 |

### 2.7 Security headers

`Content-Security-Policy: default-src 'self'` (extensible) · `X-Content-Type-Options: nosniff` · `X-Frame-Options: DENY` · `Referrer-Policy: strict-origin-when-cross-origin` · `Strict-Transport-Security` (con TLS). CORS: solo los origins del frontend (config por ambiente).

### 2.8 Compliance — Ley 25.326

Datos personales de propietarios, inquilinos y usuarios: se usan solo para la administración declarada; derecho de acceso/rectificación/supresión atendido operativamente (soft delete + rectificación auditada); sin transferencia a terceros. Registro AAIP: trámite manual del responsable.

### 2.9 Indisponibilidad de servicios externos

Único servicio externo del MVP: **email (Resend)**. Si falla: la notificación in-app se crea igual (el email es best-effort), el envío se reintenta con la política §1.3, y el fallo persistente queda logueado con `request_id`. Ninguna operación de negocio se bloquea por un email caído.

---

## 3. Operación

### 3.1 Ambientes

MVP: desarrollo local (Docker Compose: Postgres 16 + Redis 7 + API + workers). Staging/producción se definen con la infra (diferida).

### 3.2 Backups

Con infra local: `pg_dump` diario automatizado + retención 30 días (responsabilidad operativa documentada en el runbook). Con infra cloud: backups gestionados + PITR.

### 3.3 Escalado de workers

MVP: 1 proceso API + 1 `notification_worker` + 1 `documents_worker` + Beat. Los workers escalan horizontalmente por réplica de contenedor si crece la carga (sin cambios de código: la cola Redis distribuye).

---

## 4. Observabilidad

### 4.1 Logging estructurado (JSON)

Campos obligatorios en cada línea: `timestamp`, `level`, `service`, `request_id`, `organization_id`, `user_id`, `message`, `duration_ms` (en requests). Scrubbing automático (§2.4). Librería: `python-json-logger`.

### 4.2 Errores

Sentry (API + workers + frontend) desde el MVP, con `request_id` como tag cross-stack.

### 4.6 Distributed tracing

`X-Request-Id` (UUID v4) generado en el ingreso, propagado a logs, jobs Celery y notificaciones; devuelto en el header de respuesta. El frontend lo registra en breadcrumbs de Sentry.

### 4.7 Health check

`GET /health`: DB, Redis y filesystem de attachments. Usado por Docker Compose y por la infra futura.
