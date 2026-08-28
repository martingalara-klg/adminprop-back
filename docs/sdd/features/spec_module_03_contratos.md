---
name: AdminProp — Módulo 3 — Contratos de Locación
description: Contratos propiedad+inquilino con condiciones pactadas, ciclo de vida, ajustes por índice con ingreso manual del % y alertas de vencimiento
type: project
version: 1.2
fecha: 2026-08-28
---
# Módulo 3 — Contratos de Locación

**Versión:** 1.2 · **Estado:** Borrador para revisión · **Fecha:** 2026-08-28

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
- **Alta de contrato en curso (issue #100, RN-08/RN-C06):** dos campos opcionales adicionales, `current_amount` + `current_amount_since`, para migrar contratos que ya vienen corriendo con aumentos ya ocurridos por fuera del sistema (ej: contrato firmado hace 8 meses). Solo se aceptan **juntos** (`400 VALIDATION_ERROR` si viene uno sin el otro); aplican a **ARS y USD** por igual. Si vienen, el contrato nace con `current_amount` en el monto vigente declarado (no en `initial_amount`, que queda como el monto histórico informativo) y el sistema registra un ajuste `applied` sintético de "carga inicial" (ver RF-04 y `sdd_02` §2.8) — sin tocar el flujo normal de ajustes manuales.

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
6. **Ajuste sintético de carga inicial (issue #100, RN-08/RN-C06):** al declarar `current_amount`/`current_amount_since` en el alta (RF-02), el sistema salta los pasos 1-2 (no hay detección ni aviso: el operador ya lo declaró) y registra directamente el `ContractAdjustment` en `applied`, con `due_period = current_amount_since`, `previous_amount = initial_amount`, `new_amount = current_amount`, `pct_applied = NULL` y `notes` prefijado `"Carga inicial:"`. Este ajuste queda como el ancla del paso 1 (`get_last_applied_adjustment_due_period`) para el próximo ajuste periódico ARS — el paso 1 no cambia su lógica, solo encuentra un `applied` más reciente.

### RF-05 — Alertas de vencimiento

- El job diario `detect_expiring_contracts` notifica (in-app + email) los contratos que vencen dentro de `contract_expiry_notice_days` (default 60, configurable — Módulo 7). Una sola notificación por contrato y umbral.
- El listado soporta el filtro `expiring_in_days` para la vista "qué vence pronto" (UC-06).

### RF-06 — Serie mensual de valores locativos (issue #106)

Feedback #2 del PO (2026-08-28): la ficha del contrato (`GET /contracts/:id`) debe mostrar el historial de valores mes a mes — el actual primero, hacia atrás — para que el operador vea de un vistazo cómo evolucionó el alquiler sin ir a buscar el historial de ajustes por separado. **Derivado enteramente en el backend** (el front no calcula lógica de negocio, `CLAUDE.md` §6).

- El detalle del contrato agrega `monthly_amounts[]`: un item `{ period, amount }` por cada mes calendario, desde `start_date` hasta el mes de corte (ver abajo), en **orden descendente** (mes más reciente primero).
- **Mes de corte:** el mes actual para un contrato vigente (`draft`/`active`); `end_date` si venció naturalmente (`expired`); la **fecha de terminación efectiva** si fue terminado anticipadamente (`terminated`) — ver RN-09 para la derivación (no hay columna `terminated_at` en `contracts`).
- **Monto de cada mes:** determinístico — `initial_amount` hasta el primer ajuste `applied` cuyo `due_period` cae en o antes de ese mes; a partir de ahí, el `new_amount` del ajuste `applied` más reciente cuyo `due_period` cae en o antes de ese mes. Incluye el ajuste sintético "Carga inicial" del issue #100 (RF-04 paso 6) igual que cualquier otro `applied`. Los ajustes `pending` **no** cuentan.
- Un contrato USD sin carga inicial declarada tiene una serie plana en `initial_amount` (RN-03/RN-C02: sin ajuste periódico automático). Un contrato cuyo `start_date` todavía no llegó devuelve `monthly_amounts: []`.

## Reglas de Negocio (del módulo)

- **RN-01:** Una propiedad no puede tener dos contratos `active` con vigencias superpuestas (= RN-C01; constraint EXCLUDE en DB + validación app-level con mensaje claro).
- **RN-02:** Todo contrato nace `draft` y solo genera efectos (períodos, estado de propiedad) al activarse.
- **RN-03:** Un contrato USD no tiene frecuencia ni índice de ajuste (= RN-C02; CHECK en DB).
- **RN-04:** El monto vigente solo cambia mediante un ajuste registrado (= RN-C04).
- **RN-05:** `daily_late_fee_pct` es obligatorio y ≥ 0 desde el alta (sin él no se puede sugerir mora).
- **RN-06:** Propiedad e inquilino referenciados deben existir, no estar borrados y pertenecer a la organización (cross-tenant = 404, RN-D01).
- **RN-07:** Un contrato `expired`/`terminated` no genera nuevos períodos; sus deudas siguen cobrables (= RN-C05).
- **RN-08** (issue #100, = RN-C06): Alta de contrato en curso — `current_amount` + `current_amount_since` son opcionales pero solo válidos juntos; si vienen, `current_amount` reemplaza a `initial_amount` como monto de arranque del contrato y el sistema registra un ajuste sintético `applied` trazable (ver RF-02, RF-04 paso 6). Aplica a ARS y USD por igual — RN-03/RN-C02 solo excluye a USD del ajuste periódico automático por índice, no de esta declaración puntual.
- **RN-09** (issue #106): Serie mensual de valores locativos (RF-06) — cálculo determinístico desde `initial_amount` + ajustes `applied` (solo `applied`; `pending` no cuenta), orden descendente. Como `contracts` no persiste una fecha propia de terminación anticipada (RF-03 solo audita el motivo, no agrega columna), la fecha de corte de un contrato `terminated` se deriva del evento `contract.terminated` más reciente de ese contrato en `audit_logs` (misma transacción que la transición de estado — decisión de implementación, issue #106); si no existiera (defensivo), el fallback es `end_date`. Un contrato `expired` usa directamente `end_date` (vencimiento natural, sin ambigüedad).

## Validaciones

- `initial_amount` > 0; `end_date` > `start_date`; duración máxima razonable (≤ 10 años).
- `adjustment_frequency_months` entero > 0 (solo ARS); `adjustment_index` obligatorio si hay frecuencia; `adjustment_index_notes` obligatoria si el índice es `otro`.
- `pct` del ajuste: decimal, puede ser negativo (deflación/renegociación) — confirmación explícita en UI si < 0; tope de sanidad ±500%.
- `current_amount` > 0 (issue #100, RN-08); `current_amount_since` se normaliza al día 1 de su mes y debe ser `>= start_date` y `<= hoy` (`400 INVALID_DATE_RANGE`, `field: "current_amount_since"`); enviar solo uno de los dos campos es `400 VALIDATION_ERROR`.

## Criterios de Aceptación

- [ ] **CA-03-01:** Se crea un contrato ARS con % de mora, frecuencia de ajuste e índice de referencia; nace en `draft` y no genera períodos hasta activarse.
- [ ] **CA-03-02:** Crear o activar un contrato cuya vigencia se superpone con otro `active` de la misma propiedad devuelve `409 CONTRACT_OVERLAP` con el contrato en conflicto en `details`.
- [ ] **CA-03-03:** Crear un contrato USD con frecuencia o índice de ajuste devuelve `400 VALIDATION_ERROR` (RN-03).
- [ ] **CA-03-04:** Al llegar el mes de ajuste, el sistema crea el ajuste `pending`, notifica, y el contrato aparece en la bandeja; el rent_period de ese mes no existe todavía.
- [ ] **CA-03-05:** Al aplicar el ajuste con un %, el monto vigente se actualiza, el historial registra % / monto anterior / monto nuevo / autor, y el rent_period del mes se genera con el valor nuevo.
- [ ] **CA-03-06:** El monto vigente de un contrato activo no puede editarse por PATCH (`422 BUSINESS_RULE_VIOLATION` — RN-04); solo cambia vía ajuste.
- [ ] **CA-03-07:** Un contrato que vence dentro del umbral configurado genera la notificación de vencimiento una sola vez, y aparece en el filtro `expiring_in_days`.
- [ ] **CA-03-08:** Al terminar un contrato, la propiedad vuelve a `available` y sus períodos impagos siguen visibles en el estado de deuda.
- [ ] **CA-03-09** (issue #100): Al activar un contrato dado de alta en curso (con `current_amount`/`current_amount_since`), el período del mes actual nace con el monto vigente declarado, no con `initial_amount`.
- [ ] **CA-03-10** (issue #100): El próximo ajuste por índice de un contrato ARS dado de alta en curso se detecta contando desde `current_amount_since` (no desde `start_date`).
- [ ] **CA-03-11** (issue #100): El historial de un contrato dado de alta en curso (`GET /contracts/:id/adjustments`) muestra el ajuste sintético `applied` con `previous_amount = initial_amount`, `new_amount = current_amount`, `pct_applied` nulo y `notes` con el prefijo `"Carga inicial:"`.
- [ ] **CA-03-12** (issue #100): Un alta normal (sin `current_amount`/`current_amount_since`) se comporta idéntico a antes del issue #100: `current_amount = initial_amount`, sin ajuste sintético.
- [ ] **CA-03-13** (issue #100): Un contrato USD también puede darse de alta en curso con `current_amount`/`current_amount_since` — el ajuste sintético se registra igual, sin habilitar el ajuste periódico automático (RN-03/RN-C02 sigue vigente).
- [ ] **CA-03-14** (issue #100): `current_amount_since` anterior a `start_date` (ya normalizado a día 1) o posterior a hoy devuelve `400 INVALID_DATE_RANGE`.
- [ ] **CA-03-15** (issue #100): Enviar `current_amount` sin `current_amount_since` (o viceversa) devuelve `400 VALIDATION_ERROR`.
- [ ] **CA-03-16** (issue #106): `GET /contracts/:id` de un contrato sin ajustes devuelve `monthly_amounts[]` con `initial_amount` en todos los meses desde `start_date` hasta el mes actual, orden descendente.
- [ ] **CA-03-17** (issue #106): `GET /contracts/:id` de un contrato con 2 ajustes `applied` devuelve 3 tramos de monto (inicial + 2 ajustes), cada uno vigente desde su `due_period`.
- [ ] **CA-03-18** (issue #106): `GET /contracts/:id` de un contrato con carga inicial retroactiva (issue #100) incluye el ajuste sintético en el cálculo — los meses anteriores a `current_amount_since` muestran `initial_amount`, los posteriores muestran `current_amount`.
- [ ] **CA-03-19** (issue #106): `GET /contracts/:id` de un contrato `terminated` corta la serie en el mes de la terminación efectiva (evento `contract.terminated` de `audit_logs`), no en `end_date`.
- [ ] **CA-03-20** (issue #106): `GET /contracts/:id` de un contrato cuyo `start_date` cae en el mes actual devuelve `monthly_amounts` con exactamente 1 elemento.
- [ ] **CA-03-21** (issue #106): `monthly_amounts[]` viene siempre en orden estrictamente descendente por `period`.
- [ ] **CA-03-22** (issue #106): `GET /contracts/:id` de un contrato USD sin carga inicial devuelve una serie plana en `initial_amount` (RN-03/RN-C02, sin ajuste periódico automático).

## Integraciones

| Módulo | Motivo |
|---|---|
| Módulo 1 (Propiedades) | Estado `rented`/`available` derivado |
| Módulo 2 (Personas) | Inquilino del contrato; propietario vía propiedad |
| Módulo 4 (Cobranzas) | Los rent_periods nacen del contrato y su monto vigente |
| Notificaciones | `adjustment_pending`, `contract_expiring` |
| Log de Auditoría | Ajustes aplicados, terminaciones, extensiones de fecha |
