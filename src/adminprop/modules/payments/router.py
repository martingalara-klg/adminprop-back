"""Endpoints /v1/rent-periods/* y /v1/payments/*, /v1/debt de cobranzas
(issues #22/#23).

SDD: core/sdd_03_api_contracts.md §9 "Cobranzas".
Implements: CA-04-03, CA-04-04, CA-04-05, CA-04-06 (issue #22);
CA-04-07 (anulacion), CA-04-09 (deuda global) (issue #23);
CA-04-10 (recibo PDF, RF-07) (issue #24).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from adminprop.modules.payments.schemas import (
    DebtEntryData,
    DebtListResponse,
    InterestPreviewData,
    InterestPreviewResponse,
    PaymentCreate,
    PaymentDetail,
    PaymentResponse,
    PaymentVoidRequest,
    PaymentVoidResponse,
    RentPeriodListResponse,
    RentPeriodResponse,
    RentPeriodSummary,
)
from adminprop.modules.payments.service import (
    DebtService,
    PaymentService,
    RentPeriodPanelService,
    get_debt_service,
    get_payment_service,
    get_rent_period_panel_service,
)
from adminprop.shared.auth.jwt import JWTPayload
from adminprop.shared.errors.codes import NotFoundException, ValidationError
from adminprop.shared.rbac import requires_permission
from adminprop.shared.tenant import get_current_tenant

router = APIRouter(prefix="/v1/rent-periods", tags=["payments"])
payments_root_router = APIRouter(prefix="/v1/payments", tags=["payments"])
debt_router = APIRouter(prefix="/v1/debt", tags=["payments"])

# sdd_03 §9: "?period=YYYY-MM".
_PERIOD_PATTERN = re.compile(r"^\d{4}-\d{2}$")


def _parse_period(period: str | None) -> date | None:
    if period is None:
        return None
    if not _PERIOD_PATTERN.match(period):
        raise ValidationError(field="period", message="El formato de period debe ser YYYY-MM.")
    year, month = period.split("-")
    return date(int(year), int(month), 1)


@router.get(
    "/{rent_period_id}/interest-preview",
    response_model=InterestPreviewResponse,
    dependencies=[Depends(requires_permission("rent-period:read"))],
)
async def preview_interest(
    rent_period_id: UUID,
    payment_date: date = Query(...),
    organization_id: UUID = Depends(get_current_tenant),
    service: PaymentService = Depends(get_payment_service),
) -> InterestPreviewResponse:
    """sdd_03 §9 + RF-04: interes sugerido a `payment_date` -- RN-P02/P03
    (saldo impago x % de mora diaria del contrato x dias de mora; dia de
    gracia de la org, default 10; dia 11 = 1 dia de mora)."""
    preview = await service.preview_interest(rent_period_id, organization_id, payment_date)
    return InterestPreviewResponse(data=InterestPreviewData(**preview))


@router.post(
    "/{rent_period_id}/payments",
    response_model=PaymentResponse,
    status_code=201,
)
async def register_payment(
    rent_period_id: UUID,
    dto: PaymentCreate,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("payment:create")),
    service: PaymentService = Depends(get_payment_service),
) -> PaymentResponse:
    """sdd_03 §9 + RF-03/RF-04: registra el cobro -- RN-P04 (imputacion
    libre con perdon auditado, CA-04-06), RN-P05 (parciales, CA-04-04),
    RN-P06 (TC obligatorio si difiere la moneda, CA-04-03), RN-P07
    (destino)."""
    payment = await service.register_payment(
        rent_period_id,
        organization_id,
        payment_date=dto.payment_date,
        method=dto.method,
        payment_currency=dto.payment_currency,
        amount=dto.amount,
        exchange_rate=dto.exchange_rate,
        destination=dto.destination,
        charged_interest=dto.charged_interest,
        notes=dto.notes,
        actor_user_id=payload.sub,
    )
    return PaymentResponse(data=payment)


@router.get(
    "",
    response_model=RentPeriodListResponse,
    dependencies=[Depends(requires_permission("rent-period:read"))],
)
async def list_rent_periods(
    period: str | None = Query(default=None, description="YYYY-MM"),
    status: str | None = Query(default=None),
    in_arrears: bool | None = Query(default=None),
    property_id: UUID | None = Query(default=None),
    landlord_id: UUID | None = Query(default=None),
    renter_id: UUID | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    organization_id: UUID = Depends(get_current_tenant),
    service: RentPeriodPanelService = Depends(get_rent_period_panel_service),
) -> RentPeriodListResponse:
    """sdd_03 §9 + RF-02: panel de cobranzas del mes -- filtros de estado,
    `in_arrears=true`, propiedad, propietario, inquilino. Cada fila
    muestra propiedad, inquilino, monto, saldo, dias de mora e interes
    sugerido al dia de hoy."""
    entries, next_cursor = await service.list_panel(
        organization_id=organization_id,
        today=datetime.now(tz=UTC).date(),
        period=_parse_period(period),
        status=status,
        in_arrears=in_arrears,
        property_id=property_id,
        landlord_id=landlord_id,
        renter_id=renter_id,
        cursor=cursor,
        limit=limit,
    )
    return RentPeriodListResponse(
        data=[RentPeriodSummary.model_validate(e) for e in entries],
        meta={"next_cursor": next_cursor, "limit": limit},
    )


@router.get(
    "/{rent_period_id}",
    response_model=RentPeriodResponse,
    dependencies=[Depends(requires_permission("rent-period:read"))],
)
async def get_rent_period(
    rent_period_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: RentPeriodPanelService = Depends(get_rent_period_panel_service),
) -> RentPeriodResponse:
    """sdd_03 §9 + RF-02: detalle de un periodo del panel."""
    entry = await service.get_panel_entry(
        rent_period_id, organization_id, today=datetime.now(tz=UTC).date()
    )
    if entry is None:
        # RN-D01: 404, no 403 -- no distingue "no existe" de "otra org".
        raise NotFoundException()
    return RentPeriodResponse(data=RentPeriodSummary.model_validate(entry))


# ─── /v1/payments/:id/void -- RF-05 (issue #23) ────────────────────────


@payments_root_router.post(
    "/{payment_id}/void",
    response_model=PaymentVoidResponse,
)
async def void_payment(
    payment_id: UUID,
    dto: PaymentVoidRequest,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("payment:void")),
    service: PaymentService = Depends(get_payment_service),
) -> PaymentVoidResponse:
    """sdd_03 §9 + RF-05: anulacion logica con motivo obligatorio --
    recompone el saldo del periodo (RN-D04) y audita autor + motivo.
    Segunda anulacion -> `409 PAYMENT_ALREADY_VOIDED` (CA-04-07)."""
    voided = await service.void_payment(
        payment_id,
        organization_id,
        reason=dto.reason,
        actor_user_id=payload.sub,
    )
    return PaymentVoidResponse(data=PaymentDetail.model_validate(voided))


# ─── /v1/payments/:id/receipt -- RF-07 (issue #24) ─────────────────────


@payments_root_router.get(
    "/{payment_id}/receipt",
    dependencies=[Depends(requires_permission("rent-period:read"))],
)
async def get_payment_receipt(
    payment_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: PaymentService = Depends(get_payment_service),
) -> Response:
    """sdd_03 §9 + RF-07/CA-04-10: genera bajo demanda y descarga el
    recibo PDF del cobro (una pagina, WeasyPrint, SINCRONICO) -- capital,
    interes, TC (si aplico) y encabezado de la administradora. Sobre un
    cobro anulado -> `422 BUSINESS_RULE_VIOLATION` (RN-P08).

    Permiso `rent-period:read` (no existe un `payment:read` atomico en el
    catalogo de sdd_03 -- ver "Decisiones de implementacion" del PR): el
    catalogo de permisos es fijo (CLAUDE.md §8 "Nunca hacer sin
    preguntar: Agregar dependencias/permisos no mencionados en los
    SDDs"), y `rent-period:read` es el permiso de lectura que ya cubre
    este mismo dominio (panel de cobranzas, preview de interes)."""
    pdf_bytes = await service.generate_receipt_pdf(payment_id, organization_id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="recibo-{payment_id}.pdf"'},
    )


# ─── /v1/debt -- RF-06 (issue #23) ──────────────────────────────────────


@debt_router.get(
    "",
    response_model=DebtListResponse,
    dependencies=[Depends(requires_permission("rent-period:read"))],
)
async def list_debt(
    landlord_id: UUID | None = Query(default=None),
    renter_id: UUID | None = Query(default=None),
    min_days: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    organization_id: UUID = Depends(get_current_tenant),
    service: DebtService = Depends(get_debt_service),
) -> DebtListResponse:
    """sdd_03 §9 + RF-06: estado de deuda global -- por inquilino y
    propiedad, periodos adeudados, saldo, dias de mora e interes sugerido
    acumulado (CA-04-09). Filtrable por antiguedad (`min_days`, UC-10)."""
    entries, next_cursor = await service.list_debt(
        organization_id=organization_id,
        today=datetime.now(tz=UTC).date(),
        landlord_id=landlord_id,
        renter_id=renter_id,
        min_days=min_days,
        cursor=cursor,
        limit=limit,
    )
    return DebtListResponse(
        data=[DebtEntryData.model_validate(e) for e in entries],
        meta={"next_cursor": next_cursor, "limit": limit},
    )
