# async-worker

## Cuándo leer este skill

Leer **antes de**:

- Crear o modificar una tarea Celery.
- Encolar un job desde un endpoint HTTP (`202 Accepted`).
- Configurar Celery Beat (schedules: digests, aplicación programada de índices, alertas de vencimiento de contrato).
- Procesar un dataset largo (cálculo masivo de liquidaciones, generación de documentos).

> Lista canónica de workers: se define en `core/sdd_04_nonfunctional.md` (paso 4 del diseño); los workers de este skill (`indices_worker`, `notification_worker`, `documents_worker`) son ilustrativos.

## Stack relevante

| Capa | Tecnología | Fuente |
|---|---|---|
| Queue | Celery 5+ | backend `CLAUDE.md` §3 |
| Broker | Redis 7 | backend `CLAUDE.md` §3 |
| Scheduler | Celery Beat (cron) | backend `CLAUDE.md` §3 |
| Workers separados (ilustrativos) | `indices_worker` (obtiene índices ICL/IPC y aplica ajustes programados), `notification_worker` (email + in-app), `documents_worker` (PDFs de recibos y liquidaciones) | backend `CLAUDE.md` §3 |
| Ubicación | `src/adminprop/workers/` (`celery_app.py` + un archivo por worker) | backend `CLAUDE.md` §9 |
| Result backend | Redis (sólo para estado de tarea, no para datos de negocio) | backend `CLAUDE.md` §3 |

## SDDs de referencia

- `core/sdd_04_nonfunctional.md` §1.3 — SLAs P90/P99 por tipo de tarea, política de retry.
- `core/sdd_04_nonfunctional.md` §3.3 — strategy de escalado de workers por profundidad de cola.
- `features/spec_module_05_liquidaciones.md` §RF-02 — reintentos de cálculo/ajuste por índice.
- `infrastructure/spec_notificaciones.md` §RF-04 — política de reintento de canales.
- `features/spec_module_06_mantenimiento.md` — generación de documentos asociados a órdenes de trabajo.

## El patrón

### Configuración del Celery app

```python
# src/adminprop/workers/celery_app.py
from celery import Celery

from adminprop.config import settings


celery_app = Celery(
    "adminprop",
    broker=settings.REDIS_BROKER_URL,
    backend=settings.REDIS_RESULT_BACKEND_URL,
    include=[
        "adminprop.workers.indices_worker",
        "adminprop.workers.notification_worker",
        "adminprop.workers.documents_worker",
    ],
)

celery_app.conf.update(
    task_acks_late=True,                        # ack tras éxito (no al recibir)
    task_reject_on_worker_lost=True,            # re-encolar si el worker muere
    task_track_started=True,
    worker_prefetch_multiplier=1,               # sin hoarding; equidad entre workers
    broker_connection_retry_on_startup=True,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)

# ─── Celery Beat (schedulers) ──────────────────────────────────────
# SDD: spec_notificaciones §RF-01 (digests), spec_module_05_liquidaciones §RF-02
# (ajustes programados por índice), spec_module_03_contratos (alertas de vencimiento).
celery_app.conf.beat_schedule = {
    "digest-diario-per-org-08-local": {
        "task": "adminprop.workers.notification_worker.dispatch_daily_digests",
        "schedule": 60 * 15,   # cada 15 min; el worker filtra por timezone-org == 08:00
    },
    "digest-semanal-lunes-am": {
        "task": "adminprop.workers.notification_worker.dispatch_weekly_digests",
        "schedule": 60 * 60,   # cada hora; el worker filtra lunes AM por timezone
    },
    "apply-scheduled-index-adjustments-03-utc": {
        "task": "adminprop.workers.indices_worker.apply_scheduled_adjustments",
        "schedule": {"hour": 3, "minute": 0},   # 03:00 UTC — batch diario
    },
    "contract-expiration-alert-09-local": {
        "task": "adminprop.workers.notification_worker.dispatch_contract_expiration_alerts",
        "schedule": 60 * 15,   # cada 15 min; filtra orgs cuyo 09:00 local cae en esta ventana
    },
}
```

### Estructura de una tarea

Cada tarea debe:

1. Recibir IDs (no objetos), porque los argumentos viajan por JSON.
2. Re-cargar los objetos desde DB con scoping multi-tenant explícito.
3. Setear `app.current_tenant_id` antes de cualquier query (igual que un request HTTP).
4. Actualizar el estado del job en cada transición (`pending → processing → completed | failed`).
5. Diferenciar errores reintentables de no-reintentables.
6. Loggear con `request_id` + `organization_id` + `user_id` propagados.

