"""Logica de negocio del ciclo de mantenimiento (issue #26, cierre en el #31).

SDD: docs/sdd/features/spec_module_06_mantenimiento.md §RF-01..RF-06.
Implements: CA-06-01 (alta + notificacion a maintenance), CA-06-02
(cotizaciones + notificacion a owner/admin), CA-06-03 (aprobacion,
RN-02, notificacion `quote_approved` al encargado), CA-06-04 (cierre +
notificacion), CA-06-05 (historial por propiedad), CA-06-07 (RN-04,
bloqueo de cancelacion/reapertura via
`settlement_hook.is_work_order_settled`).

CA-06-06 (accesos denegados del rol maintenance a otros modulos) NO se
implementa aca: ya lo enforza `shared/rbac.requires_permission` +
`shared/audit.record_access_denied` (RN-A04), reutilizados sin cambios
-- este modulo solo aporta los tests que lo verifican contra los otros
routers (ver "Decisiones de implementacion" del PR).

Issue #31 cierra la brecha CA-06-03 documentada en el #26 (decision
#115, spec_notificaciones.md v1.1): la migracion
`20260821_100000_add_quote_approved_to_notifications.py` agrega el
sexto valor `quote_approved` al CHECK de `notifications.event_type`, y
`approve()` ahora llama a `notifications.emit(...)` para el encargado
(usuarios `maintenance`, `EVENT_RECIPIENT_ROLES` en
`shared/notifications/service.py`) en la MISMA transaccion que la
aprobacion (RF-01), ademas de seguir auditando (`audit()`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import Depends

from adminprop.modules.maintenance.exceptions import (
    QuoteAlreadyApprovedException,
    WorkOrderAlreadyClosedException,
    WorkOrderAlreadySettledException,
)
from adminprop.modules.maintenance.models import WorkOrder, WorkOrderQuote
from adminprop.modules.maintenance.repository import (
    WorkOrderQuoteRepository,
    WorkOrderRepository,
    WorkOrderWithAddress,
    get_work_order_quote_repository,
    get_work_order_repository,
)
from adminprop.modules.maintenance.settlement_hook import is_work_order_settled
from adminprop.shared.attachments.models import Attachment
from adminprop.shared.attachments.repository import (
    AttachmentRepository,
    get_attachment_repository,
)
from adminprop.shared.audit.service import audit
from adminprop.shared.errors.codes import (
    InvalidStatusTransitionException,
    NotFoundException,
    ValidationError,
)
from adminprop.shared.notifications import service as notifications
from adminprop.shared.storage.local import (
    MAX_ATTACHMENTS_PER_ENTITY,
    AttachmentTooLargeError,
    UnsupportedAttachmentTypeError,
    read_attachment,
    save_attachment,
)

# RF-05: "Owner/admin cancelan un pedido open o in_progress" -- solo
# estos dos estados son candidatos a cancelacion.
_CANCELLABLE_STATUSES: tuple[str, ...] = ("open", "in_progress")
# RF-02: "Un pedido open puede acumular cotizaciones" -- una vez
# aprobada una (in_progress) o cerrado/cancelado, no se aceptan mas.
_QUOTABLE_STATUSES: tuple[str, ...] = ("open",)


@dataclass(frozen=True)
class WorkOrderDetailData:
    """Agregado de `WorkOrderWithAddress` + sus cotizaciones/adjuntos --
    consumido por `schemas.WorkOrderDetail.model_validate(...)`."""

    id: UUID
    property_id: UUID
    property_address: str
    title: str
    description: str | None
    payer: str
    status: str
    final_cost: Decimal | None
    approved_quote_id: UUID | None
    created_by: UUID
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    quotes: list[WorkOrderQuote]
    attachments: list[Attachment]


def _to_detail(
    row: WorkOrderWithAddress, quotes: list[WorkOrderQuote], attachments: list[Attachment]
) -> WorkOrderDetailData:
    return WorkOrderDetailData(
        id=row.id,
        property_id=row.property_id,
        property_address=row.property_address,
        title=row.title,
        description=row.description,
        payer=row.payer,
        status=row.status,
        final_cost=row.final_cost,
        approved_quote_id=row.approved_quote_id,
        created_by=row.created_by,
        closed_at=row.closed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        quotes=quotes,
        attachments=attachments,
    )


class WorkOrderService:
    """RF-01/RF-04/RF-05/RF-06 -- ciclo del pedido (alta, cierre,
    cancelacion, historial, adjuntos propios)."""

    def __init__(
        self,
        repo: WorkOrderRepository,
        quote_repo: WorkOrderQuoteRepository,
        attachment_repo: AttachmentRepository,
    ) -> None:
        self._repo = repo
        self._quote_repo = quote_repo
        self._attachment_repo = attachment_repo

    async def create(
        self,
        *,
        organization_id: UUID,
        property_id: UUID,
        title: str,
        description: str | None,
        payer: str,
        actor_user_id: UUID,
        request_id: str,
    ) -> WorkOrder:
        """RF-01/CA-06-01: crea el pedido `open` y notifica a los
        usuarios `maintenance` de la organizacion (`work_order_created`,
        RN-01 de spec_notificaciones.md)."""
        # RN-D01: 404 (no 500 por FK violation) si la propiedad no existe
        # o es de otro tenant.
        if not await self._repo.property_exists(property_id, organization_id):
            raise NotFoundException()

        work_order = await self._repo.insert(
            organization_id=organization_id,
            property_id=property_id,
            title=title,
            description=description,
            payer=payer,
            created_by=actor_user_id,
        )

        notification_ids = await notifications.emit(
            self._repo.session,
            organization_id=organization_id,
            event_type="work_order_created",
            payload={"work_order_id": str(work_order.id), "property_id": str(property_id)},
        )

        await self._repo.commit()
        # RF-01 "patron outbox simple": el email se encola DESPUES del
        # commit (mismo patron que `administracion.UserService.invite`).
        notifications.enqueue_pending_emails(
            notification_ids, organization_id=organization_id, request_id=request_id
        )
        return work_order

    async def get_detail(
        self, work_order_id: UUID, organization_id: UUID
    ) -> WorkOrderDetailData | None:
        """RF-02: `GET /work-orders/:id` con cotizaciones + adjuntos."""
        row = await self._repo.get_with_address(work_order_id, organization_id)
        if row is None:
            return None
        quotes = await self._quote_repo.list_by_work_order(work_order_id, organization_id)
        attachments = await self._attachment_repo.list_by_entity(
            entity_type="work_order", entity_id=work_order_id, organization_id=organization_id
        )
        return _to_detail(row, quotes, attachments)

    async def list(
        self,
        *,
        organization_id: UUID,
        status: str | None,
        property_id: UUID | None,
    ) -> list[WorkOrderWithAddress]:
        """sdd_03 §12: `GET /work-orders?status=&property_id=` -- CA-06-01:
        "maintenance ve todos los de la org" (mismo listado para los tres
        roles, el permiso `work-order:read` ya los habilita a todos)."""
        return await self._repo.list_with_address(
            organization_id=organization_id, status=status, property_id=property_id
        )

    async def history_by_property(
        self, property_id: UUID, organization_id: UUID
    ) -> list[WorkOrderWithAddress]:
        """RF-06/CA-06-05: `GET /properties/:id/work-orders`."""
        if not await self._repo.property_exists(property_id, organization_id):
            raise NotFoundException()
        return await self._repo.history_by_property(property_id, organization_id)

    async def close(
        self,
        work_order_id: UUID,
        organization_id: UUID,
        *,
        final_cost: Decimal | None,
        actor_user_id: UUID,
        request_id: str,
    ) -> WorkOrder:
        """RF-04/CA-06-04: cierra el trabajo -- `final_cost` ajustable
        (default: el monto de la cotizacion aprobada). RN-01: el efecto
        en la liquidacion (`payer=agency` -> pendiente de liquidar;
        `payer=landlord` -> solo historial) es responsabilidad del
        Modulo 5 (issue #27); este service solo persiste `payer`/
        `final_cost`, ya suficientes para que ese modulo futuro los
        consuma."""
        work_order = await self._repo.get_by_id(work_order_id, organization_id)
        if work_order is None:
            raise NotFoundException()
        if work_order.status == "closed":
            # RF-04: "Cerrar un pedido ya cerrado -> 409".
            raise WorkOrderAlreadyClosedException()
        if work_order.status not in ("open", "in_progress"):
            # cancelado -- no es un flujo de cierre valido (RF-05, el
            # pedido ya es terminal por otra via).
            raise InvalidStatusTransitionException()

        resolved_final_cost = final_cost
        if resolved_final_cost is None:
            if work_order.approved_quote_id is None:
                # RF-04: "default: el monto de la cotizacion aprobada" --
                # sin cotizacion aprobada ni final_cost explicito no hay
                # de donde derivar el costo.
                raise ValidationError(
                    field="final_cost",
                    message=(
                        "final_cost es obligatorio: el pedido no tiene una cotizacion "
                        "aprobada de la cual tomar el monto por defecto."
                    ),
                )
            approved_quote = await self._quote_repo.get_by_id(
                work_order.approved_quote_id, organization_id
            )
            resolved_final_cost = (
                approved_quote.amount if approved_quote is not None else Decimal("0.00")
            )

        # SQLAlchemy identity map: `mark_closed` re-consulta el MISMO
        # WorkOrder por PK dentro de la misma sesion y devuelve el MISMO
        # objeto Python que `work_order` (no una copia) -- mutarlo ahi
        # mutaria `work_order.status` en el lugar. Se congela el status
        # ORIGINAL antes de llamar a `mark_closed` para que el "before"
        # del audit no quede pisado por el "after".
        status_before_close = work_order.status

        closed_at = datetime.now(tz=UTC)
        updated = await self._repo.mark_closed(
            work_order_id, organization_id, final_cost=resolved_final_cost, closed_at=closed_at
        )
        assert updated is not None  # ya validamos existencia arriba

        notification_ids = await notifications.emit(
            self._repo.session,
            organization_id=organization_id,
            event_type="work_order_closed",
            payload={
                "work_order_id": str(work_order_id),
                "payer": work_order.payer,
                "final_cost": str(resolved_final_cost),
            },
        )
        await audit(
            self._repo.session,
            organization_id=organization_id,
            action="work_order.closed",
            entity_type="work_order",
            entity_id=work_order_id,
            before={"status": status_before_close},
            after={"status": "closed", "final_cost": str(resolved_final_cost)},
            user_id=actor_user_id,
        )

        await self._repo.commit()
        notifications.enqueue_pending_emails(
            notification_ids, organization_id=organization_id, request_id=request_id
        )
        return updated

    async def cancel(
        self,
        work_order_id: UUID,
        organization_id: UUID,
        *,
        reason: str,
        actor_user_id: UUID,
    ) -> WorkOrder:
        """RF-05: cancela un pedido `open`/`in_progress` con motivo
        (auditado, sin columna dedicada). CA-06-07: un pedido `closed`
        (aproximacion de "ya liquidado", ver `settlement_hook.py`) no
        puede cancelarse ni reabrirse -> 422 WORK_ORDER_ALREADY_SETTLED."""
        work_order = await self._repo.get_by_id(work_order_id, organization_id)
        if work_order is None:
            raise NotFoundException()
        if is_work_order_settled(work_order):
            # CA-06-07.
            raise WorkOrderAlreadySettledException()
        if work_order.status not in _CANCELLABLE_STATUSES:
            # `cancelled` ya es terminal -- no es el caso "settled" de
            # CA-06-07, pero tampoco un flujo valido.
            raise InvalidStatusTransitionException()

        # Mismo motivo que en `close()`: `mark_cancelled` muta el MISMO
        # objeto `work_order` (identity map) -- congelar el status
        # original antes de llamarlo.
        status_before_cancel = work_order.status

        updated = await self._repo.mark_cancelled(work_order_id, organization_id)
        assert updated is not None  # ya validamos existencia arriba

        await audit(
            self._repo.session,
            organization_id=organization_id,
            action="work_order.cancelled",
            entity_type="work_order",
            entity_id=work_order_id,
            before={"status": status_before_cancel},
            after={"status": "cancelled", "reason": reason},
            user_id=actor_user_id,
        )
        await self._repo.commit()
        return updated

    # ─── Adjuntos (RN-05) ────────────────────────────────────────────────

    async def upload_work_order_attachment(
        self,
        work_order_id: UUID,
        organization_id: UUID,
        *,
        content: bytes,
        content_type: str,
        actor_user_id: UUID,
    ) -> Attachment:
        work_order = await self._repo.get_by_id(work_order_id, organization_id)
        if work_order is None:
            raise NotFoundException()
        return await _store_attachment(
            self._attachment_repo,
            organization_id=organization_id,
            entity_type="work_order",
            entity_id=work_order_id,
            content=content,
            content_type=content_type,
            uploaded_by=actor_user_id,
        )

    async def download_attachment(
        self, attachment_id: UUID, organization_id: UUID
    ) -> tuple[bytes, str, str]:
        """RN-05: cualquier usuario con `attachment:manage` (owner/admin/
        maintenance) puede descargar -- el permiso ya filtra el acceso,
        RN-D01 filtra el tenant."""
        attachment = await self._attachment_repo.get_by_id(attachment_id, organization_id)
        if attachment is None:
            raise NotFoundException()
        content = read_attachment(attachment.file_path)
        return content, attachment.mime_type, attachment.file_name


class WorkOrderQuoteService:
    """RF-02 (cotizaciones) + RF-03 (aprobacion)."""

    def __init__(
        self,
        quote_repo: WorkOrderQuoteRepository,
        work_order_repo: WorkOrderRepository,
        attachment_repo: AttachmentRepository,
    ) -> None:
        self._quote_repo = quote_repo
        self._work_order_repo = work_order_repo
        self._attachment_repo = attachment_repo

    async def add_quote(
        self,
        work_order_id: UUID,
        organization_id: UUID,
        *,
        amount: Decimal,
        description: str | None,
        actor_user_id: UUID,
        request_id: str,
    ) -> WorkOrderQuote:
        """RF-02/CA-06-02: sube una cotizacion -- notifica a owner+admin
        (`quote_submitted`) por CADA cotizacion nueva."""
        work_order = await self._work_order_repo.get_by_id(work_order_id, organization_id)
        if work_order is None:
            raise NotFoundException()
        if work_order.status not in _QUOTABLE_STATUSES:
            # RF-02: "Un pedido open puede acumular cotizaciones" --
            # in_progress/closed/cancelled ya no aceptan mas.
            raise InvalidStatusTransitionException()

        quote = await self._quote_repo.insert(
            organization_id=organization_id,
            work_order_id=work_order_id,
            amount=amount,
            description=description,
            submitted_by=actor_user_id,
        )

        notification_ids = await notifications.emit(
            self._quote_repo.session,
            organization_id=organization_id,
            event_type="quote_submitted",
            payload={
                "work_order_id": str(work_order_id),
                "quote_id": str(quote.id),
                "amount": str(amount),
            },
        )
        await self._quote_repo.commit()
        notifications.enqueue_pending_emails(
            notification_ids, organization_id=organization_id, request_id=request_id
        )
        return quote

    async def approve(
        self,
        quote_id: UUID,
        organization_id: UUID,
        *,
        actor_user_id: UUID,
        request_id: str,
    ) -> tuple[WorkOrder, WorkOrderQuote]:
        """RF-03/CA-06-03: aprueba UNA cotizacion -- el pedido pasa a
        `in_progress`, las demas quedan `discarded` (RN-02). Reaprobar
        (misma u otra cotizacion del mismo pedido) -> 409
        QUOTE_ALREADY_APPROVED.

        Issue #31: notifica al encargado (`quote_approved`, usuarios
        `maintenance` de la organizacion) ademas de auditar -- mismo
        patron que `create()`/`close()`/`add_quote()` (emit en la misma
        transaccion, enqueue del email DESPUES del commit).
        """
        quote = await self._quote_repo.get_by_id(quote_id, organization_id)
        if quote is None:
            raise NotFoundException()
        work_order = await self._work_order_repo.get_by_id(quote.work_order_id, organization_id)
        if work_order is None:  # pragma: no cover -- defensivo, integridad referencial de la DB
            raise NotFoundException()

        if quote.status == "approved" or await self._quote_repo.has_approved(
            quote.work_order_id, organization_id
        ):
            # RF-03: "Aprobar sobre un pedido que ya tiene aprobada ->
            # 409" -- cubre tanto re-aprobar la misma cotizacion como
            # aprobar una distinta cuando ya hay una `approved`.
            raise QuoteAlreadyApprovedException()

        approved_quote = await self._quote_repo.approve(quote_id, organization_id)
        assert approved_quote is not None  # ya validamos existencia arriba
        await self._quote_repo.discard_others(
            quote.work_order_id, organization_id, except_quote_id=quote_id
        )
        updated_work_order = await self._work_order_repo.mark_in_progress(
            quote.work_order_id,
            organization_id,
            approved_quote_id=quote_id,
            final_cost=approved_quote.amount,
        )
        assert updated_work_order is not None

        await audit(
            self._quote_repo.session,
            organization_id=organization_id,
            action="work_order_quote.approved",
            entity_type="work_order_quote",
            entity_id=quote_id,
            before={"status": "submitted"},
            after={"status": "approved", "work_order_status": "in_progress"},
            user_id=actor_user_id,
        )

        notification_ids = await notifications.emit(
            self._quote_repo.session,
            organization_id=organization_id,
            event_type="quote_approved",
            payload={
                "work_order_id": str(quote.work_order_id),
                "quote_id": str(quote_id),
            },
        )
        await self._quote_repo.commit()
        notifications.enqueue_pending_emails(
            notification_ids, organization_id=organization_id, request_id=request_id
        )
        return updated_work_order, approved_quote

    async def upload_quote_attachment(
        self,
        quote_id: UUID,
        organization_id: UUID,
        *,
        content: bytes,
        content_type: str,
        actor_user_id: UUID,
    ) -> Attachment:
        quote = await self._quote_repo.get_by_id(quote_id, organization_id)
        if quote is None:
            raise NotFoundException()
        return await _store_attachment(
            self._attachment_repo,
            organization_id=organization_id,
            entity_type="work_order_quote",
            entity_id=quote_id,
            content=content,
            content_type=content_type,
            uploaded_by=actor_user_id,
        )


async def _store_attachment(
    attachment_repo: AttachmentRepository,
    *,
    organization_id: UUID,
    entity_type: str,
    entity_id: UUID,
    content: bytes,
    content_type: str,
    uploaded_by: UUID,
) -> Attachment:
    """RN-05 + spec_module_06_mantenimiento.md §Validaciones: valida el
    limite de "<= 10 por entidad" ANTES de guardar en disco (evita
    escribir un archivo huerfano si el limite ya se alcanzo)."""
    existing_count = await attachment_repo.count_by_entity(
        entity_type=entity_type, entity_id=entity_id, organization_id=organization_id
    )
    if existing_count >= MAX_ATTACHMENTS_PER_ENTITY:
        raise ValidationError(
            field="file",
            message=f"Se alcanzo el maximo de {MAX_ATTACHMENTS_PER_ENTITY} adjuntos por entidad.",
        )

    try:
        file_path, file_name = save_attachment(
            organization_id=organization_id,
            entity_type=entity_type,
            content=content,
            content_type=content_type,
        )
    except UnsupportedAttachmentTypeError as exc:
        raise ValidationError(
            field="file",
            message="Tipo de archivo no soportado: solo jpg/png/webp/pdf.",
        ) from exc
    except AttachmentTooLargeError as exc:
        raise ValidationError(
            field="file", message="El archivo supera el tamano maximo de 10 MB."
        ) from exc

    attachment = await attachment_repo.insert(
        organization_id=organization_id,
        entity_type=entity_type,
        entity_id=entity_id,
        file_path=file_path,
        file_name=file_name,
        mime_type=content_type,
        size_bytes=len(content),
        uploaded_by=uploaded_by,
    )
    await attachment_repo.commit()
    return attachment


def get_work_order_service(
    repo: WorkOrderRepository = Depends(get_work_order_repository),
    quote_repo: WorkOrderQuoteRepository = Depends(get_work_order_quote_repository),
    attachment_repo: AttachmentRepository = Depends(get_attachment_repository),
) -> WorkOrderService:
    return WorkOrderService(repo, quote_repo, attachment_repo)


def get_work_order_quote_service(
    quote_repo: WorkOrderQuoteRepository = Depends(get_work_order_quote_repository),
    work_order_repo: WorkOrderRepository = Depends(get_work_order_repository),
    attachment_repo: AttachmentRepository = Depends(get_attachment_repository),
) -> WorkOrderQuoteService:
    return WorkOrderQuoteService(quote_repo, work_order_repo, attachment_repo)
