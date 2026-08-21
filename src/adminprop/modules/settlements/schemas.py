"""Pydantic schemas del modulo `settlements` -- PascalCase singular
(issue #29).

SDD: docs/sdd/features/spec_module_05_liquidaciones.md §RF-01/RF-02 +
core/sdd_03_api_contracts.md §11 "Liquidaciones".
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from adminprop.shared.errors.codes import ValidationError

# sdd_03 §11: "?period=&landlord_id=&status=" -- mismo patron que
# `modules/charges/schemas.py.parse_period`.
_PERIOD_PATTERN = re.compile(r"^\d{4}-\d{2}$")


def parse_period(period: str) -> date:
    """Convierte `YYYY-MM` a `date` (dia 1 del mes) -- reutilizado por el
    body de `SettlementGenerateRequest` y el query param `period` de
    `GET /settlements`. `400 VALIDATION_ERROR` con formato invalido;
    "mes no futuro" se valida en `service.py` (RF-02 §Validaciones)."""
    if not _PERIOD_PATTERN.match(period):
        raise ValidationError(field="period", message="El formato de period debe ser YYYY-MM.")
    year, month = period.split("-")
    return date(int(year), int(month), 1)


# ─── POST /settlements/generate ─────────────────────────────────────────


class SettlementGenerateRequest(BaseModel):
    """Body de POST /v1/settlements/generate -- sdd_03 §11:
    "{ landlord_id, period, exchange_rate? }"."""

    model_config = ConfigDict(extra="forbid")

    landlord_id: UUID
    period: str = Field(..., description="YYYY-MM")
    exchange_rate: Decimal | None = Field(default=None, gt=0)

    @field_validator("period")
    @classmethod
    def _period_valid_format(cls, v: str) -> str:
        parse_period(v)
        return v

    @property
    def period_date(self) -> date:
        return parse_period(self.period)


class SettlementGenerateAcceptedData(BaseModel):
    """docs/skills/api-endpoint.md: shape del 202 -- `{ <id>, status,
    estimated_completion_seconds }`."""

    settlement_id: UUID
    status: str
    estimated_completion_seconds: int


class SettlementGenerateAccepted(BaseModel):
    data: SettlementGenerateAcceptedData


# ─── GET /settlements/:id ────────────────────────────────────────────────


class SettlementLineItemDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    line_type: str
    property_id: UUID | None
    source_entity_type: str | None
    source_entity_id: UUID | None
    original_amount: Decimal
    original_currency: str
    amount_ars: Decimal
    description: str | None
    created_at: datetime


class SettlementPropertyGroup(BaseModel):
    """RF-04: `scope=per_property` -- una propiedad con sus lineas y el
    subtotal (`exports.PropertyGroup` serializado)."""

    model_config = ConfigDict(from_attributes=True)

    property_id: UUID
    property_label: str
    line_items: list[SettlementLineItemDetail]
    subtotal_ars: Decimal


class SettlementAttachmentSummary(BaseModel):
    """RF-03 (issue #30): metadata de un export ya generado (Excel/PDF),
    descargable via `GET /settlements/:id/export?format=`."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    file_name: str
    mime_type: str
    format: str
    created_at: datetime


class SettlementDetail(BaseModel):
    """RF-01/RF-02/RF-03/RF-04: totales + line items + estado del job
    (RF-01, trackeado fuera de `status`, ver `job_status.py`) +
    advertencias (CA-05-03) + bandera "requiere regeneracion" (CA-05-06,
    derivada -- ver `repository.list_needs_regeneration_flags`) +
    agrupacion por propiedad opcional (`scope=per_property`, RF-04) +
    adjuntos ya generados (Excel/PDF, issue #30)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    landlord_id: UUID
    period: date
    status: str
    job_status: str
    warnings: list[str]
    needs_regeneration: bool
    exchange_rate: Decimal | None
    total_collected: Decimal
    commission_total: Decimal
    charges_total: Decimal
    repairs_total: Decimal
    already_settled_total: Decimal
    net_amount: Decimal
    commission_pct_used: Decimal
    regenerated_count: int
    generated_by: UUID
    issued_at: datetime | None
    created_at: datetime
    updated_at: datetime
    line_items: list[SettlementLineItemDetail]
    property_groups: list[SettlementPropertyGroup] | None = Field(
        default=None, description="Solo presente con ?scope=per_property (RF-04)."
    )
    attachments: list[SettlementAttachmentSummary] = Field(default_factory=list)


class SettlementResponse(BaseModel):
    data: SettlementDetail


# ─── GET /settlements ────────────────────────────────────────────────────


class SettlementSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    landlord_id: UUID
    period: date
    status: str
    net_amount: Decimal
    commission_pct_used: Decimal
    needs_regeneration: bool = False
    created_at: datetime


class SettlementListResponse(BaseModel):
    """RF-01: `GET /settlements?period=&landlord_id=&status=` -- sin
    `meta`: el volumen mensual por organizacion (MVP) no requiere
    paginar (mismo criterio que `ChargeVerificationResponse`)."""

    data: list[SettlementSummary]


# ─── POST /settlements/:id/regenerate — RF-03 (issue #30) ────────────────


class SettlementRegenerateRequest(BaseModel):
    """Body de POST /v1/settlements/:id/regenerate -- sdd_03 §11: el TC
    nuevo es opcional (si no viene, se mantiene el de la liquidacion)."""

    model_config = ConfigDict(extra="forbid")

    exchange_rate: Decimal | None = Field(default=None, gt=0)


class SettlementRegenerateAccepted(BaseModel):
    data: SettlementGenerateAcceptedData
