---
name: AdminProp — Módulo 2 — Personas (Propietarios e Inquilinos)
description: ABM de propietarios (con % de comisión propio y datos bancarios cifrados) e inquilinos (con su estado de deuda). Registros sin login
type: project
version: 1.1
fecha: 2026-08-31
---
# Módulo 2 — Personas (Propietarios e Inquilinos)

**Versión:** 1.1 · **Estado:** Borrador para revisión · **Fecha:** 2026-08-31

## Propósito

Los actores del negocio que no usan el sistema: los **propietarios** (a quienes la administradora les rinde, cada uno con su % de comisión) y los **inquilinos** (a quienes les cobra). Son registros, no usuarios — no tienen login en el MVP (S-02 del PRD).

## Actores

| Actor | Puede |
|---|---|
| owner | ABM completo; **cambiar el % de comisión de un propietario** |
| admin | ABM completo salvo el % de comisión (lo ve, no lo edita) |
| maintenance | Nada — nunca accede a datos de propietarios ni inquilinos (RN-A01) |

## Entidades Principales

- **Landlord** — ver `sdd_02` §2.3: nombre/razón social, CUIT/DNI, contacto, datos bancarios (**cifrados** con pgcrypto, `sdd_04` §2.4), **`commission_pct`**, notas.
- **Renter** — ver `sdd_02` §2.4: nombre, DNI/CUIT, contacto, notas (garantes y referencias como texto libre en MVP).

## Requerimientos Funcionales

### RF-01 — ABM de Propietarios

- Alta con: nombre (obligatorio), CUIT/DNI, teléfono, email, datos bancarios, **% de comisión (obligatorio, 0–100)** y notas.
- El % de comisión aplica a **todos los contratos** del propietario (UC-02).
- **Cambio de % de comisión:** solo `owner`; queda auditado (valor anterior y nuevo, RN-D04) y rige para liquidaciones futuras — nunca recalcula períodos ya liquidados (RN-L05).
- `bank_info` viaja cifrado en DB y **nunca aparece en logs** (scrubbing, `sdd_04` §2.4).
- Baja: soft delete; con propiedades activas → `409 ENTITY_HAS_DEPENDENCIES`.

### RF-02 — Ficha del Propietario

- Datos + listado de sus propiedades (con estado y contrato vigente).
- Historial de liquidaciones (`GET /landlords/:id/settlements`) con acceso a los exports Excel/PDF de cada una.

### RF-03 — ABM de Inquilinos

- Alta con: nombre (obligatorio), DNI/CUIT, teléfono, email, notas.
- Baja: **soft delete** (RN-D02/RN-D05 — issue #124, decisión #130, reemplaza el `409 ENTITY_HAS_DEPENDENCIES` que este caso devolvía hasta v1.0). Con al menos un contrato `active` (no eliminado) → `422 ENTITY_HAS_ACTIVE_CONTRACT` con `details.active_contracts[]` (contratos `draft`/`expired`/`terminated` NO bloquean). Sin contrato activo, la baja es lógica y auditada (`renter.deleted`): el inquilino desaparece del listado y de los selects (no es elegible para contratos nuevos → `404`), su ficha y `GET /renters/:id/debt` devuelven `404`, y la trazabilidad queda intacta — contratos históricos, cobros, liquidaciones y auditoría siguen referenciándolo (RN-12), y la deuda de sus contratos históricos no eliminados sigue computándose y cobrable (RN-C05).

### RF-04 — Ficha del Inquilino y Estado de Deuda

- Datos + contratos (vigentes e históricos).
- **Estado de deuda** (`GET /renters/:id/debt`): períodos adeudados con saldo (capital impago), días de mora al día de hoy e interés sugerido acumulado (RN-P03), y pagos parciales aplicados. Es la vista que responde "¿cuánto me debe este inquilino y desde cuándo?" (UC-10 a nivel inquilino).

## Reglas de Negocio

- `commission_pct` ≥ 0 y ≤ 100; obligatorio desde el alta (sin él no se puede liquidar).
- El estado de deuda es **calculado**, nunca persistido: sale de los `rent_periods` impagos y la regla de mora vigente.
- RN-A01: `maintenance` no accede a ningún endpoint de este módulo.
- RN-D01/D02/D04 aplican (aislamiento, soft delete, auditoría de cambios sensibles).

## Validaciones

- `name`: 2–150 caracteres.
- `tax_id`: CUIT de 11 dígitos con dígito verificador válido, o DNI de 7–8 dígitos (campo flexible, validación por formato detectado).
- `email`: RFC 5322 (opcional). `phone`: texto libre ≤ 30.
- `commission_pct`: decimal 0–100, hasta 2 decimales.

## Criterios de Aceptación

- [ ] **CA-02-01:** Se crea un propietario con % de comisión; el % queda disponible para las liquidaciones de todas sus propiedades.
- [ ] **CA-02-02:** Un `admin` puede editar los datos de contacto de un propietario pero recibe `403 FORBIDDEN` al intentar cambiar su % de comisión; el `owner` puede cambiarlo y el cambio queda auditado con valor anterior y nuevo.
- [ ] **CA-02-03:** El cambio de % de comisión no altera liquidaciones ya generadas: la liquidación siguiente usa el % nuevo (verificable por `commission_pct_used`).
- [ ] **CA-02-04:** `bank_info` se persiste cifrado (verificable a nivel DB) y jamás aparece en logs ni en respuestas de listado (solo en el detalle para owner/admin).
- [ ] **CA-02-05:** La ficha del inquilino muestra sus contratos y su estado de deuda con: períodos adeudados, saldo, días de mora e interés sugerido acumulado.
- [ ] **CA-02-06** (v1.1, issue #124 — el caso inquilino antes devolvía `409 ENTITY_HAS_DEPENDENCIES`): Borrar un propietario con propiedades activas devuelve `409 ENTITY_HAS_DEPENDENCIES`; borrar un inquilino con contrato `active` devuelve `422 ENTITY_HAS_ACTIVE_CONTRACT`; sin dependencias, la baja es lógica.
- [ ] **CA-02-07:** Un usuario `maintenance` recibe `403`/`404` (según sdd_03) en todos los endpoints de propietarios e inquilinos.
- [ ] **CA-02-08** (issue #124, RN-D05): `DELETE /renters/:id` con un contrato `active` devuelve `422 ENTITY_HAS_ACTIVE_CONTRACT` con `details.entity_type = "renter"`, `details.entity_id` y `details.active_contracts[]` (cada item con `contract_id`, `property_address`, `renter_name`, `start_date`, `end_date`); un contrato `terminated`/`expired`/`draft` no bloquea la baja.
- [ ] **CA-02-09** (issue #124, RN-D05): la baja lógica de un inquilino queda auditada (`renter.deleted`); el inquilino desaparece de `GET /renters`, su `GET /renters/:id` devuelve `404`, `POST /contracts` que lo referencie devuelve `404 NOT_FOUND`, y sus contratos históricos siguen exponiendo `renter_name` (RN-12).

## Integraciones

| Módulo | Motivo |
|---|---|
| Módulo 1 (Propiedades) | Las propiedades del propietario |
| Módulo 3 (Contratos) | Los contratos del inquilino |
| Módulo 4 (Cobranzas) | El estado de deuda sale de los períodos impagos |
| Módulo 5 (Liquidaciones) | `commission_pct` + historial de liquidaciones del propietario |
| Log de Auditoría | Cambios de % de comisión y de datos bancarios |
