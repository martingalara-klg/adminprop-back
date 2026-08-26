"""documents_worker — calculo asincrono de liquidaciones (issue #29) +
regeneracion auditada y exports Excel/PDF (issue #30).

SDD: core/sdd_04_nonfunctional.md §1.3 + docs/sdd/features/
spec_module_05_liquidaciones.md §RF-01/RF-02/RF-03/RF-04.
Skill: docs/skills/async-worker.md, docs/skills/tenant-isolation.md.
Implements: CA-05-01..07, RN-L01/L02/L03/L04/L05/L06, RN-D01.

Reemplaza el esqueleto del issue #4 (`generate_document_skeleton`), que
documentaba explicitamente que #29/#30 reemplazarian su cuerpo.
"""

import asyncio
import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from celery import Task

from adminprop.db.session import tenant_scoped_session
from adminprop.modules.administracion.repository import AdministracionRepository
from adminprop.modules.settlements.exports import (
    build_settlement_pdf,
    build_settlement_workbook,
    group_line_items_by_property,
)
from adminprop.modules.settlements.job_status import set_job_status
from adminprop.modules.settlements.models import Settlement
from adminprop.modules.settlements.repository import SettlementRepository
from adminprop.modules.settlements.service import calculate_settlement
from adminprop.shared.attachments.repository import AttachmentRepository
from adminprop.shared.audit.service import audit
from adminprop.shared.errors.retryable import NonRetryableError, RetryableError
from adminprop.shared.storage.local import save_attachment
from adminprop.shared.worker_runtime import run_worker_coroutine
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
def generate_settlement(
    self: Task, settlement_id: str, organization_id: str, request_id: str
) -> None:
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
    # issue #93: `run_worker_coroutine` dispone el engine/cliente Redis
    # cacheados por proceso al terminar -- sin esto, la SEGUNDA tarea que
    # este mismo proceso worker procese revienta con `Future attached to
    # a different loop`/`Event loop is closed` (ver
    # `shared/worker_runtime.py`).
    asyncio.run(
        run_worker_coroutine(
            _generate_settlement_async(UUID(settlement_id), UUID(organization_id), request_id)
        )
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

            updated = await repo.apply_calculation(
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
    except (NonRetryableError, Exception) as exc:
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

    # RF-03: Excel (openpyxl) + PDF (WeasyPrint), guardados como Adjuntos
    # (issue #30). Un fallo aca NO revierte el calculo ya confirmado (el
    # `job_status` ya quedo `completed`/`with_errors` arriba) -- se loguea
    # y Sentry lo captura, pero la liquidacion sigue siendo consultable/
    # emitible sin sus exports (`GET /settlements/:id/export` responde
    # `404 NOT_FOUND` hasta que un reintento manual los genere).
    await _generate_and_store_exports(
        settlement_id, organization_id, uploaded_by=updated.generated_by, request_id=request_id
    )


async def _generate_and_store_exports(
    settlement_id: UUID, organization_id: UUID, *, uploaded_by: UUID, request_id: str
) -> None:
    """RF-03/RF-04: arma el Excel y el PDF de la liquidacion (agrupados
    por propiedad con subtotales, consolidado al final) y los guarda como
    Adjuntos (`shared/attachments/` + `shared/storage/`, issue #26) --
    consumidos por `GET /settlements/:id/export?format=xlsx|pdf` y
    listados en `GET /settlements/:id.attachments`. Sesion NUEVA (el
    calculo ya se comiteo): un fallo en la generacion de documentos no
    debe poder revertir totales ya persistidos."""
    try:
        async with tenant_scoped_session(organization_id) as session:
            repo = SettlementRepository(session)
            settlement = await repo.get_by_id(settlement_id, organization_id)
            if settlement is None:  # pragma: no cover -- defensivo
                return

            line_items = await repo.list_line_items(settlement_id, organization_id)
            landlord_name = await repo.get_landlord_name(settlement.landlord_id, organization_id)
            property_ids = {item.property_id for item in line_items if item.property_id is not None}
            property_labels = await repo.list_property_labels(list(property_ids), organization_id)
            property_groups, general_items = group_line_items_by_property(
                line_items, property_labels
            )

            admin_repo = AdministracionRepository(session)
            settings = await admin_repo.get_organization_settings(organization_id)
            billing_header = (settings or {}).get("billing_header") or {}

            pdf_bytes = build_settlement_pdf(
                settlement=settlement,
                landlord_name=landlord_name or str(settlement.landlord_id),
                property_groups=property_groups,
                general_items=general_items,
                billing_header=billing_header,
            )
            xlsx_bytes = build_settlement_workbook(
                settlement=settlement,
                landlord_name=landlord_name or str(settlement.landlord_id),
                property_groups=property_groups,
                general_items=general_items,
            )

            attachment_repo = AttachmentRepository(session)
            for content, content_type, suffix in (
                (
                    xlsx_bytes,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "xlsx",
                ),
                (pdf_bytes, "application/pdf", "pdf"),
            ):
                file_path, _generated_file_name = save_attachment(
                    organization_id=organization_id,
                    entity_type="settlement",
                    content=content,
                    content_type=content_type,
                )
                await attachment_repo.insert(
                    organization_id=organization_id,
                    entity_type="settlement",
                    entity_id=settlement_id,
                    file_path=file_path,
                    file_name=f"liquidacion-{settlement_id}.{suffix}",
                    mime_type=content_type,
                    size_bytes=len(content),
                    uploaded_by=uploaded_by,
                )
            await session.commit()
    except Exception as exc:
        logger.error(
            "generate_settlement exports failed",
            exc_info=exc,
            extra={
                "request_id": request_id,
                "settlement_id": str(settlement_id),
                "organization_id": str(organization_id),
                "service": "documents_worker",
            },
        )


# ─── Regeneracion auditada (issue #30) ──────────────────────────────────


@celery_app.task(
    base=DocumentsTask,
    bind=True,
    name="adminprop.workers.documents_worker.regenerate_settlement",
)
def regenerate_settlement(
    self: Task,
    settlement_id: str,
    organization_id: str,
    request_id: str,
    exchange_rate: str | None,
    actor_user_id: str,
) -> None:
    """RF-03/RN-L03: recalcula `settlement_id` con los datos actuales
    (cobros anulados/agregados, cargos corregidos, TC nuevo si se paso).
    `exchange_rate`/`actor_user_id` llegan como `str` -- docs/skills/
    async-worker.md ("recibe IDs como string")."""
    logger.info(
        "regenerate_settlement start",
        extra={
            "request_id": request_id,
            "organization_id": organization_id,
            "settlement_id": settlement_id,
            "attempt": self.request.retries + 1,
            "service": "documents_worker",
        },
    )
    # issue #93: ver comentario equivalente en `generate_settlement`.
    asyncio.run(
        run_worker_coroutine(
            _regenerate_settlement_async(
                UUID(settlement_id),
                UUID(organization_id),
                request_id,
                Decimal(exchange_rate) if exchange_rate is not None else None,
                UUID(actor_user_id),
            )
        )
    )


async def _regenerate_settlement_async(
    settlement_id: UUID,
    organization_id: UUID,
    request_id: str,
    exchange_rate: Decimal | None,
    actor_user_id: UUID,
) -> None:
    await set_job_status(settlement_id, "processing")

    try:
        async with tenant_scoped_session(organization_id) as session:
            repo = SettlementRepository(session)
            settlement: Settlement | None = await repo.get_by_id(settlement_id, organization_id)
            if settlement is None:
                logger.warning(
                    "regenerate_settlement settlement not found, skipping",
                    extra={"request_id": request_id, "settlement_id": str(settlement_id)},
                )
                return

            before_totals: dict[str, Any] = {
                "total_collected": str(settlement.total_collected),
                "commission_total": str(settlement.commission_total),
                "charges_total": str(settlement.charges_total),
                "repairs_total": str(settlement.repairs_total),
                "already_settled_total": str(settlement.already_settled_total),
                "net_amount": str(settlement.net_amount),
                "regenerated_count": settlement.regenerated_count,
            }

            # RN-L04/CA-05-05: `current_settlement_id=settlement_id` --
            # las reparaciones YA vinculadas a esta liquidacion siguen
            # contando en el recalculo (ver docstring de `_REPAIRS_SQL`).
            gathered = await repo.gather_generation_data(
                settlement.landlord_id,
                organization_id,
                settlement.period,
                current_settlement_id=settlement_id,
            )
            unpaid_periods = await repo.list_unpaid_rent_periods(
                settlement.landlord_id, organization_id, settlement.period
            )
            missing_charges = await repo.list_missing_charge_entries(
                settlement.landlord_id, organization_id, settlement.period
            )

            # RN-L05: `commission_pct_used` NO se re-congela en una
            # regeneracion (el % vigente se congela solo al GENERAR,
            # RF-02) -- se reutiliza el valor ya persistido.
            effective_rate = (
                exchange_rate if exchange_rate is not None else settlement.exchange_rate
            )
            result = calculate_settlement(
                data=gathered,
                commission_pct=settlement.commission_pct_used,
                exchange_rate=effective_rate,
                unpaid_periods=unpaid_periods,
                missing_charges=missing_charges,
            )

            # RF-03: recalcula desde cero -- borra las lineas de la
            # version anterior ANTES de insertar las nuevas (RN-L04: las
            # reparaciones ya estampadas no vuelven a aparecer, ver
            # `_REPAIRS_SQL`).
            await repo.clear_line_items(settlement_id, organization_id)
            updated = await repo.apply_regeneration(
                settlement_id,
                organization_id,
                exchange_rate=exchange_rate,
                total_collected=result.total_collected,
                commission_total=result.commission_total,
                charges_total=result.charges_total,
                repairs_total=result.repairs_total,
                already_settled_total=result.already_settled_total,
                net_amount=result.net_amount,
                line_items=[item.as_dict() for item in result.line_items],
                settled_work_order_ids=result.settled_work_order_ids,
            )
            if updated is None:  # pragma: no cover -- defensivo
                return

            # CA-05-06/RN-L03: "auditoria con que cambio" -- before/after
            # de los totales, en la MISMA transaccion que el UPDATE.
            after_totals: dict[str, Any] = {
                "total_collected": str(updated.total_collected),
                "commission_total": str(updated.commission_total),
                "charges_total": str(updated.charges_total),
                "repairs_total": str(updated.repairs_total),
                "already_settled_total": str(updated.already_settled_total),
                "net_amount": str(updated.net_amount),
                "regenerated_count": updated.regenerated_count,
            }
            await audit(
                session,
                organization_id=organization_id,
                action="settlement.regenerated",
                entity_type="settlement",
                entity_id=settlement_id,
                before=before_totals,
                after=after_totals,
                user_id=actor_user_id,
                request_id=request_id,
            )
            # `tenant_scoped_session` comitea al salir del bloque sin
            # excepcion.

    except RetryableError:
        await set_job_status(settlement_id, "pending")
        raise
    except (NonRetryableError, Exception) as exc:
        # A diferencia de `generate_settlement`, un fallo real ACA no
        # borra nada (RN-L03: nunca se borra una liquidacion ya generada
        # -- la version anterior de sus totales/lineas sigue intacta si
        # la excepcion se disparo antes del `clear_line_items`/
        # `apply_regeneration`, y si se disparo despues, el rollback de
        # `tenant_scoped_session` revierte esos cambios igual).
        logger.error(
            "regenerate_settlement failed",
            exc_info=exc,
            extra={
                "request_id": request_id,
                "settlement_id": str(settlement_id),
                "organization_id": str(organization_id),
                "service": "documents_worker",
            },
        )
        await set_job_status(settlement_id, "failed", warnings=[str(exc)])
        return

    job_status = "with_errors" if result.warnings else "completed"
    await set_job_status(settlement_id, job_status, warnings=result.warnings)
    logger.info(
        "regenerate_settlement done",
        extra={
            "request_id": request_id,
            "settlement_id": str(settlement_id),
            "job_status": job_status,
            "warning_count": len(result.warnings),
            "service": "documents_worker",
        },
    )

    # RF-03: los exports viejos quedan (nunca se borran, RN-L03) y se
    # agrega una version nueva -- `export_settlement` (router) sirve
    # siempre la mas reciente (`list_by_entity` ordena `created_at asc`).
    await _generate_and_store_exports(
        settlement_id, organization_id, uploaded_by=actor_user_id, request_id=request_id
    )
