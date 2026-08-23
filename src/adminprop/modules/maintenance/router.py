"""Endpoints /v1/work-orders/*, /v1/quotes/*, /v1/attachments/:id/download
y /v1/properties/:id/work-orders (issue #26).

SDD: core/sdd_03_api_contracts.md §12 "Mantenimiento".
Implements: CA-06-01..07.

Permisos por endpoint (sdd_03 §12 + §"Catalogo de Permisos" +
`modules/superadmin/provisioning.py.MAINTENANCE_PERMISSIONS`):
`work-order:read` (los 3 roles) / `work-order:create` (owner/admin,
maintenance NO lo tiene) / `work-order:quote` / `work-order:approve`
(owner/admin) / `work-order:close` / `work-order:cancel` (owner/admin) /
`attachment:manage`.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status

from adminprop.modules.maintenance.repository import (
    WorkOrderQuoteRepository,
    WorkOrderWithAddress,
    get_work_order_quote_repository,
)
from adminprop.modules.maintenance.schemas import (
    PropertyWorkOrderHistoryEntry,
    PropertyWorkOrderHistoryResponse,
    WorkOrderApproveResponse,
    WorkOrderCancelRequest,
    WorkOrderCloseRequest,
    WorkOrderCreate,
    WorkOrderDetail,
    WorkOrderDetailResponse,
    WorkOrderListResponse,
    WorkOrderQuoteCreate,
    WorkOrderQuoteResponse,
    WorkOrderQuoteSummary,
    WorkOrderResponse,
    WorkOrderSummary,
)
from adminprop.modules.maintenance.service import (
    WorkOrderQuoteService,
    WorkOrderService,
    get_work_order_quote_service,
    get_work_order_service,
)
from adminprop.shared.auth.jwt import JWTPayload
from adminprop.shared.errors.codes import NotFoundException
from adminprop.shared.logging.json_logger import request_id_var
from adminprop.shared.rbac import requires_permission
from adminprop.shared.tenant import get_current_tenant

work_orders_router = APIRouter(prefix="/v1/work-orders", tags=["maintenance"])
quotes_router = APIRouter(prefix="/v1/quotes", tags=["maintenance"])
attachments_router = APIRouter(prefix="/v1/attachments", tags=["maintenance"])
property_work_orders_router = APIRouter(prefix="/v1/properties", tags=["maintenance"])


def _to_summary(row: WorkOrderWithAddress) -> WorkOrderSummary:
    return WorkOrderSummary.model_validate(row, from_attributes=True)


# ─── Pedidos de reparacion — RF-01, RF-06 ─────────────────────────────────


@work_orders_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkOrderResponse,
    dependencies=[Depends(requires_permission("work-order:create"))],
)
async def create_work_order(
    dto: WorkOrderCreate,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("work-order:create")),
    service: WorkOrderService = Depends(get_work_order_service),
) -> WorkOrderResponse:
    """RF-01/CA-06-01: crea el pedido -- `payer` obligatorio, notifica a
    `maintenance`."""
    work_order = await service.create(
        organization_id=organization_id,
        property_id=dto.property_id,
        title=dto.title,
        description=dto.description,
        payer=dto.payer,
        actor_user_id=payload.sub,
        request_id=request_id_var.get(),
    )
    detail = await service.get_detail(work_order.id, organization_id)
    if detail is None:  # pragma: no cover -- defensivo, recien creado en la misma sesion
        raise NotFoundException()
    return WorkOrderResponse(data=WorkOrderSummary.model_validate(detail))


@work_orders_router.get(
    "",
    response_model=WorkOrderListResponse,
    dependencies=[Depends(requires_permission("work-order:read"))],
)
async def list_work_orders(
    status_filter: str | None = Query(default=None, alias="status"),
    property_id: UUID | None = Query(default=None),
    organization_id: UUID = Depends(get_current_tenant),
    service: WorkOrderService = Depends(get_work_order_service),
) -> WorkOrderListResponse:
    """sdd_03 §12: `?status=&property_id=` -- CA-06-01: "maintenance ve
    todos los de la org"."""
    items = await service.list(
        organization_id=organization_id, status=status_filter, property_id=property_id
    )
    return WorkOrderListResponse(data=[_to_summary(i) for i in items], meta={})


