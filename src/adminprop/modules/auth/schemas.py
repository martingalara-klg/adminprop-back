"""Pydantic schemas del modulo auth -- PascalCase singular (issue #6).

SDD: core/sdd_03_api_contracts.md §1 "Autenticacion".
"""

from __future__ import annotations

import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from adminprop.shared.auth.passwords import validate_password_policy

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LoginRequest(BaseModel):
    """Body de POST /v1/auth/login. sdd_03 §1."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=1, max_length=255)
    # Opcional: solo relevante si el usuario pertenece a mas de una
    # organizacion (sdd_03 §1 "el login incluye la seleccion de
    # organizacion"). Unico caso donde un identificador de organizacion
    # viaja en el body -- no es un `organization_id` de recurso protegido,
    # es la seleccion explicita del propio usuario autenticandose.
    organization_id: UUID | None = None

    @field_validator("email")
    @classmethod
    def _valid_email_format(cls, value: str) -> str:
        if not _EMAIL_RE.match(value):
            raise ValueError("email invalido.")
        return value.lower()


class OrganizationSummary(BaseModel):
    """Organizacion a la que el usuario autenticado pertenece (activa)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    role: str


class UserSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str


class LoginResponseData(BaseModel):
    """sdd_03 §1 v1.6: `200 { data: { status, user, organizations[],
    permissions[], is_super_admin } }`.

    `status` == "authenticated": cookies de sesion seteadas, `user`
    presente, `permissions`/`is_super_admin` son los mismos valores que
    porta el JWT emitido (issue #84 -- el front no puede leer el JWT
    porque vive en cookie HttpOnly, decision #20).
    `status` == "organization_selection_required": decision de
    implementacion (no explicita en sdd_03) para el caso "usuario
    multi-org sin `organization_id` en el body" -- no se emite JWT/cookies,
    `permissions`/`is_super_admin` van `None` (todavia no hay organizacion
    resuelta), el cliente reintenta el login con `organization_id` elegido
    de `organizations[]`.
    """

    status: str
    user: UserSummary | None
    organizations: list[OrganizationSummary]
    permissions: list[str] | None = None
    is_super_admin: bool | None = None


class LoginResponse(BaseModel):
    data: LoginResponseData


class RefreshResponseData(BaseModel):
    status: str = "refreshed"


class RefreshResponse(BaseModel):
    data: RefreshResponseData


# ─── issue #8 — Activacion de cuenta ───────────────────────────────────────


class InvitationDetailResponseData(BaseModel):
    """sdd_03 §1: `GET /auth/invitation/:token -> 200 { data: { email,
    organization_name, role_name } }`."""

    email: str
    organization_name: str
    role_name: str


class InvitationDetailResponse(BaseModel):
    data: InvitationDetailResponseData


class AcceptInvitationRequest(BaseModel):
    """Body de POST /v1/auth/accept-invitation. sdd_03 §1: "nombre y
    password (politica sdd_04 §2.2)"."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(..., min_length=1)
    full_name: str = Field(..., min_length=2, max_length=120)
    # min_length se enforza en `validate_password_policy` (mensaje en
    # espanol, sdd_04 §2.2) -- no duplicado aca via Field(min_length=...)
    # para que ese branch del validator sea alcanzable (y cubierto por
    # tests), en vez de quedar shadowed por la constraint de Pydantic.
    password: str = Field(..., min_length=1, max_length=255)

    @field_validator("password")
    @classmethod
    def _password_policy(cls, value: str) -> str:
        validate_password_policy(value)
        return value


class AcceptInvitationOrganization(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    role: str


class AcceptInvitationResponseData(BaseModel):
    """CA-00-03: "el owner queda logueado con rol owner" -- mismo shape
    conceptual que `LoginResponseData`, pero con una sola organizacion
    (la recien activada) en vez de la lista de todas las del usuario.

    sdd_03 §1 v1.6 (issue #84): `permissions`/`is_super_admin` son los
    mismos valores que porta el JWT emitido en este mismo request --
    a diferencia de `login`, este flujo siempre emite JWT (nunca hay
    seleccion de organizacion pendiente), por lo que no son opcionales.
    `is_super_admin` siempre `false` (accept-invitation nunca activa
    cuentas de Super Admin).
    """

    status: str = "authenticated"
    user: UserSummary
    organization: AcceptInvitationOrganization
    permissions: list[str]
    is_super_admin: bool = False


class AcceptInvitationResponse(BaseModel):
    data: AcceptInvitationResponseData


# ─── issue #8 — Forgot / reset password ────────────────────────────────────


class ForgotPasswordRequest(BaseModel):
    """Body de POST /v1/auth/forgot-password. sdd_03 §1."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=3, max_length=255)

    @field_validator("email")
    @classmethod
    def _valid_email_format(cls, value: str) -> str:
        if not _EMAIL_RE.match(value):
            raise ValueError("email invalido.")
        return value.lower()


class ForgotPasswordResponseData(BaseModel):
    message: str


class ForgotPasswordResponse(BaseModel):
    data: ForgotPasswordResponseData


class ResetPasswordTokenResponseData(BaseModel):
    """sdd_03 §1: `GET /auth/reset-password/:token -> 200 | 404 | 410`."""

    email: str


class ResetPasswordTokenResponse(BaseModel):
    data: ResetPasswordTokenResponseData


class ResetPasswordRequest(BaseModel):
    """Body de POST /v1/auth/reset-password."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(..., min_length=1)
    # min_length se enforza en `validate_password_policy` (mensaje en
    # espanol, sdd_04 §2.2) -- no duplicado aca via Field(min_length=...)
    # para que ese branch del validator sea alcanzable (y cubierto por
    # tests), en vez de quedar shadowed por la constraint de Pydantic.
    password: str = Field(..., min_length=1, max_length=255)

    @field_validator("password")
    @classmethod
    def _password_policy(cls, value: str) -> str:
        validate_password_policy(value)
        return value


class ResetPasswordResponseData(BaseModel):
    message: str = "Tu contrasena fue actualizada correctamente."


class ResetPasswordResponse(BaseModel):
    data: ResetPasswordResponseData


# ─── issue #84 — GET /auth/me (rehidratar sesion) ──────────────────────────


class MeOrganization(BaseModel):
    """Organizacion activa del JWT -- `None` para sesiones de Super Admin
    (el JWT de `/superadmin/*` no lleva `org`, sdd_03 §Convenciones)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str


class MeResponseData(BaseModel):
    """sdd_03 §1 v1.6: `GET /auth/me -> 200 { data: { user, organization,
    role, permissions[], is_super_admin } }`.

    `organization`/`role` son `None` solo para Super Admin. `permissions`
    se resuelve en vivo contra la membresia actual (no el contenido
    cacheado del JWT) -- si el rol perdio/gano permisos despues de emitido
    el JWT, esta respuesta ya refleja el estado vigente.
    """

    user: UserSummary
    organization: MeOrganization | None
    role: str | None
    permissions: list[str]
    is_super_admin: bool


class MeResponse(BaseModel):
    data: MeResponseData
