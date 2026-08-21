"""Pydantic schemas del modulo `charges` -- PascalCase singular (issue #28).

SDD: docs/sdd/features/spec_module_05_liquidaciones.md §RF-05 +
core/sdd_03_api_contracts.md §10 "Cargos del mes".
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from adminprop.shared.errors.codes import ValidationError

# spec_data_model.md §Capa 6 "recurring_charges.charge_type".
ChargeType = Literal["rentas", "municipalidad", "otro"]

# sdd_03 §10: "?period=YYYY-MM" -- mismo patron que
# `modules/payments/router.py._PERIOD_PATTERN`.
_PERIOD_PATTERN = re.compile(r"^\d{4}-\d{2}$")


def parse_period(period: str) -> date:
    """Convierte `YYYY-MM` a `date` (dia 1 del mes) -- reutilizado por
    `schemas.py` (body de `ChargeEntryCreate`) y `router.py` (query param
    de `GET /charge-entries`). `400 VALIDATION_ERROR` con formato
    invalido; "mes no futuro" se valida en `service.py` (RF-05
    §Validaciones), no aca."""
    if not _PERIOD_PATTERN.match(period):
        raise ValidationError(field="period", message="El formato de period debe ser YYYY-MM.")
    year, month = period.split("-")
    return date(int(year), int(month), 1)


# ─── RecurringCharge (conceptos) — ABM por propiedad ───────────────────────


class RecurringChargeCreate(BaseModel):
    """Body de POST /v1/properties/:id/recurring-charges. `property_id`
    viene del path, nunca del body (RN-D01)."""

    model_config = ConfigDict(extra="forbid")

    charge_type: ChargeType = Field(...)
    label: str = Field(..., min_length=1, max_length=255)


class RecurringChargeUpdate(BaseModel):
    """Body de PATCH /v1/recurring-charges/:id -- sdd_03 §10: "(label,
    is_active)". `charge_type`/`property_id` no son editables (el
    concepto se re-crea si cambia de tipo)."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(None, min_length=1, max_length=255)
    is_active: bool | None = Field(None)


class RecurringChargeDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    property_id: UUID
    charge_type: str
    label: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class RecurringChargeResponse(BaseModel):
    data: RecurringChargeDetail


class RecurringChargeListResponse(BaseModel):
    """RF-05: ABM de conceptos por propiedad -- sin `meta` (conjunto
    acotado, mismo criterio que `PropertyServiceAccountListResponse`)."""

    data: list[RecurringChargeDetail]


# ─── ChargeEntry (carga mensual) ───────────────────────────────────────────


class ChargeEntryCreate(BaseModel):
    """Body de POST /v1/recurring-charges/:id/entries -- sdd_03 §10:
    "{ period, amount, notes }"."""

    model_config = ConfigDict(extra="forbid")

    period: str = Field(..., description="YYYY-MM")
    amount: Decimal = Field(..., ge=0)
    notes: str | None = Field(default=None)

    @field_validator("period")
    @classmethod
    def _period_valid_format(cls, v: str) -> str:
        parse_period(v)
        return v

    @property
    def period_date(self) -> date:
        return parse_period(self.period)


class ChargeEntryUpdate(BaseModel):
    """Body de PATCH /v1/charge-entries/:id -- RN-D04: correccion
    auditada. `recurring_charge_id`/`period` son inmutables (una
    correccion cambia el importe/las notas, no de que concepto o mes se
    trata)."""

    model_config = ConfigDict(extra="forbid")

    amount: Decimal | None = Field(None, ge=0)
    notes: str | None = Field(default=None)


class ChargeEntryDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    recurring_charge_id: UUID
    period: date
    amount: Decimal
    notes: str | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class ChargeEntryResponse(BaseModel):
    data: ChargeEntryDetail


# ─── Vista de verificacion mensual — RF-05/CA-05-08 ────────────────────────


class ChargeVerificationItem(BaseModel):
    """RF-05/CA-05-08: una fila por concepto activo -- `has_entry`
    discrimina "propiedad con cargo cargado" de "propiedad que falta"
    (el checklist mensual de la secretaria)."""

    model_config = ConfigDict(from_attributes=True)

    recurring_charge_id: UUID
    property_id: UUID
    charge_type: str
    label: str
    has_entry: bool
    charge_entry_id: UUID | None
    amount: Decimal | None
    notes: str | None


class ChargeVerificationResponse(BaseModel):
    """Sin `meta`: el checklist mensual no pagina (acotado a los
    conceptos activos de la organizacion en ese mes)."""

    data: list[ChargeVerificationItem]
