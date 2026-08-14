"""Pydantic schemas del modulo superadmin -- PascalCase singular (issue #7).

SDD: core/sdd_03_api_contracts.md §2 "Super Admin".
"""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class OrganizationCreate(BaseModel):
    """Body de POST /v1/superadmin/organizations. RF-02."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=120)
    timezone: str = Field(default="America/Argentina/Cordoba", min_length=1, max_length=64)


class OrganizationSummary(BaseModel):
    """Item de GET /v1/superadmin/organizations (RF-01 dashboard)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    status: str
    timezone: str
    created_at: datetime
    owner_email: str | None = None


class OrganizationDetail(OrganizationSummary):
    """GET /v1/superadmin/organizations/:id -- agrega settings + updated_at."""

    model_config = ConfigDict(from_attributes=True)

    settings: dict
    updated_at: datetime


class OrganizationResponse(BaseModel):
    data: OrganizationDetail


class OrganizationListResponse(BaseModel):
    """RF-01: listado paginado (cursor-based, sdd_03 §"Paginacion")."""

    data: list[OrganizationSummary]
    meta: dict


class InviteOwnerRequest(BaseModel):
    """Body de POST /v1/superadmin/organizations/:id/invite-owner. RF-03."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=3, max_length=255)

    @field_validator("email")
    @classmethod
    def _valid_email_format(cls, value: str) -> str:
        if not _EMAIL_RE.match(value):
            raise ValueError("email invalido.")
        return value.lower()


class InvitationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    status: str
    expires_at: datetime


class InvitationResponse(BaseModel):
    data: InvitationSummary


class OrganizationStatusChangeRequest(BaseModel):
    """Body de POST .../disable y .../enable.

    RN-05 (spec_module_00_superadmin.md): "las operaciones del Super Admin
    se auditan siempre ... con actor y motivo" -- `reason` es obligatorio
    para que quede constancia del motivo (TODO(#10): persistencia real en
    audit_logs, hoy va al logger estructurado -- ver router.py).
    """

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=3, max_length=500)
