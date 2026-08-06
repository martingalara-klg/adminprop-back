---
name: AdminProp — Módulo 4 — Cobranzas y Mora
description: Generación mensual de alquileres pendientes, registro de cobros (medio, moneda, TC, destino), mora sugerida con perdón total/parcial, pagos parciales y estado de deuda
type: project
version: 1.0
fecha: 2026-08-06
---
# Módulo 4 — Cobranzas y Mora

**Versión:** 1.0 · **Estado:** Borrador para revisión · **Fecha:** 2026-08-06

## Propósito

El flujo de plata que entra: cada mes el sistema genera los alquileres pendientes de todos los contratos activos, la secretaria registra los cobros como efectivamente ocurren (efectivo o transferencia, pesos o dólares, a la cuenta de la administración o directo al dueño), y ante pagos fuera de término el sistema **sugiere** el interés pero la imputación es libre — el perdón total o parcial es decisión humana y queda registrado (UC-07, UC-08, UC-09, UC-10).

## Actores

| Actor | Puede |
|---|---|
| owner / admin | Todo el módulo: ver panel, registrar cobros, perdonar intereses, anular cobros, ver deuda |
| maintenance | Nada (RN-A01) |

## Entidades Principales

- **RentPeriod** — ver `sdd_02` §2.9: el alquiler de un mes de un contrato; único por `(contract, period)`.
- **Payment** — ver `sdd_02` §2.10: la imputación con medio, moneda, TC, destino e interés sugerido/cobrado/perdonado.

## Requerimientos Funcionales

### RF-01 — Generación mensual de pendientes

- El job `generate_rent_periods` (Beat, 1° de cada mes, `sdd_04` §1.3) crea el rent_period de cada contrato `active` con el monto vigente, en estado `pending`. **Idempotente** (el UNIQUE por contrato+período garantiza no duplicar).
- Excepción: contrato con ajuste `pending` para ese período → el rent_period se genera recién al aplicarse el % (RN-P01, ver Módulo 3 RF-04).
- Al activarse un contrato a mitad de mes, su rent_period del mes en curso se genera en el acto (Módulo 3 RF-03).

### RF-02 — Panel de cobranzas del mes

- `GET /rent-periods?period=YYYY-MM` con los alquileres del período y su estado: `pending` / `partial` / `paid`, más el derivado **"en mora"** (pendiente o parcial con el día de gracia vencido).
- Filtros: estado, `in_arrears=true`, propiedad, propietario, inquilino.
- Cada fila muestra: propiedad, inquilino, monto, saldo, días de mora e interés sugerido al día de hoy.

### RF-03 — Registro de cobro

- `POST /rent-periods/:id/payments` con: fecha de pago, medio (`cash`/`transfer`), moneda del pago, importe (a capital, en la moneda del contrato), TC si la moneda difiere, destino, interés cobrado y notas.
- **Moneda y TC (RN-P06):** contrato USD pagado en ARS (o viceversa) exige `exchange_rate` libre (`400 EXCHANGE_RATE_REQUIRED` si falta). El TC es del momento del pago y queda en el cobro.
- **Destino (RN-P07):** `agency_account` (entra a la administración) o `landlord_account` (fue directo al dueño = "dinero ya rendido"; no suma al neto a rendir pero sí a la base de comisión).
- **Pagos parciales (RN-P05):** importe > 0 y ≤ saldo (`422 PAYMENT_EXCEEDS_CONTRACT_BALANCE` si excede). El período pasa a `partial` y el saldo restante sigue generando mora.
- El período pasa a `paid` cuando el capital imputado alcanza `amount_due`.

### RF-04 — Mora sugerida y perdón

- **Preview:** `GET /rent-periods/:id/interest-preview?payment_date=` calcula el interés sugerido a esa fecha: `saldo impago × daily_late_fee_pct del contrato × días de mora` (RN-P02/P03; día de gracia de la org, default 10; día 11 = 1 día).
- **Imputación libre (RN-P04):** al registrar el cobro, el operador ve el sugerido y decide el interés cobrado: igual, cero (perdón total) o un valor intermedio (perdón parcial). El sistema persiste los tres valores: `suggested_interest`, `charged_interest`, `forgiven_interest`.
- Todo perdón (forgiven > 0) queda en el log de auditoría (`interest.forgiven`) con el cobro asociado.

