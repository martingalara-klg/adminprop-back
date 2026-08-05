---
name: AdminProp — Modelo de Dominio
description: Entidades del dominio de gestión de alquileres, invariantes (RN-C, RN-P, RN-L, RN-A, RN-D), relaciones y glosario unificado
type: project
version: 1.0
fecha: 2026-08-05
---
# AdminProp — Modelo de Dominio

**Versión:** 1.0
**Estado:** Borrador para revisión
**Fecha:** 2026-08-05

---

## 1. Mapa Conceptual del Dominio

```
[ Organización (administradora) ]  ← tenant; todo lo de abajo vive dentro de una org
        │
        ├── [ Usuarios ] (owner / admin / maintenance)
        │
        ├── [ Propietario (Landlord) ] ──┐  % comisión propio
        │         │                      │
        │         ▼                      │
        ├── [ Propiedad ] ── [ Cuentas de Servicio ] (informativas)
        │         │
        │         ├── [ Concepto Recurrente ] ── [ Cargo del Mes ] (rentas, muni — importe manual)
        │         │
        │         ├── [ Pedido de Reparación ] ── [ Cotizaciones ] ── [ Adjuntos/Fotos ]
        │         │         (paga: Dueño | Administración)
        │         │
        │         └── [ Contrato ] ── [ Inquilino (Renter) ]
        │                   │
        │                   ├── [ Ajuste de Contrato ] (historial; % manual)
        │                   │
        │                   └── [ Período de Alquiler ] (generado el 1° de cada mes)
        │                             │
        │                             └── [ Cobro ] (medio, moneda, TC, destino, interés)
        │
        └── [ Liquidación ] ← consolida por propietario y período:
                  cobros − comisión − cargos − reparaciones (paga administración) − ya rendido
```

---

## 2. Entidades del Dominio

### 2.1 Organización (Organization)

La administradora de propiedades. Es el tenant: todos los datos operativos viven dentro de una organización.

| Atributo | Tipo | Descripción |
|---|---|---|
| id | UUID | Identificador único |
| name | texto | Nombre de la administradora |
| slug | texto | Identificador corto único (para URLs y buckets futuros) |
| status | enum | `pending_owner` \| `active` \| `disabled` |
| settings | JSON | Configuración: `grace_day` (default 10), `contract_expiry_notice_days` (default 60), datos de encabezado para liquidaciones |

**Invariantes:**
- Una organización `disabled` no puede operar (ningún usuario puede autenticarse en ella).
- `slug` es único global.
- Solo el Super Admin crea organizaciones (no hay auto-registro).

---

### 2.2 Usuarios, Roles y Membresías (User, Role, OrganizationMember, OrganizationInvitation)

Los usuarios del sistema son el equipo de la administradora. Los roles son data-driven con permisos atómicos (`permissions[]` en el JWT).

| Entidad | Atributos clave | Notas |
|---|---|---|
| **User** | id, email (único global), password_hash, is_super_admin | Global (fuera del tenant); un user puede pertenecer a varias orgs |
| **Role** | id, name, permissions (JSON array), is_system_role | Roles de sistema sembrados por org: `owner`, `admin`, `maintenance` — inmutables |
| **OrganizationMember** | user_id, organization_id, role_id, status (`active` \| `inactive`) | La membresía une user + org + rol |
| **OrganizationInvitation** | email, organization_id, role_id, token, expires_at, status | Toda alta de usuario nace de invitación; expira a las 72h |

**Invariantes:**
- Siempre debe existir al menos un `owner` activo por organización (`LAST_OWNER_REQUIRED`).
- Los roles de sistema (`is_system_role=true`) no se editan ni se eliminan.
- Un usuario con rol `maintenance` solo accede al módulo de mantenimiento (ver RN-A01).

---

### 2.3 Propietario (Landlord)

El dueño de una o más propiedades, a quien la administradora le rinde. Es un registro, no un usuario (sin login en MVP).

