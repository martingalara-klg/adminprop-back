"""Endpoints /v1/users/*, /v1/roles, /v1/organization/settings (issue #9).

SDD: core/sdd_03_api_contracts.md §3 "Usuarios y Roles" + §4
"Configuracion de la Organizacion". Implements: CA-07-01..CA-07-05.

Tres sub-routers (cada uno con su propio prefix) porque `sdd_03` agrupa
estos endpoints bajo tres raices de recurso distintas (`/users`, `/roles`,
`/organization/settings`) -- mismo criterio de legibilidad que
`docs/skills/module-structure.md` recomienda para modulos con varios
recursos relacionados.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from adminprop.modules.administracion.audit_query_repository import AuditLogRow
from adminprop.modules.administracion.repository import InvitationRow, MemberRow, RoleRow
from adminprop.modules.administracion.schemas import (
    AuditLogEntry,
    AuditLogListResponse,
    AuditLogResponse,
    BillingHeader,
    ChangeUserRoleRequest,
    InvitationListResponse,
    InvitationResponse,
    InvitationSummary,
    InviteUserRequest,
    OrganizationSettingsData,
    OrganizationSettingsResponse,
    OrganizationSettingsUpdate,
    RoleListResponse,
    RoleSummary,
    UserListResponse,
    UserResponse,
    UserSummary,
)
from adminprop.modules.administracion.service import (
    AuditLogQueryService,
    OrganizationSettingsService,
    RoleService,
    UserService,
    get_audit_log_query_service,
    get_organization_settings_service,
    get_role_service,
    get_user_service,
)
from adminprop.shared.auth.jwt import JWTPayload
from adminprop.shared.errors.codes import NotFoundException
from adminprop.shared.logging.json_logger import request_id_var
from adminprop.shared.rbac import requires_permission
from adminprop.shared.tenant import get_current_tenant

users_router = APIRouter(prefix="/v1/users", tags=["administracion"])
roles_router = APIRouter(prefix="/v1/roles", tags=["administracion"])
organization_settings_router = APIRouter(
    prefix="/v1/organization/settings", tags=["administracion"]
)
audit_logs_router = APIRouter(prefix="/v1/audit-logs", tags=["administracion"])

# sdd_03 §16 "Audit Logs": page/page_size (default 50, maximo 100) --
# UNICA excepcion de sdd_03 §Paginacion al resto de la API, cursor-based.
_AUDIT_LOG_PAGE_SIZE_DEFAULT = 50
_AUDIT_LOG_PAGE_SIZE_MAX = 100


def _request_id() -> str:
    return request_id_var.get() or ""


def _to_invitation_summary(row: InvitationRow) -> InvitationSummary:
    return InvitationSummary(
        id=row.id,
        email=row.email,
        role=row.role_name,
        status=row.status,
        expires_at=row.expires_at,
        created_at=row.created_at,
    )


def _to_user_summary(row: MemberRow) -> UserSummary:
    return UserSummary(
        id=row.user_id,
        email=row.email,
        full_name=row.full_name,
        role_name=row.role_name,
        status=row.status,
        created_at=row.created_at,
    )


def _to_role_summary(row: RoleRow) -> RoleSummary:
    return RoleSummary(
        id=row.id, name=row.name, permissions=row.permissions, is_system_role=row.is_system_role
    )


def _to_audit_log_entry(row: AuditLogRow) -> AuditLogEntry:
    return AuditLogEntry(
        id=row.id,
        user_id=row.user_id,
        user_email=row.user_email,
        action=row.action,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        before_state=row.before_state,
        after_state=row.after_state,
        request_id=row.request_id,
        created_at=row.created_at,
    )


def _to_settings_data(settings: dict) -> OrganizationSettingsData:
    """RF-04: `billing_header` puede no existir todavia en settings
    persistidos (`DEFAULT_ORGANIZATION_SETTINGS` no lo incluye) -- se
    serializa como todos los campos en `None`."""
    billing_header = settings.get("billing_header") or {}
    return OrganizationSettingsData(
        grace_day=settings["grace_day"],
        contract_expiry_notice_days=settings["contract_expiry_notice_days"],
        billing_header=BillingHeader(
            name=billing_header.get("name"),
            cuit=billing_header.get("cuit"),
            contact=billing_header.get("contact"),
        ),
    )


# ─── RF-01: invitaciones ─────────────────────────────────────────────────


@users_router.post(
    "/invite",
    status_code=status.HTTP_201_CREATED,
    response_model=InvitationResponse,
    dependencies=[Depends(requires_permission("user:manage"))],
)
async def invite_user(
    dto: InviteUserRequest,
    organization_id: UUID = Depends(get_current_tenant),
    service: UserService = Depends(get_user_service),
) -> InvitationResponse:
    """RF-01 + CA-07-01: invita un usuario del equipo con rol `admin` o
    `maintenance`. `409 USER_ALREADY_MEMBER` / `409 INVITATION_PENDING_EXISTS`."""
    invitation = await service.invite(
        organization_id=organization_id,
        email=dto.email,
        role_name=dto.role,
        request_id=_request_id(),
    )
    return InvitationResponse(data=_to_invitation_summary(invitation))


@users_router.get(
    "/invitations",
    response_model=InvitationListResponse,
    dependencies=[Depends(requires_permission("user:manage"))],
)
async def list_invitations(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    organization_id: UUID = Depends(get_current_tenant),
    service: UserService = Depends(get_user_service),
) -> InvitationListResponse:
    """RF-01: listado de invitaciones `pending` (paginado cursor-based)."""
    items, next_cursor = await service.list_invitations(
        organization_id=organization_id, cursor=cursor, limit=limit
    )
    return InvitationListResponse(
        data=[_to_invitation_summary(item) for item in items],
        meta={"next_cursor": next_cursor, "limit": limit},
    )


@users_router.post(
    "/invitations/{invitation_id}/resend",
    status_code=status.HTTP_201_CREATED,
    response_model=InvitationResponse,
    dependencies=[Depends(requires_permission("user:manage"))],
)
async def resend_invitation(
    invitation_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: UserService = Depends(get_user_service),
) -> InvitationResponse:
    """RF-01: revoca la invitacion anterior y emite una nueva."""
    invitation = await service.resend_invitation(
        organization_id=organization_id, invitation_id=invitation_id, request_id=_request_id()
    )
    return InvitationResponse(data=_to_invitation_summary(invitation))


@users_router.delete(
    "/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires_permission("user:manage"))],
)
async def revoke_invitation(
    invitation_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: UserService = Depends(get_user_service),
) -> None:
    """RF-01: revoca (`status='revoked'`) una invitacion pendiente."""
    await service.revoke_invitation(organization_id=organization_id, invitation_id=invitation_id)


# ─── RF-02: gestion de usuarios ──────────────────────────────────────────


@users_router.get(
    "",
    response_model=UserListResponse,
    dependencies=[Depends(requires_permission("user:manage"))],
)
async def list_users(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    organization_id: UUID = Depends(get_current_tenant),
    service: UserService = Depends(get_user_service),
) -> UserListResponse:
    """RF-02: miembros de la organizacion, paginado cursor-based."""
    items, next_cursor = await service.list_members(
        organization_id=organization_id, cursor=cursor, limit=limit
    )
    return UserListResponse(
        data=[_to_user_summary(item) for item in items],
        meta={"next_cursor": next_cursor, "limit": limit},
    )


@users_router.patch(
    "/{user_id}",
    response_model=UserResponse,
)
async def change_user_role(
    user_id: UUID,
    dto: ChangeUserRoleRequest,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("user:manage")),
    service: UserService = Depends(get_user_service),
) -> UserResponse:
    """RF-02 + CA-07-02: cambia el rol de un miembro. `422
    LAST_OWNER_REQUIRED` si es el unico owner activo. Cambiar a `owner`
    no esta permitido (`ChangeUserRoleRequest.role` ya lo rechaza con
    422 VALIDATION_ERROR via Pydantic `Literal`)."""
    member = await service.change_role(
        organization_id=organization_id,
        user_id=user_id,
        new_role_name=dto.role,
        actor_user_id=payload.sub,
    )
    return UserResponse(data=_to_user_summary(member))


@users_router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def deactivate_user(
    user_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("user:manage")),
    service: UserService = Depends(get_user_service),
) -> None:
    """RF-02 + CA-07-02: desactiva (soft) un miembro y revoca sus sesiones.
    `422 LAST_OWNER_REQUIRED` si es el unico owner activo."""
    await service.deactivate(
        organization_id=organization_id, user_id=user_id, actor_user_id=payload.sub
    )


# ─── RF-03: roles ─────────────────────────────────────────────────────────


@roles_router.get(
    "",
    response_model=RoleListResponse,
    dependencies=[Depends(requires_permission("role:read"))],
)
async def list_roles(
    organization_id: UUID = Depends(get_current_tenant),
    service: RoleService = Depends(get_role_service),
) -> RoleListResponse:
    """RF-03: los 3 roles de sistema de la organizacion, con sus
    `permissions[]`. Solo lectura en MVP (sin endpoint de escritura)."""
    roles = await service.list_roles(organization_id)
    return RoleListResponse(data=[_to_role_summary(role) for role in roles])


# ─── RF-04: configuracion de la organizacion ─────────────────────────────


@organization_settings_router.get(
    "",
    response_model=OrganizationSettingsResponse,
    dependencies=[Depends(requires_permission("organization:configure"))],
)
async def get_organization_settings(
    organization_id: UUID = Depends(get_current_tenant),
    service: OrganizationSettingsService = Depends(get_organization_settings_service),
) -> OrganizationSettingsResponse:
    """RF-04: `grace_day`, `contract_expiry_notice_days` y el encabezado
    de liquidaciones de la organizacion del JWT."""
    settings = await service.get_settings(organization_id)
    return OrganizationSettingsResponse(data=_to_settings_data(settings))


@organization_settings_router.put(
    "",
    response_model=OrganizationSettingsResponse,
)
async def update_organization_settings(
    dto: OrganizationSettingsUpdate,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("organization:configure")),
    service: OrganizationSettingsService = Depends(get_organization_settings_service),
) -> OrganizationSettingsResponse:
    """RF-04 + CA-07-05: actualiza `grace_day`, `contract_expiry_notice_days`
    y el encabezado de liquidaciones. `grace_day` rige desde el momento
    del cambio (RN-05), sin recalcular intereses ya imputados."""
    settings = await service.update_settings(
        organization_id,
        grace_day=dto.grace_day,
        contract_expiry_notice_days=dto.contract_expiry_notice_days,
        billing_name=dto.billing_name,
        billing_cuit=dto.billing_cuit,
        billing_contact=dto.billing_contact,
        actor_user_id=payload.sub,
    )
    return OrganizationSettingsResponse(data=_to_settings_data(settings))


# ─── RF-05: visor del log de auditoria ────────────────────────────────────


@audit_logs_router.get(
    "",
    response_model=AuditLogListResponse,
    dependencies=[Depends(requires_permission("audit:read"))],
)
async def list_audit_logs(
    organization_id: UUID = Depends(get_current_tenant),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=_AUDIT_LOG_PAGE_SIZE_DEFAULT, ge=1, le=_AUDIT_LOG_PAGE_SIZE_MAX),
    entity_type: str | None = Query(default=None),
    entity_id: UUID | None = Query(default=None),
    user_id: UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    service: AuditLogQueryService = Depends(get_audit_log_query_service),
) -> AuditLogListResponse:
    """RF-05 + CA-07-06: filtra por entidad, usuario, accion y rango de
    fechas; pagina con `page`/`page_size` (default 50, maximo 100 --
    sdd_03 §16, unica excepcion a la paginacion cursor-based del resto de
    la API). Permiso `audit:read` (owner y admin; `maintenance` no lo
    tiene -- 403 FORBIDDEN, CA-07-04)."""
    items, total = await service.list_entries(
        organization_id,
        page=page,
        page_size=page_size,
        entity_type=entity_type,
        entity_id=entity_id,
        user_id=user_id,
        action=action,
        date_from=date_from,
        date_to=date_to,
    )
    return AuditLogListResponse(
        data=[_to_audit_log_entry(item) for item in items],
        meta={"page": page, "page_size": page_size, "total": total},
    )


@audit_logs_router.get(
    "/{audit_log_id}",
    response_model=AuditLogResponse,
    dependencies=[Depends(requires_permission("audit:read"))],
)
async def get_audit_log(
    audit_log_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: AuditLogQueryService = Depends(get_audit_log_query_service),
) -> AuditLogResponse:
    """RF-05: detalle de un evento de auditoria. RN-D01: cross-tenant o
    inexistente -> 404 NOT_FOUND (nunca 403)."""
    entry = await service.get(organization_id, audit_log_id)
    if entry is None:
        raise NotFoundException()
    return AuditLogResponse(data=_to_audit_log_entry(entry))
