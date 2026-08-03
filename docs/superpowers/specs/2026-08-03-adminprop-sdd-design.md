# AdminProp — Diseño del sistema documental SDD

**Fecha:** 2026-08-03
**Estado:** en revisión
**Autor:** Martin Galara + Claude Code

---

## 1. Objetivo

Replicar en `adminprop-back` y `adminprop-front` el sistema de desarrollo dirigido por agente de clarix (Spec-Driven Development): un árbol documental donde los SDDs son la fuente de verdad, skills que codifican los patrones de implementación, y un prompt de sesión (`docs/prompts/session-start.md`) que Claude Code ejecuta de forma autónoma para tomar issues de GitHub, implementarlos por capas y abrir PRs.

Este documento diseña **el sistema documental**, no la aplicación. Las specs de producto (PRD, modelo de dominio, contratos) se escribirán después, siguiendo este diseño, a partir de la descripción del negocio que dará el usuario.

## 2. El producto (decisiones tomadas)

| Decisión | Valor |
|---|---|
| Dominio | **Gestión de alquileres**: contratos, cobranzas, ajustes por índice, liquidaciones a propietarios, mantenimiento |
| Tenancy | **Multi-tenant desde el diseño** (una administradora hoy, SaaS mañana). Modelo clarix: `organization_id` + RLS PostgreSQL |
| Usuarios MVP | Equipo interno de la administradora + rol restringido **encargado de reparaciones** (sube cotizaciones). Propietarios e inquilinos NO tienen login en MVP |
| Stack | **El mismo de clarix**: Python 3.11 + FastAPI + SQLAlchemy 2.0 + Alembic + PostgreSQL 16 + Celery/Redis; React 18 + Vite + TypeScript + Tailwind/shadcn + TanStack Query + Zustand |
| Infra cloud | **Diferida.** MVP se desarrolla y testea con Docker Compose local + CI de tests. Terraform GCP, runbooks de deploy y workflows cd-* se agregan cuando exista ambiente |
| GitHub | Repos `martingalara-klg/adminprop-back` y `martingalara-klg/adminprop-front`. Hoy solo tienen `main`; el bootstrap crea `develop`, Projects, labels e issues |
| Origen de specs | No hay documentación previa: el usuario describe el negocio y los `.md` se redactan juntos, con revisión por documento |

## 3. Mapa de módulos (aprobado)

La numeración refleja el orden de dependencias, que será el orden del roadmap de issues.

| # | Módulo | Cubre | Depende de |
|---|---|---|---|
| 0 | Superadmin | Alta de administradoras (orgs), invitación de owner, deshabilitación. Mínimo pero necesario por multi-tenancy | — |
| 1 | Propiedades y unidades | Inventario de inmuebles: dirección, tipo, propietario, estado | 0 |
| 2 | Personas | Propietarios e inquilinos (registros, sin login): contacto, CUIT/DNI, datos bancarios | 0 |
| 3 | Contratos de alquiler | Propiedad + inquilino + propietario, monto, plazo, depósito, comisión. **Incluye ajustes ICL/IPC** como parte del ciclo de vida del contrato (cronograma, índice, monto vigente, alertas) | 1, 2 |
| 4 | Cobranzas y mora | Cobros de alquiler, recibos, punitorios, estado de deuda | 3 |
| 5 | Liquidaciones a propietarios | Rendición mensual: cobros − comisión − gastos, comprobante PDF | 4, 6 |
| 6 | Mantenimiento y cotizaciones | Reclamos → órdenes de trabajo → cotizaciones del encargado → aprobación → gasto registrado | 1 |
| 7 | Administración | Usuarios del equipo, RBAC (`permissions[]`), settings de la org (comisión default, % punitorios) | 0 |
| — | Notificaciones (transversal) | Servicio que los demás módulos usan: vencimiento de contrato, próximo ajuste, mora. Email + panel in-app. Versión liviana de la de clarix | todos |

**Post-MVP explícito:** facturación AFIP/ARCA, portal de propietarios/inquilinos, reportes avanzados y KPIs.

## 4. Roles