| Atributo | Tipo | Descripción |
|---|---|---|
| id | UUID | Identificador único |
| name | texto | Nombre / razón social |
| tax_id | texto | CUIT / DNI |
| phone / email | texto | Contacto |
| bank_info | texto | Datos bancarios para transferencias |
| commission_pct | decimal | **% de comisión por administración** — aplica a todos sus contratos |
| notes | texto | Observaciones |

**Invariantes:**
- `commission_pct` ≥ 0 y ≤ 100.
- Un cambio de `commission_pct` rige solo para liquidaciones futuras (ver RN-L05).
- Un propietario con propiedades activas no se elimina; se desactiva (soft delete).

---

### 2.4 Inquilino (Renter)

Quien alquila una propiedad mediante un contrato. Es un registro, no un usuario (sin login en MVP).

| Atributo | Tipo | Descripción |
|---|---|---|
| id | UUID | Identificador único |
| name | texto | Nombre completo |
| tax_id | texto | DNI / CUIT |
| phone / email | texto | Contacto |
| notes | texto | Observaciones (garantes, referencias, etc. como texto libre en MVP) |

**Invariantes:**
- Un inquilino con contratos vigentes no se elimina; se desactiva (soft delete).

---

### 2.5 Propiedad (Property)

El inmueble administrado. Pertenece a un propietario.

| Atributo | Tipo | Descripción |
|---|---|---|
| id | UUID | Identificador único |
| landlord_id | UUID | FK a Propietario — obligatorio |
| address | texto | Dirección completa |
| property_type | texto | Departamento, casa, local, cochera, otro (catálogo simple) |
| status | enum | `available` \| `rented` \| `unavailable` |
| notes | texto | Observaciones |

**Invariantes:**
- `status = rented` ⟺ existe un contrato `active` sobre la propiedad.
- Una propiedad con historial (contratos, cobros, reparaciones) no se elimina físicamente; soft delete.

---

### 2.6 Cuenta de Servicio (PropertyServiceAccount)

Registro **informativo** de los números de cuenta de servicios e impuestos de una propiedad, para las verificaciones mensuales. No trackea montos ni pagos.

| Atributo | Tipo | Descripción |
|---|---|---|
| id | UUID | Identificador único |
| property_id | UUID | FK a Propiedad |
| service_type | enum | `rentas` \| `municipalidad` \| `luz` \| `gas` \| `agua` \| `expensas` \| `otro` |
| account_number | texto | N° de cuenta / cliente |
| secondary_number | texto | N° adicional (ej: luz tiene n° de cliente y n° de contrato) |
| notes | texto | Observaciones |

**Invariantes:**
- Puramente informativa: ninguna lógica de negocio depende de estas cuentas.

---

### 2.7 Contrato (Contract)

El contrato de locación: vincula propiedad + inquilino con las condiciones pactadas.

| Atributo | Tipo | Descripción |
|---|---|---|
| id | UUID | Identificador único |
| property_id | UUID | FK a Propiedad |
| renter_id | UUID | FK a Inquilino |
| currency | enum | `ARS` \| `USD` |
| initial_amount | decimal | Monto mensual al inicio del contrato |
| current_amount | decimal | Monto mensual **vigente** (se modifica solo vía Ajuste) |
| start_date / end_date | fecha | Vigencia del contrato |
| daily_late_fee_pct | decimal | **% de mora diaria** (propio de cada contrato) |
| adjustment_frequency_months | entero | Cada cuántos meses ajusta — solo contratos ARS |
| adjustment_index | enum | `icl` \| `ipc_cordoba` \| `otro` — **informativo**; solo contratos ARS |
| adjustment_index_notes | texto | Detalle si el índice es `otro` |
| status | enum | `draft` \| `active` \| `expired` \| `terminated` |

**Ciclo de vida del estado:**
- `draft` → `active`: el contrato entra en vigencia (a partir de `start_date`).
- `active` → `expired`: se cumplió `end_date` sin renovación.
- `active` → `terminated`: rescisión anticipada.

