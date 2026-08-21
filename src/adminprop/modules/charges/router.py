"""Endpoints de `charges`: conceptos recurrentes + carga mensual + vista
de verificacion (issue #28).

SDD: core/sdd_03_api_contracts.md §10 "Cargos del mes"
(`/recurring-charges`, `/charge-entries`) + §7 "Propiedades" (nested
`GET/POST /properties/:id/recurring-charges`).
Implements: CA-05-08.

Permiso unico `charge:manage` en los 5 endpoints -- sdd_03
§"Catalogo de Permisos" no declara un `charge:read` separado; el
`ALL_PERMISSIONS`/`ROLE_DEFINITIONS` de `modules/superadmin/provisioning.py`
solo tiene `charge:manage` (owner/admin lo tienen completo, RN-A01
`maintenance` no tiene ningun permiso de este dominio -- ver
`core/sdd_03_api_contracts.md` §"Resumen de Autorizacion por Recurso").
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from adminprop.modules.charges.schemas import (
    ChargeEntryCreate,
    ChargeEntryResponse,
    ChargeEntryUpdate,
    ChargeVerificationItem,
    ChargeVerificationResponse,
    RecurringChargeCreate,
    RecurringChargeDetail,
    RecurringChargeListResponse,
    RecurringChargeResponse,
    RecurringChargeUpdate,
    parse_period,
)
from adminprop.modules.charges.service import (
    ChargeEntryService,
    RecurringChargeService,
    get_charge_entry_service,
    get_recurring_charge_service,
)
from adminprop.shared.auth.jwt import JWTPayload
from adminprop.shared.rbac import requires_permission
from adminprop.shared.tenant import get_current_tenant

property_recurring_charges_router = APIRouter(prefix="/v1/properties", tags=["charges"])
recurring_charges_router = APIRouter(prefix="/v1/recurring-charges", tags=["charges"])
charge_entries_router = APIRouter(prefix="/v1/charge-entries", tags=["charges"])


# ─── Conceptos por propiedad — RF-05 §1 ─────────────────────────────────


@property_recurring_charges_router.get(
    "/{property_id}/recurring-charges",
    response_model=RecurringChargeListResponse,
    dependencies=[Depends(requires_permission("charge:manage"))],
)
async def list_recurring_charges(
    property_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: RecurringChargeService = Depends(get_recurring_charge_service),
) -> RecurringChargeListResponse:
    """sdd_03 §7 + RF-05: ABM de conceptos por propiedad (activos e
    inactivos)."""
    items = await service.list_by_property(property_id, organization_id)
    return RecurringChargeListResponse(
        data=[RecurringChargeDetail.model_validate(i) for i in items]
    )


@property_recurring_charges_router.post(
    "/{property_id}/recurring-charges",
    status_code=status.HTTP_201_CREATED,
    response_model=RecurringChargeResponse,
    dependencies=[Depends(requires_permission("charge:manage"))],
)
async def create_recurring_charge(
    property_id: UUID,
    dto: RecurringChargeCreate,
    organization_id: UUID = Depends(get_current_tenant),
    service: RecurringChargeService = Depends(get_recurring_charge_service),
) -> RecurringChargeResponse:
    """sdd_03 §7 + RF-05: alta de un concepto recurrente
    (`rentas`/`municipalidad`/`otro` + label)."""
    charge = await service.create(
        property_id=property_id,
        organization_id=organization_id,
        charge_type=dto.charge_type,
        label=dto.label,
    )
    return RecurringChargeResponse(data=RecurringChargeDetail.model_validate(charge))


# ─── ABM del concepto — sdd_03 §10 ──────────────────────────────────────


@recurring_charges_router.patch(
    "/{recurring_charge_id}",
    response_model=RecurringChargeResponse,
    dependencies=[Depends(requires_permission("charge:manage"))],
)
async def update_recurring_charge(
    recurring_charge_id: UUID,
    dto: RecurringChargeUpdate,
    organization_id: UUID = Depends(get_current_tenant),
    service: RecurringChargeService = Depends(get_recurring_charge_service),
) -> RecurringChargeResponse:
    """sdd_03 §10: `PATCH /recurring-charges/:id (label, is_active)` --
    un concepto `is_active=false` deja de aparecer en la carga mensual."""
    updated = await service.update(
        recurring_charge_id,
        organization_id,
        label=dto.label,
        is_active=dto.is_active,
        fields_set=dto.model_fields_set,
    )
    return RecurringChargeResponse(data=RecurringChargeDetail.model_validate(updated))


# ─── Carga mensual — RF-05 §2, CA-05-08 (duplicado) ─────────────────────


@recurring_charges_router.post(
    "/{recurring_charge_id}/entries",
    status_code=status.HTTP_201_CREATED,
    response_model=ChargeEntryResponse,
    dependencies=[Depends(requires_permission("charge:manage"))],
)
async def create_charge_entry(
    recurring_charge_id: UUID,
    dto: ChargeEntryCreate,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("charge:manage")),
    service: ChargeEntryService = Depends(get_charge_entry_service),
) -> ChargeEntryResponse:
    """sdd_03 §10 + RF-05: `{ period, amount, notes }` -- importe del mes
    ingresado a mano (UC-11). CA-05-08: cargar dos veces el mismo
    concepto+mes -> `409 CHARGE_ENTRY_ALREADY_EXISTS`."""
    entry = await service.create_entry(
        recurring_charge_id,
        organization_id,
        period=dto.period_date,
        amount=dto.amount,
        notes=dto.notes,
        actor_user_id=payload.sub,
    )
    return ChargeEntryResponse(data=entry)


# ─── Correccion auditada — sdd_03 §10, RN-D04 ───────────────────────────


@charge_entries_router.patch(
    "/{charge_entry_id}",
    response_model=ChargeEntryResponse,
    dependencies=[Depends(requires_permission("charge:manage"))],
)
async def correct_charge_entry(
    charge_entry_id: UUID,
    dto: ChargeEntryUpdate,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("charge:manage")),
    service: ChargeEntryService = Depends(get_charge_entry_service),
) -> ChargeEntryResponse:
    """sdd_03 §10: `PATCH /charge-entries/:id (correccion auditada)` --
    RN-D04, el valor anterior y nuevo quedan en `audit_logs`."""
    updated = await service.correct_entry(
        charge_entry_id,
        organization_id,
        amount=dto.amount,
        notes=dto.notes,
        fields_set=dto.model_fields_set,
        actor_user_id=payload.sub,
    )
    return ChargeEntryResponse(data=updated)


# ─── Vista de verificacion mensual — CA-05-08 ───────────────────────────


@charge_entries_router.get(
    "",
    response_model=ChargeVerificationResponse,
    dependencies=[Depends(requires_permission("charge:manage"))],
)
async def list_charge_entries_verification(
    period: str = Query(..., description="YYYY-MM"),
    organization_id: UUID = Depends(get_current_tenant),
    service: ChargeEntryService = Depends(get_charge_entry_service),
) -> ChargeVerificationResponse:
    """sdd_03 §10 + RF-05/CA-05-08: `GET /charge-entries?period=YYYY-MM`
    -- muestra las propiedades con cargos cargados y las que faltan (el
    checklist mensual de la secretaria)."""
    rows = await service.list_verification(
        organization_id=organization_id, period=parse_period(period)
    )
    return ChargeVerificationResponse(
        data=[
            ChargeVerificationItem(
                recurring_charge_id=row.recurring_charge_id,
                property_id=row.property_id,
                charge_type=row.charge_type,
                label=row.label,
                has_entry=row.charge_entry_id is not None,
                charge_entry_id=row.charge_entry_id,
                amount=row.amount,
                notes=row.notes,
            )
            for row in rows
        ]
    )