| Rol | Alcance |
|---|---|
| `owner` | Todo, incluida gestión de usuarios y settings |
| `admin` | Operación completa (contratos, cobros, liquidaciones, mantenimiento) salvo usuarios/settings |
| `maintenance` | Solo Módulo 6: órdenes de trabajo asignadas, subir cotizaciones y comprobantes. Análogo al `developer` de clarix: restricciones enforzadas en API, no solo en UI |
| Super Admin | Empleado de la plataforma (`is_super_admin=true`), solo namespace `/superadmin/*` |

## 5. Árbol documental y origen de cada pieza

### `adminprop-back/` (dueño de los SDDs)

```
adminprop-back/
├── CLAUDE.md                          ← NUEVO (esqueleto clarix; se escribe al final — compila los SDDs)
├── docs/
│   ├── sdd/
│   │   ├── _index.md                  ← NUEVO (mapa maestro, dependencias, glosario, decisiones)
│   │   ├── project_adminprop.md       ← NUEVO
│   │   ├── core/
│   │   │   ├── sdd_01_prd.md          ← NUEVO (UC-XX, restricciones, métricas)
│   │   │   ├── sdd_02_domain_model.md ← NUEVO (entidades, invariantes RN-XX)
│   │   │   ├── sdd_03_api_contracts.md← NUEVO (endpoints, errores, JWT, paginación)
│   │   │   ├── sdd_04_nonfunctional.md← NUEVO (versión más liviana que clarix)
│   │   │   └── spec_module_00_superadmin.md ← NUEVO (base clarix, recortado)
│   │   ├── features/
│   │   │   ├── spec_module_01_propiedades.md      ← NUEVO
│   │   │   ├── spec_module_02_personas.md         ← NUEVO
│   │   │   ├── spec_module_03_contratos.md        ← NUEVO (incluye ajustes ICL/IPC)
│   │   │   ├── spec_module_04_cobranzas.md        ← NUEVO
│   │   │   ├── spec_module_05_liquidaciones.md    ← NUEVO
│   │   │   ├── spec_module_06_mantenimiento.md    ← NUEVO
│   │   │   └── spec_module_07_administracion.md   ← NUEVO (base clarix módulo 6, recortado)
│   │   └── infrastructure/
│   │       ├── spec_data_model.md                 ← NUEVO (tablas, RLS, índices, seeds)
│   │       └── spec_notificaciones.md             ← NUEVO (transversal, liviana)
│   ├── skills/                        ← COPIA ADAPTADA de clarix (cambian nombres, rutas, ejemplos)
│   │   ├── api-endpoint.md
│   │   ├── async-worker.md
│   │   ├── code-review.md
│   │   ├── database-migration.md
│   │   ├── error-handling.md
│   │   ├── external-integrations.md   (índices ICL/IPC, email)
│   │   ├── git-workflow.md
│   │   ├── github-project-workflow.md
│   │   ├── module-structure.md
│   │   ├── tenant-isolation.md
│   │   └── testing.md
│   ├── prompts/
│   │   └── session-start.md           ← COPIA ADAPTADA (variables ORG/REPO/PROJECT, mismas 4 fases)
│   └── runbooks/
│       └── RUNBOOK-LOCAL-001-backend.md ← COPIA ADAPTADA (Docker Compose local)
├── .github/workflows/
│   ├── ci.yml                         ← COPIA ADAPTADA (tests + lint; sin deploy)
│   ├── pr-format.yml                  ← COPIA ADAPTADA
│   ├── sdd-integrity.yml              ← COPIA casi literal
│   └── sync-sdd-to-frontend.yml       ← COPIA ADAPTADA (target: adminprop-front)
└── (docker/, pyproject.toml, .env.example — los crea el agente en las primeras issues)
```

### `adminprop-front/` (espejo)

```
adminprop-front/
├── CLAUDE.md                          ← NUEVO (esqueleto clarix frontend)
├── docs/
│   ├── sdd/                           ← copia sincronizada desde adminprop-back (CI)
│   ├── skills/                        ← COPIA ADAPTADA (api-client, error-handling,
│   │                                     flow-implementation, git-workflow,
│   │                                     github-project-workflow, module-structure,
│   │                                     state-management, tenant-context, testing, code-review)
│   ├── prompts/session-start.md       ← COPIA ADAPTADA
│   └── runbooks/RUNBOOK-LOCAL-002-frontend.md ← COPIA ADAPTADA
└── .github/workflows/ci.yml           ← COPIA ADAPTADA
```