**Invariantes:**
- Una propiedad no puede tener dos contratos `active` con vigencias superpuestas (ver RN-C01).
- Contratos `USD` no tienen configuración de ajuste (`adjustment_frequency_months` y `adjustment_index` nulos) (ver RN-C02).
- `current_amount` nunca se edita directamente: solo cambia al aplicar un Ajuste (ver RN-C04).
- Un contrato `expired` o `terminated` no genera nuevos Períodos de Alquiler (ver RN-C05); sus deudas pendientes siguen cobrables.
- `daily_late_fee_pct` ≥ 0.

---

### 2.8 Ajuste de Contrato (ContractAdjustment)

La actualización del monto de un contrato ARS. El sistema detecta cuándo toca (según la frecuencia), lo genera como pendiente y notifica; el operador ingresa el % calculado por fuera.

| Atributo | Tipo | Descripción |
|---|---|---|
| id | UUID | Identificador único |
| contract_id | UUID | FK a Contrato |
| due_period | año-mes | Período al que aplica el ajuste (ej: 2026-09) |
| status | enum | `pending` \| `applied` |
| pct_applied | decimal | % ingresado manualmente por el operador (null mientras `pending`) |
| previous_amount | decimal | Monto vigente antes del ajuste |
| new_amount | decimal | Monto resultante = previous × (1 + pct/100) |
| applied_by / applied_at | UUID / timestamp | Quién y cuándo lo aplicó |

**Invariantes:**
- El sistema genera el ajuste `pending` al llegar el período que corresponde según `adjustment_frequency_months`; nunca aplica un % automáticamente (ver RN-C03).
- Un ajuste `applied` es inmutable; un error se corrige con un nuevo ajuste correctivo (con nota).
- No puede haber dos ajustes `pending` del mismo contrato.
- Mientras exista un ajuste `pending`, el contrato se muestra como "ajuste pendiente" y su Período de Alquiler del mes de ajuste no se genera hasta aplicar el % (ver RN-P01).

---

### 2.9 Período de Alquiler (RentPeriod)

El alquiler de un mes de un contrato: la unidad que se cobra. Se genera automáticamente el 1° de cada mes.

| Atributo | Tipo | Descripción |
|---|---|---|
| id | UUID | Identificador único |
| contract_id | UUID | FK a Contrato |
| period | año-mes | Período (ej: 2026-08) |
| amount_due | decimal | Monto del mes (el `current_amount` del contrato al generarse) |
| currency | enum | Hereda la moneda del contrato |
| status | enum | `pending` \| `partial` \| `paid` |
| paid_total | decimal | Suma imputada hasta el momento (capital, sin intereses) |

**Estado "en mora" (derivado):** un período `pending` o `partial` cuyo día de gracia ya pasó está en mora; no es un estado persistido sino calculado (fecha actual vs. `grace_day` de la org).

**Invariantes:**
- Único por `(contract_id, period)` (ver RN-P01).
- `amount_due` refleja el monto vigente del contrato al momento de generarse (post-ajuste si el ajuste del período fue aplicado).
- `paid_total` ≤ `amount_due`; cuando `paid_total = amount_due` el estado pasa a `paid`.
- El saldo impago (`amount_due − paid_total`) es la base del cálculo de mora (ver RN-P03).

---

### 2.10 Cobro (Payment)

La imputación de un pago de un inquilino contra un Período de Alquiler.

| Atributo | Tipo | Descripción |
|---|---|---|
| id | UUID | Identificador único |
| rent_period_id | UUID | FK a Período de Alquiler |
| payment_date | fecha | Fecha del pago |
| method | enum | `cash` \| `transfer` |
| payment_currency | enum | `ARS` \| `USD` — moneda en la que efectivamente pagó |
| amount | decimal | Importe imputado a capital (en la moneda del contrato) |
| exchange_rate | decimal | Tipo de cambio ingresado **libremente** — obligatorio si `payment_currency` ≠ moneda del contrato |
| destination | enum | `agency_account` \| `landlord_account` — el segundo es "dinero ya rendido" |
| suggested_interest | decimal | Interés calculado por el sistema al momento de imputar |
| charged_interest | decimal | Interés efectivamente cobrado (decisión del operador) |
| forgiven_interest | decimal | Diferencia perdonada = suggested − charged |
| days_late | entero | Días de mora al momento del pago |
| notes | texto | Observaciones |
| voided_at / voided_by | timestamp / UUID | Anulación lógica (si el cobro se registró mal) |

