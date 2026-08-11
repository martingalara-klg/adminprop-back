---
name: AdminProp — PRD (Product Requirements Document)
description: Problema, propuesta de valor, usuarios, casos de uso, MVP vs futuro, restricciones y métricas de éxito de AdminProp
type: project
version: 1.1
fecha: 2026-08-11
---
# AdminProp — Product Requirements Document (PRD)

**Versión:** 1.1
**Estado:** Borrador para revisión
**Fecha:** 2026-08-04

---

## 1. El Problema

Un corredor inmobiliario que administra alquileres de múltiples dueños (30+ propiedades) opera hoy de forma completamente manual, con una secretaria que lleva la interacción con los inquilinos. Los problemas sistemáticos:

| Problema | Manifestación concreta | Impacto |
|---|---|---|
| **Ciclo mensual manual** | Cada mes hay que recordar qué alquileres vencen, cuáles se cobraron, a quién le toca ajuste y con qué índice | Horas de trabajo repetitivo; errores de cálculo; ajustes que se pasan de fecha |
| **Moras calculadas a mano** | El interés por pago fuera de término se calcula caso por caso, y el perdón (total o parcial) no queda registrado | Inconsistencia entre inquilinos; sin rastro de qué se perdonó y por qué |
| **Información dispersa** | Números de cuenta de rentas, municipalidad, luz, gas, agua y expensas de cada propiedad están en papeles y archivos sueltos | Tiempo perdido buscando datos para verificaciones mensuales |
| **Liquidaciones artesanales** | La rendición mensual de cada propietario se arma en Excel cruzando cobros, impuestos, reparaciones y comisiones | Proceso lento y propenso a errores; difícil de auditar meses después |
| **Reparaciones sin trazabilidad** | Los pedidos, cotizaciones y aprobaciones de arreglos van por WhatsApp/teléfono | No hay historial por propiedad; no queda claro quién pagó cada arreglo |

### Propuesta de Valor

> **AdminProp convierte el ciclo mensual de una administradora de alquileres en un proceso guiado y auditable.** Cada mes el sistema genera los alquileres pendientes, sugiere las moras, avisa los ajustes que tocan, acumula los gastos de cada propiedad, y arma la liquidación de cada propietario en un clic — dejando registro de cada decisión (cobros, perdones de interés, ajustes, reparaciones).

**Para el corredor:** visibilidad total de la cartera y liquidaciones listas sin armar Excels.
**Para la secretaria:** un solo lugar donde cobrar, imputar y consultar — con el sistema calculando por ella.
**Para el encargado de reparaciones:** un canal ordenado para recibir pedidos, subir cotizaciones y cerrar trabajos.

---

## 2. Usuarios Objetivo

### 2.1 Segmento Primario

**Corredores inmobiliarios y administradoras de alquileres** con las siguientes características:
- 10–200 propiedades administradas, de múltiples dueños
- Equipo chico (1–5 personas)
- Contratos en ARS (con ajuste por índice) y USD (sin ajuste)
- Operación en Argentina (mercado inicial: Córdoba)
- Sin sistema de gestión: Excel, papel y WhatsApp

### 2.2 Roles en el Sistema

| Rol | Descripción | Acceso |
|---|---|---|
| **Owner** | El corredor / dueño de la administradora | Total — todos los módulos, configuración, gestión de usuarios |
| **Admin** | Secretaria / operadora | Operación completa (propiedades, contratos, cobranzas, liquidaciones, mantenimiento) excepto usuarios y configuración |
| **Maintenance** | Encargado de reparaciones | Restringido — solo módulo de mantenimiento: órdenes asignadas, cotizaciones, fotos, cierre de trabajos |
| **Super Admin** | Empleado de la plataforma | Solo portal `/superadmin/*`: alta y gestión de administradoras |

Los **propietarios** y los **inquilinos** son registros del sistema, no usuarios: no tienen login en el MVP.

