"""documents_worker — calculo asincrono de liquidaciones (issue #29).

SDD: core/sdd_04_nonfunctional.md §1.3 + docs/sdd/features/
spec_module_05_liquidaciones.md §RF-01/RF-02.
Skill: docs/skills/async-worker.md, docs/skills/tenant-isolation.md.
Implements: CA-05-01..04, RN-L01/L02/L04/L05/L06, RN-D01.

Reemplaza el esqueleto del issue #4 (`generate_document_skeleton`), que
documentaba explicitamente que #29/#30 reemplazarian su cuerpo. La
generacion de Excel/PDF (openpyxl/WeasyPrint, RF-03) queda como punto de
extension documentado mas abajo -- llega con el issue #30.
"""

import asyncio
import logging
from uuid import UUID

from celery import Task

from adminprop.db.session import tenant_scoped_session
from adminprop.modules.settlements.job_status import set_job_status
from adminprop.modules.settlements.repository import SettlementRepository
from adminprop.modules.settlements.service import calculate_settlement
from adminprop.shared.errors.retryable import NonRetryableError, RetryableError
from adminprop.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# ─── Politica de reintentos: sdd_04 §1.3 / docs/skills/async-worker.md ─────
class DocumentsTask(Task):
    """RetryableError (I/O transitorio) reintenta con backoff + jitter;
    NonRetryableError (regla de negocio / dato invalido) marca `failed`
    de una y no reintenta -- mismo patron que `NotificationTask`."""

    autoretry_for = (RetryableError,)
    retry_backoff = True
    retry_backoff_max = 600
    retry_jitter = True
    max_retries = 3


@celery_app.task(
    base=DocumentsTask,
    bind=True,
    name="adminprop.workers.documents_worker.generate_settlement",
)
def generate_settlement(self: Task, settlement_id: str, organization_id: str, request_id: str) -> None:
    """RF-01: calcula la liquidacion `settlement_id` (placeholder `draft`
    ya creado por `SettlementService.generate` ANTES del 202). Recibe IDs
    como string -- docs/skills/async-worker.md."""
    logger.info(
        "generate_settlement start",
        extra={
            "request_id": request_id,
            "organization_id": organization_id,
            "settlement_id": settlement_id,
            "attempt": self.request.retries + 1,
            "service": "documents_worker",
        },
    )
    asyncio.run(
        _generate_settlement_async(UUID(settlement_id), UUID(organization_id), request_id)
    )


async def _generate_settlement_async(
    settlement_id: UUID, organization_id: UUID, request_id: str
) -> None:
    await set_job_status(settlement_id, "processing")

    try:
        async with tenant_scoped_session(organization_id) as session:
            # RN-D01/docs/skills/tenant-isolation.md: el contexto de
            # tenant ya esta seteado por `tenant_scoped_session` antes de
            # cualquier query.
            repo = SettlementRepository(session)
            settlement = await repo.get_by_id(settlement_id, organization_id)
            if settlement is None:
                # Liquidacion borrada entre encolar y procesar (o job
                # duplicado corriendo dos veces): terminar limpio, sin
                # marcar ningun estado (no hay recurso al que atarlo).
                logger.warning(
                    "generate_settlement settlement not found, skipping",
                    extra={"request_id": request_id, "settlement_id": str(settlement_id)},
                )
                return

            gathered = await repo.gather_generation_data(
                settlement.landlord_id, organization_id, settlement.period
            )
            unpaid_periods = await repo.list_unpaid_rent_periods(
                settlement.landlord_id, organization_id, settlement.period
            )
            missing_charges = await repo.list_missing_charge_entries(
                settlement.landlord_id, organization_id, settlement.period
            )

            result = calculate_settlement(
                data=gathered,
                commission_pct=settlement.commission_pct_used,
                exchange_rate=settlement.exchange_rate,
                unpaid_periods=unpaid_periods,
                missing_charges=missing_charges,
            )

            await repo.apply_calculation(
                settlement_id,
                organization_id,
                total_collected=result.total_collected,
                commission_total=result.commission_total,
                charges_total=result.charges_total,
                repairs_total=result.repairs_total,
                already_settled_total=result.already_settled_total,
                net_amount=result.net_amount,
                line_items=[item.as_dict() for item in result.line_items],
                settled_work_order_ids=result.settled_work_order_ids,
            )
            # `tenant_scoped_session` comitea al salir del bloque sin
            # excepcion (mismo patron que `workers/notification_worker.py`).

    except RetryableError:
        # Dejar que `DocumentsTask.autoretry_for` reintente -- el
        # placeholder `draft` queda intacto (nada se aplico todavia,
        # `apply_calculation`/el `commit()` implicito no llegaron a
        # ejecutarse porque la excepcion se propaga desde dentro del
        # `async with`, que hace rollback).
        await set_job_status(settlement_id, "pending")
        raise
    except (NonRetryableError, Exception) as exc:  # noqa: BLE001 -- ver docstring
        # RF-01: "failed: no se genero (error real); el motivo queda en
        # el job y en Sentry" -- se loguea con exc_info (Sentry lo
        # captura via su integracion de logging) y se marca `failed` en
        # el job status. Decision de implementacion (documentada en el
        # PR): el placeholder `draft` NUNCA llego a ser una liquidacion
        # real (sin totales, sin line items) -- se borra para no dejar
        # bloqueado el `(landlord_id, period)` con un `409
        # SETTLEMENT_ALREADY_EXISTS` fantasma (ver
        # `SettlementRepository.delete_placeholder`).
        logger.error(
            "generate_settlement failed",
            exc_info=exc,
            extra={
                "request_id": request_id,
                "settlement_id": str(settlement_id),
                "organization_id": str(organization_id),
                "service": "documents_worker",
            },
        )
        await set_job_status(settlement_id, "failed", warnings=[str(exc)])
        async with tenant_scoped_session(organization_id) as cleanup_session:
            await SettlementRepository(cleanup_session).delete_placeholder(
                settlement_id, organization_id
            )
        return

    job_status = "with_errors" if result.warnings else "completed"
    await set_job_status(settlement_id, job_status, warnings=result.warnings)
    logger.info(
        "generate_settlement done",
        extra={
            "request_id": request_id,
            "settlement_id": str(settlement_id),
            "job_status": job_status,
            "warning_count": len(result.warnings),
            "service": "documents_worker",
        },
    )

    # Punto de extension (issue #30): generar Excel (openpyxl) + PDF
    # (WeasyPrint) y guardarlos como Adjuntos de la liquidacion (RF-03).
    # No se invoca aca -- fuera de alcance de este issue.
