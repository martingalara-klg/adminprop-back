"""Acceso a datos de `properties` y `property_service_accounts` (issue #15).

SDD: infrastructure/spec_data_model.md §Capa 2. docs/skills/tenant-isolation.md
("todo metodo del repository recibe organization_id como parametro y lo
aplica en el WHERE" -- defense in depth sobre RLS, RN-D01).

`landlord_exists` importa `Landlord` de `modules/people/models.py`
(primer cruce de modelos ORM entre modulos del repo): `properties` es
dueno de su propia tabla pero NO de `landlords` -- reusar el modelo ORM
ya registrado evita duplicar el mapeo de columnas via SQL crudo solo
para un `SELECT EXISTS`.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.db.session import get_tenant_db_session
from adminprop.modules.people.models import Landlord
from adminprop.modules.properties.models import Property, PropertyServiceAccount

# RF-04: estados manuales validos (`rented` es derivado, nunca aceptado
# directamente del cliente -- ver `schemas.py.PropertyUpdate`).
MANUAL_STATUSES = ("available", "unavailable")


def _encode_cursor(created_at: datetime, row_id: UUID) -> str:
    raw = f"{created_at.isoformat()}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
    created_at_raw, row_id_raw = raw.split("|", 1)
    return datetime.fromisoformat(created_at_raw), UUID(row_id_raw)


@dataclass(frozen=True)
class PropertyFilters:
    """Filtros de RF-01 "Listado con filtros: propietario, estado, tipo;
    busqueda por direccion"."""

    landlord_id: UUID | None = None
    status: str | None = None
    property_type: str | None = None
    search: str | None = None


class PropertyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Expuesto para que `service.py` pase la MISMA sesion a
        `AuditService.audit()` (mismo criterio que
        `modules/people/repository.py.LandlordRepository.session`) -- el
        cambio de `landlord_id` con contrato/liquidaciones historicas
        (RN "auditada") debe persistirse en la misma transaccion que el
        UPDATE."""
        return self._session

    async def landlord_exists(self, landlord_id: UUID, organization_id: UUID) -> bool:
        """RN "Toda propiedad pertenece a exactamente un propietario":
        valida que `landlord_id` exista, pertenezca al MISMO tenant y no
        este dado de baja -- RN-D01 (cross-tenant se trata igual que
        "no existe", nunca revela el recurso de otra organizacion)."""
        stmt = select(Landlord.id).where(
            Landlord.id == landlord_id,
            Landlord.organization_id == organization_id,
            Landlord.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.first() is not None

    async def create(
        self,
        *,
        organization_id: UUID,
        landlord_id: UUID,
        address: str,
        property_type: str,
        notes: str | None,
    ) -> Property:
        row = Property(
            organization_id=organization_id,
            landlord_id=landlord_id,
            address=address,
            property_type=property_type,
            status="available",
            notes=notes,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get_by_id(self, property_id: UUID, organization_id: UUID) -> Property | None:
        return await self._get_row(property_id, organization_id)

    async def list_by_landlord(self, landlord_id: UUID, organization_id: UUID) -> list[Property]:
        """spec_module_02_personas.md §RF-02 "Ficha del Propietario: Datos
        + listado de sus propiedades (con estado y contrato vigente)" --
        consumido por `modules/people/router.py.get_landlord` (integracion
        declarada, issue #15). Filtro EXPLICITO de `organization_id` +
        `landlord_id` (RN-D01) ademas del `deleted_at IS NULL`."""
        stmt = (
            select(Property)
            .where(
                Property.landlord_id == landlord_id,
                Property.organization_id == organization_id,
                Property.deleted_at.is_(None),
            )
            .order_by(Property.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list(
        self,
        *,
        organization_id: UUID,
        cursor: str | None,
        limit: int,
        filters: PropertyFilters,
    ) -> tuple[list[Property], str | None]:
        """RF-01: listado paginado cursor-based + filtros de propietario,
        estado, tipo y busqueda por direccion (`ILIKE`)."""
        stmt = select(Property).where(
            Property.organization_id == organization_id, Property.deleted_at.is_(None)
        )
        if filters.landlord_id is not None:
            stmt = stmt.where(Property.landlord_id == filters.landlord_id)
        if filters.status is not None:
            stmt = stmt.where(Property.status == filters.status)
        if filters.property_type is not None:
            stmt = stmt.where(Property.property_type == filters.property_type)
        if filters.search is not None:
            stmt = stmt.where(Property.address.ilike(f"%{filters.search}%"))
        if cursor:
            cursor_created_at, cursor_id = _decode_cursor(cursor)
            stmt = stmt.where((Property.created_at, Property.id) < (cursor_created_at, cursor_id))
        stmt = stmt.order_by(Property.created_at.desc(), Property.id.desc()).limit(limit + 1)

        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = (
            _encode_cursor(page[-1].created_at, page[-1].id) if has_more and page else None
        )
        return page, next_cursor

    async def update(
        self, property_id: UUID, organization_id: UUID, *, fields: dict[str, object]
    ) -> Property | None:
        row = await self._get_row(property_id, organization_id)
        if row is None:  # pragma: no cover -- defensivo, `service.update` ya valido existencia
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def soft_delete(self, property_id: UUID, organization_id: UUID) -> bool:
        """RF-01 + CA-01-03: baja logica (`deleted_at`). Devuelve `False`
        si no existe/ya esta borrado/es de otra organizacion (404, RN-D01)."""
        row = await self._get_row(property_id, organization_id)
        if row is None:  # pragma: no cover -- defensivo, `service.delete` ya valido existencia
            return False
        row.deleted_at = datetime.now(UTC)
        await self._session.flush()
        return True

    async def has_active_dependencies(self, property_id: UUID, organization_id: UUID) -> bool:
        """CA-01-03: `409 ENTITY_HAS_DEPENDENCIES` si la propiedad tiene un
        contrato `active`.

        Issue #17: el modulo `contracts` ya existe -- delega en
        `ContractRepository.has_active_contract_for_property` (misma
        `session`, misma transaccion) en vez de duplicar el SQL. Import
        diferido (dentro del metodo, no a nivel de modulo): `contracts.repository`
        importa `properties.models.Property` para validar `property_id`
        en `ContractService.create`, y `properties.service` (que si se
        importa a nivel de modulo desde este archivo via el
        `service.py` del propio modulo) queda en el mismo paquete que
        este repository -- diferir el import evita depender del orden de
        carga entre ambos modulos al arrancar `adminprop.main`.
        """
        from adminprop.modules.contracts.repository import ContractRepository

        contracts_repo = ContractRepository(self._session)
        return await contracts_repo.has_active_contract_for_property(property_id, organization_id)

    async def _get_row(self, property_id: UUID, organization_id: UUID) -> Property | None:
        stmt = select(Property).where(
            Property.id == property_id,
            Property.organization_id == organization_id,
            Property.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def commit(self) -> None:
        await self._session.commit()


def get_property_repository(
    session: AsyncSession = Depends(get_tenant_db_session),
) -> PropertyRepository:
    return PropertyRepository(session)


class PropertyServiceAccountRepository:
    """RF-02: ABM de cuentas de servicio por propiedad. Puramente
    informativa -- ninguna logica de negocio depende de estos datos."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def property_exists(self, property_id: UUID, organization_id: UUID) -> bool:
        """RN-D01: valida que la propiedad exista, sea del mismo tenant y
        no este borrada antes de operar sobre sus cuentas de servicio."""
        stmt = select(Property.id).where(
            Property.id == property_id,
            Property.organization_id == organization_id,
            Property.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.first() is not None

    async def create(
        self,
        *,
        organization_id: UUID,
        property_id: UUID,
        service_type: str,
        account_number: str,
        secondary_number: str | None,
        notes: str | None,
    ) -> PropertyServiceAccount:
        row = PropertyServiceAccount(
            organization_id=organization_id,
            property_id=property_id,
            service_type=service_type,
            account_number=account_number,
            secondary_number=secondary_number,
            notes=notes,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get_by_id(
        self, service_account_id: UUID, organization_id: UUID
    ) -> PropertyServiceAccount | None:
        return await self._get_row(service_account_id, organization_id)

    async def list_by_property(
        self, property_id: UUID, organization_id: UUID
    ) -> list[PropertyServiceAccount]:
        """RF-02: "vista unica: todas las cuentas de la propiedad visibles
        juntas en su ficha" -- sin paginacion (conjunto acotado: a lo sumo
        7 tipos de servicio por propiedad)."""
        stmt = (
            select(PropertyServiceAccount)
            .where(
                PropertyServiceAccount.property_id == property_id,
                PropertyServiceAccount.organization_id == organization_id,
                PropertyServiceAccount.deleted_at.is_(None),
            )
            .order_by(PropertyServiceAccount.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update(
        self,
        service_account_id: UUID,
        organization_id: UUID,
        *,
        fields: dict[str, object],
    ) -> PropertyServiceAccount | None:
        row = await self._get_row(service_account_id, organization_id)
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def soft_delete(self, service_account_id: UUID, organization_id: UUID) -> bool:
        row = await self._get_row(service_account_id, organization_id)
        if row is None:  # pragma: no cover -- defensivo, service ya valido existencia
            return False
        row.deleted_at = datetime.now(UTC)
        await self._session.flush()
        return True

    async def _get_row(
        self, service_account_id: UUID, organization_id: UUID
    ) -> PropertyServiceAccount | None:
        stmt = select(PropertyServiceAccount).where(
            PropertyServiceAccount.id == service_account_id,
            PropertyServiceAccount.organization_id == organization_id,
            PropertyServiceAccount.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def commit(self) -> None:
        await self._session.commit()


def get_property_service_account_repository(
    session: AsyncSession = Depends(get_tenant_db_session),
) -> PropertyServiceAccountRepository:
    return PropertyServiceAccountRepository(session)