**Invariantes:**
- `exchange_rate` obligatorio y > 0 cuando la moneda del pago difiere de la del contrato (ver RN-P06).
- `charged_interest` la decide el operador: el sistema **sugiere**, el operador imputa (ver RN-P04). Sugerido, cobrado y perdonado quedan siempre registrados.
- Un cobro con `destination = landlord_account` cuenta como "dinero ya rendido" en la liquidación (ver RN-P07).
- Un cobro anulado (`voided_at` no nulo) no suma a `paid_total` ni a liquidaciones; la anulación queda en el log de auditoría (ver RN-D04).

---

### 2.11 Concepto Recurrente (RecurringCharge) y Cargo del Mes (ChargeEntry)

Los gastos que se repiten todos los meses por propiedad (impuestos: rentas, municipalidad) cuyo importe varía y se carga a mano, y que se descuentan en la liquidación del propietario.

| Entidad | Atributos clave | Notas |
|---|---|---|
| **RecurringCharge** | id, property_id, charge_type (`rentas` \| `municipalidad` \| `otro`), label, is_active | El concepto: "esta propiedad paga rentas y muni todos los meses" |
| **ChargeEntry** | id, recurring_charge_id, period (año-mes), amount, notes | El importe concreto de ese concepto en ese mes, ingresado a mano |

**Invariantes:**
- Único `ChargeEntry` por `(recurring_charge_id, period)`.
- Un `ChargeEntry` entra como descuento en la liquidación del propietario del período correspondiente (ver RN-L01).
- Los importes son siempre en ARS en MVP.

---

### 2.12 Pedido de Reparación (WorkOrder)

Un arreglo sobre una propiedad, con su ciclo de cotización, aprobación y ejecución.

| Atributo | Tipo | Descripción |
|---|---|---|
| id | UUID | Identificador único |
| property_id | UUID | FK a Propiedad |
| title / description | texto | Qué hay que arreglar |
| payer | enum | `landlord` \| `agency` — **"Paga: Dueño / Administración"** |
| status | enum | `open` \| `in_progress` \| `closed` \| `cancelled` |
| approved_quote_id | UUID | FK a la Cotización aprobada (null hasta aprobar) |
| final_cost | decimal | Costo final (de la cotización aprobada; ajustable al cierre) |
| settled_in_settlement_id | UUID | FK a la Liquidación donde se descontó (solo si `payer = agency`) |
| created_by / closed_at | UUID / timestamp | Trazabilidad |

**Ciclo de vida del estado:**
- `open`: cargado por owner/admin; el encargado recibe notificación y puede subir cotizaciones.
- `open` → `in_progress`: owner/admin aprueba una cotización; el encargado recibe notificación.
- `in_progress` → `closed`: el encargado marca el trabajo terminado (con fotos opcionales); owner y admin reciben notificación.
- `open` / `in_progress` → `cancelled`: se desiste del arreglo.

**Invariantes:**
- Solo owner/admin crean pedidos y aprueban cotizaciones; solo el encargado (o admin) sube cotizaciones y marca terminado.
- Si `payer = agency` y el pedido está `closed`, el `final_cost` se descuenta en la **próxima** liquidación del propietario y queda registrado en cuál (ver RN-L04).
- Si `payer = landlord`, el pedido nunca aparece en liquidaciones; solo en el historial de la propiedad.
- Un pedido `closed` con liquidación asociada no puede volver a `in_progress`.

---

### 2.13 Cotización (WorkOrderQuote)

Presupuesto subido por el encargado de reparaciones para un pedido.

