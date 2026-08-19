"""Endpoints /v1/rent-periods/* de cobranzas (issue #22).

SDD: core/sdd_03_api_contracts.md §9 "Cobranzas".
Implements: CA-04-03, CA-04-04, CA-04-05, CA-04-06.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from adminprop.modules.payments.schemas import (
    InterestPreviewData,
    InterestPreviewResponse,
    PaymentCreate,
    PaymentResponse,
)
from adminprop.modules.payments.service import PaymentService, get_payment_service
from adminprop.shared.auth.jwt import JWTPayload
from adminprop.shared.rbac import requires_permission
from adminprop.shared.tenant import get_current_tenant

router = APIRouter(prefix="/v1/rent-periods", tags=["payments"])


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
