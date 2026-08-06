---
name: AdminProp — Módulo 3 — Contratos de Locación
description: Contratos propiedad+inquilino con condiciones pactadas, ciclo de vida, ajustes por índice con ingreso manual del % y alertas de vencimiento
type: project
version: 1.0
fecha: 2026-08-06
---
# Módulo 3 — Contratos de Locación

**Versión:** 1.0 · **Estado:** Borrador para revisión · **Fecha:** 2026-08-06

## Propósito

El corazón del negocio: el contrato vincula propiedad + inquilino con las condiciones pactadas (moneda, monto, plazo, % de mora, régimen de ajuste). De él nacen los alquileres mensuales (Módulo 4) y las bases de la liquidación (Módulo 5). Los **ajustes por índice** viven acá como parte del ciclo de vida del contrato: el sistema avisa cuándo tocan y el operador ingresa el % calculado por fuera (UC-04, UC-05, UC-06).

## Actores

| Actor | Puede |
|---|---|
| owner / admin | ABM de contratos, activar/terminar, aplicar ajustes |
| maintenance | Nada (RN-A01) |

## Entidades Principales

- **Contract** — ver `sdd_02` §2.7 y `spec_data_model` Capa 3.
- **ContractAdjustment** — ver `sdd_02` §2.8: el pendiente que genera el sistema y el histórico inmutable de ajustes aplicados.

## Requerimientos Funcionales

### RF-01 — Listado y consulta

Listado con filtros: estado, propiedad, inquilino, propietario (vía propiedad), moneda, `expiring_in_days`. El detalle muestra condiciones, monto vigente, historial de ajustes y los períodos de alquiler generados.

### RF-02 — Alta de Contrato

- Campos: propiedad, inquilino, moneda (`ARS`/`USD`), monto inicial, fecha de inicio y fin, **% de mora diaria**, y solo para ARS: **frecuencia de ajuste en meses** + **índice de referencia** (`icl` / `ipc_cordoba` / `otro` + nota) — el índice es **informativo** (S-03 del PRD).
- El contrato nace en `draft`; los datos son editables hasta activarlo.
- Validaciones al crear (y al activar): no solapamiento con otro contrato `active` de la misma propiedad (`409 CONTRACT_OVERLAP`, RN-01); contratos USD sin configuración de ajuste (RN-03).

### RF-03 — Ciclo de vida

- `draft → active` (`POST /contracts/:id/activate`): valida solapamiento otra vez, pone la propiedad en `rented`, y **genera el rent_period del mes en curso** si la fecha de inicio ya pasó y aún no existe.
- `active → terminated` (`POST /contracts/:id/terminate` con motivo): rescisión anticipada; la propiedad vuelve a `available`; las deudas existentes siguen cobrables (RN-07).
- `active → expired`: automático al pasar `end_date` (job diario); mismo efecto sobre la propiedad.
- Un contrato activo **no** permite editar montos ni condiciones económicas: el monto solo cambia vía ajuste (RN-04); fechas de fin se pueden extender (renovación simple) quedando auditado.

### RF-04 — Ajustes por índice

El flujo completo del ajuste (RN-C03 del dominio):

1. **Detección:** el job diario `detect_due_adjustments` (`sdd_04` §1.3) detecta los contratos ARS cuyo próximo período de ajuste llegó (según `adjustment_frequency_months` contados desde el inicio o el último ajuste aplicado) y crea el `ContractAdjustment` en `pending` — uno solo por contrato (`409 ADJUSTMENT_PENDING_EXISTS`).
2. **Aviso:** notificación in-app + email a owner y admin (`adjustment_pending`), y el contrato aparece en la **bandeja de ajustes** (`GET /adjustments?status=pending`).
3. **Aplicación manual:** el operador calcula el % por fuera (según el índice de referencia del contrato) y lo ingresa (`POST /adjustments/:id/apply` con `pct`): el sistema calcula `new_amount = previous × (1 + pct/100)`, actualiza el monto vigente del contrato y marca el ajuste `applied` (quién, cuándo).
4. **Efecto en cobranzas:** el rent_period del mes de ajuste **no se genera** hasta que el ajuste esté aplicado (RN-P01); una vez aplicado, se genera con el monto nuevo.
5. **Historial:** cada ajuste aplicado es inmutable; una corrección es un nuevo ajuste con nota (`sdd_02` §2.8).

