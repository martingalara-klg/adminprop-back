---
name: AdminProp — Especificación del Modelo de Datos
description: Tablas físicas PostgreSQL (22 tablas en 8 capas), RLS, índices, orden de migración, seed data y convenciones de nomenclatura
type: project
version: 1.1
fecha: 2026-08-11
---
# AdminProp — Especificación del Modelo de Datos

**Versión:** 1.1
**Estado:** Borrador para revisión
**Fecha:** 2026-08-05

---

## Principios Arquitectónicos

**Estrategia multi-tenant:** base de datos compartida, schema compartido con `organization_id` en cada tabla tenant-scoped. Row-Level Security (RLS) de PostgreSQL enforza el aislamiento a nivel de base de datos.

**Implementación de RLS:**

- En cada tabla tenant-scoped:
  ```sql
  ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
  CREATE POLICY <t>_tenant_isolation ON <t>
    USING (organization_id = current_setting('app.current_tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.current_tenant_id', true)::uuid);
  ALTER TABLE <t> FORCE ROW LEVEL SECURITY;
  ```
- El backend setea el contexto al inicio de cada request: `SET LOCAL app.current_tenant_id = '<jwt.org>'`.
- **Tablas excluidas de RLS** (globales, sin `organization_id`): `users`.
- **Bypass para Super Admin:** rol PostgreSQL **`adminprop_superadmin`** con `BYPASSRLS`, usado solo cuando el JWT tiene `is_super_admin: true`; el resto opera con `adminprop_app` (sujeto a RLS). Las operaciones del superadmin se auditan.
- **Defense in depth:** además de RLS, todo repositorio filtra `WHERE organization_id = :org` explícitamente (ver skill `tenant-isolation.md`).

**Filosofía de persistencia:**
- **PostgreSQL relacional:** todos los datos operativos.
- **PostgreSQL JSONB:** `settings` de la org, `payload` de notificaciones, `before/after` de auditoría, `metadata` extensible.
- **Redis:** broker Celery, caché de listados, rate limiting.
- **Filesystem local (volumen Docker) en MVP:** archivos binarios (fotos de reparaciones, exports Excel/PDF de liquidaciones) — nunca en la DB. Convención de columna: `file_path TEXT` en `attachments`. Storage cloud post-infra.

**Convenciones de nomenclatura (ver Apéndice A):**
- **Entidades de dominio (sdd_02):** PascalCase singular (`Contract`, `RentPeriod`, `WorkOrder`).
- **Tablas:** snake_case plural (`contracts`, `rent_periods`, `work_orders`).
- **Columnas:** snake_case (`organization_id`, `daily_late_fee_pct`).
- **Enums en TEXT + CHECK** (nunca tipos ENUM de PG): snake_case minúsculas (`'agency_account'`, `'ipc_cordoba'`).
- **Fechas:** `TIMESTAMPTZ` para eventos auditables (`created_at`, `applied_at`); `DATE` para fechas operativas (`start_date`, `payment_date`); los períodos mensuales son `DATE` normalizado al día 1 del mes (`period`), con CHECK.
- **Montos:** `NUMERIC(14,2)`; tipos de cambio y porcentajes: `NUMERIC(14,4)`. Nunca `FLOAT`.
- **PKs:** UUID v4 (`gen_random_uuid()` de `pgcrypto`), nunca `BIGSERIAL`.
- **Soft delete:** `deleted_at TIMESTAMPTZ NULL` en toda tabla operativa; GETs filtran `deleted_at IS NULL` por default.

**Nota de naming del dominio:** el propietario es `landlord` y el inquilino es `renter` en todo el código y schema — "tenant" queda reservado exclusivamente para la organización (multi-tenancy).

---

## Capa 0 — Fundación (Multi-tenancy e Identidad)

### `organizations`
**Por qué existe:** entidad raíz del tenant (la administradora). Límite del aislamiento de datos.