```python
# src/adminprop/workers/indices_worker.py
# SDD: features/spec_module_05_liquidaciones.md §RF-02

from datetime import datetime
from uuid import UUID
import logging
import asyncio

from celery import Task

from adminprop.workers.celery_app import celery_app
from adminprop.db.session import async_session_factory, set_tenant_context
from adminprop.modules.contracts.models import Contract
from adminprop.modules.contracts.repository import ContractRepository
from adminprop.shared.indices.bcra import get_icl_index
from adminprop.shared.errors.retryable import (
    RetryableIndexError,
    NonRetryableIndexError,
)


logger = logging.getLogger(__name__)


# ─── Política de reintentos: spec_module_05 §RF-02 + sdd_04 §1.3 ──
# Máximo 3 intentos; backoff exponencial con jitter.
class IndicesTask(Task):
    autoretry_for = (RetryableIndexError,)
    retry_backoff = True
    retry_backoff_max = 600          # 10 min cap
    retry_jitter = True
    max_retries = 3


@celery_app.task(base=IndicesTask, bind=True, name="adminprop.workers.indices_worker.aplicar_ajuste_contrato")
def aplicar_ajuste_contrato(self, contract_id: str, organization_id: str, request_id: str) -> None:
    """
    Aplica el ajuste programado por índice (ICL/IPC) a un contrato.
    SDD: spec_module_05_liquidaciones.md §RF-02.
    Implements: RN-L01 (un ajuste aplicado por período), RN-D01 (scoping multi-tenant).
    """
    contract_uuid = UUID(contract_id)
    org_uuid = UUID(organization_id)

    # request_id propagado para distributed tracing
    logger.info(
        "aplicar_ajuste_contrato start",
        extra={
            "request_id": request_id,
            "organization_id": organization_id,
            "contract_id": contract_id,
            "attempt": self.request.retries + 1,
            "service": "indices_worker",
        },
    )

    asyncio.run(_aplicar_ajuste_contrato_async(contract_uuid, org_uuid, request_id))


async def _aplicar_ajuste_contrato_async(contract_id: UUID, org_id: UUID, request_id: str) -> None:
    async with async_session_factory() as session:
        # Setear contexto de tenant antes de cualquier query — adminprop_app usa RLS.
        await set_tenant_context(session, org_id)

        repo = ContractRepository(session)
        contract = await repo.get_by_id(contract_id, org_id)
        if contract is None:
            # Contrato borrado entre encolar y procesar: terminar limpio
            logger.warning("contract not found, skipping", extra={"contract_id": str(contract_id)})
            return

        # Estado → adjusting
        contract.adjustment_status = "processing"
        await session.flush()

        try:
            index_value = await get_icl_index(contract.adjustment_reference_date)

            new_amount = contract.monthly_amount * (1 + index_value.variation_percent / 100)
            await repo.apply_adjustment(
                contract_id=contract_id,
                organization_id=org_id,
                new_monthly_amount=new_amount,
                index_value_used=index_value.variation_percent,
                applied_at=datetime.utcnow(),
            )
            contract.adjustment_status = "completed"
            await session.flush()
            await session.commit()

        except RetryableIndexError as exc:
            # Retryable: dejá que IndicesTask reintente.
            await session.rollback()
            contract.adjustment_status = "pending"   # vuelve a pending para próximo intento
            await session.commit()
            raise

        except NonRetryableIndexError as exc:
            # Non-retryable: marcar como failed inmediatamente.
            await session.rollback()
            contract.adjustment_status = "failed"
            contract.metadata = {**(contract.metadata or {}), "last_error": str(exc)}
            await session.commit()
            # Notificar al owner/admin (Módulo notificaciones evento CONTRACT_ADJUSTMENT_FAILED)
            await notify_adjustment_failure(contract_id, org_id, str(exc))
            raise
```

### Setear contexto de tenant en un worker

El worker no tiene un request HTTP que dispare el middleware. Debe setear el contexto manualmente:

```python
# src/adminprop/db/session.py (fragmento)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from uuid import UUID


async def set_tenant_context(session: AsyncSession, organization_id: UUID | None) -> None:
    """
    Setear app.current_tenant_id para que RLS funcione.
    En HTTP: lo llama el middleware FastAPI.
    En workers: lo llama explícitamente la tarea Celery antes de cualquier query.
    """
    if organization_id is None:
        # Caso Super Admin (rol adminprop_superadmin con BYPASSRLS): no requiere setting.
        return
    await session.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, true)"),
        {"tid": str(organization_id)},
    )
```

### Encolar desde un endpoint

```python
# src/adminprop/modules/contracts/service.py
from uuid import UUID
from adminprop.workers.indices_worker import aplicar_ajuste_contrato as celery_aplicar_ajuste


class ContractService:
    async def request_index_adjustment(self, contract_id: UUID, organization_id: UUID, request_id: str) -> None:
        # 1. Marcar el contrato en estado pending de ajuste
        await self._repo.mark_adjustment_pending(contract_id, organization_id)

        # 2. Encolar el job. Args: IDs serializables, no objetos ORM.
        celery_aplicar_ajuste.apply_async(
            args=[str(contract_id), str(organization_id), request_id],
            queue="indices",   # un worker dedicado consume sólo esta cola
        )
```

### Política de reintentos por tipo de tarea

| Worker | Reintentables | No reintentables | Política |
|---|---|---|---|
| `indices_worker.aplicar_ajuste_contrato` | 429/5xx/timeouts del servicio de índices (BCRA/INDEC) | valor de índice inexistente para el período, contrato en estado inválido | `max_retries=3`, backoff exponencial con jitter (30s → 90s → 270s). Tras agotar: `contract.adjustment_status = 'failed'` + evento `CONTRACT_ADJUSTMENT_FAILED`. |
| `notification_worker` (por canal) | 429, 5xx del proveedor (Resend) | 400 (email inválido), 404 (destinatario eliminado) | `max_retries=3`, backoff (30s, 5min, 30min). |
| `documents_worker` | I/O / generación de PDF temporal | Datos inexistentes para el período (genera PDF con mensaje "sin datos", no falla) | `max_retries=3`, backoff 15 min. Sin éxito → notificación `DOCUMENTO_FALLIDO`. |

### Categorización reintentable vs no reintentable

```python
# src/adminprop/shared/errors/retryable.py
from typing import Type


# Excepciones que indican que el siguiente intento puede tener éxito
class RetryableError(Exception):
    """Base para errores donde el retry es razonable."""


class RetryableIndexError(RetryableError):
    """Servicio de índices (BCRA/INDEC) temporal: 429, 5xx, timeouts."""


class RetryableNotificationError(RetryableError):
    """Resend transient."""


# Excepciones que NO deben reintentarse (matar el job inmediatamente)
class NonRetryableError(Exception):
    """Base para errores estructurales: input inválido, credencial mala, regla de negocio."""


class NonRetryableIndexError(NonRetryableError):
    """Valor de índice inexistente para el período, o contrato en estado inválido."""


def is_retryable(exc: Exception) -> bool:
    return isinstance(exc, RetryableError)
```

Mapping de errores HTTP de proveedores a categorías:

```python
def classify_index_service_error(exc: Exception) -> Type[Exception]:
    from httpx import HTTPStatusError, TimeoutException

    if isinstance(exc, TimeoutException):
        return RetryableIndexError
    if isinstance(exc, HTTPStatusError):
        status = exc.response.status_code
        if status in {429, 500, 502, 503, 504}:
            return RetryableIndexError
        if status in {400, 404}:
            return NonRetryableIndexError
    return RetryableIndexError   # default conservador
```

### Tracking del estado de un job

Para tareas que el cliente polea vía `GET /<resource>/:id`, el estado vive en la propia tabla del recurso (no en el result backend de Celery):

- `contracts.adjustment_status` ∈ `pending|processing|completed|failed`.
- `settlement_batches.status` ∈ `pending|processing|completed|with_errors|failed`.
- `work_orders.status` ∈ `open|in_progress|closed|cancelled`.
- `generated_documents.status` ∈ `generating|ready|sent|failed`.

El result backend de Celery (`REDIS_RESULT_BACKEND_URL`) sólo se usa para `task_id` (operacional), no para exponer datos al cliente.

## Template

Skeleton de una tarea Celery:

```python
# src/adminprop/workers/<worker>.py
# SDD: <ruta-del-SDD>.md §<sección>

from uuid import UUID
import logging
import asyncio

from celery import Task

from adminprop.workers.celery_app import celery_app
from adminprop.db.session import async_session_factory, set_tenant_context
from adminprop.shared.errors.retryable import RetryableError, NonRetryableError


logger = logging.getLogger(__name__)


class <Module>Task(Task):
    autoretry_for = (RetryableError,)
    retry_backoff = True
    retry_backoff_max = 600
    retry_jitter = True
    max_retries = 3


@celery_app.task(
    base=<Module>Task,
    bind=True,
    name="adminprop.workers.<worker>.<task_name>",
)
def <task_name>(self, <resource>_id: str, organization_id: str, request_id: str) -> None:
    """
    SDD: <ruta>.md §<sección>.
    Implements: <RN-XX>.
    """
    logger.info(
        "<task_name> start",
        extra={
            "request_id": request_id,
            "organization_id": organization_id,
            "<resource>_id": <resource>_id,
            "attempt": self.request.retries + 1,
            "service": "<worker>",
        },
    )

    try:
        asyncio.run(
            _<task_name>_async(
                UUID(<resource>_id),
                UUID(organization_id),
                request_id,
            )
        )
    except RetryableError as exc:
        logger.warning("retryable error", extra={"error": str(exc)})
        raise
    except NonRetryableError as exc:
        logger.error("non-retryable, marking failed", extra={"error": str(exc)})
        # marcar el resource como failed (depende del módulo) y notificar
        raise


async def _<task_name>_async(
    <resource>_id: UUID,
    organization_id: UUID,
    request_id: str,
) -> None:
    async with async_session_factory() as session:
        await set_tenant_context(session, organization_id)

        # 1. Cargar el recurso (filtrado por organization_id)
        # 2. Marcar status = processing
        # 3. Ejecutar la lógica
        # 4. Marcar status = completed | failed con metadata
        # 5. Commit
        ...
```

## Checklist pre-commit

- [ ] La tarea recibe IDs como string (UUID), no objetos.
- [ ] La tarea está en `src/adminprop/workers/<worker>.py` y se importa en `celery_app.include`.
- [ ] La tarea declara `name="adminprop.workers.<worker>.<task_name>"` explícito (no auto-generado).
- [ ] Antes de cualquier query a una tabla tenant-scoped, llama a `set_tenant_context(session, organization_id)`.
- [ ] El estado del recurso (`contracts.adjustment_status`, `settlement_batches.status`, etc.) se actualiza en cada transición.
- [ ] Diferencia `RetryableError` de `NonRetryableError`.
- [ ] La clase base `<Module>Task` declara `autoretry_for`, `retry_backoff`, `retry_jitter`, `max_retries`.
- [ ] El número máximo de reintentos respeta lo que el SDD especifica (típicamente 3).
- [ ] Tras agotar reintentos, marca el recurso como `failed` y emite notificación.
- [ ] El `request_id` se propaga al log y a la notificación generada.
- [ ] El logger usa `extra={"request_id": ..., "organization_id": ..., "service": "..."}`.
- [ ] La tarea es idempotente o respeta `task_acks_late=True` para que el re-procesamiento no duplique side-effects.
- [ ] Si la tarea hace batch, respeta el tamaño máximo del SDD.
- [ ] Las constantes (delays, max_retries) están alineadas con `sdd_04` §1.3.

## Antipatrones

```python
# ❌ Pasar objetos ORM a la tarea
celery_aplicar_ajuste.apply_async(args=[contract])   # ¡Falla la serialización JSON!

# ✅ Pasar IDs como string
celery_aplicar_ajuste.apply_async(args=[str(contract.id), str(org_id), request_id])
```

```python
# ❌ Worker sin setear tenant context
async def _aplicar_ajuste_contrato_async(contract_id, org_id):
    async with async_session_factory() as session:
        # ¡No se setea app.current_tenant_id!
        repo = ContractRepository(session)
        contract = await repo.get_by_id(contract_id)
        # RLS bloquea la query → 0 filas → "contrato no encontrado" silencioso.

# ✅ Setear contexto al inicio
async def _aplicar_ajuste_contrato_async(contract_id, org_id, request_id):
    async with async_session_factory() as session:
        await set_tenant_context(session, org_id)
        repo = ContractRepository(session)
        contract = await repo.get_by_id(contract_id, org_id)
```