---

## 3. Casos de Uso Principales

### 3.1 Módulo 1 — Propiedades

**UC-01**
Como **admin**, quiero registrar cada propiedad con sus datos y los números de cuenta de todos sus servicios e impuestos, para tener la información centralizada cuando hago las verificaciones mensuales de pago.

*Criterios de aceptación:*
- La propiedad registra: dirección, propietario (obligatorio), tipo, estado y notas.
- Registra números de cuenta **informativos** (sin tracking de montos por servicio): rentas, municipalidad, luz (n° de cliente y n° de contrato), gas, agua, expensas/otros.
- Los números de cuenta son consultables desde la ficha de la propiedad en una sola vista.

---

### 3.2 Módulo 2 — Personas

**UC-02**
Como **owner**, quiero dar de alta a cada propietario con su porcentaje de comisión, para que todas las liquidaciones de sus propiedades usen ese % automáticamente.

*Criterios de aceptación:*
- El alta de propietario incluye: nombre/razón social, datos de contacto, CUIT/DNI, datos bancarios y **% de comisión por administración**.
- El % de comisión del propietario aplica a todos sus contratos.
- El % puede modificarse; el cambio rige para liquidaciones futuras, nunca retroactivo.

---

**UC-03**
Como **admin**, quiero registrar a cada inquilino con sus datos, para asociarlo a contratos y tener su información a mano.

*Criterios de aceptación:*
- El alta de inquilino incluye: nombre, DNI/CUIT, teléfono, email y notas.
- Desde la ficha del inquilino se ven sus contratos (vigentes e históricos) y su estado de deuda.

---

### 3.3 Módulo 3 — Contratos

**UC-04**
Como **admin**, quiero dar de alta un contrato de locación con todas sus condiciones, para que el sistema genere los alquileres y calcule moras y ajustes según lo pactado.

*Criterios de aceptación:*
- El contrato vincula propiedad + inquilino y registra: moneda (ARS o USD), monto inicial, fecha de inicio y fin, **% de mora diaria**, y para contratos en ARS: **cada cuántos meses ajusta** y **con qué índice de referencia** (ICL, IPC Córdoba u otro — dato informativo).
- Los contratos en USD no tienen configuración de ajuste (no se actualizan).
- Una propiedad no puede tener dos contratos vigentes superpuestos en el tiempo.

---

**UC-05**
Como **admin**, quiero que el sistema me avise cuando a un contrato le toca ajuste y me pida el % de aumento, para aplicar el nuevo valor sin que se me pase ninguna actualización.

*Criterios de aceptación:*
- Al llegar el mes de ajuste (según la frecuencia del contrato), el sistema marca el contrato como "ajuste pendiente" y notifica (in-app + email).
- El operador calcula el % por fuera (según el índice de referencia) y lo **ingresa manualmente**; el sistema aplica el % sobre el monto vigente y genera el nuevo monto.
- El alquiler del mes de ajuste se genera con el nuevo valor una vez ingresado el %; mientras no se ingrese, el contrato queda visiblemente en "ajuste pendiente".
- Cada ajuste queda en el historial del contrato: fecha, % aplicado, monto anterior y monto nuevo.

---

**UC-06**
Como **owner**, quiero que el sistema me avise cuando un contrato está por vencer, para negociar la renovación a tiempo.

*Criterios de aceptación:*
- El sistema notifica con anticipación configurable (default 60 días) el vencimiento de cada contrato.
- El listado de contratos permite filtrar por "vence en los próximos N días".

---

### 3.4 Módulo 4 — Cobranzas

**UC-07**
Como **admin**, quiero que el 1° de cada mes el sistema genere automáticamente el alquiler del mes de cada contrato vigente en estado "pendiente", para saber exactamente qué tengo que cobrar.

