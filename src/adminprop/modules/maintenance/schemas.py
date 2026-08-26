"""Pydantic schemas del modulo mantenimiento -- PascalCase singular (issue #26).

SDD: docs/sdd/features/spec_module_06_mantenimiento.md §RF-01..RF-06 +
core/sdd_03_api_contracts.md §12 "Mantenimiento".
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# spec_module_06_mantenimiento.md §RF-01: "pagador (landlord = Dueno /
# agency = Administracion)".
WorkOrderPayer = Literal["landlord", "agency"]
# spec_data_model.md §Capa 5 "work_orders.status".
WorkOrderStatusLiteral = Literal["open", "in_progress", "closed", "cancelled"]
# spec_data_model.md §Capa 5 "work_order_quotes.status".
WorkOrderQuoteStatusLiteral = Literal["submitted", "approved", "discarded"]

# spec_module_06_mantenimiento.md §Validaciones: "title: 3-200 caracteres".
_TITLE_MIN_LENGTH = 3
_TITLE_MAX_LENGTH = 200


# ─── Pedidos de reparacion (work_orders) — RF-01, RF-06 ──────────────────


class WorkOrderCreate(BaseModel):
    """Body de POST /v1/work-orders. RF-01: "propiedad, titulo,
    descripcion, pagador ... y fotos opcionales" -- las fotos van por
    `POST /work-orders/:id/attachments` (multipart), no en este body."""

    model_config = ConfigDict(extra="forbid")

    property_id: UUID = Field(...)
    title: str = Field(..., min_length=_TITLE_MIN_LENGTH, max_length=_TITLE_MAX_LENGTH)
    description: str | None = Field(default=None)
    payer: WorkOrderPayer = Field(...)


class WorkOrderSummary(BaseModel):
    """Item de GET /v1/work-orders y base de WorkOrderDetail -- CA-06-01:
    "lo ve en su listado con la direccion de la propiedad". RN-03: sin
    datos de contrato/inquilino/cobros/liquidacion (el modelo de dominio
    de `WorkOrder` nunca los tuvo)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    property_id: UUID
    property_address: str
    title: str
    description: str | None
    payer: WorkOrderPayer
    status: WorkOrderStatusLiteral
    final_cost: Decimal | None
    approved_quote_id: UUID | None
    created_by: UUID
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkOrderResponse(BaseModel):
    data: WorkOrderSummary


class WorkOrderListResponse(BaseModel):
    data: list[WorkOrderSummary]
    meta: dict


class WorkOrderQuoteSummary(BaseModel):
    """RF-02: "todas quedan visibles con autor y fecha"."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_order_id: UUID
    amount: Decimal
    description: str | None
    status: WorkOrderQuoteStatusLiteral
    submitted_by: UUID
    created_at: datetime


class AttachmentSummary(BaseModel):
    """RN-05: los adjuntos heredan los permisos del pedido -- se listan
    en el detalle del pedido/cotizacion, se descargan via
    `GET /attachments/:id/download`."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_type: str
    entity_id: UUID
    file_name: str
    mime_type: str
    size_bytes: int
    uploaded_by: UUID
    created_at: datetime


class WorkOrderDetail(WorkOrderSummary):
    """GET /v1/work-orders/:id -- RF-02: cotizaciones + adjuntos del
    pedido en la misma respuesta (evita N+1 desde el frontend)."""

    quotes: list[WorkOrderQuoteSummary]
    attachments: list[AttachmentSummary]


class WorkOrderDetailResponse(BaseModel):
    data: WorkOrderDetail


# ─── Cotizaciones (work_order_quotes) — RF-02, RF-03 ─────────────────────


class WorkOrderQuoteCreate(BaseModel):
    """Body de POST /v1/work-orders/:id/quotes. Validaciones: "amount de
    cotizacion > 0"."""

    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(..., gt=0)
    description: str | None = Field(default=None)


class WorkOrderQuoteResponse(BaseModel):
    data: WorkOrderQuoteSummary


class WorkOrderApproveResponse(BaseModel):
    """Response de POST /v1/quotes/:id/approve -- el pedido pasa a
    `in_progress` (RF-03); se devuelve el pedido actualizado junto con la
    cotizacion recien aprobada."""

    data: WorkOrderDetail


# ─── Cierre y cancelacion (work_orders) — RF-04, RF-05 ───────────────────


class WorkOrderCloseRequest(BaseModel):
    """Body de POST /v1/work-orders/:id/close. RF-04: "final_cost
    ajustable (default: el monto de la cotizacion aprobada)" -- opcional,
    `None` deja que el service resuelva el default. Validaciones:
    "final_cost >= 0"."""

    model_config = ConfigDict(extra="forbid")

    final_cost: Decimal | None = Field(default=None, ge=0)


class WorkOrderCancelRequest(BaseModel):
    """Body de POST /v1/work-orders/:id/cancel. RF-05: "(con motivo)"."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1)


# ─── Historial por propiedad — RF-06/CA-06-05 ────────────────────────────


class PropertyWorkOrderHistoryEntry(BaseModel):
    """GET /v1/properties/:id/work-orders -- RF-06: "fecha, descripcion,
    estado, pagador, costo final y -- si aplica -- en que liquidacion se
    desconto". `settled_in_settlement_id` se puebla al liquidarse la
    reparacion agency de la orden (issue #29, Modulo 5) -- `None` solo
    mientras la orden no fue liquidada; ver
    `modules/maintenance/settlement_hook.py`."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    description: str | None
    status: WorkOrderStatusLiteral
    payer: WorkOrderPayer
    final_cost: Decimal | None
    closed_at: datetime | None
    created_at: datetime
    settled_in_settlement_id: UUID | None = None


class PropertyWorkOrderHistoryResponse(BaseModel):
    """RF-06: la ficha de la propiedad lista TODAS las reparaciones, sin
    paginar (volumen acotado por propiedad, mismo criterio que
    `RenterDebtResponse`)."""

    data: list[PropertyWorkOrderHistoryEntry]
