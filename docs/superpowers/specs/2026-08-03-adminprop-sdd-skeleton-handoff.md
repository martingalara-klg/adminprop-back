# AdminProp — Handoff del esqueleto SDD (paso 1 completado)

**Fecha:** 2026-08-04
**Estado del paso 1:** completo — 6 tareas ejecutadas y revisadas (revisión final de rama limpia tras una ola de fixes).
**Ramas:** `develop` en ambos repos (commits locales, sin push). Backend: `88498b1..e53e6b8`. Frontend: `46ae79d..fd46a4b`.

Este documento preserva lo que la próxima fase (pasos 2–6 del diseño: escritura de SDDs) necesita saber. Complementa al diseño (`2026-08-03-adminprop-sdd-design.md`) y al plan del esqueleto.

---

## 1. Pendientes diferidos (triage de la revisión final)

| # | Pendiente | Cuándo resolverlo |
|---|---|---|
| 1 | `database-migration.md` perdió la precisión "N tablas" del source | Paso 3 — al escribir `spec_data_model.md`, fijar el número real |
| 2 | `session-start.md` (ambos repos) referencia `CLAUDE.md`, que no existe aún | Paso 6 — se escribe ahí |
| 3 | `RUNBOOK-LOCAL-001` conserva `ANTHROPIC_API_KEY` sin worker de IA que la justifique | Paso 4 — decidir con la lista canónica de workers en `sdd_04`; mientras tanto tratarla como opcional |
| 4 | `module-structure.md:362` usa `HTTPException(detail=...)` en el template, contradiciendo el formato de error custom de los demás skills | Al escribir `sdd_03` — alinear el template a `NotFoundException` |
| 5 | `module-structure.md` ubica tests en `modules/<m>/tests/`; ci.yml/testing.md/session-start usan `tests/{unit,integration}/<módulo>/` (heredado tal cual de clarix) | Primer issue de scaffolding — decidir y unificar (CI hoy solo colecta `tests/`) |
| 6 | "In Progress" (P mayúscula) como texto display en session-start back; la columna real es "In progress" | Cosmético — cuando se toque el archivo |
| 7 | `RUNBOOK-LOCAL-002` (front) tiene un comentario HTML dentro del template bash de `.env.local` | Cuando se toque el archivo — convertir a `#` |
| 8 | `pr-format.yml` no acepta tipos `perf` ni `migrate` en PRs de mantenimiento sin issue | Cuando se toque el workflow |
| 9 | Job E2E del ci.yml front corre aunque el backend no exista (mitigado por el orden del roadmap: back primero) | Si cambia el orden del roadmap |
| 10 | Los dos templates de PR del front difieren (session-start §4.3 vs github-project-workflow) — manda session-start | Cuando se toque github-project-workflow front |
| 11 | `error-handling.md` front no explicita la restricción del rol maintenance (cubierta en tenant-context, code-review, flow-implementation) | Opcional |

## 2. Decisiones a tomar en el bootstrap (paso 7)

- **Promoción `develop → main` del backend:** el flujo diario opera en `develop`, pero `sync-sdd-to-frontend.yml` dispara en push a `main` (correcto: el sync propaga SDDs "publicados"). Documentar cuándo/cómo se mergea develop→main (¿release manual? ¿al cerrar cada fase?).
- **`PROJECT_NUMBER`** en ambos `session-start.md` (hoy `1` back / `2` front con comentario ⚠) — confirmar al crear los GitHub Projects.
- **Secret `SYNC_SDD_TOKEN`** con permisos sobre `adminprop-front` para el workflow de sync (guía: `clarix-backend/docs/ops/ci-sync-sdd-setup.md`).
- Crear `.env.example` y `docker/docker-compose.yml` (referenciados por runbooks/session-start; hoy son referencias transitorias documentadas).

## 3. Contrato de referencias forward — lo que los SDDs DEBEN cubrir

Los 21 archivos del esqueleto citan secciones y nombres concretos. Al escribir cada SDD (pasos 2–5), incluir estos nombres/anclas o hacer un pase de corrección sobre los skills:

### `sdd_01_prd.md`
- §3 con casos de uso `UC-XX` (dan nombre a los tests E2E).

### `sdd_02_domain_model.md`
- §3 "Reglas de Negocio Críticas" con catálogo `RN-XX` en familias: **RN-C** (contratos), **RN-P** (pagos), **RN-L** (liquidaciones), **RN-A** (accesos), **RN-D** (datos). Citados con ID exacto: `RN-D01` (aislamiento tenant, cross-tenant=404) y `RN-D02` (soft delete).
- Roles con la restricción de `maintenance` (solo módulo de mantenimiento).

