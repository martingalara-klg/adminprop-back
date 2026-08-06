---
name: AdminProp — Módulo 7 — Administración
description: Usuarios del equipo e invitaciones, roles con permisos atómicos, configuración de la organización y visor del log de auditoría
type: project
version: 1.0
fecha: 2026-08-06
---
# Módulo 7 — Administración

**Versión:** 1.0 · **Estado:** Borrador para revisión · **Fecha:** 2026-08-06

## Propósito

La gestión del sistema en sí: quiénes usan la plataforma y con qué rol, los parámetros generales de la administradora (día de gracia de mora, aviso de vencimientos, encabezado de las liquidaciones) y la consulta del log de auditoría (UC-17, UC-18).

## Actores

| Actor | Puede |
|---|---|
| owner | Todo el módulo: usuarios, roles, configuración, auditoría |
| admin | Solo lectura del log de auditoría; **no** gestiona usuarios ni configuración (RN-A02) |
| maintenance | Nada |

## Entidades Principales

- **User / OrganizationMember / Role / OrganizationInvitation** — ver `sdd_02` §2.2 y `spec_data_model` Capa 0.
- **Organization.settings** — ver `sdd_02` §2.1.
- **AuditLog** — ver `sdd_02` §2.17.

## Requerimientos Funcionales

### RF-01 — Invitación de usuarios del equipo

- El owner invita por email con rol `admin` o `maintenance` (el rol `owner` solo se transfiere vía Super Admin en MVP).
- Invitación con expiración 72h; reenvío y revocación desde el listado de invitaciones pendientes.
- Mismo flujo de activación de cuenta que el Módulo 0 (§"Flujo de Activación de Cuenta").
- Duplicados: email ya miembro → `409 USER_ALREADY_MEMBER`; ya invitado pendiente → `409 INVITATION_PENDING_EXISTS`.

### RF-02 — Gestión de usuarios

- Cambiar el rol de un miembro (`PATCH /users/:id`) y desactivarlo (`DELETE /users/:id`, soft — la membresía pasa a `inactive` y no puede loguearse).
- Ambas operaciones validan `LAST_OWNER_REQUIRED` (RN-A03): nunca puede quedar la org sin un owner activo.
- Todo cambio de rol y desactivación queda auditado.

### RF-03 — Roles y permisos

- `GET /roles` lista los 3 roles de sistema con sus `permissions[]` (catálogo de `sdd_03`).
- Los roles de sistema son **inmutables** (`422 SYSTEM_ROLE_IMMUTABLE` ante cualquier intento de edición). Roles custom: post-MVP.
- El chequeo de permisos en API es siempre por permiso atómico, nunca por nombre de rol (`sdd_03` §Autorización).

### RF-04 — Configuración de la organización (cobros, avisos y liquidaciones)

- `GET/PUT /organization/settings` (solo owner):
  - **`grace_day`** (default 10): último día del mes para pagar en término — la base del cálculo de mora (RN-P02).
  - **`contract_expiry_notice_days`** (default 60): anticipación del aviso de vencimiento de contratos.
  - **Encabezado de liquidaciones:** nombre de la administradora, CUIT, contacto — usados en los exports Excel/PDF del Módulo 5.
- Cambios de `grace_day` rigen para los cálculos de mora **desde el momento del cambio** (no recalculan intereses ya imputados); el cambio queda auditado.

### RF-05 — Visor del log de auditoría

- `GET /audit-logs` (owner y admin, permiso `audit:read`): paginación `page`/`page_size`, filtros por entidad, usuario, acción y rango de fechas.
- Muestra: quién, qué acción, sobre qué entidad, valores anterior/nuevo, `request_id` y fecha.
- Eventos garantizados (mínimo, según `sdd_02` §2.17): perdones de interés, anulaciones de cobros, regeneraciones de liquidaciones, ajustes aplicados, cambios de % de comisión, cambios de rol/usuario, cambios de configuración, accesos denegados.

## Reglas de Negocio (del módulo)

- **RN-01:** Solo el owner gestiona usuarios, roles y configuración (= RN-A02).
- **RN-02:** Siempre ≥ 1 owner activo (= RN-A03, `LAST_OWNER_REQUIRED`).
- **RN-03:** Roles de sistema inmutables.
- **RN-04:** El log de auditoría es de solo lectura vía API; nadie lo edita ni borra (= RN-D03).
- **RN-05:** Toda escritura de este módulo se audita (es el módulo que gobierna el acceso de los demás).

## Validaciones

- `grace_day`: entero 1–28. `contract_expiry_notice_days`: entero 7–365.
- Datos de encabezado: nombre ≤ 120, CUIT válido (11 dígitos con verificador), contacto ≤ 200.

## Criterios de Aceptación

- [ ] **CA-07-01:** El owner invita a un usuario con rol `maintenance`; el invitado activa la cuenta y solo ve el módulo de mantenimiento.
- [ ] **CA-07-02:** Desactivar al único owner activo o cambiarle el rol devuelve `422 LAST_OWNER_REQUIRED`.
- [ ] **CA-07-03:** Intentar editar un rol de sistema devuelve `422 SYSTEM_ROLE_IMMUTABLE`.
- [ ] **CA-07-04:** Un `admin` recibe `403 FORBIDDEN` al intentar invitar usuarios o cambiar la configuración; puede leer el log de auditoría.
- [ ] **CA-07-05:** Cambiar `grace_day` de 10 a 15 hace que la mora de los cobros posteriores se calcule con el día 15, sin recalcular intereses ya imputados; el cambio queda auditado.
- [ ] **CA-07-06:** El visor de auditoría filtra por entidad y usuario, pagina con `page`/`page_size`, y muestra valores anterior/nuevo de cada cambio.

## Integraciones

| Módulo | Motivo |
|---|---|
| Módulo 0 (Superadmin) | El owner semilla llega desde allí; el flujo de activación es compartido |
| Módulo 4 (Cobranzas) | `grace_day` gobierna el cálculo de mora |
| Módulo 3 (Contratos) | `contract_expiry_notice_days` gobierna los avisos |
| Módulo 5 (Liquidaciones) | Encabezado de los exports |
| Log de Auditoría | Este módulo lo consulta y también lo alimenta |
