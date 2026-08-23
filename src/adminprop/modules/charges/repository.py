"""Acceso a datos de `recurring_charges` y `charge_entries` (issue #28).

SDD: infrastructure/spec_data_model.md §Capa 6. docs/skills/tenant-isolation.md
("todo metodo del repository recibe organization_id como parametro y lo
aplica en el WHERE" -- defense in depth sobre RLS, RN-D01).

`property_exists` valida `property_id` contra `properties` -- import
directo del modelo ORM (mismo criterio que
`modules/properties/repository.py.landlord_exists` importando
`modules/people/models.Landlord`): `charges` es un modulo hoja (ningun
modulo existente lo importa a el), asi que no hay riesgo de ciclo de
import como el documentado en `modules/payments/repository.py` (ese si
usa SQL crudo porque el ciclo era real en su caso).

`list_verification` (RF-05, CA-05-08) hace el JOIN
`recurring_charges -> charge_entries` con SQL crudo -- filtro EXPLICITO
de `organization_id` en AMBAS tablas del join (docs/skills/
tenant-isolation.md §"Queries con join/agregacion", RN-D01), no solo en
`recurring_charges`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.db.session import get_tenant_db_session
from adminprop.modules.charges.models import ChargeEntry, RecurringCharge
from adminprop.modules.properties.models import Property


@dataclass(frozen=True)
class ChargeVerificationRow:
    """RF-05/CA-05-08: fila cruda del JOIN `recurring_charges LEFT JOIN
    charge_entries` para un `period` dado -- una fila por concepto
    `is_active` de la organizacion, con o sin `charge_entry` cargado."""

    recurring_charge_id: UUID
    property_id: UUID
    charge_type: str
    label: str
    charge_entry_id: UUID | None
    amount: Decimal | None
    notes: str | None


class RecurringChargeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Expuesto para que `service.py` pase la MISMA sesion a
        `AuditService.audit()` (mismo criterio que
        `modules/properties/repository.py.PropertyRepository.session`)."""
        return self._session

    async def property_exists(self, property_id: UUID, organization_id: UUID) -> bool:
        """RN-D01: valida que la propiedad exista, sea del mismo tenant y
        no este dada de baja -- un `property_id` invalido o de otra
        organizacion se trata como 404, sin distincion (no revela
        existencia cross-tenant)."""
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
        charge_type: str,
        label: str,
    ) -> RecurringCharge:
        row = RecurringCharge(
            organization_id=organization_id,
            property_id=property_id,
            charge_type=charge_type,
            label=label,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get_by_id(
        self, recurring_charge_id: UUID, organization_id: UUID
    ) -> RecurringCharge | None:
        """RN-D01: filtro explicito de `organization_id` (defense in depth
        sobre RLS)."""
        stmt = select(RecurringCharge).where(
            RecurringCharge.id == recurring_charge_id,
            RecurringCharge.organization_id == organization_id,
            RecurringCharge.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_property(
        self, property_id: UUID, organization_id: UUID
    ) -> list[RecurringCharge]:
        """RF-05: ABM de conceptos de la propiedad -- devuelve activos e
        inactivos (la vista de ABM administra ambos; solo la carga
        mensual, RF-05 parrafo 1, filtra `is_active`)."""
        stmt = (
            select(RecurringCharge)
            .where(
                RecurringCharge.property_id == property_id,
                RecurringCharge.organization_id == organization_id,
                RecurringCharge.deleted_at.is_(None),
            )
            .order_by(RecurringCharge.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update(
        self, recurring_charge_id: UUID, organization_id: UUID, *, fields: dict[str, object]
    ) -> RecurringCharge | None:
        row = await self.get_by_id(recurring_charge_id, organization_id)
        if row is None:  # pragma: no cover -- defensivo, el service ya valido existencia
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def commit(self) -> None:
        await self._session.commit()


def get_recurring_charge_repository(
    session: AsyncSession = Depends(get_tenant_db_session),
) -> RecurringChargeRepository:
    return RecurringChargeRepository(session)


class ChargeEntryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def get_by_charge_and_period(
        self, recurring_charge_id: UUID, organization_id: UUID, period: date
    ) -> ChargeEntry | None:
        """RF-05: chequeo app-level ANTES del INSERT para levantar
        `409 CHARGE_ENTRY_ALREADY_EXISTS` con un mensaje claro -- la
        UNIQUE `charge_entries_recurring_charge_period_unique` (migracion
        #27) es la red de seguridad, no la UX (mismo criterio que
        `ContractOverlapException` con el EXCLUDE de `contracts`)."""
        stmt = select(ChargeEntry).where(
            ChargeEntry.recurring_charge_id == recurring_charge_id,
            ChargeEntry.organization_id == organization_id,
            ChargeEntry.period == period,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        organization_id: UUID,
        recurring_charge_id: UUID,
        period: date,
        amount: Decimal,
        notes: str | None,
        created_by: UUID,
    ) -> ChargeEntry:
        row = ChargeEntry(
            organization_id=organization_id,
            recurring_charge_id=recurring_charge_id,
            period=period,
            amount=amount,
            notes=notes,
            created_by=created_by,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get_by_id(self, charge_entry_id: UUID, organization_id: UUID) -> ChargeEntry | None:
        """RN-D01: filtro explicito de `organization_id` -- usado por la
        correccion auditada (`PATCH /charge-entries/:id`, RN-D04)."""
        stmt = select(ChargeEntry).where(
            ChargeEntry.id == charge_entry_id,
            ChargeEntry.organization_id == organization_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def update(
        self, charge_entry_id: UUID, organization_id: UUID, *, fields: dict[str, object]
    ) -> ChargeEntry | None:
        """RN-D04: correccion auditada -- el `before`/`after` para
        `AuditService.audit()` lo arma `service.py` ANTES de llamar aca
        (necesita el valor viejo, que este metodo ya sobreescribe)."""
        row = await self.get_by_id(charge_entry_id, organization_id)
        if row is None:  # pragma: no cover -- defensivo, el service ya valido existencia
            return None
        row.updated_at = datetime.now(UTC)
        for key, value in fields.items():
            setattr(row, key, value)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def commit(self) -> None:
        await self._session.commit()

    # ─── RF-05/CA-05-08: vista de verificacion mensual ─────────────────────

    # RN-D01: filtro EXPLICITO de `organization_id` en AMBAS tablas del
    # join (docs/skills/tenant-isolation.md §"Queries con join/agregacion")
    # -- no alcanza con filtrarlo solo en `recurring_charges`. `rc.is_active`
    # filtra los conceptos dados de baja (RF-05 parrafo 1: "un concepto
    # inactivo deja de aparecer en la carga mensual"). `LEFT JOIN` con el
    # `period` fijo en el ON (no en el WHERE) para que un concepto SIN
    # `charge_entry` en ese mes siga apareciendo (con columnas NULL) en vez
    # de desaparecer del checklist.
    _VERIFICATION_SQL = """
        SELECT
            rc.id AS recurring_charge_id,
            rc.property_id AS property_id,
            rc.charge_type AS charge_type,
            rc.label AS label,
            ce.id AS charge_entry_id,
            ce.amount AS amount,
            ce.notes AS notes
        FROM recurring_charges rc
        LEFT JOIN charge_entries ce
            ON ce.recurring_charge_id = rc.id
            AND ce.organization_id = :org_id
            AND ce.period = :period
        WHERE rc.organization_id = :org_id
          AND rc.is_active = true
          AND rc.deleted_at IS NULL
        ORDER BY rc.property_id, rc.created_at
    """

    async def list_verification(
        self, *, organization_id: UUID, period: date
    ) -> list[ChargeVerificationRow]:
        """RF-05/CA-05-08: `GET /charge-entries?period=` -- una fila por
        concepto activo de la organizacion, con el `charge_entry` del
        `period` si ya se cargo (o columnas `None` si falta) -- "muestra
        que propiedades ya tienen sus cargos del mes y cuales faltan"."""
        result = await self._session.execute(
            text(self._VERIFICATION_SQL),
            {"org_id": str(organization_id), "period": period},
        )
        return [ChargeVerificationRow(**dict(row._mapping)) for row in result]


def get_charge_entry_repository(
    session: AsyncSession = Depends(get_tenant_db_session),
) -> ChargeEntryRepository:
    return ChargeEntryRepository(session)