| Columna | Tipo | Restricciones | Notas |
|---|---|---|---|
| id | UUID | PK | `gen_random_uuid()` |
| slug | TEXT | UNIQUE, NOT NULL | URL-safe (ej: `galara-propiedades`) |
| name | TEXT | NOT NULL | Nombre visible |
| status | TEXT | NOT NULL DEFAULT `'pending_owner'` CHECK IN (`pending_owner`,`active`,`disabled`) | |
| timezone | TEXT | NOT NULL DEFAULT `'America/Argentina/Cordoba'` | IANA tz |
| settings | JSONB | NOT NULL DEFAULT `'{}'` | `grace_day` (default 10), `contract_expiry_notice_days` (default 60), encabezado de liquidaciones (nombre, CUIT, contacto) |
| created_at / updated_at | TIMESTAMPTZ | NOT NULL DEFAULT now() | |
| deleted_at | TIMESTAMPTZ | NULL | Soft delete |

Sin RLS (es la raíz); el acceso se controla por membresía. Solo `adminprop_superadmin` la escribe.

---

### `users`
**Por qué existe:** identidad global de login. Un user puede pertenecer a varias organizaciones.

| Columna | Tipo | Restricciones | Notas |
|---|---|---|---|
| id | UUID | PK | |
| email | TEXT | UNIQUE, NOT NULL | Login |
| password_hash | TEXT | NOT NULL | bcrypt |
| full_name | TEXT | NOT NULL | |
| is_super_admin | BOOLEAN | NOT NULL DEFAULT false | Empleado de la plataforma |
| created_at / updated_at | TIMESTAMPTZ | NOT NULL DEFAULT now() | |
| deleted_at | TIMESTAMPTZ | NULL | |

**Global — excluida de RLS.**

---

### `roles`
**Por qué existe:** RBAC data-driven. Roles de sistema sembrados por organización.

| Columna | Tipo | Restricciones | Notas |
|---|---|---|---|
| id | UUID | PK | |
| organization_id | UUID | NOT NULL, FK → organizations | RLS |
| name | TEXT | NOT NULL | `owner` / `admin` / `maintenance` |
| permissions | JSONB | NOT NULL | Array de permisos atómicos (`"contract:manage"`, `"work-order:read"`, …) |
| is_system_role | BOOLEAN | NOT NULL DEFAULT true | Roles de sistema inmutables |
| created_at / updated_at | TIMESTAMPTZ | NOT NULL | |
| | | UNIQUE (organization_id, name) | |

---

### `organization_members`
**Por qué existe:** la membresía user ↔ org con su rol.

| Columna | Tipo | Restricciones | Notas |
|---|---|---|---|
| id | UUID | PK | |
| organization_id | UUID | NOT NULL, FK | RLS |
| user_id | UUID | NOT NULL, FK → users | |
| role_id | UUID | NOT NULL, FK → roles | |
| status | TEXT | NOT NULL DEFAULT `'active'` CHECK IN (`active`,`inactive`) | |
| created_at / updated_at | TIMESTAMPTZ | NOT NULL | |
| | | UNIQUE (organization_id, user_id) | Un rol por org |

**Invariante app-level:** siempre ≥ 1 owner activo (RN-A03, `LAST_OWNER_REQUIRED`).

---

### `organization_invitations`
**Por qué existe:** toda alta de usuario nace de invitación (sin auto-registro).

| Columna | Tipo | Restricciones | Notas |
|---|---|---|---|
| id | UUID | PK | |
| organization_id | UUID | NOT NULL, FK | RLS |
| email | TEXT | NOT NULL | |
| role_id | UUID | NOT NULL, FK → roles | |
| token | TEXT | UNIQUE, NOT NULL | Hash del token de invitación |
| status | TEXT | NOT NULL DEFAULT `'pending'` CHECK IN (`pending`,`accepted`,`expired`,`revoked`) | |
| expires_at | TIMESTAMPTZ | NOT NULL | 72h desde la emisión |
| created_at / updated_at | TIMESTAMPTZ | NOT NULL | |

---

## Capa 1 — Personas (Propietarios e Inquilinos)

### `landlords`
**Por qué existe:** el dueño de las propiedades, a quien se le rinde. Registro sin login.

| Columna | Tipo | Restricciones | Notas |
|---|---|---|---|
| id | UUID | PK | |
| organization_id | UUID | NOT NULL, FK | RLS |
| name | TEXT | NOT NULL | Nombre / razón social |
| tax_id | TEXT | | CUIT / DNI |
| phone / email | TEXT | | Contacto |
| bank_info | TEXT | | Datos bancarios para transferencias |
| commission_pct | NUMERIC(14,4) | NOT NULL CHECK (commission_pct >= 0 AND commission_pct <= 100) | % de comisión por administración |
| notes | TEXT | | |
| metadata | JSONB | DEFAULT `'{}'` | |
| created_at / updated_at / deleted_at | TIMESTAMPTZ | | Soft delete |