### `sdd_03_api_contracts.md` (headings literales citados)
- **"Convenciones Generales"**: prefijo `/v1`, envelope `{ data, meta }`, sin endpoint de switch de org, JWT con `org` + `permissions[]` + `is_super_admin`.
- **"Formato de respuesta"**: error custom `{ "error": { code, message, field, details } }` (no RFC 7807).
- **"Códigos de Error Globales"** — debe incluir la unión de códigos ya usados como ejemplo en ambos repos: `VALIDATION_ERROR`, `INVALID_DATE_RANGE`, `UNAUTHORIZED`, `FORBIDDEN`, `ROLE_REQUIRED`, `SUPERADMIN_REQUIRED`, `ACCOUNT_LOCKED`, `MEMBERSHIP_INACTIVE`, `NOT_FOUND`, `CONFLICT`, `PERIOD_LOCKED`, `PERIOD_OVERLAP`, `ENTITY_HAS_DEPENDENCIES`, `USER_ALREADY_MEMBER`, `INVITATION_PENDING_EXISTS/_NOT_FOUND/_EXPIRED/_ALREADY_ACCEPTED`, `LAST_OWNER_REQUIRED`, `ROLE_NOT_FOUND`, `WORK_ORDER_ALREADY_CLOSED`, `MAINTENANCE_WORK_ORDER_NOT_ASSIGNED`, `DELETION_ALREADY_REQUESTED`, `BUSINESS_RULE_VIOLATION`, `INVALID_STATUS_TRANSITION`, `PAYMENT_EXCEEDS_CONTRACT_BALANCE`, `INDEX_VALUE_NOT_FOUND`, `INDEX_SERVICE_UNAVAILABLE`, `MFA_INVALID_CODE/_TOKEN_INVALID/_CONFIRMATION_INVALID`, `WIZARD_INCOMPLETE`, `FEATURE_NOT_ACTIVATED`, `RATE_LIMIT_EXCEEDED`, `INTERNAL_ERROR`.
  - ⚠ `FEATURE_NOT_ACTIVATED`/`WIZARD_INCOMPLETE` presuponen "activación de módulos por wizard" heredada de clarix — el PRD debe confirmar o descartar ese concepto para adminprop.
- **"Catálogo de Permisos"**: permisos atómicos (`contract:read/manage`, `payment:read`, `settlement:manage`, `work-order:read`, `user:manage`, `audit:read`, …).
- **"Resumen de Autorización por Recurso"**, **"Regla de oro"**, **"Paginación"** (cursor default; excepción audit-logs con page/page_size, citada como "§16").
- §1 auth: login con escenarios `authenticated | mfa_challenge_required | mfa_enrollment_required` + `mfa_token`; forgot-password siempre 200; accept-invitation. §15 notificaciones. Secciones por dominio ("3. Contratos", "Cobros", "5. Liquidaciones").

### `sdd_04_nonfunctional.md`
- §1.3 SLAs y retry de workers (backoff 30s→90s→270s citado), §1.4 TTLs de caché (fijan `staleTime` del front), §2.1 modelo de amenazas, §2.2/2.2a/2.2b (JWT 8h + refresh 30d rotativo; anti-enumeration con texto literal **"Credenciales incorrectas."** y mensaje de forgot-password; recovery codes una sola vez), §2.3 RLS + roles PG `adminprop_app`/`adminprop_superadmin`, §2.4 cifrado columnar pgcrypto + CSRF SameSite=Lax, §2.5 tabla de rate limits + `Retry-After` (ej. login 10/IP/10min ya citado), §2.7 security headers, §2.9 indisponibilidad de servicios de índices, §3.3 escalado de workers, §4.1 campos de log JSON + scrubbing, §4.6 X-Request-Id.
- **Lista canónica de workers** (los skills declaran `indices_worker`/`notification_worker`/`documents_worker` como ilustrativos y difieren a este doc) + decisión sobre `ANTHROPIC_API_KEY`.

### `spec_data_model.md` (headings literales)
- **"Orden de Migración"** con capas Fundación → Propiedades → Personas → Contratos → Cobranzas → Mantenimiento → Liquidaciones → Notificaciones/Auditoría (citado por session-start §1.2).
- **"Estrategia de Seed Data"** (global vía Alembic idempotente vs per-tenant en `OrganizationProvisioningService`), **"Principios Arquitectónicos"** (RLS canónico), **"Índices PostgreSQL Recomendados"**, Apéndice A (nomenclatura), numeración de capas ("Capa 0 — Fundación", …).

### Specs de módulo
- `spec_module_00_superadmin`: §RF-02 creación de org `pending_owner` + slug autogenerado; §RF-03 invitaciones con expiración 72h; §RN-01/RN-06 Super Admin sin org_id; §"Flujo de Activación de Cuenta".
- `spec_module_03_contratos`: **sección de ajustes por índice ICL/IPC** (los skills la citan como `§"Ajustes por índice"` — placeholder a confirmar), alertas de vencimiento, estados `draft|active|terminated|expired`.
- `spec_module_04_cobranzas`: §RF-03 `payment_scope` per_property, saldo/punitorios.
- `spec_module_05_liquidaciones`: §RF-01 cálculo 202+polling; wizard `select_period → … → confirmation`; estados `pending|processing|completed|with_errors|failed`.
- `spec_module_06_mantenimiento`: órdenes asignadas + cotizaciones; estados `open|in_progress|closed|cancelled`.
- `spec_module_07_administracion`: §RF-08 datos bancarios cifrados.
- `spec_notificaciones`: §RF-01 digests; §RF-04/Apéndice retry por canal; From dinámico `"AdminProp · {org} <noreply@...>"`.
- `_index.md` (paso 6): §4 registro de decisiones — los skills citan números de decisión de clarix (#2, #3, #8, #20, #23, #24, #28, #42, #49, #50, #66): al escribirlo, preservar esa numeración o hacer un pase de re-numeración sobre los skills; §6 versionado 1.x/2.x.

## 4. Convenciones ya fijadas por el esqueleto (no re-decidir)

- Project de GitHub: 3 estados **Todo / In progress / Done**; labels solo `status:ready` / `status:blocked` / `sdd:divergence`.
- PRs siempre `--base develop`; commits Conventional + footers `Closes #N` / `Implements: CA-XX` / `Rule: RN-XX`; sin `Co-Authored-By`.
- Secretos: "gestor de secretos" (nunca el literal en inglés).
- Frontend: una única app Vite (`src/`), sin Turborepo; rutas `/superadmin/*` protegidas por `is_super_admin`; tipos desde OpenAPI en `src/api/generated/`.
- Roles: `owner` / `admin` / `maintenance`. Propietarios e inquilinos sin login en MVP.
- El sync CI back→front es SOLO de `docs/sdd/**`; cada repo es dueño de sus runbooks.
