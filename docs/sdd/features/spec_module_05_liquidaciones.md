---
name: AdminProp — Módulo 5 — Liquidaciones a Propietarios
description: Cargos del mes por propiedad, generación asíncrona de la rendición mensual por propietario (todo en ARS, TC manual si hay USD), regeneración auditada y exports Excel/PDF
type: project
version: 1.1
fecha: 2026-08-29
---
# Módulo 5 — Liquidaciones a Propietarios

**Versión:** 1.1 · **Estado:** Borrador para revisión · **Fecha:** 2026-08-06

## Propósito

El flujo de plata que sale: la rendición mensual a cada propietario. Consolida todas sus propiedades en un período — lo cobrado, menos la comisión de administración, menos los impuestos del mes (rentas, muni), menos las reparaciones que pagó la administración, listando aparte lo que el inquilino le transfirió directo ("ya rendido") — **todo expresado en pesos**, con tipo de cambio manual si hay montos en dólares (UC-11, UC-12, RN-L01..L06).

## Actores

| Actor | Puede |
|---|---|
| owner / admin | Cargar cargos del mes, generar/regenerar/emitir liquidaciones, exportar |
| maintenance | Nada (RN-A01) |

## Entidades Principales

- **RecurringCharge + ChargeEntry** — ver `sdd_02` §2.11: el concepto recurrente de la propiedad y el importe del mes.
- **Settlement + SettlementLineItem** — ver `sdd_02` §2.15: la liquidación con totales en ARS, `exchange_rate`, `commission_pct_used` y el detalle línea por línea.

## Requerimientos Funcionales

### RF-01 — Generación de liquidación (asíncrona)

- `POST /settlements/generate` con `{ landlord_id, period, exchange_rate? }` → **`202 Accepted`** + polling de `GET /settlements/:id` (patrón async de `sdd_03`).
- **Estados del job de generación:** `pending` → `processing` → `completed` | `with_errors` | `failed`.
  - `completed`: liquidación `draft` lista con sus exports generados.
  - `with_errors`: liquidación `draft` generada pero con advertencias (períodos del mes aún impagos, propiedades sin cargos cargados) — listadas en el detalle para decidir si emitir igual.
  - `failed`: no se generó (error real); el motivo queda en el job y en Sentry.
- **Tipo de cambio (RN-L06):** si el propietario tiene cobros o alquileres USD en el período y no vino `exchange_rate` → `400 SETTLEMENT_EXCHANGE_RATE_REQUIRED` (validación sincrónica, antes del 202).
- Única por `(landlord_id, period)` → `409 SETTLEMENT_ALREADY_EXISTS` (para corregir se regenera la existente, RF-03).
- El cálculo corre en `documents_worker` (`sdd_04` §1.3) con `set_tenant_context`.

### RF-02 — Contenido y fórmula

Todo en ARS; los montos USD se convierten con el `exchange_rate` de la liquidación y el detalle muestra el valor original junto al convertido.

```
neto a rendir = Σ cobros del período con destino administración (capital + intereses cobrados)
              − comisión = commission_pct del propietario × (alquileres + intereses cobrados) del período,
                           incluidos los cobrados directo por el dueño (RN-L02)
              − Σ cargos del mes (rentas, municipalidad, otros)
              − Σ reparaciones closed con payer = agency aún no liquidadas (RN-L04)
```

