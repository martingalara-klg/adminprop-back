# AdminProp Backend — CLAUDE.md

## 1. Proyecto

- **Nombre:** AdminProp
- **Descripción:** SaaS multi-tenant de gestión de alquileres para corredores inmobiliarios y administradoras de propiedades: propiedades, contratos con ajustes por índice, cobranzas con mora sugerida, liquidaciones a propietarios, mantenimiento con cotizaciones.
- **Tipo de aplicación:** API REST con workers asíncronos. Multi-tenant shared-schema.
- **Mercado inicial:** Argentina (Córdoba). Idioma: español. Monedas: ARS y USD. Compliance: Ley 25.326.

> **Este es el repositorio de BACKEND.**
> El repositorio de frontend es **`adminprop-front`** (React 18 + Vite + TypeScript, una única app con rutas `/superadmin/*`).

---

## 2. Fuente de verdad

**Antes de implementar cualquier feature, leer el SDD correspondiente en `docs/sdd/`.** Los SDDs son la fuente de verdad. Si el código diverge del SDD, **el SDD manda**: detenerse, señalar la divergencia y esperar instrucción. Nunca alinear el código en silencio.

| Ruta | Qué cubre |
|---|---|
| [docs/sdd/_index.md](docs/sdd/_index.md) | Mapa maestro, dependencias, registro de decisiones (#XX), versionado |
| [docs/sdd/project_adminprop.md](docs/sdd/project_adminprop.md) | Contexto general |
| [docs/sdd/core/sdd_01_prd.md](docs/sdd/core/sdd_01_prd.md) | Casos de uso UC-01..UC-20, restricciones R-XX, supuestos S-XX, métricas |
| [docs/sdd/core/sdd_02_domain_model.md](docs/sdd/core/sdd_02_domain_model.md) | 17 entidades, invariantes RN-C/P/L/A/D, glosario |
| [docs/sdd/core/sdd_03_api_contracts.md](docs/sdd/core/sdd_03_api_contracts.md) | Endpoints, códigos de error, permisos, paginación — **contrato vinculante con el front** |
| [docs/sdd/core/sdd_04_nonfunctional.md](docs/sdd/core/sdd_04_nonfunctional.md) | Performance, workers (§1.3), seguridad (§2.x), observabilidad (§4.x) |
| [docs/sdd/core/spec_module_00_superadmin.md](docs/sdd/core/spec_module_00_superadmin.md) | Portal `/superadmin/*`, onboarding de organizaciones |
| [docs/sdd/features/spec_module_01..07](docs/sdd/features/) | Propiedades, personas, contratos, cobranzas, liquidaciones, mantenimiento, administración — RF-XX y CA-XX por módulo |
| [docs/sdd/infrastructure/spec_data_model.md](docs/sdd/infrastructure/spec_data_model.md) | 22 tablas en 8 capas, RLS, índices, orden de migración, seed |
| [docs/sdd/infrastructure/spec_notificaciones.md](docs/sdd/infrastructure/spec_notificaciones.md) | Eventos, enrutamiento por rol, retry por canal |

**Regla de conflicto:** ante divergencia código↔SDD, reportar (1) qué SDD y sección, (2) qué dice vs qué hace el código, (3) opción sugerida — y esperar instrucción.

**Repo de SDDs:** este repo es la fuente de verdad. `sync-sdd-to-frontend.yml` abre un PR automático en `adminprop-front` en cada push a `main` que toque `docs/sdd/**`. Nunca editar SDDs fuera de este repo.

---

## 3. Stack de backend

- **Python 3.11+ · FastAPI · Uvicorn · Pydantic** (schemas PascalCase)
- **PostgreSQL 16** — extensiones `pgcrypto` (UUID + AES-256 columnar) y `btree_gist` (no-solapamiento de contratos)
- **SQLAlchemy 2.0 + Alembic** — migraciones `YYYYMMDD_HHMMSS_<slug>.py`, SQL raw con `op.execute`
- **Redis 7 + Celery 5** — workers `notification_worker` y `documents_worker` + Celery Beat (`generate_rent_periods`, `detect_due_adjustments`, `detect_expiring_contracts`) — lista canónica en `sdd_04` §1.3
- **Documentos:** openpyxl (Excel) + WeasyPrint (PDF) para liquidaciones
- **Email:** Resend (único servicio externo del MVP)
- **Auth:** JWT RS256 en HttpOnly Secure cookies (`SameSite=Lax`), access 8h + refresh 30d rotativo; bcrypt cost 12; **sin MFA en MVP** (`sdd_04` §2.2b)
- **Observabilidad:** `python-json-logger` + Sentry + `X-Request-Id` propagado
- **Tests:** pytest + pytest-asyncio + httpx. **Cobertura mínima 95%** (excluye DTOs, modelos declarativos, boilerplate). Naming: `test_<ca_id>_<descripcion>`
- **Local:** Docker Compose (Postgres + Redis + API + workers). **Sin infra cloud ni CD en MVP** (decisión #111)
- **Secretos:** `.env` local (no commiteado); migra a un gestor de secretos con la infra cloud

---

## 4. Arquitectura

### Multi-tenancy (decisiones #2, #3, #42)

- Shared schema; toda tabla tenant-scoped tiene `organization_id` + RLS con `FORCE`.
- Middleware setea `SET LOCAL app.current_tenant_id = <jwt.org>` antes de cualquier query, y verifica **membresía activa** (no basta JWT válido).
- `organization_id` **nunca** se acepta de body/path/query — siempre del JWT (excepción: filtro opcional en `/superadmin/*`).
- Roles PG: `adminprop_app` (default, RLS) y `adminprop_superadmin` (`BYPASSRLS`, solo `/superadmin/*` con `is_super_admin=true`).
- Defense in depth: todo repositorio filtra `WHERE organization_id = :org` explícitamente además del RLS.
- Cross-tenant o inexistente → **404** (RN-D01), nunca 403.

### Flujos asíncronos

- `POST /settlements/generate` y `/regenerate` → 202 + polling (`documents_worker`).
- Emails → `notification_worker` post-commit (outbox: `email_sent_at IS NULL`); nunca bloquean el negocio.
- Jobs Beat idempotentes (UNIQUE constraints los protegen).
- Patrón: > 5 segundos ⇒ 202 + polling. Retry: 3 intentos, backoff 30/90/270s + jitter, `RetryableError` vs `NonRetryableError`.

---

## 5. Modelo de datos (resumen — detalle en `spec_data_model.md`)

**22 tablas en 8 capas.** Orden de migración (= orden del roadmap): **Fundación → Personas → Propiedades → Contratos → Cobranzas → Mantenimiento → Liquidaciones → Notificaciones/Auditoría.**

Convenciones: PKs UUID `gen_random_uuid()` · tablas snake_case plural · enums TEXT+CHECK (nunca ENUM de PG) · money `NUMERIC(14,2)`, TC y % `NUMERIC(14,4)`, nunca FLOAT · `TIMESTAMPTZ` para eventos, `DATE` para fechas operativas, períodos = DATE día 1 del mes · soft delete `deleted_at` (cobros: `voided_at` + autor) · naming del dominio: `landlord`/`renter` — "tenant" es SIEMPRE la organización (#109).

Inmutables/append-only: `audit_logs`, ajustes `applied`, pedidos cerrados ya liquidados. Editables con auditoría: liquidaciones (regenerables, #105), cargos del mes.

---

## 6. Contratos de API (resumen — detalle en `sdd_03`)

- Prefijo `/v1` (no `/api/v1`) · respuesta `{ data, meta }` · error custom `{ "error": { code, message, field, details } }` (no RFC 7807) · paginación cursor (audit-logs: page/page_size) · sin endpoint de switch de org (#49).
- Permisos atómicos (`contract:manage`, `work-order:quote`, …) chequeados con `requires_permission()` — nunca por nombre de rol.
- Textos anti-enumeration LITERALES de `sdd_04` §2.2a — no reescribirlos.
- **Regla de oro:** ningún contrato se modifica sin actualizar `sdd_03` primero. Inventar un `error.code` fuera del catálogo es divergencia.

---

## 7. Reglas de negocio globales (catálogo completo en `sdd_02` §3)

Las cinco familias — citarlas en el código al implementarlas (`# RN-P03: interés sobre saldo impago`):

- **RN-C (contratos):** sin solapamiento por propiedad; USD no ajusta; ajuste = % manual, nunca automático; monto vigente solo vía ajuste; contrato terminado no genera períodos.
- **RN-P (pagos):** un período por contrato/mes; en término hasta el día de gracia (default 10); interés = saldo × % diario × días; imputación libre con perdón registrado; pagos parciales; TC manual obligatorio si difiere la moneda; destino dueño = "ya rendido".
- **RN-L (liquidaciones):** todo en ARS con TC manual (#103); comisión = % × (alquileres + intereses cobrados), incluidos directos (#104); regeneración libre pero auditada; reparación agency se descuenta una sola vez.
- **RN-A (accesos):** maintenance SOLO módulo de mantenimiento (enforzado en API); solo owner gestiona usuarios/config; siempre ≥1 owner activo; accesos denegados auditados.
- **RN-D (datos):** aislamiento con 404; soft delete universal; auditoría append-only; correcciones de plata siempre trazadas.

---

## 8. Comportamiento esperado de Claude Code — Backend

### Siempre hacer

- Leer el SDD del módulo antes de implementar (índice en `docs/sdd/_index.md`).
- Migraciones Alembic versionadas para todo cambio de schema, idénticas a `spec_data_model`.
- Comentar la RN-XX al implementar una invariante; nombrar tests con el CA-XX del SDD.
- RLS + `set_tenant_context` + filtro explícito de `organization_id` en cada query; test de aislamiento cross-tenant (404) en cada módulo.
- Propagar `request_id` a logs, jobs y notificaciones; JSON logging con scrubbing (nunca `password`, tokens, `bank_info`).
- Encolar en Celery todo lo que supere 5s (202 + polling).
- Actualizar `.env.example` si se agrega una variable, y el runbook local si cambia el setup.

### Nunca hacer sin preguntar primero

- Modificar schema sin migración, o un contrato de `sdd_03` sin actualizar el SDD antes.
- Agregar dependencias no mencionadas en los SDDs.
- Resolver ambigüedades del SDD por cuenta propia, o tomar decisiones de diseño que pertenecen a un SDD.
- Queries sin filtro de organización o fuera de RLS.
- Aplicar un % de ajuste automáticamente (decisión #101), recalcular liquidaciones ya emitidas sin regeneración auditada, o borrar físicamente datos operativos.
- Guardar secretos en código o logs; usar el literal "secret manager" (la frase canónica es "gestor de secretos").
- Operar con el rol `adminprop_superadmin` fuera de `/superadmin/*`.

### Ante algo no especificado

Detenerse y reportar: qué se intenta implementar, qué falta especificar, opciones con trade-offs. **No inventar el comportamiento.**

---

## 9. Estructura del repositorio

```
adminprop-back/
├── docs/sdd/                       ← fuente de verdad (core/, features/, infrastructure/, _index.md)
├── docs/skills/                    ← patrones de implementación (leer según tipo de tarea)
├── docs/prompts/session-start.md   ← el comando de sesión autónoma
├── docs/runbooks/RUNBOOK-LOCAL-001-backend.md
├── src/adminprop/
│   ├── main.py / config.py
│   ├── modules/                    ← properties, people, contracts, payments, settlements,
│   │   │                             maintenance, admin, superadmin, notifications, auth
│   │   └── <módulo>/{router,service,repository,schemas,models,exceptions}.py
│   ├── workers/                    ← celery_app, notification_worker, documents_worker, beat
│   ├── shared/                     ← auth/, tenant/, rbac/, errors/, logging/, rate_limit/,
│   │                                 encryption/, storage/, email/
│   └── db/                         ← base, session (hook RLS), migrations/versions/
├── tests/{unit,integration}/<módulo>/
├── docker/docker-compose.yml       ← se crea en el primer issue de scaffolding
├── pyproject.toml · alembic.ini · .env.example
└── .github/workflows/              ← ci, pr-format, sdd-integrity, sync-sdd-to-frontend
```

---

## 10. Pendientes

| Pendiente | Cuándo |
|---|---|
| `PROJECT_NUMBER` en `session-start.md` | Bootstrap (al crear el GitHub Project) |
| Secret `SYNC_SDD_TOKEN` del workflow de sync | Bootstrap |
| Promoción `develop → main` (dispara el sync de SDDs) | Definir en el bootstrap |
| `docker-compose.yml`, `pyproject.toml`, `.env.example` | Primeros issues de scaffolding del roadmap |
| Infra cloud, CD, storage de archivos, MFA, AFIP, portal externo | Post-MVP (ver `sdd_01` §4) |
