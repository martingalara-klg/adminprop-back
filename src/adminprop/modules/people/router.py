"""Endpoints /v1/landlords/* y /v1/renters/* (issue #13).

SDD: core/sdd_03_api_contracts.md §5 "Propietarios" + §6 "Inquilinos".
Implements: CA-02-01, 02, 03, 04, 06, 07.

Fuera de alcance de este issue (ver "Decisiones de implementacion" del PR):
- `GET /landlords/:id/settlements` (historial de liquidaciones) -- depende
  del modulo de Liquidaciones, todavia inexistente.
- `GET /renters/:id/debt` (CA-02-05, estado de deuda) -- es del issue #23
  (depende de Cobranzas, `rent_periods`).
- `POST /renters/:id/debt-certificate` -- idem, depende de Cobranzas.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from adminprop.modules.people.models import Renter
from adminprop.modules.people.repository import LandlordFields
from adminprop.modules.people.schemas import (
    LandlordCreate,
    LandlordDetail,
    LandlordListResponse,
    LandlordResponse,
    LandlordSummary,
    LandlordUpdate,
    RenterCreate,
    RenterDetail,
    RenterListResponse,
    RenterResponse,
    RenterUpdate,
)
from adminprop.modules.people.service import (
    LandlordService,
    RenterService,
    get_landlord_service,
    get_renter_service,
)
from adminprop.shared.auth.jwt import JWTPayload
from adminprop.shared.errors.codes import NotFoundException
from adminprop.shared.rbac import requires_permission
from adminprop.shared.tenant import get_current_tenant

landlords_router = APIRouter(prefix="/v1/landlords", tags=["people"])
renters_router = APIRouter(prefix="/v1/renters", tags=["people"])


def _to_landlord_detail(landlord: LandlordFields) -> LandlordDetail:
    return LandlordDetail(
        id=landlord.id,
        name=landlord.name,
        tax_id=landlord.tax_id,
        phone=landlord.phone,
        email=landlord.email,
        bank_info=landlord.bank_info,
        commission_pct=landlord.commission_pct,
        notes=landlord.notes,
        created_at=landlord.created_at,
        updated_at=landlord.updated_at,
    )


def _to_landlord_summary(landlord) -> LandlordSummary:
    """CA-02-04: construido a partir de la fila ORM del listado -- nunca
    del `LandlordFields` descifrado (`LandlordSummary` ni siquiera
    declara `bank_info`, ver `schemas.py`)."""
    return LandlordSummary.model_validate(landlord)


def _to_renter_detail(renter: Renter) -> RenterDetail:
    return RenterDetail.model_validate(renter)


# ─── Propietarios (/v1/landlords) — RF-01, RF-02 ─────────────────────────


@landlords_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=LandlordResponse,
    dependencies=[Depends(requires_permission("landlord:manage"))],
)
async def create_landlord(
    dto: LandlordCreate,
    organization_id: UUID = Depends(get_current_tenant),
    service: LandlordService = Depends(get_landlord_service),
) -> LandlordResponse:
    """RF-01 + CA-02-01: crea un propietario con `commission_pct`
    obligatorio -- queda disponible para las liquidaciones de todas sus
    propiedades (futuras, issue #15+)."""
    landlord = await service.create(
        organization_id=organization_id,
        name=dto.name,
        tax_id=dto.tax_id,
        phone=dto.phone,
        email=dto.email,
        bank_info=dto.bank_info,
        commission_pct=dto.commission_pct,
        notes=dto.notes,
    )
    return LandlordResponse(data=_to_landlord_detail(landlord))


@landlords_router.get(
    "",
    response_model=LandlordListResponse,
    dependencies=[Depends(requires_permission("landlord:read"))],
)
async def list_landlords(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    organization_id: UUID = Depends(get_current_tenant),
    service: LandlordService = Depends(get_landlord_service),
) -> LandlordListResponse:
    """RF-02: listado paginado. CA-02-04: `bank_info` NUNCA aparece aca
    (`LandlordSummary` no declara el campo)."""
    items, next_cursor = await service.list(
        organization_id=organization_id, cursor=cursor, limit=limit
    )
    return LandlordListResponse(
        data=[_to_landlord_summary(item) for item in items],
        meta={"next_cursor": next_cursor, "limit": limit},
    )


@landlords_router.get(
    "/{landlord_id}",
    response_model=LandlordResponse,
    dependencies=[Depends(requires_permission("landlord:read"))],
)
async def get_landlord(
    landlord_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: LandlordService = Depends(get_landlord_service),
) -> LandlordResponse:
    """RF-02 + CA-02-04: ficha del propietario -- `bank_info` descifrado
    solo aca (owner/admin, via `landlord:read`)."""
    landlord = await service.get(landlord_id, organization_id)
    if landlord is None:
        # RN-D01: 404, no 403 -- no distingue "no existe" de "otra org".
        raise NotFoundException()
    return LandlordResponse(data=_to_landlord_detail(landlord))


@landlords_router.patch(
    "/{landlord_id}",
    response_model=LandlordResponse,
)
async def update_landlord(
    landlord_id: UUID,
    dto: LandlordUpdate,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("landlord:manage")),
    service: LandlordService = Depends(get_landlord_service),
) -> LandlordResponse:
    """RF-01 + CA-02-02/CA-02-03: `admin` edita datos de contacto libremente;
    si incluye `commission_pct` recibe 403 FORBIDDEN (solo `owner`). El
    cambio de `commission_pct` (cuando lo hace el owner) queda auditado
    con valor anterior/nuevo y rige unicamente para liquidaciones futuras
    (RN-L05 -- no hay liquidaciones ya generadas que recalcular en este
    modulo todavia)."""
    updated = await service.update(
        landlord_id,
        organization_id,
        name=dto.name,
        tax_id=dto.tax_id,
        phone=dto.phone,
        email=dto.email,
        bank_info=dto.bank_info,
        commission_pct=dto.commission_pct,
        notes=dto.notes,
        actor_user_id=payload.sub,
        actor_role=payload.role,
        fields_set=dto.model_fields_set,
    )
    return LandlordResponse(data=_to_landlord_detail(updated))