| Atributo | Tipo | Descripción |
|---|---|---|
| id | UUID | Identificador único |
| work_order_id | UUID | FK a Pedido de Reparación |
| amount | decimal | Monto cotizado |
| description | texto | Detalle del presupuesto |
| status | enum | `submitted` \| `approved` \| `discarded` |
| submitted_by | UUID | Usuario que la subió (rol maintenance o admin) |

**Invariantes:**
- Solo una cotización `approved` por pedido; al aprobar una, las demás pasan a `discarded`.
- Al subirse una cotización, owner y admin reciben notificación.

---

### 2.14 Adjunto (Attachment)

Archivo asociado a una entidad: fotos de pedidos de reparación, cotizaciones y cierres; exports de liquidaciones.

| Atributo | Tipo | Descripción |
|---|---|---|
| id | UUID | Identificador único |
| entity_type / entity_id | texto / UUID | A qué entidad pertenece (work_order, quote, settlement, …) |
| file_path | texto | Ubicación en storage (filesystem local en MVP) |
| file_name / mime_type / size | texto / texto / entero | Metadatos |
| uploaded_by | UUID | Usuario |

**Invariantes:**
- El acceso a un adjunto respeta los permisos de la entidad padre (un maintenance solo ve adjuntos de pedidos de reparación).

---

### 2.15 Liquidación (Settlement + SettlementLineItem)

La rendición mensual a un propietario: consolida todas sus propiedades en un período.

| Atributo | Tipo | Descripción |
|---|---|---|
| id | UUID | Identificador único |
| landlord_id | UUID | FK a Propietario |
| period | año-mes | Período liquidado |
| status | enum | `draft` \| `issued` |
| totals | (por moneda) | total_collected, commission_total, charges_total, repairs_total, already_settled_total, net_amount — **separados por moneda ARS / USD, sin conversión automática** |
| generated_by / issued_at | UUID / timestamp | Trazabilidad |
| regenerated_count | entero | Cuántas veces se corrigió/regeneró (cada regeneración queda en auditoría) |

**SettlementLineItem:** cada línea del detalle — `line_type` (`rent_collected` \| `commission` \| `tax_charge` \| `repair` \| `already_settled`), referencia a la entidad origen (cobro, cargo, pedido), propiedad, importe y moneda.

**Fórmula (por moneda):**
```
neto a rendir = Σ cobros del período (destino administración)
              − comisión (% del propietario × alquileres del período, incluidos los directos)
              − Σ cargos del mes (rentas, muni)                 [solo ARS]
              − Σ reparaciones cerradas con payer = agency      [pendientes de liquidar]
              (los cobros con destino cuenta-propietario se listan como "ya rendido",
               no suman al neto pero sí a la base de comisión)
```

**Invariantes:**
- Única por `(landlord_id, period)` (una corrección regenera la misma liquidación, no crea otra).
- Una liquidación `issued` puede corregirse y regenerarse; cada regeneración queda registrada en el log de auditoría con quién, cuándo y qué cambió (ver RN-L03).
- Los montos ARS y USD se liquidan por separado; el sistema no convierte monedas en la liquidación (MVP).
- Exportable en Excel y PDF; los archivos generados quedan como Adjuntos de la liquidación.

---

### 2.16 Notificación (Notification)

Aviso in-app + email a un usuario por un evento del sistema.

| Atributo | Tipo | Descripción |
|---|---|---|
| id | UUID | Identificador único |
| user_id | UUID | Destinatario |
| event_type | enum | `adjustment_pending` \| `contract_expiring` \| `quote_submitted` \| `work_order_created` \| `work_order_closed` |
| payload | JSON | Datos del evento (contrato, propiedad, pedido, etc.) |
| read_at | timestamp | Null = no leída |
| email_sent_at | timestamp | Cuándo salió el email (null si falló/pendiente) |

**Invariantes:**
- El evento se enruta según el rol: `work_order_created` → maintenance; `quote_submitted`, `work_order_closed`, `adjustment_pending`, `contract_expiring` → owner y admin.

---

### 2.17 Log de Auditoría (AuditLog)