*Criterios de aceptación:*
- El 1° de cada mes, cada contrato vigente genera su alquiler del período con el monto vigente (post-ajuste si corresponde) en estado `pendiente`.
- El panel de cobranzas muestra los alquileres del mes con su estado (pendiente / cobrado / en mora / parcial).

---

**UC-08**
Como **admin**, quiero registrar cada cobro con su medio, moneda y destino, para que la liquidación del propietario refleje exactamente cómo entró la plata.

*Criterios de aceptación:*
- El cobro registra: fecha, medio (efectivo / transferencia), moneda del pago (ARS / USD) e importe.
- Si un contrato en USD se paga en pesos, el sistema permite ingresar **libremente el tipo de cambio** aplicado.
- El cobro registra el destino: **cuenta de la administración** o **cuenta del propietario** — en el segundo caso queda marcado como "dinero ya rendido" y se descuenta en la liquidación.
- Se permiten pagos parciales; el saldo restante queda como deuda del inquilino.
- Al registrar el cobro se puede generar **opcionalmente el recibo en PDF** para el inquilino (encabezado de la administradora, propiedad, período, capital e intereses cobrados, TC si aplica).

---

**UC-09**
Como **admin**, quiero que ante un pago fuera de término el sistema me **sugiera** el monto con intereses pero me deje imputar libremente, para poder perdonar la mora total o parcialmente según el caso.

*Criterios de aceptación:*
- El alquiler está en término hasta el día 10 del mes inclusive; desde el día 11 corre mora (el día 11 = 1 día de mora).
- El interés sugerido = monto del alquiler × % de mora diaria del contrato × días de mora.
- Al imputar, el operador puede: aceptar el interés sugerido, perdonarlo totalmente, o ingresar un monto de interés distinto (perdón parcial).
- Queda registrado: interés sugerido, interés cobrado y diferencia perdonada.

---

**UC-10**
Como **owner**, quiero ver el estado de deuda de cada inquilino, para saber quién debe, cuánto y desde cuándo.

*Criterios de aceptación:*
- La vista de deuda muestra por inquilino: períodos adeudados, saldos parciales, días de mora e interés acumulado sugerido.
- Se puede filtrar por propiedad, propietario y antigüedad de la deuda.

---

**UC-10b**
Como **admin**, quiero generar un certificado de **libre deuda** para un inquilino, para entregárselo cuando lo solicita (fin de contrato, garantías, trámites).

*Criterios de aceptación:*
- El certificado solo se emite si el inquilino no registra períodos impagos ni saldos parciales en ninguno de sus contratos.
- Es un PDF con el encabezado de la administradora, datos del inquilino, sus contratos/propiedades y la fecha de emisión.
- Si el inquilino tiene deuda, el sistema lo rechaza mostrando el detalle de lo adeudado.
- Cada emisión queda registrada en el log de auditoría.

---

### 3.5 Módulo 5 — Liquidaciones

**UC-11**
Como **admin**, quiero cargar cada mes los importes de impuestos de cada propiedad (rentas y municipalidad), para que se descuenten automáticamente en la liquidación del propietario.

*Criterios de aceptación:*
- Cada propiedad tiene sus conceptos recurrentes (rentas, municipalidad) que se repiten todos los meses.
- El importe de cada concepto se ingresa manualmente cada mes (varía mes a mes).
- Los conceptos cargados aparecen como descuento en la liquidación del propietario del período.

---

**UC-12**
Como **owner**, quiero generar la liquidación mensual de cada propietario en un clic y exportarla, para rendirle sin armar Excels a mano.

*Criterios de aceptación:*
- La liquidación mensual de un propietario consolida todas sus propiedades: **total cobrado − comisión de administración (% del propietario) − impuestos del mes − reparaciones pagadas por la administración − dinero ya rendido** (alquileres transferidos directo a su cuenta).
- Es exportable en **Excel y PDF** con el detalle por propiedad y por concepto.
- Una liquidación emitida puede corregirse y regenerarse (se re-exporta); cada corrección queda registrada en el log de auditoría (quién, cuándo, qué cambió).
- El historial de liquidaciones de cada propietario es consultable.