### RF-05 — Anulación de cobro

- `POST /payments/:id/void` con motivo: anulación **lógica** (RN-D04) — el cobro queda visible con marca de anulado, el saldo del período se recompone, y la anulación se audita con autor y motivo.
- Un cobro ya anulado → `409 PAYMENT_ALREADY_VOIDED`. Un cobro incluido en una liquidación emitida puede anularse igual: la liquidación afectada queda marcada para regeneración (Módulo 5 RF-03).

### RF-06 — Estado de deuda global

- `GET /debt` consolidado: por inquilino y propiedad, períodos adeudados, saldo, días de mora, interés sugerido acumulado. Filtros: propietario, inquilino, `min_days` (antigüedad). Es la vista de gestión de morosos (UC-10).

## Reglas de Negocio (del módulo)

- **RN-01:** Un rent_period por contrato y mes; la generación es idempotente (= RN-P01).
- **RN-02:** En término hasta el día de gracia inclusive; mora desde el día siguiente (= RN-P02).
- **RN-03:** Interés sugerido sobre el **saldo impago**, no sobre el alquiler completo (= RN-P03).
- **RN-04:** El interés cobrado lo decide el operador; sugerido/cobrado/perdonado siempre quedan registrados (= RN-P04).
- **RN-05:** El capital imputado nunca supera el monto del período (los intereses cobrados van aparte, no reducen capital).
- **RN-06:** Cobros inmutables una vez registrados: los errores se corrigen anulando (lógico) y recargando (= RN-D04).
- **RN-07:** Todos los importes del cobro se expresan en la moneda del contrato; el TC solo documenta la conversión del pago recibido.

## Validaciones

- `payment_date`: no futura; puede ser anterior a hoy (carga diferida).
- `amount` > 0; `exchange_rate` > 0 cuando aplica.
- `charged_interest` ≥ 0; el sistema no impone tope (imputación libre) pero la UI pide confirmación si supera al sugerido.

## Criterios de Aceptación

- [ ] **CA-04-01:** El 1° del mes, cada contrato activo tiene su rent_period `pending` con el monto vigente; re-ejecutar el job no duplica ninguno.
- [ ] **CA-04-02:** Un contrato con ajuste pendiente no genera el período del mes hasta aplicar el %; al aplicarlo, el período nace con el monto nuevo.
- [ ] **CA-04-03:** Un cobro de contrato USD pagado en pesos sin `exchange_rate` devuelve `400 EXCHANGE_RATE_REQUIRED`; con TC, el cobro registra el TC usado.
- [ ] **CA-04-04:** Un pago parcial deja el período en `partial`, y el interés de un pago posterior se calcula solo sobre el saldo restante.
- [ ] **CA-04-05:** Pagando el día 15 con día de gracia 10, el sistema sugiere interés por 5 días de mora con el % del contrato; el operador puede imputar 0 (perdón total) o un valor menor (parcial), y quedan registrados sugerido/cobrado/perdonado.
- [ ] **CA-04-06:** Todo perdón de interés queda en el log de auditoría con autor y cobro asociado.
- [ ] **CA-04-07:** Anular un cobro recompone el saldo del período, conserva el cobro visible como anulado, y queda auditado con motivo; anular dos veces devuelve `409 PAYMENT_ALREADY_VOIDED`.
- [ ] **CA-04-08:** Un cobro con destino "cuenta del propietario" aparece en la liquidación como "ya rendido": descuenta del neto pero paga comisión (verificable en Módulo 5).
- [ ] **CA-04-09:** El estado de deuda muestra por inquilino los períodos adeudados con saldo, días de mora e interés sugerido acumulado, filtrable por antigüedad.

## Integraciones

| Módulo | Motivo |
|---|---|
| Módulo 3 (Contratos) | Monto vigente y % de mora del contrato; bloqueo por ajuste pendiente |
| Módulo 5 (Liquidaciones) | Los cobros del período son la base de la liquidación; anulaciones disparan regeneración |
| Módulo 7 (Administración) | `grace_day` configurable de la organización |
| Log de Auditoría | Perdones de interés, anulaciones de cobros |
