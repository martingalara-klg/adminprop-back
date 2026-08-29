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
# RN-P09 (issue #119): 'manual' (default, operador) | 'initial_load'
# (carga inicial de un contrato en curso -- excluido de liquidaciones,
# recibo y anulacion). No se acepta en el body de `PaymentCreate` -- todo
# cobro registrado vía `POST /rent-periods/:id/payments` nace `manual`.
PaymentOrigin = Literal["manual", "initial_load"]


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
    origin: PaymentOrigin


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


# ─── RF-05 (anulacion) -- issue #23 ────────────────────────────────────


class PaymentVoidRequest(BaseModel):
    """Body de POST /v1/payments/:id/void. RF-05: "motivo obligatorio"."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1)


class PaymentDetail(PaymentSummary):
    """Igual a `PaymentSummary` + los campos de anulacion (RN-D04) --
    "el cobro queda visible con marca de anulado" (CA-04-07)."""

    voided_at: datetime | None
    voided_by: UUID | None


class PaymentVoidResponse(BaseModel):
    data: PaymentDetail


# ─── RF-02 (panel del mes) -- issue #23 ────────────────────────────────


# spec_data_model.md §Capa 4 "rent_periods.status".
RentPeriodStatusLiteral = Literal["pending", "partial", "paid"]


class RentPeriodSummary(BaseModel):
    """Item de GET /v1/rent-periods y de GET /v1/rent-periods/:id -- RF-02:
    "cada fila muestra: propiedad, inquilino, monto, saldo, dias de mora e
    interes sugerido al dia de hoy"."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    contract_id: UUID
    property_id: UUID
    landlord_id: UUID
    renter_id: UUID
    period: date
    amount_due: Decimal
    currency: str
    status: RentPeriodStatusLiteral
    paid_total: Decimal
    balance: Decimal
    in_arrears: bool
    days_late: int
    suggested_interest: Decimal


class RentPeriodResponse(BaseModel):
    data: RentPeriodSummary


class RentPeriodListResponse(BaseModel):
    data: list[RentPeriodSummary]
    meta: dict


# ─── RF-02 (detalle del periodo con payments[]) -- issue #87 ───────────


class RentPeriodDetail(RentPeriodSummary):
    """Data de `GET /v1/rent-periods/:id` (v1.7) -- `RentPeriodSummary` +
    `payments[]`: el historial de cobros del periodo, ordenado por
    `payment_date`. Incluye cobros ANULADOS (`voided_at`/`voided_by`
    poblados, via `PaymentDetail`) -- CA-04-07: "el cobro queda visible
    con marca de anulado" pasa a ser verificable por API. El motivo de
    anulacion no viaja aca -- vive en `audit_logs` (issue #23),
    consultable via el visor de auditoria. `GET /v1/rent-periods`
    (panel/listado) no cambia -- sigue devolviendo `RentPeriodSummary`
    sin este campo."""

    payments: list[PaymentDetail]


class RentPeriodDetailResponse(BaseModel):
    data: RentPeriodDetail


# ─── RF-06/CA-02-05 (estado de deuda) -- issue #23 ─────────────────────


class DebtEntryData(BaseModel):
    """RF-06: "por inquilino y propiedad, periodos adeudados, saldo, dias
    de mora e interes sugerido acumulado" -- una fila por contrato con
    deuda (agregada sobre sus `rent_periods` `pending`/`partial`)."""

    model_config = ConfigDict(from_attributes=True)

    contract_id: UUID
    property_id: UUID
    landlord_id: UUID
    renter_id: UUID
    periods_overdue: int
    balance: Decimal
    days_late: int
    suggested_interest: Decimal


class DebtListResponse(BaseModel):
    data: list[DebtEntryData]
    meta: dict


class RenterDebtResponse(BaseModel):
    """CA-02-05: `GET /renters/:id/debt` -- sin `meta`: la ficha del
    inquilino no pagina (un inquilino tiene un numero acotado de
    contratos)."""

    data: list[DebtEntryData]