Registro append-only de las operaciones sensibles.

| Atributo | Tipo | Descripción |
|---|---|---|
| id | UUID | Identificador único |
| user_id | UUID | Quién |
| action / entity_type / entity_id | texto | Qué y sobre qué |
| before_state / after_state | JSON | Valores anterior y nuevo |
| created_at | timestamp | Cuándo |

**Eventos auditados como mínimo:** correcciones y anulaciones de cobros, regeneraciones de liquidaciones, perdones de interés, ajustes aplicados, cambios de % de comisión, cambios de rol/usuario, intentos de acceso no autorizado.

**Invariantes:**
- Append-only: sin UPDATE ni DELETE (ver RN-D03).

---

## 3. Reglas de Negocio Críticas (Invariantes del Sistema)

### RN-C — Contratos

- **RN-C01:** Una propiedad no puede tener dos contratos `active` con vigencias superpuestas.
- **RN-C02:** Un contrato en USD no tiene configuración de ajuste: su monto no se actualiza durante la vigencia.
- **RN-C03:** Ningún ajuste se aplica automáticamente: el sistema genera el ajuste pendiente y notifica; el nuevo monto solo existe tras el ingreso **manual** del % por un operador. El índice del contrato es informativo.
- **RN-C04:** El monto vigente de un contrato (`current_amount`) solo cambia mediante un Ajuste registrado en el historial; nunca por edición directa.
- **RN-C05:** Un contrato `expired` o `terminated` no genera nuevos períodos de alquiler; sus deudas existentes siguen cobrables.

### RN-P — Pagos y Cobranzas

- **RN-P01:** El 1° de cada mes se genera exactamente un Período de Alquiler por contrato `active` y por período (único por contrato+período). Si el contrato tiene un ajuste pendiente para ese período, el período se genera recién al aplicarse el %.
- **RN-P02:** El alquiler está en término hasta el día de gracia de la organización inclusive (default: día 10); la mora corre desde el día siguiente (día 11 = 1 día de mora).
- **RN-P03:** Interés sugerido = saldo impago × % de mora diaria del contrato × días de mora. Los pagos parciales reducen el saldo base.
- **RN-P04:** La imputación es libre: el operador puede cobrar el interés sugerido, perdonarlo total o parcialmente. Sugerido, cobrado y perdonado quedan siempre registrados.
- **RN-P05:** Se aceptan pagos parciales; el saldo restante queda como deuda del inquilino.
- **RN-P06:** Si la moneda del pago difiere de la del contrato, el tipo de cambio se ingresa manualmente y es obligatorio.
- **RN-P07:** Un cobro con destino "cuenta del propietario" es dinero ya rendido: no suma al neto a rendir, pero sí a la base de cálculo de la comisión.

### RN-L — Liquidaciones

- **RN-L01:** Neto a rendir (por moneda) = cobros del período con destino administración − comisión − cargos del mes − reparaciones pagadas por la administración; el dinero ya rendido se lista informativamente.
- **RN-L02:** La comisión = % del propietario × alquileres del período de todas sus propiedades, **incluidos** los cobrados directo en su cuenta.
- **RN-L03:** Una liquidación emitida puede corregirse y regenerarse; cada corrección queda trazada en el log de auditoría (quién, cuándo, qué cambió). Nunca hay borrado físico.
- **RN-L04:** Una reparación entra a la liquidación solo si `payer = agency` y estado `closed`; se descuenta una única vez y queda registrado en qué liquidación.
- **RN-L05:** Un cambio en el % de comisión del propietario rige para liquidaciones futuras; nunca recalcula períodos ya liquidados.

### RN-A — Accesos

- **RN-A01:** El rol `maintenance` accede únicamente al módulo de mantenimiento (pedidos asignados, cotizaciones, adjuntos); nunca a contratos, cobranzas, liquidaciones ni datos de propietarios/inquilinos. Enforzado en API, no solo en UI.
- **RN-A02:** Solo el `owner` gestiona usuarios, roles y configuración de la organización.
- **RN-A03:** Siempre debe existir al menos un `owner` activo por organización (`LAST_OWNER_REQUIRED`).
- **RN-A04:** Todo intento de acceso no autorizado queda registrado en el log de auditoría.

