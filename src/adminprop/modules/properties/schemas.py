"""Pydantic schemas del modulo propiedades -- PascalCase singular (issue #15).

SDD: docs/sdd/features/spec_module_01_propiedades.md §"Validaciones" +
core/sdd_03_api_contracts.md §7 "Propiedades".
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# RF-01 §"Validaciones": "property_type: uno del catalogo sugerido o texto
# libre corto (<= 50)" -- sin CHECK cerrado en DB (migracion #14), por eso
# aca es `str` con `max_length`, no `Literal`.
_PROPERTY_TYPE_MAX_LENGTH = 50

# RF-02 §"Validaciones": "service_type: uno de los 7 valores del enum"
# (CHECK cerrado en DB, migracion #14) -- aca si es `Literal`.
ServiceType = Literal["rentas", "municipalidad", "luz", "gas", "agua", "expensas", "otro"]

# RF-04: "available / unavailable son estados manuales validos" -- `rented`
# es derivado (issue #17, modulo contratos) y NUNCA se acepta directamente
# del cliente; por eso `PropertyUpdate.status` es un `Literal` de 2 valores,
# no los 3 que acepta el CHECK de DB.
ManualPropertyStatus = Literal["available", "unavailable"]


# ─── Barrios (neighborhoods) — RF-05 (issue #99) ─────────────────────────


class NeighborhoodCreate(BaseModel):
    """Body de POST /v1/neighborhoods."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=100)


