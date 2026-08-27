"""Endpoints /v1/properties/* y /v1/service-accounts/:id (issue #15).

SDD: core/sdd_03_api_contracts.md §7 "Propiedades".
Implements: CA-01-01, 02, 03, 06.

Fuera de alcance de este issue (ver "Decisiones de implementacion" del PR):
- `GET /properties/:id/work-orders` (historial de reparaciones, UC-16) --
  depende del modulo de Mantenimiento (issue #26), todavia inexistente.
- `GET/POST /properties/:id/recurring-charges` -- depende del modulo de
  Liquidaciones (issue #28).
- CA-01-04/05 (estado `rented` automatico + contrato vigente en la ficha)
  -- dependen del modulo de Contratos (issue #17).

CA-01-06: un usuario `maintenance` no tiene ningun permiso `property:*`
(ver `modules/superadmin/provisioning.py.MAINTENANCE_PERMISSIONS`) -- la
dependency `requires_permission` ya rechaza con 403 FORBIDDEN antes de
llegar a estos handlers, sin codigo adicional en este modulo (mismo
patron que `modules/people/router.py`, CA-02-07).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from adminprop.modules.properties.repository import PropertyFilters
from adminprop.modules.properties.schemas import (
    NeighborhoodCreate,
    NeighborhoodDetail,
    NeighborhoodListResponse,
    NeighborhoodResponse,
    NeighborhoodUpdate,
    PropertyCreate,
    PropertyDetail,
    PropertyDetailResponse,
    PropertyListResponse,
    PropertyResponse,
    PropertyServiceAccountCreate,
    PropertyServiceAccountDetail,
    PropertyServiceAccountListResponse,
    PropertyServiceAccountResponse,
    PropertyServiceAccountUpdate,
    PropertySummary,
    PropertyUpdate,
)
from adminprop.modules.properties.service import (
    NeighborhoodService,
    PropertyService,
    PropertyServiceAccountService,
    get_neighborhood_service,
    get_property_service,
    get_property_service_account_service,
)
from adminprop.shared.auth.jwt import JWTPayload
from adminprop.shared.errors.codes import NotFoundException
from adminprop.shared.rbac import requires_permission
from adminprop.shared.tenant import get_current_tenant

properties_router = APIRouter(prefix="/v1/properties", tags=["properties"])
service_accounts_router = APIRouter(prefix="/v1/service-accounts", tags=["properties"])
neighborhoods_router = APIRouter(prefix="/v1/neighborhoods", tags=["properties"])


async def _neighborhood_map(organization_id, neighborhood_service: NeighborhoodService) -> dict:
    """Issue #99: catalogo de barrios acotado por organizacion (RF-05) --
    un solo `list()` por request alcanza para embeber `neighborhood` en
    listados sin N+1 queries por propiedad."""
    neighborhoods = await neighborhood_service.list(organization_id)
    return {n.id: NeighborhoodDetail.model_validate(n) for n in neighborhoods}


def _to_property_summary(prop, neighborhood_map: dict) -> PropertySummary:
    """Issue #99: `neighborhood` no es un atributo del modelo ORM
    `Property` -- se resuelve aparte via `neighborhood_map` y se aplica
    con `model_copy(update=...)` (mismo patron que
    `modules/people/router.py.get_landlord_settlements`)."""
    return PropertySummary.model_validate(prop).model_copy(
        update={"neighborhood": neighborhood_map.get(prop.neighborhood_id)}
    )


def _to_property_detail(prop, service_accounts: list, neighborhood_map: dict) -> PropertyDetail:
    """RF-03: ficha consolidada. `active_contract`/`work_orders_history`/
    `recurring_charges` quedan en sus defaults vacios (ver `schemas.py`
    docstring de `PropertyDetail`) -- ningun modulo dependiente existe
    todavia. `neighborhood` (issue #99): `None` para propiedades legacy
    sin barrio."""
    return PropertyDetail(
        id=prop.id,
        address=prop.address,
        landlord_id=prop.landlord_id,
        neighborhood_id=prop.neighborhood_id,
        neighborhood=neighborhood_map.get(prop.neighborhood_id),
        property_type=prop.property_type,
        status=prop.status,
        notes=prop.notes,
        created_at=prop.created_at,
        updated_at=prop.updated_at,
        service_accounts=[PropertyServiceAccountDetail.model_validate(a) for a in service_accounts],
    )


# ─── Propiedades (/v1/properties) — RF-01, RF-03 ─────────────────────────


@properties_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=PropertyResponse,
    dependencies=[Depends(requires_permission("property:manage"))],
)
async def create_property(
    dto: PropertyCreate,
    organization_id: UUID = Depends(get_current_tenant),
    service: PropertyService = Depends(get_property_service),
    neighborhood_service: NeighborhoodService = Depends(get_neighborhood_service),
) -> PropertyResponse:
    """RF-01 + CA-01-01: crea una propiedad con direccion, propietario y
    tipo -- `landlord_id` validado contra el mismo tenant (RN-D01).
    Issue #99 + CA-01-08: `neighborhood_id` obligatorio, mismo criterio."""
    prop = await service.create(
        organization_id=organization_id,
        landlord_id=dto.landlord_id,
        neighborhood_id=dto.neighborhood_id,
        address=dto.address,
        property_type=dto.property_type,
        notes=dto.notes,
    )
    neighborhood_map = await _neighborhood_map(organization_id, neighborhood_service)
    return PropertyResponse(data=_to_property_summary(prop, neighborhood_map))


@properties_router.get(
    "",
    response_model=PropertyListResponse,
    dependencies=[Depends(requires_permission("property:read"))],
)
async def list_properties(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    landlord_id: UUID | None = Query(default=None),
    neighborhood_id: UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    property_type: str | None = Query(default=None),
    search: str | None = Query(default=None),
    organization_id: UUID = Depends(get_current_tenant),
    service: PropertyService = Depends(get_property_service),
    neighborhood_service: NeighborhoodService = Depends(get_neighborhood_service),
) -> PropertyListResponse:
    """RF-01: "Listado con filtros: propietario, estado, tipo, barrio
    (issue #99); busqueda por direccion". CA-01-01: la propiedad creada
    aparece aca. CA-01-09: filtro `?neighborhood_id=`."""
    items, next_cursor = await service.list(
        organization_id=organization_id,
        cursor=cursor,
        limit=limit,
        filters=PropertyFilters(
            landlord_id=landlord_id,
            neighborhood_id=neighborhood_id,
            status=status_filter,
            property_type=property_type,
            search=search,
        ),
    )
    neighborhood_map = await _neighborhood_map(organization_id, neighborhood_service)
    return PropertyListResponse(
        data=[_to_property_summary(p, neighborhood_map) for p in items],
        meta={"next_cursor": next_cursor, "limit": limit},
    )


@properties_router.get(
    "/{property_id}",
    response_model=PropertyDetailResponse,
    dependencies=[Depends(requires_permission("property:read"))],
)
async def get_property(
    property_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: PropertyService = Depends(get_property_service),
    accounts_service: PropertyServiceAccountService = Depends(get_property_service_account_service),
    neighborhood_service: NeighborhoodService = Depends(get_neighborhood_service),
) -> PropertyDetailResponse:
    """RF-03 + CA-01-02: ficha consolidada -- datos + cuentas de servicio
    juntas. `active_contract`/historial/conceptos: ver docstring de
    `PropertyDetail`. `neighborhood` (issue #99, CA-01-08): `None` para
    propiedades legacy sin barrio."""
    prop = await service.get(property_id, organization_id)
    if prop is None:
        # RN-D01: 404, no 403 -- no distingue "no existe" de "otra org".
        raise NotFoundException()
    accounts = await accounts_service.list_by_property(property_id, organization_id)
    neighborhood_map = await _neighborhood_map(organization_id, neighborhood_service)
    return PropertyDetailResponse(data=_to_property_detail(prop, accounts, neighborhood_map))


@properties_router.patch(
    "/{property_id}",
    response_model=PropertyResponse,
)
async def update_property(
    property_id: UUID,
    dto: PropertyUpdate,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("property:manage")),
    service: PropertyService = Depends(get_property_service),
    neighborhood_service: NeighborhoodService = Depends(get_neighborhood_service),
) -> PropertyResponse:
    """RF-01: PATCH parcial -- todos los campos salvo `status="rented"`
    (rechazado por Pydantic, RF-04). Cambio de `landlord_id` auditado.
    `neighborhood_id` (issue #99): editable, pero no puede vaciarse."""
    updated = await service.update(
        property_id,
        organization_id,
        address=dto.address,
        landlord_id=dto.landlord_id,
        neighborhood_id=dto.neighborhood_id,
        property_type=dto.property_type,
        status=dto.status,
        notes=dto.notes,
        actor_user_id=payload.sub,
        fields_set=dto.model_fields_set,
    )
    neighborhood_map = await _neighborhood_map(organization_id, neighborhood_service)
    return PropertyResponse(data=_to_property_summary(updated, neighborhood_map))


