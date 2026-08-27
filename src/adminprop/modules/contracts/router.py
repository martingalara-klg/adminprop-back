"""Endpoints /v1/contracts/* y /v1/adjustments/* (issues #17 y #18).

SDD: core/sdd_03_api_contracts.md §8 "Contratos".
Implements: CA-03-01, 02, 03, 06, 08, CA-01-04 (issue #17);
            CA-03-04, CA-03-05 (issue #18, RF-04).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from adminprop.modules.contracts.adjustment_schemas import (
    AdjustmentApplyRequest,
    AdjustmentListResponse,
    AdjustmentResponse,
)
from adminprop.modules.contracts.adjustment_service import (
    ContractAdjustmentService,
    get_contract_adjustment_service,
)
from adminprop.modules.contracts.repository import ContractFilters
from adminprop.modules.contracts.schemas import (
    ContractCreate,
    ContractListResponse,
    ContractResponse,
    ContractTerminateRequest,
    ContractUpdate,
)
from adminprop.modules.contracts.service import ContractService, get_contract_service
from adminprop.shared.auth.jwt import JWTPayload
from adminprop.shared.errors.codes import NotFoundException
from adminprop.shared.rbac import requires_permission
from adminprop.shared.tenant import get_current_tenant

router = APIRouter(prefix="/v1/contracts", tags=["contracts"])
# sdd_03 §8: `/v1/adjustments` no cuelga de `/v1/contracts` -- router
# aparte, mismo modulo (la bandeja de ajustes cruza todos los contratos
# del tenant, no es un sub-recurso de uno solo).
adjustments_router = APIRouter(prefix="/v1/adjustments", tags=["contracts"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ContractResponse,
)
async def create_contract(
    dto: ContractCreate,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("contract:manage")),
    service: ContractService = Depends(get_contract_service),
) -> ContractResponse:
    """RF-02 + CA-03-01/02/03: crea un contrato -- nace en `draft` (RN-02).
    `property_id`/`renter_id` validados contra el mismo tenant (RN-06/RN-D01).
    RN-08/RN-C06 (issue #100): `current_amount`/`current_amount_since` --
    `actor_user_id` (`payload.sub`) es quien queda como `applied_by` del
    ajuste sintetico de carga inicial, si corresponde."""
    contract = await service.create(
        organization_id=organization_id,
        property_id=dto.property_id,
        renter_id=dto.renter_id,
        currency=dto.currency,
        initial_amount=dto.initial_amount,
        start_date=dto.start_date,
        end_date=dto.end_date,
        daily_late_fee_pct=dto.daily_late_fee_pct,
        adjustment_frequency_months=dto.adjustment_frequency_months,
        adjustment_index=dto.adjustment_index,
        adjustment_index_notes=dto.adjustment_index_notes,
        notes=dto.notes,
        current_amount=dto.current_amount,
        current_amount_since=dto.current_amount_since,
        actor_user_id=payload.sub,
    )
    return ContractResponse(data=contract)


@router.get(
    "",
    response_model=ContractListResponse,
    dependencies=[Depends(requires_permission("contract:read"))],
)
async def list_contracts(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
    property_id: UUID | None = Query(default=None),
    renter_id: UUID | None = Query(default=None),
    currency: str | None = Query(default=None),
    expiring_in_days: int | None = Query(default=None, ge=0),
    organization_id: UUID = Depends(get_current_tenant),
    service: ContractService = Depends(get_contract_service),
) -> ContractListResponse:
    """RF-01: "Listado con filtros: estado, propiedad, inquilino, moneda,
    expiring_in_days"."""
    items, next_cursor = await service.list(
        organization_id=organization_id,
        cursor=cursor,
        limit=limit,
        filters=ContractFilters(
            status=status_filter,
            property_id=property_id,
            renter_id=renter_id,
            currency=currency,
            expiring_in_days=expiring_in_days,
        ),
    )
    return ContractListResponse(data=items, meta={"next_cursor": next_cursor, "limit": limit})


@router.get(
    "/{contract_id}",
    response_model=ContractResponse,
    dependencies=[Depends(requires_permission("contract:read"))],
)
async def get_contract(
    contract_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: ContractService = Depends(get_contract_service),
) -> ContractResponse:
    """RF-01: detalle del contrato."""
    contract = await service.get(contract_id, organization_id)
    if contract is None:
        # RN-D01: 404, no 403 -- no distingue "no existe" de "otra org".
        raise NotFoundException()
    return ContractResponse(data=contract)


@router.patch(
    "/{contract_id}",
    response_model=ContractResponse,
)
async def update_contract(
    contract_id: UUID,
    dto: ContractUpdate,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("contract:manage")),
    service: ContractService = Depends(get_contract_service),
) -> ContractResponse:
    """sdd_03 §8 + CA-03-06: PATCH parcial -- solo `notes`/`end_date`;
    `current_amount` siempre 422 BUSINESS_RULE_VIOLATION (RN-C04)."""
    updated = await service.update(
        contract_id,
        organization_id,
        notes=dto.notes,
        end_date=dto.end_date,
        current_amount=dto.current_amount,
        actor_user_id=payload.sub,
        fields_set=dto.model_fields_set,
    )
    return ContractResponse(data=updated)


