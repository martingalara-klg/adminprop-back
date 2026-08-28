"""Endpoints /v1/landlords/* y /v1/renters/* (issue #13, extendido
#23/#30).

SDD: core/sdd_03_api_contracts.md §5 "Propietarios" + §6 "Inquilinos".
Implements: CA-02-01, 02, 03, 04, 06, 07 (issue #13); CA-02-05 (estado
de deuda, issue #23); CA-05-07 (historial de liquidaciones en la ficha
del propietario, issue #30 -- "descargables desde el detalle y desde la
ficha del propietario": el detalle/export vive en `GET /settlements/:id`/
`GET /settlements/:id/export`, esta lista es el punto de entrada desde
la ficha).

`POST /renters/:id/debt-certificate` (CA-04-11/CA-04-12, RF-08, issue
#24) vivio aca hasta el issue #104: decision del PO (2026-08-28) de que
el libre deuda es POR CONTRATO -- se movio a `POST /contracts/:id/
debt-certificate` (`modules/contracts/router.py`)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.db.session import get_tenant_db_session
from adminprop.modules.payments.schemas import DebtEntryData, RenterDebtResponse
from adminprop.modules.people.models import Renter
from adminprop.modules.people.repository import LandlordFields, RenterRepository
from adminprop.modules.people.schemas import (
    LandlordCreate,
    LandlordDetail,
    LandlordListResponse,
    LandlordPropertySummary,
    LandlordResponse,
    LandlordSummary,
    LandlordUpdate,
    RenterCreate,
    RenterDetail,
    RenterListResponse,
    RenterResponse,
    RenterUpdate,
)
from adminprop.modules.people.service import (
    LandlordService,
    RenterService,
    get_landlord_service,
    get_renter_service,
)
from adminprop.modules.properties.repository import (
    PropertyRepository,
    get_property_repository,
)
from adminprop.modules.settlements.schemas import SettlementListResponse, SettlementSummary
from adminprop.shared.auth.jwt import JWTPayload
from adminprop.shared.errors.codes import NotFoundException
from adminprop.shared.rbac import requires_permission
from adminprop.shared.tenant import get_current_tenant

landlords_router = APIRouter(prefix="/v1/landlords", tags=["people"])
renters_router = APIRouter(prefix="/v1/renters", tags=["people"])


def _to_landlord_detail(
    landlord: LandlordFields, properties: list[LandlordPropertySummary] | None = None
) -> LandlordDetail:
    return LandlordDetail(
        id=landlord.id,
        name=landlord.name,
        tax_id=landlord.tax_id,
        phone=landlord.phone,
        email=landlord.email,
        bank_info=landlord.bank_info,
        commission_pct=landlord.commission_pct,
        notes=landlord.notes,
        created_at=landlord.created_at,
        updated_at=landlord.updated_at,
        properties=properties or [],
    )


def _to_landlord_summary(landlord) -> LandlordSummary:
    """CA-02-04: construido a partir de la fila ORM del listado -- nunca
    del `LandlordFields` descifrado (`LandlordSummary` ni siquiera
    declara `bank_info`, ver `schemas.py`)."""
    return LandlordSummary.model_validate(landlord)


def _to_renter_detail(renter: Renter) -> RenterDetail:
    return RenterDetail.model_validate(renter)


# ─── Propietarios (/v1/landlords) — RF-01, RF-02 ─────────────────────────


@landlords_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=LandlordResponse,
    dependencies=[Depends(requires_permission("landlord:manage"))],
)
async def create_landlord(
    dto: LandlordCreate,
    organization_id: UUID = Depends(get_current_tenant),
    service: LandlordService = Depends(get_landlord_service),
) -> LandlordResponse:
    """RF-01 + CA-02-01: crea un propietario con `commission_pct`
    obligatorio -- queda disponible para las liquidaciones de todas sus
    propiedades (futuras, issue #15+)."""
    landlord = await service.create(
        organization_id=organization_id,
        name=dto.name,
        tax_id=dto.tax_id,
        phone=dto.phone,
        email=dto.email,
        bank_info=dto.bank_info,
        commission_pct=dto.commission_pct,
        notes=dto.notes,
    )
    return LandlordResponse(data=_to_landlord_detail(landlord))


@landlords_router.get(
    "",
    response_model=LandlordListResponse,
    dependencies=[Depends(requires_permission("landlord:read"))],
)
async def list_landlords(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    organization_id: UUID = Depends(get_current_tenant),
    service: LandlordService = Depends(get_landlord_service),
) -> LandlordListResponse:
    """RF-02: listado paginado. CA-02-04: `bank_info` NUNCA aparece aca
    (`LandlordSummary` no declara el campo)."""
    items, next_cursor = await service.list(
        organization_id=organization_id, cursor=cursor, limit=limit
    )
    return LandlordListResponse(
        data=[_to_landlord_summary(item) for item in items],
        meta={"next_cursor": next_cursor, "limit": limit},
    )


@landlords_router.get(
    "/{landlord_id}",
    response_model=LandlordResponse,
    dependencies=[Depends(requires_permission("landlord:read"))],
)
async def get_landlord(
    landlord_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: LandlordService = Depends(get_landlord_service),
    properties_repo: PropertyRepository = Depends(get_property_repository),
) -> LandlordResponse:
    """RF-02 + CA-02-04: ficha del propietario -- `bank_info` descifrado
    solo aca (owner/admin, via `landlord:read`).

    RF-02 (issue #15): "Datos + listado de sus propiedades (con estado y
    contrato vigente)" -- `properties_repo.list_by_landlord` es la
    integracion declarada con el modulo `properties`; `active_contract`
    de cada propiedad queda en `None` hasta que exista el modulo
    `contracts` (issue #17, ver `LandlordPropertySummary`)."""
    landlord = await service.get(landlord_id, organization_id)
    if landlord is None:
        # RN-D01: 404, no 403 -- no distingue "no existe" de "otra org".
        raise NotFoundException()
    properties = await properties_repo.list_by_landlord(landlord_id, organization_id)
    return LandlordResponse(
        data=_to_landlord_detail(
            landlord,
            [LandlordPropertySummary.model_validate(p) for p in properties],
        )
    )


@landlords_router.patch(
    "/{landlord_id}",
    response_model=LandlordResponse,
)
async def update_landlord(
    landlord_id: UUID,
    dto: LandlordUpdate,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("landlord:manage")),
    service: LandlordService = Depends(get_landlord_service),
) -> LandlordResponse:
    """RF-01 + CA-02-02/CA-02-03: `admin` edita datos de contacto libremente;
    si incluye `commission_pct` recibe 403 FORBIDDEN sin el permiso atomico
    `landlord:set-commission` (sdd_03 v1.5, issue #51 -- solo `owner` lo
    tiene en el seed de roles; ya NO se compara `payload.role`). El
    cambio de `commission_pct` (cuando el actor tiene el permiso) queda
    auditado con valor anterior/nuevo y rige unicamente para liquidaciones
    futuras (RN-L05 -- no hay liquidaciones ya generadas que recalcular en
    este modulo todavia)."""
    updated = await service.update(
        landlord_id,
        organization_id,
        name=dto.name,
        tax_id=dto.tax_id,
        phone=dto.phone,
        email=dto.email,
        bank_info=dto.bank_info,
        commission_pct=dto.commission_pct,
        notes=dto.notes,
        actor_user_id=payload.sub,
        actor_permissions=frozenset(payload.permissions),
        fields_set=dto.model_fields_set,
    )
    return LandlordResponse(data=_to_landlord_detail(updated))


