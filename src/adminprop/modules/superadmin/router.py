"""Endpoints /v1/superadmin/organizations/* (issue #7).

SDD: core/sdd_03_api_contracts.md §2 "Super Admin". Implements: CA-00-01,
CA-00-02, CA-00-05, CA-00-06.

Nota de alcance (declarada en el PR): `sdd_03` §2 tambien lista
`PATCH /superadmin/organizations/:id`, sin especificar que campos son
editables ni sus validaciones -- ninguno de los CA de este issue lo
requiere (crear org, invitar owner, reenviar, disable/enable, dashboard).
Se deja fuera de este PR para no inventar un contrato no especificado;
se implementa cuando un issue lo necesite con su propio spec.

`sdd_03` no especifica el status code de exito de cada endpoint de esta
seccion (a diferencia de la seccion "Autenticacion", que si lo hace) --
se usa la convencion general de `docs/skills/api-endpoint.md`: 201 para
recursos creados (organizacion, invitacion), 200 para lecturas y
transiciones de estado (disable/enable).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from adminprop.modules.superadmin.repository import OrganizationRow
from adminprop.modules.superadmin.schemas import (
    InvitationResponse,
    InvitationSummary,
    InviteOwnerRequest,
    OrganizationCreate,
    OrganizationDetail,
    OrganizationListResponse,
    OrganizationResponse,
    OrganizationStatusChangeRequest,
    OrganizationSummary,
)
from adminprop.modules.superadmin.service import OrganizationService, get_organization_service
from adminprop.shared.auth.dependencies import requires_super_admin
from adminprop.shared.auth.jwt import JWTPayload
from adminprop.shared.errors.codes import NotFoundException
from adminprop.shared.logging.json_logger import request_id_var

router = APIRouter(
    prefix="/v1/superadmin/organizations",
    tags=["superadmin"],
    dependencies=[Depends(requires_super_admin)],
)


def _request_id() -> str:
    return request_id_var.get() or ""


def _to_summary(row: OrganizationRow) -> OrganizationSummary:
    return OrganizationSummary(
        id=row.id,
        slug=row.slug,
        name=row.name,
        status=row.status,
        timezone=row.timezone,
        created_at=row.created_at,
        owner_email=row.owner_email,
    )


def _to_detail(row: OrganizationRow) -> OrganizationDetail:
    return OrganizationDetail(
        id=row.id,
        slug=row.slug,
        name=row.name,
        status=row.status,
        timezone=row.timezone,
        created_at=row.created_at,
        owner_email=row.owner_email,
        settings=row.settings,
        updated_at=row.updated_at,
    )


@router.get("", response_model=OrganizationListResponse)
async def list_organizations(
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    service: OrganizationService = Depends(get_organization_service),
) -> OrganizationListResponse:
    """RF-01: dashboard de organizaciones -- filtro por status y busqueda."""
    items, next_cursor = await service.list(
        status=status_filter, search=search, cursor=cursor, limit=limit
    )
    return OrganizationListResponse(
        data=[_to_summary(item) for item in items],
        meta={"next_cursor": next_cursor, "limit": limit},
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=OrganizationResponse)
async def create_organization(
    dto: OrganizationCreate,
    payload: JWTPayload = Depends(requires_super_admin),
    service: OrganizationService = Depends(get_organization_service),
) -> OrganizationResponse:
    """RF-02 + CA-00-01: crea la organizacion en `pending_owner`, slug
    autogenerado unico, 3 roles de sistema + settings default sembrados
    en la misma transaccion."""
    org = await service.create(dto.name, dto.timezone, payload.sub)
    return OrganizationResponse(data=_to_detail(org))


@router.get("/{organization_id}", response_model=OrganizationResponse)
async def get_organization(
    organization_id: UUID,
    service: OrganizationService = Depends(get_organization_service),
) -> OrganizationResponse:
    """RF-01 detalle. CA-00-06: solo metadata de la organizacion (nunca
    datos operativos -- este modulo no expone ningun endpoint de
    propiedades/contratos/cobros/liquidaciones)."""
    org = await service.get(organization_id)
    if org is None:
        raise NotFoundException()
    return OrganizationResponse(data=_to_detail(org))


@router.post(
    "/{organization_id}/invite-owner",
    status_code=status.HTTP_201_CREATED,
    response_model=InvitationResponse,
)
async def invite_owner(
    organization_id: UUID,
    dto: InviteOwnerRequest,
    payload: JWTPayload = Depends(requires_super_admin),
    service: OrganizationService = Depends(get_organization_service),
) -> InvitationResponse:
    """RF-03 + CA-00-02: invita al owner inicial; expira a las 72h."""
    invitation = await service.invite_owner(organization_id, dto.email, _request_id(), payload.sub)
    return InvitationResponse(data=InvitationSummary.model_validate(invitation))


@router.post(
    "/{organization_id}/resend-invitation",
    status_code=status.HTTP_201_CREATED,
    response_model=InvitationResponse,
)
async def resend_invitation(
    organization_id: UUID,
    payload: JWTPayload = Depends(requires_super_admin),
    service: OrganizationService = Depends(get_organization_service),
) -> InvitationResponse:
    """RF-04 + CA-00-02: regenera token/expiracion; la anterior queda `revoked`."""
    invitation = await service.resend_invitation(organization_id, _request_id(), payload.sub)
    return InvitationResponse(data=InvitationSummary.model_validate(invitation))


@router.post("/{organization_id}/disable", response_model=OrganizationResponse)
async def disable_organization(
    organization_id: UUID,
    dto: OrganizationStatusChangeRequest,
    payload: JWTPayload = Depends(requires_super_admin),
    service: OrganizationService = Depends(get_organization_service),
) -> OrganizationResponse:
    """RF-05: sus miembros no pueden autenticarse ni renovar sesion (RN-03)."""
    org = await service.disable(organization_id, dto.reason, payload.sub)
    return OrganizationResponse(data=_to_detail(org))


@router.post("/{organization_id}/enable", response_model=OrganizationResponse)
async def enable_organization(
    organization_id: UUID,
    dto: OrganizationStatusChangeRequest,
    payload: JWTPayload = Depends(requires_super_admin),
    service: OrganizationService = Depends(get_organization_service),
) -> OrganizationResponse:
    """RF-05: recupera acceso con sus datos intactos."""
    org = await service.enable(organization_id, dto.reason, payload.sub)
    return OrganizationResponse(data=_to_detail(org))
