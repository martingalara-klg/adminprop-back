---
name: AdminProp — Módulo 1 — Propiedades
description: Inventario de inmuebles administrados con sus cuentas de servicios informativas y la ficha consolidada por propiedad
type: project
version: 1.0
fecha: 2026-08-06
---
# Módulo 1 — Propiedades

**Versión:** 1.0 · **Estado:** Borrador para revisión · **Fecha:** 2026-08-06

## Propósito

El inventario físico de la administradora: cada inmueble con su dueño, su estado y la información centralizada de números de cuenta de servicios e impuestos — el dato que la secretaria hoy busca en papeles sueltos cuando hace las verificaciones mensuales (UC-01).

## Actores

| Actor | Puede |
|---|---|
| owner / admin | ABM completo de propiedades y cuentas de servicio; ver la ficha consolidada |
| maintenance | Nada en este módulo (ve la dirección de la propiedad solo dentro de sus pedidos de reparación) |

## Entidades Principales

- **Property** — ver `sdd_02` §2.5 y `spec_data_model` Capa 2: dirección, propietario (obligatorio), tipo, estado (`available` / `rented` / `unavailable`), notas.
- **PropertyServiceAccount** — ver `sdd_02` §2.6: tipo de servicio (`rentas`, `municipalidad`, `luz`, `gas`, `agua`, `expensas`, `otro`), número de cuenta, número secundario (luz: n° de cliente + n° de contrato), notas. **Puramente informativa.**

## Requerimientos Funcionales

### RF-01 — ABM de Propiedades

- Alta con: dirección (obligatoria), propietario (obligatorio, FK a `landlords`), tipo (departamento / casa / local / cochera / otro), notas.
- Edición de todos los campos salvo el estado `rented` (derivado — ver RF-04).
- Baja: **soft delete** (RN-D02). Si la propiedad tiene un contrato `active` → `409 ENTITY_HAS_DEPENDENCIES`. El historial (contratos pasados, cobros, reparaciones) se conserva siempre.
- Listado con filtros: propietario, estado, tipo; búsqueda por dirección.

### RF-02 — Cuentas de Servicio (informativas)

- ABM de cuentas por propiedad: una fila por servicio, con número de cuenta y número secundario opcional.
- El caso `luz` usa ambos números (n° de cliente + n° de contrato), como los registra la distribuidora.
- **Ninguna lógica de negocio depende de estas cuentas**: no hay tracking de montos ni de pagos de servicios (los importes de rentas/muni que se descuentan en liquidaciones viven en el Módulo 5 como cargos del mes).
- Vista única: todas las cuentas de la propiedad visibles juntas en su ficha (UC-01).

### RF-03 — Ficha consolidada de la Propiedad

La vista de detalle reúne todo lo de la propiedad:
- Datos y cuentas de servicio.
- Contrato vigente (si hay) con inquilino y monto actual — link al Módulo 3.
- Historial de reparaciones con pagador y costos — link al Módulo 6 (UC-16).
- Conceptos recurrentes activos (rentas, muni) — link al Módulo 5.

### RF-04 — Estado automático

- `rented` ⟺ existe un contrato `active` sobre la propiedad (se actualiza al activar/terminar/vencer contratos — Módulo 3).
- `available` / `unavailable` son estados manuales válidos solo sin contrato activo (`unavailable` = en refacción, fuera de cartera temporal, etc.).

## Reglas de Negocio

- Toda propiedad pertenece a exactamente un propietario (`landlord_id` obligatorio).
- Cambiar el propietario de una propiedad con contrato activo o liquidaciones históricas es una operación auditada (afecta a quién se liquida — el cambio rige desde la próxima liquidación).
- RN-D01/RN-D02 aplican como en todo el sistema (aislamiento, soft delete).

## Validaciones

- `address`: 5–300 caracteres, obligatoria.
- `property_type`: uno del catálogo sugerido o texto libre corto (≤ 50).
- `service_type`: uno de los 7 valores del enum.
- `account_number`: 1–100 caracteres, obligatorio en cada cuenta.

## Criterios de Aceptación

- [ ] **CA-01-01:** Se crea una propiedad con dirección, propietario y tipo; aparece en el listado y en la ficha del propietario.
- [ ] **CA-01-02:** Se cargan las cuentas de rentas, muni, luz (con n° de cliente y n° de contrato), gas, agua y expensas de una propiedad y se ven todas juntas en su ficha.
- [ ] **CA-01-03:** Intentar borrar una propiedad con contrato activo devuelve `409 ENTITY_HAS_DEPENDENCIES`; sin contrato activo, la baja es lógica y la propiedad conserva su historial.
- [ ] **CA-01-04:** Al activarse un contrato la propiedad pasa a `rented` automáticamente; al terminarse, vuelve a `available`.
- [ ] **CA-01-05:** La ficha de la propiedad muestra contrato vigente, historial de reparaciones y conceptos recurrentes activos.
- [ ] **CA-01-06:** Un usuario `maintenance` no puede listar propiedades ni ver fichas (`403`/`404` según sdd_03) — solo ve la dirección dentro de sus pedidos.

## Integraciones

| Módulo | Motivo |
|---|---|
| Módulo 2 (Personas) | `landlord_id` — el dueño de la propiedad |
| Módulo 3 (Contratos) | Estado `rented` derivado del contrato activo |
| Módulo 5 (Liquidaciones) | Los conceptos recurrentes de la propiedad |
| Módulo 6 (Mantenimiento) | Historial de reparaciones en la ficha |