@landlords_router.delete(
    "/{landlord_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires_permission("landlord:manage"))],
)
async def delete_landlord(
    landlord_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: LandlordService = Depends(get_landlord_service),
) -> None:
    """RF-01 + CA-02-06: baja logica. `409 ENTITY_HAS_DEPENDENCIES` si el
    propietario tiene propiedades activas (chequeo extensible, ver
    `repository.py` -- siempre `False` hoy)."""
    await service.delete(landlord_id, organization_id)


@landlords_router.get(
    "/{landlord_id}/settlements",
    response_model=SettlementListResponse,
    dependencies=[Depends(requires_permission("settlement:read"))],
)
async def get_landlord_settlements(
    landlord_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: LandlordService = Depends(get_landlord_service),
    session: AsyncSession = Depends(get_tenant_db_session),
) -> SettlementListResponse:
    """sdd_03 §5 "GET /landlords/:id/settlements" (historial de
    liquidaciones) + CA-05-07 (issue #30): punto de entrada desde la
    ficha del propietario -- el detalle/export de cada liquidacion sigue
    viviendo en `GET /settlements/:id`/`GET /settlements/:id/export`
    (modulo `settlements`). Permiso `settlement:read` (no `landlord:read`):
    es un listado de liquidaciones, mismo permiso que
    `GET /settlements`.

    `SettlementService`/`SettlementRepository` se importan DIFERIDO --
    mismo ciclo de import documentado en `get_renter_debt` de este
    archivo (`properties/people` cargan muy temprano; un import a nivel
    de modulo de otro modulo de negocio aca arriesga el mismo ciclo)."""
    landlord = await service.get(landlord_id, organization_id)
    if landlord is None:
        # RN-D01: 404, no 403 -- no distingue "no existe" de "otra org".
        raise NotFoundException()

    from adminprop.modules.settlements.repository import SettlementRepository
    from adminprop.modules.settlements.service import SettlementService

    settlement_service = SettlementService(SettlementRepository(session))
    settlements, flags = await settlement_service.list(
        organization_id=organization_id, period=None, landlord_id=landlord_id, status=None
    )
    return SettlementListResponse(
        data=[
            SettlementSummary.model_validate(s).model_copy(
                update={"needs_regeneration": flags.get(s.id, False)}
            )
            for s in settlements
        ]
    )


# ─── Inquilinos (/v1/renters) — RF-03 ────────────────────────────────────