- Los cobros `landlord_account` se listan como **"ya rendido"**: no suman al neto, sí a la base de comisión (RN-P07).
- Los **intereses de mora cobrados** integran lo que se rinde al propietario, y **pagan comisión igual que el alquiler** (la base de comisión es capital + intereses cobrados).
- **Exclusión de carga inicial (issue #119, RN-06/RN-P09):** los cobros `origin = initial_load` (generados automáticamente al dar de alta un contrato en curso, Módulo 3 RF-02/RN-11) quedan TOTALMENTE fuera de la fórmula — ni suman al neto, ni a la base de comisión, ni aparecen como línea "ya rendido" — a diferencia de `landlord_account`, que sí integra la base de comisión (RN-P07). Ese dinero ya fue rendido fuera del sistema antes de que el contrato existiera en AdminProp, por lo que no corresponde cobrar comisión sobre él.
- `commission_pct_used` congela el % usado (RN-L05).
- Line items por tipo: `rent_collected` / `commission` / `tax_charge` / `repair` / `already_settled`, cada uno con referencia a su origen (cobro, cargo, pedido) y propiedad.

### RF-03 — Emisión, regeneración y exports

- `draft → issued` (`POST /settlements/:id/issue`): la rendición queda formalmente emitida.
- **Regeneración (RN-L03):** `POST /settlements/:id/regenerate` (202) recalcula con los datos corregidos (cobros anulados/agregados, cargos corregidos, TC nuevo si se pasa). `regenerated_count++` y auditoría con qué cambió. Una liquidación emitida sigue siendo regenerable — la flexibilidad es deliberada (decisión del PRD, R-04).
- Si se **anula un cobro** incluido en una liquidación, esta queda marcada "requiere regeneración" y visible así en el listado (integración Módulo 4 RF-05).
- **Exports:** Excel (openpyxl) + PDF (WeasyPrint) generados por `documents_worker`, guardados como Adjuntos; `GET /settlements/:id/export?format=xlsx|pdf` los descarga. El encabezado usa los datos de la organización (Módulo 7).

### RF-04 — Desglose por propiedad (`scope=per_property`)

- El detalle y los exports agrupan **por propiedad**: cada propiedad con sus cobros, cargos y reparaciones, con subtotal; el consolidado del propietario al final.
- `GET /settlements/:id?scope=per_property` devuelve los line items agrupados por propiedad; `scope=consolidated` (default) devuelve los totales + detalle plano.

### RF-05 — Conceptos recurrentes y cargos del mes

- ABM de conceptos por propiedad (`rentas`, `municipalidad`, `otro` + label). Un concepto inactivo deja de aparecer en la carga mensual.
- **Carga mensual:** `POST /recurring-charges/:id/entries` con `{ period, amount }` — el importe varía mes a mes y se ingresa a mano (UC-11). Duplicado → `409 CHARGE_ENTRY_ALREADY_EXISTS`; corrección vía `PATCH` auditado.
- **Vista de verificación:** `GET /charge-entries?period=` muestra qué propiedades ya tienen sus cargos del mes y cuáles faltan — el checklist mensual de la secretaria.

## Wizard de liquidación mensual (UI)

Flujo guiado del frontend (`flow-implementation.md`):

1. **`select_period`** — elegir propietario y período; el sistema muestra el estado del mes (cobros registrados, cargos cargados, reparaciones pendientes de liquidar).
2. **`review`** — checklist previo: períodos impagos del propietario, propiedades sin cargos, reparaciones `closed` sin liquidar. Se puede continuar igual (generará `with_errors`) o salir a completar.
3. **`exchange_rate`** — solo si hay montos USD: ingreso manual del TC (mostrando qué montos va a convertir).
4. **`confirmation`** — resumen de la fórmula con los totales estimados → `POST /settlements/generate` → pantalla de progreso (polling) → detalle con exports.

## Reglas de Negocio (del módulo)

- **RN-01:** Neto y totales siempre en ARS; conversión solo con el TC manual de la liquidación (= RN-L06).
- **RN-02:** Comisión sobre los alquileres del período **más los intereses de mora cobrados**, de todas las propiedades del dueño, incluidos los directos (= RN-L02).
- **RN-03:** Regeneración libre pero siempre auditada; nunca se borra una liquidación (= RN-L03, RN-D02).
- **RN-04:** Una reparación se descuenta una sola vez, en una sola liquidación (= RN-L04; `settled_in_settlement_id`).
- **RN-05:** Una liquidación por propietario y período; correcciones = regeneración, no duplicado.
- **RN-06** (issue #119, = RN-P09 de `sdd_02`): los cobros `origin = initial_load` (carga inicial del alta de contrato en curso, Módulo 3/4) están EXCLUIDOS de la fórmula de liquidación — ni neto ni base de comisión.

## Validaciones

- `period`: mes válido no futuro. `exchange_rate` > 0 cuando se provee.
- No se puede generar la liquidación de un período si el propietario no tiene ninguna propiedad con contrato activo ni movimientos en ese mes (`422 BUSINESS_RULE_VIOLATION` con mensaje claro).

## Criterios de Aceptación

- [ ] **CA-05-01:** Generar la liquidación de un propietario con 2 propiedades ARS consolida cobros − comisión (con su `commission_pct`) − cargos − reparaciones agency, y el neto coincide con la fórmula a centavo (redondeo half-even a 2 decimales).
- [ ] **CA-05-02:** Con una propiedad USD en el período y sin TC, `POST /settlements/generate` devuelve `400 SETTLEMENT_EXCHANGE_RATE_REQUIRED`; con TC, el detalle muestra el monto USD original y el convertido, y los totales quedan en ARS.
- [ ] **CA-05-03:** La generación responde 202 y el polling atraviesa `processing` → `completed`; con períodos impagos o cargos faltantes termina `with_errors` y las advertencias se listan en el detalle.
- [ ] **CA-05-04:** Un cobro "ya rendido" aparece como línea informativa que descuenta del neto pero integra la base de comisión.
- [ ] **CA-05-05:** Una reparación `closed` con payer agency se descuenta en la liquidación y queda vinculada (`settled_in_settlement_id`); regenerar no la descuenta dos veces.
- [ ] **CA-05-06:** Anular un cobro de una liquidación emitida la marca "requiere regeneración"; al regenerar, los totales se recomputan, `regenerated_count` incrementa y la auditoría registra el cambio.
- [ ] **CA-05-07:** El export Excel y el PDF agrupan por propiedad con subtotales (`scope=per_property`) y quedan descargables desde el detalle y desde la ficha del propietario.
- [ ] **CA-05-08:** `GET /charge-entries?period=` muestra las propiedades con cargos cargados y las que faltan; cargar dos veces el mismo concepto+mes devuelve `409 CHARGE_ENTRY_ALREADY_EXISTS`.
- [ ] **CA-05-09** (issue #119, RN-06): generar la liquidación de un período que incluye cobros `origin = initial_load` (carga inicial del alta de un contrato en curso) los EXCLUYE por completo del cálculo — no suman al neto ni a la base de comisión, y no aparecen como línea `already_settled`.

## Integraciones

| Módulo | Motivo |
|---|---|
| Módulo 2 (Personas) | `commission_pct` del propietario; historial en su ficha |
| Módulo 4 (Cobranzas) | Cobros del período (capital + intereses, destino) |
| Módulo 6 (Mantenimiento) | Reparaciones agency `closed` sin liquidar |
| Módulo 7 (Administración) | Datos del encabezado de los exports |
| `documents_worker` | Cálculo + Excel/PDF asíncronos |
| Log de Auditoría | Regeneraciones con detalle de cambios |
