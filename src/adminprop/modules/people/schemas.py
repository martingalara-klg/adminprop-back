"""Pydantic schemas del modulo personas -- PascalCase singular (issue #13).

SDD: docs/sdd/features/spec_module_02_personas.md §"Validaciones" +
core/sdd_03_api_contracts.md §5 "Propietarios" + §6 "Inquilinos".
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

_DNI_RE = re.compile(r"^\d{7,8}$")
_CUIT_MULTIPLIERS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)


def _cuit_check_digit(digits: str) -> int:
    """Mismo algoritmo estandar de digito verificador CUIT que
    `modules/administracion/schemas.py._cuit_check_digit` -- se duplica
    (no se extrae a `shared/`) por el mismo criterio que el repo ya
    aplica a otros helpers de test/seed replicados entre modulos: cada
    modulo es dueno de su propia validacion, sin acoplar `administracion`
    y `people` a un import cruzado por una funcion de 5 lineas."""
    total = sum(int(d) * m for d, m in zip(digits, _CUIT_MULTIPLIERS, strict=True))
    remainder = total % 11
    check = 11 - remainder
    if check == 11:
        return 0
    return check


def _validate_tax_id(value: str | None) -> str | None:
    """spec_module_02_personas.md §"Validaciones": "CUIT de 11 digitos con
    digito verificador valido, o DNI de 7-8 digitos (campo flexible,
    validacion por formato detectado)" -- el formato se detecta por
    longitud tras quitar guiones/espacios."""
    if value is None:
        return value
    digits = value.replace("-", "").replace(" ", "").strip()
    if not digits:
        return None
    if len(digits) == 11 and digits.isdigit():
        expected = _cuit_check_digit(digits[:10])
        if expected == 10 or int(digits[10]) != expected:
            raise ValueError("CUIT invalido.")
        return digits
    if _DNI_RE.match(digits):
        return digits
    raise ValueError("tax_id invalido: debe ser un CUIT de 11 digitos o un DNI de 7-8 digitos.")


def _validate_email(value: str | None) -> str | None:
    if value is None:
        return value
    if "@" not in value or " " in value:
        raise ValueError("email invalido.")
    return value.lower()


def _validate_commission_pct(value: Decimal) -> Decimal:
    """RF-01 / spec_data_model.md: `commission_pct` 0-100, hasta 2
    decimales (CHECK de rango ya vive en DB -- CA-12-03; esta validacion
    evita un round-trip a Postgres solo para un 400 evitable)."""
    if value < 0 or value > 100:
        raise ValueError("commission_pct debe estar entre 0 y 100.")
    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if quantized != value:
        raise ValueError("commission_pct admite hasta 2 decimales.")
    return value


# ─── Propietarios (landlords) — RF-01, RF-02 ─────────────────────────────


class LandlordCreate(BaseModel):
    """Body de POST /v1/landlords. `commission_pct` es obligatorio desde
    el alta (RF-01: "sin el no se puede liquidar")."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=150)
    tax_id: str | None = Field(None)
    phone: str | None = Field(None, max_length=30)
    email: str | None = Field(None, max_length=255)
    bank_info: str | None = Field(None)
    commission_pct: Decimal = Field(...)
    notes: str | None = Field(None)

    _validate_tax_id = field_validator("tax_id")(classmethod(lambda cls, v: _validate_tax_id(v)))
    _validate_email = field_validator("email")(classmethod(lambda cls, v: _validate_email(v)))
    _validate_commission_pct = field_validator("commission_pct")(
        classmethod(lambda cls, v: _validate_commission_pct(v))
    )


class LandlordUpdate(BaseModel):
    """Body de PATCH /v1/landlords/:id.

    `commission_pct` es el UNICO campo restringido por rol: CA-02-02 --
    un `admin` que lo incluya (aunque sea con el mismo valor vigente)
    recibe 403 FORBIDDEN; `owner` puede cambiarlo (auditado, RN-D04/RN-L05).
    Todos los demas campos son "datos de contacto" editables por ambos.
    `None` (campo ausente) significa "no tocar" -- no hay forma de
    limpiar `commission_pct` a NULL porque la columna es NOT NULL.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=2, max_length=150)
    tax_id: str | None = Field(None)
    phone: str | None = Field(None, max_length=30)
    email: str | None = Field(None, max_length=255)
    bank_info: str | None = Field(None)
    commission_pct: Decimal | None = Field(None)
    notes: str | None = Field(None)

    _validate_tax_id = field_validator("tax_id")(classmethod(lambda cls, v: _validate_tax_id(v)))
    _validate_email = field_validator("email")(classmethod(lambda cls, v: _validate_email(v)))

    @field_validator("commission_pct")
    @classmethod
    def _validate_commission_pct_opt(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return value
        return _validate_commission_pct(value)


class LandlordSummary(BaseModel):
    """Item de GET /v1/landlords -- CA-02-04: `bank_info` NUNCA aparece en
    listados, ni siquiera cifrado."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    tax_id: str | None
    phone: str | None
    email: str | None
    commission_pct: Decimal
    notes: str | None
    created_at: datetime


class LandlordDetail(BaseModel):
    """GET /v1/landlords/:id y respuesta de POST/PATCH -- CA-02-04:
    `bank_info` solo aparece aca (detalle), ya descifrado por el service."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    tax_id: str | None
    phone: str | None
    email: str | None
    bank_info: str | None
    commission_pct: Decimal
    notes: str | None
    created_at: datetime
    updated_at: datetime


class LandlordResponse(BaseModel):
    data: LandlordDetail


class LandlordListResponse(BaseModel):
    data: list[LandlordSummary]
    meta: dict


# ─── Inquilinos (renters) — RF-03 ────────────────────────────────────────


class RenterCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=150)
    tax_id: str | None = Field(None)
    phone: str | None = Field(None, max_length=30)
    email: str | None = Field(None, max_length=255)
    notes: str | None = Field(None)

    _validate_tax_id = field_validator("tax_id")(classmethod(lambda cls, v: _validate_tax_id(v)))
    _validate_email = field_validator("email")(classmethod(lambda cls, v: _validate_email(v)))


class RenterUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=2, max_length=150)
    tax_id: str | None = Field(None)
    phone: str | None = Field(None, max_length=30)
    email: str | None = Field(None, max_length=255)
    notes: str | None = Field(None)

    _validate_tax_id = field_validator("tax_id")(classmethod(lambda cls, v: _validate_tax_id(v)))
    _validate_email = field_validator("email")(classmethod(lambda cls, v: _validate_email(v)))


class RenterDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    tax_id: str | None
    phone: str | None
    email: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class RenterResponse(BaseModel):
    data: RenterDetail


class RenterListResponse(BaseModel):
    data: list[RenterDetail]
    meta: dict