---

### `renters`
**Por qué existe:** el inquilino. Registro sin login.

| Columna | Tipo | Restricciones | Notas |
|---|---|---|---|
| id | UUID | PK | |
| organization_id | UUID | NOT NULL, FK | RLS |
| name | TEXT | NOT NULL | |
| tax_id | TEXT | | DNI / CUIT |
| phone / email | TEXT | | |
| notes | TEXT | | Garantes, referencias (texto libre en MVP) |
| metadata | JSONB | DEFAULT `'{}'` | |
| created_at / updated_at / deleted_at | TIMESTAMPTZ | | Soft delete |

---

## Capa 2 — Propiedades

### `properties`
**Por qué existe:** el inmueble administrado.

| Columna | Tipo | Restricciones | Notas |
|---|---|---|---|
| id | UUID | PK | |
| organization_id | UUID | NOT NULL, FK | RLS |
| landlord_id | UUID | NOT NULL, FK → landlords | |
| address | TEXT | NOT NULL | Dirección completa |
| property_type | TEXT | NOT NULL DEFAULT `'departamento'` | Texto sugerido en UI: departamento, casa, local, cochera, otro |
| status | TEXT | NOT NULL DEFAULT `'available'` CHECK IN (`available`,`rented`,`unavailable`) | `rented` ⟺ contrato activo |
| notes | TEXT | | |
| metadata | JSONB | DEFAULT `'{}'` | |
| created_at / updated_at / deleted_at | TIMESTAMPTZ | | Soft delete |

---

### `property_service_accounts`
**Por qué existe:** números de cuenta de servicios e impuestos, **solo informativos** (UC-01). Ninguna lógica de negocio depende de ellos.

| Columna | Tipo | Restricciones | Notas |
|---|---|---|---|
| id | UUID | PK | |
| organization_id | UUID | NOT NULL, FK | RLS |
| property_id | UUID | NOT NULL, FK → properties | |
| service_type | TEXT | NOT NULL CHECK IN (`rentas`,`municipalidad`,`luz`,`gas`,`agua`,`expensas`,`otro`) | |
| account_number | TEXT | NOT NULL | N° de cuenta / cliente |
| secondary_number | TEXT | | N° adicional (luz: n° de contrato) |
| notes | TEXT | | |
| created_at / updated_at / deleted_at | TIMESTAMPTZ | | |

---

## Capa 3 — Contratos

### `contracts`
**Por qué existe:** el contrato de locación con sus condiciones (UC-04).