@work_orders_router.get(
    "/{work_order_id}",
    response_model=WorkOrderDetailResponse,
    dependencies=[Depends(requires_permission("work-order:read"))],
)
async def get_work_order(
    work_order_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: WorkOrderService = Depends(get_work_order_service),
) -> WorkOrderDetailResponse:
    """RF-02: pedido + cotizaciones + adjuntos."""
    detail = await service.get_detail(work_order_id, organization_id)
    if detail is None:
        # RN-D01: 404, no 403 -- no distingue "no existe" de "otra org".
        raise NotFoundException()
    return WorkOrderDetailResponse(data=WorkOrderDetail.model_validate(detail))


@work_orders_router.post(
    "/{work_order_id}/close",
    response_model=WorkOrderResponse,
    dependencies=[Depends(requires_permission("work-order:close"))],
)
async def close_work_order(
    work_order_id: UUID,
    dto: WorkOrderCloseRequest,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("work-order:close")),
    service: WorkOrderService = Depends(get_work_order_service),
) -> WorkOrderResponse:
    """RF-04/CA-06-04: cierra con fotos (adjuntos aparte) y costo final."""
    await service.close(
        work_order_id,
        organization_id,
        final_cost=dto.final_cost,
        actor_user_id=payload.sub,
        request_id=request_id_var.get(),
    )
    detail = await service.get_detail(work_order_id, organization_id)
    if detail is None:  # pragma: no cover -- defensivo, ya validado arriba
        raise NotFoundException()
    return WorkOrderResponse(data=WorkOrderSummary.model_validate(detail))


@work_orders_router.post(
    "/{work_order_id}/cancel",
    response_model=WorkOrderResponse,
    dependencies=[Depends(requires_permission("work-order:cancel"))],
)
async def cancel_work_order(
    work_order_id: UUID,
    dto: WorkOrderCancelRequest,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("work-order:cancel")),
    service: WorkOrderService = Depends(get_work_order_service),
) -> WorkOrderResponse:
    """RF-05/CA-06-07: cancela con motivo -- `closed` (aproximado a
    "liquidado") -> 422 WORK_ORDER_ALREADY_SETTLED."""
    await service.cancel(
        work_order_id, organization_id, reason=dto.reason, actor_user_id=payload.sub
    )
    detail = await service.get_detail(work_order_id, organization_id)
    if detail is None:  # pragma: no cover -- defensivo, ya validado arriba
        raise NotFoundException()
    return WorkOrderResponse(data=WorkOrderSummary.model_validate(detail))


@work_orders_router.post(
    "/{work_order_id}/attachments",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkOrderResponse,
    dependencies=[Depends(requires_permission("attachment:manage"))],
)
async def upload_work_order_attachment(
    work_order_id: UUID,
    file: UploadFile = File(...),
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("attachment:manage")),
    service: WorkOrderService = Depends(get_work_order_service),
) -> WorkOrderResponse:
    """RF-01/RF-04: fotos del pedido (alta) o del cierre -- mismo
    endpoint, sin distincion de "fase" (RN-05: hereda permisos del
    pedido)."""
    content = await file.read()
    await service.upload_work_order_attachment(
        work_order_id,
        organization_id,
        content=content,
        content_type=file.content_type or "application/octet-stream",
        actor_user_id=payload.sub,
    )
    detail = await service.get_detail(work_order_id, organization_id)
    if detail is None:  # pragma: no cover -- defensivo, ya validado arriba
        raise NotFoundException()
    return WorkOrderResponse(data=WorkOrderSummary.model_validate(detail))


# ─── Cotizaciones — RF-02, RF-03 ───────────────────────────────────────────


@work_orders_router.post(
    "/{work_order_id}/quotes",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkOrderQuoteResponse,
    dependencies=[Depends(requires_permission("work-order:quote"))],
)
async def add_quote(
    work_order_id: UUID,
    dto: WorkOrderQuoteCreate,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("work-order:quote")),
    service: WorkOrderQuoteService = Depends(get_work_order_quote_service),
) -> WorkOrderQuoteResponse:
    """RF-02/CA-06-02: sube una cotizacion -- notifica a owner+admin."""
    quote = await service.add_quote(
        work_order_id,
        organization_id,
        amount=dto.amount,
        description=dto.description,
        actor_user_id=payload.sub,
        request_id=request_id_var.get(),
    )
    return WorkOrderQuoteResponse(data=WorkOrderQuoteSummary.model_validate(quote))


