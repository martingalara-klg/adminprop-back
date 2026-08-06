---
name: AdminProp — Módulo 0 — Super Admin & Onboarding de Organizaciones
description: Portal /superadmin/* — alta de administradoras, invitación del owner, deshabilitación. Sin auto-registro público
type: project
version: 1.0
fecha: 2026-08-06
---
# Módulo 0 — Super Admin & Onboarding de Organizaciones

**Versión:** 1.0 · **Estado:** Borrador para revisión · **Fecha:** 2026-08-06

## Propósito

El único mecanismo de alta de administradoras (organizaciones) en la plataforma. No existe auto-registro público: un empleado de la plataforma (`is_super_admin=true`) crea la organización e invita a su owner. En el MVP se usa una vez (la organización fundadora); es la base del modelo SaaS futuro.

## Actores

| Actor | Acceso |
|---|---|
| **Super Admin** | Portal `/superadmin/*` exclusivamente. Su JWT no tiene `org` ni `role` (RN-01) |
| Owner / admin / maintenance | **Nunca** acceden a `/superadmin/*` → `403 SUPERADMIN_REQUIRED` |

## Entidades Principales

- **Organization** (vista Super Admin): `slug`, `name`, `status` (`pending_owner` → `active` → `disabled`), `timezone`, `settings`. Ver `sdd_02` §2.1 y `spec_data_model` Capa 0.
- **OrganizationInvitation:** email, rol, token hasheado, `expires_at` (72h), status (`pending`/`accepted`/`expired`/`revoked`).

## Requerimientos Funcionales

### RF-01 — Dashboard de Organizaciones

Listado de todas las organizaciones con: nombre, slug, status (con distinción visual: `pending_owner` naranja, `active` verde, `disabled` gris), fecha de alta, owner actual (si activó). Filtros por status y búsqueda por nombre/slug.

### RF-02 — Creación de Organización

- El Super Admin crea la organización con `name` (obligatorio) y `timezone` (default `America/Argentina/Cordoba`).
- El `slug` se **autogenera** desde el nombre (kebab-case, único global; ante colisión se sufija `-2`, `-3`, …).
- La organización nace en estado **`pending_owner`**.
- En la **misma transacción**, `OrganizationProvisioningService` siembra los 3 roles de sistema y los settings default (`grace_day: 10`, `contract_expiry_notice_days: 60`) — ver `spec_data_model` §"Estrategia de Seed Data".

### RF-03 — Invitación de Usuario Owner

- El Super Admin invita al owner por email. La invitación expira a las **72 horas**.
- **Una sola invitación de owner `pending` por organización** (app-level, `SELECT ... FOR UPDATE`): reinvitar revoca la anterior automáticamente.
- El email llega vía `notification_worker` (Resend) con el link de activación.

### RF-04 — Reenvío de Invitación

Reenviar regenera el token y la expiración; la invitación anterior queda `revoked`. Disponible mientras la org esté en `pending_owner`.

### RF-05 — Deshabilitación / Habilitación de Organización

- `disable`: la organización pasa a `disabled`. Sus usuarios no pueden autenticarse ni renovar sesión (el refresh falla). Los datos se conservan intactos.
- `enable`: vuelve a `active`. Ambas operaciones quedan en el log de auditoría con actor y motivo.

## Reglas de Negocio

- **RN-01:** El JWT del Super Admin no contiene `org` ni `role`; opera con el rol PostgreSQL `adminprop_superadmin` (BYPASSRLS) solo en `/superadmin/*` (`sdd_04` §2.3).
- **RN-02:** No existe auto-registro público de organizaciones ni de usuarios: toda alta nace de este módulo o de una invitación del Módulo 7.
- **RN-03:** Una organización `disabled` rechaza login y refresh de todos sus miembros (`403 MEMBERSHIP_INACTIVE`).
- **RN-04:** La organización pasa de `pending_owner` a `active` únicamente cuando el owner completa la activación de su cuenta.
- **RN-05:** Las operaciones del Super Admin se auditan siempre (creación, invitaciones, disable/enable).
- **RN-06:** El Super Admin **no accede a los datos operativos** de las organizaciones (propiedades, contratos, cobros, liquidaciones, reparaciones): solo a la metadata de la organización y sus invitaciones.

## Flujo de Activación de Cuenta

1. El owner recibe el email con link `/accept-invitation?token=<uuid>`.
2. El frontend valida el token: `GET /auth/invitation/:token` → email + nombre de la organización (o `INVITATION_EXPIRED` / `INVITATION_NOT_FOUND`).
3. El owner completa nombre y password (política `sdd_04` §2.2) → `POST /auth/accept-invitation`.
4. En una transacción: se crea el `user`, la membresía con rol `owner`, la invitación pasa a `accepted` y la organización a **`active`**.
5. El backend setea las cookies de sesión; el owner entra directo a la app.

## Validaciones

- `name`: 2–120 caracteres. `slug`: generado, `^[a-z0-9-]+$`, único global.
- Email de invitación: formato válido; no puede tener otra invitación `pending` de la misma org.
- Token de invitación: hasheado en DB; la comparación es constant-time.

## Criterios de Aceptación

- [ ] **CA-00-01:** El Super Admin crea una organización y queda `pending_owner`, con slug autogenerado único y sus 3 roles de sistema + settings default sembrados en la misma transacción.
- [ ] **CA-00-02:** La invitación de owner llega por email y expira a las 72h; el link expirado muestra "invitación expirada" y el Super Admin puede reenviar (la anterior queda `revoked`).
- [ ] **CA-00-03:** Al completar la activación, la organización pasa a `active` y el owner queda logueado con rol `owner`.
- [ ] **CA-00-04:** Al deshabilitar una organización, sus usuarios reciben error en el próximo login o refresh; al rehabilitarla, recuperan acceso con sus datos intactos.
- [ ] **CA-00-05:** Un usuario `owner`/`admin`/`maintenance` que intenta acceder a `/superadmin/*` recibe `403 SUPERADMIN_REQUIRED` y el intento queda auditado.
- [ ] **CA-00-06:** El Super Admin no puede consultar propiedades, contratos, cobros ni liquidaciones de ninguna organización desde este portal.

## Integraciones

| Servicio / Módulo | Motivo |
|---|---|
| **Resend** (`notification_worker`) | Email de invitación y reenvíos |
| **Módulo 7 (Administración)** | El owner creado acá es la semilla del sistema de usuarios de la org |
| **Log de Auditoría** | Creación de orgs, invitaciones, disable/enable |