@router.post(
    "/{contract_id}/activate",
    response_model=ContractResponse,
)
async def activate_contract(
    contract_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("contract:manage")),
    service: ContractService = Depends(get_contract_service),
) -> ContractResponse:
    """RF-03 + CA-03-01/02, CA-01-04: `draft -> active`; revalida
    solapamiento (RN-01/RN-C01) y pone la propiedad en `rented`."""
    updated = await service.activate(contract_id, organization_id, actor_user_id=payload.sub)
    return ContractResponse(data=updated)


@router.post(
    "/{contract_id}/terminate",
    response_model=ContractResponse,
)
async def terminate_contract(
    contract_id: UUID,
    dto: ContractTerminateRequest,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("contract:manage")),
    service: ContractService = Depends(get_contract_service),
) -> ContractResponse:
    """RF-03 + CA-03-08: `active -> terminated` con motivo; la propiedad
    vuelve a `available` (CA-01-04)."""
    updated = await service.terminate(
        contract_id, organization_id, reason=dto.reason, actor_user_id=payload.sub
    )
    return ContractResponse(data=updated)


@router.get(
    "/{contract_id}/adjustments",
    response_model=AdjustmentListResponse,
    dependencies=[Depends(requires_permission("contract:read"))],
)
async def list_contract_adjustments(
    contract_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    contract_service: ContractService = Depends(get_contract_service),
    adjustment_service: ContractAdjustmentService = Depends(get_contract_adjustment_service),
) -> AdjustmentListResponse:
    """RF-04 paso 5: historial completo de ajustes de un contrato.
    RN-D01: 404 si el contrato no existe en el tenant (no revela
    existencia cross-tenant) antes de listar sus ajustes."""
    contract = await contract_service.get(contract_id, organization_id)
    if contract is None:
        raise NotFoundException()
    items = await adjustment_service.list_for_contract(contract_id, organization_id)
    return AdjustmentListResponse(data=items, meta={})


@adjustments_router.get(
    "",
    response_model=AdjustmentListResponse,
    dependencies=[Depends(requires_permission("contract:read"))],
)
async def list_pending_adjustments(
    status_filter: str | None = Query(default=None, alias="status"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    organization_id: UUID = Depends(get_current_tenant),
    service: ContractAdjustmentService = Depends(get_contract_adjustment_service),
) -> AdjustmentListResponse:
    """RF-04 paso 3, CA-03-04: bandeja de ajustes que tocan --
    `GET /adjustments?status=pending` (sdd_03 §8). Unico valor soportado
    hoy es `pending` (la bandeja); otros valores devuelven lista vacia en
    vez de exponer un filtro no especificado por el SDD."""
    if status_filter is not None and status_filter != "pending":
        return AdjustmentListResponse(data=[], meta={"next_cursor": None, "limit": limit})
    items, next_cursor = await service.list_pending(
        organization_id=organization_id, cursor=cursor, limit=limit
    )
    return AdjustmentListResponse(data=items, meta={"next_cursor": next_cursor, "limit": limit})


@adjustments_router.post(
    "/{adjustment_id}/apply",
    response_model=AdjustmentResponse,
)
async def apply_adjustment(
    adjustment_id: UUID,
    dto: AdjustmentApplyRequest,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("adjustment:apply")),
    service: ContractAdjustmentService = Depends(get_contract_adjustment_service),
) -> AdjustmentResponse:
    """RF-04 paso 4, CA-03-05: `pending -> applied`; `new_amount = previous
    × (1 + pct/100)`, actualiza `current_amount` del contrato y deja
    historial completo (pct/monto anterior/monto nuevo/autor)."""
    updated = await service.apply(
        adjustment_id, organization_id, pct=dto.pct, actor_user_id=payload.sub
    )
    return AdjustmentResponse(data=updated)