### RN-D — Datos y Persistencia

- **RN-D01:** Los datos de un tenant nunca son accesibles desde otro. Todo acceso cross-tenant responde 404 (no 403).
- **RN-D02:** Toda eliminación es lógica (soft delete); no hay DELETE físico de datos operativos.
- **RN-D03:** El log de auditoría es append-only e inmutable.
- **RN-D04:** Las correcciones de cobros y liquidaciones siempre quedan trazadas en el log de auditoría con valor anterior y nuevo.

---

## 4. Relaciones entre Entidades

```
Organization 1─N OrganizationMember N─1 User
Organization 1─N Role
Organization 1─N Landlord
Organization 1─N Renter

Landlord 1─N Property
Property 1─N PropertyServiceAccount
Property 1─N RecurringCharge 1─N ChargeEntry
Property 1─N WorkOrder 1─N WorkOrderQuote
Property 1─N Contract N─1 Renter

Contract 1─N ContractAdjustment
Contract 1─N RentPeriod 1─N Payment

Landlord 1─N Settlement 1─N SettlementLineItem
SettlementLineItem N─1 (Payment | ChargeEntry | WorkOrder)   [según line_type]

WorkOrder / WorkOrderQuote / Settlement 1─N Attachment
User 1─N Notification
* ─N AuditLog (todas las operaciones sensibles)
```

---

## 5. Glosario de Términos del Dominio

- **Organización (tenant):** la administradora de propiedades cliente de la plataforma. En la arquitectura, "tenant" refiere SIEMPRE a la organización — nunca al inquilino.
- **Propietario / Landlord:** el dueño de las propiedades, a quien se le rinde. En código se usa `landlord` para evitar la colisión entre "inquilino" (tenant en inglés) y "tenant" (multi-tenancy).
- **Inquilino / Renter:** quien alquila. En código se usa `renter` por la misma razón.
- **Contrato de locación:** vínculo propiedad + inquilino con monto, plazo, % de mora diaria y (solo ARS) configuración de ajuste.
- **Ajuste:** actualización del monto de un contrato ARS. El sistema avisa cuándo toca; el % lo ingresa el operador (calculado por fuera según el índice de referencia).
- **Índice de referencia:** ICL (BCRA), IPC Córdoba u otro — dato informativo del contrato; el sistema no almacena tablas de índices.
- **Período de alquiler:** el alquiler de un mes de un contrato; se genera "pendiente" el 1° de cada mes.
- **Día de gracia:** último día del mes para pagar en término (default 10, configurable por organización). Desde el día siguiente corre la mora.
- **Mora:** interés diario por pago fuera de término: saldo impago × % diario del contrato × días. El sistema sugiere; el operador puede perdonar total o parcialmente.
- **Dinero ya rendido:** alquiler que el inquilino transfirió directo a la cuenta del propietario; no pasa por la administración pero paga comisión igual.
- **Cargo del mes:** importe mensual de un concepto recurrente de la propiedad (rentas, municipalidad), ingresado a mano, descontado en la liquidación.
- **Pedido de reparación / orden de trabajo:** ciclo de arreglo de una propiedad: pedido → cotizaciones → aprobación → ejecución → cierre.
- **Pagador (Paga: Dueño / Administración):** quién paga una reparación. Administración → se descuenta en la liquidación; Dueño → solo historial.
- **Liquidación / rendición:** documento mensual por propietario: cobros − comisión − cargos − reparaciones − ya rendido, por moneda. Export Excel + PDF.
- **Comisión por administración:** % propio de cada propietario sobre los alquileres del período de todas sus propiedades.
- **Soft delete:** eliminación lógica (`deleted_at`); nada se borra físicamente.
- **RLS (Row-Level Security):** aislamiento físico entre organizaciones en PostgreSQL vía `organization_id`.