class NeighborhoodUpdate(BaseModel):
    """Body de PATCH /v1/neighborhoods/:id -- rename."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=100)


class NeighborhoodDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str


class NeighborhoodResponse(BaseModel):
    data: NeighborhoodDetail


class NeighborhoodListResponse(BaseModel):
    """RF-05: "listado del catalogo completo, sin paginacion"."""

    data: list[NeighborhoodDetail]


# ─── Propiedades (properties) — RF-01, RF-03 ─────────────────────────────


class PropertyCreate(BaseModel):
    """Body de POST /v1/properties. `landlord_id` obligatorio (CA-01-01).
    `neighborhood_id` obligatorio (issue #99, CA-01-08) -- decision del
    PO: propiedades nuevas siempre llevan barrio, aunque la columna sea
    nullable en DB por datos legacy."""

    model_config = ConfigDict(extra="forbid")

    address: str = Field(..., min_length=5, max_length=300)
    landlord_id: UUID = Field(...)
    neighborhood_id: UUID = Field(...)
    property_type: str = Field(default="departamento", max_length=_PROPERTY_TYPE_MAX_LENGTH)
    notes: str | None = Field(None)


class PropertyUpdate(BaseModel):
    """Body de PATCH /v1/properties/:id.

    RF-01: "Edicion de todos los campos salvo el estado `rented`
    (derivado)" -- `status` solo acepta los 2 valores manuales
    (`ManualPropertyStatus`); enviar `"rented"` es rechazado por Pydantic
    con `422 VALIDATION_ERROR` antes de llegar al service.

    `neighborhood_id` (issue #99): si el campo viene en el body, no puede
    ser `None` -- "obligatorio en PATCH solo si el campo viene" (RF-01).
    Por eso NO es `UUID | None`: si el cliente omite el campo, Pydantic no
    lo incluye en `model_fields_set` y el service no lo toca; si lo envia
    como `null`, Pydantic rechaza con `422 VALIDATION_ERROR` antes de
    llegar al service (mismo criterio que `status="rented"` mas arriba).
    """

    model_config = ConfigDict(extra="forbid")

    address: str | None = Field(None, min_length=5, max_length=300)
    landlord_id: UUID | None = Field(None)
    neighborhood_id: UUID | None = Field(None)
    property_type: str | None = Field(None, max_length=_PROPERTY_TYPE_MAX_LENGTH)
    status: ManualPropertyStatus | None = Field(None)
    notes: str | None = Field(None)

    @field_validator("neighborhood_id")
    @classmethod
    def _neighborhood_id_cannot_be_explicit_null(cls, v: UUID | None) -> UUID | None:
        # `validate_default=False` (default de Pydantic v2) hace que este
        # validator SOLO corra cuando el cliente envia el campo
        # explicitamente -- si lo omite, el default `None` se aplica sin
        # pasar por aca. Por eso `v is None` aca significa "el cliente
        # mando `neighborhood_id: null`", no "el cliente omitio el campo".
        if v is None:
            raise ValueError("neighborhood_id no puede ser null.")
        return v


class PropertySummary(BaseModel):
    """Item de GET /v1/properties -- listado, sin cuentas de servicio ni
    ficha extendida (RF-03 solo aplica al detalle)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    address: str
    landlord_id: UUID
    neighborhood_id: UUID | None
    neighborhood: NeighborhoodDetail | None = None
    property_type: str
    status: str
    created_at: datetime


class PropertyResponse(BaseModel):
    data: PropertySummary


class PropertyListResponse(BaseModel):
    data: list[PropertySummary]
    meta: dict


# ─── Cuentas de servicio (property_service_accounts) — RF-02 ─────────────


class PropertyServiceAccountCreate(BaseModel):
    """Body de POST /v1/properties/:id/service-accounts. `secondary_number`
    es el caso `luz` (n° de cliente en `account_number` + n° de contrato
    en `secondary_number`), pero el campo es generico para los 7 tipos."""

    model_config = ConfigDict(extra="forbid")

    service_type: ServiceType = Field(...)
    account_number: str = Field(..., min_length=1, max_length=100)
    secondary_number: str | None = Field(None, max_length=100)
    notes: str | None = Field(None)


class PropertyServiceAccountUpdate(BaseModel):
    """Body de PATCH /v1/service-accounts/:id. `service_type` no es
    editable (RF-02 no lo contempla; cambiar el tipo de servicio de una
    cuenta ya cargada es, en la practica, dar de baja y crear una nueva)."""

    model_config = ConfigDict(extra="forbid")

    account_number: str | None = Field(None, min_length=1, max_length=100)
    secondary_number: str | None = Field(None, max_length=100)
    notes: str | None = Field(None)


class PropertyServiceAccountDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    property_id: UUID
    service_type: str
    account_number: str
    secondary_number: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class PropertyServiceAccountResponse(BaseModel):
    data: PropertyServiceAccountDetail


class PropertyServiceAccountListResponse(BaseModel):
    """RF-02: "vista unica, todas las cuentas juntas" -- sin `meta` (no
    paginado, conjunto acotado a lo sumo a los 7 tipos de servicio)."""

    data: list[PropertyServiceAccountDetail]


# ─── Ficha consolidada de la propiedad — RF-03 ───────────────────────────


class PropertyDetail(BaseModel):
    """GET /v1/properties/:id -- ficha consolidada (RF-03).

    Alcance de este PR (issue #15, ver "Decisiones de implementacion"):
    `active_contract`, `work_orders_history` y `recurring_charges` se
    devuelven explicitamente vacios/`None` -- los modulos que originan
    esos datos (`contracts` issue #17, `maintenance` issue #26,
    `settlements` issue #28) todavia no existen. Se declaran los 3 campos
    en el shape final (en vez de omitirlos) para que el frontend ya
    integre contra el contrato definitivo de la ficha y solo necesite
    que dejen de venir vacios cuando esos modulos aterricen -- ningun
    cambio de shape futuro, solo de contenido.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    address: str
    landlord_id: UUID
    neighborhood_id: UUID | None
    neighborhood: NeighborhoodDetail | None = None
    property_type: str
    status: str
    notes: str | None
    created_at: datetime
    updated_at: datetime
    service_accounts: list[PropertyServiceAccountDetail]
    active_contract: dict | None = None
    work_orders_history: list[dict] = Field(default_factory=list)
    recurring_charges: list[dict] = Field(default_factory=list)


class PropertyDetailResponse(BaseModel):
    data: PropertyDetail