```python
# ❌ Tratar todo error como reintentable
@celery_app.task(autoretry_for=(Exception,), max_retries=10)
def aplicar_ajuste_contrato(contract_id):
    ...
# Si el índice no existe para el período (404 estructural), retry no va a cambiar el resultado.
# Consume cuota, eleva costos, mantiene el contrato en estado ambiguo por horas.

# ✅ Diferenciar reintentables
@celery_app.task(base=IndicesTask, autoretry_for=(RetryableIndexError,), max_retries=3)
def aplicar_ajuste_contrato(contract_id):
    try:
        result = get_icl_index(...)
        return result
    except RetryableIndexError:
        raise   # Celery reintenta
    except NonRetryableIndexError as exc:
        # marcar contract.adjustment_status = 'failed' + notificar admin
        ...
```

```python
# ❌ No actualizar el estado del recurso al terminar
async def _aplicar_ajuste_contrato_async(contract_id, org_id, ...):
    contract = await repo.get(...)
    contract.adjustment_status = "processing"
    await session.flush()
    # ... procesar ...
    # ¡Falta marcar completed! El cliente lo verá en "processing" para siempre.

# ✅ Actualizar el estado al final (en try/except)
try:
    # ... procesar ...
    contract.adjustment_status = "completed"
    await session.commit()
except RetryableError:
    contract.adjustment_status = "pending"   # vuelve a estado inicial para próximo intento
    await session.commit()
    raise
except NonRetryableError as exc:
    contract.adjustment_status = "failed"
    contract.metadata = {**(contract.metadata or {}), "last_error": str(exc)}
    await session.commit()
    raise
```

```python
# ❌ Bloquear el thread principal con I/O sincrónico
@celery_app.task
def aplicar_ajuste_contrato(contract_id):
    contract = sync_db.query(Contract).filter_by(id=contract_id).first()
    response = requests.get("https://api.bcra.gob.ar/estadisticas/v3.0/monetarias/40", ...)  # blocking
    contract.monthly_amount = response.json()["valor"]
    sync_db.commit()
# Si el servicio del índice tarda 5s, el worker está ocioso 5s. Throughput cae.

# ✅ Async dentro de una tarea Celery: usar asyncio.run
@celery_app.task(bind=True)
def aplicar_ajuste_contrato(self, contract_id, org_id, request_id):
    asyncio.run(_aplicar_ajuste_contrato_async(UUID(contract_id), UUID(org_id), request_id))
```

```python
# ❌ Ignorar el request_id
@celery_app.task
def aplicar_ajuste_contrato(contract_id):
    logger.info("processing")   # sin request_id → debug imposible

# ✅ Propagar request_id desde el endpoint hasta el log del worker
@celery_app.task(bind=True)
def aplicar_ajuste_contrato(self, contract_id, org_id, request_id):
    logger.info("processing", extra={"request_id": request_id, "contract_id": contract_id})
```

```python
# ❌ Tarea no-idempotente con task_acks_late=True
@celery_app.task
def send_contract_notice_email(contract_id):
    contract = ...
    smtp_client.send(contract)
    contract.notice_sent_at = datetime.utcnow()
    db.commit()
# Si el worker muere DESPUÉS de enviar pero ANTES del commit, al re-encolar
# se envía un segundo email. Spam.

# ✅ Hacer la tarea idempotente: chequear estado antes de side-effect
@celery_app.task
def send_contract_notice_email(contract_id):
    contract = ...
    if contract.notice_sent_at:
        logger.info("notice already sent, skipping", extra={"contract_id": contract_id})
        return
    # Marcar primero (en transacción), luego enviar.
    contract.notice_sent_at = datetime.utcnow()
    db.commit()
    smtp_client.send(contract)
```

## Referencias

- Backend `CLAUDE.md` §3 "Cola y caché" — workers separados por dominio.
- Backend `CLAUDE.md` §4 "Flujos asíncronos identificados" — lista exhaustiva de endpoints async.
- Backend `CLAUDE.md` §8 — "encolar tareas largas en Celery, retornar 202 Accepted" + "request_id propagado".
- `core/sdd_04_nonfunctional.md` §1.3 — SLAs P90/P99 + política de reintentos por tipo de tarea.
- `core/sdd_04_nonfunctional.md` §3.3 — escalado de workers por profundidad de cola.
- `features/spec_module_05_liquidaciones.md` §RF-02 — pipeline de ajuste programado por índice.
- `infrastructure/spec_notificaciones.md` §"Apéndice" — política de reintento por canal (email/in-app).
- `_index.md` §4 #8 — Celery + Redis con workers separados.
