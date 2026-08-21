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


class SettlementDetail(BaseModel):
    """RF-01/RF-02: totales + line items + estado del job (RF-01,
    trackeado fuera de `status`, ver `job_status.py`) + advertencias
    (CA-05-03, "con periodos impagos o cargos faltantes termina
    `with_errors` y las advertencias se listan en el detalle"). El campo
    de adjuntos (Excel/PDF) existe en el schema del SDD pero queda vacio
    hasta el issue #30 (exports), tal como indica el alcance de esta
    tarea."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    landlord_id: UUID
    period: date
    status: str
    job_status: str
    warnings: list[str]
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
    attachments: list[dict] = Field(
        default_factory=list,
        description="Vacio hasta el issue #30 (exports Excel/PDF).",
    )


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
    created_at: datetime


class SettlementListResponse(BaseModel):
    """RF-01: `GET /settlements?period=&landlord_id=&status=` -- sin
    `meta`: el volumen mensual por organizacion (MVP) no requiere
    paginar (mismo criterio que `ChargeVerificationResponse`)."""

    data: list[SettlementSummary]
