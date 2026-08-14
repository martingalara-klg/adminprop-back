"""Pydantic schemas del modulo administracion -- PascalCase singular (issue #9).

SDD: core/sdd_03_api_contracts.md §3 "Usuarios y Roles" + §4 "Configuracion
de la Organizacion". docs/sdd/features/spec_module_07_administracion.md
§"Validaciones".
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ─── Usuarios e invitaciones (RF-01, RF-02) ─────────────────────────────


class InviteUserRequest(BaseModel):
    """Body de POST /v1/users/invite.

    RF-01: el rol `owner` JAMAS se acepta aca -- la transferencia de
    owner es exclusivamente via Super Admin en MVP (sdd_03 §1
    "el rol owner solo se transfiere via Super Admin").
    """

    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=3, max_length=255)
    role: Literal["admin", "maintenance"]

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        if "@" not in value or " " in value:
            raise ValueError("email invalido.")
        return value.lower()


class InvitationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    role: str
    status: str
    expires_at: datetime
    created_at: datetime


class InvitationResponse(BaseModel):
    data: InvitationSummary


class InvitationListResponse(BaseModel):
    """RF-01: listado de invitaciones `pending` (paginado cursor-based)."""

    data: list[InvitationSummary]
    meta: dict


class UserSummary(BaseModel):
    """Item de GET /v1/users -- `id` es el `user_id` (RF-02: PATCH/DELETE
    /users/:id operan sobre ese mismo id)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str
    role_name: str
    status: str
    created_at: datetime


class UserListResponse(BaseModel):
    data: list[UserSummary]
    meta: dict


class UserResponse(BaseModel):
    data: UserSummary


class ChangeUserRoleRequest(BaseModel):
    """Body de PATCH /v1/users/:id.

    RF-02: cambiar el rol A `owner` no esta permitido via este endpoint
    -- `Literal` ya rechaza cualquier otro valor con 422 VALIDATION_ERROR.
    """

    model_config = ConfigDict(extra="forbid")

    role: Literal["admin", "maintenance"]


# ─── Roles (RF-03) ───────────────────────────────────────────────────────


class RoleSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    permissions: list[str]
    is_system_role: bool


class RoleListResponse(BaseModel):
    """RF-03: los 3 roles de sistema, sin paginacion (son un numero fijo)."""

    data: list[RoleSummary]


# ─── Configuracion de la organizacion (RF-04) ───────────────────────────


class BillingHeader(BaseModel):
    """Encabezado de liquidaciones -- usado por los exports del Modulo 5.

    `billing_header` no existe todavia en `DEFAULT_ORGANIZATION_SETTINGS`
    (`modules/superadmin/provisioning.py`) -- su ausencia en el JSON
    persistido se serializa como todos los campos en `None`.
    """

    name: str | None = None
    cuit: str | None = None
    contact: str | None = None


class OrganizationSettingsData(BaseModel):
    grace_day: int
    contract_expiry_notice_days: int
    billing_header: BillingHeader


class OrganizationSettingsResponse(BaseModel):
    data: OrganizationSettingsData


_CUIT_MULTIPLIERS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)


def _cuit_check_digit(digits: str) -> int:
    total = sum(int(d) * m for d, m in zip(digits, _CUIT_MULTIPLIERS, strict=True))
    remainder = total % 11
    check = 11 - remainder
    if check == 11:
        return 0
    return check


class OrganizationSettingsUpdate(BaseModel):
    """Body de PUT /v1/organization/settings.

    Validaciones: spec_module_07_administracion.md §"Validaciones"
    (`grace_day` 1-28, `contract_expiry_notice_days` 7-365, nombre <=120,
    contacto <=200, CUIT con digito verificador valido).
    """

    model_config = ConfigDict(extra="forbid")

    grace_day: int = Field(..., ge=1, le=28)
    contract_expiry_notice_days: int = Field(..., ge=7, le=365)
    billing_name: str | None = Field(None, max_length=120)
    billing_cuit: str | None = Field(None)
    billing_contact: str | None = Field(None, max_length=200)

    @field_validator("billing_cuit")
    @classmethod
    def _valid_cuit(cls, value: str | None) -> str | None:
        """Algoritmo estandar de digito verificador CUIT argentino:
        multiplicadores [5,4,3,2,7,6,5,4,3,2] sobre los primeros 10
        digitos, modulo 11 -- rechaza cualquier CUIT cuyo digito
        verificador calculado no coincida con el digito 11 (incluido el
        caso borde en el que el modulo da 10, que no tiene digito
        verificador valido para personas juridicas comunes)."""
        if value is None:
            return value
        digits = value.replace("-", "").strip()
        if len(digits) != 11 or not digits.isdigit():
            raise ValueError("CUIT invalido.")
        expected = _cuit_check_digit(digits[:10])
        if expected == 10 or int(digits[10]) != expected:
            raise ValueError("CUIT invalido.")
        return digits
