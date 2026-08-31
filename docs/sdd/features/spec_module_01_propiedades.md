---
name: AdminProp — Módulo 1 — Propiedades
description: Inventario de inmuebles administrados con sus cuentas de servicios informativas, catálogo de barrios y la ficha consolidada por propiedad
type: project
version: 1.4
fecha: 2026-08-31
---
# Módulo 1 — Propiedades

**Versión:** 1.4 · **Estado:** Borrador para revisión · **Fecha:** 2026-08-31

## Propósito

El inventario físico de la administradora: cada inmueble con su dueño, su estado y la información centralizada de números de cuenta de servicios e impuestos — el dato que la secretaria hoy busca en papeles sueltos cuando hace las verificaciones mensuales (UC-01).

## Actores

| Actor | Puede |
|---|---|
| owner / admin | ABM completo de propiedades y cuentas de servicio; ver la ficha consolidada |
| maintenance | Nada en este módulo (ve la dirección de la propiedad solo dentro de sus pedidos de reparación) |

## Entidades Principales

- **Property** — ver `sdd_02` §2.5 y `spec_data_model` Capa 2: dirección, propietario (obligatorio), barrio (obligatorio en API — issue #99), tipo, estado (`available` / `rented` / `unavailable`), notas.
- **PropertyServiceAccount** — ver `sdd_02` §2.6: tipo de servicio (`rentas`, `municipalidad`, `luz`, `gas`, `agua`, `expensas`, `otro`), número de cuenta, número secundario (luz: n° de cliente + n° de contrato), notas. **Puramente informativa.**
- **Neighborhood** — ver `sdd_02` §2.4a: catálogo de barrios parametrizable por organización (issue #99), con la intención futura de agrupar por barrio en liquidaciones y vistas.

## Requerimientos Funcionales

### RF-01 — ABM de Propiedades

- Alta con: dirección (obligatoria), propietario (obligatorio, FK a `landlords`), barrio (**obligatorio**, FK a `neighborhoods` — issue #99, decisión del PO), tipo (departamento / casa / local / cochera / otro), notas.
- Edición de todos los campos salvo el estado `rented` (derivado — ver RF-04). El barrio es editable pero, si el campo viene en el `PATCH`, no puede vaciarse (mismo criterio de obligatoriedad que el alta).
- Baja: **soft delete** (RN-D02/RN-D05 — issue #124, decisión #130, reemplaza el `409 ENTITY_HAS_DEPENDENCIES` que este caso devolvía hasta v1.3). Si la propiedad tiene un contrato `active` (no eliminado) → `422 ENTITY_HAS_ACTIVE_CONTRACT` con `details.active_contracts[]` (contratos `draft`/`expired`/`terminated` NO bloquean). Sin contrato activo, la baja es lógica y auditada (`property.deleted`): la propiedad desaparece del listado y de los selects (no es elegible para contratos ni pedidos de reparación nuevos → `404`), su ficha y sub-recursos devuelven `404`, y el historial (contratos pasados, cobros, liquidaciones, reparaciones — legibles vía sus endpoints top-level) se conserva siempre; la deuda de sus contratos históricos no eliminados sigue computándose (RN-C05).
- Listado con filtros: propietario, estado, tipo, **barrio** (issue #99); búsqueda por dirección.
- **Propiedades legacy** (creadas antes de issue #99) pueden tener `neighborhood_id = NULL` en DB — siguen siendo legibles en listado/ficha (con `neighborhood: null`); la obligatoriedad del campo rige solo para altas/ediciones de ahora en más, nunca retroactiva.

### RF-05 — ABM del catálogo de Barrios (issue #99)

- Sección "Barrios" dentro del módulo de propiedades (decisión del PO: no es un módulo aparte).
- Alta: `name` (obligatorio, único por organización, case-insensitive).
- Edición: rename (`name`).
- Baja: **soft delete** (RN-D02). Si el barrio tiene propiedades asociadas (no borradas) → `409 ENTITY_HAS_DEPENDENCIES`.
- Listado del catálogo completo de la organización (sin paginación — conjunto acotado).
- Permisos: lectura con `property:read`; alta/edición/baja con `property:manage` — **sin permisos nuevos**.

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
- **Defensa en profundidad (issue #109):** `PATCH /properties/:id` con `status` en el body sobre una propiedad con contrato `active` devuelve `422 INVALID_STATUS_TRANSITION` — el invariante `rented ⟺ contrato active` no se rompe manualmente ni siquiera cuando el cliente omite la mitigación del front (PR `adminprop-front#58`). Un `PATCH` que **no** incluye `status` sobre una propiedad `rented` sigue editando el resto de los campos con normalidad.

## Reglas de Negocio

- Toda propiedad pertenece a exactamente un propietario (`landlord_id` obligatorio).
- Cambiar el propietario de una propiedad con contrato activo o liquidaciones históricas es una operación auditada (afecta a quién se liquida — el cambio rige desde la próxima liquidación).
- RN-D01/RN-D02 aplican como en todo el sistema (aislamiento, soft delete).

## Validaciones

- `address`: 5–300 caracteres, obligatoria.
- `property_type`: catálogo cerrado (decisión #122, issue #103) — uno de `departamento`, `casa`, `duplex`, `local`, `cochera`, `otro`. Un valor fuera del catálogo devuelve `400 VALIDATION_ERROR`.
- `service_type`: uno de los 7 valores del enum.
- `account_number`: 1–100 caracteres, obligatorio en cada cuenta.
- `neighborhood_id` (issue #99): obligatorio en `POST /properties`; en `PATCH /properties/:id` obligatorio solo si el campo viene en el body (no puede enviarse `null`). Debe referenciar un barrio existente, del mismo tenant y no borrado — de lo contrario `404 NOT_FOUND` (`field: "neighborhood_id"`).
- `neighborhoods.name`: 1–100 caracteres, obligatorio, único por organización (case-insensitive).

## Criterios de Aceptación

- [ ] **CA-01-01:** Se crea una propiedad con dirección, propietario y tipo; aparece en el listado y en la ficha del propietario.
- [ ] **CA-01-02:** Se cargan las cuentas de rentas, muni, luz (con n° de cliente y n° de contrato), gas, agua y expensas de una propiedad y se ven todas juntas en su ficha.
- [ ] **CA-01-03** (v1.4, issue #124 — antes devolvía `409 ENTITY_HAS_DEPENDENCIES`): Intentar borrar una propiedad con contrato activo devuelve `422 ENTITY_HAS_ACTIVE_CONTRACT`; sin contrato activo, la baja es lógica y la propiedad conserva su historial.
- [ ] **CA-01-04:** Al activarse un contrato la propiedad pasa a `rented` automáticamente; al terminarse, vuelve a `available`.
- [ ] **CA-01-05:** La ficha de la propiedad muestra contrato vigente, historial de reparaciones y conceptos recurrentes activos.
- [ ] **CA-01-06:** Un usuario `maintenance` no puede listar propiedades ni ver fichas (`403`/`404` según sdd_03) — solo ve la dirección dentro de sus pedidos.
- [ ] **CA-01-07** (issue #99): ABM de barrios funciona — alta, rename, listado del catálogo y baja lógica; un `name` duplicado (case-insensitive) en la misma organización devuelve `409 CONFLICT`; borrar un barrio con propiedades asociadas devuelve `409 ENTITY_HAS_DEPENDENCIES`.
- [ ] **CA-01-08** (issue #99): Crear o editar una propiedad sin `neighborhood_id` devuelve `400 VALIDATION_ERROR`; con un `neighborhood_id` inexistente o de otra organización devuelve `404 NOT_FOUND`. Una propiedad legacy (creada antes de issue #99, `neighborhood_id = NULL` en DB) sigue siendo legible en listado y ficha, con `neighborhood: null`.
- [ ] **CA-01-09** (issue #99): `GET /properties?neighborhood_id=<id>` devuelve solo las propiedades de ese barrio.
- [ ] **CA-01-10** (issue #103): `duplex` es un valor válido de `property_type` — se puede crear y editar una propiedad con `property_type: "duplex"`; un valor fuera del catálogo cerrado (`departamento`/`casa`/`duplex`/`local`/`cochera`/`otro`) devuelve `400 VALIDATION_ERROR`.
- [ ] **CA-01-11** (issue #109): `PATCH /properties/:id` con `status` (`available`/`unavailable`) sobre una propiedad con contrato `active` devuelve `422 INVALID_STATUS_TRANSITION`, sin aplicar el cambio; el mismo `PATCH` **sin** `status` sobre esa propiedad `rented` devuelve `200` y aplica el resto de los campos editados.
- [ ] **CA-01-12** (issue #124, RN-D05): `DELETE /properties/:id` con un contrato `active` devuelve `422 ENTITY_HAS_ACTIVE_CONTRACT` con `details.entity_type = "property"`, `details.entity_id` y `details.active_contracts[]` (cada item con `contract_id`, `property_address`, `renter_name`, `start_date`, `end_date`); un contrato `terminated`/`expired`/`draft` no bloquea la baja.
- [ ] **CA-01-13** (issue #124, RN-D05): la baja lógica de una propiedad queda auditada (`property.deleted`); la propiedad desaparece de `GET /properties`, su `GET /properties/:id` devuelve `404`, y `POST /contracts` o `POST /work-orders` que la referencien devuelven `404 NOT_FOUND`.
- [ ] **CA-01-14** (issue #124, RN-D05): tras la baja lógica, la trazabilidad queda intacta — los contratos históricos de la propiedad siguen exponiendo `property_address` (RN-12) y sus pedidos de reparación siguen legibles en `GET /work-orders`.

## Integraciones

| Módulo | Motivo |
|---|---|
| Módulo 2 (Personas) | `landlord_id` — el dueño de la propiedad |
| Módulo 3 (Contratos) | Estado `rented` derivado del contrato activo |
| Módulo 5 (Liquidaciones) | Los conceptos recurrentes de la propiedad |
| Módulo 6 (Mantenimiento) | Historial de reparaciones en la ficha |