---

### 3.6 Módulo 6 — Mantenimiento

**UC-13**
Como **admin**, quiero cargar un pedido de reparación sobre una propiedad para que se cotice, dejando registrado quién va a pagar el arreglo.

*Criterios de aceptación:*
- El pedido registra: propiedad, descripción del problema, fotos (opcional) y el campo **Paga: Dueño / Administración**.
- Si paga la **Administración**: al completarse, el gasto se descuenta en la próxima liquidación del propietario y queda en el historial.
- Si paga el **Dueño**: el gasto queda solo en el historial de la propiedad, sin reflejarse en la liquidación.
- El encargado de reparaciones recibe notificación del nuevo pedido.

---

**UC-14**
Como **encargado de reparaciones**, quiero subir cotizaciones (con fotos) a los pedidos que me asignan, para que el corredor decida sin llamadas ni WhatsApp.

*Criterios de aceptación:*
- El encargado ve únicamente los pedidos de reparación; nunca contratos, cobranzas ni liquidaciones.
- Puede subir una o más cotizaciones por pedido: monto, descripción y fotos/archivos adjuntos.
- Al subir una cotización, owner y admin reciben notificación.

---

**UC-15**
Como **owner**, quiero aprobar una cotización y que el encargado marque el trabajo como terminado, para tener el ciclo completo de cada arreglo trazado.

*Criterios de aceptación:*
- Owner o admin aprueban una de las cotizaciones; el pedido pasa a "en ejecución" y el encargado recibe notificación.
- El encargado marca el trabajo como terminado, con fotos del resultado (opcional); owner y admin reciben notificación.
- El ciclo completo queda registrado: pedido → cotizaciones → aprobación → ejecución → terminado, con fechas y autores.

---

**UC-16**
Como **owner**, quiero consultar el historial de reparaciones de cada propiedad, para conocer qué se arregló, cuándo, cuánto costó y quién lo pagó.

*Criterios de aceptación:*
- La ficha de la propiedad lista todas las reparaciones con: fecha, descripción, monto aprobado, pagador (Dueño / Administración) y estado.
- Incluye tanto las pagadas por la administración (descontadas en liquidación) como las pagadas por el dueño.

---

### 3.7 Módulo 7 — Administración

**UC-17**
Como **owner**, quiero gestionar los usuarios del equipo y sus roles, para que cada uno vea solo lo que le corresponde.

*Criterios de aceptación:*
- El owner invita usuarios por email y les asigna rol (`admin` / `maintenance`).
- Un usuario `maintenance` no puede acceder a endpoints ni pantallas de contratos, cobranzas o liquidaciones; el control se aplica en la API, no solo en la UI.
- Todo intento de acceso no autorizado queda registrado en el log de auditoría.

---

**UC-18**
Como **owner**, quiero configurar los parámetros generales de mi administradora, para adaptar el sistema a cómo trabajo.

*Criterios de aceptación:*
- Configurables a nivel organización: día de gracia para mora (default: en término hasta el día 10), anticipación del aviso de vencimiento de contrato (default 60 días), datos de la administradora para los encabezados de liquidaciones.

---

### 3.8 Módulo 0 — Superadmin

**UC-19**
Como **super admin**, quiero dar de alta una administradora e invitar a su owner, para incorporar nuevos clientes a la plataforma sin auto-registro público.

*Criterios de aceptación:*
- El alta crea la organización y envía invitación por email al owner.
- El owner activa su cuenta desde el link de invitación (con expiración).
- El super admin puede deshabilitar/rehabilitar una organización; una organización deshabilitada no puede operar.

---

### 3.9 Notificaciones (transversal)

**UC-20**
Como **usuario**, quiero recibir los avisos del sistema in-app y por email, para enterarme sin tener que revisar pantalla por pantalla.