@landlords_router.delete(
    "/{landlord_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires_permission("landlord:manage"))],
)
async def delete_landlord(
    landlord_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: LandlordService = Depends(get_landlord_service),
) -> None:
    """RF-01 + CA-02-06: baja logica. `409 ENTITY_HAS_DEPENDENCIES` si el
    propietario tiene propiedades activas (chequeo extensible, ver
    `repository.py` -- siempre `False` hoy)."""
    await service.delete(landlord_id, organization_id)


# ─── Inquilinos (/v1/renters) — RF-03 ────────────────────────────────────


@renters_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=RenterResponse,
    dependencies=[Depends(requires_permission("renter:manage"))],
)
async def create_renter(
    dto: RenterCreate,
    organization_id: UUID = Depends(get_current_tenant),
    service: RenterService = Depends(get_renter_service),
) -> RenterResponse:
    """RF-03: alta de inquilino."""
    renter = await service.create(
        organization_id=organization_id,
        name=dto.name,
        tax_id=dto.tax_id,
        phone=dto.phone,
        email=dto.email,
        notes=dto.notes,
    )
    return RenterResponse(data=_to_renter_detail(renter))


@renters_router.get(
    "",
    response_model=RenterListResponse,
    dependencies=[Depends(requires_permission("renter:read"))],
)
async def list_renters(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    organization_id: UUID = Depends(get_current_tenant),
    service: RenterService = Depends(get_renter_service),
) -> RenterListResponse:
    """RF-03: listado paginado."""
    items, next_cursor = await service.list(
        organization_id=organization_id, cursor=cursor, limit=limit
    )
    return RenterListResponse(
        data=[_to_renter_detail(item) for item in items],
        meta={"next_cursor": next_cursor, "limit": limit},
    )


@renters_router.get(
    "/{renter_id}",
    response_model=RenterResponse,
    dependencies=[Depends(requires_permission("renter:read"))],
)
async def get_renter(
    renter_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: RenterService = Depends(get_renter_service),
) -> RenterResponse:
    """RF-04 (parcial -- solo datos + ficha; el estado de deuda,
    CA-02-05, queda diferido al issue #23)."""
    renter = await service.get(renter_id, organization_id)
    if renter is None:
        raise NotFoundException()
    return RenterResponse(data=_to_renter_detail(renter))


@renters_router.patch(
    "/{renter_id}",
    response_model=RenterResponse,
)
async def update_renter(
    renter_id: UUID,
    dto: RenterUpdate,
    organization_id: UUID = Depends(get_current_tenant),
    _payload: JWTPayload = Depends(requires_permission("renter:manage")),
    service: RenterService = Depends(get_renter_service),
) -> RenterResponse:
    """RF-03: edicion de datos de contacto."""
    updated = await service.update(
        renter_id,
        organization_id,
        name=dto.name,
        tax_id=dto.tax_id,
        phone=dto.phone,
        email=dto.email,
        notes=dto.notes,
        fields_set=dto.model_fields_set,
    )
    return RenterResponse(data=_to_renter_detail(updated))


@renters_router.delete(
    "/{renter_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires_permission("renter:manage"))],
)
async def delete_renter(
    renter_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: RenterService = Depends(get_renter_service),
) -> None:
    """RF-03 + CA-02-06: baja logica. `409 ENTITY_HAS_DEPENDENCIES` si el
    inquilino tiene contrato vigente (chequeo extensible, ver
    `repository.py` -- siempre `False` hoy)."""
    await service.delete(renter_id, organization_id)
