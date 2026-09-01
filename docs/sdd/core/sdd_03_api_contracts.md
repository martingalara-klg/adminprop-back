---
name: AdminProp — Contratos de API
description: Endpoints REST, convenciones, formato de error, códigos de error globales, catálogo de permisos y autorización por recurso. Contrato vinculante entre backend y frontend
type: project
version: 1.17
fecha: 2026-08-31
---
# AdminProp — Contratos de API

**Versión:** 1.17
**Estado:** Borrador para revisión
**Fecha:** 2026-08-05

> **Regla de oro:** ningún contrato de este documento se modifica sin actualizar este SDD primero. El frontend lo consume tal cual; ante divergencia entre API real y SDD, se reporta — nunca se adapta el frontend para compensar.

---

## Convenciones Generales

- **Base URL:** `/v1` en path (no `/api/v1`). Dominios definitivos se definen con la infra.
- **Autenticación:** JWT RS256 en **HttpOnly Secure cookies** (server-set en login), `SameSite=Lax`. Access 8h; refresh 30 días rotativo (single-use). Sin MFA en MVP (post-MVP; ver `sdd_04` §2.2b).
- **Shape del JWT:** `sub` (user_id), `org` (organization_id), `role` (nombre), `permissions[]` (permisos atómicos), `is_super_admin` (bool). En `/superadmin/*` el JWT no lleva `org` ni `role`.
- **`organization_id` nunca viaja en body, path ni query** — siempre se deriva del JWT (excepción: `/superadmin/*`, donde es filtro opcional).
- **Formato de respuesta exitosa:** `{ "data": {...}, "meta": {...} }` — `meta` solo en endpoints paginados.
- **Cross-tenant y recursos inexistentes:** siempre **404 NOT_FOUND** (nunca 403) — RN-D01.
- **Soft delete:** los GET filtran borrados por default; `?include_deleted=true` requiere permiso `audit:read`.
- **Async:** operaciones > 5s (generación de liquidaciones con export) retornan `202 Accepted` con `{ "data": { "id", "status": "processing", "estimated_completion_seconds" } }` + polling del recurso.
- **Sin endpoint de switch de organización:** usuario multi-org = logout + login.
- **Idioma:** mensajes de error en español (es-AR).

## Formato de respuesta

