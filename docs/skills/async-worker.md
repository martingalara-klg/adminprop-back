# async-worker

## Cuándo leer este skill

Leer **antes de**:

- Crear o modificar una tarea Celery.
- Encolar un job desde un endpoint HTTP (`202 Accepted`).
- Configurar Celery Beat (schedules: `generate_rent_periods`, `detect_due_adjustments`, `detect_expiring_contracts`).
- Procesar un dataset largo (cálculo masivo de liquidaciones, generación de documentos).

> Lista canónica de workers: `core/sdd_04_nonfunctional.md` §1.3 — `notification_worker` + `documents_worker` + las 3 tareas de Celery Beat (`generate_rent_periods`, `detect_due_adjustments`, `detect_expiring_contracts`). No existe un worker de índices: los ajustes por índice son ingreso manual del porcentaje (`sdd_03` §8).

## Stack relevante

| Capa | Tecnología | Fuente |
|---|---|---|
| Queue | Celery 5+ | backend `CLAUDE.md` §3 |
| Broker | Redis 7 | backend `CLAUDE.md` §3 |
| Scheduler | Celery Beat (cron) | backend `CLAUDE.md` §3 |
| Workers (canónicos) | `notification_worker` (email vía Resend + notificaciones in-app), `documents_worker` (Excel/PDF de liquidaciones) | `sdd_04` §1.3 |
| Ubicación | `src/adminprop/workers/` (`celery_app.py` + un archivo por worker) | backend `CLAUDE.md` §9 |
| Result backend | Redis (sólo para estado de tarea, no para datos de negocio) | backend `CLAUDE.md` §3 |

## SDDs de referencia

