---
name: AdminProp — Índice maestro de SDDs
description: Mapa de los 15 SDDs de AdminProp, dependencias, registro de decisiones arquitectónicas, estructura de repos y convención de versionado
type: project
version: 1.0
fecha: 2026-08-06
---

# AdminProp — Índice maestro de Specification-Driven Development

Este archivo es el **mapa de navegación** de toda la documentación SDD del proyecto. Antes de implementar cualquier feature, leer el SDD correspondiente. Si el código del repo (backend o frontend) diverge del SDD, **el SDD manda**: señalar la divergencia y esperar instrucción.

---

## 1. Tabla maestra de SDDs

| Código | Nombre | Categoría | Estado | Versión | Backend | Frontend |
|---|---|---|---|---|---|---|
| `project_adminprop` | Contexto general del proyecto | (raíz) | Activo | 1.0 | ✅ | ✅ |
| `sdd_01_prd` | Product Requirements Document (UC-01..UC-20, R-XX, S-XX) | core | Activo | 1.1 | ✅ | ✅ |
| `sdd_02_domain_model` | Modelo de dominio + invariantes (RN-C/P/L/A/D) | core | Activo | 1.9 | ✅ | ✅ (lectura) |
| `sdd_03_api_contracts` | Contratos REST de la API | core | Activo | 1.17 | ✅ | ✅ **compartido — vinculante** |
| `sdd_04_nonfunctional` | Requisitos no funcionales | core | Activo | 1.1 | ✅ | ✅ (parcial: §2) |
| `spec_module_00_superadmin` | Módulo 0 — Super Admin & onboarding | core | Activo | 1.0 | ✅ | ✅ (rutas /superadmin) |
| `spec_module_01_propiedades` | Módulo 1 — Propiedades y cuentas de servicio | features | Activo | 1.4 | ✅ | ✅ |
| `spec_module_02_personas` | Módulo 2 — Propietarios e inquilinos | features | Activo | 1.1 | ✅ | ✅ |
| `spec_module_03_contratos` | Módulo 3 — Contratos + ajustes por índice | features | Activo | 1.7 | ✅ | ✅ |
| `spec_module_04_cobranzas` | Módulo 4 — Cobranzas y mora | features | Activo | 1.3 | ✅ | ✅ |
| `spec_module_05_liquidaciones` | Módulo 5 — Liquidaciones a propietarios | features | Activo | 1.1 | ✅ | ✅ |
| `spec_module_06_mantenimiento` | Módulo 6 — Mantenimiento y cotizaciones | features | Activo | 1.0 | ✅ | ✅ |
| `spec_module_07_administracion` | Módulo 7 — Usuarios, roles, configuración, auditoría | features | Activo | 1.0 | ✅ | ✅ |
| `spec_data_model` | Modelo de datos físico (23 tablas, RLS, migraciones) | infrastructure | Activo | 1.5 | ✅ | ⚪ |
| `spec_notificaciones` | Notificaciones (transversal: in-app + email) | infrastructure | Activo | 1.1 | ✅ | ✅ (panel in-app) |

**Leyenda:** ✅ referencia primaria (leer antes de implementar) · ⚪ referencia secundaria · ❌ no aplica.

---

## 2. Dependencias entre SDDs

```
project_adminprop ──► sdd_01_prd
                          │
        ┌─────────────────┼──────────────────┐
        ▼                 ▼                  ▼
  sdd_02_domain     sdd_03_api         sdd_04_nonfunctional
  (RN-XX)           (contratos REST)   (seguridad, workers)
        │                 │                  │
        └────────┬────────┴─────────┬────────┘
                 ▼                  ▼
        spec_data_model     spec_notificaciones
        (22 tablas, RLS)    (eventos, retry)
                 │
                 ▼
   spec_module_00_superadmin ──► spec_module_07_administracion
                 │
     ┌───────────┼───────────┐
     ▼           ▼           ▼
  spec_01     spec_02     (personas antes que propiedades:
  propiedades personas     properties.landlord_id)
     └─────┬─────┘
           ▼
  spec_module_03_contratos (+ ajustes por índice)
           │
           ▼
  spec_module_04_cobranzas
           │
     ┌─────┴──────────────┐
     ▼                    ▼
  spec_module_05      spec_module_06
  liquidaciones ◄──── mantenimiento (gastos payer=agency)
```