*Criterios de aceptación:*
- Eventos con notificación en MVP: ajuste de alquiler pendiente, contrato por vencer, cotización recibida, reparación terminada, nuevo pedido de reparación (al encargado).
- Panel in-app con contador de no leídas y marcar como leída (individual y masivo).
- Los avisos también se envían por email al rol correspondiente.

---

## 4. Funcionalidades MVP vs. Futuras

### MVP — Alcance del primer lanzamiento

| Módulo | Funcionalidades incluidas en MVP |
|---|---|
| Superadmin | Alta de organizaciones, invitación de owner, deshabilitación |
| Propiedades | ABM de propiedades con cuentas de servicios informativas |
| Personas | ABM de propietarios (con % comisión) e inquilinos |
| Contratos | ABM de contratos ARS/USD, % mora diaria, ajustes con ingreso manual del % (aviso automático + historial), aviso de vencimiento |
| Cobranzas | Generación mensual de pendientes, registro de cobros (medio/moneda/TC libre/destino), mora sugerida con perdón total/parcial, pagos parciales, estado de deuda, recibo PDF opcional por cobro, certificado de libre deuda |
| Liquidaciones | Conceptos recurrentes por propiedad (rentas, muni) con importe mensual manual, liquidación mensual por propietario con export Excel + PDF, historial |
| Mantenimiento | Pedidos → cotizaciones (con fotos) → aprobación → ejecución → cierre; pagador Dueño/Administración; historial por propiedad |
| Administración | Usuarios y roles (owner/admin/maintenance), configuración de la org, log de auditoría |
| Notificaciones | In-app + email para los eventos del MVP |

### Funcionalidades Futuras (Post-MVP)

| Funcionalidad | Justificación del diferimiento |
|---|---|
| Facturación electrónica AFIP/ARCA (comisiones de la administradora) | Complejidad alta (WSAA/WSFE); el negocio factura hoy por fuera |
| Portal de propietarios (ver sus liquidaciones) e inquilinos (ver su deuda) | El MVP es interno; los dueños reciben Excel/PDF |
| Carga automática de índices (API BCRA / estadísticas) y cálculo automático del % | El ajuste con % manual cubre la operación actual con menos riesgo |
| Recordatorios automáticos a inquilinos (email/WhatsApp) | La secretaria hoy maneja la comunicación; se evalúa post-MVP |
| KPIs y dashboards avanzados (rentabilidad, vacancia) | Los listados y el estado de deuda cubren el MVP |
| Multi-idioma (EN) | Mercado inicial 100% hispanohablante |
| Cobranza SaaS integrada (planes, gateway de pagos) | Una sola administradora en el arranque; enforcement de planes cuando haya demanda |
| App móvil nativa | Web responsive cubre la necesidad |
| Gestión de depósitos de garantía | Decisión explícita (2026-08-11): fuera del alcance — no se registra ni trackea el depósito |

---

## 5. Restricciones y Supuestos del Sistema

### Restricciones

**R-01:** El sistema es SaaS multi-tenant. Cada administradora es un tenant aislado; los datos de un tenant nunca son accesibles desde otro.

**R-02:** La plataforma es web-first y responsive. Sin app móvil nativa en MVP.

**R-03:** Los datos de contratos, cobranzas y liquidaciones son visibles únicamente para roles `owner` y `admin`. El rol `maintenance` accede solo al módulo de mantenimiento; el control se aplica en la API.

**R-04:** Las liquidaciones y los cobros pueden corregirse después de registrados (flexibilidad operativa), pero **toda corrección queda trazada en el log de auditoría** (quién, cuándo, valor anterior y nuevo). Nunca hay borrado físico: las anulaciones son lógicas.

**R-05:** La interfaz es en español (es-AR). Formatos locales: fechas DD/MM/AAAA, números con `.` de miles y `,` decimal, monedas ARS y USD.

