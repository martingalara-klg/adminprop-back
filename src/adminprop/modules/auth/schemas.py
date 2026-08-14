"""Pydantic schemas del modulo auth -- PascalCase singular (issue #6).

SDD: core/sdd_03_api_contracts.md §1 "Autenticacion".
"""

from __future__ import annotations

import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    """sdd_03 §1: `200 { data: { status, user, organizations[] } }`.

    `status` == "authenticated": cookies de sesion seteadas, `user` presente.
    `status` == "organization_selection_required": decision de
    implementacion (no explicita en sdd_03) para el caso "usuario
    multi-org sin `organization_id` en el body" -- no se emite JWT/cookies,
    el cliente reintenta el login con `organization_id` elegido de
    `organizations[]`.
    """

    status: str
    user: UserSummary | None
    organizations: list[OrganizationSummary]


class LoginResponse(BaseModel):
    data: LoginResponseData


class RefreshResponseData(BaseModel):
    status: str = "refreshed"


class RefreshResponse(BaseModel):
    data: RefreshResponseData