### Reglas de lectura

- **Implementar un módulo de `features/`** → leer `sdd_02`, `sdd_03`, el SDD del módulo y `spec_data_model` (sus tablas). Si tiene UI: además `sdd_04` §2.
- **Implementar cobranzas o liquidaciones** → leer también `spec_module_03_contratos` (el monto vigente y la mora nacen del contrato).
- **Implementar auth/usuarios** → `sdd_04` §2.2/2.2a, `sdd_03` §1, `spec_module_00` (flujo de activación), `spec_module_07` §RF-01/02.
- **Cualquier worker o job** → `sdd_04` §1.3 + `spec_notificaciones` §RF-04.

---

## 3. Glosario

El glosario unificado del dominio vive en `sdd_02_domain_model.md` §5. Términos clave para no perderse:

- **Tenant = organización (administradora)** — nunca el inquilino.
- **Landlord = propietario · Renter = inquilino** (naming para evitar la colisión con "tenant").
- **Período de alquiler** (rent_period), **día de gracia**, **dinero ya rendido**, **cargo del mes**, **Paga: Dueño/Administración**, **liquidación/rendición** — ver definiciones completas en `sdd_02` §5.

---

## 4. Decisiones arquitectónicas registradas

Numeración heredada del sistema de referencia donde los skills la citan (#2..#66); las decisiones propias de AdminProp continúan desde #101.

| # | Decisión | Origen | Estado |
|---|---|---|---|
| 1 | Stack: Python FastAPI + PostgreSQL 16 + Celery/Redis; React 18 + Vite + TS | Diseño §2, `sdd_01` R-07 | ✅ Tomada |
| 2 | Multi-tenant **shared schema** con `organization_id` y RLS PostgreSQL (+FORCE) | `sdd_04` §2.3, `spec_data_model` | ✅ Tomada |
| 3 | `organization_id` siempre derivado del JWT — nunca de body/path/query | `sdd_03` §Convenciones | ✅ Tomada |
| 5 | No auto-registro público — toda alta nace de invitación | `spec_module_00`, `spec_module_07` | ✅ Tomada |
| 6 | RBAC data-driven con permisos atómicos en JWT (`permissions[]`) | `sdd_03` §Catálogo de Permisos | ✅ Tomada |
| 7 | Roles de sistema (`owner`/`admin`/`maintenance`) inmutables; custom post-MVP | `spec_module_07` RF-03 | ✅ Tomada |
| 8 | Procesamiento async vía Celery + Redis; workers separados `notification_worker` + `documents_worker` + Beat | `sdd_04` §1.3 | ✅ Tomada |
| 9 | Soft delete universal (`deleted_at`); nunca DELETE físico | RN-D02, `spec_data_model` Apéndice B | ✅ Tomada |
| 20 | **JWT en HttpOnly Secure cookies** (no localStorage); refresh 30d rotativo single-use | `sdd_04` §2.2 | ✅ Tomada |
| 23 | Naming Alembic: **timestamp ISO** (`YYYYMMDD_HHMMSS_<slug>.py`) | `spec_data_model` Apéndice A | ✅ Tomada |
| 24 | Logging JSON con **`python-json-logger`** | `sdd_04` §4.1 | ✅ Tomada |
| 27 | Sin PgBouncer en MVP (escala chica); se incorpora con la infra cloud | `sdd_04` §1.2 | ✅ Tomada |
| 28 | Rate limiter: **Redis token bucket** | `sdd_04` §2.5 | ✅ Tomada |
| 42 | **Bypass de RLS para Super Admin:** rol PostgreSQL `adminprop_superadmin` con `BYPASSRLS`; el resto opera con `adminprop_app` | `sdd_04` §2.3 | ✅ Tomada |
| 43 | **CSRF:** `SameSite=Lax` + `Secure` + HttpOnly; sin header CSRF custom | `sdd_04` §2.4 | ✅ Tomada |
| 44 | Cobertura mínima de tests backend: **95%** (excluye DTOs, modelos declarativos, boilerplate) | `testing.md` | ✅ Tomada |
| 49 | **No existe endpoint de switch de organización** — logout + login | `sdd_03` §Convenciones | ✅ Tomada |
| 50 | SDDs viven en `adminprop-back/docs/sdd/`; sync a `adminprop-front` vía PR automático de CI (solo `docs/sdd/**`) | Diseño §5, `sync-sdd-to-frontend.yml` | ✅ Tomada |
| 66 | **Seed híbrida:** global en migraciones (ninguno en MVP) + per-tenant en `OrganizationProvisioningService` (roles + settings) | `spec_data_model` §Estrategia de Seed Data | ✅ Tomada |
| 101 | **Ajustes por índice = ingreso manual del %**; el índice del contrato (ICL/IPC Córdoba/otro) es informativo; sin tablas de índices ni APIs externas | `sdd_01` S-03, `spec_module_03` RF-04 | ✅ Tomada |
| 102 | **Mora sugerida, imputación libre:** % diario por contrato, día de gracia por org (default 10, día 11 = 1 día); perdón total/parcial siempre registrado | `sdd_01` S-05, RN-P02..P04 | ✅ Tomada |
| 103 | **Liquidación toda en ARS** con TC manual obligatorio si hay USD; detalle con valor original + convertido | RN-L06, `spec_module_05` | ✅ Tomada |
| 104 | **Comisión** = % por propietario × (alquileres + intereses cobrados), incluidos los cobros directos ("ya rendido") | RN-L02, decisión 2026-08-06 | ✅ Tomada |
| 105 | **Liquidaciones editables/regenerables** con rastro completo en auditoría (sin inmutabilidad de documentos); cobros se anulan (void), nunca se editan | `sdd_01` R-04, RN-L03/RN-D04 | ✅ Tomada |
| 106 | **MFA post-MVP**; login simple con lockout y anti-enumeration | `sdd_04` §2.2b, decisión 2026-08-05 | ✅ Tomada |
| 107 | **Frontend: una única app Vite** (sin Turborepo); rutas `/superadmin/*` protegidas por `is_super_admin` | Diseño §5 | ✅ Tomada |
| 108 | **Reparaciones con pagador** (`Paga: Dueño / Administración`): agency → descuenta en liquidación; landlord → solo historial | `spec_module_06`, RN-L04 | ✅ Tomada |
| 109 | Naming del dominio: `landlord`/`renter` en código; "tenant" reservado para la organización | `sdd_02` §5, `spec_data_model` | ✅ Tomada |
| 110 | `ACCOUNT_LOCKED` responde **403** (con countdown) | `sdd_03` §Códigos | ✅ Tomada |
| 111 | **Infra cloud diferida:** MVP corre en Docker Compose local; archivos en filesystem (volumen); sin CD (merge a develop = solo CI) | Diseño §2, `sdd_04` §3.1 | ✅ Tomada |
| 112 | El % de ajuste puede ser **negativo** (renegociación a la baja) con confirmación explícita en UI | `spec_module_03` §Validaciones, decisión 2026-08-06 | ✅ Tomada |
| 113 | **Recibo de cobro opcional** (PDF sincrónico bajo demanda) y **certificado de libre deuda** (solo sin saldos impagos, auditado) | `spec_module_04` RF-07/RF-08, RN-P08, decisión 2026-08-11 | ✅ Tomada |
| 114 | **Depósito de garantía: fuera del alcance del MVP** — no se registra ni trackea | `sdd_01` §4 Futuras, decisión 2026-08-11 | ✅ Tomada |
| 115 | **Evento `quote_approved` agregado al MVP** (aviso al encargado al aprobarse su cotización — cierra la brecha CA-06-03 vs tabla de eventos detectada en el issue #26; se implementa en #31). **WhatsApp como canal: post-MVP** — MVP notifica por email + in-app | `spec_notificaciones` v1.1, `sdd_02` §2.16 v1.2, decisión 2026-08-20 | ✅ Tomada |
| 116 | **Permiso atómico `landlord:set-commission`** agregado al catálogo (solo `owner`), reemplazando el chequeo por nombre de rol (`payload.role`) que el PR #50 (issue #13) había introducido por ausencia de un permiso dedicado | `sdd_03` v1.5, decisión 2026-08-24, issue #51 | ✅ Tomada |
| 117 | **`login`/`accept-invitation` exponen `permissions[]` e `is_super_admin`** (mismos valores que el JWT emitido) y se agrega `GET /auth/me` para rehidratar la sesión — el front no puede leer el JWT porque vive en cookie HttpOnly (decisión #20) | `sdd_03` v1.6, decisión 2026-08-24, issue #84 | ✅ Tomada |
| 118 | **`GET /rent-periods/:id` embebe `payments[]`** (no endpoint nuevo) — cobros anulados incluidos (CA-04-07 verificable por API); el motivo de anulación no viaja acá, se consulta vía `audit_logs`/visor de auditoría. `GET /rent-periods` (panel) no cambia, sigue liviano | `sdd_03` v1.7, decisión 2026-08-25, issue #87 | ✅ Tomada |
| 119 | **CORS deshabilitado por default** (`CORS_ALLOWED_ORIGINS` vacío ⇒ sin `CORSMiddleware`); con orígenes configurados, siempre exactos + `allow_credentials=True` (nunca `*`). No sustituye mismo-origen: las cookies `SameSite=Lax` (#43) no viajan cross-site aunque CORS lo permita — el despliegue recomendado sigue siendo front+API bajo el mismo origen | `sdd_04` v1.1 §2.4a, decisión 2026-08-26, issue #90 | ✅ Tomada |
| 120 | **Catálogo de barrios parametrizable por organización** (`neighborhoods`): `name` único por org case-insensitive, soft delete protegido por dependencias. `properties.neighborhood_id` **nullable en DB** (datos legacy preexistentes) pero **obligatorio en la API** para create/update de propiedades de ahora en más. ABM vía `property:manage`/`property:read` — sin permisos nuevos | `sdd_02` v1.3, `sdd_03` v1.8, `spec_data_model` v1.2, `spec_module_01` v1.1, decisión 2026-08-27, issue #99 | ✅ Tomada |
| 121 | **Alta de contrato en curso — declarar monto vigente:** `POST /contracts` acepta `current_amount` + `current_amount_since` opcionales (solo juntos), aplicable a ARS y USD. El sistema registra un `ContractAdjustment` sintético ya `applied` (`pct_applied` nulo, `notes` prefijado `"Carga inicial:"`) que ancla la detección del próximo ajuste periódico (`detect_due_adjustments`, RN-C03) sin modificar esa lógica; `current_amount` reemplaza a `initial_amount` como monto de arranque | `sdd_02` v1.4 (RN-C06), `sdd_03` v1.9, `spec_module_03` v1.1, decisión 2026-08-27, issue #100 | ✅ Tomada |
| 122 | **`duplex` agregado al catálogo de `property_type`**, que pasa de texto libre sugerido a **catálogo cerrado** (`CHECK` en DB + `Literal` en la API): `departamento`/`casa`/`duplex`/`local`/`cochera`/`otro` | `sdd_02` v1.5, `spec_data_model` v1.3, `spec_module_01` v1.2, decisión 2026-08-28, issue #103 | ✅ Tomada |
| 123 | **Certificado de libre deuda: es POR CONTRATO, no por inquilino** (feedback #2 del PO): un inquilino puede alquilar 2 propiedades y deber en una sí y en otra no. `POST /renters/:id/debt-certificate` se reemplaza por `POST /contracts/:id/debt-certificate` (permiso `contract:read`), que verifica SOLO los períodos del contrato del path. El error `RENTER_HAS_DEBT` se **renombra** a `CONTRACT_HAS_DEBT` (no se mantiene con semántica nueva — el código debe reflejar el recurso real) | `sdd_02` v1.6 (RN-P08), `sdd_03` v1.10, `spec_module_04` v1.2, decisión 2026-08-28, issue #104 | ✅ Tomada |
| 124 | **Permiso atómico `contract:terminate`** agregado al catálogo (solo `owner`, exclusivo): feedback #2 del PO — terminar un contrato dejó de ser posible para `admin` (hasta ahora lo permitía `contract:manage`, compartido con el resto del ciclo de vida del contrato). Mismo patrón que `landlord:set-commission` (decisión #116): migración de backfill agrega el permiso al rol `owner` de organizaciones ya existentes | `sdd_03` v1.11, decisión 2026-08-28, issue #105 | ✅ Tomada |
| 125 | **`GET /contracts/:id` expone `monthly_amounts[]`** (serie mensual de valores locativos, mes actual primero): feedback #2 del PO — cálculo determinístico en el backend desde `initial_amount` + ajustes `applied` por `due_period` (incluye la "Carga inicial" sintética del issue #100). Como `contracts` no tiene columna propia de fecha de terminación anticipada, la fecha de corte de un contrato `terminated` se deriva del evento `contract.terminated` más reciente en `audit_logs` (mismo timestamp que la transición de estado, misma transacción); `expired` usa `end_date` directamente (vencimiento natural, sin ambigüedad) | `sdd_03` v1.12 (RN-C: RN-09 de `spec_module_03`), `spec_module_03` v1.2, decisión 2026-08-28, issue #106 | ✅ Tomada |
| 126 | **Alta de contrato en curso v2 — cadena guiada de valores históricos** (feedback #2 del PO, supersede PARCIALMENTE la decisión #121/issue #100): para contratos con `adjustment_frequency_months` configurado (solo ARS), `POST /contracts` reemplaza `current_amount`+`current_amount_since` por `historical_amounts[]` — lista ordenada de montos, uno por cada tramo transcurrido desde `start_date` (el backend deriva las fechas de cada tramo, valida la cantidad exacta con `400 VALIDATION_ERROR` si no coincide, y valida `historical_amounts[0] == initial_amount`). El sistema crea una cadena de `ContractAdjustment` sintéticos `applied` "Carga inicial" (uno por tramo a partir del segundo, encadenados) y `current_amount` queda en el último valor; el ancla de `detect_due_adjustments` (RN-C03) no cambia. Para contratos SIN `adjustment_frequency_months` (USD siempre; ARS sin ajuste) se mantiene sin cambios el mecanismo de un único valor del issue #100 (`current_amount`+`current_amount_since`), formalizado como caso explícito — los dos mecanismos son mutuamente excluyentes. Datos ya cargados con el issue #100 siguen válidos (ajustes `applied` normales, sin migración) | `sdd_02` v1.7 (RN-C06 v2), `sdd_03` v1.13, `spec_module_03` v1.3, decisión 2026-08-28, issue #107 | ✅ Tomada |
| 127 | **Historial de ajustes expone `applied_by_name` y `pct_effective` (calculado)** (feedback #3 del PO): "Aplicado por" mostraba el UUID crudo del usuario, y la columna % quedaba vacía en los ajustes de carga inicial (`pct_applied` `NULL`, issues #100/#107). El backend resuelve `full_name` de `users` (`applied_by_name`, `null` mientras `pending`) y calcula `pct_effective` en `Decimal` con `ROUND_HALF_EVEN` para TODO ajuste `applied` (única fuente confiable del % en la carga inicial; para los manuales normalmente coincide con `pct_applied`, que usa `ROUND_HALF_UP`); `previous_amount = 0` o ajuste `pending` → `null`. Mismo `AdjustmentSummary` en `GET /contracts/:id/adjustments`, `GET /adjustments` y `POST /adjustments/:id/apply` — sin migración (campos derivados, no persistidos) | `sdd_03` v1.14, `spec_module_03` v1.4 (RN-10), decisión 2026-08-29, issue #118 | ✅ Tomada |
| 128 | **Alta de contrato en curso genera los meses transcurridos como cobrados** (feedback #3 del PO, 2026-08-29): al dar de alta un contrato con `start_date` anterior al mes actual, el backend genera automáticamente, en la MISMA transacción del alta, un `RentPeriod` `paid` + un `Payment` `origin = initial_load` (columna nueva, `NOT NULL DEFAULT 'manual' CHECK IN ('manual','initial_load')`) por cada mes transcurrido — mismo disparador que `historical_amounts[]`/RN-08, incluido el caso sin tramos (contrato iniciado el mes pasado sin ajuste). El cobro `initial_load`: destino `landlord_account` (dinero ya cobrado directamente por el propietario antes del alta — decisión de implementación, `landlord_account` no alcanzaba por sí solo para excluirlo de comisión, de ahí la columna dedicada), `payment_date` = día 1 del período, interés 0, sin TC, `notes` literal del issue. Queda TOTALMENTE excluido de liquidaciones (ni neto ni comisión, RN-L02/RN-06 de `spec_module_05`), no emite recibo ni admite anulación (`422 BUSINESS_RULE_VIOLATION` en ambos, reutilizando el código existente — sin `error.code` nuevo). El mes actual sigue naciendo `pending` sin cambios. Auditado con evento resumen `contract.initial_load_generated` | `sdd_02` v1.8 (RN-P09/RN-C07), `sdd_03` v1.15, `spec_module_03` v1.5 (RN-11), `spec_module_04` v1.3 (RN-08), `spec_module_05` v1.1 (RN-06), `spec_data_model` v1.4, decisión 2026-08-29, issue #119 | ✅ Tomada |
| 129 | **`ContractSummary` expone `property_address`, `property_neighborhood` y `renter_name` (denormalizados de solo lectura)** (feedback #4 del PO): el front agrupa el listado de contratos por barrio mostrando dirección e inquilino — el backend resuelve los tres campos por JOIN (`properties` → LEFT `neighborhoods`, `renters`) en el mismo query del repository, sin N+1 (mismo criterio que la decisión #127/issue #118). `property_neighborhood` es `null` si la propiedad no tiene barrio asignado. Aplica a todo endpoint que devuelve `ContractSummary` (`GET /contracts`, `POST`/`PATCH`/`activate`/`terminate`) y a `ContractDetail` (`GET /contracts/:id`, que lo hereda). Sin migración (campos derivados, no persistidos); enviarlos en un body es `400 VALIDATION_ERROR`; la resolución no filtra `deleted_at` de las tablas referenciadas | `sdd_03` v1.16, `spec_module_03` v1.6 (RN-12), decisión 2026-08-31, issue #123 | ✅ Tomada |
| 130 | **Borrado lógico y reglas de eliminación** (feedback #4 del PO, 2026-08-31): (a) propiedades e inquilinos vinculados a un contrato `active` NO se eliminan → `422 ENTITY_HAS_ACTIVE_CONTRACT` (código nuevo, con `details.active_contracts[]` estructurado — precedente `CONTRACT_HAS_DEBT`/#104); con solo contratos inactivos (o ninguno) la baja es LÓGICA (`deleted_at`): desaparecen de listados/selects (detalle → 404, no elegibles para altas nuevas) pero la trazabilidad queda intacta (contratos históricos, cobros, liquidaciones, auditoría y display RN-12 siguen referenciándolos; la deuda de contratos históricos NO eliminados sigue computándose). (b) `DELETE /contracts/:id` nuevo, permiso atómico **`contract:delete`** SOLO en `owner` (precedente #105 `contract:terminate`; backfill por migración con guarda `jsonb_typeof='array'` — lección #116): el owner elimina CUALQUIER contrato, incluso `active`, siempre lógico — se detiene la generación de períodos futuros (jobs Beat + hooks ignoran eliminados), la propiedad vuelve a `available`, la deuda del contrato deja de computarse (panel/deuda/advertencias excluyen sus períodos; sin cobros nuevos), un ajuste `pending` sale de la bandeja, y los cobros/liquidaciones ya emitidos quedan intactos. (c) Auditoría: `property.deleted`, `renter.deleted`, `contract.deleted`. Mantenimiento/cuentas de servicio de una propiedad eliminada: legibles históricamente vía endpoints top-level, sin acciones nuevas (POST sobre propiedad eliminada → 404) | `sdd_02` v1.9 (RN-C08/RN-D05), `sdd_03` v1.17, `spec_module_01` v1.4, `spec_module_02` v1.1, `spec_module_03` v1.7 (RF-07/RN-13), `spec_data_model` v1.5, decisión 2026-08-31, issue #124 | ✅ Tomada |

### Decisiones aún pendientes

*(Ninguna bloqueante para iniciar la implementación. Operativas del bootstrap — `PROJECT_NUMBER`, secret del sync, promoción develop→main — registradas en el handoff del esqueleto.)*

---

## 5. Estructura de repositorios y referencia primaria

Dos repositorios independientes comunicados por la API REST de `sdd_03`:

- **`adminprop-back`** (Python FastAPI): fuente de verdad de los SDDs (`docs/sdd/`). Referencia primaria: todos los SDDs.
- **`adminprop-front`** (React 18 + Vite + TS): recibe `docs/sdd/` como **copia sincronizada vía CI** (PR automático en cada push a `main` de adminprop-back que toque `docs/sdd/**`). Nunca editar SDDs en el front. Referencia primaria: `sdd_01`, `sdd_03` (vinculante), `sdd_04` §2, todas las specs de módulo (cada flujo UI tiene una) y `spec_notificaciones` (panel in-app).

### SDDs compartidos — sincronización obligatoria

| SDD | Implicancia |
|---|---|
| `sdd_03_api_contracts` | **Contrato vinculante.** Todo cambio se escribe acá ANTES de implementarse en cualquier repo. El front nunca compensa divergencias: reporta. |
| `sdd_02_domain_model` | Las RN-XX son la única fuente de verdad de comportamiento; el front valida con Zod un subconjunto, el back enforza el total. |
| `sdd_04_nonfunctional` | Reglas de seguridad y comportamiento ante 401/403/429/5xx atraviesan ambos repos. |

---

## 6. Estado de los SDDs

Todos los SDDs están en versión **1.0**, estado **Activo** (aprobados por el usuario durante los pasos 2–5 del diseño, 2026-08-04 → 2026-08-06).

**Convención de versionado:**

- `1.0` = primera versión funcional para iniciar implementación.
- `1.x` = adiciones backwards-compatible (nuevos endpoints opcionales, nuevos campos opcionales).
- `2.0` = breaking changes (renombre, eliminación, cambio de contrato).

Los breaking changes a SDDs compartidos (`sdd_03`, `sdd_02`, `sdd_04`) requieren actualizar este índice y coordinar back+front antes del merge.