@quotes_router.post(
    "/{quote_id}/approve",
    response_model=WorkOrderApproveResponse,
    dependencies=[Depends(requires_permission("work-order:approve"))],
)
async def approve_quote(
    quote_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("work-order:approve")),
    quote_service: WorkOrderQuoteService = Depends(get_work_order_quote_service),
    work_order_service: WorkOrderService = Depends(get_work_order_service),
) -> WorkOrderApproveResponse:
    """RF-03/CA-06-03: aprueba una cotizacion -- `open -> in_progress`,
    las demas quedan `discarded`. Reaprobar -> 409 QUOTE_ALREADY_APPROVED.
    Issue #31: notifica `quote_approved` al encargado."""
    updated_work_order, _approved_quote = await quote_service.approve(
        quote_id, organization_id, actor_user_id=payload.sub, request_id=request_id_var.get()
    )
    detail = await work_order_service.get_detail(updated_work_order.id, organization_id)
    if detail is None:  # pragma: no cover -- defensivo, ya validado arriba
        raise NotFoundException()
    return WorkOrderApproveResponse(data=WorkOrderDetail.model_validate(detail))


@quotes_router.post(
    "/{quote_id}/attachments",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkOrderQuoteResponse,
    dependencies=[Depends(requires_permission("attachment:manage"))],
)
async def upload_quote_attachment(
    quote_id: UUID,
    file: UploadFile = File(...),
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("attachment:manage")),
    service: WorkOrderQuoteService = Depends(get_work_order_quote_service),
    quote_repo: WorkOrderQuoteRepository = Depends(get_work_order_quote_repository),
) -> WorkOrderQuoteResponse:
    """RF-02/CA-06-02: fotos de la cotizacion."""
    content = await file.read()
    await service.upload_quote_attachment(
        quote_id,
        organization_id,
        content=content,
        content_type=file.content_type or "application/octet-stream",
        actor_user_id=payload.sub,
    )
    quote = await quote_repo.get_by_id(quote_id, organization_id)
    if quote is None:  # pragma: no cover -- defensivo, ya validado arriba
        raise NotFoundException()
    return WorkOrderQuoteResponse(data=WorkOrderQuoteSummary.model_validate(quote))


# ─── Adjuntos — descarga ───────────────────────────────────────────────────


@attachments_router.get(
    "/{attachment_id}/download",
    dependencies=[Depends(requires_permission("attachment:manage"))],
)
async def download_attachment(
    attachment_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: WorkOrderService = Depends(get_work_order_service),
) -> Response:
    """RN-05: descarga binaria del adjunto -- hereda permisos del pedido
    (cualquiera con `attachment:manage`)."""
    content, mime_type, file_name = await service.download_attachment(
        attachment_id, organization_id
    )
    return Response(
        content=content,
        media_type=mime_type,
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


# ─── Historial por propiedad — RF-06/CA-06-05 ─────────────────────────────


@property_work_orders_router.get(
    "/{property_id}/work-orders",
    response_model=PropertyWorkOrderHistoryResponse,
    dependencies=[Depends(requires_permission("property:read"))],
)
async def get_property_work_order_history(
    property_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: WorkOrderService = Depends(get_work_order_service),
) -> PropertyWorkOrderHistoryResponse:
    """RF-06/CA-06-05 (UC-16): historial completo de reparaciones de la
    propiedad. Permiso `property:read` -- `maintenance` no lo tiene
    (CA-06-06, ver `modules/superadmin/provisioning.py.MAINTENANCE_PERMISSIONS`),
    consistente con RN-03 (el rol maintenance no ve la ficha de
    propietarios/propiedades fuera de sus propios pedidos)."""
    items = await service.history_by_property(property_id, organization_id)
    return PropertyWorkOrderHistoryResponse(
        data=[PropertyWorkOrderHistoryEntry.model_validate(i, from_attributes=True) for i in items]
    )