@properties_router.delete(
    "/{property_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires_permission("property:manage"))],
)
async def delete_property(
    property_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: PropertyService = Depends(get_property_service),
) -> None:
    """RF-01 + CA-01-03: baja logica; `409 ENTITY_HAS_DEPENDENCIES` si hay
    contrato activo (chequeo extensible, ver `repository.py` -- siempre
    `False` hoy)."""
    await service.delete(property_id, organization_id)


# ─── Cuentas de servicio — RF-02 ──────────────────────────────────────────


@properties_router.get(
    "/{property_id}/service-accounts",
    response_model=PropertyServiceAccountListResponse,
    dependencies=[Depends(requires_permission("property:read"))],
)
async def list_service_accounts(
    property_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: PropertyServiceAccountService = Depends(get_property_service_account_service),
) -> PropertyServiceAccountListResponse:
    """RF-02 + CA-01-02: todas las cuentas de la propiedad."""
    items = await service.list_by_property(property_id, organization_id)
    return PropertyServiceAccountListResponse(data=items)


@properties_router.post(
    "/{property_id}/service-accounts",
    status_code=status.HTTP_201_CREATED,
    response_model=PropertyServiceAccountResponse,
    dependencies=[Depends(requires_permission("property:manage"))],
)
async def create_service_account(
    property_id: UUID,
    dto: PropertyServiceAccountCreate,
    organization_id: UUID = Depends(get_current_tenant),
    service: PropertyServiceAccountService = Depends(get_property_service_account_service),
) -> PropertyServiceAccountResponse:
    """RF-02 + CA-01-02: carga una cuenta de servicio (rentas/muni/luz/gas/
    agua/expensas/otro) -- `secondary_number` cubre el caso `luz` (n° de
    cliente + n° de contrato)."""
    account = await service.create(
        property_id=property_id,
        organization_id=organization_id,
        service_type=dto.service_type,
        account_number=dto.account_number,
        secondary_number=dto.secondary_number,
        notes=dto.notes,
    )
    return PropertyServiceAccountResponse(data=account)


