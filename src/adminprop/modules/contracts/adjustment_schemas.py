"""Pydantic schemas de `ContractAdjustment` -- PascalCase singular (issue #18).

SDD: docs/sdd/features/spec_module_03_contratos.md §RF-04 +
core/sdd_03_api_contracts.md §8 "Contratos" (GET .../adjustments,
GET /adjustments, POST /adjustments/:id/apply).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# RF-02 §"Validaciones": "pct del ajuste... tope de sanidad ±500%".
_PCT_SANITY_MIN = Decimal(-500)
_PCT_SANITY_MAX = Decimal(500)


class AdjustmentApplyRequest(BaseModel):
    """Body de POST /v1/adjustments/:id/apply.

    `pct` se acepta como opcional a nivel de schema (mismo criterio que
    `ContractUpdate.current_amount`, issue #17) para poder distinguir "no
    lo mando" -> 400 ADJUSTMENT_PCT_REQUIRED (sdd_03) en vez de un
    generico 422 VALIDATION_ERROR de Pydantic por campo requerido. Puede
    ser negativo (deflacion/renegociacion, RF-02 §"Validaciones") dentro
    del tope de sanidad ±500%.
    """

    model_config = ConfigDict(extra="forbid")

    pct: Decimal | None = Field(None, ge=_PCT_SANITY_MIN, le=_PCT_SANITY_MAX)


class AdjustmentSummary(BaseModel):
    """Item de GET /contracts/:id/adjustments, GET /adjustments y
    respuesta de POST /adjustments/:id/apply."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    contract_id: UUID
    due_period: date
    status: str
    pct_applied: Decimal | None
    previous_amount: Decimal | None
    new_amount: Decimal | None
    notes: str | None
    applied_by: UUID | None
    applied_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AdjustmentResponse(BaseModel):
    data: AdjustmentSummary


class AdjustmentListResponse(BaseModel):
    data: list[AdjustmentSummary]
    meta: dict
