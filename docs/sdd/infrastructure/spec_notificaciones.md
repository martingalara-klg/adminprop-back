---
name: AdminProp — Notificaciones (plataforma transversal)
description: Servicio transversal de avisos in-app + email — eventos, enrutamiento por rol, panel in-app, y política de reintento por canal
type: project
version: 1.0
fecha: 2026-08-06
---
# Notificaciones — Plataforma Transversal

**Versión:** 1.0 · **Estado:** Borrador para revisión · **Fecha:** 2026-08-06

## Propósito

El servicio que los demás módulos usan para avisar: ajuste de alquiler pendiente, contrato por vencer, pedido de reparación nuevo, cotización recibida, trabajo terminado. Dos canales en MVP: **in-app** (transaccional, siempre) y **email** (best-effort vía Resend). No es un módulo con pantallas propias más allá del panel de avisos (UC-20).

## Eventos del MVP y enrutamiento por rol

| Evento | Disparado por | Destinatarios |
|---|---|---|
| `adjustment_pending` | Job `detect_due_adjustments` (Módulo 3 RF-04) | owner + admin |
| `contract_expiring` | Job `detect_expiring_contracts` (Módulo 3 RF-05) | owner + admin |
| `work_order_created` | Alta de pedido (Módulo 6 RF-01) | usuarios `maintenance` |
| `quote_submitted` | Cotización subida (Módulo 6 RF-02) | owner + admin |
| `work_order_closed` | Cierre de trabajo (Módulo 6 RF-04) | owner + admin |

Agregar un evento nuevo = actualizar esta tabla primero (regla de oro de `sdd_03`).

## Requerimientos Funcionales

### RF-01 — Emisión de eventos

- Los módulos emiten eventos a través de un servicio único (`NotificationService.emit(event_type, payload)`), nunca creando filas de `notifications` a mano.
- La fila in-app se crea **en la misma transacción** del evento de negocio (si el alta del pedido falla, no hay aviso fantasma).
- El envío de email se encola a `notification_worker` **después del commit** (patrón outbox simple: el worker toma notificaciones con `email_sent_at IS NULL`).

### RF-02 — Panel in-app

- `GET /notifications` (propias del usuario; `?unread=true`), badge con contador de no leídas (cacheado 5 min, `sdd_04` §1.4).
- `POST /notifications/:id/read` y `POST /notifications/read-all`.
- Cada aviso lleva el payload suficiente para navegar al recurso (ej: `work_order_id`) sin queries extra.

### RF-03 — Email (best-effort)

- El email **nunca bloquea la operación de negocio** (`sdd_04` §2.9): si Resend falla, el aviso in-app ya existe y el email se reintenta.
- Template simple por evento, en español, con link directo al recurso.

### RF-04 — Política de reintento de canales

- **In-app:** transaccional, sin reintentos (no puede fallar de forma independiente).
- **Email:** reintentos según `sdd_04` §1.3 — `max_retries=3`, backoff 30s → 90s → 270s con jitter, `RetryableError` (5xx/timeout/rate limit de Resend) vs `NonRetryableError` (email inválido, supresión).
- **Dead-letter:** agotados los reintentos, la notificación queda con `email_sent_at IS NULL` + el error registrado en logs con `request_id`; un contador de fallidos por hora dispara alerta a Sentry. No se reintenta más automáticamente (el aviso in-app ya cubrió la necesidad).

## Reglas de Negocio

- **RN-01:** El enrutamiento es por rol según la tabla de eventos; un usuario `inactive` no recibe avisos.
- **RN-02:** El aviso in-app y el email comparten la misma fila de `notifications` (`sdd_02` §2.16): un evento = una notificación por destinatario.
- **RN-03:** Sin preferencias por usuario en MVP (todos los eventos llegan a sus destinatarios); las preferencias configurables son post-MVP.

## Validaciones

- `event_type`: uno de los 5 del enum (CHECK en DB).
- `payload`: JSON con los IDs mínimos del evento (validado por schema del servicio).

## Criterios de Aceptación

- [ ] **CA-NT-01:** Cada uno de los 5 eventos genera la notificación in-app a los destinatarios correctos según la tabla de enrutamiento (y a nadie más).
- [ ] **CA-NT-02:** Si el alta del pedido de reparación falla a mitad de transacción, no queda ninguna notificación creada.
- [ ] **CA-NT-03:** Con Resend caído, la operación de negocio termina OK, el aviso in-app existe, y el email se reintenta con backoff 30/90/270s; agotados los reintentos queda registrado el fallo con `request_id`.
- [ ] **CA-NT-04:** El badge muestra las no leídas del usuario; `read-all` las marca todas y el badge queda en cero.
- [ ] **CA-NT-05:** Un usuario desactivado no recibe nuevas notificaciones.

## Email

- **Proveedor:** Resend (único servicio externo del MVP — `sdd_04` §2.9). API key en `.env` local; migra a un gestor de secretos con la infra cloud.
- **From dinámico:** `"AdminProp · {organization.name} <noreply@{dominio}>"` — un único sender verificado de la plataforma con el nombre de la administradora visible; el dominio definitivo se configura con la infra. `Reply-To`: el email del owner de la organización.
- **Contenido:** asunto corto por evento + cuerpo con el dato esencial y link directo. Sin adjuntos en MVP (las liquidaciones se descargan desde la app).

## Apéndice — Política de reintento por canal

| Canal | Reintentos | Backoff | Errores retryables | Dead-letter |
|---|---|---|---|---|
| in-app | n/a (transaccional) | — | — | — |
| email (Resend) | 3 | 30s → 90s → 270s + jitter | HTTP 5xx, timeout, 429 del proveedor | `email_sent_at IS NULL` + error en logs (`request_id`) + alerta Sentry si se acumulan; sin reintento automático posterior |

**Rate limits del proveedor:** respetar el 429 de Resend como retryable con el backoff estándar; el volumen del MVP (decenas de emails/mes) está lejos de cualquier límite.

## Integraciones

| Módulo / Servicio | Motivo |
|---|---|
| Módulos 3 y 6 | Emiten los 5 eventos del MVP |
| `notification_worker` | Envío de emails post-commit |
| Resend | Canal email |
| Módulo 7 | Los destinatarios salen de las membresías activas y sus roles |
