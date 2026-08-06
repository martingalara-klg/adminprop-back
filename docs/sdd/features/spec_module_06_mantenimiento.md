---
name: AdminProp — Módulo 6 — Mantenimiento y Cotizaciones
description: Pedidos de reparación con ciclo cotización → aprobación → ejecución → cierre, pagador Dueño/Administración, fotos y historial por propiedad. Único módulo del rol maintenance
type: project
version: 1.0
fecha: 2026-08-06
---
# Módulo 6 — Mantenimiento y Cotizaciones

**Versión:** 1.0 · **Estado:** Borrador para revisión · **Fecha:** 2026-08-06

## Propósito

Reemplazar el WhatsApp: el ciclo completo de un arreglo — se carga el pedido, el encargado cotiza con fotos, se aprueba, se ejecuta y se cierra — con trazabilidad de quién paga cada arreglo (**Paga: Dueño / Administración**) y su efecto en la liquidación (UC-13..UC-16). Es el **único módulo accesible para el rol `maintenance`**.

## Actores

| Actor | Puede |
|---|---|
| owner / admin | Crear pedidos, aprobar cotizaciones, cancelar, ver todo; admin además puede cotizar/cerrar (backup del encargado) |
| **maintenance** | Ver los pedidos de la organización, subir cotizaciones con fotos, marcar terminado. **Nada más en todo el sistema** (RN-A01) |

## Entidades Principales

- **WorkOrder** — ver `sdd_02` §2.12: estados `open` → `in_progress` → `closed` (o `cancelled`), `payer`, `final_cost`, vínculo a la liquidación donde se descontó.
- **WorkOrderQuote** — ver `sdd_02` §2.13: `submitted` → `approved` | `discarded`.
- **Attachment** — ver `sdd_02` §2.14: fotos del pedido, de las cotizaciones y del cierre.

## Requerimientos Funcionales

### RF-01 — Carga del pedido de reparación

- Owner/admin crean el pedido: propiedad, título, descripción, **pagador** (`landlord` = Dueño / `agency` = Administración) y fotos opcionales.
- Al crearse, los usuarios `maintenance` de la org reciben notificación (`work_order_created`) in-app + email.
- El pedido nace `open` y acepta cotizaciones.

### RF-02 — Cotizaciones del encargado

- El encargado (o admin) sube una o más cotizaciones: monto, descripción, fotos/archivos.
- Cada cotización nueva notifica a owner y admin (`quote_submitted`).
- Un pedido `open` puede acumular cotizaciones; todas quedan visibles con autor y fecha.

### RF-03 — Aprobación

- Owner/admin aprueban **una** cotización (`POST /quotes/:id/approve`): el pedido pasa a `in_progress`, las demás cotizaciones quedan `discarded`, y el encargado recibe notificación.
- Aprobar sobre un pedido que ya tiene aprobada → `409 QUOTE_ALREADY_APPROVED`.

### RF-04 — Ejecución y cierre

- El encargado (o admin) marca el trabajo terminado (`POST /work-orders/:id/close`) con fotos del resultado (opcional) y `final_cost` ajustable (default: el monto de la cotización aprobada).
- Al cerrar: notificación a owner y admin (`work_order_closed`).
- Si `payer = agency`: el costo queda **pendiente de liquidar** y se descuenta en la próxima liquidación del propietario (RN-L04, Módulo 5). Si `payer = landlord`: solo historial.
- Cerrar un pedido ya cerrado → `409 WORK_ORDER_ALREADY_CLOSED`.

### RF-05 — Cancelación

- Owner/admin cancelan un pedido `open` o `in_progress` (con motivo). Un pedido `closed` ya liquidado no puede cancelarse ni reabrirse (`422 WORK_ORDER_ALREADY_SETTLED`).

### RF-06 — Historial por propiedad

- La ficha de la propiedad (Módulo 1 RF-03) lista todas las reparaciones: fecha, descripción, estado, pagador, costo final y — si aplica — en qué liquidación se descontó.
- Incluye las pagadas por el dueño (solo historial) y las de la administración (historial + liquidación).

## Reglas de Negocio (del módulo)

- **RN-01:** El `payer` se define al crear el pedido y es visible en todo el ciclo; determina el efecto en la liquidación (RN-L04).
- **RN-02:** Una sola cotización `approved` por pedido (índice parcial único en DB).
- **RN-03:** El rol `maintenance` ve los pedidos de su organización con la dirección de la propiedad, pero **sin datos del contrato, inquilino, cobros ni montos de liquidación** (RN-A01).
- **RN-04:** Un pedido `closed` con `settled_in_settlement_id` es inmutable (no reabre, no cambia costo): una corrección posterior es un ajuste manual documentado en la liquidación regenerada.
- **RN-05:** Los adjuntos heredan los permisos del pedido (RN de `sdd_02` §2.14).

## Validaciones

- `title`: 3–200 caracteres. `amount` de cotización > 0. `final_cost` ≥ 0.
- Adjuntos: jpg/png/webp/pdf, ≤ 10 MB por archivo, ≤ 10 por entidad.

## Criterios de Aceptación

- [ ] **CA-06-01:** Al crear un pedido con pagador y fotos, el encargado recibe la notificación y lo ve en su listado con la dirección de la propiedad.
- [ ] **CA-06-02:** El encargado sube dos cotizaciones con fotos; owner y admin reciben notificación por cada una.
- [ ] **CA-06-03:** Al aprobar una cotización, el pedido pasa a `in_progress`, la otra queda `discarded`, y el encargado es notificado; aprobar de nuevo devuelve `409 QUOTE_ALREADY_APPROVED`.
- [ ] **CA-06-04:** Al cerrar el trabajo con fotos y costo final, owner y admin son notificados; con `payer=agency` el costo aparece como pendiente de liquidar; con `payer=landlord` solo queda en el historial.
- [ ] **CA-06-05:** El historial de la propiedad muestra todas las reparaciones con pagador, costo y liquidación asociada cuando corresponde.
- [ ] **CA-06-06:** Un usuario `maintenance` recibe `403`/`404` (según sdd_03) al intentar acceder a contratos, cobros, liquidaciones, propietarios o inquilinos; el intento queda auditado.
- [ ] **CA-06-07:** Un pedido cerrado y ya liquidado no puede cancelarse ni reabrirse (`422 WORK_ORDER_ALREADY_SETTLED`).

## Integraciones

| Módulo | Motivo |
|---|---|
| Módulo 1 (Propiedades) | El pedido pertenece a una propiedad; historial en su ficha |
| Módulo 5 (Liquidaciones) | Reparaciones `agency` cerradas se descuentan una única vez |
| Notificaciones | `work_order_created`, `quote_submitted`, `work_order_closed` |
| Attachments | Fotos de pedido, cotizaciones y cierre |
