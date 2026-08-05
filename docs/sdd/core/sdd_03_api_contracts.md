---
name: AdminProp — Contratos de API
description: Endpoints REST, convenciones, formato de error, códigos de error globales, catálogo de permisos y autorización por recurso. Contrato vinculante entre backend y frontend
type: project
version: 1.0
fecha: 2026-08-05
---
# AdminProp — Contratos de API

**Versión:** 1.0
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
`VALIDATION_ERROR` (400) · `INVALID_DATE_RANGE` (400) · `UNAUTHORIZED` (401) · `ACCOUNT_LOCKED` (401, con countdown en `details.retry_after_seconds`) · `FORBIDDEN` (403) · `ROLE_REQUIRED` (403) · `SUPERADMIN_REQUIRED` (403) · `MEMBERSHIP_INACTIVE` (403) · `NOT_FOUND` (404) · `CONFLICT` (409) · `ENTITY_HAS_DEPENDENCIES` (409) · `BUSINESS_RULE_VIOLATION` (422) · `INVALID_STATUS_TRANSITION` (422) · `RATE_LIMIT_EXCEEDED` (429, con header `Retry-After`) · `INTERNAL_ERROR` (500)

**Auth y usuarios:**
`INVITATION_NOT_FOUND` (404) · `INVITATION_EXPIRED` (410) · `INVITATION_ALREADY_ACCEPTED` (409) · `INVITATION_PENDING_EXISTS` (409) · `USER_ALREADY_MEMBER` (409) · `LAST_OWNER_REQUIRED` (422) · `ROLE_NOT_FOUND` (404) · `SYSTEM_ROLE_IMMUTABLE` (422)

**Contratos:**
`CONTRACT_OVERLAP` (409, con `details.conflicting_contract_id`) · `CONTRACT_NOT_ACTIVE` (422) · `ADJUSTMENT_PENDING_EXISTS` (409) · `ADJUSTMENT_ALREADY_APPLIED` (409) · `ADJUSTMENT_PCT_REQUIRED` (400)

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

`landlord:read` `landlord:manage` · `renter:read` `renter:manage` · `property:read` `property:manage` · `contract:read` `contract:manage` · `adjustment:apply` · `rent-period:read` · `payment:create` `payment:void` · `charge:manage` · `settlement:read` `settlement:generate` `settlement:issue` · `work-order:read` `work-order:create` `work-order:quote` `work-order:approve` `work-order:close` `work-order:cancel` · `attachment:manage` · `user:manage` `role:read` `organization:configure` · `audit:read` · `notification:read`

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
POST   /auth/login                       → 200 { data: { status: "authenticated", user, organizations[] } }
POST   /auth/logout                      → 204 (invalida refresh server-side, limpia cookies)
POST   /auth/refresh                     → 200 (rota refresh token; cookie nueva)
GET    /auth/invitation/:token           → 200 { data: { email, organization_name, role_name } }
POST   /auth/accept-invitation           → 201 (nombre + password; setea cookies)
POST   /auth/forgot-password             → 200 SIEMPRE (anti-enumeration)
GET    /auth/reset-password/:token       → 200 | 404 | 410
POST   /auth/reset-password              → 200
```

**Comportamientos obligatorios:**
- Login fallido: mensaje literal **"Credenciales incorrectas."** — sin diferenciar email inexistente de password incorrecta (`sdd_04` §2.2a).
- Forgot-password: respuesta literal **"Si el email está registrado, recibirás instrucciones para restablecer tu contraseña en los próximos minutos."** — siempre 200.
- Lockout: 5 intentos fallidos en 10 min → `ACCOUNT_LOCKED` por 30 min.
- Si el usuario pertenece a múltiples orgs, el login incluye la selección de organización (el JWT se emite para UNA org).
- Password: ≥ 10 caracteres, ≥ 1 mayúscula, ≥ 1 número.

## 2. Super Admin (`/superadmin/*`) — JWT con `is_super_admin=true`

```
GET    /superadmin/organizations
POST   /superadmin/organizations                      (crea org en pending_owner + siembra roles/settings)
GET    /superadmin/organizations/:id
PATCH  /superadmin/organizations/:id
POST   /superadmin/organizations/:id/invite-owner
POST   /superadmin/organizations/:id/resend-invitation
POST   /superadmin/organizations/:id/disable
POST   /superadmin/organizations/:id/enable
```

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
- `DELETE` con propiedades activas → `ENTITY_HAS_DEPENDENCIES`.

## 6. Inquilinos (`/renters`)

```
GET    /renters                          GET    /renters/:id
POST   /renters                          PATCH  /renters/:id
DELETE /renters/:id                      GET    /renters/:id/debt            (estado de deuda del inquilino)
```

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

## 8. Contratos (`/contracts`)

```
GET    /contracts                        (?status=&expiring_in_days=)
POST   /contracts                        (valida CONTRACT_OVERLAP, RN-C02)
GET    /contracts/:id
PATCH  /contracts/:id                    (solo notes/metadata; montos NUNCA — RN-C04)
POST   /contracts/:id/activate           (draft → active; genera el rent_period del mes en curso si corresponde)
POST   /contracts/:id/terminate          (active → terminated; body: { reason })
GET    /contracts/:id/adjustments        (historial de ajustes)
GET    /adjustments                      (?status=pending — bandeja de ajustes que tocan)
POST   /adjustments/:id/apply            (body: { pct }; pending → applied; recalcula current_amount — RN-C03)
```

## 9. Cobranzas (`/rent-periods`, `/payments`)

```
GET    /rent-periods                     (?period=YYYY-MM&status=&in_arrears=true — panel del mes)
GET    /rent-periods/:id
GET    /rent-periods/:id/interest-preview  (?payment_date= — interés sugerido a esa fecha, RN-P03)
POST   /rent-periods/:id/payments        (registrar cobro — RN-P04/P05/P06/P07)
POST   /payments/:id/void                (anulación lógica con motivo; auditada — RN-D04)
GET    /debt                             (?landlord_id=&renter_id=&min_days= — estado de deuda global, UC-10)
```

- `POST payments` valida: `amount` > 0 y ≤ saldo (`PAYMENT_EXCEEDS_CONTRACT_BALANCE`), `exchange_rate` si moneda difiere (`EXCHANGE_RATE_REQUIRED`), y registra `suggested_interest` / `charged_interest` / `forgiven_interest`.
- La generación mensual de rent_periods es un job de Celery Beat (1° de cada mes, `sdd_04` §1.3), no un endpoint.

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

---

## Notas de secciones por dominio

- **"3. Contratos"** (§8): el ciclo draft→active→expired/terminated, ajustes y la bandeja de pendientes.
- **"Cobros"** (§9): imputación libre de intereses, pagos parciales, TC manual, destino administración/propietario.
- **"5. Liquidaciones"** (§11): generación 202 + polling, regeneración auditada, export Excel/PDF, TC obligatorio con USD.
- **§15 notificaciones** → ver §13 (numeración compacta de este documento; la referencia histórica "§15" de los skills apunta a la sección de Notificaciones).