**Error (formato CUSTOM, no RFC 7807):**

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Mensaje legible para el usuario",
    "field": "campo_relevante",
    "details": {}
  }
}
```

El frontend discrimina por `error.code`, muestra `error.message`, asocia `error.field` al input, y usa `error.details` para casos específicos (ej: `{ "conflicting_contract_id": "..." }` en `CONTRACT_OVERLAP`).

## Paginación

- **Default:** cursor-based — `?cursor=<opaque>&limit=<n>` (default 20, máx 100). Respuesta con `meta.next_cursor`.
- **Excepción:** `GET /audit-logs` usa `page` + `page_size` (default 50, máx 100) — citada como §16.

---

## Códigos de Error Globales

**Transversales:**
`VALIDATION_ERROR` (400) · `INVALID_DATE_RANGE` (400) · `UNAUTHORIZED` (401) · `ACCOUNT_LOCKED` (403, con countdown en `details.retry_after_seconds`) · `FORBIDDEN` (403) · `ROLE_REQUIRED` (403) · `SUPERADMIN_REQUIRED` (403) · `MEMBERSHIP_INACTIVE` (403) · `NOT_FOUND` (404) · `CONFLICT` (409) · `ENTITY_HAS_DEPENDENCIES` (409) · `BUSINESS_RULE_VIOLATION` (422) · `INVALID_STATUS_TRANSITION` (422) · `RATE_LIMIT_EXCEEDED` (429, con header `Retry-After`) · `INTERNAL_ERROR` (500)

**Personas y propiedades:**
`ENTITY_HAS_ACTIVE_CONTRACT` (422, `DELETE /properties/:id` y `DELETE /renters/:id` — issue #124, decisión #130: la entidad está vinculada a un contrato `active` y no puede eliminarse; `details` estructurado para que el front arme el mensaje — mismo criterio que `CONTRACT_HAS_DEBT`/issue #104: `details.entity_type` (`"property"` | `"renter"`), `details.entity_id` y `details.active_contracts[]`, cada item con `contract_id`, `property_id`, `property_address`, `renter_id`, `renter_name`, `start_date`, `end_date`)

**Auth y usuarios:**
`INVITATION_NOT_FOUND` (404) · `INVITATION_EXPIRED` (410) · `INVITATION_ALREADY_ACCEPTED` (409) · `INVITATION_PENDING_EXISTS` (409) · `USER_ALREADY_MEMBER` (409) · `LAST_OWNER_REQUIRED` (422) · `ROLE_NOT_FOUND` (404) · `SYSTEM_ROLE_IMMUTABLE` (422) · `RESET_TOKEN_EXPIRED` (410, agregado issue #8 — `GET/POST /auth/reset-password/:token`; token existió pero venció su ventana de 1h. El caso "nunca existió / ya usado" usa el `NOT_FOUND` genérico de arriba)

**Contratos:**
`CONTRACT_OVERLAP` (409, con `details.conflicting_contract_id`) · `CONTRACT_NOT_ACTIVE` (422) · `ADJUSTMENT_PENDING_EXISTS` (409) · `ADJUSTMENT_ALREADY_APPLIED` (409) · `ADJUSTMENT_PCT_REQUIRED` (400) · `CONTRACT_HAS_DEBT` (422, `POST /contracts/:id/debt-certificate`, con el detalle de lo adeudado del contrato en `details` — issue #104, renombrado desde `RENTER_HAS_DEBT`: el libre deuda es por contrato, no por inquilino)

**Cobranzas:**
`RENT_PERIOD_ALREADY_PAID` (422) · `PAYMENT_EXCEEDS_CONTRACT_BALANCE` (422) · `EXCHANGE_RATE_REQUIRED` (400) · `PAYMENT_ALREADY_VOIDED` (409)

**Liquidaciones:**
`SETTLEMENT_ALREADY_EXISTS` (409) · `SETTLEMENT_EXCHANGE_RATE_REQUIRED` (400) · `CHARGE_ENTRY_ALREADY_EXISTS` (409)

**Mantenimiento:**
`WORK_ORDER_ALREADY_CLOSED` (409) · `WORK_ORDER_ALREADY_SETTLED` (422) · `QUOTE_ALREADY_APPROVED` (409)

> Inventar un `error.code` fuera de este catálogo es una divergencia del SDD: se actualiza este documento primero.

---

## Catálogo de Permisos

Permisos atómicos (`recurso:acción`) portados en `permissions[]` del JWT:

`landlord:read` `landlord:manage` `landlord:set-commission` · `renter:read` `renter:manage` · `property:read` `property:manage` · `contract:read` `contract:manage` `contract:terminate` `contract:delete` · `adjustment:apply` · `rent-period:read` · `payment:create` `payment:void` · `charge:manage` · `settlement:read` `settlement:generate` `settlement:issue` · `work-order:read` `work-order:create` `work-order:quote` `work-order:approve` `work-order:close` `work-order:cancel` · `attachment:manage` · `user:manage` `role:read` `organization:configure` · `audit:read` · `notification:read`

> `landlord:set-commission` (agregado v1.5, issue #51): permiso atómico exclusivo de `owner` para cambiar `commission_pct` de un propietario — reemplaza el chequeo previo por nombre de rol (`payload.role`). `admin` conserva `landlord:manage` (ABM completo salvo este campo).

> `contract:terminate` (agregado v1.11, issue #105, decisión #124): permiso atómico exclusivo de `owner` para terminar un contrato (`POST /contracts/:id/terminate`). `admin` conserva `contract:manage` (ABM completo del contrato salvo terminarlo) — mismo patrón que `landlord:set-commission`: un endpoint/acción completa exigido por `Depends(requires_permission("contract:terminate"))` en el router, en vez del chequeo condicional por campo que usa `landlord:set-commission`.

> `contract:delete` (agregado v1.17, issue #124, decisión #130): permiso atómico exclusivo de `owner` para eliminar (lógicamente) un contrato (`DELETE /contracts/:id`), en CUALQUIER estado — incluso `active`. Mismo patrón que `contract:terminate` (decisión #124): sembrado SOLO en el rol `owner`, migración de backfill para las organizaciones ya existentes (con la lección del issue #116: el UPDATE solo toca filas cuyo `permissions` es realmente un array JSONB — `jsonb_typeof(permissions) = 'array'` — y concatena con `|| '[...]'::jsonb`, nunca como string). `admin` conserva `contract:manage` para el resto del ciclo de vida.

## Resumen de Autorización por Recurso

| Recurso | owner | admin | maintenance |
|---|---|---|---|
| Propietarios, inquilinos, propiedades, contratos, cobranzas, cargos, liquidaciones | ✅ total | ✅ total | ❌ (404/403 vía permisos) |
| Pedidos de reparación | ✅ total | ✅ total | 🔶 solo `work-order:read` / `quote` / `close` + adjuntos de work orders |
| Usuarios, roles, configuración de la org | ✅ | ❌ | ❌ |
| Audit logs | ✅ | ✅ (lectura) | ❌ |
| Notificaciones propias | ✅ | ✅ | ✅ |

El chequeo es **por permiso atómico** (`Depends(requires_permission("<recurso>:<acción>"))`), nunca por nombre de rol.

---

## 1. Autenticación (`/auth/*`)

```
POST   /auth/login                       → 200 { data: { status: "authenticated", user, organizations[], permissions[], is_super_admin } }
POST   /auth/logout                      → 204 (invalida refresh server-side, limpia cookies)
POST   /auth/refresh                     → 200 (rota refresh token; cookie nueva)
GET    /auth/invitation/:token           → 200 { data: { email, organization_name, role_name } }
POST   /auth/accept-invitation           → 201 (nombre + password; setea cookies) { data: { status, user, organization, permissions[], is_super_admin } }
POST   /auth/forgot-password             → 200 SIEMPRE (anti-enumeration)
GET    /auth/reset-password/:token       → 200 | 404 | 410
POST   /auth/reset-password              → 200
GET    /auth/me                          → 200 { data: { user, organization, role, permissions[], is_super_admin } } | 401
```

**Comportamientos obligatorios:**
- Login fallido: mensaje literal **"Credenciales incorrectas."** — sin diferenciar email inexistente de password incorrecta (`sdd_04` §2.2a).
- Forgot-password: respuesta literal **"Si el email está registrado, recibirás instrucciones para restablecer tu contraseña en los próximos minutos."** — siempre 200.
- Lockout: 5 intentos fallidos en 10 min → `ACCOUNT_LOCKED` por 30 min.
- Si el usuario pertenece a múltiples orgs, el login incluye la selección de organización (el JWT se emite para UNA org).
- Password: ≥ 10 caracteres, ≥ 1 mayúscula, ≥ 1 número.

**`permissions[]` / `is_super_admin` en `login` y `accept-invitation`** (v1.6, issue #84 — el front no puede leer el JWT porque vive en cookie HttpOnly, decisión #20):
- La respuesta expone, para la organización del JWT efectivamente emitido, los mismos `permissions[]` (catálogo cerrado de §Catálogo de Permisos) e `is_super_admin` que porta ese JWT — leídos de la misma fuente que arma el JWT (rol de la membresía), nunca recalculados con lógica propia del endpoint.
- `login` con `status: "authenticated"`: `permissions[]` = permisos del rol de la organización elegida; `is_super_admin` = el flag del usuario (`true` solo en el login de Super Admin, que no lleva `organizations[]`).
- `login` con `status: "organization_selection_required"` (multi-org sin `organization_id` en el body — ningún JWT se emite todavía): `permissions` e `is_super_admin` van **`null`** — el cliente reintenta el login con la organización elegida y recién ahí recibe el valor real.
- `accept-invitation` (issue #8, siempre emite JWT en el mismo request): `permissions[]` = permisos del rol de la invitación aceptada; `is_super_admin` siempre `false` (este flujo nunca activa cuentas de Super Admin).

**`GET /auth/me`** (nuevo, v1.6, issue #84): devuelve la sesión vigente para rehidratar el front al recargar la página. Autenticado por cookie igual que el resto de `/auth/*` protegidos (`get_current_access_token_payload`) — sin body, sin params.
- 200 con JWT de organización válido y membresía todavía activa: `{ data: { user, organization: { id, name }, role, permissions[], is_super_admin: false } }`. `permissions[]`/`role` se leen en vivo de la membresía actual (misma consulta que resuelve permisos en `login`/`refresh`), no del contenido cacheado del JWT — si el rol cambió de permisos después de emitido el JWT, `/auth/me` ya refleja el rol vigente.
- 200 con JWT de Super Admin (`is_super_admin=true`, sin `org`): `{ data: { user, organization: null, role: null, permissions: [], is_super_admin: true } }`.
- Sin cookie / JWT inválido o expirado → `401 UNAUTHORIZED` (estándar, igual que cualquier endpoint protegido).
- JWT de organización válido pero la membresía ya no está activa (o la organización fue deshabilitada) → `403 MEMBERSHIP_INACTIVE` (misma regla que valida la membresía en `login`).

## 2. Super Admin (`/superadmin/*`) — JWT con `is_super_admin=true`

```
GET    /superadmin/organizations
POST   /superadmin/organizations                      (crea org en pending_owner + siembra roles/settings)
GET    /superadmin/organizations/:id
PATCH  /superadmin/organizations/:id                   (name?, timezone? — ver detalle abajo)
POST   /superadmin/organizations/:id/invite-owner
POST   /superadmin/organizations/:id/resend-invitation
POST   /superadmin/organizations/:id/disable
POST   /superadmin/organizations/:id/enable
```

**`PATCH /superadmin/organizations/:id`** (issue #44 — campo definido, no existía en v1.3):

- Body acepta **exactamente** dos campos, ambos opcionales: `name` (string, `2..120` — misma validación que `POST /superadmin/organizations`) y `timezone` (string, nombre de zona IANA válido). **Al menos uno de los dos debe estar presente** — body vacío o con ambos ausentes → `400 VALIDATION_ERROR`.
- `slug` es **inmutable** post-creación — no forma parte de este body. Un PATCH que incluya `slug` (o cualquier otro campo no listado, ej. `status`, `settings`) → `400 VALIDATION_ERROR` (Pydantic `extra="forbid"`).
- `status` sólo cambia vía `POST .../disable` y `POST .../enable` — nunca por este PATCH.
- `settings` pertenece al owner de la organización (`PUT /organization/settings`, módulo administración) — nunca por este PATCH.
- Validación de `timezone`: debe ser un nombre de zona IANA reconocido por la base `tzdata` del sistema (`zoneinfo.available_timezones()` en Python, o equivalente). Un valor no reconocido (ej. `"No/Existe"`) → `400 VALIDATION_ERROR` con `field: "timezone"`.
- Éxito → `200` con el mismo shape de `OrganizationDetail` que `GET /superadmin/organizations/:id`.
- Organización inexistente (o soft-deleted) → `404 NOT_FOUND`.
- Cambio auditado (`org.updated`, ver "Códigos de Error Globales" y `docs/skills/tenant-isolation.md` "Super Admin: rol DB privilegiado") con `before`/`after` de únicamente los campos efectivamente modificados.

## 3. Usuarios y Roles — permiso `user:manage` (owner)

```
GET    /users                            GET    /users/invitations
POST   /users/invite                     POST   /users/invitations/:id/resend
PATCH  /users/:id                        DELETE /users/invitations/:id
DELETE /users/:id                        GET    /roles                      (role:read; solo lectura en MVP)
```

- `DELETE /users/:id` y `PATCH` de rol validan `LAST_OWNER_REQUIRED`.

## 4. Configuración de la Organización — permiso `organization:configure` (owner)

```
GET    /organization/settings
PUT    /organization/settings            (grace_day, contract_expiry_notice_days, encabezado de liquidaciones)
```

## 5. Propietarios (`/landlords`)

```
GET    /landlords                        GET    /landlords/:id
POST   /landlords                        PATCH  /landlords/:id
DELETE /landlords/:id                    GET    /landlords/:id/settlements   (historial de liquidaciones)
```

- Alta requiere `commission_pct`. Cambio de `commission_pct` → auditado, rige a futuro (RN-L05).
- `PATCH /landlords/:id` con `commission_pct` en el body requiere además el permiso `landlord:set-commission` (solo `owner`) — sin él, `403 FORBIDDEN` aunque el actor tenga `landlord:manage` (CA-02-02).
- `DELETE` con propiedades activas → `ENTITY_HAS_DEPENDENCIES`.

## 6. Inquilinos (`/renters`)

```
GET    /renters                          GET    /renters/:id
POST   /renters                          PATCH  /renters/:id
DELETE /renters/:id                      GET    /renters/:id/debt            (estado de deuda del inquilino)
```

**`DELETE /renters/:id` — issue #124, decisión #130 (RN-D05):** baja lógica (`deleted_at`, RN-D02), `204 No Content`, permiso `renter:manage`.
- Con al menos un contrato `active` vinculado → `422 ENTITY_HAS_ACTIVE_CONTRACT` con `details.active_contracts[]` (ver §Códigos de Error Globales). Contratos `draft`/`expired`/`terminated` NO bloquean.
- Sin contrato activo (contratos inactivos o ninguno): baja lógica auditada (`renter.deleted`). El inquilino desaparece de `GET /renters` y deja de ser elegible para contratos nuevos (`POST /contracts` con su id → `404 NOT_FOUND`, RN-06); `GET /renters/:id` y `GET /renters/:id/debt` → `404 NOT_FOUND`.
- Trazabilidad intacta: sus contratos históricos, cobros, liquidaciones y auditoría siguen referenciándolo, y `renter_name` sigue resolviéndose en `ContractSummary` y en el recibo de cobro (RN-12: la resolución de display no filtra `deleted_at`). La deuda de sus contratos históricos NO eliminados sigue computándose y cobrable (RN-C05) — solo la eliminación del CONTRATO detiene el cómputo (§8).

## 7. Propiedades (`/properties`)

```
GET    /properties                                      GET    /properties/:id
POST   /properties                                      PATCH  /properties/:id
DELETE /properties/:id
GET    /properties/:id/service-accounts                 POST   /properties/:id/service-accounts
PATCH  /service-accounts/:id                            DELETE /service-accounts/:id
GET    /properties/:id/work-orders                      (historial de reparaciones — UC-16)
GET    /properties/:id/recurring-charges                POST   /properties/:id/recurring-charges
```

- `GET /properties` acepta `?neighborhood_id=` como filtro adicional (issue #99).
- `POST /properties` y `PATCH /properties/:id` requieren `neighborhood_id` (`VALIDATION_ERROR` si falta en el `POST`; en `PATCH` solo si el campo viene en el body — parcial). `neighborhood_id` inexistente o de otra organización → `404 NOT_FOUND` con `field: "neighborhood_id"` (mismo criterio que `landlord_id`, RN-D01).
- `GET /properties` y `GET /properties/:id` embeben el barrio como `neighborhood: { id, name }` (o `null` para propiedades legacy sin barrio, preexistentes a issue #99).

**`DELETE /properties/:id` — issue #124, decisión #130 (RN-D05):** baja lógica (`deleted_at`, RN-D02), `204 No Content`, permiso `property:manage`.
- Con un contrato `active` sobre la propiedad → `422 ENTITY_HAS_ACTIVE_CONTRACT` con `details.active_contracts[]` (ver §Códigos de Error Globales — reemplaza el `409 ENTITY_HAS_DEPENDENCIES` que este caso devolvía hasta v1.16; el 409 queda para las dependencias de `landlords` y `neighborhoods`). Contratos `draft`/`expired`/`terminated` NO bloquean.
- Sin contrato activo: baja lógica auditada (`property.deleted`). La propiedad desaparece de `GET /properties` y deja de ser elegible para contratos nuevos (`POST /contracts` con su id → `404 NOT_FOUND`, RN-06) y para pedidos de reparación nuevos (`POST /work-orders` → `404 NOT_FOUND`); `GET /properties/:id` y sus sub-recursos (`/service-accounts`, `/recurring-charges`, `/work-orders` de la ficha) → `404 NOT_FOUND`.
- Trazabilidad intacta: contratos históricos, cobros, liquidaciones, pedidos de reparación y auditoría siguen referenciándola — `property_address` sigue resolviéndose en `ContractSummary`, en el recibo de cobro y en los listados top-level de mantenimiento (`GET /work-orders`, que siguen legibles; RN-12: la resolución de display no filtra `deleted_at`). La deuda de sus contratos históricos NO eliminados sigue computándose y cobrable (RN-C05).

### 7.1 Barrios (`/neighborhoods`) — catálogo parametrizable por organización (issue #99)

```
GET    /neighborhoods                    (listado del catálogo de la org, sin paginación — catálogo acotado)
POST   /neighborhoods                    (body: { name })
PATCH  /neighborhoods/:id                (rename; body: { name })
DELETE /neighborhoods/:id                (soft; 409 ENTITY_HAS_DEPENDENCIES si tiene propiedades)
```

- Permiso: lectura con `property:read`; alta/edición/baja con `property:manage` — **sin permisos nuevos** (decisión del PO, issue #99).
- `POST`/`PATCH` validan `name` único por organización, case-insensitive → `409 CONFLICT` si ya existe.
- `DELETE` con propiedades asociadas (no borradas) → `409 ENTITY_HAS_DEPENDENCIES` con `details.entity_type = "neighborhood"`.

## 8. Contratos (`/contracts`)

```
GET    /contracts                        (?status=&expiring_in_days=)
POST   /contracts                        (valida CONTRACT_OVERLAP, RN-C02, RN-C06 v2 — historical_amounts[] | current_amount+since)
GET    /contracts/:id                    (incluye monthly_amounts[] — issue #106, ver debajo)
PATCH  /contracts/:id                    (solo notes/metadata; montos NUNCA — RN-C04)
POST   /contracts/:id/activate           (draft → active; genera el rent_period del mes en curso si corresponde)
POST   /contracts/:id/terminate          (active → terminated; body: { reason }; permiso contract:terminate, solo owner)
DELETE /contracts/:id                    (borrado LÓGICO en cualquier estado; sin body; permiso contract:delete, solo owner)
GET    /contracts/:id/adjustments        (historial de ajustes)
POST   /contracts/:id/debt-certificate   (emite el certificado de libre deuda en PDF — RN-P08; verifica SOLO los períodos de ESE contrato; con deuda → 422 CONTRACT_HAS_DEBT con el detalle en details; permiso contract:read)
GET    /adjustments                      (?status=pending — bandeja de ajustes que tocan)
POST   /adjustments/:id/apply            (body: { pct }; pending → applied; recalcula current_amount — RN-C03)
```

**`POST /contracts/:id/debt-certificate` — issue #104, decisión #123 (RN-P08, `spec_module_04` RF-08):** reemplaza a `POST /renters/:id/debt-certificate` (eliminado). Decisión del PO (2026-08-28): el libre deuda es conceptualmente **por contrato** — un inquilino puede alquilar 2 propiedades (ej: comercial) y deber en una sí y en otra no, así que el certificado se emite desde el contrato y verifica SOLO los períodos de ESE contrato (nunca los de otros contratos del mismo inquilino). El PDF (sincrónico) incluye encabezado de la administradora, inquilino, propiedad y fecha de emisión del contrato puntual. Permiso `contract:read` (no `renter:read`): mismo criterio que el resto de los endpoints de lectura de `/contracts/:id`. El error pasa de `RENTER_HAS_DEBT` a `CONTRACT_HAS_DEBT` (renombrado, no reutilizado con semántica nueva — ver §Códigos de Error Globales).

**`POST /contracts/:id/terminate` — issue #105, decisión #124 (RN-A, `spec_module_03`):** feedback #2 del PO — terminar un contrato pasa a ser exclusivo de `owner` (hasta ahora, `contract:manage` se lo permitía también a `admin`). Se agrega el permiso atómico dedicado `contract:terminate` al catálogo, sembrado SOLO en el rol `owner` (`admin` conserva `contract:manage` para el resto del ciclo de vida del contrato — crear, actualizar, activar). Mismo patrón que `landlord:set-commission` (decisión #116): migración de backfill agrega el permiso al rol `owner` de organizaciones ya existentes.

**`DELETE /contracts/:id` — issue #124, decisión #130 (RN-C08, `spec_module_03` RF-07):** feedback #4 del PO (2026-08-31) — el `owner` puede eliminar CUALQUIER contrato, incluso `active` (decisión del PO vía AskUserQuestion). Borrado LÓGICO siempre (`deleted_at`, RN-D02), `204 No Content`, sin body (a diferencia de `terminate`, no exige `reason` — el evento de auditoría `contract.deleted` registra autor y estado previo). Permiso atómico `contract:delete`, exclusivo de `owner` (ver §Catálogo de Permisos); un `admin` recibe `403 FORBIDDEN`. Contrato inexistente, ya eliminado o cross-tenant → `404 NOT_FOUND` (RN-D01). Efectos:

- El contrato desaparece de `GET /contracts` y de todo panel; `GET /contracts/:id`, `PATCH`, `activate`, `terminate`, `GET .../adjustments` y `POST .../debt-certificate` sobre él → `404 NOT_FOUND`.
- Si estaba `active`: su propiedad vuelve a `available` (mismo efecto que `terminate`/`expired` — el invariante `rented ⟺ contrato active no eliminado` de `spec_module_01` RF-04 se mantiene) y se DETIENE la generación de períodos futuros — el job mensual `generate_rent_periods`, el hook de activación y `detect_due_adjustments`/`detect_expiring_contracts` ignoran contratos eliminados (RN-C08).
- La deuda del contrato deja de computarse: sus `rent_periods` desaparecen del panel de cobranzas (`GET /rent-periods`), del estado de deuda (`GET /debt`, `GET /renters/:id/debt`) y de la advertencia de períodos impagos de liquidaciones; `GET /rent-periods/:id` de un período suyo → `404 NOT_FOUND` y no admite cobros nuevos (`POST .../payments` → `404`).
- Un ajuste `pending` del contrato eliminado desaparece de la bandeja (`GET /adjustments?status=pending`) y `POST /adjustments/:id/apply` sobre él → `404 NOT_FOUND`.
- Los cobros y liquidaciones YA emitidos quedan intactos: `payments`, `settlements` y sus line items no se tocan, el recibo de un cobro existente (`GET /payments/:id/receipt`) sigue descargable, y la auditoría del contrato sigue consultable (`GET /audit-logs`). La resolución de display (RN-12) sigue mostrando sus referencias.

**`POST /contracts` — issue #107, decisión #126 (RN-C06 v2, `sdd_02` §3 — supersede parcialmente la decisión #121/issue #100):** el body acepta dos mecanismos de alta de contrato en curso, **mutuamente excluyentes** según si el contrato configura `adjustment_frequency_months` (siempre `null` para USD, opcional para ARS):

- **Con `adjustment_frequency_months` (ARS con ajuste periódico):** campo opcional `historical_amounts` — lista ORDENADA de decimales `> 0`, uno por cada **tramo transcurrido** desde `start_date` (tramo `i` = `[start_date + i·frecuencia meses, start_date + (i+1)·frecuencia meses)`, fechas derivadas por el backend — el cliente nunca las envía). La cantidad esperada la calcula el backend desde `start_date` + `adjustment_frequency_months` + la fecha de hoy:
  - Cantidad incorrecta → `400 VALIDATION_ERROR`, `field: "historical_amounts"`, mensaje indicando cuántos valores espera el sistema y el rango `[start, end)` de cada tramo esperado (en `details.expected_count` y `details.tramos`).
  - `historical_amounts[0]` distinto de `initial_amount` → `400 VALIDATION_ERROR`, `field: "historical_amounts"`.
  - Si el contrato recién arrancó (un solo tramo posible, ninguno "transcurrido" más allá del inicial) no corresponde enviarlo — enviarlo en ese caso también es `400 VALIDATION_ERROR` (equivale a un alta normal, sin declarar nada).
  - `current_amount`/`current_amount_since` en el body de un contrato con `adjustment_frequency_months` → `400 VALIDATION_ERROR` (quedan superados por `historical_amounts` para este caso).
  - Si `historical_amounts` viene con ≥ 2 elementos: la respuesta trae `current_amount` igual al ÚLTIMO valor de la lista, y el historial (`GET /contracts/:id/adjustments`) incluye una **cadena** de ajustes sintéticos `applied` de carga inicial (uno por tramo a partir del segundo, RN-C06 v2).
- **Sin `adjustment_frequency_months` (USD siempre; ARS sin ajuste periódico):** se mantiene, sin cambios, el mecanismo del issue #100 — campos opcionales `current_amount` (decimal > 0) y `current_amount_since` (fecha), **solo válidos juntos** (enviar uno sin el otro es `400 VALIDATION_ERROR`). `current_amount_since`, normalizado al día 1 de su mes, debe ser `>= start_date` y `<= hoy` (`400 INVALID_DATE_RANGE`, `field: "current_amount_since"`). Si vienen: la respuesta trae `current_amount` igual al valor declarado y el historial incluye el único ajuste sintético `applied` correspondiente. `historical_amounts` en el body de un contrato sin `adjustment_frequency_months` → `400 VALIDATION_ERROR` (sin frecuencia no hay noción de "tramo").

Los contratos ya dados de alta con el mecanismo del issue #100 (un único ajuste sintético) siguen siendo válidos — no requieren migración, son ajustes `applied` normales.

**`GET /contracts/:id` — issue #106, decisión #125 (`spec_module_03` RF-06):** feedback #2 del PO — la ficha del contrato debe mostrar el valor del alquiler mes a mes (el actual primero, hacia atrás), **derivado en el backend** (el front no calcula lógica de negocio). La respuesta agrega `monthly_amounts[]`, lista de `{ "period": "YYYY-MM-01", "amount": "123.45" }` (mismo formato de `period` que `due_period` de `ContractAdjustment` — día 1 del mes calendario; `amount` como `NUMERIC`, nunca float), en **orden DESCENDENTE**. El rango va desde `start_date` hasta:
- el mes actual, si el contrato sigue vigente (`draft`/`active`);
- `end_date`, si venció naturalmente (`status = "expired"`);
- la **fecha de terminación efectiva**, si fue terminado anticipadamente (`status = "terminated"`) — `contracts` no tiene columna propia para esto (RF-03 solo persiste el motivo en `audit_logs`), así que se deriva del evento `contract.terminated` más reciente de ESE contrato en `audit_logs` (mismo timestamp que la transición de estado — misma transacción, `POST /contracts/:id/terminate`); si por algún motivo no existe (defensivo), el fallback es `end_date`.

El monto de cada mes es determinístico: `initial_amount` hasta el primer ajuste `applied` cuyo `due_period <= mes`, luego el `new_amount` del **último** ajuste `applied` cuyo `due_period <= mes` (incluye el ajuste sintético "Carga inicial" del issue #100/RN-C06). Solo cuentan ajustes `applied` — los `pending` no afectan el histórico. Un contrato USD sin carga inicial declarada tiene una serie plana en `initial_amount` (RN-C02: sin ajuste periódico automático). Si el contrato aún no empezó (`start_date` futuro), `monthly_amounts` es `[]`.

**Historial de ajustes — `applied_by_name` y `pct_effective` — issue #118, decisión #127 (RN-10 de `spec_module_03`):** feedback #3 del PO (2026-08-29) — el item de ajuste devuelto por `GET /contracts/:id/adjustments`, `GET /adjustments` y la respuesta de `POST /adjustments/:id/apply` (los tres comparten el mismo schema `AdjustmentSummary`) agrega dos campos:

- `applied_by_name` (`str | null`): el `full_name` de `users` resuelto desde `applied_by` — antes el front solo tenía el UUID crudo. `null` mientras el ajuste sigue `pending` (no hay `applied_by` todavía).
- `pct_effective` (`Decimal | null`): recalculado en el backend como `((new_amount − previous_amount) / previous_amount) × 100`, redondeado a 2 decimales con `ROUND_HALF_EVEN` (banker's rounding), siempre en `Decimal` — nunca `float`. Se calcula para TODO ajuste `applied`, incluido el ajuste sintético de "Carga inicial" (issues #100/#107) donde es la ÚNICA fuente confiable del % ya que ahí `pct_applied` queda `NULL` (RN-C06). Para los ajustes manuales normalmente coincide con `pct_applied` (que ya usa `ROUND_HALF_UP` al calcularse `new_amount` en `POST /adjustments/:id/apply`). `null` si el ajuste no está `applied` (`pending`) o si `previous_amount = 0` (evita división por cero).

**`ContractSummary` enriquecido — `property_address`, `property_neighborhood` y `renter_name` — issue #123, decisión #129 (RN-12 de `spec_module_03`):** feedback #4 del PO (2026-08-31) — el shape `ContractSummary` (item de `GET /contracts` y respuesta de `POST /contracts`, `PATCH /contracts/:id`, `POST /contracts/:id/activate` y `POST /contracts/:id/terminate`; `GET /contracts/:id` lo hereda vía `ContractDetail`) agrega tres campos denormalizados de SOLO LECTURA, para que el front agrupe el listado de contratos por barrio mostrando dirección e inquilino sin resolver referencias por su cuenta:

- `property_address` (`str`): `properties.address` de la propiedad del contrato.
- `property_neighborhood` (`str | null`): `neighborhoods.name` resuelto vía `properties.neighborhood_id`; `null` si la propiedad no tiene barrio asignado (columna nullable — datos legacy, issue #99).
- `renter_name` (`str`): `renters.name` del inquilino del contrato.

Los tres se resuelven por JOIN en el SQL del repository — un solo query por página del listado, sin N+1 (mismo criterio que la decisión #127/issue #118: la resolución de referencias para display es del backend). Son campos derivados, no persistidos en `contracts` — sin migración. No se aceptan en ningún body: enviarlos en `POST /contracts` o `PATCH /contracts/:id` es `400 VALIDATION_ERROR` (solo lectura). La resolución NO filtra `deleted_at` de `properties`/`renters`/`neighborhoods`: el contrato sigue existiendo y muestra su referencia aunque el registro referenciado esté soft-deleted (RN-06 ya impide borrar una propiedad o inquilino con contrato activo; el caso solo aplica a contratos históricos). El JOIN mantiene el filtro explícito de `organization_id` en cada tabla unida (defense in depth, RN-D01).

## 9. Cobranzas (`/rent-periods`, `/payments`)

```
GET    /rent-periods                     (?period=YYYY-MM&status=&in_arrears=true — panel del mes)
GET    /rent-periods/:id
GET    /rent-periods/:id/interest-preview  (?payment_date= — interés sugerido a esa fecha, RN-P03)
POST   /rent-periods/:id/payments        (registrar cobro — RN-P04/P05/P06/P07)
POST   /payments/:id/void                (anulación lógica con motivo; auditada — RN-D04)
GET    /payments/:id/receipt             (genera bajo demanda y descarga el recibo PDF del cobro — RN-P08; sobre un cobro anulado → 422 BUSINESS_RULE_VIOLATION)
GET    /debt                             (?landlord_id=&renter_id=&min_days= — estado de deuda global, UC-10)
```

- `POST payments` valida: `amount` > 0 y ≤ saldo (`PAYMENT_EXCEEDS_CONTRACT_BALANCE`), `exchange_rate` si moneda difiere (`EXCHANGE_RATE_REQUIRED`), y registra `suggested_interest` / `charged_interest` / `forgiven_interest`.
- La generación mensual de rent_periods es un job de Celery Beat (1° de cada mes, `sdd_04` §1.3), no un endpoint.
- `GET /rent-periods/:id` (v1.7, issue #87) además de los campos del panel (§RF-02) incluye `payments[]` — el historial de cobros del período, ordenado por `payment_date` ascendente. Cada item trae `id`, `payment_date`, `method`, `payment_currency`, `amount`, `exchange_rate`, `destination`, `suggested_interest`, `charged_interest`, `forgiven_interest`, `notes`, `voided_at`, `voided_by`, `created_at`, `origin` (v1.15, issue #119). **Los cobros anulados se incluyen** (con `voided_at`/`voided_by` poblados) — es la vía por la que CA-04-07 ("el cobro queda visible con marca de anulado") se verifica por API, no solo a nivel DB. El **motivo** de la anulación no viaja acá — vive en `audit_logs` (acción `payment.voided`, decisión #23) y se consulta vía `GET /audit-logs?entity_type=payment&entity_id=<id>` (visor de auditoría, permiso `audit:read`). `GET /rent-periods` (panel/listado, §RF-02) **no cambia** — sigue sin `payments[]`, liviano para el panel mensual.

**`payments.origin` — issue #119, decisión #128 (RN-P09 de `sdd_02`):** feedback #3 del PO (2026-08-29) — al dar de alta un contrato en curso (`start_date` anterior al mes actual), el backend genera automáticamente los `rent_periods` `paid` de los meses transcurridos y un `payment` por cada uno con `origin: "initial_load"` (vs. `"manual"` para todo cobro registrado por un operador vía `POST /rent-periods/:id/payments` — este endpoint sigue sin aceptar `origin` en el body, siempre nace `manual`). `origin` se agrega al shape `PaymentSummary`/`PaymentDetail` (respuesta de `POST /rent-periods/:id/payments`, `POST /payments/:id/void` y `payments[]` de `GET /rent-periods/:id`). Un cobro `initial_load` es un registro histórico, no una operación corriente: `GET /payments/:id/receipt` y `POST /payments/:id/void` devuelven `422 BUSINESS_RULE_VIOLATION` sobre él (mismo código que ya usaban sobre un cobro anulado/inexistente-recibo). No hay `error.code` nuevo.

## 10. Cargos del mes (`/recurring-charges`, `/charge-entries`)

```
PATCH  /recurring-charges/:id            (label, is_active)
POST   /recurring-charges/:id/entries    (body: { period, amount, notes } — CHARGE_ENTRY_ALREADY_EXISTS si duplicado)
GET    /charge-entries                   (?period=YYYY-MM — verificación mensual)
PATCH  /charge-entries/:id               (corrección auditada)
```

## 11. Liquidaciones (`/settlements`)

```
GET    /settlements                      (?period=&landlord_id=&status=)
POST   /settlements/generate             → 202 (body: { landlord_id, period, exchange_rate? } — SETTLEMENT_EXCHANGE_RATE_REQUIRED si hay USD y falta TC; SETTLEMENT_ALREADY_EXISTS si ya hay una para landlord+period)
GET    /settlements/:id                  (totales + line items + adjuntos Excel/PDF)
POST   /settlements/:id/regenerate       → 202 (recalcula con datos corregidos; regenerated_count++; auditada — RN-L03)
POST   /settlements/:id/issue            (draft → issued)
GET    /settlements/:id/export           (?format=xlsx|pdf → descarga del archivo generado)
```

## 12. Mantenimiento (`/work-orders`)

```
GET    /work-orders                      (?status=&property_id= — maintenance ve todos los de la org)
POST   /work-orders                      (work-order:create — owner/admin; payer obligatorio; notifica a maintenance)
GET    /work-orders/:id
POST   /work-orders/:id/quotes           (work-order:quote — maintenance/admin; notifica a owner+admin)
POST   /quotes/:id/approve               (work-order:approve — owner/admin; open → in_progress; las demás quotes → discarded)
POST   /work-orders/:id/close            (work-order:close — maintenance/admin; body: { final_cost? }; notifica)
POST   /work-orders/:id/cancel           (work-order:cancel — owner/admin)
POST   /work-orders/:id/attachments      (fotos; también sobre quotes: POST /quotes/:id/attachments)
GET    /attachments/:id/download
```

## 13. Notificaciones (`/notifications`)

```
GET    /notifications                    (?unread=true)
POST   /notifications/:id/read
POST   /notifications/read-all
```

## 16. Audit Logs (`/audit-logs`) — permiso `audit:read`

```
GET    /audit-logs                       (page + page_size; filtros: entity_type, entity_id, user_id, action, date range)
GET    /audit-logs/:id
```

- `meta` de la paginación page/page_size: `{ total, page, page_size }` (v1.3 — documenta lo implementado en el issue #32).
- Cada entrada incluye `user_email` (derivado de `users` por conveniencia del visor, solo lectura — el "quién" de RF-05) además de `user_id`, `before_state`/`after_state`, `request_id` y `created_at`.

---

## Notas de secciones por dominio

- **"3. Contratos"** (§8): el ciclo draft→active→expired/terminated, ajustes y la bandeja de pendientes.
- **"Cobros"** (§9): imputación libre de intereses, pagos parciales, TC manual, destino administración/propietario.
- **"5. Liquidaciones"** (§11): generación 202 + polling, regeneración auditada, export Excel/PDF, TC obligatorio con USD.
- **§15 notificaciones** → ver §13 (numeración compacta de este documento; la referencia histórica "§15" de los skills apunta a la sección de Notificaciones).
