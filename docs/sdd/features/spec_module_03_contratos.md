---
name: AdminProp — Módulo 3 — Contratos de Locación
description: Contratos propiedad+inquilino con condiciones pactadas, ciclo de vida, ajustes por índice con ingreso manual del % y alertas de vencimiento
type: project
version: 1.4
fecha: 2026-08-29
---
# Módulo 3 — Contratos de Locación

**Versión:** 1.4 · **Estado:** Borrador para revisión · **Fecha:** 2026-08-29

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
- **Alta de contrato en curso (RN-08/RN-C06 v2, issue #107 — supersede parcialmente el issue #100):** para migrar contratos que ya vienen corriendo con aumentos ya ocurridos por fuera del sistema (ej: contrato firmado hace 8 meses), el mecanismo depende de si el contrato configura `adjustment_frequency_months`:
  - **Con `adjustment_frequency_months` (solo ARS):** campo opcional `historical_amounts[]` — lista ORDENADA de montos, uno por cada tramo transcurrido desde `start_date` (tramo = ventana de `adjustment_frequency_months` meses; el backend deriva las fechas, nunca el cliente). Cantidad esperada = tramos transcurridos hasta hoy, calculada por el backend; cantidad incorrecta es `400 VALIDATION_ERROR` con mensaje explícito. `current_amount`/`current_amount_since` **no se aceptan** en este caso (superados por `historical_amounts[]`).
  - **Sin `adjustment_frequency_months` (USD siempre; ARS sin ajuste):** se mantiene sin cambios el mecanismo del issue #100 — `current_amount` + `current_amount_since`, solo válidos **juntos** (`400 VALIDATION_ERROR` si viene uno sin el otro). `historical_amounts[]` **no se acepta** en este caso.
  - En ambos casos, si vienen, el contrato nace con `current_amount` en el monto vigente declarado (no en `initial_amount`, que queda como el monto histórico informativo del tramo/período inicial) y el sistema registra uno o más ajustes `applied` sintéticos de "carga inicial" (ver RF-04 y `sdd_02` §2.8) — sin tocar el flujo normal de ajustes manuales.

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
6. **Ajuste(s) sintético(s) de carga inicial (RN-08/RN-C06 v2, issue #107):** al declarar la carga inicial en el alta (RF-02), el sistema salta los pasos 1-2 (no hay detección ni aviso: el operador ya lo declaró) y registra directamente el/los `ContractAdjustment` en `applied`, con `pct_applied = NULL` y `notes` prefijado `"Carga inicial:"`:
   - Con `historical_amounts[]` (contratos con `adjustment_frequency_months`): una **cadena** de ajustes, uno por cada tramo transcurrido a partir del segundo — `due_period` = inicio de ese tramo, `previous_amount`/`new_amount` encadenados con el valor del tramo anterior/siguiente de la lista.
   - Con `current_amount`/`current_amount_since` (contratos sin `adjustment_frequency_months`): un único ajuste, con `due_period = current_amount_since`, `previous_amount = initial_amount`, `new_amount = current_amount` (comportamiento del issue #100, sin cambios).

   El ÚLTIMO ajuste sintético queda como el ancla del paso 1 (`get_last_applied_adjustment_due_period`) para el próximo ajuste periódico ARS — el paso 1 no cambia su lógica, solo encuentra un `applied` más reciente.

7. **Historial expone nombre y % efectivo (issue #118):** feedback #3 del PO (2026-08-29) — el item de ajuste devuelto por `GET /contracts/:id/adjustments`, `GET /adjustments` y `POST /adjustments/:id/apply` agrega `applied_by_name` (`full_name` de `users` resuelto desde `applied_by`; `null` mientras el ajuste sigue `pending`) y `pct_effective` (recalculado en el backend con `Decimal`/`ROUND_HALF_EVEN`; `null` si el ajuste no está `applied` o si `previous_amount = 0` — ver RN-10). Resuelve dos huecos de UI: el "Aplicado por" mostraba el UUID crudo, y la columna % quedaba vacía en los ajustes de carga inicial (`pct_applied = NULL`).

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
- **RN-08** (v2, issue #107, = RN-C06 — supersede parcialmente el issue #100): Alta de contrato en curso. Con `adjustment_frequency_months` configurado (solo ARS): `historical_amounts[]` — uno por tramo transcurrido, cantidad exacta calculada por el backend; `current_amount` termina en el último valor de la lista y el sistema registra una cadena de ajustes sintéticos `applied` trazables (ver RF-02, RF-04 paso 6). Sin `adjustment_frequency_months` (USD siempre; ARS sin ajuste): `current_amount` + `current_amount_since`, opcionales pero solo válidos juntos (comportamiento del issue #100, sin cambios) — reemplaza a `initial_amount` como monto de arranque y registra un único ajuste sintético `applied`. Los dos mecanismos son mutuamente excluyentes según `adjustment_frequency_months`; RN-03/RN-C02 solo excluye a USD del ajuste periódico automático por índice, no de esta declaración puntual de carga inicial.
- **RN-09** (issue #106): Serie mensual de valores locativos (RF-06) — cálculo determinístico desde `initial_amount` + ajustes `applied` (solo `applied`; `pending` no cuenta), orden descendente. Como `contracts` no persiste una fecha propia de terminación anticipada (RF-03 solo audita el motivo, no agrega columna), la fecha de corte de un contrato `terminated` se deriva del evento `contract.terminated` más reciente de ese contrato en `audit_logs` (misma transacción que la transición de estado — decisión de implementación, issue #106); si no existiera (defensivo), el fallback es `end_date`. Un contrato `expired` usa directamente `end_date` (vencimiento natural, sin ambigüedad).
- **RN-10** (issue #118): `pct_effective` de un ajuste `applied` = `((new_amount − previous_amount) / previous_amount) × 100`, redondeado a 2 decimales con `ROUND_HALF_EVEN` (banker's rounding), siempre en `Decimal` — nunca `float`. Es la única fuente confiable del % para el ajuste sintético de carga inicial (`pct_applied` es `NULL` ahí, issues #100/#107); para los ajustes manuales normalmente coincide con `pct_applied` (que ya usa `ROUND_HALF_UP` al calcular `new_amount` en `POST /adjustments/:id/apply`), pero `pct_effective` es el valor recalculado y expuesto de forma uniforme en todos los casos. Ajustes `pending` → `null` (no hay `new_amount` todavía). `previous_amount = 0` → `null` (evita división por cero — defensivo, no debería ocurrir en la práctica dado RN-01, `initial_amount > 0`).

## Validaciones

- `initial_amount` > 0; `end_date` > `start_date`; duración máxima razonable (≤ 10 años).
- `adjustment_frequency_months` entero > 0 (solo ARS); `adjustment_index` obligatorio si hay frecuencia; `adjustment_index_notes` obligatoria si el índice es `otro`.
- `pct` del ajuste: decimal, puede ser negativo (deflación/renegociación) — confirmación explícita en UI si < 0; tope de sanidad ±500%.
- Sin `adjustment_frequency_months` (RN-08, comportamiento #100): `current_amount` > 0; `current_amount_since` se normaliza al día 1 de su mes y debe ser `>= start_date` y `<= hoy` (`400 INVALID_DATE_RANGE`, `field: "current_amount_since"`); enviar solo uno de los dos campos es `400 VALIDATION_ERROR`; enviar `historical_amounts` en este caso es `400 VALIDATION_ERROR`.
- Con `adjustment_frequency_months` (RN-08 v2, issue #107): `historical_amounts[]`, cada elemento > 0; cantidad = tramos transcurridos calculados por el backend (cantidad distinta → `400 VALIDATION_ERROR` con el detalle de cuántos espera); `historical_amounts[0]` debe ser igual a `initial_amount` (`400 VALIDATION_ERROR` si difiere); si el contrato recién arrancó (ningún tramo más allá del inicial) no corresponde enviarlo; enviar `current_amount`/`current_amount_since` en este caso es `400 VALIDATION_ERROR`.

## Criterios de Aceptación

- [ ] **CA-03-01:** Se crea un contrato ARS con % de mora, frecuencia de ajuste e índice de referencia; nace en `draft` y no genera períodos hasta activarse.
- [ ] **CA-03-02:** Crear o activar un contrato cuya vigencia se superpone con otro `active` de la misma propiedad devuelve `409 CONTRACT_OVERLAP` con el contrato en conflicto en `details`.
- [ ] **CA-03-03:** Crear un contrato USD con frecuencia o índice de ajuste devuelve `400 VALIDATION_ERROR` (RN-03).
- [ ] **CA-03-04:** Al llegar el mes de ajuste, el sistema crea el ajuste `pending`, notifica, y el contrato aparece en la bandeja; el rent_period de ese mes no existe todavía.
- [ ] **CA-03-05:** Al aplicar el ajuste con un %, el monto vigente se actualiza, el historial registra % / monto anterior / monto nuevo / autor, y el rent_period del mes se genera con el valor nuevo.
- [ ] **CA-03-06:** El monto vigente de un contrato activo no puede editarse por PATCH (`422 BUSINESS_RULE_VIOLATION` — RN-04); solo cambia vía ajuste.
- [ ] **CA-03-07:** Un contrato que vence dentro del umbral configurado genera la notificación de vencimiento una sola vez, y aparece en el filtro `expiring_in_days`.
- [ ] **CA-03-08:** Al terminar un contrato, la propiedad vuelve a `available` y sus períodos impagos siguen visibles en el estado de deuda.
- [ ] **CA-03-09** (v2, issue #107): Alta de un contrato ARS con `adjustment_frequency_months` y `historical_amounts[]` con exactamente 1 tramo transcurrido más allá del inicial (2 elementos totales: original + 1 aumento) — el sistema crea 1 ajuste sintético `applied` (`due_period` = inicio del segundo tramo, `previous_amount` = el primer valor, `new_amount` = el segundo) y `current_amount` queda en el último valor; al activar, el período del mes actual nace con ese monto.
- [ ] **CA-03-10** (v2, issue #107): Alta de un contrato ARS con 2 tramos transcurridos más allá del inicial (3 elementos, ejemplo del PO: original + 2 aumentos, frecuencia cada 4 meses, 10 meses corridos) — el sistema crea 2 ajustes sintéticos `applied` encadenados (cada uno con su `due_period` = inicio de su tramo), visibles en orden en `GET /contracts/:id/adjustments`, y `current_amount` queda en el último valor; el próximo ajuste real se detecta anclado en el `due_period` del último sintético (sin cambios en `detect_due_adjustments`).
- [ ] **CA-03-11** (v2, issue #107): Alta de un contrato ARS con `adjustment_frequency_months` cuyo `start_date` está dentro del tramo actual (0 tramos transcurridos más allá del inicial) y se envía `historical_amounts` de todos modos → `400 VALIDATION_ERROR` (no corresponde declararlo — equivale a un alta normal).
- [ ] **CA-03-12** (v2, issue #107): Enviar `historical_amounts[]` con una cantidad de elementos distinta a la esperada (calculada por el backend desde `start_date` + `adjustment_frequency_months` + hoy) devuelve `400 VALIDATION_ERROR` con un mensaje que indica cuántos valores espera el sistema y el rango de fechas de cada tramo.
- [ ] **CA-03-13** (v2, issue #107): Enviar `historical_amounts[0]` distinto de `initial_amount` devuelve `400 VALIDATION_ERROR`.
- [ ] **CA-03-14** (v2, issue #107): Un contrato sin `adjustment_frequency_months` configurado (USD, o ARS sin ajuste) mantiene el mecanismo de un único valor vigente `current_amount`/`current_amount_since` (comportamiento del issue #100, sin cambios: opcionales pero solo válidos juntos, `current_amount_since` `>= start_date` y `<= hoy` con `400 INVALID_DATE_RANGE` si no, ajuste sintético único trazable en el historial); enviar `historical_amounts[]` en este caso devuelve `400 VALIDATION_ERROR`.
- [ ] **CA-03-15** (v2, issue #107): Un contrato ARS con `adjustment_frequency_months` configurado que envía `current_amount`/`current_amount_since` (en vez de `historical_amounts[]`) devuelve `400 VALIDATION_ERROR` — ese mecanismo quedó superado por `historical_amounts[]` para contratos con ajuste periódico. Un alta normal (sin ninguno de los dos mecanismos) sigue comportándose igual: `current_amount = initial_amount`, sin ajuste sintético.
- [ ] **CA-03-16** (issue #106): `GET /contracts/:id` de un contrato sin ajustes devuelve `monthly_amounts[]` con `initial_amount` en todos los meses desde `start_date` hasta el mes actual, orden descendente.
- [ ] **CA-03-17** (issue #106): `GET /contracts/:id` de un contrato con 2 ajustes `applied` devuelve 3 tramos de monto (inicial + 2 ajustes), cada uno vigente desde su `due_period`.
- [ ] **CA-03-18** (issue #106): `GET /contracts/:id` de un contrato con carga inicial retroactiva (issue #100) incluye el ajuste sintético en el cálculo — los meses anteriores a `current_amount_since` muestran `initial_amount`, los posteriores muestran `current_amount`.
- [ ] **CA-03-19** (issue #106): `GET /contracts/:id` de un contrato `terminated` corta la serie en el mes de la terminación efectiva (evento `contract.terminated` de `audit_logs`), no en `end_date`.
- [ ] **CA-03-20** (issue #106): `GET /contracts/:id` de un contrato cuyo `start_date` cae en el mes actual devuelve `monthly_amounts` con exactamente 1 elemento.
- [ ] **CA-03-21** (issue #106): `monthly_amounts[]` viene siempre en orden estrictamente descendente por `period`.
- [ ] **CA-03-22** (issue #106): `GET /contracts/:id` de un contrato USD sin carga inicial devuelve una serie plana en `initial_amount` (RN-03/RN-C02, sin ajuste periódico automático).
- [ ] **CA-03-23** (issue #118): un ajuste `applied` expone `applied_by_name` con el `full_name` del usuario que lo aplicó (resuelto desde `users` por `applied_by` — no expone solo el UUID).
- [ ] **CA-03-24** (issue #118): el ajuste sintético de carga inicial (`pct_applied = NULL`, issues #100/#107) expone `pct_effective` calculado — ejemplo: `previous_amount = 1.000.000`, `new_amount = 1.200.000` → `pct_effective = 20.00`.
- [ ] **CA-03-25** (issue #118): un ajuste manual aplicado con un `pct` dado expone `pct_effective` que coincide con el `pct_applied` guardado (dentro del redondeo `ROUND_HALF_EVEN` a 2 decimales, RN-10).
- [ ] **CA-03-26** (issue #118): un ajuste `pending` (sin aplicar) expone `applied_by_name: null` y `pct_effective: null`.

## Integraciones

| Módulo | Motivo |
|---|---|
| Módulo 1 (Propiedades) | Estado `rented`/`available` derivado |
| Módulo 2 (Personas) | Inquilino del contrato; propietario vía propiedad |
| Módulo 4 (Cobranzas) | Los rent_periods nacen del contrato y su monto vigente |
| Notificaciones | `adjustment_pending`, `contract_expiring` |
| Log de Auditoría | Ajustes aplicados, terminaciones, extensiones de fecha |
