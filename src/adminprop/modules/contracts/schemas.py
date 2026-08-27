"""Pydantic schemas del modulo contratos -- PascalCase singular (issue #17).

SDD: docs/sdd/features/spec_module_03_contratos.md §"Validaciones" +
core/sdd_03_api_contracts.md §8 "Contratos".
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# RF-02: "moneda (ARS/USD)".
Currency = Literal["ARS", "USD"]
# RF-02: "indice de referencia (icl / ipc_cordoba / otro) -- informativo".
AdjustmentIndex = Literal["icl", "ipc_cordoba", "otro"]

# RF-02 §"Validaciones": "duracion maxima razonable (<= 10 anios)".
_MAX_CONTRACT_DURATION_DAYS = 10 * 366


class ContractCreate(BaseModel):
    """Body de POST /v1/contracts.

    CA-03-01: ARS con % de mora, frecuencia e indice de ajuste; nace en
    `draft` (el service fuerza `status="draft"`, no es un campo aceptado
    aca -- RN-02). CA-03-03: USD con frecuencia/indice -> 400
    VALIDATION_ERROR (RN-03/RN-C02), enforzado por `_validate_usd_no_adjustment`
    antes de llegar al service.
    """

    model_config = ConfigDict(extra="forbid")

    property_id: UUID = Field(...)
    renter_id: UUID = Field(...)
    currency: Currency = Field(...)
    initial_amount: Decimal = Field(..., gt=0)
    start_date: date = Field(...)
    end_date: date = Field(...)
    daily_late_fee_pct: Decimal = Field(..., ge=0)
    adjustment_frequency_months: int | None = Field(None, gt=0)
    adjustment_index: AdjustmentIndex | None = Field(None)
    adjustment_index_notes: str | None = Field(None)
    notes: str | None = Field(None)
    # RN-08/RN-C06 (issue #100): alta de contrato en curso -- opcionales,
    # solo validos juntos (`_validate_current_amount_pair`). Aplican a ARS
    # y USD por igual (RN-03/RN-C02 solo excluye a USD del ajuste
    # PERIODICO automatico, no de esta declaracion puntual).
    current_amount: Decimal | None = Field(None, gt=0)
    current_amount_since: date | None = Field(None)

    @model_validator(mode="after")
    def _validate_date_range(self) -> ContractCreate:
        if self.end_date <= self.start_date:
            raise ValueError("end_date debe ser posterior a start_date.")
        if (self.end_date - self.start_date).days > _MAX_CONTRACT_DURATION_DAYS:
            raise ValueError("La duracion del contrato no puede superar los 10 anios.")
        return self

    @model_validator(mode="after")
    def _validate_current_amount_pair(self) -> ContractCreate:
        # RN-08/RN-C06, CA-03-15: `current_amount`/`current_amount_since`
        # solo son validos juntos -- uno sin el otro es 400
        # VALIDATION_ERROR (mismo criterio de shape-validation que
        # `_validate_usd_no_adjustment`).
        has_amount = self.current_amount is not None
        has_since = self.current_amount_since is not None
        if has_amount != has_since:
            raise ValueError("current_amount y current_amount_since solo son validos juntos.")
        if has_since:
            # RN-08/RN-C06: `current_amount_since` se normaliza al dia 1
            # de su mes (mismo criterio que `due_period` de
            # ContractAdjustment, CHECK date_trunc de la migracion #16).
            # CA-03-14 (`>= start_date` y `<= hoy`, ambos 400
            # INVALID_DATE_RANGE): se valida en `service.py.create`, no
            # aca -- ese error.code especifico esta reservado para
            # `AdminPropException` (mismo criterio que
            # `ContractOverlapException`, que tampoco vive en Pydantic).
            self.current_amount_since = date(
                self.current_amount_since.year, self.current_amount_since.month, 1
            )
        return self

    @model_validator(mode="after")
    def _validate_usd_no_adjustment(self) -> ContractCreate:
        # RN-03/RN-C02, CA-03-03: USD nunca tiene configuracion de ajuste.
        if self.currency == "USD" and (
            self.adjustment_frequency_months is not None or self.adjustment_index is not None
        ):
            raise ValueError("Un contrato en USD no puede tener frecuencia ni indice de ajuste.")
        return self

    @model_validator(mode="after")
    def _validate_adjustment_index_notes(self) -> ContractCreate:
        # RF-02 §"Validaciones": nota obligatoria si el indice es "otro".
        if self.adjustment_index == "otro" and not self.adjustment_index_notes:
            raise ValueError(
                "adjustment_index_notes es obligatoria cuando adjustment_index es 'otro'."
            )
        return self


class ContractUpdate(BaseModel):
    """Body de PATCH /v1/contracts/:id.

    sdd_03 §8: "solo notes/metadata; montos NUNCA (RN-C04)". `current_amount`
    se acepta a nivel de schema deliberadamente (para poder distinguir,
    en el service, "el cliente intento cambiar el monto" -> 422
    BUSINESS_RULE_VIOLATION -- CA-03-06) en vez de que Pydantic lo
    rechace con un generico 400 VALIDATION_ERROR por `extra="forbid"`.
    `end_date` es la unica condicion economica editable (RF-03: "fechas
    de fin se pueden extender, quedando auditado") -- sin restriccion de
    estado, el service audita el cambio.
    """

    model_config = ConfigDict(extra="forbid")

    notes: str | None = Field(None)
    end_date: date | None = Field(None)
    current_amount: Decimal | None = Field(None, gt=0)


class ContractTerminateRequest(BaseModel):
    """Body de POST /v1/contracts/:id/terminate. RF-03: "rescision
    anticipada con motivo"."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=3, max_length=500)


class ContractSummary(BaseModel):
    """Item de GET /v1/contracts y respuesta de POST/PATCH/activate/terminate."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    property_id: UUID
    renter_id: UUID
    currency: str
    initial_amount: Decimal
    current_amount: Decimal
    start_date: date
    end_date: date
    daily_late_fee_pct: Decimal
    adjustment_frequency_months: int | None
    adjustment_index: str | None
    adjustment_index_notes: str | None
    status: str
    notes: str | None
    created_at: datetime
    updated_at: datetime


class ContractResponse(BaseModel):
    data: ContractSummary


class ContractListResponse(BaseModel):
    data: list[ContractSummary]
    meta: dict