@renters_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=RenterResponse,
    dependencies=[Depends(requires_permission("renter:manage"))],
)
async def create_renter(
    dto: RenterCreate,
    organization_id: UUID = Depends(get_current_tenant),
    service: RenterService = Depends(get_renter_service),
) -> RenterResponse:
    """RF-03: alta de inquilino."""
    renter = await service.create(
        organization_id=organization_id,
        name=dto.name,
        tax_id=dto.tax_id,
        phone=dto.phone,
        email=dto.email,
        notes=dto.notes,
    )
    return RenterResponse(data=_to_renter_detail(renter))


@renters_router.get(
    "",
    response_model=RenterListResponse,
    dependencies=[Depends(requires_permission("renter:read"))],
)
async def list_renters(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    organization_id: UUID = Depends(get_current_tenant),
    service: RenterService = Depends(get_renter_service),
) -> RenterListResponse:
    """RF-03: listado paginado."""
    items, next_cursor = await service.list(
        organization_id=organization_id, cursor=cursor, limit=limit
    )
    return RenterListResponse(
        data=[_to_renter_detail(item) for item in items],
        meta={"next_cursor": next_cursor, "limit": limit},
    )


@renters_router.get(
    "/{renter_id}",
    response_model=RenterResponse,
    dependencies=[Depends(requires_permission("renter:read"))],
)
async def get_renter(
    renter_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: RenterService = Depends(get_renter_service),
) -> RenterResponse:
    """RF-04: ficha del inquilino (datos). El estado de deuda vive en
    `GET /renters/:id/debt` (CA-02-05, issue #23)."""
    renter = await service.get(renter_id, organization_id)
    if renter is None:
        raise NotFoundException()
    return RenterResponse(data=_to_renter_detail(renter))


@renters_router.get(
    "/{renter_id}/debt",
    response_model=RenterDebtResponse,
    dependencies=[Depends(requires_permission("renter:read"))],
)
async def get_renter_debt(
    renter_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    renter_service: RenterService = Depends(get_renter_service),
    session: AsyncSession = Depends(get_tenant_db_session),
) -> RenterDebtResponse:
    """sdd_03 §6 + CA-02-05: "la ficha del inquilino muestra sus contratos
    y su estado de deuda con: periodos adeudados, saldo, dias de mora e
    interes sugerido acumulado". Delega el calculo en `DebtService`
    (modulo `payments`, issue #23) -- este endpoint solo valida que el
    inquilino exista en el tenant (RN-D01) antes de delegar.

    `DebtService`/`RentPeriodRepository`/`AdministracionRepository` se
    importan DIFERIDO (no a nivel de modulo, mismo criterio documentado
    en `shared/audit/service.py.record_access_denied`): `payments.service`
    importa (transitivamente, via `contracts.rent_period_hook`) modelos
    de `properties`, y `properties/__init__.py` importa `people/__init__.py`
    (para `Landlord`) -- que a su vez importa ESTE router. Un import a
    nivel de modulo de `payments.service` aca cierra ese ciclo (confirmado
    con `python -c "import adminprop.main"` fallando con `ImportError:
    cannot import name '...' from partially initialized module`); el
    import diferido lo evita porque para cuando el primer request llega,
    todos los modulos ya terminaron de cargar."""
    renter = await renter_service.get(renter_id, organization_id)
    if renter is None:
        # RN-D01: 404, no 403 -- no distingue "no existe" de "otra org".
        raise NotFoundException()

    from adminprop.modules.administracion.repository import AdministracionRepository
    from adminprop.modules.payments.repository import RentPeriodRepository
    from adminprop.modules.payments.service import DebtService

    debt_service = DebtService(RentPeriodRepository(session), AdministracionRepository(session))
    entries = await debt_service.renter_debt(
        renter_id, organization_id, today=datetime.now(tz=UTC).date()
    )
    return RenterDebtResponse(data=[DebtEntryData.model_validate(e) for e in entries])


@renters_router.patch(
    "/{renter_id}",
    response_model=RenterResponse,
)
async def update_renter(
    renter_id: UUID,
    dto: RenterUpdate,
    organization_id: UUID = Depends(get_current_tenant),
    _payload: JWTPayload = Depends(requires_permission("renter:manage")),
    service: RenterService = Depends(get_renter_service),
) -> RenterResponse:
    """RF-03: edicion de datos de contacto."""
    updated = await service.update(
        renter_id,
        organization_id,
        name=dto.name,
        tax_id=dto.tax_id,
        phone=dto.phone,
        email=dto.email,
        notes=dto.notes,
        fields_set=dto.model_fields_set,
    )
    return RenterResponse(data=_to_renter_detail(updated))


@renters_router.delete(
    "/{renter_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires_permission("renter:manage"))],
)
async def delete_renter(
    renter_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: RenterService = Depends(get_renter_service),
) -> None:
    """RF-03 + CA-02-06: baja logica. `409 ENTITY_HAS_DEPENDENCIES` si el
    inquilino tiene contrato vigente (chequeo extensible, ver
    `repository.py` -- siempre `False` hoy)."""
    await service.delete(renter_id, organization_id)