- `core/sdd_04_nonfunctional.md` §1.3 — lista canónica de workers, tareas de Celery Beat y política de retry.
- `core/sdd_04_nonfunctional.md` §3.3 — strategy de escalado de workers por profundidad de cola.
- `core/sdd_03_api_contracts.md` §9 — generación mensual de `rent_periods` como job de Celery Beat.
- `infrastructure/spec_notificaciones.md` §RF-04 — política de reintento de canales.
- `features/spec_module_06_mantenimiento.md` — eventos de notificación del ciclo de órdenes de trabajo (`work_order_created`, `quote_submitted`, `work_order_closed`).

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
# SDD: sdd_04 §1.3 — lista canónica de tareas programadas.
celery_app.conf.beat_schedule = {
    "generate-rent-periods-monthly": {
        "task": "adminprop.workers.notification_worker.generate_rent_periods",
        "schedule": {"hour": 0, "minute": 30},   # 1° de cada mes, 00:30 (tz de la org); idempotente
    },
    "detect-due-adjustments-daily": {
        "task": "adminprop.workers.notification_worker.detect_due_adjustments",
        "schedule": {"hour": 1, "minute": 0},   # diaria, 01:00 — crea ajustes pending (RN-C03)
    },
    "detect-expiring-contracts-daily": {
        "task": "adminprop.workers.notification_worker.detect_expiring_contracts",
        "schedule": {"hour": 1, "minute": 30},   # diaria, 01:30 — notifica contratos por vencer
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
# src/adminprop/workers/documents_worker.py
# SDD: core/sdd_03_api_contracts.md §11 "Liquidaciones"

from datetime import datetime
from uuid import UUID
import logging
import asyncio

from celery import Task

from adminprop.workers.celery_app import celery_app
from adminprop.db.session import async_session_factory, set_tenant_context
from adminprop.modules.settlements.repository import SettlementRepository
from adminprop.shared.documents.excel import build_settlement_excel
from adminprop.shared.documents.pdf import build_settlement_pdf
from adminprop.shared.errors.retryable import (
    RetryableError,
    NonRetryableError,
)


logger = logging.getLogger(__name__)


# ─── Política de reintentos: sdd_04 §1.3 ───────────────────────────
# Máximo 3 intentos; backoff exponencial con jitter.
class DocumentsTask(Task):
    autoretry_for = (RetryableError,)
    retry_backoff = True
    retry_backoff_max = 600          # 10 min cap
    retry_jitter = True
    max_retries = 3


@celery_app.task(base=DocumentsTask, bind=True, name="adminprop.workers.documents_worker.generate_settlement_files")
def generate_settlement_files(self, settlement_id: str, organization_id: str, request_id: str) -> None:
    """
    Genera el Excel (openpyxl) y el PDF (WeasyPrint) de una liquidación.
    SDD: core/sdd_03_api_contracts.md §11 "POST /settlements/generate".
    Implements: RN-L03 (regeneración auditada), RN-D01 (scoping multi-tenant).
    """
    settlement_uuid = UUID(settlement_id)
    org_uuid = UUID(organization_id)

    # request_id propagado para distributed tracing
    logger.info(
        "generate_settlement_files start",
        extra={
            "request_id": request_id,
            "organization_id": organization_id,
            "settlement_id": settlement_id,
            "attempt": self.request.retries + 1,
            "service": "documents_worker",
        },
    )

    asyncio.run(_generate_settlement_files_async(settlement_uuid, org_uuid, request_id))


async def _generate_settlement_files_async(settlement_id: UUID, org_id: UUID, request_id: str) -> None:
    async with async_session_factory() as session:
        # Setear contexto de tenant antes de cualquier query — adminprop_app usa RLS.
        await set_tenant_context(session, org_id)

        repo = SettlementRepository(session)
        settlement = await repo.get_by_id(settlement_id, org_id)
        if settlement is None:
            # Liquidación borrada entre encolar y procesar: terminar limpio
            logger.warning("settlement not found, skipping", extra={"settlement_id": str(settlement_id)})
            return

        # Estado → processing
        settlement.status = "processing"
        await session.flush()

        try:
            excel_path = await build_settlement_excel(settlement)
            pdf_path = await build_settlement_pdf(settlement)

            await repo.attach_generated_files(
                settlement_id=settlement_id,
                organization_id=org_id,
                excel_path=str(excel_path),
                pdf_path=str(pdf_path),
                generated_at=datetime.utcnow(),
            )
            settlement.status = "completed"
            await session.flush()
            await session.commit()

        except RetryableError as exc:
            # Retryable: dejá que DocumentsTask reintente.
            await session.rollback()
            settlement.status = "pending"   # vuelve a pending para próximo intento
            await session.commit()
            raise

        except NonRetryableError as exc:
            # Non-retryable: marcar como failed inmediatamente.
            await session.rollback()
            settlement.status = "failed"
            settlement.metadata = {**(settlement.metadata or {}), "last_error": str(exc)}
            await session.commit()
            # Notificar al owner/admin (Módulo notificaciones evento DOCUMENTO_FALLIDO)
            await notify_document_generation_failure(settlement_id, org_id, str(exc))
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
# src/adminprop/modules/settlements/service.py
from uuid import UUID
from adminprop.workers.documents_worker import generate_settlement_files as celery_generate_settlement_files


class SettlementService:
    async def generate(self, landlord_id: UUID, period: str, organization_id: UUID, request_id: str) -> UUID:
        # 1. Crear la liquidación en estado pending
        settlement_id = await self._repo.create_pending(landlord_id, period, organization_id)

        # 2. Encolar el job. Args: IDs serializables, no objetos ORM.
        celery_generate_settlement_files.apply_async(
            args=[str(settlement_id), str(organization_id), request_id],
            queue="documents",   # un worker dedicado consume sólo esta cola
        )
        return settlement_id
```

### Política de reintentos por tipo de tarea

| Worker | Reintentables | No reintentables | Política |
|---|---|---|---|
| `documents_worker.generate_settlement_files` | I/O / generación de Excel/PDF temporal | Datos inexistentes para el período (genera el documento con mensaje "sin datos", no falla) | `max_retries=3`, backoff 15 min. Sin éxito → notificación `DOCUMENTO_FALLIDO`. |
| `notification_worker` (por canal) | 429, 5xx del proveedor (Resend) | 400 (email inválido), 404 (destinatario eliminado) | `max_retries=3`, backoff (30s, 5min, 30min). |

### Categorización reintentable vs no reintentable

```python
# src/adminprop/shared/errors/retryable.py
from typing import Type


# Excepciones que indican que el siguiente intento puede tener éxito
class RetryableError(Exception):
    """Base para errores donde el retry es razonable."""


class RetryableNotificationError(RetryableError):
    """Resend transient: 429, 5xx, timeouts."""


# Excepciones que NO deben reintentarse (matar el job inmediatamente)
class NonRetryableError(Exception):
    """Base para errores estructurales: input inválido, credencial mala, regla de negocio."""


class NonRetryableNotificationError(NonRetryableError):
    """Email inválido (400) o destinatario eliminado (404) en Resend."""


def is_retryable(exc: Exception) -> bool:
    return isinstance(exc, RetryableError)
```

Mapping de errores HTTP de proveedores a categorías:

```python
def classify_provider_error(exc: Exception) -> Type[Exception]:
    from httpx import HTTPStatusError, TimeoutException

    if isinstance(exc, TimeoutException):
        return RetryableError
    if isinstance(exc, HTTPStatusError):
        status = exc.response.status_code
        if status in {429, 500, 502, 503, 504}:
            return RetryableError
        if status in {400, 404}:
            return NonRetryableError
    return RetryableError   # default conservador
```

### Tracking del estado de un job

Para tareas que el cliente polea vía `GET /<resource>/:id`, el estado vive en la propia tabla del recurso (no en el result backend de Celery):

- `settlements.status` ∈ `pending|processing|completed|failed|draft|issued`.
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
- [ ] El estado del recurso (`settlements.status`, `settlement_batches.status`, etc.) se actualiza en cada transición.
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
celery_generate_settlement_files.apply_async(args=[settlement])   # ¡Falla la serialización JSON!

# ✅ Pasar IDs como string
celery_generate_settlement_files.apply_async(args=[str(settlement.id), str(org_id), request_id])
```

```python
# ❌ Worker sin setear tenant context
async def _generate_settlement_files_async(settlement_id, org_id):
    async with async_session_factory() as session:
        # ¡No se setea app.current_tenant_id!
        repo = SettlementRepository(session)
        settlement = await repo.get_by_id(settlement_id)
        # RLS bloquea la query → 0 filas → "liquidación no encontrada" silencioso.

# ✅ Setear contexto al inicio
async def _generate_settlement_files_async(settlement_id, org_id, request_id):
    async with async_session_factory() as session:
        await set_tenant_context(session, org_id)
        repo = SettlementRepository(session)
        settlement = await repo.get_by_id(settlement_id, org_id)
```

```python
# ❌ Tratar todo error como reintentable
@celery_app.task(autoretry_for=(Exception,), max_retries=10)
def generate_settlement_files(settlement_id):
    ...
# Si los datos del período no existen (404 estructural), retry no va a cambiar el resultado.
# Consume cuota, eleva costos, mantiene la liquidación en estado ambiguo por horas.

# ✅ Diferenciar reintentables
@celery_app.task(base=DocumentsTask, autoretry_for=(RetryableError,), max_retries=3)
def generate_settlement_files(settlement_id):
    try:
        result = build_settlement_excel(...)
        return result
    except RetryableError:
        raise   # Celery reintenta
    except NonRetryableError as exc:
        # marcar settlement.status = 'failed' + notificar admin
        ...
```

```python
# ❌ No actualizar el estado del recurso al terminar
async def _generate_settlement_files_async(settlement_id, org_id, ...):
    settlement = await repo.get(...)
    settlement.status = "processing"
    await session.flush()
    # ... procesar ...
    # ¡Falta marcar completed! El cliente lo verá en "processing" para siempre.

# ✅ Actualizar el estado al final (en try/except)
try:
    # ... procesar ...
    settlement.status = "completed"
    await session.commit()
except RetryableError:
    settlement.status = "pending"   # vuelve a estado inicial para próximo intento
    await session.commit()
    raise
except NonRetryableError as exc:
    settlement.status = "failed"
    settlement.metadata = {**(settlement.metadata or {}), "last_error": str(exc)}
    await session.commit()
    raise
```

```python
# ❌ Bloquear el thread principal con I/O sincrónico
@celery_app.task
def generate_settlement_files(settlement_id):
    settlement = sync_db.query(Settlement).filter_by(id=settlement_id).first()
    excel_bytes = build_excel_sync(settlement)  # blocking, I/O pesado
    settlement.excel_path = save_to_disk(excel_bytes)
    sync_db.commit()
# Si la generación del documento tarda varios segundos, el worker está ocioso. Throughput cae.

# ✅ Async dentro de una tarea Celery: usar asyncio.run
@celery_app.task(bind=True)
def generate_settlement_files(self, settlement_id, org_id, request_id):
    asyncio.run(_generate_settlement_files_async(UUID(settlement_id), UUID(org_id), request_id))
```

```python
# ❌ Ignorar el request_id
@celery_app.task
def generate_settlement_files(settlement_id):
    logger.info("processing")   # sin request_id → debug imposible

# ✅ Propagar request_id desde el endpoint hasta el log del worker
@celery_app.task(bind=True)
def generate_settlement_files(self, settlement_id, org_id, request_id):
    logger.info("processing", extra={"request_id": request_id, "settlement_id": settlement_id})
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
- `core/sdd_04_nonfunctional.md` §1.3 — lista canónica de workers, tareas Beat y política de reintentos por tipo de tarea.
- `core/sdd_04_nonfunctional.md` §3.3 — escalado de workers por profundidad de cola.
- `core/sdd_03_api_contracts.md` §11 "Liquidaciones" — generación 202 + polling, regeneración auditada.
- `infrastructure/spec_notificaciones.md` §"Apéndice" — política de reintento por canal (email/in-app).
- `_index.md` §4 #8 — Celery + Redis con workers separados.