@service_accounts_router.patch(
    "/{service_account_id}",
    response_model=PropertyServiceAccountResponse,
    dependencies=[Depends(requires_permission("property:manage"))],
)
async def update_service_account(
    service_account_id: UUID,
    dto: PropertyServiceAccountUpdate,
    organization_id: UUID = Depends(get_current_tenant),
    service: PropertyServiceAccountService = Depends(get_property_service_account_service),
) -> PropertyServiceAccountResponse:
    """RF-02: PATCH parcial de una cuenta de servicio."""
    updated = await service.update(
        service_account_id,
        organization_id,
        account_number=dto.account_number,
        secondary_number=dto.secondary_number,
        notes=dto.notes,
        fields_set=dto.model_fields_set,
    )
    return PropertyServiceAccountResponse(data=updated)


@service_accounts_router.delete(
    "/{service_account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires_permission("property:manage"))],
)
async def delete_service_account(
    service_account_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: PropertyServiceAccountService = Depends(get_property_service_account_service),
) -> None:
    """RF-02: baja logica de la cuenta de servicio (RN-D02)."""
    await service.delete(service_account_id, organization_id)


# ─── Barrios (/v1/neighborhoods) — RF-05 (issue #99) ─────────────────────


@neighborhoods_router.get(
    "",
    response_model=NeighborhoodListResponse,
    dependencies=[Depends(requires_permission("property:read"))],
)
async def list_neighborhoods(
    organization_id: UUID = Depends(get_current_tenant),
    service: NeighborhoodService = Depends(get_neighborhood_service),
) -> NeighborhoodListResponse:
    """RF-05: catalogo completo de la organizacion, sin paginacion."""
    items = await service.list(organization_id)
    return NeighborhoodListResponse(data=items)


@neighborhoods_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=NeighborhoodResponse,
    dependencies=[Depends(requires_permission("property:manage"))],
)
async def create_neighborhood(
    dto: NeighborhoodCreate,
    organization_id: UUID = Depends(get_current_tenant),
    service: NeighborhoodService = Depends(get_neighborhood_service),
) -> NeighborhoodResponse:
    """RF-05 + CA-01-07: alta de barrio; `name` unico por organizacion
    (case-insensitive) -- `409 CONFLICT` si ya existe."""
    row = await service.create(organization_id=organization_id, name=dto.name)
    return NeighborhoodResponse(data=row)


@neighborhoods_router.patch(
    "/{neighborhood_id}",
    response_model=NeighborhoodResponse,
    dependencies=[Depends(requires_permission("property:manage"))],
)
async def update_neighborhood(
    neighborhood_id: UUID,
    dto: NeighborhoodUpdate,
    organization_id: UUID = Depends(get_current_tenant),
    service: NeighborhoodService = Depends(get_neighborhood_service),
) -> NeighborhoodResponse:
    """RF-05 + CA-01-07: rename del barrio."""
    row = await service.update(neighborhood_id, organization_id, name=dto.name)
    return NeighborhoodResponse(data=row)


@neighborhoods_router.delete(
    "/{neighborhood_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires_permission("property:manage"))],
)
async def delete_neighborhood(
    neighborhood_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: NeighborhoodService = Depends(get_neighborhood_service),
) -> None:
    """RF-05 + CA-01-07: baja logica; `409 ENTITY_HAS_DEPENDENCIES` si
    tiene propiedades asociadas."""
    await service.delete(neighborhood_id, organization_id)
