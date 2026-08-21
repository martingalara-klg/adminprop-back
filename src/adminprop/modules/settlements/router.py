"""Endpoints de `settlements`: generacion asincrona + lectura (issue #29).

SDD: core/sdd_03_api_contracts.md §11 "Liquidaciones" +
docs/sdd/features/spec_module_05_liquidaciones.md §RF-01/RF-02.

Solo los 3 endpoints de este issue: `GET /settlements`,
`POST /settlements/generate`, `GET /settlements/:id`.
`regenerate`/`issue`/`export` son del issue #30 -- fuera de alcance.

Permiso `settlement:generate` para el POST (encola el calculo),
`settlement:read` para los dos GET -- catalogo real de
`modules/superadmin/provisioning.py.ALL_PERMISSIONS`.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from adminprop.modules.settlements.schemas import (
    SettlementDetail,
    SettlementGenerateAccepted,
    SettlementGenerateAcceptedData,
    SettlementGenerateRequest,
    SettlementLineItemDetail,
    SettlementListResponse,
    SettlementResponse,
    SettlementSummary,
    parse_period,
)
from adminprop.modules.settlements.service import (
    SettlementService,
    get_settlement_service,
)
from adminprop.shared.auth.jwt import JWTPayload
from adminprop.shared.rbac import requires_permission
from adminprop.shared.tenant import get_current_tenant

router = APIRouter(prefix="/v1/settlements", tags=["settlements"])

# RF-01: "el cliente polea GET /settlements/:id" -- estimacion informativa
# del tiempo de procesamiento (mismo orden de magnitud que
# `SettlementCalculationAccepted` del skill api-endpoint.md).
_ESTIMATED_COMPLETION_SECONDS = 15


@router.get(
    "",
    response_model=SettlementListResponse,
    dependencies=[Depends(requires_permission("settlement:read"))],
)
async def list_settlements(
    period: str | None = Query(default=None, description="YYYY-MM"),
    landlord_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None, description="draft|issued"),
    organization_id: UUID = Depends(get_current_tenant),
    service: SettlementService = Depends(get_settlement_service),
) -> SettlementListResponse:
    """sdd_03 §11: `GET /settlements?period=&landlord_id=&status=`."""
    period_date = parse_period(period) if period is not None else None
    settlements = await service.list(
        organization_id=organization_id,
        period=period_date,
        landlord_id=landlord_id,
        status=status,
    )
    return SettlementListResponse(data=[SettlementSummary.model_validate(s) for s in settlements])


@router.post(
    "/generate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SettlementGenerateAccepted,
    dependencies=[Depends(requires_permission("settlement:generate"))],
)
async def generate_settlement(
    dto: SettlementGenerateRequest,
    request: Request,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("settlement:generate")),
    service: SettlementService = Depends(get_settlement_service),
) -> SettlementGenerateAccepted:
    """SDD: spec_module_05_liquidaciones.md §RF-01 + sdd_03 §11
    "POST /settlements/generate". Implements: CA-05-01..04, RN-L01/L02/
    L04/L05/L06. Validaciones sincronicas ANTES del 202 (existencia,
    duplicado, TC requerido, mes no futuro, regla de "sin propiedad/
    movimientos") -- el calculo real corre en `documents_worker`."""
    request_id = request.headers.get("X-Request-Id") or str(
        getattr(request.state, "request_id", "")
    )
    settlement = await service.generate(
        organization_id=organization_id,
        landlord_id=dto.landlord_id,
        period=dto.period_date,
        exchange_rate=dto.exchange_rate,
        actor_user_id=payload.sub,
        request_id=request_id,
    )
    return SettlementGenerateAccepted(
        data=SettlementGenerateAcceptedData(
            settlement_id=settlement.id,
            status="pending",
            estimated_completion_seconds=_ESTIMATED_COMPLETION_SECONDS,
        )
    )


@router.get(
    "/{settlement_id}",
    response_model=SettlementResponse,
    dependencies=[Depends(requires_permission("settlement:read"))],
)
async def get_settlement(
    settlement_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: SettlementService = Depends(get_settlement_service),
) -> SettlementResponse:
    """sdd_03 §11: `GET /settlements/:id` (totales + line items). RN-D01:
    404 si es de otro tenant o no existe -- no distingue ambos casos."""
    detail = await service.get_detail(settlement_id, organization_id)
    settlement = detail.settlement
    return SettlementResponse(
        data=SettlementDetail(
            id=settlement.id,
            landlord_id=settlement.landlord_id,
            period=settlement.period,
            status=settlement.status,
            job_status=detail.job_status,
            warnings=detail.warnings,
            exchange_rate=settlement.exchange_rate,
            total_collected=settlement.total_collected,
            commission_total=settlement.commission_total,
            charges_total=settlement.charges_total,
            repairs_total=settlement.repairs_total,
            already_settled_total=settlement.already_settled_total,
            net_amount=settlement.net_amount,
            commission_pct_used=settlement.commission_pct_used,
            regenerated_count=settlement.regenerated_count,
            generated_by=settlement.generated_by,
            issued_at=settlement.issued_at,
            created_at=settlement.created_at,
            updated_at=settlement.updated_at,
            line_items=[SettlementLineItemDetail.model_validate(li) for li in detail.line_items],
        )
    )