### RF-05 — Alertas de vencimiento

- El job diario `detect_expiring_contracts` notifica (in-app + email) los contratos que vencen dentro de `contract_expiry_notice_days` (default 60, configurable — Módulo 7). Una sola notificación por contrato y umbral.
- El listado soporta el filtro `expiring_in_days` para la vista "qué vence pronto" (UC-06).

## Reglas de Negocio (del módulo)

- **RN-01:** Una propiedad no puede tener dos contratos `active` con vigencias superpuestas (= RN-C01; constraint EXCLUDE en DB + validación app-level con mensaje claro).
- **RN-02:** Todo contrato nace `draft` y solo genera efectos (períodos, estado de propiedad) al activarse.
- **RN-03:** Un contrato USD no tiene frecuencia ni índice de ajuste (= RN-C02; CHECK en DB).
- **RN-04:** El monto vigente solo cambia mediante un ajuste registrado (= RN-C04).
- **RN-05:** `daily_late_fee_pct` es obligatorio y ≥ 0 desde el alta (sin él no se puede sugerir mora).
- **RN-06:** Propiedad e inquilino referenciados deben existir, no estar borrados y pertenecer a la organización (cross-tenant = 404, RN-D01).
- **RN-07:** Un contrato `expired`/`terminated` no genera nuevos períodos; sus deudas siguen cobrables (= RN-C05).

## Validaciones

- `initial_amount` > 0; `end_date` > `start_date`; duración máxima razonable (≤ 10 años).
- `adjustment_frequency_months` entero > 0 (solo ARS); `adjustment_index` obligatorio si hay frecuencia; `adjustment_index_notes` obligatoria si el índice es `otro`.
- `pct` del ajuste: decimal, puede ser negativo (deflación/renegociación) — confirmación explícita en UI si < 0; tope de sanidad ±500%.

## Criterios de Aceptación

- [ ] **CA-03-01:** Se crea un contrato ARS con % de mora, frecuencia de ajuste e índice de referencia; nace en `draft` y no genera períodos hasta activarse.
- [ ] **CA-03-02:** Crear o activar un contrato cuya vigencia se superpone con otro `active` de la misma propiedad devuelve `409 CONTRACT_OVERLAP` con el contrato en conflicto en `details`.
- [ ] **CA-03-03:** Crear un contrato USD con frecuencia o índice de ajuste devuelve `400 VALIDATION_ERROR` (RN-03).
- [ ] **CA-03-04:** Al llegar el mes de ajuste, el sistema crea el ajuste `pending`, notifica, y el contrato aparece en la bandeja; el rent_period de ese mes no existe todavía.
- [ ] **CA-03-05:** Al aplicar el ajuste con un %, el monto vigente se actualiza, el historial registra % / monto anterior / monto nuevo / autor, y el rent_period del mes se genera con el valor nuevo.
- [ ] **CA-03-06:** El monto vigente de un contrato activo no puede editarse por PATCH (`422 BUSINESS_RULE_VIOLATION` — RN-04); solo cambia vía ajuste.
- [ ] **CA-03-07:** Un contrato que vence dentro del umbral configurado genera la notificación de vencimiento una sola vez, y aparece en el filtro `expiring_in_days`.
- [ ] **CA-03-08:** Al terminar un contrato, la propiedad vuelve a `available` y sus períodos impagos siguen visibles en el estado de deuda.

## Integraciones

| Módulo | Motivo |
|---|---|
| Módulo 1 (Propiedades) | Estado `rented`/`available` derivado |
| Módulo 2 (Personas) | Inquilino del contrato; propietario vía propiedad |
| Módulo 4 (Cobranzas) | Los rent_periods nacen del contrato y su monto vigente |
| Notificaciones | `adjustment_pending`, `contract_expiring` |
| Log de Auditoría | Ajustes aplicados, terminaciones, extensiones de fecha |
