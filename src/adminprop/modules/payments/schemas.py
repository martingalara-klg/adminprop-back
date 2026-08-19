"""Pydantic schemas del modulo de cobranzas -- PascalCase singular (issue #22).

SDD: docs/sdd/features/spec_module_04_cobranzas.md §RF-03/RF-04 +
core/sdd_03_api_contracts.md §9 "Cobranzas".
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# sdd_03 §9: "cash"/"transfer".
PaymentMethod = Literal["cash", "transfer"]
# spec_data_model.md §Capa 4 "payments": moneda del pago recibido.
PaymentCurrency = Literal["ARS", "USD"]
# RN-P07: destino del cobro -- "ya rendido" vs. entra a la administracion.
PaymentDestination = Literal["agency_account", "landlord_account"]


class PaymentCreate(BaseModel):
    """Body de POST /v1/rent-periods/:id/payments.

    CA-04-03/04/05/06: fecha de pago, medio, moneda del pago, importe (a
    capital, en la moneda del contrato), TC si la moneda difiere, destino,
    interes cobrado (decision del operador, RN-P04) y notas.
    """

    model_config = ConfigDict(extra="forbid")

    payment_date: date = Field(...)
    method: PaymentMethod = Field(...)
    payment_currency: PaymentCurrency = Field(...)
    amount: Decimal = Field(..., gt=0)
    exchange_rate: Decimal | None = Field(default=None, gt=0)
    destination: PaymentDestination = Field(...)
    # RN-P04: el operador decide -- igual al sugerido, cero (perdon
    # total) o un valor intermedio (perdon parcial). "Validaciones":
    # charged_interest >= 0, sin tope impuesto por el sistema.
    charged_interest: Decimal = Field(..., ge=0)
    notes: str | None = Field(default=None)

    @field_validator("payment_date")
    @classmethod
    def _payment_date_not_future(cls, v: date) -> date:
        # spec_module_04_cobranzas.md §"Validaciones": "payment_date: no
        # futura; puede ser anterior a hoy (carga diferida)."
        if v > datetime.now(tz=UTC).date():
            raise ValueError("payment_date no puede ser una fecha futura.")
        return v


class PaymentSummary(BaseModel):
    """Item de respuesta de POST /v1/rent-periods/:id/payments."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    rent_period_id: UUID
    payment_date: date
    method: str
    payment_currency: str
    amount: Decimal
    exchange_rate: Decimal | None
    destination: str
    suggested_interest: Decimal
    charged_interest: Decimal
    forgiven_interest: Decimal
    days_late: int
    notes: str | None
    created_by: UUID
    created_at: datetime


class PaymentResponse(BaseModel):
    data: PaymentSummary


class InterestPreviewData(BaseModel):
    """RF-04: interes sugerido a `payment_date` -- RN-P02/P03."""

    rent_period_id: UUID
    payment_date: date
    balance: Decimal
    days_late: int
    suggested_interest: Decimal


class InterestPreviewResponse(BaseModel):
    data: InterestPreviewData