| Columna | Tipo | Restricciones | Notas |
|---|---|---|---|
| id | UUID | PK | |
| organization_id | UUID | NOT NULL, FK | RLS |
| property_id | UUID | NOT NULL, FK → properties | |
| renter_id | UUID | NOT NULL, FK → renters | |
| currency | TEXT | NOT NULL CHECK IN (`ARS`,`USD`) | |
| initial_amount | NUMERIC(14,2) | NOT NULL CHECK (> 0) | |
| current_amount | NUMERIC(14,2) | NOT NULL | Solo cambia vía ajuste (RN-C04) |
| start_date / end_date | DATE | NOT NULL, CHECK (end_date > start_date) | |
| daily_late_fee_pct | NUMERIC(14,4) | NOT NULL CHECK (>= 0) | % de mora diaria del contrato |
| adjustment_frequency_months | SMALLINT | NULL, CHECK (> 0) | Solo ARS; NULL en USD (RN-C02) |
| adjustment_index | TEXT | NULL CHECK IN (`icl`,`ipc_cordoba`,`otro`) | Informativo; solo ARS |
| adjustment_index_notes | TEXT | | Detalle si `otro` |
| status | TEXT | NOT NULL DEFAULT `'draft'` CHECK IN (`draft`,`active`,`expired`,`terminated`) | |
| notes | TEXT | | |
| metadata | JSONB | DEFAULT `'{}'` | |
| created_at / updated_at / deleted_at | TIMESTAMPTZ | | |
| expiring_notified_at | TIMESTAMPTZ | NULL | Marca de idempotencia del aviso de vencimiento (RF-05/CA-03-07, issue #19) |
| | | CHECK: currency='USD' ⇒ adjustment_frequency_months IS NULL AND adjustment_index IS NULL | RN-C02 |

**Invariante RN-C01 (no solapamiento):** constraint de exclusión —
```sql
ALTER TABLE contracts ADD CONSTRAINT contracts_no_overlap
  EXCLUDE USING gist (
    property_id WITH =,
    daterange(start_date, end_date, '[]') WITH &&
  ) WHERE (status = 'active' AND deleted_at IS NULL);
-- requiere CREATE EXTENSION IF NOT EXISTS btree_gist;
```

---

### `contract_adjustments`
**Por qué existe:** el historial de ajustes; el pendiente que el sistema genera y el operador aplica con % manual (UC-05, RN-C03).

| Columna | Tipo | Restricciones | Notas |
|---|---|---|---|
| id | UUID | PK | |
| organization_id | UUID | NOT NULL, FK | RLS |
| contract_id | UUID | NOT NULL, FK → contracts | |
| due_period | DATE | NOT NULL CHECK (= date_trunc('month', due_period)) | Mes al que aplica (día 1) |
| status | TEXT | NOT NULL DEFAULT `'pending'` CHECK IN (`pending`,`applied`) | |
| pct_applied | NUMERIC(14,4) | NULL | NULL mientras `pending` |
| previous_amount / new_amount | NUMERIC(14,2) | | new = previous × (1 + pct/100) |
| notes | TEXT | | Ajustes correctivos explican acá |
| applied_by | UUID | NULL, FK → users | |
| applied_at | TIMESTAMPTZ | NULL | |
| created_at / updated_at | TIMESTAMPTZ | NOT NULL | |
| | | UNIQUE parcial: un solo `pending` por contrato — `CREATE UNIQUE INDEX ON contract_adjustments (contract_id) WHERE status = 'pending'` | |

---

## Capa 4 — Cobranzas

### `rent_periods`
**Por qué existe:** el alquiler de un mes de un contrato; generado el 1° de cada mes (UC-07, RN-P01).

| Columna | Tipo | Restricciones | Notas |
|---|---|---|---|
| id | UUID | PK | |
| organization_id | UUID | NOT NULL, FK | RLS |
| contract_id | UUID | NOT NULL, FK → contracts | |
| period | DATE | NOT NULL CHECK (= date_trunc('month', period)) | Mes (día 1) |
| amount_due | NUMERIC(14,2) | NOT NULL | `current_amount` del contrato al generarse |
| currency | TEXT | NOT NULL CHECK IN (`ARS`,`USD`) | Hereda del contrato |
| status | TEXT | NOT NULL DEFAULT `'pending'` CHECK IN (`pending`,`partial`,`paid`) | "en mora" es derivado (fecha vs grace_day) |
| paid_total | NUMERIC(14,2) | NOT NULL DEFAULT 0 CHECK (paid_total <= amount_due) | Capital imputado |
| created_at / updated_at | TIMESTAMPTZ | NOT NULL | |
| | | UNIQUE (contract_id, period) | RN-P01 |

---

### `payments`
**Por qué existe:** la imputación de un pago (UC-08, UC-09).

| Columna | Tipo | Restricciones | Notas |
|---|---|---|---|
| id | UUID | PK | |
| organization_id | UUID | NOT NULL, FK | RLS |
| rent_period_id | UUID | NOT NULL, FK → rent_periods | |
| payment_date | DATE | NOT NULL | |
| method | TEXT | NOT NULL CHECK IN (`cash`,`transfer`) | |
| payment_currency | TEXT | NOT NULL CHECK IN (`ARS`,`USD`) | Moneda en que pagó |
| amount | NUMERIC(14,2) | NOT NULL CHECK (> 0) | Capital, en moneda del contrato |
| exchange_rate | NUMERIC(14,4) | NULL CHECK (> 0) | Obligatorio si payment_currency ≠ moneda del contrato (RN-P06; validación app-level) |
| destination | TEXT | NOT NULL CHECK IN (`agency_account`,`landlord_account`) | `landlord_account` = ya rendido (RN-P07) |
| suggested_interest | NUMERIC(14,2) | NOT NULL DEFAULT 0 | Calculado por el sistema (RN-P03) |
| charged_interest | NUMERIC(14,2) | NOT NULL DEFAULT 0 | Decisión del operador (RN-P04) |
| forgiven_interest | NUMERIC(14,2) | NOT NULL DEFAULT 0 | suggested − charged |
| days_late | SMALLINT | NOT NULL DEFAULT 0 | Al momento del pago (RN-P02) |
| notes | TEXT | | |
| voided_at | TIMESTAMPTZ | NULL | Anulación lógica (RN-D04) |
| voided_by | UUID | NULL, FK → users | |
| created_by | UUID | NOT NULL, FK → users | |
| created_at / updated_at | TIMESTAMPTZ | NOT NULL | |

---

## Capa 5 — Mantenimiento

### `work_orders`
**Por qué existe:** el pedido de reparación con su ciclo completo (UC-13..UC-16).

| Columna | Tipo | Restricciones | Notas |
|---|---|---|---|
| id | UUID | PK | |
| organization_id | UUID | NOT NULL, FK | RLS |
| property_id | UUID | NOT NULL, FK → properties | |
| title | TEXT | NOT NULL | |
| description | TEXT | | |
| payer | TEXT | NOT NULL CHECK IN (`landlord`,`agency`) | "Paga: Dueño / Administración" |
| status | TEXT | NOT NULL DEFAULT `'open'` CHECK IN (`open`,`in_progress`,`closed`,`cancelled`) | |
| approved_quote_id | UUID | NULL, FK → work_order_quotes | FK agregada por ALTER tras crear quotes |
| final_cost | NUMERIC(14,2) | NULL | De la cotización aprobada; ajustable al cierre |
| settled_in_settlement_id | UUID | NULL | FK → settlements agregada por ALTER en Capa 6 (RN-L04) |
| created_by | UUID | NOT NULL, FK → users | |
| closed_at | TIMESTAMPTZ | NULL | |
| created_at / updated_at / deleted_at | TIMESTAMPTZ | | |

---

### `work_order_quotes`
**Por qué existe:** las cotizaciones del encargado (UC-14).

| Columna | Tipo | Restricciones | Notas |
|---|---|---|---|
| id | UUID | PK | |
| organization_id | UUID | NOT NULL, FK | RLS |
| work_order_id | UUID | NOT NULL, FK → work_orders | |
| amount | NUMERIC(14,2) | NOT NULL CHECK (> 0) | |
| description | TEXT | | |
| status | TEXT | NOT NULL DEFAULT `'submitted'` CHECK IN (`submitted`,`approved`,`discarded`) | Una sola `approved` por pedido |
| submitted_by | UUID | NOT NULL, FK → users | |
| created_at / updated_at | TIMESTAMPTZ | NOT NULL | |
| | | `CREATE UNIQUE INDEX ON work_order_quotes (work_order_id) WHERE status = 'approved'` | |

---

### `attachments`
**Por qué existe:** archivos (fotos, exports) asociados genéricamente a entidades. Vive en esta capa por ser el mantenimiento su primer consumidor; también la usan las liquidaciones.

| Columna | Tipo | Restricciones | Notas |
|---|---|---|---|
| id | UUID | PK | |
| organization_id | UUID | NOT NULL, FK | RLS |
| entity_type | TEXT | NOT NULL CHECK IN (`work_order`,`work_order_quote`,`settlement`,`payment`,`renter`) | `payment` = recibo de cobro; `renter` = libre deuda |
| entity_id | UUID | NOT NULL | Sin FK física (polimórfica); integridad app-level |
| file_path | TEXT | NOT NULL | Filesystem local en MVP |
| file_name / mime_type | TEXT | NOT NULL | |
| size_bytes | BIGINT | NOT NULL | |
| uploaded_by | UUID | NOT NULL, FK → users | |
| created_at / deleted_at | TIMESTAMPTZ | | |

---

## Capa 6 — Liquidaciones

### `recurring_charges`
**Por qué existe:** el concepto recurrente de la propiedad (rentas, muni) (UC-11).

| Columna | Tipo | Restricciones | Notas |
|---|---|---|---|
| id | UUID | PK | |
| organization_id | UUID | NOT NULL, FK | RLS |
| property_id | UUID | NOT NULL, FK → properties | |
| charge_type | TEXT | NOT NULL CHECK IN (`rentas`,`municipalidad`,`otro`) | |
| label | TEXT | NOT NULL | Nombre visible (ej: "Rentas Córdoba") |
| is_active | BOOLEAN | NOT NULL DEFAULT true | |
| created_at / updated_at / deleted_at | TIMESTAMPTZ | | |

---

### `charge_entries`
**Por qué existe:** el importe del mes de un concepto, ingresado a mano (UC-11).

| Columna | Tipo | Restricciones | Notas |
|---|---|---|---|
| id | UUID | PK | |
| organization_id | UUID | NOT NULL, FK | RLS |
| recurring_charge_id | UUID | NOT NULL, FK → recurring_charges | |
| period | DATE | NOT NULL CHECK (= date_trunc('month', period)) | |
| amount | NUMERIC(14,2) | NOT NULL CHECK (>= 0) | Siempre ARS en MVP |
| notes | TEXT | | |
| created_by | UUID | NOT NULL, FK → users | |
| created_at / updated_at | TIMESTAMPTZ | NOT NULL | |
| | | UNIQUE (recurring_charge_id, period) | |

---

### `settlements`
**Por qué existe:** la liquidación mensual por propietario, toda en ARS (UC-12, RN-L01, RN-L06).

| Columna | Tipo | Restricciones | Notas |
|---|---|---|---|
| id | UUID | PK | |
| organization_id | UUID | NOT NULL, FK | RLS |
| landlord_id | UUID | NOT NULL, FK → landlords | |
| period | DATE | NOT NULL CHECK (= date_trunc('month', period)) | |
| status | TEXT | NOT NULL DEFAULT `'draft'` CHECK IN (`draft`,`issued`) | |
| exchange_rate | NUMERIC(14,4) | NULL CHECK (> 0) | Obligatorio si hay montos USD en el período (RN-L06; validación app-level) |
| total_collected | NUMERIC(14,2) | NOT NULL DEFAULT 0 | En ARS |
| commission_total | NUMERIC(14,2) | NOT NULL DEFAULT 0 | RN-L02 |
| charges_total | NUMERIC(14,2) | NOT NULL DEFAULT 0 | |
| repairs_total | NUMERIC(14,2) | NOT NULL DEFAULT 0 | |
| already_settled_total | NUMERIC(14,2) | NOT NULL DEFAULT 0 | Informativo (RN-P07) |
| net_amount | NUMERIC(14,2) | NOT NULL DEFAULT 0 | Neto a rendir en ARS |
| commission_pct_used | NUMERIC(14,4) | NOT NULL | % del propietario al momento de generar (RN-L05) |
| regenerated_count | SMALLINT | NOT NULL DEFAULT 0 | Cada regeneración se audita (RN-L03) |
| generated_by | UUID | NOT NULL, FK → users | |
| issued_at | TIMESTAMPTZ | NULL | |
| created_at / updated_at | TIMESTAMPTZ | NOT NULL | |
| | | UNIQUE (landlord_id, period) | Una por propietario y mes |

**ALTER en esta capa:** `work_orders.settled_in_settlement_id` → FK a `settlements` (referencia diferida entre capas).

---

### `settlement_line_items`
**Por qué existe:** el detalle línea por línea de la liquidación.

| Columna | Tipo | Restricciones | Notas |
|---|---|---|---|
| id | UUID | PK | |
| organization_id | UUID | NOT NULL, FK | RLS |
| settlement_id | UUID | NOT NULL, FK → settlements | |
| line_type | TEXT | NOT NULL CHECK IN (`rent_collected`,`commission`,`tax_charge`,`repair`,`already_settled`) | |
| property_id | UUID | NULL, FK → properties | |
| source_entity_type | TEXT | NULL | `payment` / `charge_entry` / `work_order` |
| source_entity_id | UUID | NULL | Referencia al origen |
| original_amount | NUMERIC(14,2) | NOT NULL | En la moneda original |
| original_currency | TEXT | NOT NULL CHECK IN (`ARS`,`USD`) | |
| amount_ars | NUMERIC(14,2) | NOT NULL | Convertido con `settlements.exchange_rate` si USD |
| description | TEXT | | |
| created_at | TIMESTAMPTZ | NOT NULL | |

---

## Capa 7 — Notificaciones y Auditoría

### `notifications`
**Por qué existe:** avisos in-app + email (UC-20).

| Columna | Tipo | Restricciones | Notas |
|---|---|---|---|
| id | UUID | PK | |
| organization_id | UUID | NOT NULL, FK | RLS |
| user_id | UUID | NOT NULL, FK → users | Destinatario |
| event_type | TEXT | NOT NULL CHECK IN (`adjustment_pending`,`contract_expiring`,`quote_submitted`,`work_order_created`,`work_order_closed`) | |
| payload | JSONB | NOT NULL DEFAULT `'{}'` | IDs y datos del evento |
| read_at | TIMESTAMPTZ | NULL | |
| email_sent_at | TIMESTAMPTZ | NULL | |
| created_at | TIMESTAMPTZ | NOT NULL | |

---

### `audit_logs`
**Por qué existe:** registro append-only de operaciones sensibles (RN-D03, RN-D04).

| Columna | Tipo | Restricciones | Notas |
|---|---|---|---|
| id | UUID | PK | |
| organization_id | UUID | NOT NULL, FK | RLS |
| user_id | UUID | NULL, FK → users | NULL para acciones del sistema |
| action | TEXT | NOT NULL | `payment.voided`, `settlement.regenerated`, `interest.forgiven`, `adjustment.applied`, `commission.changed`, `access.denied`, … |
| entity_type / entity_id | TEXT / UUID | NOT NULL / NULL | |
| before_state / after_state | JSONB | | |
| request_id | TEXT | | Trazabilidad cross-stack |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT now() | |

**Sin UPDATE ni DELETE** (revocados a nivel de permisos del rol `adminprop_app`; solo INSERT y SELECT).

---

## Resumen de Decisiones de Persistencia

| Decisión | Valor |
|---|---|
| Multi-tenancy | Shared schema + `organization_id` + RLS con FORCE en toda tabla tenant-scoped |
| PKs | UUID v4 `gen_random_uuid()` (extensión `pgcrypto`) |
| Enums | TEXT + CHECK, nunca tipos ENUM de PG |
| Money | `NUMERIC(14,2)`; TC y % `NUMERIC(14,4)` |
| Períodos mensuales | `DATE` normalizado al día 1 + CHECK `date_trunc` |
| Soft delete | `deleted_at` en tablas operativas; `payments` usa `voided_at` (anulación con autor) |
| No solapamiento de contratos | Constraint EXCLUDE con `btree_gist` (RN-C01) |
| Archivos | Filesystem local (volumen Docker) vía `attachments.file_path`; storage cloud post-infra |
| Extensiones requeridas | `pgcrypto`, `btree_gist` |

---

## Índices PostgreSQL Recomendados

```sql
-- Multi-tenant: toda tabla tenant-scoped indexa organization_id
CREATE INDEX ON <tabla> (organization_id) WHERE deleted_at IS NULL;   -- patrón general

-- Cobranzas (las consultas más frecuentes del sistema)
CREATE INDEX ON rent_periods (organization_id, period);
CREATE INDEX ON rent_periods (contract_id, period);
CREATE INDEX ON rent_periods (organization_id, status) WHERE status <> 'paid';
CREATE INDEX ON payments (rent_period_id) WHERE voided_at IS NULL;
CREATE INDEX ON payments (organization_id, payment_date);

-- Contratos
CREATE INDEX ON contracts (organization_id, status);
CREATE INDEX ON contracts (property_id) WHERE deleted_at IS NULL;
CREATE INDEX ON contracts (organization_id, end_date) WHERE status = 'active';  -- vencimientos
CREATE UNIQUE INDEX ON contract_adjustments (contract_id) WHERE status = 'pending';

-- Liquidaciones
CREATE UNIQUE INDEX ON settlements (landlord_id, period);
CREATE INDEX ON settlement_line_items (settlement_id);
CREATE INDEX ON charge_entries (organization_id, period);

-- Mantenimiento
CREATE INDEX ON work_orders (organization_id, status);
CREATE INDEX ON work_orders (property_id);
CREATE UNIQUE INDEX ON work_order_quotes (work_order_id) WHERE status = 'approved';
CREATE INDEX ON attachments (entity_type, entity_id);

-- Notificaciones y auditoría
CREATE INDEX ON notifications (user_id) WHERE read_at IS NULL;
CREATE INDEX ON audit_logs (organization_id, created_at);
CREATE INDEX ON audit_logs (entity_type, entity_id);
```

---

## Orden de Migración

El orden canónico de capas — es también el orden del roadmap de issues (lo consume `session-start.md` Fase 1.2):

| Fase | Capa | Tablas |
|---|---|---|
| 1 | **Capa 0 — Fundación** | `organizations`, `users`, `roles`, `organization_members`, `organization_invitations` |
| 2 | **Capa 1 — Personas** | `landlords`, `renters` |
| 3 | **Capa 2 — Propiedades** | `properties`, `property_service_accounts` |
| 4 | **Capa 3 — Contratos** | `contracts`, `contract_adjustments` (+ extensión `btree_gist`) |
| 5 | **Capa 4 — Cobranzas** | `rent_periods`, `payments` |
| 6 | **Capa 5 — Mantenimiento** | `work_orders`, `work_order_quotes`, `attachments` |
| 7 | **Capa 6 — Liquidaciones** | `recurring_charges`, `charge_entries`, `settlements`, `settlement_line_items` (+ ALTER de `work_orders.settled_in_settlement_id`) |
| 8 | **Capa 7 — Notificaciones/Auditoría** | `notifications`, `audit_logs` |

**Resumen del orden:** Fundación → Personas → Propiedades → Contratos → Cobranzas → Mantenimiento → Liquidaciones → Notificaciones/Auditoría.

FKs entre capas: siempre de capa posterior a capa anterior, salvo `work_orders.settled_in_settlement_id` (capa 5 → capa 6), que se agrega con `ALTER TABLE` en la migración de la Capa 6.

---

## Resumen de Entidades

**22 tablas en 8 capas.** Tenant-scoped con RLS: 20. Globales sin RLS: 1 (`users`). Raíz: 1 (`organizations`).

---

## Estrategia de Seed Data

### Seed global (en migraciones Alembic versionadas)

- **Ninguno en MVP.** No hay catálogos globales: los tipos (service_type, charge_type, event_type) son TEXT+CHECK, y no hay tabla de planes (la cobranza SaaS es post-MVP).

### Seed per-tenant (servicio Python en la transacción de creación de la org)

`OrganizationProvisioningService` se ejecuta en la **misma transacción** que crea la organización (Módulo 0) y siembra:

1. **Roles de sistema** (3 filas en `roles`, `is_system_role=true`):
   - `owner`: todos los permisos.
   - `admin`: todo excepto `user:manage`, `role:manage`, `organization:configure`.
   - `maintenance`: solo `work-order:read`, `work-order:quote`, `work-order:close`, `attachment:manage` (scoped a work orders).
2. **Settings default** en `organizations.settings`: `grace_day: 10`, `contract_expiry_notice_days: 60`.

**Test de invariante:** toda organización nueva tiene exactamente 3 roles de sistema y sus settings default (se verifica en la suite del Módulo 0).

---

## Apéndice A — Convención de nomenclatura

| Capa | Convención | Ejemplo |
|---|---|---|
| Entidades de dominio (sdd_02) | PascalCase singular | `Contract`, `RentPeriod`, `Settlement` |
| Tablas | snake_case plural | `contracts`, `rent_periods`, `settlements` |
| Columnas | snake_case | `daily_late_fee_pct`, `settled_in_settlement_id` |
| Valores de enum (TEXT) | snake_case minúsculas | `'agency_account'`, `'ipc_cordoba'`, `'in_progress'` |
| Endpoints | kebab-case plural | `/v1/rent-periods`, `/v1/work-orders` |
| Migraciones Alembic | `YYYYMMDD_HHMMSS_<slug>.py` | `20260901_120000_create_contracts.py` |
| Dominio ES ↔ código EN | propietario=`landlord`, inquilino=`renter`, cobro=`payment`, liquidación=`settlement`, pedido de reparación=`work_order`, ajuste=`adjustment`, cargo=`charge` | "tenant" reservado para la organización |

---

## Apéndice B — Política de soft delete por entidad

| Entidad | Mecanismo | Nota |
|---|---|---|
| landlords, renters, properties, contracts, property_service_accounts, recurring_charges, work_orders, attachments | `deleted_at` | Nunca DELETE físico (RN-D02) |
| payments | `voided_at` + `voided_by` | Anulación con autor, auditada (RN-D04) |
| rent_periods, charge_entries, settlements, settlement_line_items | Sin delete | Se corrigen/regeneran, nunca se borran |
| contract_adjustments `applied` | Inmutables | Corrección = nuevo ajuste con nota |
| audit_logs | Append-only | Sin UPDATE/DELETE (RN-D03) |
| notifications | `read_at` | No se borran en MVP |