**Diferencias deliberadas con clarix:** sin `infra/terraform/`, sin runbooks de deploy, sin workflows `cd-staging`/`cd-production`/`backport`, sin spec de embeddings/RAG ni AFIP (post-MVP). El frontend de adminprop no necesita monorepo Turborepo con dos apps salvo que el portal superadmin lo justifique — decisión a tomar al escribir el CLAUDE.md del front (default: una sola app con rutas `/superadmin` protegidas, más simple que clarix).

## 6. Convenciones heredadas de clarix (sin cambios)

- SDDs = fuente de verdad; ante divergencia código↔SDD, detenerse y reportar (issue `sdd:divergence`).
- GitFlow: `main` / `develop` / `feature/<issue>-<slug>`; todos los PRs a `develop`; el usuario mergea.
- Conventional Commits con footers `Closes #N`, `Implements: CA-XX`, `Rule: RN-XX`.
- Trazabilidad: UC-XX (casos de uso) → CA-XX (criterios de aceptación en issues y nombres de tests) → RN-XX (reglas de negocio comentadas en el código).
- Issues con labels `status:ready` / `status:blocked`; GitHub Project con Todo / In progress / Done.
- Implementación por capas: migración → models/repository/service → endpoint/worker → tests (aislamiento multi-tenant obligatorio, cross-tenant = 404).
- Formato de error custom `{ "error": { code, message, field, details } }`; `organization_id` siempre del JWT.

## 7. Bootstrap de GitHub (cuando los SDDs estén aprobados)

1. Crear rama `develop` en ambos repos (y protegerla junto a `main` si se desea).
2. Crear un GitHub Project por repo (owner `martingalara-klg`) con columnas Todo / In progress / Done.
3. Crear labels: `status:ready`, `status:blocked`, `sdd:divergence`.
4. Crear milestones por fase del roadmap.
5. Generar issues del roadmap desde las specs: título, SDD de referencia, CA-XX como checkboxes, secciones `## Depende de` / `## Bloquea a`, label inicial según dependencias.
6. Configurar el secret/token que necesita `sync-sdd-to-frontend.yml` (ver `docs/ops/ci-sync-sdd-setup.md` de clarix como guía).

## 8. Orden de construcción del sistema documental

| Paso | Entregable | Gate de revisión |
|---|---|---|
| 1 | Esqueleto copiado/adaptado en ambos repos (skills, prompts, workflows, runbooks locales) | Revisión rápida del usuario |
| 2 | `project_adminprop.md` + `sdd_01_prd.md` (a partir de la descripción del negocio del usuario) | Usuario aprueba |
| 3 | `sdd_02_domain_model.md` + `spec_data_model.md` | Usuario aprueba |
| 4 | `sdd_03_api_contracts.md` + `sdd_04_nonfunctional.md` | Usuario aprueba |
| 5 | Specs de módulo (00–07 + notificaciones), de a una o en tandas chicas | Usuario aprueba cada una |
| 6 | `CLAUDE.md` de ambos repos + `_index.md` final | Usuario aprueba |
| 7 | Bootstrap GitHub + issues del roadmap | Usuario aprueba la lista de issues |
| 8 | Ejecutar `session-start.md` — el agente toma la primera tarea | — |

## 9. Criterio de éxito

Una sesión nueva de Claude Code en `adminprop-back` puede leer `docs/prompts/session-start.md`, seleccionar un issue `status:ready` del Project, presentar su plan, implementarlo por capas siguiendo los skills y las specs, y abrir un PR a `develop` trazado con CA-XX/RN-XX — sin que falte ningún documento referenciado.

## 10. Fuera de alcance de este diseño

- El contenido de negocio de los SDDs (se define en los pasos 2–5 con el usuario).
- Infraestructura cloud y deploy.
- Todo lo listado como post-MVP (AFIP, portal externo, KPIs avanzados).