**R-06:** El sistema cumple con la Ley N° 25.326 de Protección de Datos Personales (Argentina): derecho de acceso/rectificación/supresión, y protección de los datos de inquilinos y propietarios.

**R-07:** El stack tecnológico es el definido en el esqueleto SDD: Python FastAPI + PostgreSQL 16 (RLS) + Celery/Redis; React 18 + Vite + TypeScript. Multi-tenancy shared-schema con `organization_id` + RLS.

**R-08:** Existe un **Portal Super Admin** en namespace separado (`/superadmin/*`), único mecanismo de alta de organizaciones. No hay auto-registro público.

**R-09:** El MVP **no** emite comprobantes fiscales (AFIP/ARCA queda post-MVP). Las liquidaciones son documentos internos de rendición.

### Supuestos

**S-01:** Escala inicial: 10–200 propiedades por organización, equipos de 1–5 usuarios. La organización fundadora administra ~30 propiedades.

**S-02:** Propietarios e inquilinos no acceden al sistema (sin portal en MVP); reciben las liquidaciones en Excel/PDF por fuera (email/WhatsApp manual).

**S-03:** Los índices de ajuste **no** se cargan como tablas en el sistema: el operador calcula el % por fuera según el índice de referencia del contrato (ICL, IPC Córdoba u otro) y lo ingresa manualmente. El índice del contrato es un dato informativo.

**S-04:** El tipo de cambio para pagos en pesos de contratos USD se ingresa manualmente en cada imputación (sin API de cotización).

**S-05:** La regla de mora es: en término hasta el día 10 del mes inclusive; desde el día 11 corre el interés diario (día 11 = 1 día de mora). El día de gracia es configurable a nivel organización (default 10); el % diario es propio de cada contrato.

**S-06:** La comisión de administración se calcula sobre el alquiler del período de todas las propiedades del dueño, **incluidos** los alquileres que el inquilino transfirió directo a la cuenta del propietario (dinero ya rendido): la administración gestiona igual, cobra igual.

**S-07:** Se aceptan pagos parciales de un alquiler; el saldo queda como deuda y la mora se calcula sobre el saldo impago.

**S-08:** Los archivos generados (liquidaciones Excel/PDF) y subidos (fotos de reparaciones y cotizaciones) se almacenan en filesystem local vía volumen Docker en MVP; storage cloud cuando exista infraestructura.

---

## 6. Métricas de Éxito

### Métricas de adopción (organización fundadora, primeros 3 meses)

| Métrica | Definición | Target |
|---|---|---|
| **Cobros registrados en el sistema** | % de los cobros del mes registrados en AdminProp (vs. cuaderno/Excel) | 100% al mes 2 |
| **Liquidaciones generadas por el sistema** | % de liquidaciones mensuales emitidas desde AdminProp (vs. Excel manual) | 100% al mes 3 |
| **Ajustes aplicados a tiempo** | % de ajustes de contrato aplicados en el mes que correspondía | 100% |
| **Reparaciones trazadas** | % de reparaciones del mes gestionadas por el sistema (vs. WhatsApp) | ≥ 80% al mes 3 |

### Métricas de eficiencia operativa

| Métrica | Definición | Target |
|---|---|---|
| **Tiempo de registro de un cobro** | Desde abrir el panel hasta confirmar la imputación | < 1 minuto |
| **Tiempo de generación de una liquidación** | Con los datos del mes cargados | < 1 minuto (un clic + export) |
| **Tiempo del ciclo mensual completo** | Generación de pendientes → cobros → liquidaciones de todos los dueños | Reducción ≥ 50% vs. proceso manual actual |

### Métricas de performance técnica

| Métrica | Target |
|---|---|
| Latencia P95 de API (lectura) | < 500ms |
| Latencia P95 de API (escritura) | < 1.000ms |
| Generación de liquidación (Excel + PDF) | < 15 segundos |
| Uptime mensual | ≥ 99.5% |
